"""Popup de estação — coluna raw_report, hit-test em pixels e formatação.

O hit-test roda contra o subset AFINADO plotado (`_station_plotted`): estação
descartada pelo thinning ou camada oculta não pode responder ao clique.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from cartomet_br.data.stations import (
    OBS_MODE_LATEST,
    STATION_COLUMNS,
    _metar_record_from_json,
    _set_obs_attrs,
    _synop_record_from_decoded,
    normalize_station_records,
)
from cartomet_br.gui.dialogs import station_report_lines

# ═══════════════════════════════════════════════════════════════════════════════
#  Camada de dados — raw_report
# ═══════════════════════════════════════════════════════════════════════════════


class TestRawReportColumn:
    def test_column_is_canonical(self):
        assert "raw_report" in STATION_COLUMNS

    def test_metar_maps_rawob(self):
        rec = _metar_record_from_json(
            {"icaoId": "SBBR", "lat": -15.8, "lon": -47.9, "rawOb": "SBBR 311400Z 09010KT CAVOK"}
        )
        assert rec["raw_report"] == "SBBR 311400Z 09010KT CAVOK"

    def test_synop_record_carries_raw(self):
        rec = _synop_record_from_decoded(
            {}, "83378", {"83378": (-15.8, -47.9)}, raw_report="AAXX 31121 83378 32598="
        )
        assert rec["raw_report"] == "AAXX 31121 83378 32598="

    def test_normalize_keeps_raw_as_text(self):
        df = normalize_station_records(
            [
                {
                    "station_id": "SBBR",
                    "latitude": -15.8,
                    "longitude": -47.9,
                    "raw_report": "SBBR 311400Z ...",
                },
                {"station_id": "SBGL", "latitude": -22.8, "longitude": -43.2},
            ]
        )
        assert df.loc[0, "raw_report"] == "SBBR 311400Z ..."
        assert df.loc[1, "raw_report"] == ""  # ausente vira string vazia, não NaN


# ═══════════════════════════════════════════════════════════════════════════════
#  Formatação do relatório (pura)
# ═══════════════════════════════════════════════════════════════════════════════


def _payload(**extra) -> dict:
    base = {
        "kind": "metar",
        "station_id": "SBBR",
        "latitude": -15.8,
        "longitude": -47.9,
        "air_temperature": 25.0,
        "dew_point_temperature": 18.0,
        "air_pressure_at_sea_level": 1015.2,
        # vento de LESTE (090°) a 10 kt: u = -s·sin(90°) < 0, v ≈ 0
        "eastward_wind": -10 * 0.514444,
        "northward_wind": 0.0,
        "cloud_coverage": 6.0,
        "current_wx1_symbol": 60.0,
        "raw_report": "SBBR 311400Z 09010KT CAVOK 25/18 Q1015",
        "obs_time_utc": datetime(2026, 7, 31, 14, 0, tzinfo=UTC),
        "obs_mode": OBS_MODE_LATEST,
    }
    base.update(extra)
    return base


class TestReportLines:
    def test_wind_back_to_meteorological_convention(self):
        linhas = dict(station_report_lines(_payload()))
        assert linhas["Vento"] == "090° / 10 kt"

    def test_calm_wind(self):
        linhas = dict(station_report_lines(_payload(eastward_wind=0.0, northward_wind=0.0)))
        assert linhas["Vento"] == "calmo"

    def test_nan_becomes_dash(self):
        linhas = dict(
            station_report_lines(_payload(air_temperature=math.nan, cloud_coverage=math.nan))
        )
        assert linhas["Temperatura"] == "—"
        assert linhas["Nebulosidade"] == "—"

    def test_fields_formatted(self):
        linhas = dict(station_report_lines(_payload()))
        assert linhas["Temperatura"] == "25.0 °C"
        assert linhas["PNMM"] == "1015.2 hPa"
        assert linhas["Nebulosidade"] == "6/8"
        assert linhas["Horário da obs"] == "14:00Z 31/07/2026"

    def test_missing_obs_time(self):
        linhas = dict(station_report_lines(_payload(obs_time_utc=None)))
        assert linhas["Horário da obs"] == "—"


# ═══════════════════════════════════════════════════════════════════════════════
#  Hit-test no canvas (offscreen)
# ═══════════════════════════════════════════════════════════════════════════════


def _obs_df():
    df = normalize_station_records(
        [
            {
                "station_id": "SBBR",
                "latitude": -15.8,
                "longitude": -47.9,
                "air_temperature": 25.0,
                "raw_report": "SBBR 311400Z 09010KT",
            },
            {
                "station_id": "SBPA",
                "latitude": -30.0,
                "longitude": -51.2,
                "air_temperature": 12.0,
                "raw_report": "SBPA 311400Z 27015KT",
            },
        ]
    )
    return _set_obs_attrs(df, OBS_MODE_LATEST, datetime(2026, 7, 31, 14, 32, tzinfo=UTC))


def _pixel_of(canvas, lon: float, lat: float) -> tuple[float, float]:
    x, y = canvas.ax.transData.transform((lon, lat))
    return float(x), float(y)


class TestStationHitTest:
    def test_click_near_station_returns_payload(self, canvas):
        canvas.plot_stations(_obs_df(), kind="metar")
        px, py = _pixel_of(canvas, -47.9, -15.8)
        payload = canvas._station_at_pixel(px + 3, py - 3)
        assert payload is not None
        assert payload["station_id"] == "SBBR"
        assert payload["kind"] == "metar"
        assert payload["raw_report"].startswith("SBBR")
        assert payload["obs_time_utc"] == datetime(2026, 7, 31, 14, 32, tzinfo=UTC)

    def test_click_far_returns_none(self, canvas):
        canvas.plot_stations(_obs_df(), kind="metar")
        px, py = _pixel_of(canvas, -47.9, -15.8)
        assert canvas._station_at_pixel(px + 200, py + 200) is None

    def test_hidden_layer_does_not_respond(self, canvas):
        canvas.plot_stations(_obs_df(), kind="metar")
        canvas.toggle_stations("metar", False)
        px, py = _pixel_of(canvas, -47.9, -15.8)
        assert canvas._station_at_pixel(px, py) is None

    def test_removed_layer_does_not_respond(self, canvas):
        canvas.plot_stations(_obs_df(), kind="metar")
        canvas.remove_stations("metar")
        px, py = _pixel_of(canvas, -47.9, -15.8)
        assert canvas._station_at_pixel(px, py) is None

    def test_neutral_click_emits_signal(self, canvas):
        canvas.plot_stations(_obs_df(), kind="metar")
        got: list[dict] = []
        canvas.station_report_requested.connect(got.append)
        px, py = _pixel_of(canvas, -30.0 * 0 + -51.2, -30.0)  # SBPA
        event = SimpleNamespace(inaxes=canvas.ax, button=1, x=px, y=py, xdata=-51.2, ydata=-30.0)
        canvas.interaction_mode = None
        canvas._on_click(event)
        assert len(got) == 1
        assert got[0]["station_id"] == "SBPA"

    def test_draw_mode_click_does_not_probe(self, canvas):
        canvas.plot_stations(_obs_df(), kind="metar")
        got: list[dict] = []
        canvas.station_report_requested.connect(got.append)
        px, py = _pixel_of(canvas, -47.9, -15.8)
        event = SimpleNamespace(inaxes=canvas.ax, button=1, x=px, y=py, xdata=-47.9, ydata=-15.8)
        canvas.interaction_mode = "pan"  # ferramenta ativa: clique é do pan
        canvas._on_click(event)
        assert got == []

    def test_thinning_survivor_only(self, canvas):
        # Duas estações quase no mesmo ponto: o thinning descarta uma; a
        # descartada NÃO pode responder ao clique.
        df = normalize_station_records(
            [
                {
                    "station_id": "AAAA",
                    "latitude": -15.80,
                    "longitude": -47.90,
                    "air_temperature": 25.0,
                },
                {
                    "station_id": "BBBB",
                    "latitude": -15.81,
                    "longitude": -47.91,
                    "air_temperature": 26.0,
                },
            ]
        )
        canvas.plot_stations(_set_obs_attrs(df, OBS_MODE_LATEST, None), kind="metar")
        plotted = canvas._station_plotted["metar"]
        assert plotted is not None and len(plotted) == 1  # thinning agiu
        px, py = _pixel_of(canvas, -47.90, -15.80)
        payload = canvas._station_at_pixel(px, py)
        assert payload is not None
        assert payload["station_id"] == str(plotted.iloc[0]["station_id"])


def test_numpy_types_in_payload_are_plain(canvas):
    """to_dict() do pandas devolve tipos numpy — o dialog formata sem quebrar."""
    canvas.plot_stations(_obs_df(), kind="metar")
    px, py = _pixel_of(canvas, -47.9, -15.8)
    payload = canvas._station_at_pixel(px, py)
    linhas = station_report_lines(payload)
    assert any(v == "25.0 °C" for _k, v in linhas)
    assert isinstance(np.asarray(payload["latitude"]).item(), float)
