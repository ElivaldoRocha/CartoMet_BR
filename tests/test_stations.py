"""Testes para cartomet_br.data.stations — normalização de observações (sem rede)."""

import math

import numpy as np
import pytest

from cartomet_br.data.stations import (
    STATION_COLUMNS,
    _filter_extent,
    _metar_record_from_json,
    _parse_dms_coord,
    empty_stations_df,
    normalize_station_records,
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
        records = [{"longitude": -50.0, "latitude": -10.0, "air_temperature": 25.0,
                    "station_id": "Z"}]
        df = normalize_station_records(records)
        assert list(df.columns) == STATION_COLUMNS

    def test_numeric_coercion(self):
        records = [{"station_id": "X", "latitude": "-10.0", "longitude": "-50.0",
                    "air_temperature": "25.5"}]
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
        assert _parse_dms_coord("047-54-30W") == pytest.approx(-(47 + 54/60 + 30/3600))

    def test_empty(self):
        assert _parse_dms_coord("") is None


# ═══════════════════════════════════════════════════════════════════════════════
#  _metar_record_from_json (AWC)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetarRecord:
    def test_basic_mapping(self):
        obj = {
            "icaoId": "SBBR", "lat": -15.87, "lon": -47.92,
            "temp": 25.0, "dewp": 18.0, "mslp": 1015.0,
            "wdir": 90, "wspd": 10,  # 10 kt de leste
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
