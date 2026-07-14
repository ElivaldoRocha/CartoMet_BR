"""Detecção de Células Convectivas (camada de dados pura) — SEM Qt/rede.

Exercita ``detect_convective_cells`` sobre grades sintéticas: filtro por área
mínima, temperatura mínima por célula, centroide, tratamento de NaN e da grade
degenerada.
"""

import numpy as np

from cartomet_br.data.convective_cells import (
    DEFAULT_MIN_AREA_KM2,
    DEFAULT_THRESHOLD_C,
    detect_convective_cells,
)

# Grade 100×100 com pixel de 2 km (dx=dy=2000 m → 4 km²/pixel).
_N = 100
_X = np.arange(_N) * 2000.0
_Y = np.arange(_N) * 2000.0


def _grid() -> np.ndarray:
    data = np.full((_N, _N), 15.0)  # fundo quente (sem convecção)
    data[10:40, 10:40] = -60.0  # bloco frio grande: 30×30 = 900 px × 4 = 3600 km²
    data[10:12, 80:82] = -60.0  # bloco frio pequeno: 4 px × 4 = 16 km² (ruído)
    return data


def test_filters_small_cells_by_area():
    data = _grid()
    r = detect_convective_cells(data, _X, _Y, threshold_c=-50.0, min_area_km2=3000.0)
    assert r.n_total == 2  # duas componentes frias antes do filtro
    assert r.n_kept == 1  # só a grande sobrevive
    cell = r.cells[0]
    assert cell.npix == 900
    assert abs(cell.area_km2 - 3600.0) < 1.0
    assert cell.t_min_c == -60.0
    assert int(r.mask.sum()) == 900


def test_centroid_position():
    r = detect_convective_cells(_grid(), _X, _Y, threshold_c=-50.0, min_area_km2=3000.0)
    cx, cy = r.cells[0].centroid_x, r.cells[0].centroid_y
    # Bloco em índices [10:40) → centro ≈ índice 24.5 → 24.5 * 2000 m.
    assert abs(cx - 24.5 * 2000.0) < 2000.0
    assert abs(cy - 24.5 * 2000.0) < 2000.0


def test_threshold_changes_detection():
    data = _grid()
    # Limiar mais frio que os blocos (-60): nada é detectado.
    r = detect_convective_cells(data, _X, _Y, threshold_c=-70.0, min_area_km2=3000.0)
    assert r.n_kept == 0
    assert not r.mask.any()


def test_nan_outside_disk_ignored():
    data = _grid()
    data[0, :] = np.nan  # "fora do disco" — não deve virar célula nem quebrar
    r = detect_convective_cells(data, _X, _Y, threshold_c=-50.0, min_area_km2=3000.0)
    assert r.n_kept == 1  # o NaN não cria célula espúria


def test_no_cold_pixels():
    warm = np.full((_N, _N), 20.0)
    r = detect_convective_cells(warm, _X, _Y, threshold_c=-50.0)
    assert r.n_total == 0 and r.n_kept == 0 and r.cells == []


def test_degenerate_grid_returns_empty():
    # Grade com < 2 pontos → área de pixel indefinida → sem células.
    tiny = np.array([[-60.0]])
    r = detect_convective_cells(tiny, np.array([0.0]), np.array([0.0]), threshold_c=-50.0)
    assert r.n_kept == 0


def test_defaults_are_sane():
    assert DEFAULT_THRESHOLD_C == -50.0
    assert DEFAULT_MIN_AREA_KM2 == 3000.0
    # Com os defaults, a célula grande do grid é retida.
    r = detect_convective_cells(_grid(), _X, _Y)
    assert r.n_kept == 1


def test_many_components_vectorized():
    """Muitos blocos frios: a versão vetorizada deve contar/filtrar corretamente
    e rodar rápido (o antigo laço O(n×N) travaria)."""
    import time

    n = 600
    x = np.arange(n) * 2000.0
    y = np.arange(n) * 2000.0
    data = np.full((n, n), 20.0)
    # 25 blocos grandes (40×40 px = 6400 km² ≥ 3000) numa grade 5×5, bem separados.
    big = 0
    for i in range(5):
        for j in range(5):
            r0, c0 = 20 + i * 110, 20 + j * 110
            data[r0 : r0 + 40, c0 : c0 + 40] = -60.0
            big += 1
    # 1 bloco pequeno (10×10 = 400 km² < 3000) → descartado.
    data[560:570, 560:570] = -60.0

    t = time.perf_counter()
    res = detect_convective_cells(data, x, y, threshold_c=-50.0, min_area_km2=3000.0)
    dt = time.perf_counter() - t

    assert res.n_total == big + 1  # 25 grandes + 1 pequeno
    assert res.n_kept == big  # só os 25 grandes
    assert all(c.npix == 1600 for c in res.cells)
    assert dt < 3.0  # vetorizado: folgado mesmo em CI lento
