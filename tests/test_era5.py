"""Testes do motor ERA5 (reanálise Copernicus/CDS) — sem tocar a rede.

Cobre: construção da requisição, span de datas, nome de cache determinístico,
agregação temporal, o caminho cache-first de ``load_era5_field`` (arquivo já em
disco → nenhum download) e os validadores do ``DataService`` (incl. o atraso do
ERA5T).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
import xarray as xr

from cartomet_br.core.config import Config
from cartomet_br.data.cds_credentials import ERA5_MIN_DELAY_DAYS
from cartomet_br.data.era5 import (
    AGG_INDEX_MODES,
    AGG_MODES,
    AGG_PROFILES,
    ERA5_VARIABLES,
    INDEX_RENDER_KEY,
    _cache_path,
    _date_span,
    _max_consecutive,
    _point_area,
    _series_cache_path,
    build_era5_request,
    default_agg,
    era5_period_days,
    load_era5_field,
    load_era5_timeseries,
    profile_modes,
)
from cartomet_br.services.data_service import DataService, ValidationError

# Extensão de teste [lon_min, lat_min, lon_max, lat_max] — Atlântico equatorial.
EXTENT = [-50.0, -10.0, -30.0, 10.0]


def _safe_day() -> str:
    """Um dia seguramente publicado (hoje − atraso do ERA5T), em ISO."""
    return (datetime.now(UTC).date() - timedelta(days=ERA5_MIN_DELAY_DAYS)).isoformat()


# ── build_era5_request ──────────────────────────────────────────────────────


def test_request_area_is_north_west_south_east():
    var = ERA5_VARIABLES["era5_t2m"]
    req = build_era5_request(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT)
    # area = [N, W, S, E] a partir de [lon_min, lat_min, lon_max, lat_max]
    assert req["area"] == [10.0, -50.0, -10.0, -30.0]
    assert req["data_format"] == "netcdf"
    assert req["variable"] == ["2m_temperature"]


def test_request_instant_has_single_hour_aggregate_has_24():
    var = ERA5_VARIABLES["era5_t2m"]
    inst = build_era5_request(var, "2024-06-01", "2024-06-01", 6, "hora", EXTENT)
    agg = build_era5_request(var, "2024-06-01", "2024-06-01", 6, "media", EXTENT)
    assert inst["time"] == ["06:00"]
    assert len(agg["time"]) == 24


def test_request_rejects_bad_agg():
    var = ERA5_VARIABLES["era5_t2m"]
    with pytest.raises(ValueError):
        build_era5_request(var, "2024-06-01", "2024-06-01", 12, "mediana", EXTENT)


# ── _date_span ──────────────────────────────────────────────────────────────


def test_date_span_single_day():
    years, months, days = _date_span("2024-06-15", "2024-06-15")
    assert years == ["2024"]
    assert months == ["06"]
    assert days == ["15"]


def test_date_span_crossing_month():
    years, months, days = _date_span("2024-06-29", "2024-07-02")
    assert years == ["2024"]
    assert months == ["06", "07"]
    assert days == ["01", "02", "29", "30"]


# ── nome de cache determinístico ────────────────────────────────────────────


def test_cache_path_is_deterministic_and_encodes_region(tmp_path):
    var = ERA5_VARIABLES["era5_precip"]
    p1 = _cache_path(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT, tmp_path)
    p2 = _cache_path(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT, tmp_path)
    assert p1 == p2
    assert p1.suffix == ".nc"
    # região diferente ⇒ arquivo diferente (subsetting é server-side)
    other = _cache_path(var, "2024-06-01", "2024-06-01", 12, "hora", [0, 0, 10, 10], tmp_path)
    assert other != p1


# ── caminho cache-first de load_era5_field (sem rede) ───────────────────────


def _write_fake_cache(path, var_name, values_2d, *, kelvin_offset=0.0):
    """Escreve um NetCDF sintético no caminho de cache exato."""
    lats = np.array([10, 5, 0, -5, -10], dtype="float32")  # descendente (como o CDS)
    lons = np.array([-50, -45, -40, -35, -30], dtype="float32")
    times = np.array(["2024-06-01T12:00"], dtype="datetime64[ns]")
    data = np.broadcast_to(values_2d, (1, lats.size, lons.size)).astype("float32")
    ds = xr.Dataset(
        {var_name: (("valid_time", "latitude", "longitude"), data + kelvin_offset)},
        coords={"valid_time": times, "latitude": lats, "longitude": lons},
    )
    ds.to_netcdf(path)
    ds.close()


def test_load_scalar_cache_hit_applies_conversion(tmp_path):
    var = ERA5_VARIABLES["era5_t2m"]
    target = _cache_path(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT, tmp_path)
    grid = np.full((5, 5), 300.0, dtype="float32")  # 300 K
    _write_fake_cache(target, "t2m", grid)

    data = load_era5_field(
        "era5_t2m",
        "2024-06-01",
        "2024-06-01",
        12,
        "hora",
        EXTENT,
        data_dir=tmp_path,
        smoothing_sigma=0.0,
    )
    assert data.source == "era5"
    assert data.variable == "era5_t2m"
    assert data.unit == "°C"
    # 300 K − 273.15 ≈ 26.85 °C
    assert np.isclose(np.nanmean(data.values), 26.85, atol=0.1)
    assert data.extra["agg"] == "hora"
    assert data.extra["date_start"] == "2024-06-01"


def test_load_wind_cache_hit_builds_components(tmp_path):
    var = ERA5_VARIABLES["era5_wind10m"]
    target = _cache_path(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT, tmp_path)
    lats = np.array([10, 5, 0, -5, -10], dtype="float32")
    lons = np.array([-50, -45, -40, -35, -30], dtype="float32")
    times = np.array(["2024-06-01T12:00"], dtype="datetime64[ns]")
    u = np.full((1, 5, 5), 3.0, dtype="float32")
    v = np.full((1, 5, 5), 4.0, dtype="float32")
    ds = xr.Dataset(
        {
            "u10": (("valid_time", "latitude", "longitude"), u),
            "v10": (("valid_time", "latitude", "longitude"), v),
        },
        coords={"valid_time": times, "latitude": lats, "longitude": lons},
    )
    ds.to_netcdf(target)
    ds.close()

    data = load_era5_field(
        "era5_wind10m",
        "2024-06-01",
        "2024-06-01",
        12,
        "hora",
        EXTENT,
        data_dir=tmp_path,
        smoothing_sigma=0.0,
    )
    assert data.u_values is not None and data.v_values is not None
    # |(3,4)| = 5
    assert np.allclose(data.values, 5.0)


def test_load_daily_sum_aggregates_over_time(tmp_path):
    var = ERA5_VARIABLES["era5_precip"]
    target = _cache_path(var, "2024-06-01", "2024-06-01", 0, "soma", EXTENT, tmp_path)
    lats = np.array([10, 5, 0, -5, -10], dtype="float32")
    lons = np.array([-50, -45, -40, -35, -30], dtype="float32")
    times = np.array([f"2024-06-01T{h:02d}:00" for h in range(24)], dtype="datetime64[ns]")
    # 0.001 m por hora × 24 = 0.024 m = 24 mm
    tp = np.full((24, 5, 5), 0.001, dtype="float32")
    ds = xr.Dataset(
        {"tp": (("valid_time", "latitude", "longitude"), tp)},
        coords={"valid_time": times, "latitude": lats, "longitude": lons},
    )
    ds.to_netcdf(target)
    ds.close()

    data = load_era5_field(
        "era5_precip",
        "2024-06-01",
        "2024-06-01",
        0,
        "soma",
        EXTENT,
        data_dir=tmp_path,
        smoothing_sigma=0.0,
    )
    assert np.isclose(np.nanmean(data.values), 24.0, atol=0.1)  # mm


# ── validadores do DataService ──────────────────────────────────────────────


def _service(tmp_path):
    return DataService(Config(extent=EXTENT, data_dir=tmp_path))


def test_validate_rejects_recent_date(tmp_path):
    today = datetime.now(UTC).date().isoformat()
    with pytest.raises(ValidationError):
        _service(tmp_path).validate_era5_request("era5_t2m", today, today, 12, "hora")


def test_validate_accepts_safe_date(tmp_path):
    day = _safe_day()
    # não deve levantar
    _service(tmp_path).validate_era5_request("era5_t2m", day, day, 12, "hora")


def test_validate_rejects_sum_for_non_precip(tmp_path):
    day = _safe_day()
    with pytest.raises(ValidationError):
        _service(tmp_path).validate_era5_request("era5_t2m", day, day, 12, "soma")


def test_validate_rejects_inverted_range(tmp_path):
    with pytest.raises(ValidationError):
        _service(tmp_path).validate_era5_request(
            "era5_t2m", "2024-06-10", "2024-06-01", 12, "media"
        )


def test_validate_rejects_bad_hour(tmp_path):
    day = _safe_day()
    with pytest.raises(ValidationError):
        _service(tmp_path).validate_era5_request("era5_t2m", day, day, 25, "hora")


def test_validate_rejects_unknown_variable(tmp_path):
    day = _safe_day()
    with pytest.raises(ValidationError):
        _service(tmp_path).validate_era5_request("era5_bogus", day, day, 12, "hora")


# ── Sumarização temporal: hora fixa + duas etapas (diária) ──────────────────


def _write_time_cache(path, var_name, iso_times, per_step_values):
    """NetCDF sintético com campo constante por passo (valor único por instante)."""
    lats = np.array([10, 5, 0, -5, -10], dtype="float32")
    lons = np.array([-50, -45, -40, -35, -30], dtype="float32")
    times = np.array(iso_times, dtype="datetime64[ns]")
    data = np.empty((len(iso_times), lats.size, lons.size), dtype="float32")
    for i, v in enumerate(per_step_values):
        data[i] = v
    ds = xr.Dataset(
        {var_name: (("valid_time", "latitude", "longitude"), data)},
        coords={"valid_time": times, "latitude": lats, "longitude": lons},
    )
    ds.to_netcdf(path)
    ds.close()


def test_request_media_hora_is_single_hour_max_diaria_is_24():
    var = ERA5_VARIABLES["era5_tmax"]
    mh = build_era5_request(var, "2024-06-01", "2024-06-03", 12, "media_hora", EXTENT)
    md = build_era5_request(var, "2024-06-01", "2024-06-03", 12, "max_diaria", EXTENT)
    assert mh["time"] == ["12:00"]
    assert len(md["time"]) == 24


def test_media_hora_cache_path_differs_from_media(tmp_path):
    var = ERA5_VARIABLES["era5_tmax"]
    a = _cache_path(var, "2024-06-01", "2024-06-03", 12, "media_hora", EXTENT, tmp_path)
    b = _cache_path(var, "2024-06-01", "2024-06-03", 12, "media", EXTENT, tmp_path)
    assert a != b
    assert "12z" in a.name and "24h" in b.name


def test_load_media_hora_averages_only_fixed_hour(tmp_path):
    var = ERA5_VARIABLES["era5_tmax"]
    target = _cache_path(var, "2024-06-01", "2024-06-03", 12, "media_hora", EXTENT, tmp_path)
    # só a hora 12 de 3 dias (o motor pede só essa hora): 15, 17, 19 °C
    _write_time_cache(
        target,
        "mx2t",
        ["2024-06-01T12:00", "2024-06-02T12:00", "2024-06-03T12:00"],
        [288.15, 290.15, 292.15],
    )
    data = load_era5_field(
        "era5_tmax",
        "2024-06-01",
        "2024-06-03",
        12,
        "media_hora",
        EXTENT,
        data_dir=tmp_path,
        smoothing_sigma=0.0,
    )
    assert np.isclose(np.nanmean(data.values), 17.0, atol=0.05)  # média (15,17,19)


def test_load_max_diaria_is_two_stage_not_absolute_max(tmp_path):
    var = ERA5_VARIABLES["era5_tmax"]
    times = ["2024-06-01T00:00", "2024-06-01T12:00", "2024-06-02T00:00", "2024-06-02T12:00"]
    kelvin = [283.15, 288.15, 293.15, 298.15]  # °C: 10, 15, 20, 25

    tgt_md = _cache_path(var, "2024-06-01", "2024-06-02", 0, "max_diaria", EXTENT, tmp_path)
    _write_time_cache(tgt_md, "mx2t", times, kelvin)
    md = load_era5_field(
        "era5_tmax",
        "2024-06-01",
        "2024-06-02",
        0,
        "max_diaria",
        EXTENT,
        data_dir=tmp_path,
        smoothing_sigma=0.0,
    )
    # máx diária: dia1 max(10,15)=15, dia2 max(20,25)=25 → média = 20
    assert np.isclose(np.nanmean(md.values), 20.0, atol=0.05)

    tgt_abs = _cache_path(var, "2024-06-01", "2024-06-02", 0, "maxima", EXTENT, tmp_path)
    _write_time_cache(tgt_abs, "mx2t", times, kelvin)
    mx = load_era5_field(
        "era5_tmax",
        "2024-06-01",
        "2024-06-02",
        0,
        "maxima",
        EXTENT,
        data_dir=tmp_path,
        smoothing_sigma=0.0,
    )
    # máxima ABSOLUTA do período = 25 → distinta da média das máximas diárias (20)
    assert np.isclose(np.nanmean(mx.values), 25.0, atol=0.05)


# ── Fase 2: níveis de pressão (Pressure Levels) ─────────────────────────────


def test_pl_variable_uses_pressure_dataset():
    from cartomet_br.data.era5 import ERA5_PRESSURE_LEVELS

    var = ERA5_VARIABLES["era5pl_t"]
    assert var.pressure_level is True
    assert var.dataset == ERA5_PRESSURE_LEVELS


def test_pl_request_has_pressure_level_key():
    var = ERA5_VARIABLES["era5pl_gh"]
    req = build_era5_request(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT, level=500)
    assert req["pressure_level"] == ["500"]
    assert req["variable"] == ["geopotential"]


def test_pl_request_requires_level():
    var = ERA5_VARIABLES["era5pl_t"]
    with pytest.raises(ValueError):
        build_era5_request(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT, level=0)


def test_surface_request_has_no_pressure_level_key():
    var = ERA5_VARIABLES["era5_t2m"]
    req = build_era5_request(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT)
    assert "pressure_level" not in req


def test_pl_cache_path_encodes_level(tmp_path):
    var = ERA5_VARIABLES["era5pl_t"]
    p500 = _cache_path(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT, tmp_path, level=500)
    p850 = _cache_path(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT, tmp_path, level=850)
    assert p500 != p850
    assert "500hPa" in p500.name


def test_load_pl_selects_level_and_converts_geopotential(tmp_path):
    var = ERA5_VARIABLES["era5pl_gh"]
    target = _cache_path(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT, tmp_path, level=500)
    lats = np.array([10, 5, 0, -5, -10], dtype="float32")
    lons = np.array([-50, -45, -40, -35, -30], dtype="float32")
    times = np.array(["2024-06-01T12:00"], dtype="datetime64[ns]")
    levels = np.array([850, 500, 250], dtype="int32")
    # z (m²/s²) distinto por nível: 500 hPa ≈ 5500 mgp × g0
    zvals = np.empty((1, 3, 5, 5), dtype="float32")
    zvals[:, 0] = 1500.0 * 9.80665  # 850 hPa
    zvals[:, 1] = 5500.0 * 9.80665  # 500 hPa
    zvals[:, 2] = 10500.0 * 9.80665  # 250 hPa
    ds = xr.Dataset(
        {"z": (("valid_time", "pressure_level", "latitude", "longitude"), zvals)},
        coords={
            "valid_time": times,
            "pressure_level": levels,
            "latitude": lats,
            "longitude": lons,
        },
    )
    ds.to_netcdf(target)
    ds.close()

    data = load_era5_field(
        "era5pl_gh",
        "2024-06-01",
        "2024-06-01",
        12,
        "hora",
        EXTENT,
        level=500,
        data_dir=tmp_path,
        smoothing_sigma=0.0,
    )
    assert data.level == 500
    assert data.unit == "mgp"
    # selecionou o nível de 500 hPa e converteu z→altura (÷ g0) ≈ 5500 mgp
    assert np.isclose(np.nanmean(data.values), 5500.0, atol=1.0)
    assert data.extra["level"] == 500


def test_validate_rejects_pl_without_level(tmp_path):
    day = _safe_day()
    with pytest.raises(ValidationError):
        _service(tmp_path).validate_era5_request("era5pl_t", day, day, 12, "hora", level=0)


def test_validate_accepts_pl_with_valid_level(tmp_path):
    day = _safe_day()
    _service(tmp_path).validate_era5_request("era5pl_t", day, day, 12, "hora", level=500)


# ── Fase 5: catálogo expandido ──────────────────────────────────────────────

_NEW_KEYS = [
    "era5_tmax",
    "era5_tmin",
    "era5_olr",
    "era5_toa_sw",
    "era5_ssrd",
    "era5_strd",
    "era5_sst",
    "era5_d2m",
    "era5_tcc",
    "era5_cape",
    "era5_kindex",
    "era5_totalx",
    "era5_gust",
    "era5pl_q",
    "era5pl_vo",
    "era5pl_d",
]


def test_new_keys_have_catalog_and_registry_parity():
    from cartomet_br.data.ecmwf import VARIABLE_REGISTRY

    for key in _NEW_KEYS:
        assert key in ERA5_VARIABLES, f"faltou em ERA5_VARIABLES: {key}"
        assert key in VARIABLE_REGISTRY, f"faltou em VARIABLE_REGISTRY: {key}"
        # o 'param' do registro deve casar com o short-name do NetCDF
        assert VARIABLE_REGISTRY[key]["param"] == ERA5_VARIABLES[key].nc_names


def test_new_pl_keys_use_pressure_dataset():
    from cartomet_br.data.era5 import ERA5_PRESSURE_LEVELS, ERA5_SINGLE_LEVELS

    for key in ("era5pl_q", "era5pl_vo", "era5pl_d"):
        assert ERA5_VARIABLES[key].pressure_level is True
        assert ERA5_VARIABLES[key].dataset == ERA5_PRESSURE_LEVELS
    for key in ("era5_olr", "era5_sst", "era5_cape"):
        assert ERA5_VARIABLES[key].pressure_level is False
        assert ERA5_VARIABLES[key].dataset == ERA5_SINGLE_LEVELS


def test_new_variables_are_not_wind():
    # inclusive a rajada é magnitude escalar (contourf), não campo u/v
    for key in _NEW_KEYS:
        assert ERA5_VARIABLES[key].is_wind is False


def test_pl_q_request_has_pressure_level():
    var = ERA5_VARIABLES["era5pl_q"]
    req = build_era5_request(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT, level=850)
    assert req["pressure_level"] == ["850"]
    assert req["variable"] == ["specific_humidity"]


def test_load_sst_cache_hit_converts_kelvin(tmp_path):
    var = ERA5_VARIABLES["era5_sst"]
    target = _cache_path(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT, tmp_path)
    _write_fake_cache(target, "sst", np.full((5, 5), 300.0, dtype="float32"))
    data = load_era5_field(
        "era5_sst",
        "2024-06-01",
        "2024-06-01",
        12,
        "hora",
        EXTENT,
        data_dir=tmp_path,
        smoothing_sigma=0.0,
    )
    assert data.unit == "°C"
    assert np.isclose(np.nanmean(data.values), 26.85, atol=0.1)  # 300 K − 273.15


def test_load_olr_cache_hit_flips_sign(tmp_path):
    var = ERA5_VARIABLES["era5_olr"]
    target = _cache_path(var, "2024-06-01", "2024-06-01", 12, "hora", EXTENT, tmp_path)
    # saldo de onda longa no topo é negativo (saindo); OLR = −saldo
    _write_fake_cache(target, "avg_tnlwrf", np.full((5, 5), -240.0, dtype="float32"))
    data = load_era5_field(
        "era5_olr",
        "2024-06-01",
        "2024-06-01",
        12,
        "hora",
        EXTENT,
        data_dir=tmp_path,
        smoothing_sigma=0.0,
    )
    assert data.unit == "W/m²"
    assert np.isclose(np.nanmean(data.values), 240.0, atol=0.1)


# ── Fase 4: guarda de período longo ─────────────────────────────────────────


def test_era5_period_days_counts_inclusive():
    assert era5_period_days("2024-06-01", "2024-06-01") == 1
    assert era5_period_days("2024-06-01", "2024-06-30") == 30
    assert era5_period_days("2024-06-01", "2024-07-01") == 31


# ── Fase 3: série temporal num ponto ────────────────────────────────────────


def test_point_area_is_small_box_around_point():
    a = _point_area(-40.0, -5.0, pad=0.5)
    assert a == [-40.5, -5.5, -39.5, -4.5]


def test_series_cache_path_encodes_point_and_period(tmp_path):
    var = ERA5_VARIABLES["era5_t2m"]
    p = _series_cache_path(var, "2024-06-01", "2024-06-02", -40.0, -5.0, 0, tmp_path)
    assert p.name.startswith("era5series_era5_t2m_")
    assert "5.00S" in p.name and "40.00W" in p.name
    # ponto diferente ⇒ arquivo diferente
    p2 = _series_cache_path(var, "2024-06-01", "2024-06-02", -30.0, 5.0, 0, tmp_path)
    assert p2 != p


def _write_series_cache(path, var_name, series_values):
    """NetCDF sintético (uma pequena grade × N horas) no caminho de cache exato."""
    lats = np.array([-4.5, -5.0, -5.5], dtype="float32")
    lons = np.array([-40.5, -40.0, -39.5], dtype="float32")
    n = len(series_values)
    times = np.array([f"2024-06-01T{h:02d}:00" for h in range(n)], dtype="datetime64[ns]")
    data = np.zeros((n, lats.size, lons.size), dtype="float32")
    # ponto central (lat=-5.0, lon=-40.0) carrega a série; vizinhos ficam distintos
    data[:, 1, 1] = np.asarray(series_values, dtype="float32")
    ds = xr.Dataset(
        {var_name: (("valid_time", "latitude", "longitude"), data)},
        coords={"valid_time": times, "latitude": lats, "longitude": lons},
    )
    ds.to_netcdf(path)
    ds.close()


def test_load_series_cache_hit_samples_nearest_point(tmp_path):
    var = ERA5_VARIABLES["era5_t2m"]
    target = _series_cache_path(var, "2024-06-01", "2024-06-01", -40.0, -5.0, 0, tmp_path)
    kelvin = [300.0, 301.0, 302.0, 303.0]  # 4 horas
    _write_series_cache(target, "t2m", kelvin)

    series = load_era5_timeseries(
        "era5_t2m", "2024-06-01", "2024-06-01", -40.0, -5.0, data_dir=tmp_path
    )
    assert series.variable == "era5_t2m"
    assert series.unit == "°C"
    assert series.times.size == 4
    # nó mais próximo é o central; 300..303 K → 26.85..29.85 °C
    assert np.isclose(series.values[0], 26.85, atol=0.1)
    assert np.isclose(series.values[-1], 29.85, atol=0.1)
    assert np.isclose(series.grid_lat, -5.0) and np.isclose(series.grid_lon, -40.0)


# ── Fase 6: perfis de agregação por variável ────────────────────────────────


def test_every_variable_profile_is_defined_and_valid():
    """Todo perfil declarado existe, e todos os seus modos são reconhecidos."""
    for key, var in ERA5_VARIABLES.items():
        assert var.agg_profile in AGG_PROFILES, f"{key}: perfil {var.agg_profile!r} inexistente"
    for profile, modes in AGG_PROFILES.items():
        assert modes, f"perfil {profile!r} vazio"
        for mode in modes:
            assert mode in AGG_MODES, f"perfil {profile!r}: modo {mode!r} fora de AGG_MODES"


def test_profile_defaults_are_meteorologically_sane():
    """O default (1º modo) de cada variável-chave bate com o que o previsor quer."""
    assert default_agg(ERA5_VARIABLES["era5_precip"].agg_profile) == "soma"
    assert default_agg(ERA5_VARIABLES["era5_gust"].agg_profile) == "maxima"
    assert default_agg(ERA5_VARIABLES["era5_tmax"].agg_profile) == "max_diaria"
    assert default_agg(ERA5_VARIABLES["era5_tmin"].agg_profile) == "min_diaria"
    assert default_agg(ERA5_VARIABLES["era5_t2m"].agg_profile) == "media"


def test_daily_extremes_only_on_dedicated_tmax_tmin():
    """Redundância removida: t2m comum não oferece diárias; Tmáx/Tmín sim."""
    t2m_modes = profile_modes(ERA5_VARIABLES["era5_t2m"].agg_profile)
    assert "max_diaria" not in t2m_modes and "min_diaria" not in t2m_modes
    assert "max_diaria" in profile_modes(ERA5_VARIABLES["era5_tmax"].agg_profile)
    assert "min_diaria" in profile_modes(ERA5_VARIABLES["era5_tmin"].agg_profile)


def test_sum_only_offered_for_precipitation():
    """Somar só faz sentido em acumulação: nenhum perfil de estado oferece 'soma'."""
    for key, var in ERA5_VARIABLES.items():
        if var.agg_profile == "accumulation":
            assert "soma" in profile_modes(var.agg_profile)
        else:
            assert "soma" not in profile_modes(var.agg_profile), f"{key} não deveria somar"


def _write_precip_days(path, per_step_meters):
    """NetCDF de precipitação (tp em metros) com N passos horários consecutivos."""
    _write_time_cache(
        path,
        "tp",
        [
            (np.datetime64("2024-06-01T00:00") + np.timedelta64(h, "h")).astype(str)
            for h in range(len(per_step_meters))
        ],
        per_step_meters,
    )


def test_precip_daily_sum_modes_are_distinct(tmp_path):
    """soma (total) × soma_diaria (média diária) × max_soma_diaria (dia mais chuvoso)."""
    # dia 1: 0.001 m/h × 24 = 24 mm ; dia 2: 0.002 m/h × 24 = 48 mm
    per_step = [0.001] * 24 + [0.002] * 24

    def _load(agg):
        tgt = _cache_path(
            ERA5_VARIABLES["era5_precip"], "2024-06-01", "2024-06-02", 0, agg, EXTENT, tmp_path
        )
        _write_precip_days(tgt, per_step)
        return load_era5_field(
            "era5_precip",
            "2024-06-01",
            "2024-06-02",
            0,
            agg,
            EXTENT,
            data_dir=tmp_path,
            smoothing_sigma=0.0,
        )

    total = _load("soma")
    diaria = _load("soma_diaria")
    maxdia = _load("max_soma_diaria")
    assert np.isclose(np.nanmean(total.values), 72.0, atol=0.1)  # 24 + 48
    assert np.isclose(np.nanmean(diaria.values), 36.0, atol=0.1)  # (24 + 48) / 2
    assert np.isclose(np.nanmean(maxdia.values), 48.0, atol=0.1)  # dia mais chuvoso


def test_precip_unit_follows_aggregation(tmp_path):
    """A unidade da chuva acompanha o modo: mm / mm/dia / mm/h (não é sempre 'mm')."""
    per_step = [0.001] * 24 + [0.002] * 24

    def _unit(agg, ds="2024-06-01", de="2024-06-02", hour=0):
        tgt = _cache_path(ERA5_VARIABLES["era5_precip"], ds, de, hour, agg, EXTENT, tmp_path)
        _write_precip_days(tgt, per_step if agg != "hora" else [0.001])
        return load_era5_field(
            "era5_precip", ds, de, hour, agg, EXTENT, data_dir=tmp_path, smoothing_sigma=0.0
        ).unit

    assert _unit("soma") == "mm"
    assert _unit("soma_diaria") == "mm/dia"
    assert _unit("hora", ds="2024-06-01", de="2024-06-01") == "mm/h"


def test_engine_accepts_mode_outside_ui_profile(tmp_path):
    """Perfis filtram só a UI: o engine ainda carrega t2m+max_diaria (projeto antigo)."""
    assert "max_diaria" not in profile_modes(ERA5_VARIABLES["era5_t2m"].agg_profile)
    times = ["2024-06-01T00:00", "2024-06-01T12:00", "2024-06-02T00:00", "2024-06-02T12:00"]
    kelvin = [283.15, 288.15, 293.15, 298.15]  # °C: 10, 15, 20, 25
    tgt = _cache_path(
        ERA5_VARIABLES["era5_t2m"], "2024-06-01", "2024-06-02", 0, "max_diaria", EXTENT, tmp_path
    )
    _write_time_cache(tgt, "t2m", times, kelvin)
    data = load_era5_field(
        "era5_t2m",
        "2024-06-01",
        "2024-06-02",
        0,
        "max_diaria",
        EXTENT,
        data_dir=tmp_path,
        smoothing_sigma=0.0,
    )
    # dia1 max(10,15)=15, dia2 max(20,25)=25 → média = 20
    assert np.isclose(np.nanmean(data.values), 20.0, atol=0.05)


# ── Fase 8: índices de evento (chuva + temperatura) ─────────────────────────


def _days(n: int, start: str = "2024-06-01") -> list[str]:
    """N carimbos diários ao meio-dia a partir de ``start`` (ISO)."""
    d0 = np.datetime64(f"{start}T12:00")
    return [(d0 + np.timedelta64(i, "D")).astype(str) for i in range(n)]


def test_max_consecutive_counts_longest_run():
    # eixo 0 = tempo; 2 colunas com padrões distintos
    mask = np.array([[1, 0], [1, 1], [0, 1], [1, 1], [1, 0]], dtype=bool)
    out = _max_consecutive(mask, axis=0)
    assert out.tolist() == [2, 3]  # col0: 1,1 depois 1,1 → 2; col1: 1,1,1 → 3


def _load_precip_index(tmp_path, agg, daily_mm):
    """Grava um cache diário de chuva (mm→m) e carrega o índice ``agg``."""
    var = ERA5_VARIABLES["era5_precip"]
    n = len(daily_mm)
    de = _days(n)[-1][:10]
    tgt = _cache_path(var, "2024-06-01", de, 0, agg, EXTENT, tmp_path)
    _write_time_cache(tgt, "tp", _days(n), [v / 1000.0 for v in daily_mm])  # mm → m
    return load_era5_field(
        "era5_precip", "2024-06-01", de, 0, agg, EXTENT, data_dir=tmp_path, smoothing_sigma=0.0
    )


# chuva diária (mm) usada nos índices de precipitação
_DAILY_MM = [5, 0, 0, 0, 2, 0, 0, 3, 3, 3]


def test_index_cdd_is_longest_dry_spell(tmp_path):
    data = _load_precip_index(tmp_path, "idx_cdd", _DAILY_MM)
    assert data.variable == "era5_idx_cdd"
    assert data.unit == "dias"
    assert np.isclose(np.nanmax(data.values), 3.0)  # dias 2–4 secos


def test_index_cwd_is_longest_wet_spell(tmp_path):
    data = _load_precip_index(tmp_path, "idx_cwd", _DAILY_MM)
    assert data.variable == "era5_idx_cwd"
    assert np.isclose(np.nanmax(data.values), 3.0)  # dias 8–10 úmidos


def test_index_wetdays_counts_days_ge_1mm(tmp_path):
    data = _load_precip_index(tmp_path, "idx_wetdays", _DAILY_MM)
    assert data.unit == "dias"
    assert np.isclose(np.nanmax(data.values), 5.0)  # d1,d5,d8,d9,d10


def test_index_rx5day_is_max_5day_sum(tmp_path):
    data = _load_precip_index(tmp_path, "idx_rx5day", _DAILY_MM)
    assert data.variable == "era5_idx_rx5day"
    assert data.unit == "mm"
    assert np.isclose(np.nanmax(data.values), 9.0)  # janela d6..d10 = 0+0+3+3+3


def _load_temp_index(tmp_path, key, agg, daily_c, thresh=0.0):
    var = ERA5_VARIABLES[key]
    n = len(daily_c)
    de = _days(n)[-1][:10]
    tgt = _cache_path(var, "2024-06-01", de, 0, agg, EXTENT, tmp_path)
    nc = var.nc_names[0]
    _write_time_cache(tgt, nc, _days(n), [c + 273.15 for c in daily_c])  # °C → K
    return load_era5_field(
        key, "2024-06-01", de, 0, agg, EXTENT, data_dir=tmp_path, smoothing_sigma=0.0, thresh=thresh
    )


def test_index_hotdays_counts_days_above_user_threshold(tmp_path):
    data = _load_temp_index(
        tmp_path, "era5_tmax", "idx_hotdays", [28, 31, 33, 29, 36, 34], thresh=30
    )
    assert data.variable == "era5_idx_hotdays"
    assert data.unit == "dias"
    assert np.isclose(np.nanmax(data.values), 4.0)  # 31,33,36,34 > 30


def test_index_warmspell_is_longest_hot_run(tmp_path):
    data = _load_temp_index(
        tmp_path, "era5_tmax", "idx_warmspell", [28, 31, 33, 29, 36, 34], thresh=30
    )
    assert np.isclose(np.nanmax(data.values), 2.0)  # runs 31,33 e 36,34


def test_index_tropicalnights_counts_tmin_above_20(tmp_path):
    data = _load_temp_index(tmp_path, "era5_tmin", "idx_tropicalnights", [22, 19, 21, 25, 18])
    assert data.variable == "era5_idx_tropicalnights"
    assert np.isclose(np.nanmax(data.values), 3.0)  # 22,21,25 > 20


def test_index_threshold_changes_result(tmp_path):
    # o MESMO cache + limiares distintos → contagens distintas (sem re-baixar)
    lo = _load_temp_index(tmp_path, "era5_tmax", "idx_hotdays", [28, 31, 33, 29, 36, 34], thresh=30)
    hi = _load_temp_index(tmp_path, "era5_tmax", "idx_hotdays", [28, 31, 33, 29, 36, 34], thresh=35)
    assert np.isclose(np.nanmax(lo.values), 4.0)
    assert np.isclose(np.nanmax(hi.values), 1.0)  # só 36 > 35


def test_index_extra_preserves_source_and_thresh(tmp_path):
    data = _load_temp_index(tmp_path, "era5_tmax", "idx_hotdays", [28, 36], thresh=32)
    assert data.extra["variable"] == "era5_tmax"  # fonte, p/ round-trip
    assert data.extra["agg"] == "idx_hotdays"
    assert np.isclose(data.extra["thresh"], 32.0)


def test_index_render_keys_exist_and_have_no_conversion():
    from cartomet_br.data.ecmwf import VARIABLE_REGISTRY

    for agg in AGG_INDEX_MODES:
        key = INDEX_RENDER_KEY[agg]
        assert key in VARIABLE_REGISTRY, f"faltou render key: {key}"
        info = VARIABLE_REGISTRY[key]
        assert info.get("conversion") is None  # valor já em dias/mm
        assert info.get("category") == "index"
        assert info["unit_display"] in ("dias", "mm")


def test_index_modes_are_in_the_right_profiles():
    precip_modes = profile_modes(ERA5_VARIABLES["era5_precip"].agg_profile)
    for agg in ("idx_cdd", "idx_cwd", "idx_wetdays", "idx_rx5day"):
        assert agg in precip_modes
    assert "idx_hotdays" in profile_modes(ERA5_VARIABLES["era5_tmax"].agg_profile)
    assert "idx_tropicalnights" in profile_modes(ERA5_VARIABLES["era5_tmin"].agg_profile)
    # defaults NÃO mudaram (índices entram depois dos modos base)
    assert default_agg(ERA5_VARIABLES["era5_precip"].agg_profile) == "soma"
    assert default_agg(ERA5_VARIABLES["era5_tmax"].agg_profile) == "max_diaria"


def test_index_only_offered_where_profile_allows(tmp_path):
    # o serviço rejeita índice de chuva pedido sobre temperatura (defesa em profundidade)
    day = _safe_day()
    with pytest.raises(ValidationError):
        _service(tmp_path).validate_era5_request("era5_tmax", day, day, 0, "idx_cdd")
