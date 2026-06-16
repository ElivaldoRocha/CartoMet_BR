"""
zcit_axis.py — Detecção robusta do EIXO da Zona de Convergência Intertropical (ZCIT)
a partir de um campo de OLR (Outgoing Longwave Radiation), em W/m².

Portado da pesquisa ``_rascunhos/Projeto_ZCIT_AXIS/zcit_axis.py`` (validado com ERA5;
operação no CartoMet usa ECMWF IFS Open Data). Apoia o overlay opcional de eixo do
LOCZCIT-PA; o ``meridional_centroid`` aceita o gancho ``intensity=``/``mask_override=``
para operar sobre o campo acoplado I_ZCIT em vez de ``(240 − OLR)``.

METODOLOGIA (fundamentada na literatura — ver relatório de revisão):
  1. Máscara oceânica + LIMIAR FÍSICO de OLR (NÃO percentil adaptativo).
       - Envelope convectivo:  OLR < 240 W/m²   (Collimore et al. 2003)
       - Núcleo profundo:       OLR < 200 W/m²   (Gu & Adler 2002)
  2. Coerência espacial: mantém apenas aglomerados contíguos grandes (remove núcleos
     isolados). Hook opcional para LISA / I de Moran Local (Low-Low) — Anselin (1995).
  3. CENTROIDE MERIDIONAL ponderado por intensidade, coluna a coluna (longitude),
     com PORTÃO DE COBERTURA convectiva — análogo ao centroide de precipitação de
     Adam, Bischoff & Schneider (2016) e à filosofia do LOCZCIT (Ferreira et al. 2005).
  4. REJEIÇÃO DE OUTLIERS por cercas de Tukey (IQR 1.5) sobre a série de latitudes do
     eixo (herança do LOCZCIT-IQR) — global ou em janela móvel.
  5. SUAVIZAÇÃO robusta (LOWESS biweight; Cleveland 1979) com tratamento de lacunas:
     interpola vãos curtos, encerra o eixo em vãos longos (não força céu limpo).
  6. (Opcional) teste de bimodalidade por longitude para resolver ZCIT dupla; por
     padrão entrega um EIXO ÚNICO (o que o meteorologista traça à mão).

Autoria científica de base: Ferreira et al. (2005); Rocha (2022, UFPA); Adam et al. (2016);
Collimore et al. (2003); Anselin (1995); Cleveland (1979); Tukey (1977).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import gaussian_filter, label

# ──────────────────────────────────────────────────────────────────────────────
#  PARÂMETROS FÍSICOS E ESTATÍSTICOS (calibráveis; defaults com base na literatura)
# ──────────────────────────────────────────────────────────────────────────────
OLR_ENVELOPE: float = 240.0  # W/m² — borda da nebulosidade convectiva (Collimore 2003)
OLR_DEEP_CORE: float = 200.0  # W/m² — convecção profunda/organizada (Gu & Adler 2002)
OCEAN_MASK_THRESHOLD: float = 0.2  # lsm <= 0.2 é oceano (preserva a interface costeira)
CENTROID_POWER: float = 1.0  # expoente do peso (>1 afia para o núcleo; Adam usa 10)
MIN_CONVECTIVE_PIXELS: int = 3  # portão de cobertura: mínimo de pixels convectivos/coluna
MIN_CLUSTER_PIXELS: int = 80  # área mínima de um aglomerado coerente (remove núcleos isolados)
IQR_K: float = 1.5  # cercas de Tukey
IQR_WINDOW: int | None = 40  # janela móvel do IQR (em colunas); None = IQR global
LOWESS_FRAC: float = 0.30  # suavidade do LOWESS (fração de pontos por janela local)
MAX_GAP_COLS: int = 16  # vão máximo (colunas) interpolado; além disso, encerra o eixo
SMOOTH_SIGMA: float = 1.0  # suavização gaussiana do campo antes de tudo (anti-ruído)


@dataclass
class ZcitAxis:
    """Resultado da detecção do eixo da ZCIT."""

    lons: np.ndarray  # 1D — todas as longitudes do domínio
    lat_axis: np.ndarray  # 1D — latitude do eixo suavizado (NaN onde não há ZCIT)
    lat_centroid_raw: np.ndarray  # 1D — centroide bruto (antes de IQR/suavização)
    lat_centroid_kept: np.ndarray  # 1D — centroide após portão + IQR
    coverage: np.ndarray  # 1D — nº de pixels convectivos por coluna
    envelope_mask: np.ndarray  # 2D — máscara do envelope convectivo coerente
    meta: dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════════
#  1+2. ENVELOPE CONVECTIVO COERENTE (limiar físico + coerência espacial)
# ══════════════════════════════════════════════════════════════════════════════
def convective_envelope(
    olr: np.ndarray,
    lsm: np.ndarray | None,
    t_env: float = OLR_ENVELOPE,
    ocean_thr: float = OCEAN_MASK_THRESHOLD,
    min_cluster: int = MIN_CLUSTER_PIXELS,
    smooth_sigma: float = SMOOTH_SIGMA,
    coherence: str = "morphological",
) -> tuple[np.ndarray, np.ndarray]:
    """Devolve (olr_oceano, máscara_envelope_coerente).

    - Aplica máscara oceânica (lsm <= ocean_thr) — se lsm for None, assume tudo oceano.
    - Suaviza o campo (gaussiano) para diluir ruído e o sinal diurno residual da skt.
    - Limiar FÍSICO: convecção onde OLR < t_env.
    - Coerência: remove aglomerados menores que `min_cluster` pixels (núcleos isolados).
        coherence="morphological" -> componentes conexas (scipy); rápido, sem dependências.
        coherence="lisa"          -> I de Moran Local (Low-Low); exige esda/libpysal.
    """
    olr = np.asarray(olr, dtype=float)
    if smooth_sigma and smooth_sigma > 0:
        # nan-aware: preenche NaN com a mediana só para suavizar, repõe NaN depois
        finite = np.isfinite(olr)
        filled = np.where(finite, olr, np.nanmedian(olr))
        olr_s = gaussian_filter(filled, sigma=smooth_sigma)
        olr_s = np.where(finite, olr_s, np.nan)
    else:
        olr_s = olr

    if lsm is not None:
        ocean = np.asarray(lsm) <= ocean_thr
        olr_ocean = np.where(ocean, olr_s, np.nan)
    else:
        olr_ocean = olr_s

    convective = (olr_ocean < t_env) & np.isfinite(olr_ocean)

    if coherence == "lisa":
        mask = _lisa_lowlow_mask(olr_ocean)
        mask &= convective
    else:
        mask = _largest_clusters(convective, min_cluster)

    return olr_ocean, mask


def _largest_clusters(binary: np.ndarray, min_size: int) -> np.ndarray:
    """Mantém apenas aglomerados conexos com área >= min_size (8-conectividade=Queen)."""
    structure = np.ones((3, 3), dtype=int)  # vizinhança Queen (8 vizinhos)
    labels, n = label(binary, structure=structure)
    if n == 0:
        return np.zeros_like(binary, dtype=bool)
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0  # fundo
    keep = np.where(sizes >= min_size)[0]
    return np.isin(labels, keep)


def _lisa_lowlow_mask(
    olr_ocean: np.ndarray, p_thresh: float = 0.05, permutations: int = 999, seed: int = 42
) -> np.ndarray:
    """Máscara dos clusters Low-Low significativos (convecção coerente) via I de Moran Local.

    SINAL CRÍTICO: no campo CRU de OLR, convecção = OLR BAIXA. Logo a ZCIT é Low-Low
    (baixo cercado por baixo). Núcleo isolado em céu limpo = Low-High (outlier) — excluído.
    Requer esda/libpysal (extra `spatial`). Cai p/ vazio se ausentes.
    """
    try:
        from esda.moran import Moran_Local
        from libpysal.weights import lat2W
    except ImportError:
        import warnings

        warnings.warn("esda/libpysal ausentes; use coherence='morphological'.", stacklevel=2)
        return np.zeros_like(olr_ocean, dtype=bool)

    nlat, nlon = olr_ocean.shape
    finite = np.isfinite(olr_ocean)
    filled = np.where(finite, olr_ocean, np.nanmedian(olr_ocean))
    w = lat2W(nlat, nlon, rook=False)  # Queen
    w.transform = "r"  # padronização por linha
    lm = Moran_Local(filled.ravel(), w, permutations=permutations, seed=seed)
    # quadrante 3 = Low-Low no esda; significativo
    ll = (lm.q == 3) & (lm.p_sim < p_thresh)
    return ll.reshape(nlat, nlon) & finite


# ══════════════════════════════════════════════════════════════════════════════
#  3. CENTROIDE MERIDIONAL PONDERADO + PORTÃO DE COBERTURA
# ══════════════════════════════════════════════════════════════════════════════
def meridional_centroid(
    olr_ocean: np.ndarray,
    mask: np.ndarray,
    lats: np.ndarray,
    t_env: float = OLR_ENVELOPE,
    power: float = CENTROID_POWER,
    min_pixels: int = MIN_CONVECTIVE_PIXELS,
    intensity: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Para cada longitude, latitude do centro de massa da convecção (peso = profundidade
    abaixo do limiar). Só calcula onde há >= min_pixels convectivos coerentes (portão).

    Devolve (lat_centroid[nlon], coverage[nlon]).  φ(λ) = Σ w·lat / Σ w

    `intensity` (opcional): campo de PESO [nlat,nlon] já orientado para "alto = mais
    convectivo" (ex.: o índice acoplado I_ZCIT ∈ [0,1]). Quando fornecido, substitui o
    peso interno `(t_env - OLR)` — é o gancho de injeção da versão ACOPLADA. Default None
    preserva exatamente o comportamento OLR-only validado.
    """
    nlat, nlon = olr_ocean.shape
    lat_axis = np.full(nlon, np.nan)
    coverage = np.zeros(nlon, dtype=int)
    lats = np.asarray(lats, dtype=float)

    for j in range(nlon):
        col = olr_ocean[:, j]
        if intensity is not None:
            wbase = intensity[:, j]
            valid = mask[:, j] & np.isfinite(wbase)
        else:
            wbase = np.clip(t_env - col, 0.0, None)
            valid = mask[:, j] & np.isfinite(col)
        n = int(np.count_nonzero(valid))
        coverage[j] = n
        if n < min_pixels:
            continue
        depth = np.where(valid, np.clip(wbase, 0.0, None), 0.0) ** power
        wsum = depth.sum()
        if wsum <= 0:
            continue
        lat_axis[j] = float(np.sum(depth * lats) / wsum)
    return lat_axis, coverage


