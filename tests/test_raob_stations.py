"""Testes para cartomet_br.data.raob_stations — snap da Sonda Vertical (sem rede)."""

import pytest

from cartomet_br.data.raob_stations import RAOB_STATIONS, nearest_raob


class TestRaobStationsTable:
    def test_table_not_empty(self):
        assert len(RAOB_STATIONS) > 0

    def test_every_station_has_required_fields(self):
        for st in RAOB_STATIONS:
            assert set(st.keys()) >= {"wmo", "name", "lat", "lon"}
            assert isinstance(st["wmo"], str) and len(st["wmo"]) == 5
            assert -90.0 <= st["lat"] <= 90.0
            assert -180.0 <= st["lon"] <= 180.0

    def test_wmo_codes_are_unique(self):
        codes = [st["wmo"] for st in RAOB_STATIONS]
        assert len(codes) == len(set(codes))


class TestNearestRaob:
    def test_snaps_to_belem_when_near_amazon_mouth(self):
        # Clique perto da foz do Amazonas → Belém (82193)
        st = nearest_raob(lon=-48.0, lat=-1.0)
        assert st is not None
        assert st["wmo"] == "82193"
        assert st["name"] == "Belém"

    def test_snaps_to_natal_in_northeast_tip(self):
        st = nearest_raob(lon=-35.2, lat=-5.8)
        assert st is not None
        assert st["wmo"] == "82599"

    def test_snaps_to_porto_alegre_in_far_south(self):
        st = nearest_raob(lon=-51.2, lat=-30.0)
        assert st is not None
        assert st["wmo"] == "83971"

    def test_returns_distance_in_degrees(self):
        st = nearest_raob(lon=-48.48, lat=-1.38)  # exatamente sobre Belém
        assert st is not None
        assert "distance_deg" in st
        assert st["distance_deg"] == pytest.approx(0.0, abs=1e-9)

    def test_result_is_a_copy_not_table_reference(self):
        st = nearest_raob(lon=-48.48, lat=-1.38)
        assert st is not None
        st["name"] = "MUTATED"
        # A tabela original permanece intacta
        belem = next(s for s in RAOB_STATIONS if s["wmo"] == "82193")
        assert belem["name"] == "Belém"
