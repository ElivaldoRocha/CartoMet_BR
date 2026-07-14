"""Renderização da hodógrafa de Vento Térmico (módulo PURO, sem Qt).

Fonte única da LINGUAGEM VISUAL da hodógrafa — constantes, warp radial e o
renderer de janela — compartilhada entre:

- ``ThermalWindPanel`` (dock): ``render_hodograph(ax, result)`` desenha num
  Axes matplotlib comum (aspecto igual, eixos desligados);
- ``MapCanvas.render_thermal_wind``: a versão FIXADA no mapa, que mantém sua
  montagem própria de artistas (transform ancorado em pixels no GeoAxes) mas
  importa daqui as constantes e o warp.

Escala radial NÃO LINEAR (raiz): espalha os ventos fracos de baixos níveis —
que na escala linear colam na origem e escondem o giro — sem jogar os ventos
fortes de altos níveis (jato) para fora do disco. Como o warp é puramente
radial, ele PRESERVA o ângulo de cada vetor: o giro (veering/backing) e a
advecção continuam exatos; só o raio é comprimido. Anéis e leituras seguem
rotulados em nós REAIS.
"""

from __future__ import annotations

import matplotlib
import matplotlib.patheffects as pe
import numpy as np

TW_COLORS: dict[str, str] = {"warm": "#C0392B", "cold": "#2471A3", "neutral": "#7F8C8D"}
TW_ADV_PT: dict[str, str] = {"warm": "quente", "cold": "fria", "neutral": "neutra"}
KT: float = 1.94384  # m/s → nós (kt)
TW_RADIAL_POWER: float = 0.5  # 1.0 = linear; 0.5 = raiz quadrada (hodógrafa clássica)


def ring_increment(max_kt: float) -> float:
    """Incremento 'redondo' dos anéis de alcance para ~3 anéis até o máximo."""
    for inc in (5.0, 10.0, 20.0, 25.0, 50.0, 100.0):
        if max_kt <= inc * 3.2:
            return inc
    return 100.0


def warp_radius(kt: float, power: float = TW_RADIAL_POWER) -> float:
    """Magnitude REAL (kt) → raio em unidades de exibição (warp radial)."""
    return float(float(kt) ** power)


def warp_components(
    us: np.ndarray, vs: np.ndarray, power: float = TW_RADIAL_POWER
) -> tuple[np.ndarray, np.ndarray]:
    """Aplica o warp radial aos componentes u/v (kt), preservando a direção."""
    speeds = np.hypot(us, vs)
    wu = np.zeros_like(us, dtype=float)
    wv = np.zeros_like(vs, dtype=float)
    mask = speeds > 1e-9
    f = np.zeros_like(speeds)
    f[mask] = speeds[mask] ** (power - 1.0)  # r**power / r, guardando a direção
    wu[mask] = us[mask] * f[mask]
    wv[mask] = vs[mask] * f[mask]
    return wu, wv


