"""Painel da Série Temporal ERA5 num ponto (Fase 3).

Dock deslizante que desenha a evolução **horária** de uma variável ERA5 num
ponto de grade ao longo do período escolhido — reanálise (não previsão). Reusa o
``AnalysisDock`` (figura + estados de carga/erro + badge de honestidade). É
puramente de RENDER: os dados chegam prontos do ``ERA5SeriesThread``.
"""

from __future__ import annotations

import contextlib
import logging

import numpy as np

from cartomet_br.data.ecmwf import VARIABLE_REGISTRY
from cartomet_br.gui.analysis_panel import AnalysisDock

logger = logging.getLogger(__name__)

_PLACEHOLDER = (
    "Ative a Série ERA5 e clique num ponto do mapa.\n\n"
    "Evolução horária da variável selecionada naquele ponto,\n"
    "ao longo do período escolhido (reanálise Copernicus)."
)
_BADGE = "REANÁLISE ERA5 (Copernicus) — série horária pontual, não é previsão."


class ERA5SeriesPanel(AnalysisDock):
    """Dock direito com a série temporal horária de um campo ERA5."""

    def __init__(self, title: str = "Série ERA5 (Ponto)", parent=None) -> None:
        super().__init__(title, parent, min_width=460, figsize=(7.0, 4.2), placeholder=_PLACEHOLDER)

    def render(self, series) -> None:  # type: ignore[override]
        try:
            self._render(series)
        except Exception as e:  # blindagem final — nunca derruba a GUI
            logger.exception("Falha ao renderizar a série ERA5")
            self.show_error(f"Erro ao desenhar a série: {e}")

    def _render(self, series) -> None:
        var_info = VARIABLE_REGISTRY.get(series.variable, {})
        nome = var_info.get("nome", series.variable)
        ns = "N" if series.grid_lat >= 0 else "S"
        ew = "E" if series.grid_lon >= 0 else "W"
        ponto = f"{abs(series.grid_lat):.2f}°{ns} {abs(series.grid_lon):.2f}°{ew}"
        nivel = f" — {series.level} hPa" if series.level else ""
        label = f"{nome}{nivel} @ {ponto}"
        self.set_header(f"📉 {label}")
        self.set_badge(_BADGE)

        self.fig.clear()
        self.fig.set_facecolor("white")
        ax = self.fig.add_subplot(111)

        x = np.asarray(series.times)
        y = np.asarray(series.values, dtype=float)
        ax.plot(x, y, color="#16A085", marker="o", ms=2.5, lw=1.3)
        ax.set_ylabel(series.unit or "", fontsize=9)
        ax.grid(True, alpha=0.3)
        ax.tick_params(labelsize=8)

        # Datas legíveis no eixo x (rotacionadas, sem sobrepor).
        with contextlib.suppress(Exception):
            self.fig.autofmt_xdate(rotation=30, ha="right")

        periodo = series.date_start
        if series.date_end and series.date_end != series.date_start:
            periodo = f"{series.date_start} a {series.date_end}"
        self.fig.suptitle(f"{label}   •   {periodo}", fontsize=10, fontweight="bold")
        with contextlib.suppress(Exception):
            self.fig.tight_layout(rect=(0, 0, 1, 0.95))
        self.canvas.draw_idle()
