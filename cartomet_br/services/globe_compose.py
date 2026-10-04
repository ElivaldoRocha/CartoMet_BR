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
    derive_contour_levels,
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
    # Camadas sinóticas (PNMM/espessura — isolinhas vetoriais no repouso).
    # Centros H/L são detecção regional (máscara orográfica + persistência) e
    # ficam fora do globo com aviso.
    synoptic: Any = None  # SynopticData global (ou recorte no fallback) | None
    synoptic_kinds: tuple[str, ...] = ()
    synoptic_global: bool = True
    # True somente quando o forno NÃO conseguiu assar o relevo como base
    # (raster do cartopy ausente) — a janela então desenha stock_img por
    # frame como fallback.
    underlay_relevo: bool = False

    def has_fields(self) -> bool:
        """True se há QUALQUER conteúdo de dado do dia para a pele de campos."""
        return self.texture is not None or bool(self.contour_layers) or self.synoptic is not None


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
    # Borda superior: o contourf INCLUI v == levels[-1] na última banda
    # (digitize o jogaria em "acima" — ex.: prob ENS de exatos 100%).
    band[finite & (grid == levels[-1])] = len(levels) - 1

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
                gdata = load_olr(technique=technique, **common)
            elif var == "precip":
                gdata = load_precip(technique=technique, **common)
            elif var == "tcwv":
                gdata = load_tcwv(**common)
            elif var == "sst_model":
                gdata = load_model_sst(**common)
            elif var == "sst_grad":
                gdata = load_sst_gradient(**common)
            elif var in ("tmax2m", "tmin2m"):
                gdata = load_t2_extreme(var, **common)
            else:
                model = "aifs" if data.source == "aifs" else "ifs"
                gdata = load_pl_variable(
                    variable_key=var, level=int(data.level), model=model, **common
                )
    except CacheMissError:
        return data, False
    except Exception as exc:  # noqa: BLE001 — fallback honesto, nunca rede
        logger.warning("Releitura global de %s falhou (%s) — usando o recorte.", var, exc)
        return data, False

    # Identidade da camada: o loader cru não preenche .source (quem o faz é o
    # DataService) — sem isto o carimbo rotularia um campo AIFS como "IFS".
    gdata.source = data.source
    # Guarda de rodada: cycle/cycle_date vêm do PAINEL — se o usuário trocou a
    # rodada depois de carregar a camada, a releitura seria de OUTRA rodada.
    # Validade/base divergente → recorte regional honesto (nunca dado trocado).
    # Limite conhecido: a técnica de desacumulação não fica nos metadados da
    # camada — divergência de técnica (olr/precip) não é detectável aqui.
    if (data.valid_time and gdata.valid_time != data.valid_time) or (
        data.base_time and gdata.base_time != data.base_time
    ):
        logger.warning(
            "Releitura global de %s veio de outra rodada (%s != %s) — usando o recorte.",
            var,
            gdata.base_time,
            data.base_time,
        )
        return data, False
    return gdata, True


def reload_global_synoptic(
    synoptic: Any,
    *,
    config: Any,
    cycle: int | None,
    cycle_date: str | None,
) -> tuple[Any, bool]:
    """Relê PNMM/espessura em extensão GLOBAL, só do cache — mesma rodada.

    Mesmas regras de ``reload_global``: guarda de rodada (validade/base
    divergente → recorte honesto), identidade do modelo preservada, zero rede.
    """
    from cartomet_br.data.ecmwf import CacheMissError, cache_only_mode, load_synoptic_data

    try:
        with cache_only_mode():
            gsyn = load_synoptic_data(
                extent=GLOBE_EXTENT,
                step=int(synoptic.step),
                cycle=cycle,
                cycle_date=cycle_date,
                data_dir=config.grib_dir,
                smoothing_sigma=0.0,
                model="aifs" if getattr(synoptic, "source", "ifs") == "aifs" else "ifs",
            )
    except CacheMissError:
        return synoptic, False
    except Exception as exc:  # noqa: BLE001 — fallback honesto, nunca rede
        logger.warning("Releitura global do sinótico falhou (%s) — usando o recorte.", exc)
        return synoptic, False

    gsyn.source = synoptic.source
    if (synoptic.valid_time and gsyn.valid_time != synoptic.valid_time) or (
        synoptic.base_time and gsyn.base_time != synoptic.base_time
    ):
        logger.warning(
            "Sinótico global veio de outra rodada (%s != %s) — usando o recorte.",
            gsyn.base_time,
            synoptic.base_time,
        )
        return synoptic, False
    return gsyn, True


