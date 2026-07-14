"""Ímã de vértices entre frentes (aderência) — offscreen.

Ao desenhar uma frente, o clique perto de um vértice CRU de outra frente adere
à coordenada exata (junção bit a bit, como nas cartas sinóticas oficiais). O
raio de captura é em PIXELS de tela; só frentes participam (origem e alvo); o
snap opera nos comandos do histórico (nunca na spline interpolada do artista).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from cartomet_br.core.config import EXTENT_BRASIL, Config


class _FakeEvent:
    """Evento de mouse mínimo (inaxes/button/xdata/ydata/x/y) p/ _on_click/_on_motion."""

    def __init__(self, canvas, lon, lat, button=1):
        self.inaxes = canvas.ax
        self.button = button
        self.xdata = lon
        self.ydata = lat
        self.x, self.y = canvas.ax.transData.transform((lon, lat))
        self.dblclick = False


def _event_near_pixel(canvas, lon, lat, dx_px, dy_px):
    """Evento a (dx, dy) PIXELS do ponto (lon, lat) — robusto a qualquer zoom."""
    px, py = canvas.ax.transData.transform((lon, lat))
    nlon, nlat = canvas.ax.transData.inverted().transform((px + dx_px, py + dy_px))
    return _FakeEvent(canvas, float(nlon), float(nlat))


def _draw_front(canvas, xs, ys, symbol="1"):
    """Traça uma frente pelo fluxo real (vértices → preview → finalize)."""
    canvas.current_symbol = symbol
    canvas.points_x.extend(xs)
    canvas.points_y.extend(ys)
    canvas._update_preview()
    canvas.finalize_line()


_COLD_XS = [-50.0, -46.0, -42.0]
_COLD_YS = [-30.0, -26.0, -24.0]


def test_snap_candidates_only_fronts(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS, symbol="1")  # frente fria
    _draw_front(canvas, [-60.0, -55.0], [-10.0, -12.0], symbol="5")  # ZCAS: fora
    cands = canvas._snap_candidates()
    assert cands == list(zip(_COLD_XS, _COLD_YS, strict=True))


def test_snap_click_exact_vertex(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    canvas.interaction_mode = "draw"
    canvas.current_symbol = "3"  # frente estacionária nascendo na fria

    # Clique a ~6 px do vértice INTERMEDIÁRIO → adere à coordenada exata
    ev = _event_near_pixel(canvas, _COLD_XS[1], _COLD_YS[1], 4, 4)
    canvas._on_click(ev)
    assert canvas.points_x[-1] == _COLD_XS[1]
    assert canvas.points_y[-1] == _COLD_YS[1]

    # Clique longe de qualquer vértice → coordenada do clique intacta
    ev2 = _FakeEvent(canvas, -70.0, -5.0)
    canvas._on_click(ev2)
    assert canvas.points_x[-1] == -70.0
    assert canvas.points_y[-1] == -5.0


def test_snap_radius_is_in_pixels(canvas):
    """O mesmo delta em GRAUS adere ou não conforme o zoom (raio é de tela)."""
    import cartopy.crs as ccrs

    _draw_front(canvas, _COLD_XS, _COLD_YS)
    delta = 0.5  # graus — o MESMO delta nos dois zooms

    # Zoom apertado (12° de vão): 0.5° são dezenas de pixels → FORA do raio
    canvas.ax.set_extent([-52, -40, -32, -22], crs=ccrs.PlateCarree())
    ev = _FakeEvent(canvas, _COLD_XS[0] + delta, _COLD_YS[0])
    assert canvas._find_snap_target(ev.x, ev.y) is None

    # Zoom aberto (140° de vão): os mesmos 0.5° viram ~2 px → DENTRO do raio
    canvas.ax.set_extent([-120, 20, -70, 20], crs=ccrs.PlateCarree())
    ev = _FakeEvent(canvas, _COLD_XS[0] + delta, _COLD_YS[0])
    assert canvas._find_snap_target(ev.x, ev.y) == (_COLD_XS[0], _COLD_YS[0])


def test_snap_ignores_undone_line(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    canvas.undo_line()
    assert canvas._snap_candidates() == []
    canvas.redo_action()
    assert canvas._snap_candidates() == list(zip(_COLD_XS, _COLD_YS, strict=True))


def test_snap_ignores_hidden_symbology(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    ev = _FakeEvent(canvas, _COLD_XS[0], _COLD_YS[0])
    canvas.set_drawings_visible("symbology", False)
    assert canvas._find_snap_target(ev.x, ev.y) is None  # não adere ao invisível
    canvas.set_drawings_visible("symbology", True)
    assert canvas._find_snap_target(ev.x, ev.y) == (_COLD_XS[0], _COLD_YS[0])


def test_snap_disabled_flag(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    canvas.interaction_mode = "draw"
    canvas.current_symbol = "2"
    canvas.set_snap_enabled(False)
    lon, lat = _COLD_XS[1] + 0.1, _COLD_YS[1] + 0.1
    canvas._on_click(_FakeEvent(canvas, lon, lat))
    assert canvas.points_x[-1] == lon  # clique livre, sem aderir
    assert canvas._snap_marker is None


def test_nonfront_origin_does_not_snap(canvas):
    """Desenhando ZCAS (não-frente), o clique NÃO adere mesmo com frente perto."""
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    canvas.interaction_mode = "draw"
    canvas.current_symbol = "5"
    lon, lat = _COLD_XS[1] + 0.1, _COLD_YS[1] + 0.1
    canvas._on_click(_FakeEvent(canvas, lon, lat))
    assert canvas.points_x[-1] == lon


def test_snap_survives_cmbr_roundtrip(canvas, qapp, tmp_path):
    """Estacionária aderida ao fim da fria: o vértice compartilhado permanece
    idêntico entre os records após export → import num canvas novo."""
    from cartomet_br.gui.map_canvas import MapCanvas

    _draw_front(canvas, _COLD_XS, _COLD_YS, symbol="1")
    canvas.interaction_mode = "draw"
    canvas.current_symbol = "3"
    canvas._on_click(_event_near_pixel(canvas, _COLD_XS[-1], _COLD_YS[-1], 4, 3))
    canvas._on_click(_FakeEvent(canvas, -38.0, -22.0))
    canvas.finalize_line()

    records = canvas.export_drawings_state()
    data_dir = tmp_path / "d2"
    out_dir = tmp_path / "o2"
    data_dir.mkdir()
    out_dir.mkdir()
    cfg = Config(extent=EXTENT_BRASIL.copy(), data_dir=data_dir, output_dir=out_dir)
    other = MapCanvas(config=cfg)
    other.import_drawings_state(records)

    fronts = [c for c in other.history.commands if getattr(c, "symbol_key", None) in ("1", "3")]
    cold = next(c for c in fronts if c.symbol_key == "1")
    stat = next(c for c in fronts if c.symbol_key == "3")
    assert stat.points_x[0] == cold.points_x[-1]  # junção exata sobrevive
    assert stat.points_y[0] == cold.points_y[-1]


def test_undo_target_after_junction(canvas):
    """Undo da frente-alvo depois da junção: a aderida mantém o vértice (cópia
    por valor) e nada quebra."""
    _draw_front(canvas, _COLD_XS, _COLD_YS, symbol="1")
    canvas.interaction_mode = "draw"
    canvas.current_symbol = "3"
    canvas._on_click(_event_near_pixel(canvas, _COLD_XS[-1], _COLD_YS[-1], 4, 3))
    canvas._on_click(_FakeEvent(canvas, -38.0, -22.0))
    canvas.finalize_line()

    canvas.undo_line()  # remove a estacionária
    canvas.undo_line()  # remove a fria (o alvo)
    canvas.redo_action()  # refaz a fria
    canvas.redo_action()  # refaz a estacionária
    stat = canvas.history.commands[-1]
    assert stat.points_x[0] == _COLD_XS[-1]
    assert stat.points_y[0] == _COLD_YS[-1]


def test_hover_marker_lifecycle(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    canvas.interaction_mode = "draw"
    canvas.current_symbol = "2"

    canvas._on_motion(_event_near_pixel(canvas, _COLD_XS[1], _COLD_YS[1], 4, 3))
    assert canvas._snap_marker is not None  # anel aceso no candidato
    assert canvas._snap_hover_xy == (_COLD_XS[1], _COLD_YS[1])

    canvas._on_motion(_FakeEvent(canvas, -70.0, -5.0))  # longe → apaga
    assert canvas._snap_marker is None
    assert canvas._snap_hover_xy is None

    canvas._on_motion(_event_near_pixel(canvas, _COLD_XS[0], _COLD_YS[0], -4, 0))
    assert canvas._snap_hover_xy == (_COLD_XS[0], _COLD_YS[0])
    canvas.clear_all()
    assert canvas._snap_marker is None
    assert canvas._snap_hover_xy is None


def test_panel_snap_checkbox(qapp):
    """Painel: ímã ligado por default; toggle emite snap_toggled(False/True)."""
    from cartomet_br.gui.drawing_panel import SymbologyPanel

    panel = SymbologyPanel()
    assert panel.snap_check.isChecked()
    got: list = []
    panel.snap_toggled.connect(got.append)
    panel.snap_check.setChecked(False)
    panel._toggle_snap()  # atalho [I] re-liga
    assert got == [False, True]
