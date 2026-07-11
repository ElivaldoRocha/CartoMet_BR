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

_EXTENT = [-45.0, -5.0, -20.0, 10.0]  # lon_min, lat_min, lon_max, lat_max


def _synthetic_ds() -> xr.Dataset:
    levs = np.array(PL_LEVELS, dtype=float)
    lats = np.linspace(-5.0, 10.0, 6)  # crescente
    lons = np.linspace(-45.0, -20.0, 8)
    nz, ny, nx = len(levs), lats.size, lons.size
    t = np.empty((nz, ny, nx))
    q = np.empty((nz, ny, nx))
    gh = np.empty((nz, ny, nx))
    for k, p in enumerate(levs):
        t[k] = 300.0 - (1000.0 - p) * 0.05  # K, decresce com a altura
        q[k] = max(0.012 * (p / 1000.0), 1e-4)  # kg/kg, decresce com a altura
        gh[k] = (1000.0 - p) * 8.0
    dims = ("isobaricInhPa", "latitude", "longitude")
    return xr.Dataset(
        {"t": (dims, t), "q": (dims, q), "gh": (dims, gh)},
        coords={
            "isobaricInhPa": levs,
            "latitude": lats,
            "longitude": lons,
            "valid_time": np.datetime64("2026-06-14T12:00"),
            "time": np.datetime64("2026-06-14T00:00"),
        },
    )


def _unstable_ds() -> xr.Dataset:
    """Perfil convectivamente instável (CAPE>0) → LFC/EL existem.

    Lapse mais íngreme (0.085 K/hPa) + superfície quente e úmida garantem
    flutuabilidade positiva; usado para exercitar o EL (indefinido no perfil
    estável de ``_synthetic_ds``).
    """
    levs = np.array(PL_LEVELS, dtype=float)
    lats = np.linspace(-5.0, 10.0, 6)
    lons = np.linspace(-45.0, -20.0, 8)
    nz, ny, nx = len(levs), lats.size, lons.size
    t = np.empty((nz, ny, nx))
    q = np.empty((nz, ny, nx))
    gh = np.empty((nz, ny, nx))
    for k, p in enumerate(levs):
        t[k] = 301.0 - (1000.0 - p) * 0.085
        q[k] = max(0.018 * (p / 1000.0) ** 3, 1e-5)
        gh[k] = (1000.0 - p) * 8.0
    dims = ("isobaricInhPa", "latitude", "longitude")
    return xr.Dataset(
        {"t": (dims, t), "q": (dims, q), "gh": (dims, gh)},
        coords={
            "isobaricInhPa": levs,
            "latitude": lats,
            "longitude": lons,
            "valid_time": np.datetime64("2026-06-14T12:00"),
            "time": np.datetime64("2026-06-14T00:00"),
        },
    )


