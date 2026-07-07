"""Testes da detecção de centros H/L — máscara orográfica + ranking por persistência."""

import cartopy.crs as ccrs
import matplotlib.pyplot as plt
import numpy as np
import pytest

from cartomet_br.charts.synoptic import plot_maxmin_points


def _synthetic_field():
    """Campo com 3 máximos de proeminências distintas.

    G (25,25) = 1030  — máximo global (persistência infinita)
    A (25,75) = 1024  — ligado a G por um corredor de 1023 → persistência ~1
    B (75,50) = 1022  — isolado no fundo de 1013 → persistência ~9

    Ranking por INTENSIDADE escolheria G e A; por PROEMINÊNCIA, G e B.
    """
    field = np.full((100, 100), 1013.0)
    field[25, 25:76] = 1023.0  # corredor
    field[25, 25] = 1030.0  # G
    field[25, 75] = 1024.0  # A
    field[75, 50] = 1022.0  # B
    return field


@pytest.fixture
def geo_ax():
    fig = plt.figure(figsize=(4, 4))
    ax = fig.add_subplot(projection=ccrs.PlateCarree())
    yield ax
    plt.close(fig)


def _plotted_values(ax) -> set[int]:
    """Valores numéricos plotados (cada centro = símbolo + valor '\\n1234')."""
    out = set()
    for t in ax.texts:
        txt = t.get_text().strip()
        if txt.isdigit():
            out.add(int(txt))
    return out


def _run(ax, field, **kwargs):
    lon2d, lat2d = np.meshgrid(np.linspace(-80, -30, 100), np.linspace(-40, 10, 100))
    defaults = dict(
        extrema="max", nsize=5, symbol="H", min_distance=3, threshold=None, max_points=2
    )
    defaults.update(kwargs)
    plot_maxmin_points(ax, lon2d, lat2d, field, **defaults)


class TestPersistenceRanking:
    def test_proeminencia_vence_intensidade(self, geo_ax):
        # max_points=2: persistência escolhe G(1030) e B(1022) — NÃO A(1024),
        # que é mais intenso porém raso (colado ao corredor de G)
        _run(geo_ax, _synthetic_field(), max_points=2)
        values = _plotted_values(geo_ax)
        assert 1030 in values
        assert 1022 in values, f"B (proeminente) deveria vencer A (raso): {values}"
        assert 1024 not in values

    def test_todos_entram_sem_teto(self, geo_ax):
        _run(geo_ax, _synthetic_field(), max_points=10)
        values = _plotted_values(geo_ax)
        assert {1030, 1024, 1022} <= values


class TestExcludeMask:
    def test_mascara_veta_regiao(self, geo_ax):
        field = _synthetic_field()
        mask = np.zeros_like(field, dtype=bool)
        mask[:50, :50] = True  # engole G (25,25)
        _run(geo_ax, field, max_points=10, exclude_mask=mask)
        values = _plotted_values(geo_ax)
        assert 1030 not in values, "G está na região mascarada"
        assert 1022 in values

    def test_sem_mascara_comportamento_integral(self, geo_ax):
        _run(geo_ax, _synthetic_field(), max_points=10, exclude_mask=None)
        assert 1030 in _plotted_values(geo_ax)


class TestHighlandConstant:
    def test_limiar_calibravel_existe(self):
        from cartomet_br.data.ecmwf import PNMM_ARTIFACT_DELTA_HPA

        # ~155 hPa ≈ 1500 m na atmosfera padrão — parâmetro, não constante sagrada
        assert 100.0 < PNMM_ARTIFACT_DELTA_HPA < 250.0
