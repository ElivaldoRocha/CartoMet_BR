"""Desenho da rosa dos ventos num eixo polar (matplotlib puro, headless-safe).

Função livre ``render_wind_rose`` — reusada pelo painel dockável (Fase 1) e,
depois, pelo inset georreferenciado (Fase 2). Recebe as cores prontas (vindas do
tema) → theme-agnostic e testável sob o backend Agg. Sem Cartopy: aqui é um
``PolarAxes`` comum, então ``text``/patches são livres (a proibição de códigos
curvos vale só para ``GeoAxes``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from matplotlib.colors import LinearSegmentedColormap

from cartomet_br.data.wind_rose import WindRose

# Rampa perceptual frio→quente (fraco→forte) para as faixas de velocidade.
_RAMP_ANCHORS = ["#3B6FB6", "#56B0CE", "#67C98C", "#E7D64B", "#EF8B3C", "#D7301F"]
_CARDINAL_8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def speed_bin_colors(n_bins: int) -> list[tuple[float, float, float, float]]:
    """Amostra ``n_bins`` cores da rampa frio→quente (qualquer nº de faixas)."""
    cmap = LinearSegmentedColormap.from_list("windspeed", _RAMP_ANCHORS)
    xs = np.linspace(0.0, 1.0, n_bins) if n_bins > 1 else np.array([0.5])
    out: list[tuple[float, float, float, float]] = []
    for x in xs:
        r, g, b, a = cmap(float(x))
        out.append((float(r), float(g), float(b), float(a)))
    return out


def speed_bin_labels(edges: Sequence[float]) -> list[str]:
    """Rótulos das faixas de velocidade a partir das bordas (m/s)."""
    labels = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        labels.append(f"≥{lo:g}" if math.isinf(hi) else f"{lo:g}–{hi:g}")
    return labels


def render_wind_rose(
    ax: Any,
    rose: WindRose,
    *,
    bin_colors: Sequence[Any] | None = None,
    text_color: str = "#212121",
    grid_color: str = "#C0C0C0",
    title: str = "",
    show_legend: bool = True,
    show_calm: bool = True,
    compact: bool = False,
) -> list:
    """Desenha ``rose`` num eixo polar. Devolve os artistas criados (barras/textos).

    ``ax`` deve ser um eixo polar (``projection="polar"``). Rosa vazia (sem
    amostras válidas) vira uma mensagem centralizada. ``compact=True`` (usado no
    inset do mapa): só 4 rumos, sem rótulos radiais e fontes menores.
    """
    ax.clear()
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)  # horário (N→E→S→W)

    if rose.n_total == 0:
        ax.set_xticks([])
        ax.set_yticks([])
        msg = ax.text(
            0.5,
            0.5,
            "Sem dados de vento\nneste ponto",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=11,
            color=text_color,
        )
        if title:
            ax.set_title(title, fontsize=11, color=text_color, pad=12)
        return [msg]

    n_bins = len(rose.speed_bin_edges) - 1
    colors = list(bin_colors) if bin_colors is not None else speed_bin_colors(n_bins)

    theta = np.radians(np.asarray(rose.sector_centers, dtype=float))
    width = math.radians(rose.sector_width_deg) * 0.92  # folga entre pétalas
    freq = np.asarray(rose.freq, dtype=float)  # [n_setores, n_bins]

    artists: list = []
    bottom = np.zeros(len(theta))
    for b in range(n_bins):
        heights = freq[:, b]
        bars = ax.bar(
            theta,
            heights,
            width=width,
            bottom=bottom,
            color=colors[b],
            edgecolor="white",
            linewidth=0.4,
            align="center",
            zorder=2,
        )
        artists.append(bars)
        bottom = bottom + heights

    # Rumos cardeais: 8 rótulos (padrão) ou só 4 (compacto, p/ o inset do mapa).
    if compact:
        ax.set_xticks(np.radians(np.arange(0, 360, 90)))
        ax.set_xticklabels(["N", "E", "S", "W"], fontsize=7, color=text_color)
        ax.set_yticklabels([])
        ax.tick_params(axis="y", length=0)
    else:
        ax.set_xticks(np.radians(np.arange(0, 360, 45)))
        ax.set_xticklabels(_CARDINAL_8, fontsize=9, color=text_color)
        ax.tick_params(axis="y", labelsize=7, colors=text_color)
    ax.grid(True, color=grid_color, alpha=0.6, linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(grid_color)

    rmax = float(bottom.max()) if bottom.size else 0.0
    if rmax > 0:
        ax.set_ylim(0, rmax * 1.08)
    ax.set_rlabel_position(202.5)  # entre S e SW, longe da pétala dominante

    if show_calm:
        calm_txt = ax.text(
            0.5,
            0.5,
            f"{rose.calm_fraction * 100:.0f}%"
            if compact
            else f"calmo\n{rose.calm_fraction * 100:.0f}%",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=6.5 if compact else 8,
            color=text_color,
            zorder=5,
            bbox={"boxstyle": "round,pad=0.2", "fc": "white", "ec": grid_color, "alpha": 0.85},
        )
        artists.append(calm_txt)

    if title:
        ax.set_title(
            title, fontsize=8 if compact else 11, color=text_color, pad=6 if compact else 12
        )

    if show_legend:
        labels = speed_bin_labels(rose.speed_bin_edges)
        handles = [a for a in artists if hasattr(a, "patches") and len(a.patches)]
        leg = ax.legend(
            handles,
            labels,
            title="m/s",
            loc="upper left",
            bbox_to_anchor=(1.02, 1.0),
            fontsize=7,
            title_fontsize=8,
            frameon=False,
        )
        if leg is not None:
            artists.append(leg)
            leg.get_title().set_color(text_color)
            for txt in leg.get_texts():
                txt.set_color(text_color)

    return artists
