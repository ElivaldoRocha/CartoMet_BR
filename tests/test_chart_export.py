"""Testes puros da montagem de metadados da carta OMM (F7).

Sem PyQt/Matplotlib — exercita ``chart_export`` (formatação de emissão e fusão
do cabeçalho com a cronologia da carta).
"""

from datetime import datetime, timedelta, timezone

from cartomet_br.gui.chart_export import compose_chart_metadata, format_emission


def test_format_emission_naive_treated_as_utc():
    assert format_emission(datetime(2026, 6, 14, 12, 30)) == "14-06-2026 12:30"


def test_format_emission_aware_converted_to_utc():
    tz = timezone(timedelta(hours=-3))  # 09:30-03 == 12:30 UTC
    dt = datetime(2026, 6, 14, 9, 30, tzinfo=tz)
    assert format_emission(dt) == "14-06-2026 12:30"


def test_compose_merges_header_and_time_meta():
    header = {
        "institution": " UFPA ",
        "analyst": "Fulano",
        "chart_type": "Carta X",
        "logo_path": "",
    }
    time_meta = {"valid_time": "14-06-2026 12:00", "base_time": "00Z 14-06-2026", "step": 12}
    meta = compose_chart_metadata(header, time_meta, datetime(2026, 6, 14, 12, 0))

    assert meta["institution"] == "UFPA"  # trim
    assert meta["analyst"] == "Fulano"
    assert meta["chart_type"] == "Carta X"
    assert meta["valid_time"] == "14-06-2026 12:00"
    assert meta["base_time"] == "00Z 14-06-2026"
    assert meta["step"] == 12
    assert meta["emission"] == "14-06-2026 12:00"


def test_compose_handles_missing_fields():
    meta = compose_chart_metadata({}, {})
    assert meta["institution"] == ""
    assert meta["valid_time"] == ""
    assert meta["base_time"] == ""
    assert meta["step"] == 0
    assert "emission" in meta and meta["emission"]  # sempre preenchida (agora)
