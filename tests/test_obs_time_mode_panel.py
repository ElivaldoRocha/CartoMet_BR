"""Gate de modo de horário das observações no SettingsPanel (offscreen).

QSettings no Windows é registry: cada teste semeia a chave `map/obs_time_mode`
antes de construir o painel e a fixture restaura o valor original ao final —
o registro do desenvolvedor nunca fica poluído nem contamina os asserts.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import UTC, datetime

import pytest

pytest.importorskip("PyQt6")

from cartomet_br.data.stations import OBS_MODE_ANALYSIS, OBS_MODE_LATEST

_KEY = "map/obs_time_mode"


@pytest.fixture
def obs_settings(qapp):
    from PyQt6.QtCore import QSettings

    from cartomet_br.gui._constants import APP_NAME

    settings = QSettings("PPGGRD-UFPA", APP_NAME)
    original = settings.value(_KEY, None)
    try:
        yield settings
    finally:
        if original is None:
            settings.remove(_KEY)
        else:
            settings.setValue(_KEY, original)
        settings.sync()


def _make_panel(obs_settings, saved_mode=None):
    """Constrói o SettingsPanel com a chave semeada (ou ausente)."""
    if saved_mode is None:
        obs_settings.remove(_KEY)
    else:
        obs_settings.setValue(_KEY, saved_mode)
    obs_settings.sync()
    from cartomet_br.gui.layer_panel import SettingsPanel

    return SettingsPanel()


def _set_mode(panel, mode: str) -> None:
    panel.obs_time_combo.setCurrentIndex(panel.obs_time_combo.findData(mode))


def _set_forecast_step(panel) -> None:
    panel.step_combo.setCurrentIndex(1)  # +3h


def test_default_mode_is_analysis(obs_settings):
    panel = _make_panel(obs_settings)
    assert panel.get_obs_time_mode() == OBS_MODE_ANALYSIS
    assert panel.synop_check.isEnabled()  # step inicial é +0h
    assert not panel.obs_refresh_btn.isEnabled()  # 🔄 só no modo "mais recente"


def test_qsettings_restores_latest(obs_settings):
    panel = _make_panel(obs_settings, OBS_MODE_LATEST)
    assert panel.get_obs_time_mode() == OBS_MODE_LATEST
    assert panel.obs_refresh_btn.isEnabled()


def test_qsettings_invalid_value_falls_back(obs_settings):
    panel = _make_panel(obs_settings, "tempo-magico")
    assert panel.get_obs_time_mode() == OBS_MODE_ANALYSIS


def test_latest_mode_enables_any_step(obs_settings):
    panel = _make_panel(obs_settings, OBS_MODE_LATEST)
    _set_forecast_step(panel)
    assert panel.synop_check.isEnabled()
    assert panel.metar_check.isEnabled()
    assert panel.obs_refresh_btn.isEnabled()


def test_analysis_mode_blocks_forecast_steps(obs_settings):
    panel = _make_panel(obs_settings, OBS_MODE_ANALYSIS)
    panel.metar_check.setChecked(True)
    _set_forecast_step(panel)
    assert not panel.metar_check.isChecked()  # desmarcado (remove o overlay)
    assert not panel.metar_check.isEnabled()
    assert not panel.synop_check.isEnabled()


def test_switch_latest_to_analysis_on_forecast_unchecks(obs_settings):
    panel = _make_panel(obs_settings, OBS_MODE_LATEST)
    _set_forecast_step(panel)
    panel.metar_check.setChecked(True)
    _set_mode(panel, OBS_MODE_ANALYSIS)
    assert not panel.metar_check.isChecked()
    assert not panel.metar_check.isEnabled()


def test_mode_change_emits_signal(obs_settings):
    panel = _make_panel(obs_settings, OBS_MODE_ANALYSIS)
    got: list[str] = []
    panel.obs_time_mode_changed.connect(got.append)
    _set_mode(panel, OBS_MODE_LATEST)
    assert got == [OBS_MODE_LATEST]


def test_refresh_button_emits_signal(obs_settings):
    panel = _make_panel(obs_settings, OBS_MODE_LATEST)
    hits: list[bool] = []
    panel.obs_refresh_requested.connect(lambda: hits.append(True))
    panel.obs_refresh_btn.click()
    assert hits == [True]


def test_actual_times_rendered_in_hint(obs_settings):
    panel = _make_panel(obs_settings, OBS_MODE_LATEST)
    panel.set_obs_actual_times(
        {
            "metar": datetime(2026, 7, 31, 14, 32, tzinfo=UTC),
            "synop": datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
            "synop_fallback": True,
        }
    )
    hint = panel.obs_time_label.text()
    assert "14:32Z 31/07" in hint
    assert "12Z 31/07" in hint
    assert "recuo de slot" in hint


def test_mode_change_clears_actual_times(obs_settings):
    panel = _make_panel(obs_settings, OBS_MODE_LATEST)
    panel.set_obs_actual_times({"metar": datetime(2026, 7, 31, 14, 32, tzinfo=UTC)})
    _set_mode(panel, OBS_MODE_ANALYSIS)
    _set_mode(panel, OBS_MODE_LATEST)
    assert "14:32Z" not in panel.obs_time_label.text()  # hint genérico até novo fetch


def test_analysis_hint_labels_metar_as_analysis_window(obs_settings):
    panel = _make_panel(obs_settings, OBS_MODE_ANALYSIS)
    panel.set_obs_reference_time(datetime(2026, 7, 31, 17, 0, tzinfo=UTC))
    hint = panel.obs_time_label.text()
    assert "12Z" in hint  # SYNOP: piso sinótico de 6 h
    assert "janela da análise" in hint
    assert "mais recente" not in hint  # o rótulo mentiroso antigo morreu
