"""Carimbo de horário das observações no título da carta (offscreen).

No modo "mais recente" as observações se descolam da rodada carregada; o
título precisa carimbar o horário REAL da obs (contrato df.attrs dos
fetchers → MapCanvas._station_meta → _obs_time_suffix).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import UTC, datetime

import pytest

pytest.importorskip("PyQt6")

from cartomet_br.data.stations import (
    OBS_MODE_ANALYSIS,
    OBS_MODE_LATEST,
    _set_obs_attrs,
    normalize_station_records,
)


def _df(mode: str, when: datetime | None, fallback: bool = False):
    # air_temperature presente: sem nenhuma variável o StationPlot não cria
    # artista e a camada não conta como ativa no título.
    df = normalize_station_records(
        [
            {"station_id": "SBBR", "latitude": -15.8, "longitude": -47.9, "air_temperature": 25.0},
            {"station_id": "SBGL", "latitude": -22.8, "longitude": -43.2, "air_temperature": 28.0},
        ]
    )
    return _set_obs_attrs(df, mode, when, fallback)


_WHEN = datetime(2026, 7, 31, 14, 32, tzinfo=UTC)


def test_latest_mode_stamps_title(canvas):
    canvas.plot_stations(_df(OBS_MODE_LATEST, _WHEN), kind="metar")
    title = canvas.ax.get_title(loc="left")
    assert "Obs: METAR 14:32Z 31/07" in title


def test_analysis_mode_has_no_stamp(canvas):
    canvas.plot_stations(_df(OBS_MODE_ANALYSIS, _WHEN), kind="metar")
    title = canvas.ax.get_title(loc="left")
    assert "METAR" in title  # rótulo da camada continua
    assert "Obs:" not in title  # sem carimbo: horário = Válido da carta


def test_synop_stamp_uses_synoptic_hour_format(canvas):
    canvas.plot_stations(_df(OBS_MODE_LATEST, datetime(2026, 7, 31, 12, 0, tzinfo=UTC)), "synop")
    title = canvas.ax.get_title(loc="left")
    assert "Obs: SYNOP 12Z 31/07" in title


def test_both_kinds_joined_in_one_stamp(canvas):
    canvas.plot_stations(_df(OBS_MODE_LATEST, _WHEN), kind="metar")
    canvas.plot_stations(_df(OBS_MODE_LATEST, datetime(2026, 7, 31, 12, 0, tzinfo=UTC)), "synop")
    title = canvas.ax.get_title(loc="left")
    assert "METAR 14:32Z 31/07" in title
    assert "SYNOP 12Z 31/07" in title
    assert title.count("Obs:") == 1


def test_remove_stations_clears_stamp(canvas):
    canvas.plot_stations(_df(OBS_MODE_LATEST, _WHEN), kind="metar")
    canvas.remove_stations("metar")
    assert "Obs:" not in canvas.ax.get_title(loc="left")


def test_density_rerender_preserves_stamp(canvas):
    canvas.plot_stations(_df(OBS_MODE_LATEST, _WHEN), kind="metar")
    canvas.set_observation_density(4.0)  # re-render do MESMO df, sem rede
    assert "Obs: METAR 14:32Z 31/07" in canvas.ax.get_title(loc="left")
