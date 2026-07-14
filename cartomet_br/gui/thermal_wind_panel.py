"""Painel do Vento Térmico (hodógrafa num ponto) — dinâmica da Rosa dos Ventos.

Dock deslizante que desenha a hodógrafa do vento do modelo IFS num ponto
clicado, com os vetores de vento térmico coloridos por advecção. Puramente de
RENDER — os dados chegam prontos do ``ThermalWindWorker`` (um
``ThermalWindResult``). Botões: "Camada…" (troca base→topo, com memória na
MainWindow), "📌 Fixar no mapa" (ancora a hodógrafa no ponto, georreferenciada)
e "Remover do mapa" (tira a fixada).
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cartomet_br.charts.hodograph_plot import render_hodograph
from cartomet_br.gui.analysis_panel import AnalysisDock

logger = logging.getLogger(__name__)

_PLACEHOLDER = (
    "Ative o Vento Térmico e clique num ponto do mapa.\n\n"
    "Hodógrafa do vento do modelo IFS por nível de pressão, com os vetores de\n"
    "vento térmico coloridos por advecção (🔴 quente / 🔵 fria) — ciente do\n"
    "hemisfério (veering/backing). Depois, fixe-a no mapa se quiser."
)
_BADGE = (
    "VENTO DO MODELO IFS no step atual — advecção térmica inferida pela regra "
    "veering/backing (aproximação geostrófica), ciente do hemisfério (HS/HN)."
)


class ThermalWindPanel(AnalysisDock):
    """Dock direito com a hodógrafa (Axes comum) + Fixar/Remover do mapa."""

    #: Fixar a hodógrafa atual no mapa (payload dict com lon/lat/result).
    pin_requested = pyqtSignal(object)
    #: Remover a hodógrafa fixada no mapa.
    unpin_requested = pyqtSignal()
    #: Abrir o diálogo de camada (a MainWindow é dona do estado/diálogo).
    layer_config_requested = pyqtSignal()

    def __init__(self, title: str = "Vento Térmico (Ponto)", parent=None) -> None:
        super().__init__(title, parent, min_width=440, figsize=(6.4, 6.0), placeholder=_PLACEHOLDER)
        self._last_result = None

        # Linha de controles (camada + fixar/remover) inserida acima do canvas.
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(6)
        self.layer_label = QLabel("Camada: 1000→500 hPa")
        h.addWidget(self.layer_label)
        # Rótulos TEXTUAIS nos botões (glifos viram "!" em fontes sem o símbolo).
        self.layer_btn = QPushButton("Camada…")
        self.layer_btn.setToolTip(
            "Trocar a camada (base→topo). A escolha fica memorizada e o ponto\n"
            "ativo é recalculado na hora."
        )
        self.layer_btn.clicked.connect(self.layer_config_requested.emit)
        h.addWidget(self.layer_btn)
        h.addStretch(1)
        self.pin_btn = QPushButton("📌 Fixar no mapa")
        self.pin_btn.setToolTip("Fixa esta hodógrafa ancorada no ponto clicado (georreferenciada).")
        self.pin_btn.clicked.connect(self._on_pin_clicked)
        h.addWidget(self.pin_btn)
        self.unpin_btn = QPushButton("Remover do mapa")
        self.unpin_btn.setToolTip("Remove a hodógrafa fixada no mapa (a janela continua).")
        self.unpin_btn.clicked.connect(self.unpin_requested.emit)
        h.addWidget(self.unpin_btn)
        self._controls_row = row
        row.hide()  # só aparece quando há dados

        container = self.widget()
        layout = container.layout() if container is not None else None
        if isinstance(layout, QVBoxLayout):
            layout.insertWidget(max(layout.indexOf(self.canvas), 0), row)

    def render(self, result) -> None:  # type: ignore[override]
        try:
            self._last_result = result
            self._controls_row.show()
            self._draw(result)
        except Exception as e:  # blindagem final — nunca derruba a GUI
            logger.exception("Falha ao renderizar a hodógrafa do vento térmico")
            self.show_error(f"Erro ao desenhar a hodógrafa: {e}")

    def set_layer_label(self, base_p: int, top_p: int) -> None:
        """Atualiza o rótulo da camada memorizada (a MainWindow é a dona)."""
        self.layer_label.setText(f"Camada: {base_p}→{top_p} hPa")

    def _on_pin_clicked(self) -> None:
        """Emite o pedido de fixar a hodógrafa ATUAL no ponto de origem dela."""
        r = self._last_result
        if r is None:
            return
        self.pin_requested.emit({"lon": float(r.longitude), "lat": float(r.latitude), "result": r})

    def _draw(self, result) -> None:
        ns = "N" if result.latitude >= 0 else "S"
        ew = "E" if result.longitude >= 0 else "W"
        label = (
            f"{result.levels[0]}→{result.levels[-1]} hPa — "
            f"{abs(result.latitude):.1f}°{ns} {abs(result.longitude):.1f}°{ew}"
        )
        self.set_header(f"🌀 {label}")
        self.set_badge(_BADGE)

        self.fig.clear()
        self.fig.set_facecolor("white")
        ax = self.fig.add_subplot(111)
        render_hodograph(ax, result)
        ax.set_title(label, fontsize=11, color="#212121", pad=8)
        self.fig.subplots_adjust(left=0.04, right=0.96, top=0.92, bottom=0.04)
        self.canvas.draw_idle()
