"""
zcit_dual.py — Extensão do detector de eixo da ZCIT para tratar BANDA DUPLA (double-ITCZ).

Portado da pesquisa ``_rascunhos/Projeto_ZCIT_AXIS/zcit_dual.py``. Alimenta o overlay
opcional de eixo do LOCZCIT-PA no CartoMet (resultado ``ZcitAxisDual``).

PROBLEMA QUE RESOLVE
--------------------
O detector de centroide único (zcit_axis.detect_zcit_axis) tem uma falha CONHECIDA e
INERENTE em uma ZCIT dupla: quando uma coluna meridional tem DOIS lobos convectivos
(um ao norte e um ao sul, com céu limpo entre eles), o centroide ponderado cai NO VÃO
LIMPO entre os dois. Ou seja: o centroide único coloca o eixo onde NÃO há convecção.

IDEIA CENTRAL (unifica banda simples e dupla)
---------------------------------------------
Para cada longitude, detectamos quantos LOBOS convectivos existem no perfil meridional
ponderado (1 ou 2), usando os PICOS DE INTENSIDADE do perfil — não o eixo medial
(skeleton). Os ramos só são aceitos onde a bimodalidade é PROEMINENTE (vale fundo entre
os picos) e PERSISTENTE (várias longitudes seguidas) — uma coluna bimodal isolada é ruído.

Fundamentação: bimodalidade do perfil meridional de convecção (Liu & Xie 2002 — ZCIT dupla
no Atlântico/Pacífico; teste de bimodalidade no espírito de Hartigan & Hartigan 1985);
centroide ponderado por intensidade (Adam, Bischoff & Schneider 2016); robustez por
Tukey-IQR (Tukey 1977) + LOWESS (Cleveland 1979).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import gaussian_filter1d, label
from scipy.signal import find_peaks

from cartomet_br.data.zcit_axis import (
    CENTROID_POWER,
    IQR_K,
    IQR_WINDOW,
    LOWESS_FRAC,
    MAX_GAP_COLS,
    MIN_CLUSTER_PIXELS,
    MIN_CONVECTIVE_PIXELS,
    OLR_ENVELOPE,
    SMOOTH_SIGMA,
    convective_envelope,
    reject_outliers_iqr,
    smooth_axis,
)

# Parâmetros específicos da deteção de banda dupla (calibráveis)
MODE_MIN_SEPARATION_DEG: float = 4.0  # distância mínima entre dois lobos p/ contarem como distintos
MODE_MIN_PROMINENCE_FRAC: float = 0.35  # vale entre picos deve cair abaixo de (1-frac)·pico
MODE_SMOOTH_PTS: float = 1.5  # suavização do perfil meridional (em pontos de grade)
BRANCH_MIN_RUN_COLS: int = 16  # mínimo de colunas bimodais consecutivas p/ aceitar ramos


@dataclass
class ZcitAxisDual:
    """Resultado dual: trilho norte e sul (coincidem onde a banda é simples)."""

    lons: np.ndarray
    lat_north: np.ndarray  # ramo norte suavizado (= tronco onde unimodal)
    lat_south: np.ndarray  # ramo sul suavizado  (= tronco onde unimodal)
    n_modes: np.ndarray  # nº de lobos detetados por longitude (após persistência)
    is_double: bool  # houve ramo duplo aceito em alguma faixa?
    double_fraction: float  # fração de longitudes com banda dupla
    coverage: np.ndarray
    envelope_mask: np.ndarray
    meta: dict = field(default_factory=dict)


def _column_modes(
    weight_col: np.ndarray,
    lats: np.ndarray,
    grid_deg: float,
    min_sep_deg: float = MODE_MIN_SEPARATION_DEG,
    min_prom_frac: float = MODE_MIN_PROMINENCE_FRAC,
) -> list[tuple[float, float]]:
    """Detecta 1 ou 2 lobos numa coluna meridional ponderada.

    Devolve [(lat_centroide_lobo, peso_lobo), ...] (no máx. 2), ordenado do NORTE p/ o SUL.
    lats é DESCENDENTE (índice 0 = lat máxima/norte).
    """
    w = np.asarray(weight_col, dtype=float)
    total = w.sum()
    if total <= 0:
        return []

    ws = gaussian_filter1d(w, sigma=MODE_SMOOTH_PTS)
    min_dist = max(int(round(min_sep_deg / grid_deg)), 1)
    prom = min_prom_frac * ws.max()
    peaks, props = find_peaks(ws, distance=min_dist, prominence=prom)

    # 0 ou 1 pico destacado -> lobo único (centroide global da coluna)
    if len(peaks) <= 1:
        return [(float(np.sum(w * lats) / total), float(total))]

    # 2+ picos -> dois lobos mais proeminentes, separados pelo vale entre eles
    order = np.argsort(props["prominences"])[::-1][:2]
    sel = np.sort(peaks[order])  # por índice (norte -> sul)
    valley = sel[0] + int(np.argmin(ws[sel[0] : sel[1] + 1]))
    idx = np.arange(len(lats))
    out = []
    for lobe in (idx < valley, idx >= valley):  # norte, sul
        wl = np.where(lobe, w, 0.0)
        s = wl.sum()
        if s > 0:
            out.append((float(np.sum(wl * lats) / s), float(s)))
    return out if out else [(float(np.sum(w * lats) / total), float(total))]


def _enforce_min_run(flag: np.ndarray, min_run: int) -> np.ndarray:
    """Mantém True apenas em corridas contiguas com comprimento >= min_run (anti-ruído)."""
    out = np.zeros_like(flag, dtype=bool)
    labels, n = label(flag)
    for k in range(1, n + 1):
        run = labels == k
        if run.sum() >= min_run:
            out |= run
    return out


def _close_gaps(flag: np.ndarray, max_gap: int) -> np.ndarray:
    """Fecha buracos curtos (<= max_gap colunas) entre segmentos True (closing 1D).

    Faz a zona de banda dupla virar UMA região contígua, evitando que o eixo único
    'vaze' para dentro dela e permitindo ramos contínuos em vez de fragmentados.
    """
    out = flag.copy()
    idx = np.where(flag)[0]
    if idx.size < 2:
        return out
    for a, b in zip(idx[:-1], idx[1:], strict=False):
        if 1 < (b - a) <= max_gap + 1:
            out[a + 1 : b] = True
    return out


def _consolidate_branches(
    close: np.ndarray, single: np.ndarray, north_c: np.ndarray, south_c: np.ndarray
):
    """Dentro de cada região de banda dupla, monta ramos NORTE e SUL CONTÍNUOS.

    Colunas com 2 lobos já trazem norte_c/sul_c. Colunas com 1 lobo (onde o ramo sul
    sumiu na OLR) recebem o centroide único atribuído ao ramo mais próximo (acima/abaixo
    da mediana da região); o ramo faltante é interpolado ao longo da região. Fora das
    regiões, o tronco recebe o centroide único. Devolve (north, south, trunk).
    """
    north = np.where(close, north_c, np.nan)
    south = np.where(close, south_c, np.nan)
    labels, k = label(close)
    for r in range(1, k + 1):
        cols = np.where(labels == r)[0]
        nref, sref = np.nanmedian(north[cols]), np.nanmedian(south[cols])
        if not (np.isfinite(nref) and np.isfinite(sref)):
            continue  # região sem dois lobos reais
        mid = 0.5 * (nref + sref)
        for c in cols:  # colunas de 1 lobo -> ramo mais próximo
            if np.isfinite(north[c]) and np.isfinite(south[c]):
                continue
            v = single[c]
            if np.isfinite(v):
                if v >= mid and not np.isfinite(north[c]):
                    north[c] = v
                elif v < mid and not np.isfinite(south[c]):
                    south[c] = v
        loc = np.arange(len(cols))  # interpola lacunas remanescentes
        for arr in (north, south):
            seg = arr[cols]
            ok = np.isfinite(seg)
            if ok.sum() >= 2:
                arr[cols] = np.interp(loc, loc[ok], seg[ok])
    trunk = np.where(close, np.nan, single)  # tronco só FORA da zona dupla
    return north, south, trunk


def detect_zcit_axis_dual(
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
    branch_min_run: int = BRANCH_MIN_RUN_COLS,
    min_sep_deg: float = MODE_MIN_SEPARATION_DEG,
    min_prom_frac: float = MODE_MIN_PROMINENCE_FRAC,
    intensity: np.ndarray | None = None,
    mask_override: np.ndarray | None = None,
) -> ZcitAxisDual:
    """Detecta o eixo da ZCIT tratando banda simples E dupla automaticamente.

    Versão ACOPLADA (opcional): `intensity` injeta o campo de peso [nlat,nlon] (ex.: I_ZCIT)
    no lugar de `(t_env - OLR)` — o `_column_modes` passa a detectar bimodalidade sobre o
    campo acoplado; `mask_override` injeta a máscara/portão da banda (ex.: máscara ATIVA
    acoplada). Ambos None = comportamento OLR-only validado (regra de ouro #3).
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
    lats = np.asarray(lats, dtype=float)
    grid_deg = float(np.abs(np.diff(lats)).mean())
    nlat, nlon = olr_ocean.shape

    single = np.full(nlon, np.nan)  # centroide único por coluna
    north_c = np.full(nlon, np.nan)  # centroide do lobo norte (se bimodal)
    south_c = np.full(nlon, np.nan)  # centroide do lobo sul  (se bimodal)
    n_modes_raw = np.zeros(nlon, dtype=int)
    coverage = np.zeros(nlon, dtype=int)

    for j in range(nlon):
        col = olr_ocean[:, j]
        if intensity is not None:
            wbase = intensity[:, j]
            valid = mask[:, j] & np.isfinite(wbase)
        else:
            wbase = np.clip(t_env - col, 0.0, None)
            valid = mask[:, j] & np.isfinite(col)
        coverage[j] = int(valid.sum())
        if valid.sum() < min_pixels:
            continue
        w = np.where(valid, np.clip(wbase, 0.0, None), 0.0) ** centroid_power
        if w.sum() <= 0:
            continue
        single[j] = float(np.sum(w * lats) / w.sum())
        modes = _column_modes(w, lats, grid_deg, min_sep_deg, min_prom_frac)
        n_modes_raw[j] = len(modes)
        if len(modes) >= 2:
            north_c[j], south_c[j] = modes[0][0], modes[1][0]

    # Banda dupla intermitente (ramo sul some/volta na OLR): FECHA lacunas curtas PRIMEIRO,
    # transformando o liga-desliga numa zona contígua; só DEPOIS exige persistência mínima.
    closed_raw = _close_gaps(n_modes_raw >= 2, max_gap=branch_min_run)
    close = _enforce_min_run(closed_raw, branch_min_run)

    # Monta ramos contínuos dentro da zona dupla; tronco só fora dela
    north, south, trunk = _consolidate_branches(close, single, north_c, south_c)
    n_modes = np.where(close, 2, np.where(np.isfinite(single), 1, 0))

    # Robustez (IQR de Tukey) + suavização (LOWESS), separadamente em tronco e ramos
    trunk_s = smooth_axis(
        lons,
        reject_outliers_iqr(trunk, k=iqr_k, window=iqr_window),
        frac=lowess_frac,
        max_gap=max_gap,
    )
    north_s = smooth_axis(
        lons,
        reject_outliers_iqr(north, k=iqr_k, window=iqr_window),
        frac=lowess_frac,
        max_gap=max_gap,
    )
    south_s = smooth_axis(
        lons,
        reject_outliers_iqr(south, k=iqr_k, window=iqr_window),
        frac=lowess_frac,
        max_gap=max_gap,
    )

    # Recombina na convenção da dataclass: onde unimodal, ambos = tronco; onde dupla, ramos
    lat_north_s = np.where(close, north_s, trunk_s)
    lat_south_s = np.where(close, south_s, trunk_s)

    double_fraction = float(close.sum()) / max(int(np.isfinite(single).sum()), 1)
    return ZcitAxisDual(
        lons=np.asarray(lons),
        lat_north=lat_north_s,
        lat_south=lat_south_s,
        n_modes=n_modes,
        is_double=bool(close.any()),
        double_fraction=double_fraction,
        coverage=coverage,
        envelope_mask=mask,
        meta={
            "t_env": t_env,
            "branch_min_run": branch_min_run,
            "min_sep_deg": min_sep_deg,
            "min_prom_frac": min_prom_frac,
            "grid_deg": grid_deg,
            "coherence": coherence,
            "n_double_longitudes": int(close.sum()),
        },
    )
