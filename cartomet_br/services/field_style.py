"""Estilo escalar compartilhado entre a carta 2D e a Vista de Globo.

Módulo **PURO** (numpy + matplotlib.colors — sem Qt, sem Axes): a derivação de
níveis/colormap/extend que vivia dentro de ``MapCanvas._plot_scalar_contourf``
extraída para uma função única. A carta e o globo chamam A MESMA função —
garantia estrutural de que a colorbar do globo é idêntica à da carta aberta.

Regra de ouro da extração: comportamento byte-idêntico ao bloco original
(testes de caracterização em ``tests/test_field_style.py`` travam isso).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

# ─── Piso seco de precipitação (por unidade de exibição) ──────────────────────
# Abaixo do limiar a chuva vira NaN → transparente, sem véu de quase-zero.
PRECIP_DRY_FLOOR: dict[str, float] = {
    "mm/h": 0.1,  # ERA5 horário — precip. mensurável
    "mm/3h": 0.1,  # IFS (acumulação de 3 h)
    "mm/dia": 1.0,  # ERA5 diário (total/médio/máx) — dia com chuva (OMM)
    "mm": 1.0,  # ERA5 total do período
}


def precip_dry_floor(unit: str) -> float:
    """Limiar (piso seco) de precipitação para a unidade dada; 0,1 mm como fallback."""
    return PRECIP_DRY_FLOOR.get(unit, 0.1)


# ─── Paletas clássicas (antes atributos de classe do MapCanvas) ──────────────
# Paleta OLR clássica
OLR_COLORS = [
    "#3b71a1",
    "#407bb3",
    "#4483c2",
    "#4e92c7",
    "#569fcc",
    "#61aac9",
    "#66b8c4",
    "#6bc7bc",
    "#78d6a4",
    "#84e38c",
    "#8bed6b",
    "#abf056",
    "#c6f24b",
    "#dbf547",
    "#eef743",
    "#fcf942",
    "#ffef3b",
    "#ffe436",
    "#fcd32d",
    "#fcbf23",
    "#faab19",
    "#f79811",
    "#f5820f",
    "#f26a0f",
    "#ed590e",
    "#e84315",
    "#d93523",
    "#c92435",
    "#b5163e",
    "#a11045",
    "#8f0d47",
    "#800a45",
    "#61063b",
    "#520436",
    "#470334",
    "#3d022e",
    "#330128",
]

# Paleta de precipitação (mm) — branco → azul → roxo
PRECIP_COLORS = [
    "#f7fbff",
    "#d8eafc",
    "#b6dbf2",
    "#8fc8e8",
    "#62a8d8",
    "#3f8fcc",
    "#2f7ab8",
    "#2563a3",
    "#2a55a0",
    "#3a3f9e",
    "#5b2e93",
    "#7a1f86",
    "#99127a",
]
PRECIP_LEVELS = [0.2, 1, 2, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150]

# Paleta de TSM (°C) — frio (roxo/azul) → quente (vermelho)
SST_COLORS = [
    "#3b0f70",
    "#3a2a8c",
    "#2c5aa0",
    "#1f7db0",
    "#2a9db5",
    "#3fb8a8",
    "#74c794",
    "#b7d97a",
    "#ece06b",
    "#f7c044",
    "#f59331",
    "#e85f29",
    "#d62f27",
    "#b3161f",
    "#7a0a16",
]

# Variáveis cuja escala de percentis não desce abaixo de zero (mesmo conjunto
# que vivia inline no _plot_scalar_contourf — contrato da carta). PÚBLICO: o
# FrameScaleTracker da animação usa a MESMA lista (antes mantinha um espelho
# defasado — sem theta_e_grad e sem toda a família ens/era5).
CLAMP_ZERO_VARIABLES = (
    "r",
    "q",
    "wind_speed",
    "temp_grad",
    "theta_e_grad",
    "tcwv",
    "sst_grad",
    "ens_spread",
    "era5_tcwv",
    "era5_precip",
    "era5pl_r",
    "era5pl_q",
    "era5_toa_sw",
    "era5_ssrd",
    "era5_strd",
    "era5_tcc",
    "era5_cape",
    "era5_kindex",
    "era5_totalx",
    "era5_gust",
)


# ─── Fórmulas de níveis compartilhadas (carta, globo E animação) ─────────────
# A animação agrega extremos/percentis de TODOS os quadros e aplica as mesmas
# fórmulas — compartilhá-las aqui mata a deriva entre os espelhos.


def symmetric_levels(abs_max: float) -> np.ndarray:
    """Níveis simétricos ±0.9·|extremo| em 21 passos (ω, div, vort...)."""
    vmax = abs_max * 0.9
    if vmax < 1e-10:
        vmax = 1.0
    return np.linspace(-vmax, vmax, 21)


def percentile_levels(p2: float, p98: float, *, clamp_zero: bool) -> np.ndarray:
    """Níveis do caso geral: percentis 2–98 + 5% de margem, 21 passos."""
    margin = (p98 - p2) * 0.05
    lv_min = p2 - margin
    lv_max = p98 + margin
    if clamp_zero:
        lv_min = max(0, lv_min)
    # Evita levels constantes (min == max → matplotlib crash)
    if abs(lv_max - lv_min) < 1e-10:
        lv_min = lv_min - 1.0
        lv_max = lv_max + 1.0
    return np.linspace(lv_min, lv_max, 21)


def contour_levels_from_range(vmin: float, vmax: float) -> np.ndarray | None:
    """Níveis de ISOLINHAS (passo inteiro) como o _plot_scalar_contour da carta.

    ``None`` = campo constante (nada a plotar); também quando sobra < 2 níveis.
    """
    if abs(vmax - vmin) < 1e-10:
        return None
    step = max(1, int((vmax - vmin) / 20))
    levels = np.arange(int(vmin), int(vmax) + step, step)
    return levels if len(levels) >= 2 else None


def derive_contour_levels(values: np.ndarray, fixed_levels=None) -> np.ndarray | None:
    """Níveis de isolinhas de um campo (percentis 2–98 do próprio quadro)."""
    if fixed_levels is not None:
        arr = np.asarray(fixed_levels, dtype=float)
        return arr if len(arr) >= 2 else None
    vmin, vmax = np.nanpercentile(values, [2, 98])
    return contour_levels_from_range(float(vmin), float(vmax))


@dataclass(frozen=True)
class ScalarStyle:
    """Estilo completo de um campo escalar preenchido (contourf da carta)."""

    levels: Any  # list[float] | np.ndarray — como o contourf aceita
    cmap: Any  # Colormap | str
    extend: str  # "max" (precip/prob ENS) | "both"
    alpha: float = field(default=0.85)


def mask_low_signal(variable: str, unit: str, values: np.ndarray) -> np.ndarray:
    """Aplica os pisos de sinal da carta: piso seco (precip) e piso 10% (ENS).

    Abaixo do piso o valor vira NaN → o contourf/textura deixa transparente.
    Para as demais variáveis devolve os valores como vieram (sem cópia).
    """
    if variable in ("precip", "era5_precip"):
        floor = precip_dry_floor(unit)
        return np.where(np.asarray(values, dtype=float) < floor, np.nan, values)
    if variable == "ens_prob":
        return np.where(np.asarray(values, dtype=float) < 10.0, np.nan, values)
    return values


def derive_scalar_style(
    variable: str,
    unit: str,
    values: np.ndarray,
    var_info: dict,
    fixed_levels=None,
) -> ScalarStyle:
    """Deriva níveis/cmap/extend exatamente como a carta 2D sempre fez.

    ``values`` deve ser o array JÁ mascarado por ``mask_low_signal`` (é dele
    que saem percentis/extremos nos caminhos simétrico e percentil 2–98).
    ``fixed_levels`` (animação/globo) substitui a derivação de níveis.
    """
    import matplotlib.colors as mcolors

    cmap_name = var_info.get("cmap", "viridis")
    symmetric = var_info.get("symmetric", False)
    is_precip = variable in ("precip", "era5_precip")
    is_ens_prob = variable == "ens_prob"

    if cmap_name == "olr_classic":
        cmap: Any = mcolors.LinearSegmentedColormap.from_list("olr_classic", OLR_COLORS, N=256)
    elif cmap_name == "precip_classic":
        cmap = mcolors.LinearSegmentedColormap.from_list("precip_classic", PRECIP_COLORS, N=256)
    elif cmap_name == "sst_classic":
        cmap = mcolors.LinearSegmentedColormap.from_list("sst_classic", SST_COLORS, N=256)
    else:
        cmap = cmap_name

    if fixed_levels is not None:
        levels = fixed_levels
    elif is_ens_prob:
        levels = list(range(10, 101, 10))
    elif variable in ("olr", "era5_olr"):
        levels = np.linspace(100, 310, 22)
    elif is_precip:
        # Níveis fixos de precipitação; o menor nível é o piso seco (por
        # unidade), e o extend="max" não pinta nada sob ele → seca transparente.
        floor = precip_dry_floor(unit)
        levels = [floor, *[lv for lv in PRECIP_LEVELS if lv > floor]]
    elif symmetric:
        levels = symmetric_levels(max(abs(np.nanmin(values)), abs(np.nanmax(values))))
    else:
        p2, p98 = np.nanpercentile(values, [2, 98])
        clamp = (
            var_info.get("category") in ("wind_speed", "index") or variable in CLAMP_ZERO_VARIABLES
        )
        levels = percentile_levels(float(p2), float(p98), clamp_zero=clamp)

    return ScalarStyle(
        levels=levels,
        cmap=cmap,
        # precip/prob ENS: "max" não pinta abaixo do 1º nível → área sem
        # sinal fica transparente (piso seco / piso de 10%).
        extend="max" if (is_precip or is_ens_prob) else "both",
    )