# ─── Assadura das camadas vetoriais na textura ───────────────────────────────
# Lição de campo (globo travando em máquina boa): o clabel GLOBAL custa
# SEGUNDOS por render — centenas de rótulos com halo re-rasterizados a cada
# giro/zoom. Assar isolinhas+rótulos UMA vez na pele (equiretangular) torna o
# render de repouso um único imshow (~0,5 s), independente das camadas.
# Rótulos giram com o globo, como num globo físico.

# Fator de fonte/linha do forno: a textura 1× (1440 px de largura; o
# hemisfério visível ocupa ~720 px dela e ~900 px de tela) sobe ~1,25× no
# render — fontes ~1,3× preservam a leitura da carta. Medição ao vivo: assar
# em 2× dobrava o lado da fonte da reprojeção e o repouso ia de ~0,7 s a
# ~2,3 s — o forno fica na MESMA grade da textura.
_BAKE_FONT_SCALE = 1.3


def _stock_earth_rgba(shape: tuple[int, int]) -> np.ndarray | None:
    """Relevo natural (o MESMO arquivo do ``stock_img``) como base equiretangular.

    Carregado direto do raster do cartopy e reamostrado (nearest) para a grade
    da textura — o relevo entra ASSADO na pele e some do custo por frame
    (``stock_img`` custava ~0,55 s por render). ``None`` se o raster não
    estiver onde o cartopy o publica (caímos no stock_img por frame).
    """
    try:
        from pathlib import Path

        import matplotlib.image as mpimg
        from cartopy import config as cartopy_config

        raster = (
            Path(cartopy_config["repo_data_dir"])
            / "raster"
            / "natural_earth"
            / "50-natural-earth-1-downsampled.png"
        )
        img = np.asarray(mpimg.imread(str(raster)), dtype=np.float32)  # (H, W, 3) 0..1
    except Exception as exc:  # noqa: BLE001 — base é cosmética; fallback honesto
        logger.warning("Relevo do cartopy indisponível p/ o forno (%s).", exc)
        return None
    h, w = shape
    iy = np.clip((np.arange(h) * img.shape[0] / h).astype(int), 0, img.shape[0] - 1)
    ix = np.clip((np.arange(w) * img.shape[1] / w).astype(int), 0, img.shape[1] - 1)
    base = np.ones((h, w, 4), dtype=np.float32)
    base[..., :3] = img[np.ix_(iy, ix)][..., :3]
    return base


