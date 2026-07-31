"""Testes para cartomet_br.data.stations — normalização de observações (sem rede).

Os testes de fetch simulam a rede com monkeypatch em `requests.get` (os imports
de requests nos fetchers são lazy, então o patch no módulo global funciona).
"""

import json
import math
import os
import time
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

import cartomet_br.data.stations as stations
from cartomet_br.data.stations import (
    DEFAULT_OBS_DENSITY,
    OBS_DENSITY_FACTORS,
    OBS_MODE_ANALYSIS,
    OBS_MODE_LATEST,
    STATION_COLUMNS,
    _cache_path,
    _filter_extent,
    _metar_obs_time,
    _metar_record_from_json,
    _parse_dms_coord,
    _synop_text_has_reports,
    empty_stations_df,
    fetch_metar,
    fetch_synop,
    normalize_station_records,
    synop_slot,
    thinning_radius,
    wind_components,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  empty_stations_df / colunas canônicas
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmptyDataFrame:
    def test_has_canonical_columns(self):
        df = empty_stations_df()
        assert list(df.columns) == STATION_COLUMNS

    def test_is_empty(self):
        assert len(empty_stations_df()) == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  wind_components
# ═══════════════════════════════════════════════════════════════════════════════


class TestWindComponents:
    def test_north_wind(self):
        # Vento de Norte (360/0°): sopra para o Sul → v negativo, u ~0
        u, v = wind_components(10.0, 0.0)
        assert u == pytest.approx(0.0, abs=1e-9)
        assert v == pytest.approx(-10.0)

    def test_east_wind(self):
        # Vento de Leste (90°): sopra para Oeste → u negativo
        u, v = wind_components(10.0, 90.0)
        assert u == pytest.approx(-10.0)
        assert v == pytest.approx(0.0, abs=1e-9)

    def test_missing_returns_nan(self):
        u, v = wind_components(None, 90.0)
        assert math.isnan(u) and math.isnan(v)
        u, v = wind_components(5.0, None)
        assert math.isnan(u) and math.isnan(v)

    def test_invalid_string(self):
        u, v = wind_components("VRB", 5.0)
        assert math.isnan(u) and math.isnan(v)


# ═══════════════════════════════════════════════════════════════════════════════
#  normalize_station_records
# ═══════════════════════════════════════════════════════════════════════════════


class TestNormalize:
    def test_empty_list(self):
        df = normalize_station_records([])
        assert list(df.columns) == STATION_COLUMNS
        assert df.empty

    def test_fills_missing_columns(self):
        records = [{"station_id": "SBBR", "latitude": -15.8, "longitude": -47.9}]
        df = normalize_station_records(records)
        assert list(df.columns) == STATION_COLUMNS
        assert len(df) == 1
        assert df.loc[0, "station_id"] == "SBBR"
        assert np.isnan(df.loc[0, "air_temperature"])

    def test_drops_rows_without_coords(self):
        records = [
            {"station_id": "A", "latitude": -10.0, "longitude": -50.0},
            {"station_id": "B", "latitude": None, "longitude": -50.0},
            {"station_id": "C", "longitude": -50.0},  # sem latitude
        ]
        df = normalize_station_records(records)
        assert len(df) == 1
        assert df.loc[0, "station_id"] == "A"

    def test_column_order_preserved(self):
        records = [
            {"longitude": -50.0, "latitude": -10.0, "air_temperature": 25.0, "station_id": "Z"}
        ]
        df = normalize_station_records(records)
        assert list(df.columns) == STATION_COLUMNS

    def test_numeric_coercion(self):
        records = [
            {
                "station_id": "X",
                "latitude": "-10.0",
                "longitude": "-50.0",
                "air_temperature": "25.5",
            }
        ]
        df = normalize_station_records(records)
        assert df.loc[0, "air_temperature"] == pytest.approx(25.5)


# ═══════════════════════════════════════════════════════════════════════════════
#  _filter_extent
# ═══════════════════════════════════════════════════════════════════════════════


class TestFilterExtent:
    def test_keeps_inside_drops_outside(self):
        records = [
            {"station_id": "in", "latitude": -10.0, "longitude": -50.0},
            {"station_id": "out", "latitude": 40.0, "longitude": 10.0},
        ]
        df = normalize_station_records(records)
        df = _filter_extent(df, [-75.0, -35.0, -30.0, 6.0])
        assert list(df["station_id"]) == ["in"]

    def test_empty_df_safe(self):
        df = _filter_extent(empty_stations_df(), [-75, -35, -30, 6])
        assert df.empty


# ═══════════════════════════════════════════════════════════════════════════════
#  _parse_dms_coord (tabela WMO nsd_bbsss)
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseDMS:
    def test_north(self):
        assert _parse_dms_coord("15-30N") == pytest.approx(15.5)

    def test_south_negative(self):
        assert _parse_dms_coord("23-30S") == pytest.approx(-23.5)

    def test_west_negative(self):
        assert _parse_dms_coord("047-54-30W") == pytest.approx(-(47 + 54 / 60 + 30 / 3600))

    def test_empty(self):
        assert _parse_dms_coord("") is None


# ═══════════════════════════════════════════════════════════════════════════════
#  _metar_record_from_json (AWC)
# ═══════════════════════════════════════════════════════════════════════════════


class TestMetarRecord:
    def test_basic_mapping(self):
        obj = {
            "icaoId": "SBBR",
            "lat": -15.87,
            "lon": -47.92,
            "temp": 25.0,
            "dewp": 18.0,
            "mslp": 1015.0,
            "wdir": 90,
            "wspd": 10,  # 10 kt de leste
            "clouds": [{"cover": "SCT", "base": 3000}, {"cover": "BKN", "base": 8000}],
            "wxString": "RA",
        }
        rec = _metar_record_from_json(obj)
        assert rec["station_id"] == "SBBR"
        assert rec["air_temperature"] == 25.0
        assert rec["cloud_coverage"] == 6  # máx(SCT=4, BKN=6)
        # vento de leste → u negativo
        assert rec["eastward_wind"] < 0

    def test_variable_wind_no_components(self):
        obj = {"icaoId": "X", "lat": -10, "lon": -50, "wdir": "VRB", "wspd": 5}
        rec = _metar_record_from_json(obj)
        assert math.isnan(rec["eastward_wind"])

    def test_altim_fallback_for_pressure(self):
        obj = {"icaoId": "X", "lat": -10, "lon": -50, "altim": 1013.2}
        rec = _metar_record_from_json(obj)
        assert rec["air_pressure_at_sea_level"] == 1013.2

    def test_produces_normalizable_record(self):
        obj = {"icaoId": "X", "lat": -10, "lon": -50, "temp": 20}
        df = normalize_station_records([_metar_record_from_json(obj)])
        assert len(df) == 1
        assert list(df.columns) == STATION_COLUMNS


# ═══════════════════════════════════════════════════════════════════════════════
#  thinning_radius (densidade ajustável do overlay)
# ═══════════════════════════════════════════════════════════════════════════════


class TestThinningRadius:
    def test_higher_factor_smaller_radius(self):
        # Mais densidade (fator maior) → raio menor → mais estações.
        assert thinning_radius(40.0, 4.0) < thinning_radius(40.0, 1.0)

    def test_monotonic_across_levels(self):
        # Baixa > Média > Alta > Máxima em raio (densidade crescente).
        radii = [
            thinning_radius(40.0, OBS_DENSITY_FACTORS[name])
            for name in ("Baixa", "Média", "Alta", "Máxima")
        ]
        assert radii == sorted(radii, reverse=True)
        assert len(set(radii)) == len(radii)  # todos distintos

    def test_scales_with_width(self):
        # Domínio mais largo → raio maior (mesmo fator).
        assert thinning_radius(80.0, 2.0) > thinning_radius(20.0, 2.0)

    def test_floor_enforced(self):
        # Zoom forte + fator alto não derruba o raio abaixo do piso de 0,10°.
        assert thinning_radius(0.5, 4.0) == pytest.approx(0.10)

    def test_default_doubles_density_vs_media(self):
        # O padrão "Alta" tem metade do raio de "Média" (≈ 2× mais estações).
        media = thinning_radius(40.0, OBS_DENSITY_FACTORS["Média"])
        alta = thinning_radius(40.0, OBS_DENSITY_FACTORS[DEFAULT_OBS_DENSITY])
        assert alta == pytest.approx(media / 2.0)

    def test_negative_width_uses_magnitude(self):
        # extent invertido (largura negativa) não quebra: usa o módulo.
        assert thinning_radius(-40.0, 2.0) == thinning_radius(40.0, 2.0)


# ═══════════════════════════════════════════════════════════════════════════════
#  synop_slot (piso do horário sinótico — único ponto do arredondamento)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSynopSlot:
    def test_floor_6h(self):
        dt = datetime(2026, 7, 31, 17, 45, tzinfo=UTC)
        assert synop_slot(dt, 6) == datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

    def test_floor_3h(self):
        dt = datetime(2026, 7, 31, 17, 45, tzinfo=UTC)
        assert synop_slot(dt, 3) == datetime(2026, 7, 31, 15, 0, tzinfo=UTC)

    def test_exact_hour_is_identity(self):
        dt = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
        assert synop_slot(dt, 6) == dt

    def test_early_morning_floors_to_midnight(self):
        dt = datetime(2026, 7, 31, 1, 10, tzinfo=UTC)
        assert synop_slot(dt, 6) == datetime(2026, 7, 31, 0, 0, tzinfo=UTC)
        assert synop_slot(dt, 3) == datetime(2026, 7, 31, 0, 0, tzinfo=UTC)


# ═══════════════════════════════════════════════════════════════════════════════
#  Infra dos testes de fetch (rede simulada)
# ═══════════════════════════════════════════════════════════════════════════════

_EXTENT = [-60.0, -30.0, -40.0, -10.0]


def _awc_obj(icao: str, obs_epoch: float | None = None, **extra) -> dict:
    obj = {"icaoId": icao, "lat": -20.0, "lon": -50.0, "temp": 25.0}
    if obs_epoch is not None:
        obj["obsTime"] = obs_epoch
    obj.update(extra)
    return obj


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _no_network(*_a, **_k):
    raise AssertionError("este caminho não deveria tocar a rede")


def _age_file(path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


# ═══════════════════════════════════════════════════════════════════════════════
#  fetch_metar — TTL do modo "mais recente", params de análise, dedupe, attrs
# ═══════════════════════════════════════════════════════════════════════════════


class TestMetarLatestTTL:
    def _seed_cache(self, data_dir, when, payload):
        cache = _cache_path(data_dir, "metar", _EXTENT, when)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload), encoding="utf-8")
        return cache

    def test_fresh_latest_cache_served_without_network(self, tmp_path, monkeypatch):
        self._seed_cache(tmp_path, None, [_awc_obj("SBBR", 1_753_900_000)])
        monkeypatch.setattr("requests.get", _no_network)
        df = fetch_metar(_EXTENT, when=None, data_dir=tmp_path)
        assert len(df) == 1
        assert df.attrs["obs_mode"] == OBS_MODE_LATEST

    def test_aged_latest_cache_goes_to_network(self, tmp_path, monkeypatch):
        cache = self._seed_cache(tmp_path, None, [_awc_obj("VELHO", 1_000)])
        _age_file(cache, 700)  # > _LATEST_TTL_SECONDS (600)
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp([_awc_obj("NOVO", 2_000)]))
        df = fetch_metar(_EXTENT, when=None, data_dir=tmp_path)
        assert list(df["station_id"]) == ["NOVO"]

    def test_aged_analysis_cache_still_served(self, tmp_path, monkeypatch):
        when = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
        cache = self._seed_cache(tmp_path, when, [_awc_obj("SBGR", 1_753_900_000)])
        _age_file(cache, 700)  # análise é congelada: idade não importa
        monkeypatch.setattr("requests.get", _no_network)
        df = fetch_metar(_EXTENT, when=when, data_dir=tmp_path)
        assert list(df["station_id"]) == ["SBGR"]
        assert df.attrs["obs_mode"] == OBS_MODE_ANALYSIS

    def test_network_failure_serves_stale_latest_cache(self, tmp_path, monkeypatch):
        cache = self._seed_cache(tmp_path, None, [_awc_obj("STALE", 1_753_900_000)])
        _age_file(cache, 700)
        monkeypatch.setattr("requests.get", _raise_get)
        df = fetch_metar(_EXTENT, when=None, data_dir=tmp_path)
        assert list(df["station_id"]) == ["STALE"]
        assert df.attrs["obs_time_utc"] == datetime.fromtimestamp(1_753_900_000, tz=UTC)


