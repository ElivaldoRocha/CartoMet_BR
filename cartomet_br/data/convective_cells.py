"""Detecção de Células Convectivas na imagem GOES-16 IR (Banda 13).

Módulo **PURO** (sem Qt/matplotlib) — reimplementa, de forma enxuta, o
*Detector + Descriptor* da biblioteca **TATHU** (INPE) sobre a grade de
temperatura de brilho que o CartoMet já mantém (`SatelliteData.data`, °C, no
plano geoestacionário). Não é rastreio temporal: é uma **detecção de imagem
única** que serve de **guia objetivo** (topos frios = convecção profunda),
orientando o traçado manual (*human-in-the-loop*) — mesma família do LOCZCIT-PA.

Método: máscara `T < limiar` → rotulagem de componentes conexas
(`scipy.ndimage.label`) → filtro por área mínima → estatísticas por célula
(temperatura mínima, área aproximada, centroide). Limiares de topo na linhagem
consagrada da convecção tropical (≈ −40 a −70 °C / 233–203 K).

Referência conceitual: UBA et al. (2022), *TATHU — Tracking and Analysis of
Thunderstorms*, INPE.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Limiares de temperatura de topo (°C) — presets do seletor da UI.
DEFAULT_THRESHOLD_C = -50.0
THRESHOLDS_C = [-40.0, -50.0, -60.0, -70.0]

# Área mínima de uma célula para não ser descartada como ruído (km²),
# no espírito do exemplo da TATHU (3000 km²).
DEFAULT_MIN_AREA_KM2 = 3000.0


@dataclass(frozen=True)
class Cell:
    """Uma célula convectiva detectada (componente conexa fria retida)."""

    centroid_x: float  # metros, plano geoestacionário (mesmo x/y da imagem)
    centroid_y: float
    t_min_c: float  # temperatura de topo mínima (°C) dentro da célula
    area_km2: float  # área aproximada (plano do satélite)
    npix: int


@dataclass
class ConvectiveCellsResult:
    """Resultado da detecção — máscara das células retidas + descritores."""

    mask: np.ndarray  # bool 2D: True nos pixels das células retidas
    cells: list[Cell] = field(default_factory=list)
    threshold_c: float = DEFAULT_THRESHOLD_C
    min_area_km2: float = DEFAULT_MIN_AREA_KM2
    n_total: int = 0  # componentes frias antes do filtro de área
    n_kept: int = 0  # células retidas (== len(cells))


def _pixel_area_km2(x: np.ndarray, y: np.ndarray) -> float:
    """Área aproximada de um pixel (km²) a partir do espaçamento da grade.

    ``x``/``y`` estão em **metros** no plano geoestacionário; a área é do plano
    do satélite (aproxima a área no solo perto do nadir). Retorna 0 se a grade
    for degenerada (< 2 pontos).
    """
    if x.size < 2 or y.size < 2:
        return 0.0
    dx = abs(float(x[1] - x[0]))
    dy = abs(float(y[1] - y[0]))
    return (dx * dy) / 1.0e6


def detect_convective_cells(
    data_c: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    *,
    threshold_c: float = DEFAULT_THRESHOLD_C,
    min_area_km2: float = DEFAULT_MIN_AREA_KM2,
) -> ConvectiveCellsResult:
    """Detecta células convectivas na imagem IR (temperatura de brilho em °C).

    Pixels com ``data_c < threshold_c`` (topos frios) são agrupados em
    componentes conexas; cada componente com área ``>= min_area_km2`` vira uma
    ``Cell`` com temperatura mínima e centroide. ``NaN`` fora do disco não entra
    na máscara (comparação com ``NaN`` é ``False``).
    """
    from scipy import ndimage

    data = np.asarray(data_c, dtype=float)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    # Máscara dos topos frios; NaN < thr → False (fora do disco não conta).
    with np.errstate(invalid="ignore"):
        cold = data < float(threshold_c)

    labels, n_total = ndimage.label(cold)
    pixel_km2 = _pixel_area_km2(x, y)

    kept_mask = np.zeros(data.shape, dtype=bool)
    cells: list[Cell] = []

    if n_total > 0 and pixel_km2 > 0.0:
        # Vetorizado: nada de laço `labels == id` por componente (era O(n × N) e
        # travava em imagens grandes). Áreas via bincount; T_min e centroide via
        # reduções rotuladas do ndimage — só nos componentes que passam na área.
        npix_per = np.bincount(labels.ravel(), minlength=n_total + 1)[1:]  # exclui fundo (0)
        keep_ids = np.nonzero(npix_per * pixel_km2 >= float(min_area_km2))[0] + 1

        if keep_ids.size:
            kept_mask = np.isin(labels, keep_ids)  # uma passada
            t_mins = ndimage.minimum(data, labels, index=keep_ids)
            coms = ndimage.center_of_mass(np.ones_like(data), labels, index=keep_ids)
            # x/y são 1D lineares: (row, col) → coordenada por interpolação linear.
            dx = float(x[1] - x[0]) if x.size > 1 else 0.0
            dy = float(y[1] - y[0]) if y.size > 1 else 0.0
            x0 = float(x[0])
            y0 = float(y[0])
            t_arr = np.atleast_1d(np.asarray(t_mins, dtype=float))
            for lid, (row, col), t_min in zip(keep_ids, coms, t_arr, strict=True):
                npix = int(npix_per[lid - 1])
                cells.append(
                    Cell(
                        centroid_x=x0 + float(col) * dx,
                        centroid_y=y0 + float(row) * dy,
                        t_min_c=float(t_min),
                        area_km2=npix * pixel_km2,
                        npix=npix,
                    )
                )

    # Ordena por área decrescente (maiores sistemas primeiro).
    cells.sort(key=lambda c: c.area_km2, reverse=True)

    return ConvectiveCellsResult(
        mask=kept_mask,
        cells=cells,
        threshold_c=float(threshold_c),
        min_area_km2=float(min_area_km2),
        n_total=int(n_total),
        n_kept=len(cells),
    )
