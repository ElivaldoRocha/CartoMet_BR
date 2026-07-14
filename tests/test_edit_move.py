"""Modo edição — mover desenhos por arraste (Fase 2, offscreen).

Press arma o arraste; abaixo de EDIT_DRAG_MIN_PIXELS é clique (no-op de
movimento); acima, um fantasma tracejado segue o mouse e o commit (MoveOp,
cópias completas de geometria) acontece no release. Undo restaura coordenadas
bit-exatas; Esc aborta sem commit.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from cartomet_br.core.config import EXTENT_BRASIL, Config


class _FakeEvent:
    def __init__(self, canvas, lon, lat, button=1):
        self.inaxes = canvas.ax
        self.button = button
        self.xdata = lon
        self.ydata = lat
        self.x, self.y = canvas.ax.transData.transform((lon, lat))
        self.dblclick = False


def _event_near_pixel(canvas, lon, lat, dx_px, dy_px):
    px, py = canvas.ax.transData.transform((lon, lat))
    nlon, nlat = canvas.ax.transData.inverted().transform((px + dx_px, py + dy_px))
    return _FakeEvent(canvas, float(nlon), float(nlat))


def _draw_front(canvas, xs, ys, symbol="1"):
    canvas.current_symbol = symbol
    canvas.points_x.extend(xs)
    canvas.points_y.extend(ys)
    canvas._update_preview()
    canvas.finalize_line()


_COLD_XS = [-50.0, -44.0, -38.0]
_COLD_YS = [-30.0, -24.0, -22.0]


def _drag(canvas, press_ev, dx_px, dy_px):
    """Fluxo press → motion → release; devolve o delta lon/lat do motion."""
    canvas._on_click(press_ev)
    motion = _event_near_pixel(canvas, press_ev.xdata, press_ev.ydata, dx_px, dy_px)
    canvas._on_motion(motion)
    dx = motion.xdata - press_ev.xdata
    dy = motion.ydata - press_ev.ydata
    canvas._on_release(motion)
    return dx, dy


def test_drag_moves_all_vertices_by_exact_delta(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    cmd = canvas.history.commands[0]
    canvas.set_edit_mode(True)

    press = _event_near_pixel(canvas, _COLD_XS[1], _COLD_YS[1], 2, 2)
    dx, dy = _drag(canvas, press, 40, -25)

    assert cmd.points_x == [v + dx for v in _COLD_XS]
    assert cmd.points_y == [v + dy for v in _COLD_YS]
    assert cmd.artist is not None  # re-renderizado na posição nova
    assert canvas._edit_selected is cmd  # halo re-aceso
    assert canvas._edit_highlight
    assert canvas._edit_ghost is None


def test_subthreshold_drag_is_selection_click(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    cmd = canvas.history.commands[0]
    canvas.set_edit_mode(True)

    press = _event_near_pixel(canvas, _COLD_XS[0], _COLD_YS[0], 1, 1)
    _drag(canvas, press, 1, 1)  # abaixo de EDIT_DRAG_MIN_PIXELS

    assert cmd.points_x == _COLD_XS  # geometria intacta
    assert canvas._edit_selected is cmd
    assert canvas.history.undo_count == 1  # só a criação — sem MoveOp


def test_undo_restores_bit_exact_coords(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    cmd = canvas.history.commands[0]
    canvas.set_edit_mode(True)

    press = _event_near_pixel(canvas, _COLD_XS[1], _COLD_YS[1], 2, 2)
    dx, dy = _drag(canvas, press, 35, 20)
    moved_x = list(cmd.points_x)

    canvas.undo_line()  # desfaz o movimento
    assert cmd.points_x == _COLD_XS  # cópia completa → bit-exato
    assert cmd.points_y == _COLD_YS
    canvas.redo_action()  # refaz
    assert cmd.points_x == moved_x

    canvas.undo_line()  # desfaz movimento de novo
    canvas.undo_line()  # desfaz a criação
    assert canvas.history.commands == []


def test_move_emoji_updates_parallel_lists(canvas):
    canvas.add_emoji(-50.0, -10.0, "⛈", 28)
    cmd = canvas._emoji_records[0]
    canvas.set_edit_mode(True)

    press = _event_near_pixel(canvas, -50.0, -10.0, 3, 3)
    dx, dy = _drag(canvas, press, 50, 30)

    # O delta aplica-se à GEOMETRIA do comando (âncora original), não ao press.
    assert cmd.x == -50.0 + dx
    assert cmd.y == -10.0 + dy
    assert canvas._emoji_records == [cmd]
    assert len(canvas._emoji_annotations) == 1
    assert cmd.artist is canvas._emoji_annotations[0]

    canvas.undo_line()
    assert cmd.x == -50.0  # âncora original, bit-exata
    assert cmd.y == -10.0


def test_ghost_lifecycle_and_visibility(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    cmd = canvas.history.commands[0]
    canvas.set_edit_mode(True)

    press = _event_near_pixel(canvas, _COLD_XS[1], _COLD_YS[1], 2, 2)
    canvas._on_click(press)
    motion = _event_near_pixel(canvas, press.xdata, press.ydata, 30, 10)
    canvas._on_motion(motion)
    assert canvas._edit_ghost is not None  # fantasma aceso
    assert not cmd.artist.get_visible()  # original escondido

    canvas._on_release(motion)
    assert canvas._edit_ghost is None
    assert cmd.artist.get_visible()  # artista novo, visível


def test_escape_cancels_drag_without_commit(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    cmd = canvas.history.commands[0]
    canvas.set_edit_mode(True)

    press = _event_near_pixel(canvas, _COLD_XS[1], _COLD_YS[1], 2, 2)
    canvas._on_click(press)
    canvas._on_motion(_event_near_pixel(canvas, press.xdata, press.ydata, 40, 15))
    canvas.clear_edit_selection()  # Esc no meio do arraste

    assert cmd.points_x == _COLD_XS  # sem commit
    assert cmd.artist.get_visible()  # original de volta
    assert canvas._edit_ghost is None
    assert canvas.history.undo_count == 1  # sem MoveOp


def test_move_survives_cmbr_roundtrip(canvas, qapp, tmp_path):
    from cartomet_br.gui.map_canvas import MapCanvas

    _draw_front(canvas, _COLD_XS, _COLD_YS)
    cmd = canvas.history.commands[0]
    canvas.set_edit_mode(True)
    press = _event_near_pixel(canvas, _COLD_XS[1], _COLD_YS[1], 2, 2)
    _drag(canvas, press, 40, -25)

    records = canvas.export_drawings_state()
    data_dir = tmp_path / "d2"
    out_dir = tmp_path / "o2"
    data_dir.mkdir()
    out_dir.mkdir()
    cfg = Config(extent=EXTENT_BRASIL.copy(), data_dir=data_dir, output_dir=out_dir)
    other = MapCanvas(config=cfg)
    other.import_drawings_state(records)
    reaberto = other.history.commands[0]
    assert reaberto.points_x == cmd.points_x  # geometria movida persiste
    assert reaberto.points_y == cmd.points_y