def _wind_ds() -> xr.Dataset:
    """Vento u,v na MESMA grade de ``_synthetic_ds`` (gh = (1000−p)·8 m).

    u cresce 5 m/s por km de altura, v = 0 → cisalhamento 0–6 km analítico de
    30 m/s em toda a grade (alvo determinístico do teste de ``bulk_shear``).
    """
    levs = np.array(PL_LEVELS, dtype=float)
    lats = np.linspace(-5.0, 10.0, 6)
    lons = np.linspace(-45.0, -20.0, 8)
    nz, ny, nx = len(levs), lats.size, lons.size
    u = np.empty((nz, ny, nx))
    v = np.zeros((nz, ny, nx))
    for k, p in enumerate(levs):
        gh = (1000.0 - p) * 8.0
        u[k] = (gh / 1000.0) * 5.0
    dims = ("isobaricInhPa", "latitude", "longitude")
    return xr.Dataset(
        {"u": (dims, u), "v": (dims, v)},
        coords={
            "isobaricInhPa": levs,
            "latitude": lats,
            "longitude": lons,
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


def test_total_totals_native_grid_uniform_and_finite():
    # TT é vetorizado na grade nativa (como o K): entrada uniforme → constante.
    out = _instability_from_dataset(_synthetic_ds(), _EXTENT, ("totaltotals",), 2, 7)
    assert set(out) == {"totaltotals"}
    tt = out["totaltotals"]
    assert isinstance(tt, PLFieldData)
    assert tt.values.shape == (6, 8) and tt.variable == "totaltotals"
    assert tt.unit == "°C" and tt.step == 7
    assert np.all(np.isfinite(tt.values))
    assert np.nanstd(tt.values) < 1e-6
    assert 0.0 < float(np.nanmean(tt.values)) < 80.0


def test_lcl_height_in_meters_via_gh():
    # LCL sai em ALTURA (m, MSL) convertida pela coluna de gh; deve cair dentro
    # do intervalo de gh do perfil (0 … (1000-50)*8 = 7600 m) e ser finito.
    out = _instability_from_dataset(_synthetic_ds(), _EXTENT, ("lcl",), 2, 0)
    assert set(out) == {"lcl"}
    lcl = out["lcl"]
    assert lcl.unit == "m" and lcl.values.shape == (6, 8)
    assert np.all(np.isfinite(lcl.values))
    assert np.all((lcl.values >= 0.0) & (lcl.values <= 7600.0))


def test_el_height_when_unstable_profile():
    # No perfil instável (CAPE>0) o EL existe; sai em metros e finito.
    out = _instability_from_dataset(_unstable_ds(), _EXTENT, ("cape", "el"), 2, 0)
    assert "el" in out, "EL deveria existir com CAPE>0"
    el = out["el"]
    assert el.unit == "m" and el.values.shape == (6, 8)
    assert np.all(np.isfinite(el.values))
    assert np.all(el.values > 0.0)
    # EL acima do LCL/base — coerência mínima de altura no perfil sintético.
    assert float(np.nanmean(el.values)) > 2000.0


def test_lfc_height_when_unstable_profile():
    # No perfil instável (CAPE>0) o LFC existe; sai em metros, finito, e respeita
    # a ordenação física LCL ≤ LFC ≤ EL (base ≤ convecção livre ≤ topo).
    out = _instability_from_dataset(_unstable_ds(), _EXTENT, ("lcl", "lfc", "el"), 2, 0)
    assert "lfc" in out, "LFC deveria existir com CAPE>0"
    lfc = out["lfc"]
    assert lfc.unit == "m" and lfc.values.shape == (6, 8)
    assert np.all(np.isfinite(lfc.values))
    assert np.all(lfc.values > 0.0)
    lcl_m = float(np.nanmean(out["lcl"].values))
    lfc_m = float(np.nanmean(lfc.values))
    el_m = float(np.nanmean(out["el"].values))
    assert lcl_m - 1.0 <= lfc_m <= el_m + 1.0


def test_lfc_omitted_when_stable_profile():
    # Perfil estável (sem CAPE) → LFC indefinido em toda coluna → omitido (NaN
    # honesto: nada de preenchimento artificial).
    out = _instability_from_dataset(_synthetic_ds(), _EXTENT, ("lfc",), 2, 0)
    assert out == {}


def test_levels_omitted_without_gh():
    # Sem gh na rodada, LCL/LFC/EL são omitidos com elegância (não quebram os demais).
    ds = _synthetic_ds()
    del ds["gh"]
    out = _instability_from_dataset(
        ds, _EXTENT, ("kindex", "totaltotals", "lcl", "lfc", "el"), 2, 0
    )
    assert "kindex" in out and "totaltotals" in out
    assert "lcl" not in out and "lfc" not in out and "el" not in out


def test_shear_0_6km_from_wind_profile():
    # Cisalhamento 0–6 km via bulk_shear com u,v do 2º dataset; perfil de +5 m/s
    # por km sobre 6 km → 30 m/s determinístico (unidade m/s, grade nativa).
    out = _instability_from_dataset(_synthetic_ds(), _EXTENT, ("shear",), 2, 0, wind_ds=_wind_ds())
    assert set(out) == {"shear"}
    s = out["shear"]
    assert isinstance(s, PLFieldData)
    assert s.unit == "m/s" and s.values.shape == (6, 8)
    assert np.all(np.isfinite(s.values))
    assert np.allclose(s.values, 30.0, atol=1.0)


def test_shear_omitted_without_wind():
    # Sem wind_ds não há u,v → cisalhamento omitido (não quebra os demais).
    out = _instability_from_dataset(_synthetic_ds(), _EXTENT, ("kindex", "shear"), 2, 0)
    assert "kindex" in out and "shear" not in out


def test_shear_omitted_without_gh():
    # bulk_shear precisa da altura (gh) → sem gh, omitido mesmo com vento.
    ds = _synthetic_ds()
    del ds["gh"]
    out = _instability_from_dataset(ds, _EXTENT, ("shear",), 2, 0, wind_ds=_wind_ds())
    assert "shear" not in out


def test_all_nan_fields_are_omitted():
    # Regressão: campo 100% NaN não pode ser entregue (o render por percentil
    # viraria NaN e o contourf quebraria no slot da GUI). Coluna toda sem
    # temperatura → nenhum índice calculável → dict vazio (worker então avisa).
    ds = _synthetic_ds()
    ds["t"][:] = np.nan
    out = _instability_from_dataset(ds, _EXTENT, ("kindex", "cape", "cin", "li"), 2, 0)
    assert out == {}