def draw_synoptic_overlay(ax, synoptic: Any, kinds: tuple[str, ...], *, scale: float = 1.0) -> None:
    """Desenha PNMM/espessura num Axes equiretangular (dados em lon/lat crus).

    MESMOS níveis/rótulos da carta (LEVELS/COLORS canônicos); cores claras e
    halo escuro calibrados para o fundo do globo. Usada pelo forno da textura
    (``bake_vector_overlay``) — sem cartopy: no equiretangular lon/lat são as
    próprias coordenadas do eixo.
    """
    import matplotlib.patheffects as pe

    from cartomet_br.core.config import COLORS, LEVELS

    halo = [pe.withStroke(linewidth=2 * scale, foreground="#060a14")]
    if "pnmm" in kinds:
        niveis = np.arange(LEVELS["pnmm"]["min"], LEVELS["pnmm"]["max"], LEVELS["pnmm"]["step"])
        cs = ax.contour(
            synoptic.lons,
            synoptic.lats,
            synoptic.pnmm,
            levels=niveis,
            colors="#e8edf5",
            linewidths=0.9 * scale,
        )
        for txt in ax.clabel(cs, inline=True, fontsize=7 * scale, fmt="%1.0f"):
            txt.set_path_effects(halo)
    if "thickness" in kinds:
        niveis = np.arange(
            LEVELS["thickness"]["min"], LEVELS["thickness"]["max"], LEVELS["thickness"]["step"]
        )
        sem_5400 = niveis[niveis != 5400]
        cs = ax.contour(
            synoptic.lons,
            synoptic.lats,
            synoptic.thickness,
            levels=sem_5400,
            colors=[
                COLORS["thickness_cold"] if lv < 5400 else COLORS["thickness_warm"]
                for lv in sem_5400
            ],
            linestyles="dashed",
            linewidths=0.8 * scale,
        )
        for txt in ax.clabel(cs, inline=True, fontsize=7 * scale, fmt="%1.0f"):
            txt.set_path_effects(halo)
        cs_5400 = ax.contour(
            synoptic.lons,
            synoptic.lats,
            synoptic.thickness,
            levels=[5400],
            colors=COLORS["thickness_5400"],
            linestyles="solid",
            linewidths=2.2 * scale,
        )
        for txt in ax.clabel(cs_5400, inline=True, fontsize=8 * scale, fmt="%1.0f"):
            txt.set_path_effects(halo)


def draw_contour_overlay(ax, contour_layers: list[GlobeLayer], *, scale: float = 1.0) -> None:
    """Isolinhas das camadas ``plot_type=="contour"`` (gh500...) — níveis da carta."""
    import matplotlib.patheffects as pe

    halo = [pe.withStroke(linewidth=2 * scale, foreground="#060a14")]
    for layer in contour_layers:
        data = layer.data
        values = mask_low_signal(data.variable, data.unit, data.values)
        try:
            cs = ax.contour(
                data.lons,
                data.lats,
                values,
                levels=np.asarray(layer.style.levels, dtype=float),
                colors="#f2f5fa",
                linewidths=0.8 * scale,
            )
            for txt in ax.clabel(cs, inline=True, fontsize=7 * scale, fmt="%1.0f"):
                txt.set_path_effects(halo)
        except Exception as exc:  # noqa: BLE001 — isolinha é adorno, não derruba o forno
            logger.warning("Isolinhas de %s falharam no forno: %s", layer.layer_id, exc)


