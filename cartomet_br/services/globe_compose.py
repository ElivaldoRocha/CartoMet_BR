"""Motor de composição da Vista de Globo — PURO (numpy + matplotlib.colors).

Transforma as camadas ativas da carta 2D numa ÚNICA textura RGBA
equiretangular (pronta para `imshow(transform=PlateCarree)` num GeoAxes
Orthographic), relendo cada campo do cache ECMWF **em extensão global** e
SEM REDE (`cache_only_mode`). O estilo (níveis/cmap/extend) vem de
``services/field_style.py`` derivado do RECORTE REGIONAL da camada — a
colorbar do globo é idêntica à da carta aberta, por construção.

Honestidade embutida: camada sem cache global (ou de fonte regional por
natureza — ENS/ERA5/comparação de rodadas) entra como *patch* do próprio
recorte sobre o globo, com aviso textual; vento e eixos ficam fora do v1,
também com aviso. Nada é inventado, nada é baixado.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from cartomet_br.services.field_style import (
    ScalarStyle,
    derive_scalar_style,
    mask_low_signal,
)

logger = logging.getLogger(__name__)

# Extent global na ordem do app ([lon_min, lat_min, lon_max, lat_max]).
GLOBE_EXTENT: list[float] = [-180.0, -90.0, 180.0, 90.0]

# Grade-alvo da textura (0.25°, a grade nativa do ECMWF open data).
TEXTURE_SHAPE: tuple[int, int] = (721, 1440)


@dataclass(frozen=True)
class GlobeLayer:
    """Uma camada pronta para o globo (dados + estilo da carta)."""

    layer_id: str
    data: Any  # PLFieldData (global ou recorte regional no fallback)
    var_info: dict
    style: ScalarStyle
    is_global: bool


@dataclass
class GlobeScene:
    """Resultado da composição: textura única + camadas vetoriais + avisos."""

    texture: np.ndarray | None  # (H, W, 4) uint8 equiretangular; None = sem campos
    filled_layers: list[GlobeLayer] = field(default_factory=list)
    contour_layers: list[GlobeLayer] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ─── Cores de banda idênticas às do contourf ─────────────────────────────────


def band_colors(style: ScalarStyle) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    """Cores por banda EXATAMENTE como o ``contourf`` da carta as atribui.

    Devolve ``(interiores (n_bandas, 4), under (4,) | None, over (4,) | None)``.
    Regra REAL do contourf (verificada por introspecção de um ContourSet e
    travada por teste-verdade em tests/test_globe_compose.py): cada banda é
    colorida pelo seu PONTO MÉDIO normalizado linearmente no intervalo
    ``[levels[0], levels[-1]]``; as bandas de extensão caem nos extremos do
    colormap (cvalues ±5e249 → norm estoura → cores de under/over do cmap).
    """
    import matplotlib as mpl
    from matplotlib.colors import Normalize

    levels = np.asarray(style.levels, dtype=float)
    has_under = style.extend == "both"
    has_over = style.extend in ("max", "both")

    cmap = style.cmap
    if isinstance(cmap, str):
        cmap = mpl.colormaps[cmap]
    norm = Normalize(vmin=levels[0], vmax=levels[-1])

    mids = (levels[:-1] + levels[1:]) / 2.0
    interior = cmap(norm(mids))
    under = np.asarray(cmap(-1e9)) if has_under else None
    over = np.asarray(cmap(1e9)) if has_over else None
    return interior, under, over


# ─── Reamostragem nearest p/ a grade da textura ──────────────────────────────


def _nearest_index(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Índice do vizinho mais próximo de ``dst`` em ``src`` (asc ou desc).

    Devolve ``(idx, dentro)`` — ``dentro`` marca os alvos dentro do domínio da
    fonte (± meia célula); fora dele o chamador deixa transparente (patch).
    """
    src = np.asarray(src, dtype=float)
    asc = bool(src[0] <= src[-1])
    s = src if asc else src[::-1]
    j = np.searchsorted(s, dst).clip(1, len(s) - 1)
    pick_left = np.abs(dst - s[j - 1]) <= np.abs(s[j] - dst)
    idx = np.where(pick_left, j - 1, j)
    half = float(np.median(np.abs(np.diff(s)))) / 2.0 if len(s) > 1 else 0.5
    dentro = (dst >= s[0] - half) & (dst <= s[-1] + half)
    if not asc:
        idx = len(s) - 1 - idx
    return idx, dentro


