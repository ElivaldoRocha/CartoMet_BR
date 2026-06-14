"""Filtro de Coerência Espacial (LISA / Moran Local) para o LOCZCIT-PA.

Alternativa **opcional** ao filtro IQR meridional (`iqr_latitude_band`). Em vez de
perguntar "esta latitude está fora de ±1.5·IQR?", pergunta "este pixel de
convecção está cercado por outros pixels de convecção, de forma estatisticamente
significativa?" — isolando o ENVELOPE contíguo da ZCIT e descartando células
órfãs (VCANs isolados, ruído), sem amputar excursões legítimas da banda que o IQR
poderia cortar (DOL/VCAN de larga escala compartilham a faixa de latitude da ZCIT
e NÃO são *outliers* clássicos de Tukey — ver revisão científica n1).

Pipeline: suavização Gaussiana → matriz de vizinhança (Queen) → Moran Local →
mantém *hotspots* High-High significativos (p < 0.05) → isolamento morfológico
(mantém os aglomerados grandes). Opera SOMENTE sobre a matriz do instante atual
(intra-grade); não usa histórico.

Dependências (`esda`, `libpysal`) são OPCIONAIS, no extra ``spatial``:
``uv sync --extra spatial``. Sem elas, o motor cai automaticamente no IQR.
"""

from __future__ import annotations

import logging

import numpy as np
from scipy.ndimage import gaussian_filter, label

logger = logging.getLogger(__name__)

# Parâmetros calibráveis do filtro LISA.
SIGNIFICANCE_P: float = 0.05    # limiar do p-valor (Monte Carlo)
N_PERMUTATIONS: int = 999       # permutações do Moran Local
MIN_OBJECT_PIXELS: int = 50     # área mínima de um aglomerado (descarta órfãos)
SMOOTH_SIGMA: float = 1.0       # suavização anti-ruído antes do LISA
_LISA_SEED: int = 42            # reprodutibilidade operacional (mesmo dado → mesma máscara)
_HH_QUADRANT: int = 1           # convenção esda: HH=1, LH=2, LL=3, HL=4


class SpatialDepsMissing(RuntimeError):
    """esda/libpysal ausentes — instale com ``uv sync --extra spatial``."""


def coherence_mask(
    izcit: np.ndarray,
    *,
    sigma: float = SMOOTH_SIGMA,
    p_thresh: float = SIGNIFICANCE_P,
    permutations: int = N_PERMUTATIONS,
    min_pixels: int = MIN_OBJECT_PIXELS,
    seed: int = _LISA_SEED,
) -> np.ndarray:
    """Máscara booleana ``[lat, lon]`` dos pixels no envelope coerente da ZCIT.

    Pipeline: suavização Gaussiana → vizinhança Queen (``lat2W``) → Moran Local
    (row-standardized) → mantém High-High com p < ``p_thresh`` → rotula objetos
    contíguos → mantém os ≥ ``min_pixels`` (ou o maior). NaN é tratado como
    "não-convectivo" (preenchido com a mediana antes do LISA; reposto a ``False``
    na máscara final, pois esta é interseccionada com os pixels finitos).

    Raises
    ------
    SpatialDepsMissing
        Se ``esda``/``libpysal`` não estiverem instalados (extra ``spatial``).
    """
    try:
        from esda.moran import Moran_Local
        from libpysal.weights import lat2W
    except ImportError as e:  # pragma: no cover — exercido via monkeypatch no teste
        raise SpatialDepsMissing(str(e)) from e

    izcit = np.asarray(izcit, dtype=float)
    nlat, nlon = izcit.shape
    finite = np.isfinite(izcit)
    empty = np.zeros_like(izcit, dtype=bool)

    if finite.sum() < max(min_pixels, 16):
        logger.warning("Coerência espacial: pixels válidos insuficientes; máscara vazia.")
        return empty

    # 1) Suavização (dilui ruído diurno residual da skt e anomalias transientes).
    filled = np.where(finite, izcit, np.nanmedian(izcit))
    smooth = gaussian_filter(filled, sigma=sigma)
    if np.nanstd(smooth) < 1e-12:           # campo degenerado (constante) → sem hotspots
        return empty

    # 2) Vizinhança da grade regular (Queen, 8 vizinhos) + Moran Local.
    #    lat2W espera (nrows, ncols) = (nlat, nlon); a saída do Moran é 1D em ordem
    #    C — voltar com reshape(nlat, nlon) NA MESMA ordem (errar transpõe a máscara).
    w = lat2W(nlat, nlon, rook=False)
    w.transform = "r"
    lm = Moran_Local(smooth.ravel(), w, permutations=permutations, seed=seed)

    # 3) Hotspots High-High e significativos.
    hh = (lm.q == _HH_QUADRANT) & (lm.p_sim < p_thresh)
    hh_grid = hh.reshape(nlat, nlon) & finite       # só onde havia sinal real
    if not hh_grid.any():
        return empty

    # 4) Isolamento morfológico: rotula manchas, mantém as grandes (envelope ZCIT).
    labels, n = label(hh_grid)
    if n == 0:
        return empty
    # Tamanho de cada componente via bincount (counts[0] = fundo; 1..n = manchas).
    counts = np.bincount(labels.ravel(), minlength=n + 1)
    sizes = counts[1:]
    keep_ids = np.where(sizes >= min_pixels)[0] + 1
    if keep_ids.size == 0:                           # nenhum atinge min_pixels → o maior
        keep_ids = np.array([int(np.argmax(sizes)) + 1])
    return np.isin(labels, keep_ids)
