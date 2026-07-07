"""Testes do cliente Wyoming (interface WSGI nova) — ``cartomet_br/data/wyoming.py``.

A fixture ``tests/fixtures/wyoming_82193_2026070512_fm35.csv`` é uma resposta
REAL do servidor (Belém 82193, 05/07/2026 12Z, src=FM35), capturada em
06/07/2026 — material de regressão do formato.
"""

import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from cartomet_br.data.wyoming import (
    WYOMING_SOURCES,
    WyomingNoDataError,
    fetch_wyoming_sounding,
    parse_wyoming_csv,
)

FIXTURE = Path(__file__).parent / "fixtures" / "wyoming_82193_2026070512_fm35.csv"

# Amostra do formato BUFR (colunas extras de deriva do balão: time/lon/lat)
BUFR_SAMPLE = (
    "time,longitude,latitude,pressure_hPa,geopotential height_m,temperature_C,"
    "dew point temperature_C,ice point temperature_C,relative humidity_%,"
    "humidity wrt ice_%,mixing ratio_g/kg,wind direction_degree,wind speed_m/s\n"
    "2026-07-05 11:17:54,-48.4825,-1.3891,1012.5,   14, 26.6, 24.1, 24.1, 86, 86,18.94, 90, 4.1\n"
    "2026-07-05 11:18:19,-48.4837,-1.3889,1000.0,  124, 25.2, 23.5, 23.5, 90, 90,18.51, 88, 5.9\n"
)


class TestParseWyomingCsv:
    def test_fixture_fm35_completa(self):
        df = parse_wyoming_csv(FIXTURE.read_text())

        # Contrato do sounding_engine (colunas que o worker consome)
        for col in ("pressure", "temperature", "dewpoint", "u_wind", "v_wind"):
            assert col in df.columns
        assert len(df) == 110  # todos os níveis têm pressão

        # Primeiro nível: 1013.0 hPa, 26.6 °C, Td 24.1 °C, 90°/4.1 m/s
        row = df.iloc[0]
        assert row["pressure"] == pytest.approx(1013.0)
        assert row["height"] == pytest.approx(16.0)
        assert row["temperature"] == pytest.approx(26.6)
        assert row["dewpoint"] == pytest.approx(24.1)

        # Convenção meteorológica: vento DE leste (90°) → u negativo, v ~ 0
        assert row["u_wind"] == pytest.approx(-4.1, abs=1e-6)
        assert row["v_wind"] == pytest.approx(0.0, abs=1e-6)

    def test_vento_ausente_vira_nan_sem_derrubar_o_nivel(self):
        df = parse_wyoming_csv(FIXTURE.read_text())
        # Último nível (18.9 hPa) veio sem direção/velocidade (campos em branco)
        row = df.iloc[-1]
        assert row["pressure"] == pytest.approx(18.9)
        assert np.isnan(row["u_wind"])
        assert np.isnan(row["v_wind"])
        # ...mas termodinâmica do nível permanece utilizável
        assert row["temperature"] == pytest.approx(-50.5)

    def test_formato_bufr_com_colunas_de_deriva(self):
        df = parse_wyoming_csv(BUFR_SAMPLE)
        assert len(df) == 2
        assert "time" not in df.columns and "longitude" not in df.columns
        assert df.iloc[0]["pressure"] == pytest.approx(1012.5)
        assert df.iloc[1]["u_wind"] == pytest.approx(-5.9 * np.sin(np.radians(88.0)))

    def test_csv_so_com_cabecalho_e_sem_dados(self):
        header = FIXTURE.read_text().splitlines()[0]
        with pytest.raises(WyomingNoDataError):
            parse_wyoming_csv(header + "\n")

    def test_colunas_inesperadas_erro_claro(self):
        with pytest.raises(ValueError, match="colunas ausentes"):
            parse_wyoming_csv("foo,bar\n1,2\n")


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


class TestFetchWyomingSounding:
    TIME = datetime(2026, 7, 5, 12)

    def test_fallback_fm35_404_para_bufr(self, monkeypatch):
        import requests

        chamadas = []

        def fake_get(url, params=None, timeout=None):
            chamadas.append(params["src"])
            if params["src"] == "FM35":
                return _FakeResponse(404, "Unable to retrieve the data for 82193.")
            return _FakeResponse(200, BUFR_SAMPLE)

        monkeypatch.setattr(requests, "get", fake_get)
        df = fetch_wyoming_sounding(self.TIME, 82193)
        assert chamadas == list(WYOMING_SOURCES)
        assert len(df) == 2

    def test_sem_dados_em_todas_as_fontes_vira_no_data(self, monkeypatch):
        """Status real medido em 06/2026: FM35 responde 404 e BUFR 400 —
        ambos com o corpo 'Unable to retrieve the data...'."""
        import requests

        def fake_get(url, params=None, timeout=None):
            code = 404 if params["src"] == "FM35" else 400
            return _FakeResponse(
                code, "Unable to retrieve the data for 82193 at 2026-07-05 03:00:00."
            )

        monkeypatch.setattr(requests, "get", fake_get)
        with pytest.raises(WyomingNoDataError, match="Unable to retrieve"):
            fetch_wyoming_sounding(self.TIME, "82193")

    def test_400_de_request_malformado_propaga(self, monkeypatch):
        """400 com corpo DIFERENTE de 'Unable to retrieve' é bug de request,
        não 'sem dados' — deve estourar como HTTPError."""
        import requests

        monkeypatch.setattr(
            requests,
            "get",
            lambda url, params=None, timeout=None: _FakeResponse(
                400, "'datetime' was incorrectly specified, value is '...'"
            ),
        )
        with pytest.raises(requests.exceptions.HTTPError):
            fetch_wyoming_sounding(self.TIME, "82193")

    def test_erro_de_servidor_propaga_sem_virar_no_data(self, monkeypatch):
        import requests

        monkeypatch.setattr(
            requests,
            "get",
            lambda url, params=None, timeout=None: _FakeResponse(503, "maintenance"),
        )
        with pytest.raises(requests.exceptions.HTTPError):
            fetch_wyoming_sounding(self.TIME, "82193")


@pytest.mark.skipif(
    not os.environ.get("CARTOMET_NET_TESTS"),
    reason="teste de rede ao vivo — exporte CARTOMET_NET_TESTS=1 para rodar",
)
def test_fetch_ao_vivo_belem():
    """Sanidade contra o servidor real (fora do CI)."""
    df = fetch_wyoming_sounding(datetime(2026, 7, 5, 12), 82193)
    assert len(df) > 20
    assert df["pressure"].iloc[0] > 900
