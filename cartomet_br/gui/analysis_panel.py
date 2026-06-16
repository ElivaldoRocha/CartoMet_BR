"""Base de painéis de análise docados (CartoMet BR v3.0).

``AnalysisDock`` extrai o esqueleto provado de ``SoundingPanel``: um
``QDockWidget`` deslizante (não pop-up) com figura Matplotlib embutida, barra de
progresso indeterminada, badge de honestidade e os estados
placeholder/loading/error. Painéis concretos — meteograma (F6), corte vertical
(F4) — herdam e implementam ``render(result)``.

O painel é puramente de RENDER: os dados chegam prontos de um worker em thread
separada (``analysis_engine``), e nenhuma rede/CPU pesada roda aqui.
"""

from __future__ import annotations

import logging

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt6.QtWidgets import (
    QDockWidget,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class AnalysisDock(QDockWidget):
    """Dock lateral genérico: figura Matplotlib + estados de carga/erro/badge.

    Subclasses devem implementar ``render(result)``. Os helpers de estado
    (``show_placeholder``/``show_loading``/``show_error``) e o badge de
    honestidade já vêm prontos — espelham 1:1 a semântica do ``SoundingPanel``.
    """

    def __init__(
        self,
        title: str,
        parent=None,
        *,
        min_width: int = 460,
        figsize: tuple[float, float] = (7.2, 6.4),
        placeholder: str = "",
    ) -> None:
        super().__init__(title, parent)
        # Painel deslizante: pode mover/flutuar/fechar (igual à Sonda Vertical).
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.setMinimumWidth(min_width)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._header = QLabel(title)
        self._header.setStyleSheet("font-weight: bold; font-size: 12px; padding: 2px;")
        self._header.setWordWrap(True)
        layout.addWidget(self._header)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminado (spinner)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.hide()
        layout.addWidget(self._progress)

        # Badge de honestidade (preenchido p/ produtos do modelo IFS).
        self._badge = QLabel()
        self._badge.setWordWrap(True)
        self._badge.setStyleSheet(
            "background:#FFF3CD; color:#7A5C00; border:1px solid #FFE08A;"
            "border-radius:4px; padding:3px 6px; font-size:10px;"
        )
        self._badge.hide()
        layout.addWidget(self._badge)

        self.fig = Figure(figsize=figsize, facecolor="white", dpi=100)
        self.canvas = FigureCanvas(self.fig)
        layout.addWidget(self.canvas, stretch=1)

        self.setWidget(container)
        self._placeholder = placeholder
        if placeholder:
            self.show_placeholder()

    # ── helpers de desenho ──────────────────────────────────────────────────
    def _show_centered_message(
        self, text: str, color: str = "#333333", facecolor: str = "white"
    ) -> None:
        """Limpa a figura e escreve uma mensagem centralizada."""
        self.fig.clear()
        self.fig.set_facecolor(facecolor)
        ax = self.fig.add_subplot(111)
        ax.axis("off")
        ax.text(
            0.5,
            0.5,
            text,
            ha="center",
            va="center",
            wrap=True,
            fontsize=12,
            color=color,
            transform=ax.transAxes,
        )
        self.canvas.draw_idle()

    def set_badge(self, note: str) -> None:
        """Mostra (ou esconde, se vazio) o badge de honestidade."""
        if note:
            self._badge.setText(f"⚠️ {note}")
            self._badge.show()
        else:
            self._badge.hide()

    def set_header(self, text: str) -> None:
        """Atualiza o cabeçalho e esconde a barra de progresso."""
        self._progress.hide()
        self._header.setText(text)

    # ── estados públicos ────────────────────────────────────────────────────
    def show_placeholder(self, text: str | None = None) -> None:
        self._progress.hide()
        self._badge.hide()
        self._show_centered_message(text or self._placeholder, color="#555555")

    def show_loading(self, label: str, detail: str = "") -> None:
        """Estado de espera enquanto o worker baixa/calcula."""
        self._badge.hide()
        self._header.setText(f"⏳ {label}")
        self._progress.show()
        msg = label if not detail else f"{label}\n{detail}"
        self._show_centered_message(f"Calculando…\n{msg}", color="#1A6FAF")

    def show_error(self, message: str) -> None:
        """Falha de rede/cálculo — mensagem amigável, a GUI nunca quebra."""
        self._progress.hide()
        self._badge.hide()
        self._header.setText("⚠️ Análise indisponível")
        self._show_centered_message(f"⚠️ {message}", color="#B00020")

    def render(self, result) -> None:  # pragma: no cover - abstrato
        raise NotImplementedError
