"""
Diálogo da animação de steps (GIF/MP4) — configuração + progresso.

Página 1 (configuração): intervalo de steps LIMITADO ao alcance da rodada —
o erro "rodada 06Z tem alcance máximo de +144h" torna-se estruturalmente
impossível, pois valores inválidos nunca são ofertados. Também: passo,
formato (MP4 desabilitado sem o extra ``animation``), velocidade, resolução,
aviso de custo do LISA e destino.

Página 2 (progresso): fases 1/3–3/3, barra, status (inclui retries HTTP 429)
e cancelamento cooperativo. O diálogo é MODAL — efeito colateral desejado:
congela o tamanho do canvas, garantindo quadros com dimensões constantes.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from cartomet_br.gui.animation_engine import AnimationSpec
from cartomet_br.services.animation_service import (
    build_animation_steps,
    composition_tag,
    default_animation_filename,
    max_step_for_composition,
    max_step_for_cycle,
    min_start_for_composition,
    mp4_available,
)

# Após pedir cancelamento, se o worker não parar em N ms (preso num download
# bloqueante), o diálogo oferece uma escotilha de escape ("Fechar assim mesmo").
ESCAPE_GRACE_MS = 5000

FPS_OPTIONS = (1.0, 2.0, 4.0, 6.0)
DPI_OPTIONS = ((100, "100 DPI — leve"), (150, "150 DPI — padrão"))
STRIDE_OPTIONS = (
    (0, "Nativo (3h; 6h após +144h)"),
    (6, "6 em 6 horas"),
    (12, "12 em 12 horas"),
    (24, "24 em 24 horas"),
)
DEFAULT_SPAN_H = 72  # intervalo default: step atual → +72h (ou o alcance da rodada)


class AnimationDialog(QDialog):
    """Configuração e progresso da animação de steps."""

    generate_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    escape_requested = pyqtSignal()  # fechar à força quando um download travou

    def __init__(
        self,
        *,
        cycle: int,
        cycle_date: str,
        layer_specs: list[dict],
        technique: str,
        current_step: int,
        charts_dir: Path,
        layer_labels: list[str],
        static_labels: list[str],
        loczcit_lisa: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._cycle = cycle
        self._cycle_date = cycle_date
        self._layer_specs = layer_specs
        self._technique = technique
        self._charts_dir = charts_dir
        self._loczcit_lisa = loczcit_lisa
        self._dest_user_edited = False
        self._in_progress = False
        self._allow_close = True
        self._escape_offered = False

        self._cap = max_step_for_composition(layer_specs, cycle)
        self._min_start = min_start_for_composition(layer_specs, technique)
        self._allowed_steps = build_animation_steps(self._min_start, self._cap, 0, cycle)

        self.setWindowTitle("Animação de Steps (GIF/MP4)")
        self.setModal(True)
        self.setMinimumWidth(520)

        self._stack = QStackedWidget()
        root = QVBoxLayout(self)
        root.addWidget(self._stack)
        self._stack.addWidget(self._build_config_page(layer_labels, static_labels, current_step))
        self._stack.addWidget(self._build_progress_page())
        self._refresh_estimate()
        self._refresh_destination()

    # ═══════════════════════════════════════════════════════════════════
    #  PÁGINA 1 — CONFIGURAÇÃO
    # ═══════════════════════════════════════════════════════════════════

    def _build_config_page(
        self, layer_labels: list[str], static_labels: list[str], current_step: int
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        # ── Resumo da composição ──
        summary = "Serão animadas: " + (", ".join(layer_labels) or "—")
        if static_labels:
            summary += "\nEstáticas (não mudam com o step): " + ", ".join(static_labels)
        summary_label = QLabel(summary)
        summary_label.setWordWrap(True)
        summary_label.setStyleSheet("font-size: 11px; color: #BDC3C7;")
        layout.addWidget(summary_label)

        # ── Rodada (somente leitura) ──
        try:
            date_fmt = datetime.strptime(self._cycle_date, "%Y%m%d").strftime("%d/%m/%Y")
        except ValueError:
            date_fmt = self._cycle_date
        cap_note = ""
        if self._cap < max_step_for_cycle(self._cycle):
            cap_note = " (ZCIT: a OLR madura da Técnica B limita a +228h)"
        rodada_label = QLabel(
            f"Rodada: {self._cycle:02d}Z {date_fmt} — alcance até +{self._cap}h{cap_note}"
        )
        rodada_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        rodada_label.setWordWrap(True)
        layout.addWidget(rodada_label)

        # ── Intervalo de steps ──
        range_group = QGroupBox("Intervalo de previsão")
        range_layout = QVBoxLayout(range_group)

        row = QHBoxLayout()
        row.addWidget(QLabel("De:"))
        self.start_combo = QComboBox()
        for s in self._allowed_steps:
            self.start_combo.addItem(f"+{s}h", s)
        row.addWidget(self.start_combo)
        row.addWidget(QLabel("até:"))
        self.end_combo = QComboBox()
        row.addWidget(self.end_combo)
        row.addWidget(QLabel("passo:"))
        self.stride_combo = QComboBox()
        for stride, label in STRIDE_OPTIONS:
            self.stride_combo.addItem(label, stride)
        row.addWidget(self.stride_combo)
        row.addStretch()
        range_layout.addLayout(row)

        self.range_note = QLabel("")
        self.range_note.setWordWrap(True)
        self.range_note.setStyleSheet("font-size: 10px; color: #F39C12;")
        range_layout.addWidget(self.range_note)
        layout.addWidget(range_group)

        # Defaults: início = step atual (ajustado à grade), fim = +72h (ou cap)
        start_default = self._closest_allowed(max(current_step, self._min_start))
        self._select_data(self.start_combo, start_default)
        self._repopulate_end_combo()
        self._select_data(self.end_combo, self._closest_allowed(start_default + DEFAULT_SPAN_H))

        self.start_combo.currentIndexChanged.connect(self._on_range_changed)
        self.end_combo.currentIndexChanged.connect(self._on_params_changed)
        self.stride_combo.currentIndexChanged.connect(self._on_params_changed)

        # ── Saída (formato / velocidade / resolução) ──
        out_group = QGroupBox("Saída")
        out_layout = QVBoxLayout(out_group)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Formato:"))
        self.gif_radio = QRadioButton("GIF")
        self.gif_radio.setChecked(True)
        self.mp4_radio = QRadioButton("MP4 (H.264)")
        if not mp4_available():
            self.mp4_radio.setEnabled(False)
            self.mp4_radio.setToolTip(
                "Exportação MP4 requer o extra de animação:\nuv sync --extra animation"
            )
        fmt_row.addWidget(self.gif_radio)
        fmt_row.addWidget(self.mp4_radio)
        fmt_row.addSpacing(16)
        fmt_row.addWidget(QLabel("Velocidade:"))
        self.fps_combo = QComboBox()
        for fps in FPS_OPTIONS:
            self.fps_combo.addItem(f"{fps:g} quadros/s", fps)
        self.fps_combo.setCurrentIndex(1)  # 2 fps
        fmt_row.addWidget(self.fps_combo)
        fmt_row.addStretch()
        out_layout.addLayout(fmt_row)

        dpi_row = QHBoxLayout()
        dpi_row.addWidget(QLabel("Resolução:"))
        self.dpi_combo = QComboBox()
        for dpi, label in DPI_OPTIONS:
            self.dpi_combo.addItem(label, dpi)
        dpi_row.addWidget(self.dpi_combo)
        dpi_row.addStretch()
        out_layout.addLayout(dpi_row)
        layout.addWidget(out_group)

        self.gif_radio.toggled.connect(self._on_params_changed)

        # ── Aviso LISA (só se o LOCZCIT atual usa coerência espacial) ──
        self.lisa_check = None
        if self._loczcit_lisa:
            lisa_group = QGroupBox("ZCIT (LOCZCIT-PA) — filtro LISA detectado")
            lisa_layout = QVBoxLayout(lisa_group)
            warn = QLabel(
                "A Coerência Espacial (LISA) roda simulações Monte Carlo em CADA "
                "quadro — uma animação longa pode levar dezenas de minutos."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #F39C12; font-size: 11px;")
            lisa_layout.addWidget(warn)
            self.lisa_check = QCheckBox(
                "Usar IQR nesta animação (rápido; semente fixa garante consistência)"
            )
            self.lisa_check.setChecked(True)
            lisa_layout.addWidget(self.lisa_check)
            layout.addWidget(lisa_group)

        # ── Estimativa ──
        self.estimate_label = QLabel("")
        self.estimate_label.setStyleSheet("font-size: 11px; color: #3498DB;")
        layout.addWidget(self.estimate_label)

        # ── Destino ──
        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Salvar em:"))
        self.dest_edit = QLineEdit()
        self.dest_edit.textEdited.connect(self._on_dest_edited)
        dest_row.addWidget(self.dest_edit, stretch=1)
        browse_btn = QPushButton("Procurar...")
        browse_btn.clicked.connect(self._on_browse)
        dest_row.addWidget(browse_btn)
        layout.addLayout(dest_row)

        # ── Botões ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self.generate_btn = QPushButton("Gerar animação")
        self.generate_btn.setDefault(True)
        self.generate_btn.setStyleSheet("""
            QPushButton {
                background-color: #27AE60; padding: 8px 20px;
                font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #2ECC71; }
        """)
        self.generate_btn.clicked.connect(self._on_generate)
        btn_row.addWidget(self.generate_btn)
        layout.addLayout(btn_row)
        return page

    # ═══════════════════════════════════════════════════════════════════
    #  PÁGINA 2 — PROGRESSO
    # ═══════════════════════════════════════════════════════════════════

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        self.phase_label = QLabel("1/3 — Preparando dados...")
        self.phase_label.setStyleSheet("font-size: 12px; font-weight: bold; color: #3498DB;")
        layout.addWidget(self.phase_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setMinimumHeight(22)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 10px; color: #95A5A6;")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #E74C3C; padding: 6px 20px;
                font-size: 11px; min-width: 80px;
            }
            QPushButton:hover { background-color: #FF6666; }
        """)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return page

    # ═══════════════════════════════════════════════════════════════════
    #  LÓGICA DA CONFIGURAÇÃO
    # ═══════════════════════════════════════════════════════════════════

    def _closest_allowed(self, step: int) -> int:
        return min(self._allowed_steps, key=lambda s: abs(s - step))

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _repopulate_end_combo(self) -> None:
        """O combo final só oferece steps DEPOIS do inicial (e dentro do alcance)."""
        start = self.start_combo.currentData()
        previous = self.end_combo.currentData()
        self.end_combo.blockSignals(True)
        self.end_combo.clear()
        for s in self._allowed_steps:
            if start is not None and s > start:
                self.end_combo.addItem(f"+{s}h", s)
        if previous is not None:
            self._select_data(self.end_combo, previous)
        if self.end_combo.currentIndex() < 0 and self.end_combo.count() > 0:
            self.end_combo.setCurrentIndex(self.end_combo.count() - 1)
        self.end_combo.blockSignals(False)

    def _on_range_changed(self) -> None:
        self._repopulate_end_combo()
        self._on_params_changed()

    def _on_params_changed(self) -> None:
        self._refresh_estimate()
        self._refresh_destination()

    def _current_steps(self) -> list[int]:
        start = self.start_combo.currentData()
        end = self.end_combo.currentData()
        stride = self.stride_combo.currentData() or 0
        if start is None or end is None:
            return []
        return build_animation_steps(int(start), int(end), int(stride), self._cycle)

    def _refresh_estimate(self) -> None:
        steps = self._current_steps()
        n = len(steps)
        txt = f"{n} quadros"
        stride = self.stride_combo.currentData() or 0
        if steps and stride == 0 and steps[0] < 144 < steps[-1]:
            self.range_note.setText(
                "O intervalo cruza +144h: a cadência nativa muda de 3h para 6h "
                '(o tempo da animação "acelera" levemente após +144h).'
            )
        else:
            self.range_note.setText("")
        if n >= 2:
            txt += " · download cache-first (steps já baixados não contam)"
        else:
            txt = "Intervalo insuficiente — selecione ao menos 2 steps."
        self.estimate_label.setText(txt)
        if hasattr(self, "generate_btn"):
            self.generate_btn.setEnabled(n >= 2)

    def _refresh_destination(self) -> None:
        if self._dest_user_edited:
            return
        steps = self._current_steps()
        if not steps:
            return
        fmt = "mp4" if self.mp4_radio.isChecked() else "gif"
        stride = self.stride_combo.currentData() or 0
        name = default_animation_filename(
            composition_tag(self._layer_specs),
            self._cycle,
            self._cycle_date,
            steps[0],
            steps[-1],
            int(stride),
            fmt,
        )
        self.dest_edit.setText(str(self._charts_dir / name))

    def _on_dest_edited(self) -> None:
        self._dest_user_edited = True

    def _on_browse(self) -> None:
        fmt = "mp4" if self.mp4_radio.isChecked() else "gif"
        current = self.dest_edit.text() or str(self._charts_dir)
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Salvar animação", current, f"Animação (*.{fmt})"
        )
        if filepath:
            self.dest_edit.setText(filepath)
            self._dest_user_edited = True

    def _on_generate(self) -> None:
        if not self.dest_edit.text().strip():
            QMessageBox.warning(self, "Animação de Steps", "Informe o arquivo de destino.")
            return
        self.generate_requested.emit()

    # ═══════════════════════════════════════════════════════════════════
    #  SPEC + PROGRESSO (API usada pela MainWindow / AnimationController)
    # ═══════════════════════════════════════════════════════════════════

    def build_spec(self) -> AnimationSpec | None:
        """Monta o AnimationSpec a partir dos widgets (None se inválido)."""
        steps = self._current_steps()
        if len(steps) < 2:
            QMessageBox.warning(
                self, "Animação de Steps", "Intervalo insuficiente — selecione ao menos 2 steps."
            )
            return None
        fmt = "mp4" if self.mp4_radio.isChecked() else "gif"
        out_path = Path(self.dest_edit.text().strip())
        if out_path.suffix.lower() != f".{fmt}":
            out_path = out_path.with_suffix(f".{fmt}")

        lisa_kept = (
            self._loczcit_lisa and self.lisa_check is not None and not self.lisa_check.isChecked()
        )
        loczcit_method = "coherence" if lisa_kept else "iqr"
        show_axis = any(s.get("kind") == "loczcit" and s.get("axis") for s in self._layer_specs)

        return AnimationSpec(
            steps=steps,
            cycle=self._cycle,
            cycle_date=self._cycle_date,
            layer_specs=self._layer_specs,
            out_path=out_path,
            frames_dir=Path(tempfile.mkdtemp(prefix="cartomet_anim_")),
            technique=self._technique,
            loczcit_method=loczcit_method,
            show_loczcit_axis=show_axis,
            fmt=fmt,
            fps=float(self.fps_combo.currentData()),
            dpi=int(self.dpi_combo.currentData()),
        )

    def enter_progress_mode(self) -> None:
        """Troca para a página de progresso e bloqueia o fechamento pelo X."""
        self._in_progress = True
        self._allow_close = False
        self._stack.setCurrentIndex(1)

    def set_phase(self, _n: int, label: str) -> None:
        self.phase_label.setText(label)
        self.progress_bar.setValue(0)
        # A fase 3 (encode) é curta e não tem aborto cooperativo
        self.cancel_btn.setEnabled(_n < 3)

    def set_progress(self, done: int, total: int) -> None:
        self.progress_bar.setValue(int(100 * done / max(total, 1)))

    def set_status(self, msg: str) -> None:
        is_retry = "429" in msg or "tentativa" in msg or "Aguardando" in msg
        color = "#E74C3C" if is_retry else "#95A5A6"
        self.status_label.setStyleSheet(f"font-size: 10px; color: {color};")
        self.status_label.setText(msg)

    def force_close(self) -> None:
        """Fechamento programático (teardown da MainWindow)."""
        self._allow_close = True
        self.close()

    def _on_cancel_clicked(self) -> None:
        # Após a escotilha aparecer, o botão vira "Fechar assim mesmo" — o clique
        # (ou o X/Esc, que caem aqui) força a saída em vez de re-pedir cancelamento.
        if self._escape_offered:
            self.escape_requested.emit()
            return
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Cancelando...")
        self.cancel_requested.emit()
        QTimer.singleShot(ESCAPE_GRACE_MS, self._maybe_offer_escape)

    def _maybe_offer_escape(self) -> None:
        """Se o cancelamento não fez efeito (download preso), abre a escotilha."""
        if self._escape_offered or self._allow_close or not self._in_progress:
            return
        self._escape_offered = True
        self.status_label.setText(
            "O download parece travado (rede lenta). Você pode fechar — "
            "ele continua em 2º plano e não trava o programa."
        )
        self.cancel_btn.setText("Fechar assim mesmo")
        self.cancel_btn.setEnabled(True)

    def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if self._in_progress and not self._allow_close:
            # X durante o processamento = pedido de cancelamento (o teardown
            # da MainWindow fecha o diálogo quando o controller finalizar)
            self._on_cancel_clicked()
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        """Esc/Cancelar: na página de progresso vira pedido de cancelamento."""
        if self._in_progress and not self._allow_close:
            self._on_cancel_clicked()
            return
        super().reject()
