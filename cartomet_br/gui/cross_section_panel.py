"""Painel do Corte Vertical (cross-section A→B) — F4.

Dock deslizante que desenha a seção distância × pressão ao longo da reta A→B:
omega (ascendência/subsidência) sombreado, temperatura e umidade em contornos e
barbelas de vento ao longo do corte. Puramente de RENDER — os dados chegam
prontos do ``CrossSectionWorker``. Vertical grosseira (13 níveis): badge de
honestidade.
"""

from __future__ import annotations

import contextlib
import logging

import numpy as np

from cartomet_br.gui.analysis_panel import AnalysisDock

logger = logging.getLogger(__name__)

_PLACEHOLDER = (
    "🔪 Ative o Corte Vertical e clique em DOIS pontos (A → B).\n\n"
    "Seção distância × pressão do modelo IFS: ω (ascendência),\n"
    "temperatura, umidade e vento ao longo da reta."
)
_BADGE = (
    "CORTE DO MODELO IFS — 13 níveis (vertical grosseira), aproximado. "
    "ω<0 = ascendência; t em °C; q em g/kg."
)
_PLEVELS = [1000, 925, 850, 700, 500, 300, 200, 100, 50]


class CrossSectionPanel(AnalysisDock):
    """Dock direito com o corte vertical (pressão log invertida × distância)."""

    def __init__(self, title: str = "Corte Vertical (A→B)", parent=None) -> None:
        super().__init__(title, parent, min_width=520, figsize=(7.6, 6.2), placeholder=_PLACEHOLDER)

    def render(self, xs) -> None:
        try:
            self._render(xs)
        except Exception as e:  # blindagem final — nunca derruba a GUI
            logger.exception("Falha ao renderizar o corte vertical")
            self.show_error(f"Erro ao desenhar o corte: {e}")

    def _render(self, xs) -> None:
        self.set_header("🔪 Corte Vertical A→B")
        self.set_badge(_BADGE)

        d = np.asarray(xs.distances_km, dtype=float)
        p = np.asarray(xs.pressures, dtype=float)
        grid_d, grid_p = np.meshgrid(d, p)

        self.fig.clear()
        self.fig.set_facecolor("white")
        ax = self.fig.add_subplot(111)

        # Sombreado de omega (ω): ascendência (ω<0) em azul, subsidência em vermelho.
        # Guarda contra ω todo-NaN: `nan or 1.0` manteria NaN (NaN é "truthy") e
        # quebraria os níveis do contourf.
        finite_w = np.isfinite(xs.w)
        wmax = float(np.nanmax(np.abs(xs.w[finite_w]))) if finite_w.any() else 1.0
        if not np.isfinite(wmax) or wmax <= 0:
            wmax = 1.0
        levels_w = np.linspace(-wmax, wmax, 21)
        cf = ax.contourf(grid_d, grid_p, xs.w, levels=levels_w, cmap="RdBu_r", extend="both")
        cbar = self.fig.colorbar(cf, ax=ax, pad=0.02, fraction=0.046)
        cbar.set_label("ω (Pa/s) — azul: ascendência", fontsize=8)
        cbar.ax.tick_params(labelsize=7)

        # Temperatura (°C) — contornos pretos.
        with contextlib.suppress(Exception):
            cs = ax.contour(grid_d, grid_p, xs.t, colors="black", linewidths=0.6, levels=10)
            ax.clabel(cs, inline=True, fontsize=6, fmt="%d")

        # Umidade específica (g/kg) — contornos verdes tracejados.
        with contextlib.suppress(Exception):
            csq = ax.contour(
                grid_d, grid_p, xs.q, colors="#117A65", linewidths=0.7, linestyles="--", levels=6
            )
            ax.clabel(csq, inline=True, fontsize=6, fmt="%d")

        # Barbelas de vento ao longo do corte (subamostradas).
        with contextlib.suppress(Exception):
            si = max(p.size // 13, 1)
            sj = max(d.size // 12, 1)
            ax.barbs(
                grid_d[::si, ::sj],
                grid_p[::si, ::sj],
                xs.u[::si, ::sj],
                xs.v[::si, ::sj],
                length=5,
                linewidth=0.5,
            )

        ax.set_yscale("log")
        ax.set_ylim(p.max(), p.min())  # 1000 hPa embaixo, topo em cima
        ax.set_yticks(_PLEVELS)
        ax.set_yticklabels([str(v) for v in _PLEVELS])
        ax.set_ylabel("Pressão (hPa)", fontsize=9)
        ax.set_xlabel("Distância ao longo do corte (km)", fontsize=9)
        ax.tick_params(labelsize=8)

        title = (
            f"Corte A({xs.lats[0]:.1f},{xs.lons[0]:.1f}) → B({xs.lats[-1]:.1f},{xs.lons[-1]:.1f})"
        )
        if xs.base_time:
            title += f"   •   {xs.base_time}  (+{xs.step}h)"
        self.fig.suptitle(title, fontsize=10, fontweight="bold")
        with contextlib.suppress(Exception):
            self.fig.tight_layout(rect=(0, 0, 1, 0.95))
        self.canvas.draw_idle()
