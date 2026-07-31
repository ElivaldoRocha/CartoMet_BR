"""AIFS (modelo de IA do ECMWF) — motor, validações, gating e título honesto.

O contrato central: o PREFIXO do cache GRIB identifica o modelo no disco
("ecmwf_" = IFS, "aifs_" = AIFS) e `download_ecmwf` infere o modelo dele.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

import cartomet_br.data.ecmwf as ecmwf_mod
from cartomet_br.core.config import Config
from cartomet_br.data.ecmwf import (
    AIFS_MAX_STEP,
    AIFS_PUBLISH_DELAY,
    AIFS_UNAVAILABLE_VARS,
    AIFS_VALID_STEPS,
    PLFieldData,
    download_ecmwf,
    estimate_available_cycles,
    grib_prefix,
    variable_available,
)
from cartomet_br.services.data_service import (
    DataService,
    ValidationError,
    valid_steps_for,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  Contratos puros
# ═══════════════════════════════════════════════════════════════════════════════


class TestContracts:
    def test_grib_prefix(self):
        assert grib_prefix("ifs") == "ecmwf"  # nome histórico preserva caches
        assert grib_prefix("aifs") == "aifs"
        assert grib_prefix() == "ecmwf"

    def test_variable_availability(self):
        for var in ("gh", "t", "q", "w", "wind", "wind_speed", "temp_adv", "theta_e"):
            assert variable_available(var, "aifs"), var
        for var in AIFS_UNAVAILABLE_VARS:
            assert not variable_available(var, "aifs"), var
            assert variable_available(var, "ifs"), var  # IFS tem todas

    def test_aifs_step_grid(self):
        assert AIFS_VALID_STEPS[0] == 0
        assert AIFS_VALID_STEPS[-1] == AIFS_MAX_STEP == 360
        assert all(s % 6 == 0 for s in AIFS_VALID_STEPS)

    def test_step_grid_constants_do_not_drift(self):
        # Duplicação deliberada (GUI não importa xarray) — não pode divergir.
        from cartomet_br.gui._constants import AIFS_VALID_STEPS as GUI_STEPS

        assert GUI_STEPS == AIFS_VALID_STEPS

    def test_valid_steps_for(self):
        assert 3 in valid_steps_for("ifs")
        assert 3 not in valid_steps_for("aifs")
        assert 360 in valid_steps_for("aifs")
        assert 360 not in valid_steps_for("ifs")


# ═══════════════════════════════════════════════════════════════════════════════
#  download_ecmwf — inferência do modelo pelo prefixo do cache
# ═══════════════════════════════════════════════════════════════════════════════


class _FakeClient:
    """Captura os kwargs do construtor e 'baixa' escrevendo o target."""

    calls: list[dict] = []

    def __init__(self, source=None, model=None):
        _FakeClient.calls.append({"source": source, "model": model})

    def retrieve(self, **kw):
        Path(kw["target"]).write_bytes(b"GRIB-fake")

    def download(self, **kw):
        self.retrieve(**kw)


@pytest.fixture
def fake_client(monkeypatch):
    _FakeClient.calls = []
    monkeypatch.setattr(ecmwf_mod, "Client", _FakeClient)
    monkeypatch.setattr(ecmwf_mod, "_bound_session_timeout", lambda *a, **k: None)
    return _FakeClient


class TestModelInference:
    def test_aifs_prefix_builds_aifs_client(self, tmp_path, fake_client):
        download_ecmwf(
            variables=["gh"],
            levels=[500],
            step=12,
            output_path=tmp_path / "aifs_gh_20260731_00Z_500hPa_f012.grib2",
            data_dir=tmp_path,
        )
        assert fake_client.calls[-1]["model"] == "aifs-single"

    def test_ecmwf_prefix_builds_ifs_client(self, tmp_path, fake_client):
        download_ecmwf(
            variables=["msl"],
            step=12,
            output_path=tmp_path / "ecmwf_msl_20260731_00Z_f012.grib2",
            data_dir=tmp_path,
        )
        assert fake_client.calls[-1]["model"] == "ifs"

    def test_explicit_model_kwarg_wins(self, tmp_path, fake_client):
        download_ecmwf(
            variables=["gh"],
            levels=[500],
            step=12,
            output_path=tmp_path / "qualquer_nome.grib2",
            data_dir=tmp_path,
            model="aifs",
        )
        assert fake_client.calls[-1]["model"] == "aifs-single"


# ═══════════════════════════════════════════════════════════════════════════════
#  estimate_available_cycles — delays por modelo (relógio congelado)
# ═══════════════════════════════════════════════════════════════════════════════


class _FrozenDT(datetime):
    @classmethod
    def now(cls, tz=None):
        # 13:00Z: IFS (delay 7,5 h) só tem a 00Z publicada; o AIFS (6 h) já
        # tem a 06Z — o mesmo instante dá "latest" diferente por modelo.
        return datetime(2026, 7, 31, 13, 0, tzinfo=UTC)


class TestCycleEstimation:
    @pytest.fixture(autouse=True)
    def _freeze(self, monkeypatch):
        monkeypatch.setattr(ecmwf_mod, "datetime", _FrozenDT)

    def test_ifs_latest_lags_aifs(self):
        ifs = estimate_available_cycles("ifs")["latest"]
        aifs = estimate_available_cycles("aifs")["latest"]
        assert ifs["cycle"] == 0  # 06Z do IFS só publica ~13:30Z
        assert aifs["cycle"] == 6  # AIFS publica ~6 h após a rodada

    def test_aifs_all_cycles_reach_360(self):
        info = estimate_available_cycles("aifs")
        assert all(c["max_step"] == 360 for c in info["available"])

    def test_ifs_0618_still_capped(self):
        info = estimate_available_cycles("ifs")
        caps = {c["cycle"]: c["max_step"] for c in info["available"]}
        assert caps[6] == 144 and caps[18] == 144
        assert caps[0] == 240 and caps[12] == 240

    def test_delay_constant(self):
        assert AIFS_PUBLISH_DELAY < ecmwf_mod.PUBLISH_DELAY  # AIFS publica antes


# ═══════════════════════════════════════════════════════════════════════════════
#  Validações model-aware do DataService
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidation:
    def test_step_3h_invalid_in_aifs(self):
        with pytest.raises(ValidationError, match="AIFS"):
            DataService.validate_step(3, "aifs")
        DataService.validate_step(3, "ifs")  # não levanta

    def test_step_360_only_in_aifs(self):
        DataService.validate_step(360, "aifs")
        with pytest.raises(ValidationError):
            DataService.validate_step(360, "ifs")

    def test_aifs_1830z_reaches_360(self):
        DataService.validate_cycle(18, 360, "aifs")  # sem o corte do IFS
        with pytest.raises(ValidationError, match="144h"):
            DataService.validate_cycle(18, 150, "ifs")

    def test_variable_model_gate(self):
        with pytest.raises(ValidationError, match="AIFS"):
            DataService.validate_variable_model("r", "aifs")
        DataService.validate_variable_model("r", "ifs")
        DataService.validate_variable_model("gh", "aifs")

    def test_load_field_blocks_unavailable_before_network(self, config_brasil):
        config_brasil.model = "aifs"
        svc = DataService(config_brasil)
        with pytest.raises(ValidationError, match="AIFS"):
            svc.load_field("olr", None, 12)  # valida ANTES de qualquer download


# ═══════════════════════════════════════════════════════════════════════════════
#  Config + GUI (offscreen)
# ═══════════════════════════════════════════════════════════════════════════════


def test_config_default_model_is_ifs():
    cfg = Config(extent=[-75.0, -35.0, -30.0, 6.0])
    assert cfg.model == "ifs"


@pytest.fixture
def settings_panel(qapp):
    from cartomet_br.gui.layer_panel import SettingsPanel

    return SettingsPanel()


class TestModelCombo:
    def test_default_is_ifs_with_3h_grid(self, settings_panel):
        assert settings_panel.get_model() == "ifs"
        steps = [
            settings_panel.step_combo.itemData(i) for i in range(settings_panel.step_combo.count())
        ]
        assert 3 in steps

    def test_switch_to_aifs_regrades_steps(self, settings_panel):
        got: list[str] = []
        settings_panel.model_changed.connect(got.append)
        settings_panel.model_combo.setCurrentIndex(settings_panel.model_combo.findData("aifs"))
        assert got == ["aifs"]
        steps = [
            settings_panel.step_combo.itemData(i) for i in range(settings_panel.step_combo.count())
        ]
        assert steps == AIFS_VALID_STEPS

    def test_step_preserved_to_closest(self, settings_panel):
        settings_panel.step_combo.setCurrentIndex(settings_panel.step_combo.findData(9))
        settings_panel.model_combo.setCurrentIndex(settings_panel.model_combo.findData("aifs"))
        assert settings_panel.get_step() in (6, 12)  # 9h não existe no AIFS

    def test_set_model_does_not_emit(self, settings_panel):
        got: list[str] = []
        settings_panel.model_changed.connect(got.append)
        settings_panel.set_model("aifs")
        assert got == []  # restauração de projeto não dispara refetch
        assert settings_panel.get_model() == "aifs"


class TestFieldPanelGating:
    def test_gating_disables_missing_vars(self, qapp):
        from cartomet_br.gui.layer_panel import FieldLayerPanel

        panel = FieldLayerPanel()
        panel.set_model_gating("aifs")
        combo = panel.var_combo
        states = {
            str(combo.itemData(i)): combo.model().item(i).isEnabled() for i in range(combo.count())
        }
        assert states["t"] and states["gh"] and states["q"]
        assert not states["r"] and not states["d"] and not states["vo"]
        sfc = panel.sfc_var_combo
        sfc_states = {
            str(sfc.itemData(i)): sfc.model().item(i).isEnabled() for i in range(sfc.count())
        }
        assert not sfc_states["tcwv"] and not sfc_states["olr"]
        # Voltar ao IFS reabilita tudo
        panel.set_model_gating("ifs")
        assert all(combo.model().item(i).isEnabled() for i in range(combo.count()))

    def test_current_selection_moves_off_disabled(self, qapp):
        from cartomet_br.gui.layer_panel import FieldLayerPanel

        panel = FieldLayerPanel()
        panel.var_combo.setCurrentIndex(panel.var_combo.findData("r"))
        panel.set_model_gating("aifs")
        assert variable_available(str(panel.var_combo.currentData()), "aifs")


# ═══════════════════════════════════════════════════════════════════════════════
#  Título honesto (offscreen)
# ═══════════════════════════════════════════════════════════════════════════════


def _aifs_field() -> PLFieldData:
    return PLFieldData(
        values=np.linspace(0, 30, 30).reshape(5, 6),
        lons=np.linspace(-60, -40, 6),
        lats=np.linspace(-30, -10, 5),
        variable="t",
        level=850,
        unit="°C",
        valid_time="2026-07-31T12:00",
        base_time="00Z 31/07/2026",
        step=12,
        source="aifs",
    )


def test_title_prefix_aifs(canvas):
    canvas.add_pl_layer("t_850", _aifs_field(), "barbs")
    title = canvas.ax.get_title(loc="left")
    assert title.startswith("ECMWF AIFS (IA)")
    assert "ECMWF IFS" not in title