# ══════════════════════════════════════════════════════════════════════════════
#  4. REJEIÇÃO DE OUTLIERS — CERCAS DE TUKEY (IQR) sobre a série de latitudes
# ══════════════════════════════════════════════════════════════════════════════
def reject_outliers_iqr(
    lat_axis: np.ndarray, k: float = IQR_K, window: int | None = IQR_WINDOW
) -> np.ndarray:
    """Marca como NaN as longitudes cuja latitude de eixo é outlier (Tukey 1.5·IQR).

    window=None -> IQR global. window=N -> IQR em janela móvel de ±N colunas
    (mais adequado, pois a latitude do eixo é autocorrelacionada ao longo da longitude).
    """
    out = lat_axis.copy()
    finite = np.isfinite(lat_axis)
    if finite.sum() < 4:
        return out

    if window is None:
        q1, q3 = np.nanpercentile(lat_axis, [25, 75])
        iqr = q3 - q1
        lo, hi = q1 - k * iqr, q3 + k * iqr
        out[(lat_axis < lo) | (lat_axis > hi)] = np.nan
        return out

    n = len(lat_axis)
    for i in range(n):
        if not np.isfinite(lat_axis[i]):
            continue
        a, b = max(0, i - window), min(n, i + window + 1)
        seg = lat_axis[a:b]
        seg = seg[np.isfinite(seg)]
        if seg.size < 4:
            continue
        q1, q3 = np.percentile(seg, [25, 75])
        iqr = q3 - q1
        if lat_axis[i] < q1 - k * iqr or lat_axis[i] > q3 + k * iqr:
            out[i] = np.nan
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  5. SUAVIZAÇÃO ROBUSTA (LOWESS) + TRATAMENTO DE LACUNAS
# ══════════════════════════════════════════════════════════════════════════════
def smooth_axis(
    lons: np.ndarray, lat_axis: np.ndarray, frac: float = LOWESS_FRAC, max_gap: int = MAX_GAP_COLS
) -> np.ndarray:
    """LOWESS robusto (it=3, biweight) sobre os centroides retidos.

    - Ajusta y(longitude) suave e resistente a outliers residuais.
    - Interpola apenas vãos CURTOS (<= max_gap colunas entre centroides válidos).
    - Em vãos LONGOS (céu limpo), o eixo é encerrado (NaN) — NÃO força latitude espúria.

    O `statsmodels` (LOWESS) é importado preguiçosamente; se ausente, cai numa
    interpolação linear simples (o overlay segue funcionando, sem suavização robusta).
    """
    lons = np.asarray(lons, dtype=float)
    out = np.full_like(lons, np.nan, dtype=float)
    valid = np.isfinite(lat_axis)
    if valid.sum() < 5:
        return out

    x, y = lons[valid], lat_axis[valid]
    try:
        from statsmodels.nonparametric.smoothers_lowess import lowess

        sm = lowess(y, x, frac=frac, it=3, return_sorted=True)  # robusto
        interp = np.interp(lons, sm[:, 0], sm[:, 1], left=np.nan, right=np.nan)
    except ImportError:  # pragma: no cover — fallback sem statsmodels
        interp = np.interp(lons, x, y, left=np.nan, right=np.nan)

    # Distância (em colunas) de cada longitude ao centroide válido mais próximo
    idx_valid = np.where(valid)[0]
    col_idx = np.arange(len(lons))
    nearest = np.min(np.abs(col_idx[:, None] - idx_valid[None, :]), axis=1)
    interp[nearest > max_gap] = np.nan  # encerra em vãos longos
    return interp


