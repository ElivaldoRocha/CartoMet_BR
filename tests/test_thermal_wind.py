"""Vento térmico / advecção (veering-backing) — camada de dados pura, SEM Qt.

O coração da feature: a classificação da advecção pelo giro do vento com a altura,
**invertida entre hemisférios**. Perfis sintéticos com giro horário (veering) e
anti-horário (backing) confirmam quente/frio em cada hemisfério.
"""

import numpy as np
import pytest

from cartomet_br.data.thermal_wind import (
    STANDARD_LEVELS,
    classify_advection,
    compute_thermal_wind,
)

# Perfil base→topo (pressão decrescente); só os níveis-padrão importam aqui.
_P = np.array([1000.0, 850.0, 700.0, 500.0])

# Giro anti-horário (backing) com a altura: (10,0)→(7,7)→(0,10)→(-7,7).
_U_BACK = np.array([10.0, 7.0, 0.0, -7.0])
_V_BACK = np.array([0.0, 7.0, 10.0, 7.0])

# Giro horário (veering) com a altura: (10,0)→(7,-7)→(0,-10)→(-7,-7).
_U_VEER = np.array([10.0, 7.0, 0.0, -7.0])
_V_VEER = np.array([0.0, -7.0, -10.0, -7.0])


def test_backing_is_warm_in_SH_cold_in_NH():
    sh = compute_thermal_wind(_P, _U_BACK, _V_BACK, 1000, 500, latitude=-20.0)
    assert [ly.advection for ly in sh.layers] == ["warm", "warm", "warm"]
    assert sh.net_advection == "warm"

    nh = compute_thermal_wind(_P, _U_BACK, _V_BACK, 1000, 500, latitude=+20.0)
    assert [ly.advection for ly in nh.layers] == ["cold", "cold", "cold"]
    assert nh.net_advection == "cold"


def test_veering_is_cold_in_SH_warm_in_NH():
    sh = compute_thermal_wind(_P, _U_VEER, _V_VEER, 1000, 500, latitude=-20.0)
    assert [ly.advection for ly in sh.layers] == ["cold", "cold", "cold"]
    assert sh.net_advection == "cold"

    nh = compute_thermal_wind(_P, _U_VEER, _V_VEER, 1000, 500, latitude=+20.0)
    assert all(ly.advection == "warm" for ly in nh.layers)
    assert nh.net_advection == "warm"


def test_parallel_winds_are_neutral():
    u = np.array([10.0, 12.0, 15.0, 18.0])  # só acelera, sem girar
    v = np.zeros(4)
    r = compute_thermal_wind(u * 0 + _P, u, v, 1000, 500, latitude=-20.0)
    assert all(ly.advection == "neutral" for ly in r.layers)
    assert r.net_advection == "neutral"


def test_thermal_vector_is_wind_difference():
    r = compute_thermal_wind(_P, _U_BACK, _V_BACK, 1000, 500, latitude=-20.0)
    first = r.layers[0]  # 1000→850: (7,7)-(10,0) = (-3, 7)
    assert first.p_bottom == 1000 and first.p_top == 850
    assert first.u_thermal == pytest.approx(-3.0)
    assert first.v_thermal == pytest.approx(7.0)
    # Líquido 1000→500: (-7,7)-(10,0) = (-17, 7)
    assert r.net_u_thermal == pytest.approx(-17.0)
    assert r.net_v_thermal == pytest.approx(7.0)


def test_layer_selection_respects_base_and_top():
    # Perfil com todos os níveis-padrão; camada 850→500 exclui 1000 e 300.
    p = np.array([1000.0, 850.0, 700.0, 500.0, 300.0])
    u = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    v = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    r = compute_thermal_wind(p, u, v, 850, 500, latitude=-15.0)
    assert r.levels == [850, 700, 500]
    assert r.u == [2.0, 3.0, 4.0]
    assert len(r.layers) == 2


def test_nan_level_under_terrain_dropped():
    u = _U_BACK.copy()
    v = _V_BACK.copy()
    u[0] = np.nan  # 1000 hPa "sob o relevo"
    r = compute_thermal_wind(_P, u, v, 1000, 500, latitude=-20.0)
    assert r.levels == [850, 700, 500]  # começa em 850


def test_fewer_than_two_levels_raises():
    p = np.array([1000.0, 500.0])
    u = np.array([np.nan, 5.0])  # só 1 nível válido
    v = np.array([np.nan, 5.0])
    with pytest.raises(ValueError):
        compute_thermal_wind(p, u, v, 1000, 500, latitude=-20.0)


def test_classify_advection_neutral_on_calm():
    assert classify_advection(0.0, 0.0, 0.0, 0.0, latitude=-20.0) == "neutral"


def test_standard_levels_descending():
    assert all(a > b for a, b in zip(STANDARD_LEVELS, STANDARD_LEVELS[1:], strict=False))