def _raise_get(*_a, **_k):
    raise ConnectionError("rede fora")


class TestMetarAnalysisParams:
    def test_analysis_pins_date_and_hours(self, monkeypatch):
        captured: dict = {}

        def fake_get(url, params=None, timeout=None):
            captured.update(params or {})
            return _FakeResp([_awc_obj("SBBR", 1_753_900_000)])

        monkeypatch.setattr("requests.get", fake_get)
        when = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
        fetch_metar(_EXTENT, when=when)
        # `date` = fim da janela (+10 min de tolerância); `hours` = alcance p/ trás
        assert captured["date"] == "2026-07-30T12:10:00Z"
        assert captured["hours"] == pytest.approx(2.5)

    def test_latest_has_no_time_params(self, monkeypatch):
        captured: dict = {}

        def fake_get(url, params=None, timeout=None):
            captured.update(params or {})
            return _FakeResp([_awc_obj("SBBR", 1_753_900_000)])

        monkeypatch.setattr("requests.get", fake_get)
        fetch_metar(_EXTENT, when=None)
        assert "date" not in captured
        assert "hours" not in captured


class TestMetarDedupeAttrs:
    def test_dedupes_by_station_keeping_newest(self, monkeypatch):
        payload = [_awc_obj("SBBR", 1_000, temp=20.0), _awc_obj("SBBR", 2_000, temp=30.0)]
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp(payload))
        df = fetch_metar(_EXTENT, when=None)
        assert len(df) == 1
        assert df.loc[0, "air_temperature"] == pytest.approx(30.0)
        assert df.attrs["obs_time_utc"] == datetime.fromtimestamp(2_000, tz=UTC)

    def test_empty_response_still_carries_attrs(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp([]))
        df = fetch_metar(_EXTENT, when=None)
        assert df.empty
        assert df.attrs["obs_mode"] == OBS_MODE_LATEST
        assert df.attrs["obs_time_utc"] is None

    def test_obs_time_falls_back_to_report_time(self):
        payload = [{"icaoId": "X", "reportTime": "2026-07-31 12:00:00"}]
        assert _metar_obs_time(payload) == datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


