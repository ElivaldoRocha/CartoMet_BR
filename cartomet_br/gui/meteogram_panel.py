"""Painel do Meteograma (série temporal num ponto) — F6.

Dock deslizante que desenha a evolução do modelo IFS num ponto ao longo dos
steps: temperatura (1000 hPa) + PNMM, vento de 10 m, precipitação por intervalo
e água precipitável. Puramente de RENDER — os dados chegam prontos do
``MeteogramWorker``. Carrega o badge de honestidade (previsão pontual do modelo).
"""

from __future__ import annotations

import contextlib
import logging

import numpy as np

from cartomet_br.gui.analysis_panel import AnalysisDock

logger = logging.getLogger(__name__)

_PLACEHOLDER = (
    "📈 Ative o Meteograma e clique num ponto do mapa.\n\n"
    "Série do modelo IFS (+0…+72h) naquele ponto: temperatura, vento,\n"
    "precipitação, PNMM e água precipitável."
)
_BADGE = (
    "PREVISÃO DO MODELO IFS (13 níveis) — pontual e aproximada. "
    "T do ar a 1000 hPa (sem 2 m); vento de 10 m; precip por intervalo."
)


class MeteogramPanel(AnalysisDock):
    """Dock direito com o meteograma (4 painéis empilhados sobre o eixo de steps)."""

    def __init__(self, title: str = "Meteograma (Ponto)", parent=None) -> None:
        super().__init__(title, parent, min_width=480, figsize=(7.0, 7.8), placeholder=_PLACEHOLDER)

    def render(self, ts) -> None:
        try:
            self._render(ts)
        except Exception as e:  # blindagem final — nunca derruba a GUI
            logger.exception("Falha ao renderizar o meteograma")
            self.show_error(f"Erro ao desenhar o meteograma: {e}")

    def _render(self, ts) -> None:
        ns = "N" if ts.grid_lat >= 0 else "S"
        ew = "E" if ts.grid_lon >= 0 else "W"
        label = f"Modelo IFS — {abs(ts.grid_lat):.1f}°{ns} {abs(ts.grid_lon):.1f}°{ew}"
        self.set_header(f"📈 {label}")
        self.set_badge(_BADGE)

        x = np.asarray(ts.steps, dtype=float)
        self.fig.clear()
        self.fig.set_facecolor("white")
        ax_t, ax_w, ax_p, ax_q = self.fig.subplots(4, 1, sharex=True)

        # 1) Temperatura (1000 hPa) + PNMM (eixo gêmeo)
        ax_t.plot(x, ts.t, color="#C0392B", marker="o", ms=3, lw=1.4)
        ax_t.set_ylabel("T 1000 hPa\n(°C)", color="#C0392B", fontsize=9)
        ax_t.tick_params(axis="y", labelcolor="#C0392B", labelsize=8)
        ax_t.grid(True, alpha=0.3)
        ax_msl = ax_t.twinx()
        ax_msl.plot(x, ts.msl, color="#2C3E50", lw=1.2, ls="--")
        ax_msl.set_ylabel("PNMM (hPa)", color="#2C3E50", fontsize=9)
        ax_msl.tick_params(axis="y", labelcolor="#2C3E50", labelsize=8)

        # 2) Vento de 10 m: intensidade + barbelas no topo
        ax_w.plot(x, ts.wind_speed, color="#1A6FAF", marker="o", ms=3, lw=1.4)
        ax_w.set_ylabel("Vento 10 m\n(m/s)", fontsize=9)
        ax_w.grid(True, alpha=0.3)
        with contextlib.suppress(Exception):
            theta = np.radians(ts.wind_dir)  # direção de onde sopra
            u = -ts.wind_speed * np.sin(theta)
            v = -ts.wind_speed * np.cos(theta)
            vmax = float(np.nanmax(ts.wind_speed)) if ts.wind_speed.size else 1.0
            ytop = np.full_like(x, vmax * 1.12 + 0.2)
            ax_w.barbs(x, ytop, u, v, length=5.5, linewidth=0.6)
            ax_w.set_ylim(0, vmax * 1.3 + 0.4)

        # 3) Precipitação por intervalo (barras)
        width = (x[1] - x[0]) * 0.7 if x.size > 1 else 1.0
        ax_p.bar(x, ts.precip, width=width, color="#2E86C1", alpha=0.85)
        ax_p.set_ylabel("Precip\n(mm)", fontsize=9)
        ax_p.grid(True, alpha=0.3)

        # 4) Água precipitável (tcwv)
        ax_q.plot(x, ts.tcwv, color="#117A65", marker="o", ms=3, lw=1.4)
        ax_q.set_ylabel("Água precip.\n(mm)", fontsize=9)
        ax_q.grid(True, alpha=0.3)
        ax_q.set_xlabel("Step de previsão (h)", fontsize=9)

        for a in (ax_t, ax_w, ax_p, ax_q):
            a.tick_params(labelsize=8)

        title = label
        if ts.base_time:
            title += f"   •   Rodada {ts.base_time}"
        self.fig.suptitle(title, fontsize=11, fontweight="bold")
        with contextlib.suppress(Exception):
            self.fig.tight_layout(rect=(0, 0, 1, 0.96))
        self.canvas.draw_idle()