def test_levels_override_reaches_above_300():
    """Regressão: o diálogo oferece topo até 50 hPa e o worker passa
    ``levels=PL_LEVELS`` — sem o override, STANDARD_LEVELS truncava em 300 hPa
    (camada 1000→200 virava 1000→300 em silêncio) e uma camada alta exclusiva
    (ex.: 300→200) dava ValueError espúrio."""
    p = np.array([1000.0, 500.0, 300.0, 250.0, 200.0])
    u = np.array([10.0, 7.0, 0.0, -7.0, -10.0])
    v = np.array([0.0, 7.0, 10.0, 7.0, 0.0])
    levels = [1000, 500, 300, 250, 200]

    r = compute_thermal_wind(p, u, v, 1000, 200, latitude=-20.0, levels=levels)
    assert r.levels == [1000, 500, 300, 250, 200]  # topo pedido, sem truncar

    r2 = compute_thermal_wind(p, u, v, 300, 200, latitude=-20.0, levels=levels)
    assert r2.levels == [300, 250, 200]  # camada acima de 300 hPa funciona


# --- Render no canvas (offscreen) -------------------------------------------

import os  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")


def _result():
    return compute_thermal_wind(_P, _U_BACK, _V_BACK, 1000, 500, latitude=-20.0, longitude=-45.0)


def test_render_creates_artists_out_of_layout(canvas):
    canvas.render_thermal_wind(_result(), -45.0, -20.0)
    arts = canvas._thermal_wind_artists
    assert len(arts) > 0
    # Overlay de diagnóstico: NÃO dirige a mesa (não pode encolher a carta).
    assert all(a.get_in_layout() is False for a in arts)


def test_render_replaces_previous(canvas):
    canvas.render_thermal_wind(_result(), -45.0, -20.0)
    n1 = len(canvas._thermal_wind_artists)
    canvas.render_thermal_wind(_result(), -50.0, -15.0)  # re-clique substitui
    assert len(canvas._thermal_wind_artists) == n1  # não acumula


def test_toggle_thermal_wind_visibility(canvas):
    """Checkbox da camada no painel: esconde/mostra a hodógrafa sem removê-la."""
    canvas.render_thermal_wind(_result(), -45.0, -20.0)
    arts = canvas._thermal_wind_artists
    canvas.toggle_thermal_wind(False)
    assert arts and all(not a.get_visible() for a in arts)
    canvas.toggle_thermal_wind(True)
    assert all(a.get_visible() for a in arts)


def test_remove_and_clear(canvas):
    canvas.render_thermal_wind(_result(), -45.0, -20.0)
    canvas.remove_thermal_wind()
    assert canvas._thermal_wind_artists == []
    canvas.render_thermal_wind(_result(), -45.0, -20.0)
    canvas.clear_map()
    assert canvas._thermal_wind_artists == []


def test_set_mode_toggles_interaction(canvas):
    canvas.set_thermal_wind_mode(True)
    assert canvas.interaction_mode == "thermal_wind"
    canvas.set_thermal_wind_mode(False)
    assert canvas.interaction_mode is None


# --- Regressões dos problemas reportados ------------------------------------


def test_clear_sounding_marker_survives_stale(canvas):
    """Marcador *stale* (``_remove_method=None``) não pode lançar (crash do Limpar)."""
    canvas._mark_sounding_point(-47.0, -22.0, color="#D35400")
    canvas._sounding_marker._remove_method = None  # simula artista já destacado
    canvas.clear_sounding_marker()  # antes: NotImplementedError propagava
    assert canvas._sounding_marker is None


def test_clear_map_with_stale_marker_no_crash(canvas):
    """`clear_map` com estrela *stale* não pode fechar o app."""
    canvas._mark_sounding_point(-47.0, -22.0, color="#D35400")
    canvas._sounding_marker._remove_method = None
    canvas.clear_map()  # não deve levantar
    assert canvas._sounding_marker is None


def test_toggle_thermal_wind_off_clears_marker(canvas):
    """Desativar a ferramenta some com a estrela na carta."""
    canvas.set_thermal_wind_mode(True)
    canvas._mark_sounding_point(-47.0, -22.0, color="#D35400")
    assert canvas._sounding_marker is not None
    canvas.set_thermal_wind_mode(False)
    assert canvas._sounding_marker is None


@pytest.mark.parametrize(
    "setter", ["set_meteogram_mode", "set_wind_rose_mode", "set_era5_series_mode"]
)
def test_toggle_other_point_modes_off_clears_marker(canvas, setter):
    """Meteograma/Rosa/Série ERA5 plantam a mesma estrela — desligar o modo
    também tem que sumir com ela (regressão: só Sonda/Vento Térmico limpavam)."""
    getattr(canvas, setter)(True)
    canvas._mark_sounding_point(-47.0, -22.0, color="#8E44AD")
    assert canvas._sounding_marker is not None
    getattr(canvas, setter)(False)
    assert canvas._sounding_marker is None
