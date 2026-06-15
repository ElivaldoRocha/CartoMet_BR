"""Testes da extração de coluna vertical do modelo (F1) — data/ecmwf.py.

Cobrem ``_profile_from_dataset`` (parte PURA de ``load_model_profile``) com um
Dataset sintético, sem rede: seleção do ponto mais próximo, ordenação por
pressão descendente, remoção de níveis abaixo do solo (NaN) e o guarda de
níveis insuficientes.
"""

import numpy as np
import pytest
import xarray as xr

from cartomet_br.data.ecmwf import PL_LEVELS, ModelProfile, _profile_from_dataset


def _synthetic_ds(levels=PL_LEVELS) -> xr.Dataset:
    levs = np.array(levels, dtype=float)
    lats = np.array([5.0, 0.0, -5.0])
    lons = np.array([320.0, 325.0, 330.0])  # 0–360 → normaliza p/ -40, -35, -30
    nz, ny, nx = len(levs), len(lats), len(lons)
    t = np.empty((nz, ny, nx))
    gh = np.empty((nz, ny, nx))
    for k, p in enumerate(levs):
        t[k] = 300.0 - (1000.0 - p) * 0.05   # K, decresce com a altura
        gh[k] = (1000.0 - p) * 8.0           # m, cresce com a altura
    q = np.full((nz, ny, nx), 0.01)          # kg/kg
    u = np.full((nz, ny, nx), 5.0)
    v = np.full((nz, ny, nx), -3.0)
    dims = ("isobaricInhPa", "latitude", "longitude")
    return xr.Dataset(
        {"t": (dims, t), "q": (dims, q), "u": (dims, u), "v": (dims, v), "gh": (dims, gh)},
        coords={
            "isobaricInhPa": levs, "latitude": lats, "longitude": lons,
            "valid_time": np.datetime64("2026-06-14T12:00"),
            "time": np.datetime64("2026-06-14T00:00"),
        },
    )


def test_extrai_coluna_descendente_no_ponto_mais_proximo():
    prof = _profile_from_dataset(_synthetic_ds(), lon=-37.0, lat=0.4, cycle=0, step=12)
    assert isinstance(prof, ModelProfile)
    # 13 níveis, pressão estritamente descendente, superfície primeiro.
    assert prof.pressures.size == len(PL_LEVELS)
    assert np.all(np.diff(prof.pressures) < 0)
    assert prof.pressures[0] == 1000.0
    # Ponto mais próximo de (-37, 0.4) é a grade (-35, 0).
    assert prof.grid_lon == -35.0
    assert prof.grid_lat == 0.0
    # Unidades cruas preservadas (t em K, q em kg/kg).
    assert 250.0 < prof.t[0] < 320.0
    assert prof.q[0] == pytest.approx(0.01)
    assert prof.cycle == 0 and prof.step == 12
    assert "2026-06-14" in prof.valid_time


def test_drop_de_niveis_abaixo_do_solo():
    ds = _synthetic_ds()
    # Zera (NaN) o nível de superfície (1000 hPa) na coluna selecionada (-35, 0).
    ds["t"][0, 1, 1] = np.nan
    prof = _profile_from_dataset(ds, lon=-35.0, lat=0.0, cycle=12, step=0)
    assert prof.pressures.size == len(PL_LEVELS) - 1
    assert prof.pressures[0] == 925.0          # 1000 hPa foi descartado
    assert np.all(np.isfinite(prof.t))


def test_poucos_niveis_validos_levanta_erro():
    ds = _synthetic_ds(levels=[1000.0, 850.0])  # só 2 níveis
    with pytest.raises(ValueError):
        _profile_from_dataset(ds, lon=-35.0, lat=0.0, cycle=0, step=0)
