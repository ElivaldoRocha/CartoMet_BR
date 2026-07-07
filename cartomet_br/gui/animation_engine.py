"""
Motor da animação de steps (GIF/MP4) — workers QThread + orquestrador.

Pipeline em 3 fases:
  1. ``PrepareWorker``     — pré-download serializado de todos os steps
     (cache-first, anti-429), alimenta o ``FrameScaleTracker`` (escala
     congelada) e retém os results de bloqueio/LOCZCIT por step;
  2. ``FrameLoaderWorker`` — produtor: recarrega os payloads do disco
     (``cache_only_mode`` — NUNCA rede) e entrega à GUI thread, que replota
     a composição e salva o PNG do quadro. Handshake por ``threading.Event``:
     1 step em voo (o decode cfgrib do quadro N+1 roda em paralelo com o
     plot do quadro N; o canvas anima ao vivo = preview grátis);
  3. ``EncodeWorker``      — codifica GIF (Pillow) ou MP4 (imageio-ffmpeg).

Só a GUI thread toca o ``MapCanvas``; os workers apenas carregam dados.
Ao final (sucesso, erro ou cancelamento), o ``AnimationController`` restaura
a composição original e limpa os quadros temporários.
"""

from __future__ import annotations

import logging
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

import cartopy.crs as ccrs
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from cartomet_br.core.config import Config
from cartomet_br.gui.download_dialog import _attach_retry_handler
from cartomet_br.services.animation_service import FrameScaleTracker, frame_filename

logger = logging.getLogger(__name__)


@dataclass
class AnimationSpec:
    """Parâmetros de uma animação (montados pelo diálogo de configuração)."""

    steps: list[int]
    cycle: int
    cycle_date: str
    layer_specs: list[dict]  # kinds animáveis: synoptic | field | blocking | loczcit
    out_path: Path
    frames_dir: Path
    technique: str = "direct"
    loczcit_method: str = "iqr"  # "iqr" | "coherence" (LISA, caro)
    show_loczcit_axis: bool = False
    fmt: str = "gif"  # "gif" | "mp4"
    fps: float = 2.0
    dpi: int = 100


@dataclass
class PreparedData:
    """Produto da fase 1: escala congelada + results retidos por step."""

    frozen_levels: dict = field(default_factory=dict)
    blocking_by_step: dict = field(default_factory=dict)
    loczcit_by_step: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
#  FASE 1 — PRÉ-DOWNLOAD + ESCALA CONGELADA (worker)
# ═══════════════════════════════════════════════════════════════════════════════


