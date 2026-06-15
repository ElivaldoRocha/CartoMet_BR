"""Teste do ModelSoundingWorker (F1) — processa um perfil do modelo sem rede.

Monkeypatcha ``load_model_profile`` para devolver uma coluna tropical sintética e
roda o worker de forma síncrona (``run()``), verificando que entrega um
``SoundingResult`` coerente (com badge de origem, dewpoint ≤ temperatura, índices).
Offscreen; pula se o Qt/MetPy não estiverem disponíveis.
"""

import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("metpy")

import cartomet_br.data.ecmwf as ecmwf_mod
from cartomet_br.data.ecmwf import PL_LEVELS, ModelProfile
from cartomet_br.gui.sounding_engine import ModelSoundingWorker, SoundingResult


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _tropical_profile() -> ModelProfile:
    p = np.array(PL_LEVELS, dtype=float)
    t = 300.0 - (1000.0 - p) * 0.065            # K
    q = np.maximum(1e-6, 0.018 * (p / 1000.0) ** 3)  # kg/kg, úmido embaixo
    u = np.full_like(p, 5.0)
    v = np.full_like(p, -3.0)
    gh = (1000.0 - p) * 8.0
    return ModelProfile(
        lon=-35.0, lat=0.0, grid_lon=-35.0, grid_lat=0.0,
        pressures=p, t=t, q=q, u=u, v=v, gh=gh,
        valid_time="2026-06-14T12:00", base_time="00Z 14/06/2026", cycle=0, step=12,
    )


def test_worker_produz_sounding_result(qapp, monkeypatch):
    monkeypatch.setattr(ecmwf_mod, "load_model_profile", lambda *a, **k: _tropical_profile())

    worker = ModelSoundingWorker(
        lon=-35.0, lat=0.0, target_time=datetime(2026, 6, 14, 12),
        cycle=0, cycle_date="20260614", step=12, data_dir="ignorado",
    )
    out: dict = {}
    worker.finished_ok.connect(lambda r: out.setdefault("ok", r))
    worker.finished_error.connect(lambda m: out.setdefault("err", m))
    worker.run()  # síncrono (mesma thread, sem event loop)

    assert "err" not in out, out.get("err")
    res = out["ok"]
    assert isinstance(res, SoundingResult)
    assert res.source_note.startswith("PSEUDO-SONDAGEM DO MODELO")
    assert res.station["name"] == "Modelo IFS"
    assert res.pressure_q is not None and res.dewpoint_q is not None
    assert res.has_wind

    labels = [lab for lab, _ in res.indices]
    assert "CAPE (SB)" in labels and "LCL" in labels

    # Sanidade física: dewpoint nunca acima da temperatura.
    td = res.dewpoint_q.to("degC").magnitude
    tt = res.temperature_q.to("degC").magnitude
    assert np.all(td <= tt + 1e-6)


def test_worker_emite_erro_quando_perfil_falha(qapp, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("rodada indisponível")
    monkeypatch.setattr(ecmwf_mod, "load_model_profile", _boom)

    worker = ModelSoundingWorker(
        lon=-35.0, lat=0.0, target_time=datetime(2026, 6, 14, 12),
        cycle=0, cycle_date="20260614", step=12, data_dir="ignorado",
    )
    out: dict = {}
    worker.finished_ok.connect(lambda r: out.setdefault("ok", r))
    worker.finished_error.connect(lambda m: out.setdefault("err", m))
    worker.run()

    assert "ok" not in out
    assert "rodada indisponível" in out["err"]