def bake_vector_overlay(scene: GlobeScene, shape: tuple[int, int] = TEXTURE_SHAPE) -> None:
    """Assa sinótico + isolinhas NA textura da cena (uma única vez, offscreen).

    Renderiza num Agg equiretangular NA grade da textura e compõe "over" a
    textura dos campos; sem campo preenchido, a base vira o relevo natural
    (também assado — some o stock_img por frame). Depois disto a janela não
    desenha nenhum vetor pesado por frame: o repouso é um único imshow.
    """
    if scene.synoptic is None and not scene.contour_layers:
        return
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    h, w = shape
    fig = Figure(figsize=(w / 100.0, h / 100.0), dpi=100)
    fig.patch.set_alpha(0.0)
    ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
    ax.set_xlim(-180.0, 180.0)
    ax.set_ylim(-90.0, 90.0)
    ax.set_axis_off()
    ax.patch.set_alpha(0.0)

    if scene.synoptic is not None:
        draw_synoptic_overlay(ax, scene.synoptic, scene.synoptic_kinds, scale=_BAKE_FONT_SCALE)
    draw_contour_overlay(ax, scene.contour_layers, scale=_BAKE_FONT_SCALE)

    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    overlay = np.asarray(canvas.buffer_rgba(), dtype=np.float32) / 255.0  # linha 0 = +90°
    if overlay.shape[:2] != (h, w):  # DPI scaling defensivo
        iy = np.clip((np.arange(h) * overlay.shape[0] / h).astype(int), 0, overlay.shape[0] - 1)
        ix = np.clip((np.arange(w) * overlay.shape[1] / w).astype(int), 0, overlay.shape[1] - 1)
        overlay = overlay[np.ix_(iy, ix)]

    if scene.texture is not None:
        base = scene.texture.astype(np.float32) / 255.0
    else:
        relevo = _stock_earth_rgba(shape)
        if relevo is None:
            base = np.zeros((h, w, 4), dtype=np.float32)
            scene.underlay_relevo = True  # fallback: stock_img por frame
        else:
            base = relevo
    final = composite_over(base, overlay)
    scene.texture = (np.clip(final, 0.0, 1.0) * 255).astype(np.uint8)


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
    synoptic: Any = None,
    synoptic_kinds: tuple[str, ...] = (),
) -> GlobeScene:
    """Compõe a cena do globo a partir das camadas VISÍVEIS da carta.

    ``pl_data`` na ordem de inserção (= ordem de empilhamento da carta);
    camadas ``plot_type=="contour"`` viram isolinhas vetoriais (render de
    repouso) e as demais escalares entram na textura única. A textura sai em
    uint8 (8 MB vs 33 MB em float — mitigação de memória do plano).
    ``synoptic``/``synoptic_kinds``: as camadas sinóticas da carta (PNMM/
    espessura entram como isolinhas; centros H/L ficam fora com aviso).
    """
    scene = GlobeScene(texture=None)
    acc: np.ndarray | None = None

    if synoptic is not None and synoptic_kinds:
        vis = tuple(k for k in synoptic_kinds if k in ("pnmm", "thickness"))
        if vis:
            gsyn, syn_ok = reload_global_synoptic(
                synoptic, config=config, cycle=cycle, cycle_date=cycle_date
            )
            scene.synoptic = gsyn
            scene.synoptic_kinds = vis
            scene.synoptic_global = syn_ok
            if not syn_ok:
                scene.warnings.append(
                    "PNMM/Espessura: sem cache global compatível — exibindo o recorte"
                )
        if "centers" in synoptic_kinds:
            scene.warnings.append("Centros H/L ficam fora do globo (detecção regional)")

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
        # Isolinhas (plot_type "contour", ex.: gh500) usam a derivação PRÓPRIA
        # de isolinhas da carta (passo inteiro), não a de contourf — valores e
        # rótulos idênticos aos que o usuário vê na carta 2D.
        reg_values = mask_low_signal(data.variable, data.unit, data.values)
        is_contour = var_info.get("plot_type") == "contour"
        if is_contour:
            niveis = derive_contour_levels(reg_values)
            if niveis is None:
                scene.warnings.append(f"{nome}: campo constante — sem isolinhas no globo")
                continue
            style = ScalarStyle(levels=niveis, cmap="binary", extend="neither")
        else:
            style = derive_scalar_style(data.variable, data.unit, reg_values, var_info)

        gdata, is_global = reload_global(
            data, config=config, cycle=cycle, cycle_date=cycle_date, technique=technique
        )
        if not is_global:
            scene.warnings.append(f"{nome}: sem cache global compatível — exibindo o recorte")

        layer = GlobeLayer(layer_id, gdata, var_info, style, is_global)
        if is_contour:
            scene.contour_layers.append(layer)
            continue

        rgba = rasterize_layer(gdata, style, shape)
        acc = rgba if acc is None else composite_over(acc, rgba)
        scene.filled_layers.append(layer)

    if acc is not None:
        # Fidelidade de cor à carta: lá o alpha 0.85 compõe SOBRE FUNDO CLARO
        # (papel/relevo). Achatar a pilha sobre branco reproduz exatamente a
        # cor vista na carta (o "over" é associativo), em vez de escurecê-la
        # sobre o oceano noturno do globo; área sem dado segue transparente.
        a = acc[..., 3:4]
        flat = np.empty_like(acc)
        flat[..., :3] = acc[..., :3] * a + (1.0 - a)
        flat[..., 3:4] = np.where(a > 0.0, 1.0, 0.0)
        flat[..., :3] = np.where(a > 0.0, flat[..., :3], 0.0)
        scene.texture = (np.clip(flat, 0.0, 1.0) * 255).astype(np.uint8)

    # Isolinhas (sinótico + plot_type "contour") entram ASSADAS na textura:
    # o clabel global por frame era o que travava o globo (lição de campo).
    bake_vector_overlay(scene, shape)
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
