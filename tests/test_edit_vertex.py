"""Modo edição — arrastar vértices individuais (Fase 3, offscreen).

Handles (quadradinhos) aparecem nos vértices crus do desenho selecionado
(linhas OMM e formas; caneta e pontuais ficam de fora). Pegar um handle tem
prioridade sobre mover o desenho; arrastando vértice de FRENTE, o ímã adere a
outra frente (nunca à própria) — junção exata na correção. Undo é bit-exato.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")


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


def _select(canvas, cmd):
    canvas.set_edit_mode(True)
    canvas._select_command(cmd)


def _drag_vertex(canvas, lon, lat, to_lon, to_lat):
    """Press no handle em (lon,lat) → motion até (to_lon,to_lat) → release."""
    canvas._on_click(_event_near_pixel(canvas, lon, lat, 1, 1))
    motion = _FakeEvent(canvas, to_lon, to_lat)
    canvas._on_motion(motion)
    canvas._on_release(motion)


def test_drag_moves_only_that_vertex(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    cmd = canvas.history.commands[0]
    _select(canvas, cmd)

    _drag_vertex(canvas, _COLD_XS[1], _COLD_YS[1], -42.0, -26.5)
    assert cmd.points_x == [_COLD_XS[0], -42.0, _COLD_XS[2]]
    assert cmd.points_y == [_COLD_YS[0], -26.5, _COLD_YS[2]]
    assert cmd.artist is not None  # re-renderizado
    assert canvas._edit_selected is cmd  # handles/halo re-acesos


def test_vertex_undo_is_bit_exact(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    cmd = canvas.history.commands[0]
    _select(canvas, cmd)

    _drag_vertex(canvas, _COLD_XS[0], _COLD_YS[0], -52.5, -31.0)
    assert cmd.points_x[0] == -52.5

    canvas.undo_line()
    assert cmd.points_x == _COLD_XS  # cópia por valor → bit-exato
    assert cmd.points_y == _COLD_YS
    canvas.redo_action()
    assert cmd.points_x[0] == -52.5


def test_vertex_snaps_to_other_front_not_itself(canvas):
    """Arrastar o fim da estacionária até perto da fria → adere ao vértice EXATO."""
    _draw_front(canvas, _COLD_XS, _COLD_YS, symbol="1")  # fria (alvo)
    _draw_front(canvas, [-60.0, -55.0], [-15.0, -18.0], symbol="3")  # estacionária
    stat = canvas.history.commands[-1]
    _select(canvas, stat)

    # Motion até ~4 px do último vértice da fria → ímã gruda na coordenada exata
    canvas._on_click(_event_near_pixel(canvas, -55.0, -18.0, 1, 1))
    motion = _event_near_pixel(canvas, _COLD_XS[-1], _COLD_YS[-1], 4, 3)
    canvas._on_motion(motion)
    canvas._on_release(motion)
    assert stat.points_x[-1] == _COLD_XS[-1]
    assert stat.points_y[-1] == _COLD_YS[-1]


def test_vertex_does_not_snap_to_own_front(canvas):
    """O vértice arrastado não pode aderir a OUTRO vértice da mesma frente."""
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    cmd = canvas.history.commands[0]
    _select(canvas, cmd)

    # Arrasta o vértice do meio até ~4 px do PRIMEIRO vértice da própria frente
    canvas._on_click(_event_near_pixel(canvas, _COLD_XS[1], _COLD_YS[1], 1, 1))
    motion = _event_near_pixel(canvas, _COLD_XS[0], _COLD_YS[0], 4, 3)
    canvas._on_motion(motion)
    canvas._on_release(motion)
    # Sem ímã: fica na coordenada do mouse, não colapsa no vértice vizinho
    assert cmd.points_x[1] == motion.xdata
    assert cmd.points_x[1] != _COLD_XS[0]


def test_shape_diagonal_vertex(canvas):
    """Retângulo: arrastar um extremo da diagonal redimensiona a forma."""
    from cartomet_br.gui.draw_tools import DrawStyle, ShapeCommand

    cmd = ShapeCommand(
        tool="rect",
        points_x=[-50.0, -40.0],
        points_y=[-25.0, -15.0],
        style=DrawStyle().to_dict(),
    )
    canvas._rebuild_artist(cmd)
    canvas.history.push(cmd)
    _select(canvas, cmd)

    _drag_vertex(canvas, -40.0, -15.0, -35.0, -10.0)
    assert cmd.points_x == [-50.0, -35.0]
    assert cmd.points_y == [-25.0, -10.0]


def test_pen_has_no_handles(canvas):
    from cartomet_br.gui.draw_tools import DrawStyle, PenCommand

    cmd = PenCommand(
        points_x=[-50.0, -49.5, -49.0],
        points_y=[-20.0, -20.2, -20.4],
        style=DrawStyle().to_dict(),
    )
    canvas._rebuild_artist(cmd)
    canvas.history.push(cmd)
    _select(canvas, cmd)

    # Press exatamente sobre um ponto do traço NÃO vira arraste de vértice
    canvas._on_click(_event_near_pixel(canvas, -49.5, -20.2, 1, 1))
    assert canvas._edit_vertex_index is None
    assert canvas._edit_drag_cmd is cmd  # vira arraste de MOVER, não de vértice
    canvas._on_release(_FakeEvent(canvas, -49.5, -20.2))


def test_escape_cancels_vertex_drag(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    cmd = canvas.history.commands[0]
    _select(canvas, cmd)

    canvas._on_click(_event_near_pixel(canvas, _COLD_XS[1], _COLD_YS[1], 1, 1))
    canvas._on_motion(_FakeEvent(canvas, -42.0, -26.0))
    assert canvas._edit_ghost is not None
    canvas.clear_edit_selection()  # Esc

    assert cmd.points_x == _COLD_XS  # sem commit
    assert canvas._edit_ghost is None
    assert cmd.artist.get_visible()
    assert canvas.history.undo_count == 1  # só a criação
