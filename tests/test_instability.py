"""Testes dos campos de instabilidade (F9) — data/ecmwf.py.

Cobrem ``_instability_from_dataset`` (parte PURA de ``compute_instability_fields``)
com um Dataset sintético, sem rede: K-index na grade nativa, LI/CAPE/CIN
engrossados e interpolados de volta à grade do extent, e seleção dos índices
pedidos. Render contínuo — sem classes/limiares.
"""

import numpy as np
import xarray as xr

from cartomet_br.data.ecmwf import (
    PL_LEVELS,
    PLFieldData,
    _instability_from_dataset,
)

_EXTENT = [-45.0, -5.0, -20.0, 10.0]   # lon_min, lat_min, lon_max, lat_max


def _synthetic_ds() -> xr.Dataset:
    levs = np.array(PL_LEVELS, dtype=float)
    lats = np.linspace(-5.0, 10.0, 6)   # crescente
    lons = np.linspace(-45.0, -20.0, 8)
    nz, ny, nx = len(levs), lats.size, lons.size
    t = np.empty((nz, ny, nx))
    q = np.empty((nz, ny, nx))
    gh = np.empty((nz, ny, nx))
    for k, p in enumerate(levs):
        t[k] = 300.0 - (1000.0 - p) * 0.05            # K, decresce com a altura
        q[k] = max(0.012 * (p / 1000.0), 1e-4)        # kg/kg, decresce com a altura
        gh[k] = (1000.0 - p) * 8.0
    dims = ("isobaricInhPa", "latitude", "longitude")
    return xr.Dataset(
        {"t": (dims, t), "q": (dims, q), "gh": (dims, gh)},
        coords={
            "isobaricInhPa": levs, "latitude": lats, "longitude": lons,
            "valid_time": np.datetime64("2026-06-14T12:00"),
            "time": np.datetime64("2026-06-14T00:00"),
        },
    )


def test_kindex_native_grid_uniform_and_finite():
    out = _instability_from_dataset(_synthetic_ds(), _EXTENT, ("kindex",), 2, 12)
    assert set(out) == {"kindex"}
    k = out["kindex"]
    assert isinstance(k, PLFieldData)
    assert k.values.shape == (k.lats.size, k.lons.size) == (6, 8)
    assert k.variable == "kindex" and k.unit == "°C" and k.step == 12
    # Campo de entrada uniforme (T,Q independem de lat/lon) → K constante e finito.
    assert np.all(np.isfinite(k.values))
    assert np.nanstd(k.values) < 1e-6
    assert -60.0 < float(np.nanmean(k.values)) < 60.0


def test_parcel_indices_interpolated_to_native_shape():
    out = _instability_from_dataset(_synthetic_ds(), _EXTENT, ("cape", "cin", "li"), 2, 0)
    assert set(out) == {"cape", "cin", "li"}
    for name in ("cape", "cin", "li"):
        f = out[name]
        assert isinstance(f, PLFieldData)
        # Engrossado por stride e interpolado de volta → forma da grade nativa.
        assert f.values.shape == (6, 8)
    # CAPE é ≥ 0 onde definido (NaN onde a coluna falhou).
    cape = out["cape"].values
    assert np.all((cape >= -1e-6) | np.isnan(cape))


def test_only_requested_indices_returned():
    out = _instability_from_dataset(_synthetic_ds(), _EXTENT, ("kindex",), 2, 0)
    assert "cape" not in out and "li" not in out and "cin" not in out


def test_all_nan_fields_are_omitted():
    # Regressão: campo 100% NaN não pode ser entregue (o render por percentil
    # viraria NaN e o contourf quebraria no slot da GUI). Coluna toda sem
    # temperatura → nenhum índice calculável → dict vazio (worker então avisa).
    ds = _synthetic_ds()
    ds["t"][:] = np.nan
    out = _instability_from_dataset(ds, _EXTENT, ("kindex", "cape", "cin", "li"), 2, 0)
    assert out == {}