class PrepareWorker(QThread):
    """Baixa (cache-first, serializado) os dados de TODOS os steps.

    Campos ECMWF: carrega, alimenta o tracker de escala e descarta os arrays
    (a fase 2 os relê do disco). Bloqueio/LOCZCIT: retém os results (pequenos).
    """

    progress = pyqtSignal(str)
    step_progress = pyqtSignal(int, int)  # (feitos, total)
    finished_ok = pyqtSignal(object)  # PreparedData
    finished_error = pyqtSignal(str)
    finished_cancelled = pyqtSignal()

    def __init__(self, config: Config, spec: AnimationSpec, parent=None):
        super().__init__(parent)
        self.config = config
        self.spec = spec
        self._cancelled = False

    def cancel(self):
        """Cancelamento cooperativo (checado entre downloads/steps)."""
        self._cancelled = True

    def run(self):
        from cartomet_br.data.blocking_engine import BlockingCancelled, compute_blocking
        from cartomet_br.data.ecmwf import VARIABLE_REGISTRY
        from cartomet_br.data.loczcit_pa_engine import LoczcitCancelled, compute_loczcit_pa
        from cartomet_br.services.data_service import DataService

        retry_handler, retry_logger = _attach_retry_handler(lambda m: self.progress.emit(m))
        service = DataService(self.config)
        tracker = FrameScaleTracker()
        prepared = PreparedData()
        spec = self.spec
        try:
            total = len(spec.steps)
            for i, step in enumerate(spec.steps):
                if self._cancelled:
                    self.finished_cancelled.emit()
                    return
                self.step_progress.emit(i, total)
                self.progress.emit(f"Preparando dados do step +{step}h ({i + 1}/{total})...")
                for lspec in spec.layer_specs:
                    if self._cancelled:
                        self.finished_cancelled.emit()
                        return
                    kind = lspec.get("kind")
                    if kind == "synoptic":
                        service.load_synoptic(step, spec.cycle, spec.cycle_date)
                    elif kind == "field":
                        layer_id, data = service.load_field(
                            lspec.get("variable", ""),
                            lspec.get("level") or None,
                            step,
                            spec.cycle,
                            spec.cycle_date,
                            wind_type=lspec.get("wind_type", "barbs"),
                            technique=spec.technique,
                        )
                        var_info = VARIABLE_REGISTRY.get(data.variable, {})
                        tracker.observe(layer_id, data.variable, var_info, data.values)
                    elif kind == "blocking":
                        prepared.blocking_by_step[step] = compute_blocking(
                            cycle=spec.cycle,
                            cycle_date=spec.cycle_date,
                            step=step,
                            data_dir=self.config.grib_dir,
                            clim_dir=self.config.climatology_dir,
                            source=self.config.ecmwf_source,
                            progress_callback=lambda m: self.progress.emit(m),
                            cancel_check=lambda: self._cancelled,
                        )
                    elif kind == "loczcit":
                        prepared.loczcit_by_step[step] = compute_loczcit_pa(
                            cycle=spec.cycle,
                            cycle_date=spec.cycle_date,
                            data_dir=self.config.grib_dir,
                            step=step,
                            source=self.config.ecmwf_source,
                            filter_method=spec.loczcit_method,
                            progress_callback=lambda m: self.progress.emit(m),
                            cancel_check=lambda: self._cancelled,
                        )
            prepared.frozen_levels = tracker.frozen_levels()
            self.step_progress.emit(total, total)
            self.finished_ok.emit(prepared)
        except (BlockingCancelled, LoczcitCancelled):
            self.finished_cancelled.emit()
        except Exception as e:
            logger.exception("Falha na preparação da animação")
            self.finished_error.emit(str(e))
        finally:
            retry_logger.removeHandler(retry_handler)


# ═══════════════════════════════════════════════════════════════════════════════
#  FASE 2 — PRODUTOR DE PAYLOADS (worker) ↔ REPLOT NA GUI (controller)
# ═══════════════════════════════════════════════════════════════════════════════


class FrameLoaderWorker(QThread):
    """Produtor da fase 2: relê do DISCO os campos de cada step e entrega à GUI.

    Roda inteiro dentro de ``cache_only_mode()`` (``threading.local`` — ativo
    só nesta thread): um cache miss vira erro claro, jamais download. Após
    emitir ``frame_ready``, bloqueia até o ``ack()`` da GUI (1 step em voo).
    """

    frame_ready = pyqtSignal(int, int, object)  # (índice, step, payload dict)
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal()
    finished_error = pyqtSignal(str)
    finished_cancelled = pyqtSignal()

    def __init__(self, config: Config, spec: AnimationSpec, prepared: PreparedData, parent=None):
        super().__init__(parent)
        self.config = config
        self.spec = spec
        self.prepared = prepared
        self._ack = threading.Event()
        self._cancelled = False

    def ack(self):
        """GUI terminou o quadro corrente — libera o próximo."""
        self._ack.set()

    def cancel(self):
        """Cancelamento cooperativo; também desbloqueia o produtor."""
        self._cancelled = True
        self._ack.set()

    def run(self):
        from cartomet_br.data.ecmwf import CacheMissError, cache_only_mode
        from cartomet_br.services.data_service import DataService

        service = DataService(self.config)
        spec = self.spec
        try:
            for i, step in enumerate(spec.steps):
                if self._cancelled:
                    self.finished_cancelled.emit()
                    return
                payload: dict = {"synoptic": None, "fields": []}
                with cache_only_mode():
                    for lspec in spec.layer_specs:
                        kind = lspec.get("kind")
                        if kind == "synoptic":
                            payload["synoptic"] = service.load_synoptic(
                                step, spec.cycle, spec.cycle_date
                            )
                        elif kind == "field":
                            layer_id, data = service.load_field(
                                lspec.get("variable", ""),
                                lspec.get("level") or None,
                                step,
                                spec.cycle,
                                spec.cycle_date,
                                wind_type=lspec.get("wind_type", "barbs"),
                                technique=spec.technique,
                            )
                            payload["fields"].append(
                                (layer_id, data, lspec.get("wind_type", "barbs"))
                            )
                payload["blocking"] = self.prepared.blocking_by_step.get(step)
                payload["loczcit"] = self.prepared.loczcit_by_step.get(step)

                self._ack.clear()
                self.frame_ready.emit(i, step, payload)
                self._ack.wait()
                if self._cancelled:
                    self.finished_cancelled.emit()
                    return
            self.finished_ok.emit()
        except CacheMissError as e:
            self.finished_error.emit(
                f"Um campo sumiu do cache entre a preparação e a renderização:\n{e}\n\n"
                "Execute a animação novamente (os downloads serão retomados)."
            )
        except Exception as e:
            logger.exception("Falha ao carregar payload de quadro")
            self.finished_error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════════════