# ══════════════════════════════════════════════════════════════════════════════
#  ORQUESTRADOR
# ══════════════════════════════════════════════════════════════════════════════
def detect_zcit_axis(
    olr: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    lsm: np.ndarray | None = None,
    *,
    t_env: float = OLR_ENVELOPE,
    coherence: str = "morphological",
    centroid_power: float = CENTROID_POWER,
    min_pixels: int = MIN_CONVECTIVE_PIXELS,
    min_cluster: int = MIN_CLUSTER_PIXELS,
    iqr_k: float = IQR_K,
    iqr_window: int | None = IQR_WINDOW,
    lowess_frac: float = LOWESS_FRAC,
    max_gap: int = MAX_GAP_COLS,
    smooth_sigma: float = SMOOTH_SIGMA,
    intensity: np.ndarray | None = None,
    mask_override: np.ndarray | None = None,
) -> ZcitAxis:
    """Pipeline completo: envelope coerente -> centroide ponderado + portão -> IQR -> LOWESS.

    Versão ACOPLADA (opcional): `intensity` injeta um campo de peso [nlat,nlon] (ex.: I_ZCIT)
    no lugar de `(t_env - OLR)`; `mask_override` injeta a máscara/portão da banda (ex.: a
    máscara ATIVA acoplada `oceano ∧ (OLR<240 ∨ C>C_THR)`). Ambos None = OLR-only validado.
    """
    olr_ocean, mask = convective_envelope(
        olr,
        lsm,
        t_env=t_env,
        min_cluster=min_cluster,
        smooth_sigma=smooth_sigma,
        coherence=coherence,
    )
    if mask_override is not None:
        mask = np.asarray(mask_override, dtype=bool)
    lat_raw, coverage = meridional_centroid(
        olr_ocean,
        mask,
        lats,
        t_env=t_env,
        power=centroid_power,
        min_pixels=min_pixels,
        intensity=intensity,
    )
    lat_kept = reject_outliers_iqr(lat_raw, k=iqr_k, window=iqr_window)
    lat_smooth = smooth_axis(lons, lat_kept, frac=lowess_frac, max_gap=max_gap)

    return ZcitAxis(
        lons=np.asarray(lons),
        lat_axis=lat_smooth,
        lat_centroid_raw=lat_raw,
        lat_centroid_kept=lat_kept,
        coverage=coverage,
        envelope_mask=mask,
        meta={
            "t_env": t_env,
            "coherence": coherence,
            "centroid_power": centroid_power,
            "min_pixels": min_pixels,
            "min_cluster": min_cluster,
            "iqr_k": iqr_k,
            "iqr_window": iqr_window,
            "lowess_frac": lowess_frac,
            "max_gap": max_gap,
            "n_valid_longitudes": int(np.isfinite(lat_smooth).sum()),
        },
    )
