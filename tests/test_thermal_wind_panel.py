"""Painel do Vento Térmico (dinâmica da rosa) + renderer de janela — offscreen.

O clique passa a abrir o dock com a hodógrafa desenhada num Axes comum
(``render_hodograph``); "📌 Fixar no mapa" emite o payload para ancorar no
canvas e "Remover do mapa" tira a fixada. O warp radial compartilhado preserva
a direção de cada vetor (giro intacto).
"""

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from cartomet_br.charts.hodograph_plot import (
    render_hodograph,
    ring_increment,
    warp_components,
    warp_radius,
)
from cartomet_br.data.thermal_wind import ThermalWindLayer, ThermalWindResult


def _result() -> ThermalWindResult:
    return ThermalWindResult(
        latitude=-20.0,
        longitude=-45.0,
        levels=[1000, 850, 500],
        u=[2.0, 6.0, 15.0],
        v=[1.0, -3.0, 8.0],
        layers=[
            ThermalWindLayer(1000, 850, 4.0, -4.0, "warm"),
            ThermalWindLayer(850, 500, 9.0, 11.0, "cold"),
        ],
        net_u_thermal=13.0,
        net_v_thermal=7.0,
        net_advection="warm",
    )


# ── Renderer puro (sem Qt) ──────────────────────────────────────────────────


def test_warp_preserves_direction():
    us = np.array([3.0, -10.0, 0.5])
    vs = np.array([4.0, 2.0, -0.5])
    wu, wv = warp_components(us, vs)
    for i in range(3):
        assert math.atan2(vs[i], us[i]) == pytest.approx(math.atan2(wv[i], wu[i]))
        assert math.hypot(wu[i], wv[i]) == pytest.approx(math.hypot(us[i], vs[i]) ** 0.5)


def test_ring_increment_rounds():
    assert ring_increment(12.0) == 5.0
    assert ring_increment(30.0) == 10.0
    assert ring_increment(999.0) == 100.0
    assert warp_radius(25.0) == pytest.approx(5.0)


def test_render_hodograph_plain_axes():
    from matplotlib.figure import Figure

    fig = Figure()
    ax = fig.add_subplot(111)
    render_hodograph(ax, _result())
    assert len(ax.lines) > 5  # anéis + setas + segmentos + vértices
    assert len(ax.patches) >= 1  # disco de fundo / cabeças de seta
    assert any("kt" in t.get_text() for t in ax.texts)  # rótulo do anel externo
    assert any("1000→500" in t.get_text() for t in ax.texts)  # legenda


def test_render_hodograph_insufficient_levels():
    from matplotlib.figure import Figure

    fig = Figure()
    ax = fig.add_subplot(111)
    thin = ThermalWindResult(
        latitude=-20.0,
        longitude=-45.0,
        levels=[1000],
        u=[2.0],
        v=[1.0],
        layers=[],
        net_u_thermal=0.0,
        net_v_thermal=0.0,
        net_advection="neutral",
    )
    render_hodograph(ax, thin)  # não pode explodir
    assert any("insuficientes" in t.get_text() for t in ax.texts)


# ── Painel (Qt offscreen) ───────────────────────────────────────────────────


def test_panel_render_and_controls(qapp):
    from cartomet_br.gui.thermal_wind_panel import ThermalWindPanel

    panel = ThermalWindPanel()
    assert panel._controls_row.isHidden()  # controles só aparecem com dados
    panel.render(_result())
    assert not panel._controls_row.isHidden()
    assert "1000→500" in panel._header.text()
    assert "20.0°S" in panel._header.text()


def test_panel_pin_payload(qapp):
    from cartomet_br.gui.thermal_wind_panel import ThermalWindPanel

    panel = ThermalWindPanel()
    got: list = []
    panel.pin_requested.connect(got.append)
    panel.pin_btn.click()
    assert got == []  # sem resultado → não emite

    result = _result()
    panel.render(result)
    panel.pin_btn.click()
    assert len(got) == 1
    assert got[0]["result"] is result
    assert got[0]["lon"] == -45.0
    assert got[0]["lat"] == -20.0


def test_panel_unpin_and_layer_signals(qapp):
    from cartomet_br.gui.thermal_wind_panel import ThermalWindPanel

    panel = ThermalWindPanel()
    unpins: list = []
    configs: list = []
    panel.unpin_requested.connect(lambda: unpins.append(True))
    panel.layer_config_requested.connect(lambda: configs.append(True))
    panel.render(_result())
    panel.unpin_btn.click()
    panel.layer_btn.click()
    assert unpins == [True]
    assert configs == [True]

    panel.set_layer_label(850, 300)
    assert panel.layer_label.text() == "Camada: 850→300 hPa"


def test_level_dialog_preselects_layer(qapp):
    from cartomet_br.gui.dialogs import ThermalWindLevelDialog

    dlg = ThermalWindLevelDialog([1000, 925, 850, 700, 500, 300], default_base=850, default_top=300)
    assert dlg.selected_layer() == (850, 300)