# ═══════════════════════════════════════════════════════════════════════════════
#  fetch_synop — fallback de slot no modo "mais recente" e cache sem vazios
# ═══════════════════════════════════════════════════════════════════════════════

_OGIMET_HEADER_ONLY = "# consulta getsynop\n# sem dados\n"
_OGIMET_WITH_REPORT = "# consulta\n83378,2026,07,31,12,00,AAXX 31121 83378 32598 61804=\n"


class TestSynopFallback:
    @pytest.fixture(autouse=True)
    def _coords(self, monkeypatch):
        monkeypatch.setattr(stations, "_load_wmo_coords", lambda *a, **k: {"83378": (-15.8, -47.9)})

    def _fake_df(self):
        return normalize_station_records(
            [{"station_id": "83378", "latitude": -15.8, "longitude": -47.9}]
        )

    def test_falls_back_one_slot_when_base_empty(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp(_OGIMET_WITH_REPORT))
        calls: list = []

        def fake_decode(text, in_extent, coords, extent):
            calls.append(text)
            return empty_stations_df() if len(calls) == 1 else self._fake_df()

        monkeypatch.setattr(stations, "_decode_synop_text", fake_decode)
        base = synop_slot(datetime.now(UTC), 3)
        df = fetch_synop(_EXTENT, when=None)
        assert len(df) == 1
        assert df.attrs["obs_fallback"] is True
        assert df.attrs["obs_time_utc"] in (base - timedelta(hours=3), base)  # tolera virada
        assert df.attrs["obs_mode"] == OBS_MODE_LATEST

    def test_gives_up_after_three_slots(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp(_OGIMET_WITH_REPORT))
        calls: list = []

        def fake_decode(text, in_extent, coords, extent):
            calls.append(text)
            return empty_stations_df()

        monkeypatch.setattr(stations, "_decode_synop_text", fake_decode)
        df = fetch_synop(_EXTENT, when=None)
        assert df.empty
        assert len(calls) == 3  # base, −3h, −6h — e para
        assert df.attrs["obs_time_utc"] is None

    def test_analysis_mode_tries_single_6h_slot(self, monkeypatch):
        captured: list[dict] = []

        def fake_get(url, params=None, timeout=None):
            captured.append(dict(params or {}))
            return _FakeResp(_OGIMET_WITH_REPORT)

        monkeypatch.setattr("requests.get", fake_get)
        monkeypatch.setattr(stations, "_decode_synop_text", lambda *a, **k: empty_stations_df())
        when = datetime(2026, 7, 31, 14, 0, tzinfo=UTC)
        df = fetch_synop(_EXTENT, when=when)
        assert df.empty
        assert len(captured) == 1  # análise não recua de slot
        assert captured[0]["begin"] == "202607311200"  # piso 6 h
        assert df.attrs["obs_mode"] == OBS_MODE_ANALYSIS


