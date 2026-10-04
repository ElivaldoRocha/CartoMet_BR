"""Vista de Globo em tela cheia — a Terra vista do espaço com os campos ativos.

Janela independente (snapshot da sessão): projeção ``ccrs.Orthographic`` sobre
fundo estrelado, com a superfície substituída pelos campos ativos da carta
(``services/globe_compose.py`` — mesma escala de cores, sem rede), pela imagem
GOES full disk (se carregada) ou pelo relevo natural.

Arquitetura de render (3º ato da lição de campo): o frame completo é VETORIAL
(nitidez da carta — isolinhas, rótulos, colorbars) mas renderizado num
``GlobeFrameWorker`` (QThread) sobre um Agg offscreen; a GUI só troca a imagem
pronta. Arrastar mostra o rascunho (~150 ms) e NUNCA trava — o 1º ato (vetor
na thread da GUI) travava a interface, e o 2º (assar na textura 1×)
serrilhava os rótulos. Scroll aproxima por recorte do frame, duplo-clique
centraliza, setas giram, Esc fecha.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Literal, cast

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.patches import Circle
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cartomet_br.services.globe_compose import (
    GlobeScene,
    draw_contour_overlay,
    draw_synoptic_overlay,
)

logger = logging.getLogger(__name__)

# Raio do disco Orthographic em metros de projeção (≈ raio da Terra).
_ORTHO_HALF = 6.4e6
# Qualidade → regrid_shape do imshow reprojetado (textura de campos/GOES).
_QUALITY_REGRID = {"Rascunho": 400, "Equilibrado": 600, "Alta": 900}
# Retângulo do eixo do globo na figura — compartilhado pelo rascunho, pelo
# worker e pela inversão pixel→lon/lat do duplo-clique.
_AX_RECT = (0.06, 0.05, 0.88, 0.90)

_PELE_CAMPOS = "Campos ativos"
_PELE_GOES = "Satélite GOES"
_PELE_RELEVO = "Relevo natural"

# Workers vivos: a referência Python precisa sobreviver até o finished (QThread
# destruída com C++ rodando = crash); o slot descarta resultados obsoletos.
_LIVE_WORKERS: set[GlobeFrameWorker] = set()


def _visible_from(lon: float, lat: float, center: tuple[float, float]) -> bool:
    """True se (lon, lat) está no hemisfério visível do globo centrado em center.

    Símbolos pontuais do lado OCULTO não podem ser desenhados: a reprojeção
    dos seus polígonos vira lixo visual (bug pego em teste de campo).
    """
    lam1, phi1 = math.radians(center[0]), math.radians(center[1])
    lam2, phi2 = math.radians(lon), math.radians(lat)
    cosd = math.sin(phi1) * math.sin(phi2) + math.cos(phi1) * math.cos(phi2) * math.cos(lam2 - lam1)
    return cosd > 0.03


def _paint_background(fig, stars) -> None:
    """Espaço: preto + estrelas fixas + halo atmosférico azulado."""
    bg = fig.add_axes((0.0, 0.0, 1.0, 1.0), zorder=-10)
    bg.set_axis_off()
    bg.set_xlim(0, 1)
    bg.set_ylim(0, 1)
    xs, ys, sizes = stars
    bg.scatter(xs, ys, s=sizes, c="white", alpha=0.75, linewidths=0)
    # Halo: anéis concêntricos com alpha decrescente ao redor do disco.
    for r, a in ((0.462, 0.20), (0.472, 0.12), (0.484, 0.06), (0.498, 0.03)):
        bg.add_patch(
            Circle((0.5, 0.5), r, facecolor="none", edgecolor="#4da3ff", linewidth=6, alpha=a)
        )


def _paint_goes(ax, satellite, regrid: int) -> None:
    from cartomet_br.data.ecmwf import get_ir_colormap

    geos = ccrs.Geostationary(
        central_longitude=satellite.sat_lon,
        satellite_height=satellite.sat_h,
        sweep_axis=satellite.sat_sweep,
    )
    ax.imshow(
        satellite.data,
        origin="upper",
        extent=(satellite.x.min(), satellite.x.max(), satellite.y.min(), satellite.y.max()),
        transform=geos,
        cmap=get_ir_colormap(),
        vmin=-103.0,
        vmax=84.0,
        zorder=2,
        interpolation="nearest",
        regrid_shape=regrid,
    )


def _paint_colorbars(fig, scene: GlobeScene) -> list:
    """Colorbars que dizem EXATAMENTE o que a textura mostra.

    Armadilha pega em revisão: ``BoundaryNorm(levels, cmap.N)`` distribui as
    bandas UNIFORMEMENTE pelo colormap, mas o contourf da carta (e a textura,
    via ``band_colors``) colore pelo ponto MÉDIO normalizado — para níveis
    irregulares (precipitação!) as duas regras divergem em várias categorias.
    A colorbar é montada com as cores REAIS das bandas, achatadas sobre
    branco como a textura.
    """
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import BoundaryNorm, ListedColormap

    from cartomet_br.services.globe_compose import band_colors

    def achatada(cor) -> tuple[float, float, float, float]:
        arr = np.asarray(cor, dtype=float)
        return (*(arr[:3] * 0.85 + 0.15), 1.0)  # 0.85·cor + 0.15·branco

    colorbars: list = []
    layers = scene.filled_layers[:2]  # no máximo duas — legibilidade
    for i, layer in enumerate(layers):
        levels = np.asarray(layer.style.levels, dtype=float)
        interior, under, over = band_colors(layer.style)
        lcmap = ListedColormap([achatada(c) for c in interior])
        if under is not None:
            lcmap.set_under(achatada(under))
        if over is not None:
            lcmap.set_over(achatada(over))
        norm = BoundaryNorm(levels, ncolors=lcmap.N)  # banda i → cor i, exata
        extend = cast('Literal["neither", "both", "min", "max"]', layer.style.extend)
        cax = fig.add_axes((0.14 + i * 0.40, 0.035, 0.30, 0.016))
        cbar = fig.colorbar(
            ScalarMappable(norm=norm, cmap=lcmap),
            cax=cax,
            orientation="horizontal",
            extend=extend,
        )
        nome = layer.var_info.get("nome", layer.data.variable)
        unidade = layer.data.unit
        sufixo = "" if layer.is_global else " (recorte regional)"
        cbar.set_label(f"{nome} ({unidade}){sufixo}", color="#dfe6f0", fontsize=8)
        cbar.ax.tick_params(labelsize=6.5, colors="#b9c3d4")
        cbar.outline.set_edgecolor("#55657f")
        colorbars.append(cbar)
    return colorbars


def _paint_drawings(ax, drawings: list[dict], center: tuple[float, float]) -> None:
    """Traçado do usuário (records .cmbr) sobre o globo, em todas as peles.

    Mesma fonte de construção da carta (``build_drawing_artist``); emojis
    dependem do pixmap Qt do MapCanvas e ficam fora (aviso na cena). Símbolos
    pontuais e anotações do lado OCULTO são descartados (``_visible_from``).
    """
    if not drawings:
        return
    from cartomet_br.gui.map_canvas import build_drawing_artist
    from cartomet_br.gui.project_io import record_to_command

    for rec in drawings:
        kind = rec.get("type")
        if kind == "emoji":
            continue  # aviso único já entra na cena (MainWindow)
        if kind in ("symbol_point", "annotation") and not _visible_from(
            float(rec.get("x", 0.0)), float(rec.get("y", 0.0)), center
        ):
            continue  # lado oculto do globo
        try:
            build_drawing_artist(ax, record_to_command(rec))
        except Exception as exc:  # noqa: BLE001 — um desenho ruim não derruba o globo
            logger.warning("Desenho %s falhou no globo: %s", kind, exc)


def _stamp_for_skin(scene: GlobeScene, skin: str, satellite, stamp_lines: list[str]) -> list[str]:
    """Linhas do carimbo HONESTAS para a pele em exibição.

    Com a pele GOES o que se vê é a IMAGEM DE SATÉLITE (horário próprio, não
    o step do modelo); com o relevo não há dado do dia nenhum — o carimbo dos
    campos só vale na pele de campos.
    """
    titulo = stamp_lines[:1] or ["CartoMet BR — Vista de Globo"]
    if skin == _PELE_GOES and satellite is not None:
        quando = getattr(satellite, "time_str", "")
        return [*titulo, f"Satélite GOES-East — Banda 13 — {quando}"]
    if skin == _PELE_CAMPOS:
        return stamp_lines
    return [*titulo, "pele: relevo natural (imagem de referência, não é dado do dia)"]


def _paint_stamp(fig, scene: GlobeScene, skin: str, satellite, stamp_lines: list[str]) -> None:
    """Carimbo honesto no canto superior esquerdo + avisos no rodapé."""
    y = 0.975
    for i, linha in enumerate(_stamp_for_skin(scene, skin, satellite, stamp_lines)):
        fig.text(
            0.015,
            y - i * 0.028,
            linha,
            color="#dfe6f0" if i == 0 else "#aab6c8",
            fontsize=10 if i == 0 else 8,
            ha="left",
            va="top",
        )
    if skin != _PELE_CAMPOS:
        return  # avisos de camadas só fazem sentido na pele de campos
    for j, aviso in enumerate(scene.warnings[:3]):
        fig.text(
            0.985,
            0.025 + j * 0.024,
            aviso,
            color="#f0b45a",
            fontsize=7.5,
            ha="right",
            va="bottom",
        )


def compose_globe_frame(
    fig,
    *,
    scene: GlobeScene,
    satellite: Any,
    skin: str,
    quality: str,
    center: tuple[float, float],
    zoom: float,
    stars,
    stamp_lines: list[str],
    drawings: list[dict],
) -> dict:
    """Compõe UM frame completo do globo numa Figure (GUI ou Agg offscreen).

    Fonte única do render de repouso: o ``GlobeFrameWorker`` a executa numa
    thread separada e os testes a executam síncrona. Vetorial de ponta a
    ponta (nitidez da carta); devolve ``{"ax", "colorbars"}`` p/ inspeção.
    """
    fig.clf()
    _paint_background(fig, stars)
    ax = fig.add_axes(_AX_RECT, projection=ccrs.Orthographic(center[0], center[1]))
    ax.set_global()
    ax.patch.set_facecolor("#060a14")  # oceano noturno sob campos translúcidos
    ax.spines["geo"].set_edgecolor("#2a3b5c")
    regrid = _QUALITY_REGRID[quality]
    pc = ccrs.PlateCarree()

    if skin == _PELE_CAMPOS:
        if scene.texture is not None:
            ax.imshow(
                scene.texture,
                extent=[-180, 180, -90, 90],
                transform=pc,
                origin="upper",
                regrid_shape=regrid,
                zorder=2,
                interpolation="nearest",
            )
        else:
            ax.stock_img()  # só isolinhas: carta sinótica clássica sobre o relevo
        if scene.synoptic is not None:
            draw_synoptic_overlay(ax, scene.synoptic, scene.synoptic_kinds, transform=pc)
        draw_contour_overlay(ax, scene.contour_layers, transform=pc)
    elif skin == _PELE_GOES and satellite is not None:
        _paint_goes(ax, satellite, regrid)
    else:
        ax.stock_img()

    # Costa 50m custa ~0,95 s/frame na ortográfica (medido) — fora da thread
    # da GUI isso não trava nada, mas 110m segue o padrão fora da Alta.
    escala_costa = "50m" if quality == "Alta" else "110m"
    ax.add_feature(cfeature.COASTLINE.with_scale(escala_costa), linewidth=0.45, edgecolor="#e8edf5")
    ax.gridlines(color="#55657f", linewidth=0.25, alpha=0.7)

    colorbars = _paint_colorbars(fig, scene) if skin == _PELE_CAMPOS else []
    # O traçado do usuário aparece em TODAS as peles (como na carta, que o
    # desenha sobre campos E satélite).
    _paint_drawings(ax, drawings, center)
    _paint_stamp(fig, scene, skin, satellite, stamp_lines)

    half = _ORTHO_HALF / zoom
    ax.set_xlim(-half, half)
    ax.set_ylim(-half, half)
    return {"ax": ax, "colorbars": colorbars}


class GlobeFrameWorker(QThread):
    """Renderiza um frame completo do globo num Agg offscreen (fora da GUI).

    A Figure/canvas nascem e morrem DENTRO do ``run()`` (thread-safe no Agg).
    O resultado chega por sinal com a geração do pedido — o slot descarta
    frames obsoletos (o usuário pode ter girado de novo nesse meio tempo).
    """

    frame_ready = pyqtSignal(int, object)  # (geração, np.ndarray RGBA | None)

    def __init__(self, gen: int, size_px: tuple[int, int], params: dict, parent=None) -> None:
        super().__init__(parent)
        self._gen = gen
        self._size_px = size_px
        self._params = params

    def run(self) -> None:  # noqa: D102 — contrato da QThread
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg

            w, h = self._size_px
            fig = Figure(figsize=(max(w, 2) / 100.0, max(h, 2) / 100.0), dpi=100, facecolor="black")
            canvas = FigureCanvasAgg(fig)
            compose_globe_frame(fig, **self._params)
            canvas.draw()
            frame = np.asarray(canvas.buffer_rgba()).copy()
        except Exception as exc:  # noqa: BLE001 — frame com erro não derruba a janela
            logger.warning("Frame do globo falhou no worker: %s", exc)
            frame = None
        self.frame_ready.emit(self._gen, frame)


class GlobeCanvas(FigureCanvas):
    """Motor do globo na GUI: rascunho dos gestos + troca de frames do worker."""

    render_state = pyqtSignal(str)  # feedback p/ a barra de status da janela

    def __init__(
        self,
        scene: GlobeScene,
        *,
        center: tuple[float, float],
        satellite: Any = None,  # SatelliteData | None (full disk GOES)
        stamp_lines: list[str] | None = None,
        drawings: list[dict] | None = None,  # records (.cmbr) do traçado do usuário
        parent: QWidget | None = None,
    ) -> None:
        self.fig = Figure(facecolor="black")
        super().__init__(self.fig)
        self.setParent(parent)
        self._scene = scene
        self._satellite = satellite
        self._stamp_lines = stamp_lines or []
        self._drawings = list(drawings or [])
        self._home = (float(center[0]), float(center[1]))
        self._center_lon, self._center_lat = self._home
        self._zoom = 1.0
        if scene.has_fields():
            self._skin = _PELE_CAMPOS
        elif satellite is not None:
            self._skin = _PELE_GOES
        else:
            self._skin = _PELE_RELEVO
        self._quality = "Equilibrado"
        self._ax: Any = None  # GeoAxes do RASCUNHO (frames prontos são imagem crua)
        self._dragging = False
        self._drag_px: tuple[float, float] = (0.0, 0.0)
        self._drag_center0: tuple[float, float] = self._home
        self._draft_busy = False
        self._closed = False
        # Frames assíncronos: geração corrente, worker vivo, pedido pendente,
        # último frame pronto e a vista (centro/zoom) a que ele corresponde.
        self._frame_gen = 0
        self._worker: GlobeFrameWorker | None = None
        self._pending_request = False
        self._last_frame: np.ndarray | None = None
        self._frame_view: tuple[tuple[float, float], float] = (self._home, 1.0)
        self._image_ax: Any = None
        # Estrelas determinísticas (semente fixa = sem cintilação entre frames)
        rng = np.random.default_rng(42)
        self._stars = (rng.random(900), rng.random(900), rng.power(3.0, 900) * 2.2 + 0.2)

        # Repouso: 180 ms após o último gesto, pede o frame nítido ao worker
        # (espelho do _view_settle_timer do MapCanvas).
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(180)
        self._settle_timer.timeout.connect(self.render_full)

        # Modo apresentação: giro lento contínuo em modo rascunho.
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(120)
        self._spin_timer.timeout.connect(self._spin_tick)

        self._cids = [
            self.mpl_connect("button_press_event", self._on_press),
            self.mpl_connect("motion_notify_event", self._on_motion),
            self.mpl_connect("button_release_event", self._on_release),
            self.mpl_connect("scroll_event", self._on_scroll),
        ]
        self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))

    # ─── Estado consultável/ajustável pela janela ────────────────────────────

    @property
    def center(self) -> tuple[float, float]:
        return (self._center_lon, self._center_lat)

    def available_skins(self) -> list[str]:
        skins = []
        if self._scene.has_fields():
            skins.append(_PELE_CAMPOS)
        if self._satellite is not None:
            skins.append(_PELE_GOES)
        skins.append(_PELE_RELEVO)
        return skins

    def set_skin(self, skin: str) -> None:
        if skin in self.available_skins() and skin != self._skin:
            self._skin = skin
            self.render_full()

    def set_quality(self, quality: str) -> None:
        if quality in _QUALITY_REGRID and quality != self._quality:
            self._quality = quality
            self.render_full()

    def set_spinning(self, on: bool) -> None:
        if on:
            self._settle_timer.stop()
            self._spin_timer.start()
        else:
            self._spin_timer.stop()
            self.render_full()

    @property
    def spinning(self) -> bool:
        return self._spin_timer.isActive()

    def rotate_by(self, dlon: float, dlat: float) -> None:
        self._set_center(self._center_lon + dlon, self._center_lat + dlat)
        self.render_draft()
        self._settle_timer.start()

    def go_home(self) -> None:
        self._set_center(*self._home)
        self._zoom = 1.0
        self.render_draft()
        self._settle_timer.start()

    def shutdown(self) -> None:
        """Para timers, desconecta eventos e SOLTA a cena (chamado no close).

        A cena global (textura + campos 0.25° inteiros + full disk GOES) é o
        grosso da memória. O worker em voo não é esperado: a geração avança e
        o resultado dele é descartado no slot (referência viva em
        ``_LIVE_WORKERS`` até o finished — QThread não pode morrer rodando).
        """
        self._closed = True
        self._frame_gen += 1  # qualquer frame em voo fica obsoleto
        self._settle_timer.stop()
        self._spin_timer.stop()
        for cid in self._cids:
            self.mpl_disconnect(cid)
        self.fig.clf()
        self._scene = GlobeScene(texture=None)
        self._satellite = None
        self._last_frame = None
        self._ax = None
        self._image_ax = None

    def _set_center(self, lon: float, lat: float) -> None:
        self._center_lon = ((lon + 180.0) % 360.0) - 180.0
        self._center_lat = float(np.clip(lat, -89.0, 89.0))

    # ─── Rascunho (GUI, síncrono e barato) ───────────────────────────────────

    def render_draft(self) -> None:
        """Frame leve do gesto (~150 ms): costa + grade, sem textura."""
        if self._draft_busy:
            return
        self._draft_busy = True
        try:
            self.fig.clf()
            self._image_ax = None
            _paint_background(self.fig, self._stars)
            ax: Any = self.fig.add_axes(
                _AX_RECT, projection=ccrs.Orthographic(self._center_lon, self._center_lat)
            )
            ax.set_global()
            ax.patch.set_facecolor("#060a14")
            ax.spines["geo"].set_edgecolor("#2a3b5c")
            ax.add_feature(
                cfeature.COASTLINE.with_scale("110m"), linewidth=0.5, edgecolor="#cfd8e3"
            )
            ax.gridlines(color="#3a4a66", linewidth=0.3)
            half = _ORTHO_HALF / self._zoom
            ax.set_xlim(-half, half)
            ax.set_ylim(-half, half)
            self._ax = ax
            self.draw()
        finally:
            self._draft_busy = False

    # ─── Frame nítido (worker assíncrono) ────────────────────────────────────

    def render_full(self) -> None:
        """Pede ao worker o frame vetorial nítido da vista atual (não bloqueia)."""
        if self._closed:
            return
        self._frame_gen += 1
        if self._worker is not None:
            self._pending_request = True  # o worker atual termina; pedimos de novo
            return
        self._start_worker()

    def _start_worker(self) -> None:
        self._pending_request = False
        w = max(2, int(self.fig.bbox.width))
        h = max(2, int(self.fig.bbox.height))
        params = {
            "scene": self._scene,
            "satellite": self._satellite,
            "skin": self._skin,
            "quality": self._quality,
            "center": (self._center_lon, self._center_lat),
            "zoom": self._zoom,
            "stars": self._stars,
            "stamp_lines": self._stamp_lines,
            "drawings": self._drawings,
        }
        worker = GlobeFrameWorker(self._frame_gen, (w, h), params)
        worker.frame_ready.connect(self._on_frame_ready)
        worker.finished.connect(lambda wk=worker: self._on_worker_finished(wk))
        _LIVE_WORKERS.add(worker)
        self._worker = worker
        self.render_state.emit("Renderizando em alta qualidade...")
        worker.start()

    def _on_worker_finished(self, worker: GlobeFrameWorker) -> None:
        _LIVE_WORKERS.discard(worker)
        if self._worker is worker:
            self._worker = None
        if self._pending_request and not self._closed:
            self._start_worker()

    def _on_frame_ready(self, gen: int, frame) -> None:
        if self._closed or gen != self._frame_gen:
            return  # obsoleto: o usuário girou de novo (ou a janela fechou)
        if frame is None:
            self.render_state.emit("Falha no render — veja o log")
            return
        self._last_frame = frame
        self._frame_view = ((self._center_lon, self._center_lat), self._zoom)
        self._show_frame(frame)
        self.render_state.emit("")

    def _show_frame(self, frame: np.ndarray) -> None:
        """Troca a tela pelo frame pronto (imagem crua 1:1 — ~50 ms)."""
        self.fig.clf()
        self._ax = None
        ax = self.fig.add_axes((0.0, 0.0, 1.0, 1.0))
        ax.set_axis_off()
        ax.imshow(frame, interpolation="nearest")
        self._image_ax = ax
        self.draw_idle()

    # ─── Gestos ──────────────────────────────────────────────────────────────

    def _px_to_lonlat(self, x: float, y: float) -> tuple[float, float] | None:
        """Pixel do canvas → lon/lat, válido no rascunho E no frame pronto.

        Usa a VISTA DO QUE ESTÁ NA TELA (frame pronto pode ser de instantes
        atrás): inverte o retângulo ``_AX_RECT`` manualmente — não dependemos
        de um GeoAxes vivo (o frame é imagem crua).
        """
        if self._image_ax is not None:
            (center, zoom) = self._frame_view
        else:
            center, zoom = (self._center_lon, self._center_lat), self._zoom
        wf = float(self.fig.bbox.width)
        hf = float(self.fig.bbox.height)
        if wf < 2 or hf < 2:
            return None
        fx = (x / wf - _AX_RECT[0]) / _AX_RECT[2]
        fy = (y / hf - _AX_RECT[1]) / _AX_RECT[3]
        if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
            return None
        half = _ORTHO_HALF / zoom
        data_x = (fx - 0.5) * 2.0 * half
        data_y = (fy - 0.5) * 2.0 * half
        proj = ccrs.Orthographic(center[0], center[1])
        lon, lat = ccrs.PlateCarree().transform_point(data_x, data_y, proj)
        if not (np.isfinite(lon) and np.isfinite(lat)):
            return None  # fora do disco
        return float(lon), float(lat)

    def _on_press(self, event) -> None:
        if event.button != 1:
            return
        if self._spin_timer.isActive():
            self._spin_timer.stop()
        if getattr(event, "dblclick", False):
            alvo = self._px_to_lonlat(float(event.x), float(event.y))
            if alvo is not None:
                self._set_center(*alvo)
                self.render_draft()
                self._settle_timer.start()
            return
        self._dragging = True
        self._drag_px = (float(event.x), float(event.y))
        self._drag_center0 = (self._center_lon, self._center_lat)
        self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))

    def _on_motion(self, event) -> None:
        if not self._dragging:
            return
        # Pixels, nunca xdata: em Orthographic xdata é metros e None fora do
        # disco — pixels funcionam durante todo o arraste.
        dx = float(event.x) - self._drag_px[0]
        dy = float(event.y) - self._drag_px[1]
        # Sensibilidade: meio disco (~90°) por meia altura do canvas; a
        # superfície SEGUE a mão (arrastar p/ leste gira o centro p/ oeste).
        h = max(1.0, float(self.fig.bbox.height))
        k = 180.0 / h / self._zoom
        lon0, lat0 = self._drag_center0
        novo_lon = lon0 - dx * k
        novo_lat = lat0 + dy * k
        if abs(novo_lon - self._center_lon) < 0.4 and abs(novo_lat - self._center_lat) < 0.4:
            return
        self._set_center(novo_lon, novo_lat)
        self.render_draft()

    def _on_release(self, event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        self._settle_timer.start()

    def _on_scroll(self, event) -> None:
        fator = 1.25 if event.button == "up" else 0.8
        self._zoom = float(np.clip(self._zoom * fator, 1.0, 8.0))
        if self._image_ax is not None:
            self._crop_zoom_image()
        elif self._ax is not None:
            half = _ORTHO_HALF / self._zoom
            self._ax.set_xlim(-half, half)
            self._ax.set_ylim(-half, half)
        # draw_idle coalesce ticks consecutivos da rodinha.
        self.draw_idle()
        self._settle_timer.start()

    def _crop_zoom_image(self) -> None:
        """Zoom instantâneo por RECORTE do frame pronto (nítido chega no repouso)."""
        if self._image_ax is None or self._last_frame is None:
            return
        h, w = self._last_frame.shape[:2]
        rel = self._zoom / max(self._frame_view[1], 1e-6)
        half_w = w / 2.0 / rel
        half_h = h / 2.0 / rel
        self._image_ax.set_xlim(w / 2.0 - half_w, w / 2.0 + half_w)
        self._image_ax.set_ylim(h / 2.0 + half_h, h / 2.0 - half_h)  # origem no topo

    def _spin_tick(self) -> None:
        self._set_center(self._center_lon - 1.2, self._center_lat)
        self.render_draft()

    # ─── Exportação ──────────────────────────────────────────────────────────

    def save_png(self, destino) -> bool:
        """Salva o ÚLTIMO frame nítido (ou a figura atual como fallback)."""
        try:
            if self._last_frame is not None:
                import matplotlib.image as mpimg

                mpimg.imsave(str(destino), self._last_frame)
            else:
                self.fig.savefig(destino, dpi=150, facecolor="black")
            return True
        except OSError as exc:
            logger.warning("PNG do globo falhou: %s", exc)
            return False


class GlobeWindow(QMainWindow):
    """Casca fullscreen do globo: barra de controles + GlobeCanvas.

    SEM ``WA_DeleteOnClose``: o Qt deletaria o C++ do FigureCanvas com os
    wrappers matplotlib ainda vivos e o GC do Python estouraria num access
    violation depois (visto em teste). Fechar esconde + ``closed`` avisa o
    dono, que solta a referência — o Python destrói na ordem certa. Para
    isso funcionar a janela precisa nascer SEM parent Qt (revisão pegou o
    vazamento: com parent, a posse C++ é do MainWindow e soltar a referência
    Python não destrói nada — cada ciclo abrir/fechar acumulava uma janela).
    A tela do fullscreen vem por parâmetro em ``show_fullscreen_on_screen``.
    """

    closed = pyqtSignal()

    _DICA = "Arraste para girar · scroll aproxima · duplo clique centraliza"

    def __init__(
        self,
        scene: GlobeScene,
        *,
        center: tuple[float, float],
        satellite: Any = None,
        stamp_lines: list[str] | None = None,
        drawings: list[dict] | None = None,
        output_dir: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        from cartomet_br.gui.themes import DARK_STYLE

        self.setWindowTitle("Vista de Globo — CartoMet BR")
        self.setStyleSheet(DARK_STYLE)
        self._output_dir = output_dir

        self.globe = GlobeCanvas(
            scene,
            center=center,
            satellite=satellite,
            stamp_lines=stamp_lines,
            drawings=drawings,
            parent=self,
        )
        self.globe.render_state.connect(self._on_render_state)

        barra = QWidget()
        lay = QHBoxLayout(barra)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)

        voltar = QPushButton("⟵ Voltar (Esc)")
        voltar.clicked.connect(self.close)
        lay.addWidget(voltar)

        home = QPushButton("🏠 Centro")
        home.setToolTip("Volta o globo ao centro da sua região (tecla Home).")
        home.clicked.connect(self.globe.go_home)
        lay.addWidget(home)

        self.spin_btn = QPushButton("▶ Apresentação")
        self.spin_btn.setCheckable(True)
        self.spin_btn.setToolTip("Gira o globo sozinho, devagar — qualquer gesto pausa.")
        self.spin_btn.toggled.connect(self.globe.set_spinning)
        lay.addWidget(self.spin_btn)

        lay.addWidget(QLabel("Pele:"))
        self.skin_combo = QComboBox()
        self.skin_combo.addItems(self.globe.available_skins())
        self.skin_combo.currentTextChanged.connect(self.globe.set_skin)
        lay.addWidget(self.skin_combo)

        lay.addWidget(QLabel("Qualidade:"))
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(list(_QUALITY_REGRID))
        self.quality_combo.setCurrentText("Equilibrado")
        self.quality_combo.currentTextChanged.connect(self.globe.set_quality)
        lay.addWidget(self.quality_combo)

        png_btn = QPushButton("💾 PNG")
        png_btn.setToolTip("Salva a vista atual do globo (fundo estrelado incluso).")
        png_btn.clicked.connect(self._save_png)
        lay.addWidget(png_btn)

        self.status = QLabel(self._DICA)
        self.status.setStyleSheet("color: #95A5A6; font-size: 11px;")
        lay.addWidget(self.status, stretch=1)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(barra)
        v.addWidget(self.globe, stretch=1)
        self.setCentralWidget(central)

        # Esc fecha com o foco em QUALQUER filho (atalho de janela).
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.activated.connect(self.close)

    def _on_render_state(self, msg: str) -> None:
        self.status.setText(msg or self._DICA)

    def show_fullscreen_on_screen(self, screen=None) -> None:
        """Tela cheia no monitor dado (o do MainWindow — multi-monitor correto).

        Rascunho IMEDIATO (nada de tela preta) + frame nítido chega do worker.
        """
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.globe.render_draft()
        self.globe.render_full()

    def keyPressEvent(self, event) -> None:  # noqa: N802 — override Qt
        key = event.key()
        if key == Qt.Key.Key_Escape:
            # Redundante com o QShortcut de janela — mas o atalho exige
            # janela ATIVA (nem sempre verdade em offscreen/multi-monitor).
            self.close()
        elif key == Qt.Key.Key_Left:
            self.globe.rotate_by(-10.0, 0.0)
        elif key == Qt.Key.Key_Right:
            self.globe.rotate_by(10.0, 0.0)
        elif key == Qt.Key.Key_Up:
            self.globe.rotate_by(0.0, 10.0)
        elif key == Qt.Key.Key_Down:
            self.globe.rotate_by(0.0, -10.0)
        elif key == Qt.Key.Key_Home:
            self.globe.go_home()
        else:
            super().keyPressEvent(event)

    def _save_png(self) -> None:
        from pathlib import Path

        out_dir = Path(self._output_dir) if self._output_dir else Path.cwd()
        out_dir.mkdir(parents=True, exist_ok=True)
        destino = out_dir / f"globo_{datetime.now():%Y%m%d_%H%M%S}.png"
        if self.globe.save_png(destino):
            self.status.setText(f"Globo salvo: {destino}")
        else:
            self.status.setText("Erro ao salvar PNG — veja o log")

    def closeEvent(self, event) -> None:  # noqa: N802 — override Qt
        self.globe.shutdown()
        self.closed.emit()
        super().closeEvent(event)
