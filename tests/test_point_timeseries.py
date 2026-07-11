"""Testes da série temporal num ponto (F6) — data/ecmwf.py.

Cobrem as partes PURAS de ``load_point_timeseries`` com Datasets sintéticos,
sem rede: amostragem do ponto mais próximo (longitude normalizada), coleta das
variáveis de superfície espalhadas em vários datasets (como o ``cfgrib.
open_datasets`` devolve) e a montagem da série (unidades, vento, precip por
intervalo).
"""

import numpy as np
import pytest
import xarray as xr

from cartomet_br.data.ecmwf import (
    METEOGRAM_STEPS,
    PointTimeseries,
    _assemble_point_timeseries,
    _sample_nearest,
    _sample_surface,
)

_LATS = np.array([5.0, 0.0, -5.0])
_LONS = np.array([320.0, 325.0, 330.0])  # 0–360 → normaliza p/ -40, -35, -30


def _grid(varname: str, value: float) -> xr.Dataset:
    data = np.full((len(_LATS), len(_LONS)), float(value))
    return xr.Dataset(
        {varname: (("latitude", "longitude"), data)},
        coords={
            "latitude": _LATS,
            "longitude": _LONS,
            "valid_time": np.datetime64("2026-06-14T12:00"),
            "time": np.datetime64("2026-06-14T00:00"),
        },
    )


def _wind_ds(u: float, v: float) -> xr.Dataset:
    u2 = np.full((len(_LATS), len(_LONS)), float(u))
    v2 = np.full((len(_LATS), len(_LONS)), float(v))
    return xr.Dataset(
        {"u10": (("latitude", "longitude"), u2), "v10": (("latitude", "longitude"), v2)},
        coords={
            "latitude": _LATS,
            "longitude": _LONS,
            "valid_time": np.datetime64("2026-06-14T12:00"),
            "time": np.datetime64("2026-06-14T00:00"),
        },
    )


def test_meteogram_steps_default_horizon():
    assert METEOGRAM_STEPS[0] == 0 and METEOGRAM_STEPS[-1] == 72
    assert all(np.diff(METEOGRAM_STEPS) == 3)


def test_sample_nearest_normalizes_longitude_and_picks_point():
    ds = _grid("msl", 0.0)
    ds["msl"][:] = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=float)
    col, glon, glat = _sample_nearest(ds, -37.0, 0.4)
    assert glon == -35.0 and glat == 0.0
    assert float(np.asarray(col["msl"].values)) == 5.0


def test_sample_surface_collects_vars_across_datasets():
    datasets = [
        _grid("msl", 101300.0),
        _wind_ds(3.0, -4.0),
        _grid("tcwv", 45.0),
        _grid("tp", 0.002),
    ]
    out = _sample_surface(datasets, -37.0, 0.4)
    assert out["msl"] == pytest.approx(101300.0)
    assert out["u10"] == pytest.approx(3.0)
    assert out["v10"] == pytest.approx(-4.0)
    assert out["tcwv"] == pytest.approx(45.0)
    assert out["tp"] == pytest.approx(0.002)
    assert out["grid_lon"] == -35.0 and out["grid_lat"] == 0.0
    assert "2026-06-14" in out["valid_time"]


def test_sample_surface_missing_variable_raises():
    with pytest.raises(ValueError):
        _sample_surface([_grid("msl", 1.0)], -35.0, 0.0)  # faltam wind/tcwv/tp


def test_assemble_units_and_precip_deaccumulation():
    steps = [0, 3, 6, 9]
    t_k = [300.0, 299.0, 298.0, 297.0]
    u10 = [0.0, 0.0, 0.0, 0.0]
    v10 = [-5.0, -5.0, -5.0, -5.0]
    msl_pa = [101300.0] * 4
    tcwv = [40.0, 41.0, 42.0, 43.0]
    tp_m = [0.0, 0.001, 0.003, 0.003]  # acumulado (m) desde t=0

    ts = _assemble_point_timeseries(
        -35.0,
        0.0,
        -35.0,
        0.0,
        steps,
        ["a", "b", "c", "d"],
        t_k,
        u10,
        v10,
        msl_pa,
        tcwv,
        tp_m,
        "00Z 14/06/2026",
        0,
    )
    assert isinstance(ts, PointTimeseries)
    np.testing.assert_allclose(ts.t, [26.85, 25.85, 24.85, 23.85], atol=1e-6)
    np.testing.assert_allclose(ts.msl, [1013.0] * 4)
    # Precip por intervalo: diff do acumulado → [0, 1, 2, 0] mm.
    np.testing.assert_allclose(ts.precip, [0.0, 1.0, 2.0, 0.0], atol=1e-6)
    np.testing.assert_allclose(ts.wind_speed, [5.0] * 4)
    # Vento de sul (v<0) → sopra DE Norte → direção 0°.
    np.testing.assert_allclose(ts.wind_dir, [0.0] * 4, atol=1e-6)


def test_assemble_wind_direction_from_east():
    ts = _assemble_point_timeseries(
        0.0,
        0.0,
        0.0,
        0.0,
        [0],
        [""],
        [300.0],
        [-5.0],
        [0.0],
        [101300.0],
        [40.0],
        [0.0],
        "",
        0,
    )
    # u<0 (sopra p/ oeste) → vem DE leste → 90°.
    assert ts.wind_dir[0] == pytest.approx(90.0)
    assert ts.wind_speed[0] == pytest.approx(5.0)