class TestSynopNoEmptyCache:
    @pytest.fixture(autouse=True)
    def _seams(self, monkeypatch):
        monkeypatch.setattr(stations, "_load_wmo_coords", lambda *a, **k: {"83378": (-15.8, -47.9)})
        monkeypatch.setattr(stations, "_decode_synop_text", lambda *a, **k: empty_stations_df())

    def test_header_only_response_not_cached(self, tmp_path, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp(_OGIMET_HEADER_ONLY))
        fetch_synop(_EXTENT, when=datetime(2026, 7, 31, 12, 0, tzinfo=UTC), data_dir=tmp_path)
        assert not list(tmp_path.glob("synop_raw_*.txt"))

    def test_response_with_reports_is_cached(self, tmp_path, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp(_OGIMET_WITH_REPORT))
        fetch_synop(_EXTENT, when=datetime(2026, 7, 31, 12, 0, tzinfo=UTC), data_dir=tmp_path)
        assert (tmp_path / "synop_raw_202607311200.txt").exists()

    def test_has_reports_predicate(self):
        assert _synop_text_has_reports(_OGIMET_WITH_REPORT)
        assert not _synop_text_has_reports(_OGIMET_HEADER_ONLY)
        assert not _synop_text_has_reports("")


# ═══════════════════════════════════════════════════════════════════════════════
#  _cache_path — chaves distintas por modo
# ═══════════════════════════════════════════════════════════════════════════════


class TestCachePath:
    def test_latest_and_analysis_keys_differ(self, tmp_path):
        latest = _cache_path(tmp_path, "metar", _EXTENT, None)
        frozen = _cache_path(tmp_path, "metar", _EXTENT, datetime(2026, 7, 30, 12, tzinfo=UTC))
        assert latest != frozen
        assert latest.name.endswith("_latest.json")
        assert "2026073012" in frozen.name

    def test_no_data_dir_no_cache(self):
        assert _cache_path(None, "metar", _EXTENT, None) is None
