"""Comparação de rodadas (Δ novo − antigo, mesmo valid_time) — motor e render.

O motor é testado com `DataService.load_field` simulado (sem rede): o que
importa é a ARITMÉTICA de rodadas (rodada B = A − Δh, step_b = step + Δh),
as validações da grade de steps e o contrato do campo sintético `run_diff`.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import UTC, datetime

import numpy as np
import pytest

from cartomet_br.data.ecmwf import VARIABLE_REGISTRY, PLFieldData
from cartomet_br.services.data_service import (
    RUN_DIFF_VARIABLES,
    DataService,
    DataServiceError,
    ValidationError,
)


def _fake_field(values: float, step: int) -> PLFieldData:
    grid = np.full((3, 4), float(values))
    return PLFieldData(
        values=grid,
        lons=np.linspace(-60, -45, 4),
        lats=np.linspace(-25, -15, 3),
        variable="gh",
        level=500,
        unit="mgp",
        valid_time="2026-07-31T12:00",
        base_time="00Z 31/07/2026",
        step=step,
    )


@pytest.fixture
def svc(config_brasil, monkeypatch):
    """DataService com load_field simulado; captura as chamadas em svc._calls."""
    service = DataService(config_brasil)
    calls: list[dict] = []

    def fake_load_field(self, variable_key, level, step, cycle=None, cycle_date=None, **kw):
        calls.append(
            {
                "variable": variable_key,
                "level": level,
                "step": step,
                "cycle": cycle,
                "cycle_date": cycle_date,
            }
        )
        # Rodada nova (1ª chamada) vale 10; antiga (2ª) vale 4 → Δ = 6
        values = 10.0 if len(calls) == 1 else 4.0
        return f"{variable_key}_{level}", _fake_field(values, step)

    monkeypatch.setattr(DataService, "load_field", fake_load_field)
    service._calls = calls  # type: ignore[attr-defined]
    return service


class TestRunMath:
    def test_previous_run_same_valid_time(self, svc):
        layer_id, data = svc.load_run_comparison(
            "gh", 500, step=12, cycle=0, cycle_date="20260731", delta_hours=6
        )
        a, b = svc._calls
        assert (a["cycle"], a["cycle_date"], a["step"]) == (0, "20260731", 12)
        # Rodada B: 6 h antes (18Z da véspera), step compensado → MESMO valid_time
        assert (b["cycle"], b["cycle_date"], b["step"]) == (18, "20260730", 18)
        assert layer_id == "run_diff_gh_500"
        assert np.allclose(data.values, 6.0)  # novo(10) − antigo(4)

    def test_delta_24h_crosses_day(self, svc):
        svc.load_run_comparison("gh", 500, step=24, cycle=0, cycle_date="20260731", delta_hours=24)
        b = svc._calls[1]
        assert (b["cycle"], b["cycle_date"], b["step"]) == (0, "20260730", 48)

    def test_synthetic_field_contract(self, svc):
        _lid, data = svc.load_run_comparison(
            "gh", 500, step=12, cycle=0, cycle_date="20260731", delta_hours=6
        )
        assert data.variable == "run_diff"
        assert data.unit == "mgp"  # herda a unidade da variável-base
        extra = data.extra or {}
        assert extra["base_var"] == "gh"
        assert extra["delta_hours"] == 6
        assert extra["cycle_a"] == "00Z 31/07"
        assert extra["cycle_b"] == "18Z 30/07"
        assert "Δ Altura Geopotencial 500 hPa" in extra["title_desc"]
        assert "00Z 31/07 − 18Z 30/07" in extra["title_desc"]

    def test_auto_cycle_resolves_latest(self, svc, monkeypatch):
        import cartomet_br.services.data_service as ds_mod

        base = datetime(2026, 7, 31, 12, tzinfo=UTC)
        monkeypatch.setattr(
            ds_mod,
            "estimate_available_cycles",
            lambda: {"latest": {"cycle": 12, "base_datetime": base}},
        )
        svc.load_run_comparison("gh", 500, step=12, cycle=None, cycle_date=None, delta_hours=6)
        a, b = svc._calls
        assert (a["cycle"], a["cycle_date"]) == (12, "20260731")
        assert (b["cycle"], b["cycle_date"]) == (6, "20260731")


class TestRunValidation:
    def test_step_off_grid_in_old_run(self, svc):
        # 141 + 6 = 147: fora da grade (144→150 pula de 6 em 6)
        with pytest.raises(ValidationError, match=r"\+147h"):
            svc.load_run_comparison(
                "gh", 500, step=141, cycle=0, cycle_date="20260731", delta_hours=6
            )
        assert svc._calls == []  # falha ANTES de qualquer download

    def test_old_run_0618_beyond_144(self, svc):
        # step 144 + 6 = 150 na rodada 18Z (alcance máx. +144h)
        with pytest.raises(ValidationError, match="144h"):
            svc.load_run_comparison(
                "gh", 500, step=144, cycle=0, cycle_date="20260731", delta_hours=6
            )

    def test_ineligible_variable(self, svc):
        with pytest.raises(ValidationError, match="wind"):
            svc.load_run_comparison(
                "wind", 500, step=12, cycle=0, cycle_date="20260731", delta_hours=6
            )

    def test_delta_must_be_multiple_of_6(self, svc):
        with pytest.raises(ValidationError, match="múltiplo de 6"):
            svc.load_run_comparison(
                "gh", 500, step=12, cycle=0, cycle_date="20260731", delta_hours=3
            )

    def test_grid_mismatch_raises(self, config_brasil, monkeypatch):
        service = DataService(config_brasil)
        shapes = iter([(3, 4), (5, 6)])

        def fake(self, variable_key, level, step, cycle=None, cycle_date=None, **kw):
            f = _fake_field(1.0, step)
            f.values = np.zeros(next(shapes))
            return "x", f

        monkeypatch.setattr(DataService, "load_field", fake)
        with pytest.raises(DataServiceError, match="grades"):
            service.load_run_comparison(
                "gh", 500, step=12, cycle=0, cycle_date="20260731", delta_hours=6
            )


class TestRegistryEntry:
    def test_run_diff_registered_for_render(self):
        info = VARIABLE_REGISTRY["run_diff"]
        assert info["plot_type"] == "contourf"
        assert info["symmetric"] is True  # níveis simétricos em torno de zero
        assert info["cmap"] == "RdBu_r"  # divergente: Δ>0 vermelho

    def test_eligible_vars_exist_in_registry(self):
        for key in RUN_DIFF_VARIABLES:
            assert key in VARIABLE_REGISTRY


# ═══════════════════════════════════════════════════════════════════════════════
#  Render + título honesto + projeto (offscreen)
# ═══════════════════════════════════════════════════════════════════════════════


def _diff_field() -> PLFieldData:
    lons, lats = np.linspace(-60, -40, 6), np.linspace(-30, -10, 5)
    values = np.linspace(-30, 30, 30).reshape(5, 6)
    return PLFieldData(
        values=values,
        lons=lons,
        lats=lats,
        variable="run_diff",
        level=500,
        unit="mgp",
        valid_time="2026-07-31T12:00",
        base_time="00Z 31/07/2026",
        step=12,
        extra={
            "run_diff": True,
            "base_var": "gh",
            "delta_hours": 6,
            "cycle_a": "00Z 31/07",
            "cycle_b": "18Z 30/07",
            "title_desc": "Δ Altura Geopotencial 500 hPa (mgp) — rodada 00Z 31/07 − 18Z 30/07",
            "entry_label": "Δ Altura Geopotencial 500 hPa",
            "entry_detail": "00Z 31/07 − 18Z 30/07",
        },
    )


@pytest.fixture
def canvas_qt(canvas):
    pytest.importorskip("PyQt6")
    return canvas


def test_render_and_honest_title(canvas_qt):
    canvas_qt.add_pl_layer("run_diff_gh_500", _diff_field(), "barbs")
    assert canvas_qt._pl_artists.get("run_diff_gh_500")  # contourf desenhado
    title = canvas_qt.ax.get_title(loc="left")
    assert "Δ Altura Geopotencial 500 hPa" in title
    assert "rodada 00Z 31/07 − 18Z 30/07" in title


def test_project_spec_roundtrips_run_diff(canvas_qt):
    canvas_qt.add_pl_layer("run_diff_gh_500", _diff_field(), "barbs")
    specs = [s for s in canvas_qt.export_layers_state() if s.get("kind") == "run_diff"]
    assert len(specs) == 1
    spec = specs[0]
    assert spec["base_var"] == "gh"
    assert spec["level"] == 500
    assert spec["step"] == 12
    assert spec["delta_hours"] == 6