def render_hodograph(ax, result, *, show_legend: bool = True) -> None:
    """Desenha a hodógrafa rica num Axes COMUM (versão de janela/painel).

    Mesma linguagem visual da hodógrafa fixada no mapa: disco de fundo, anéis
    de alcance em kt reais, setas do vento por nível coloridas por altura,
    curva térmica com segmentos coloridos por advecção, vértices rotulados em
    hPa e círculo de origem. Coordenadas do Axes = unidades de exibição
    (kt warpados), aspecto igual, eixos desligados.
    """
    ax.set_aspect("equal")
    ax.axis("off")
    if result is None or len(getattr(result, "levels", [])) < 2:
        ax.text(0.5, 0.5, "Dados insuficientes", ha="center", va="center", transform=ax.transAxes)
        return

    us = np.asarray(result.u, dtype=float) * KT
    vs = np.asarray(result.v, dtype=float) * KT
    levels = list(result.levels)
    n = len(levels)

    speeds = np.hypot(us, vs)
    max_kt = float(max(speeds.max(), 5.0))
    inc = ring_increment(max_kt)
    n_rings = max(2, int(np.ceil(max_kt / inc)))
    outer = n_rings * inc  # valor (kt) do anel externo

    wu, wv = warp_components(us, vs)
    r_out = warp_radius(outer)

    halo = [pe.withStroke(linewidth=2.6, foreground="white")]
    dark = [pe.withStroke(linewidth=3.0, foreground="black", alpha=0.30)]
    cmap = matplotlib.colormaps["autumn_r"]  # amarelo (base) → vermelho (topo)
    th = np.linspace(0.0, 2.0 * np.pi, 240)

    # ── Disco de fundo ────────────────────────────────────────────────────────
    rbg = r_out * 1.16
    ax.fill(
        rbg * np.cos(th),
        rbg * np.sin(th),
        facecolor="white",
        edgecolor="#5D6D7E",
        lw=1.0,
        zorder=1.0,
    )

    # ── Anéis de alcance + rótulos em kt REAIS ────────────────────────────────
    for k in range(1, n_rings + 1):
        r = k * inc  # kt real
        rw = warp_radius(r)
        ax.plot(
            rw * np.cos(th),
            rw * np.sin(th),
            color="#9AA4AD",
            lw=0.9,
            ls=(0, (4, 4)),
            alpha=0.9,
            zorder=1.2,
        )
        ax.text(
            rw * np.cos(np.radians(138)),
            rw * np.sin(np.radians(138)),
            f"{r:.0f}" + (" kt" if k == n_rings else ""),
            color="#6B7680",
            fontsize=8.5,
            ha="center",
            va="center",
            zorder=1.3,
            path_effects=halo,
        )

    # ── Setas radiais do vento de cada nível (cor por altura) ────────────────
    head_len = r_out * 0.06
    head_w = r_out * 0.042
    for i in range(n):
        wx, wy = float(wu[i]), float(wv[i])
        color = cmap(i / (n - 1) if n > 1 else 0.0)
        magw = float(np.hypot(wx, wy))
        if float(speeds[i]) < 0.5 or magw < 1e-6:  # calmaria: sem seta
            continue
        ux, uy = wx / magw, wy / magw
        bx, by = wx - ux * head_len, wy - uy * head_len
        ax.plot(
            [0.0, bx],
            [0.0, by],
            color=color,
            lw=2.0,
            solid_capstyle="round",
            zorder=2.0,
            path_effects=dark,
        )
        px, py = -uy, ux  # perpendicular unitário
        ax.fill(
            [wx, bx + px * head_w, bx - px * head_w],
            [wy, by + py * head_w, by - py * head_w],
            color=color,
            zorder=2.1,
            lw=0.0,
        )

    # ── Curva da hodógrafa: segmentos = vento térmico, cor por advecção ──────
    for ly in result.layers:
        i0 = levels.index(ly.p_bottom)
        i1 = levels.index(ly.p_top)
        ax.plot(
            [wu[i0], wu[i1]],
            [wv[i0], wv[i1]],
            color=TW_COLORS.get(ly.advection, "#7F8C8D"),
            lw=4.2,
            solid_capstyle="round",
            zorder=2.4,
            path_effects=dark,
        )

    # ── Vértices: ponto + rótulo do nível (hPa) deslocado para fora ──────────
    for i in range(n):
        wx, wy = float(wu[i]), float(wv[i])
        ax.plot(
            [wx],
            [wy],
            marker="o",
            mfc="white",
            mec="#1B2631",
            mew=1.2,
            markersize=6.5,
            zorder=2.6,
        )
        magw = float(np.hypot(wx, wy))
        ox, oy = (wx / magw, wy / magw) if magw > 1e-6 else (0.0, 1.0)
        ax.text(
            wx + ox * r_out * 0.12,
            wy + oy * r_out * 0.12,
            f"{levels[i]}",
            color="#1B2631",
            fontsize=9,
            fontweight="bold",
            ha="center",
            va="center",
            zorder=2.66,
            path_effects=halo,
        )

    # ── Origem: círculo destacado ─────────────────────────────────────────────
    ax.plot([0.0], [0.0], marker="o", mfc="none", mec="#2E4EB8", mew=2.4, markersize=14, zorder=2.7)

    # ── Legenda: camada, hemisfério, advecção líquida e vento de base ────────
    if show_legend:
        hemi = "HS" if float(result.latitude) < 0 else "HN"
        spd0 = float(np.hypot(us[0], vs[0]))
        wdir0 = float((270.0 - np.degrees(np.arctan2(vs[0], us[0]))) % 360.0)
        adv_pt = TW_ADV_PT.get(result.net_advection, "—")
        ax.text(
            0.0,
            -r_out * 1.28,
            f"Vento Térmico {levels[0]}→{levels[-1]} hPa · {hemi}\n"
            f"advecção líquida: {adv_pt}  ·  base {wdir0:.0f}°/{spd0:.0f} kt",
            color=TW_COLORS.get(result.net_advection, "#2C3E50"),
            fontsize=10,
            fontweight="bold",
            ha="center",
            va="top",
            zorder=2.6,
            bbox={"boxstyle": "round,pad=0.35", "fc": "white", "ec": "#5D6D7E", "alpha": 0.9},
        )

    pad = r_out * 1.22
    bottom = r_out * (1.55 if show_legend else 1.22)
    ax.set_xlim(-pad, pad)
    ax.set_ylim(-bottom, pad)