#  FASE 3 — CODIFICAÇÃO (worker)
# ═══════════════════════════════════════════════════════════════════════════════


class EncodeWorker(QThread):
    """Codifica os PNGs em GIF/MP4 (streaming — nunca todos os quadros em RAM)."""

    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(str)  # caminho do arquivo final
    finished_error = pyqtSignal(str)

    def __init__(self, frames: list[Path], spec: AnimationSpec, parent=None):
        super().__init__(parent)
        self.frames = frames
        self.spec = spec

    def run(self):
        from cartomet_br.services.animation_service import encode_gif, encode_mp4

        try:
            self.progress.emit(
                f"Codificando {self.spec.fmt.upper()} ({len(self.frames)} quadros)..."
            )
            if self.spec.fmt == "mp4":
                out = encode_mp4(self.frames, self.spec.out_path, self.spec.fps)
            else:
                out = encode_gif(self.frames, self.spec.out_path, self.spec.fps)
            self.finished_ok.emit(str(out))
        except Exception as e:
            logger.exception("Falha na codificação da animação")
            self.finished_error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════════════
#  ORQUESTRADOR (GUI thread)
# ═══════════════════════════════════════════════════════════════════════════════


class AnimationController(QObject):
    """Orquestra as 3 fases; único dono do snapshot/restauração do canvas.

    Vive na GUI thread. O diálogo de progresso conecta-se aos sinais e chama
    ``cancel()``. Em QUALQUER desfecho (ok/erro/cancelado), a composição
    original do mapa é restaurada e os quadros temporários são apagados.
    """

    phase_changed = pyqtSignal(int, str)  # (1..3, rótulo)
    progress_text = pyqtSignal(str)
    progress_value = pyqtSignal(int, int)  # (feitos, total)
    finished_ok = pyqtSignal(str)  # caminho da animação
    finished_error = pyqtSignal(str)
    finished_cancelled = pyqtSignal()

    def __init__(self, window, spec: AnimationSpec, parent=None):
        super().__init__(parent or window)
        self._window = window
        self._canvas = window.canvas
        self._spec = spec
        self._snapshot: dict | None = None
        self._zorder_reset = 7
        self._frames: list[Path] = []
        self._frozen_ax_pos = None
        self._frozen_blocking_cbar = None
        self._frozen_loczcit_cbar = None
        self._prepare_worker: PrepareWorker | None = None
        self._loader_worker: FrameLoaderWorker | None = None
        self._encode_worker: EncodeWorker | None = None
        self._cancelled = False
        self._done = False

    # ─── ciclo de vida ───────────────────────────────────────────────────

    def start(self):
        self._snapshot = self._canvas.snapshot_composition()
        self._zorder_reset = max(
            7, int(self._snapshot["zorder_base"]) - len(self._snapshot["pl_data"])
        )
        self._freeze_layout_reference()
        self._spec.frames_dir.mkdir(parents=True, exist_ok=True)

        self.phase_changed.emit(1, "1/3 — Preparando dados (cache-first)...")
        worker = PrepareWorker(self._window.config, self._spec, parent=self)
        worker.progress.connect(self.progress_text)
        worker.step_progress.connect(self.progress_value)
        worker.finished_ok.connect(self._on_prepare_ok)
        worker.finished_error.connect(self._fail)
        worker.finished_cancelled.connect(self._on_worker_cancelled)
        self._prepare_worker = worker
        worker.start()

    def cancel(self):
        """Pede cancelamento cooperativo. Na fase 3 (encode) não há aborto —
        a codificação é curta e o worker conclui sozinho."""
        if self._done:
            return
        self._cancelled = True
        idle = True
        for worker in (self._prepare_worker, self._loader_worker):
            if worker is not None and worker.isRunning():
                worker.cancel()
                idle = False
        if self._encode_worker is not None and self._encode_worker.isRunning():
            idle = False
        if idle:
            self._on_worker_cancelled()

    # ─── fase 1 → fase 2 ─────────────────────────────────────────────────

    def _on_prepare_ok(self, prepared: PreparedData):
        self._prepare_worker = None
        if self._cancelled or self._done:
            self._on_worker_cancelled()
            return
        self._canvas.freeze_levels(prepared.frozen_levels)
        total = len(self._spec.steps)
        self.phase_changed.emit(2, f"2/3 — Renderizando {total} quadros...")
        self.progress_value.emit(0, total)

        worker = FrameLoaderWorker(self._window.config, self._spec, prepared, parent=self)
        worker.progress.connect(self.progress_text)
        worker.frame_ready.connect(self._on_frame_ready)
        worker.finished_ok.connect(self._on_frames_done)
        worker.finished_error.connect(self._fail)
        worker.finished_cancelled.connect(self._on_worker_cancelled)
        self._loader_worker = worker
        worker.start()

    def _on_frame_ready(self, index: int, step: int, payload: dict):
        """Replota a composição para o step e salva o PNG (GUI thread)."""
        worker = self._loader_worker
        if self._done or self._cancelled or worker is None:
            return  # worker é desbloqueado pelo cancel()
        try:
            self._apply_payload(payload)
            frame_path = self._spec.frames_dir / frame_filename(index, step)
            self._canvas.render_frame_png(frame_path, dpi=self._spec.dpi)
            self._frames.append(frame_path)
            total = len(self._spec.steps)
            self.progress_value.emit(index + 1, total)
            self.progress_text.emit(f"Quadro +{step}h ({index + 1}/{total})")
        except Exception as exc:
            logger.exception("Falha ao renderizar quadro da animação")
            self._fail(f"Falha ao renderizar o quadro +{step}h: {exc}")
            return
        worker.ack()

    def _apply_payload(self, payload: dict):
        """Replota a composição em ORDEM FIXA e re-aplica o extent do usuário.

        ``plot_blocking_anomaly``/``plot_loczcit_raster`` forçam extent e
        título próprios — por isso o ``set_extent`` final com o extent
        capturado no snapshot garante quadros idênticos ao enquadramento
        que o usuário via.
        """
        canvas = self._canvas
        canvas._pl_zorder_counter = self._zorder_reset
        if payload.get("synoptic") is not None:
            canvas.set_synoptic_data(payload["synoptic"])
        for layer_id, data, wind_type in payload.get("fields", []):
            canvas.add_pl_layer(layer_id, data, wind_type)
        # set_synoptic_data limpa o título (no fluxo ao vivo, quem o recompõe é
        # o toggle_layer); recompõe aqui — bloqueio/LOCZCIT sobrescrevem depois,
        # como no comportamento ao vivo.
        canvas._update_map_title()
        if payload.get("blocking") is not None:
            canvas.plot_blocking_anomaly(payload["blocking"])
        if payload.get("loczcit") is not None:
            canvas.plot_loczcit_raster(payload["loczcit"])
            if self._spec.show_loczcit_axis:
                canvas.plot_loczcit_axis(payload["loczcit"])
        assert self._snapshot is not None
        canvas.ax.set_extent(self._snapshot["extent_xyxy"], crs=ccrs.PlateCarree())
        self._enforce_frozen_layout()
        canvas.draw()

    def _freeze_layout_reference(self):
        """Calcula UMA VEZ o layout de referência e congela a geometria dos eixos.

        Rodar ``tight_layout`` por quadro deixaria a carta "pulando na mesa
        branca": os textos dos centros H/L mudam de posição a cada step (às
        vezes vazando para fora do eixo), e o tight_layout re-espremeria o
        eixo para acomodá-los. Aqui o reflow roda uma única vez — com os
        textos dos centros temporariamente ocultos, para o layout de
        referência não depender do step corrente — e as posições resultantes
        são re-impostas em todos os quadros (``_enforce_frozen_layout``).
        """
        canvas = self._canvas
        center_texts = canvas._synoptic_artists.get("centers", [])
        visible_before = [t.get_visible() for t in center_texts]
        for t in center_texts:
            t.set_visible(False)
        try:
            canvas._reflow_layout()
        finally:
            for t, vis in zip(center_texts, visible_before, strict=False):
                t.set_visible(vis)
        self._frozen_ax_pos = canvas.ax.get_position().frozen()
        cbar = canvas._blocking_colorbar
        self._frozen_blocking_cbar = cbar.ax.get_position().frozen() if cbar is not None else None
        cbar = canvas._loczcit_colorbar
        self._frozen_loczcit_cbar = cbar.ax.get_position().frozen() if cbar is not None else None
        # Suspende o motor da mesa durante os quadros: a geometria congelada é
        # imposta por _enforce_frozen_layout, e cada reflow custaria 2 draws
        # extras por camada/quadro (travadas com rasters pesados, ex. MUR SST).
        canvas._layout_suspended = True

    def _enforce_frozen_layout(self):
        """Re-impõe a geometria congelada — quadros geometricamente idênticos.

        Os replots intermediários (colorbars de bloqueio/LOCZCIT "roubam"
        espaço do eixo ao serem recriadas; o motor da mesa (``_reflow_layout``)
        desloca o conjunto) podem mover os eixos — a palavra final é sempre
        das posições de referência. As colorbars PL são ``inset_axes``
        relativas ao eixo e acompanham automaticamente.
        """
        canvas = self._canvas
        if self._frozen_ax_pos is not None:
            canvas.ax.set_position(self._frozen_ax_pos)
        cbar = canvas._blocking_colorbar
        if cbar is not None and self._frozen_blocking_cbar is not None:
            cbar.ax.set_position(self._frozen_blocking_cbar)
        cbar = canvas._loczcit_colorbar
        if cbar is not None and self._frozen_loczcit_cbar is not None:
            cbar.ax.set_position(self._frozen_loczcit_cbar)

    # ─── fase 2 → fase 3 ─────────────────────────────────────────────────

    def _on_frames_done(self):
        self._loader_worker = None
        if self._cancelled or self._done:
            self._on_worker_cancelled()
            return
        if not self._frames:
            self._fail("Nenhum quadro foi renderizado.")
            return
        self.phase_changed.emit(3, f"3/3 — Codificando {self._spec.fmt.upper()}...")
        worker = EncodeWorker(list(self._frames), self._spec, parent=self)
        worker.progress.connect(self.progress_text)
        worker.finished_ok.connect(self._on_encode_ok)
        worker.finished_error.connect(self._fail)
        self._encode_worker = worker
        worker.start()

    def _on_encode_ok(self, out_path: str):
        self._encode_worker = None
        if self._done:
            return
        self._done = True
        self._restore()
        self.finished_ok.emit(out_path)

    # ─── desfechos ───────────────────────────────────────────────────────

    def _fail(self, message: str):
        if self._done:
            return
        self._done = True
        for worker in (self._prepare_worker, self._loader_worker):
            if worker is not None and worker.isRunning():
                worker.cancel()
        self._restore()
        self.finished_error.emit(message)

    def _on_worker_cancelled(self):
        if self._done:
            return
        self._done = True
        self._restore()
        self.finished_cancelled.emit()

    def _restore(self):
        """Restaura a composição original do mapa e limpa os quadros temporários."""
        canvas = self._canvas
        canvas._layout_suspended = False  # reativa o motor da mesa ANTES do replay
        try:
            if self._snapshot is not None:
                canvas.restore_composition(self._snapshot)
                # Bloqueio/LOCZCIT: replota dos results memorizados na MainWindow
                kinds = {s.get("kind") for s in self._spec.layer_specs}
                if "blocking" in kinds:
                    result = getattr(self._window, "_last_blocking_result", None)
                    if result is not None:
                        canvas.plot_blocking_anomaly(result)
                if "loczcit" in kinds:
                    result = getattr(self._window, "_last_loczcit_result", None)
                    if result is not None:
                        canvas.plot_loczcit_raster(result)
                        axis_on = any(
                            s.get("kind") == "loczcit" and s.get("axis")
                            for s in self._spec.layer_specs
                        )
                        if axis_on:
                            canvas.plot_loczcit_axis(result)
                # plot_blocking/loczcit forçam extent próprio → re-aplica o do usuário
                canvas.ax.set_extent(self._snapshot["extent_xyxy"], crs=ccrs.PlateCarree())
                canvas._reflow_layout()
                canvas.draw()
        except Exception:
            logger.exception("Falha ao restaurar a composição após a animação")
        finally:
            shutil.rmtree(self._spec.frames_dir, ignore_errors=True)
