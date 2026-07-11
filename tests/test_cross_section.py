"""Testes do corte vertical (F4) — data/ecmwf.py.

Cobrem ``_cross_section_from_dataset`` (parte PURA de ``load_cross_section``) e
``_haversine_cumulative`` com um Dataset sintético, sem rede: forma
(n_níveis, n_pontos), amostragem nos extremos A/B, pressão descendente,
distâncias monotônicas e conversão de unidades de exibição.
"""

import numpy as np
import pytest
import xarray as xr

from cartomet_br.data.ecmwf import (
    PL_LEVELS,
    CrossSection,
    _cross_section_from_dataset,
    _haversine_cumulative,
)


def _synthetic_ds(levels=PL_LEVELS) -> xr.Dataset:
    levs = np.array(levels, dtype=float)
    lats = np.array([10.0, 5.0, 0.0, -5.0, -10.0])  # decrescente (como o IFS)
    lons = np.array([320.0, 325.0, 330.0, 335.0, 340.0])  # 0–360 → -40..-20
    nz, ny, nx = len(levs), len(lats), len(lons)
    t = np.empty((nz, ny, nx))
    gh = np.empty((nz, ny, nx))
    for k, p in enumerate(levs):
        t[k] = 300.0 - (1000.0 - p) * 0.05  # K
        gh[k] = (1000.0 - p) * 8.0  # m
    w = np.full((nz, ny, nx), -0.5)  # Pa/s (ascendência)
    q = np.full((nz, ny, nx), 0.008)  # kg/kg → 8 g/kg
    u = np.full((nz, ny, nx), 10.0)
    v = np.full((nz, ny, nx), -2.0)
    dims = ("isobaricInhPa", "latitude", "longitude")
    return xr.Dataset(
        {
            "t": (dims, t),
            "w": (dims, w),
            "q": (dims, q),
            "u": (dims, u),
            "v": (dims, v),
            "gh": (dims, gh),
        },
        coords={
            "isobaricInhPa": levs,
            "latitude": lats,
            "longitude": lons,
            "valid_time": np.datetime64("2026-06-14T12:00"),
            "time": np.datetime64("2026-06-14T00:00"),
        },
    )


def test_cross_section_shape_and_axes():
    xs = _cross_section_from_dataset(_synthetic_ds(), -38.0, 8.0, -22.0, -8.0, step=12, n_points=40)
    assert isinstance(xs, CrossSection)
    assert xs.t.shape == (len(PL_LEVELS), 40)
    assert xs.w.shape == xs.q.shape == (len(PL_LEVELS), 40)
    # Pressão descendente, superfície primeiro.
    assert xs.pressures[0] == 1000.0
    assert np.all(np.diff(xs.pressures) < 0)
    # Distâncias monotônicas a partir de 0.
    assert xs.distances_km[0] == 0.0
    assert np.all(np.diff(xs.distances_km) > 0)
    # Extremos do caminho = A e B.
    assert xs.lons[0] == pytest.approx(-38.0)
    assert xs.lons[-1] == pytest.approx(-22.0)
    assert xs.lats[0] == pytest.approx(8.0)
    assert xs.lats[-1] == pytest.approx(-8.0)
    assert xs.step == 12 and "2026-06-14" in xs.valid_time


def test_cross_section_display_units():
    xs = _cross_section_from_dataset(_synthetic_ds(), -38.0, 8.0, -22.0, -8.0, step=0, n_points=20)
    # t convertida p/ °C (1000 hPa ≈ 26.85 °C).
    assert 20.0 < xs.t[0, 0] < 30.0
    # q convertida p/ g/kg (0.008 kg/kg → 8 g/kg).
    np.testing.assert_allclose(xs.q, 8.0, atol=1e-6)
    # ω preservado em Pa/s.
    np.testing.assert_allclose(xs.w, -0.5, atol=1e-6)


def test_haversine_cumulative_monotonic_from_zero():
    lons = np.linspace(-40.0, -20.0, 10)
    lats = np.zeros(10)
    d = _haversine_cumulative(lons, lats)
    assert d[0] == 0.0
    assert np.all(np.diff(d) > 0)
    # ~20° de longitude no equador ≈ 2226 km.
    assert 2000.0 < d[-1] < 2500.0