def rasterize_layer(
    data: Any,
    style: ScalarStyle,
    shape: tuple[int, int] = TEXTURE_SHAPE,
) -> np.ndarray:
    """Rasteriza UMA camada na grade equiretangular alvo → RGBA float32.

    Valores já mascarados (``mask_low_signal``); NaN e células fora do domínio
    da fonte (patch regional) saem com alpha 0. ``extend="max"`` deixa o
    abaixo-do-piso transparente; ``"both"`` pinta com a banda de extensão —
    o MESMO comportamento do contourf da carta.
    """
    h, w = shape
    tgt_lats = np.linspace(90.0, -90.0, h)
    tgt_lons = np.linspace(-180.0, 180.0 - 360.0 / w, w)

    values = mask_low_signal(data.variable, data.unit, data.values)
    values = np.asarray(values, dtype=float)

    iy, in_y = _nearest_index(np.asarray(data.lats), tgt_lats)
    ix, in_x = _nearest_index(np.asarray(data.lons), tgt_lons)
    grid = values[np.ix_(iy, ix)]

    levels = np.asarray(style.levels, dtype=float)
    interior, under, over = band_colors(style)

    rgba = np.zeros((h, w, 4), dtype=np.float32)
    band = np.digitize(grid, levels)  # 0 = abaixo; 1..n = bandas; n+1 = acima
    finite = np.isfinite(grid)

    inside = (band >= 1) & (band <= len(levels) - 1) & finite
    if inside.any():
        rgba[inside] = interior[band[inside] - 1]
    below = (band == 0) & finite
    if under is not None and below.any():
        rgba[below] = under
    above = (band == len(levels)) & finite
    if over is not None and above.any():
        rgba[above] = over

    # Fora do domínio da fonte (patch regional) → transparente.
    dominio = in_y[:, None] & in_x[None, :]
    rgba[~dominio] = 0.0

    rgba[..., 3] *= style.alpha
    return rgba


def composite_over(base: np.ndarray, top: np.ndarray) -> np.ndarray:
    """Alpha-composite "over" (top sobre base), ambos RGBA float [0, 1]."""
    a_top = top[..., 3:4]
    a_base = base[..., 3:4]
    a_out = a_top + a_base * (1.0 - a_top)
    rgb = top[..., :3] * a_top + base[..., :3] * a_base * (1.0 - a_top)
    with np.errstate(invalid="ignore", divide="ignore"):
        rgb = np.where(a_out > 0, rgb / a_out, 0.0)
    out = np.empty_like(base)
    out[..., :3] = rgb
    out[..., 3:4] = a_out
    return out


# ─── Releitura global cache-only (espelho do despacho do DataService) ────────


def reload_global(
    data: Any,
    *,
    config: Any,
    cycle: int | None,
    cycle_date: str | None,
    technique: str = "direct",
) -> tuple[Any, bool]:
    """Relê a MESMA variável/nível/step da camada em extensão global, só do cache.

    Espelha o despacho de ``DataService._load_field``. Devolve
    ``(campo, True)`` no sucesso global; ``(recorte original, False)`` quando a
    fonte é regional por natureza (ENS/ERA5/comparação) ou o cache não cobre.
    ``smoothing_sigma=0`` evita a costura do filtro gaussiano em ±180°.
    """
    from cartomet_br.data.ecmwf import (
        CacheMissError,
        cache_only_mode,
        load_model_sst,
        load_olr,
        load_pl_variable,
        load_precip,
        load_sst_gradient,
        load_t2_extreme,
        load_tcwv,
    )

    var = data.variable
    if data.source in ("ens", "era5") or var.endswith("_diff"):
        return data, False

    common = {
        "extent": GLOBE_EXTENT,
        "step": int(data.step),
        "cycle": cycle,
        "cycle_date": cycle_date,
        "data_dir": config.grib_dir,
        "smoothing_sigma": 0.0,
    }
    try:
        with cache_only_mode():
            if var == "olr":
                return load_olr(technique=technique, **common), True
            if var == "precip":
                return load_precip(technique=technique, **common), True
            if var == "tcwv":
                return load_tcwv(**common), True
            if var == "sst_model":
                return load_model_sst(**common), True
            if var == "sst_grad":
                return load_sst_gradient(**common), True
            if var in ("tmax2m", "tmin2m"):
                return load_t2_extreme(var, **common), True
            model = "aifs" if data.source == "aifs" else "ifs"
            return (
                load_pl_variable(variable_key=var, level=int(data.level), model=model, **common),
                True,
            )
    except CacheMissError:
        return data, False
    except Exception as exc:  # noqa: BLE001 — fallback honesto, nunca rede
        logger.warning("Releitura global de %s falhou (%s) — usando o recorte.", var, exc)
        return data, False


# ─── Composição da cena ──────────────────────────────────────────────────────


def compose_scene(
    pl_data: dict[str, Any],
    variable_registry: dict[str, dict],
    *,
    config: Any,
    cycle: int | None,
    cycle_date: str | None,
    technique: str = "direct",
    shape: tuple[int, int] = TEXTURE_SHAPE,
) -> GlobeScene:
    """Compõe a cena do globo a partir das camadas VISÍVEIS da carta.

    ``pl_data`` na ordem de inserção (= ordem de empilhamento da carta);
    camadas ``plot_type=="contour"`` viram isolinhas vetoriais (render de
    repouso) e as demais escalares entram na textura única. A textura sai em
    uint8 (8 MB vs 33 MB em float — mitigação de memória do plano).
    """
    scene = GlobeScene(texture=None)
    acc: np.ndarray | None = None

    for layer_id, data in pl_data.items():
        var_info = variable_registry.get(data.variable, {})
        nome = var_info.get("nome", data.variable)

        if var_info.get("category") == "wind" or data.u_values is not None:
            scene.warnings.append(f"{nome}: vento fica fora do globo (v1)")
            continue
        if var_info.get("plot_type") == "axis_line":
            scene.warnings.append(f"{nome}: eixo de linha fica fora do globo (v1)")
            continue

        # Estilo derivado do RECORTE REGIONAL — a mesma escala da carta aberta.
        reg_values = mask_low_signal(data.variable, data.unit, data.values)
        style = derive_scalar_style(data.variable, data.unit, reg_values, var_info)

        gdata, is_global = reload_global(
            data, config=config, cycle=cycle, cycle_date=cycle_date, technique=technique
        )
        if not is_global:
            scene.warnings.append(f"{nome}: sem cache global — exibindo o recorte regional")

        layer = GlobeLayer(layer_id, gdata, var_info, style, is_global)
        if var_info.get("plot_type") == "contour":
            scene.contour_layers.append(layer)
            continue

        rgba = rasterize_layer(gdata, style, shape)
        acc = rgba if acc is None else composite_over(acc, rgba)
        scene.filled_layers.append(layer)

    if acc is not None:
        scene.texture = (np.clip(acc, 0.0, 1.0) * 255).astype(np.uint8)
    return scene


__all__ = [
    "GLOBE_EXTENT",
    "TEXTURE_SHAPE",
    "GlobeLayer",
    "GlobeScene",
    "band_colors",
    "compose_scene",
    "composite_over",
    "rasterize_layer",
    "reload_global",
]
