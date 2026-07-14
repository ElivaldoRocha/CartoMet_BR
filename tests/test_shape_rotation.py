"""Rotação de formas pela alça do modo edição (offscreen).

rect/ellipse giram via campo ``rotation_deg`` (anel girado ao redor do meio da
diagonal; RotateOp no undo); line/arrow/polygon têm a rotação ASSADA nos
vértices (MoveOp de cópias). A alça ○ fica acima da forma selecionada; Shift
trava em passos de 15°; Esc aborta sem commit.
"""

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from cartomet_br.core.config import EXTENT_BRASIL, Config
from cartomet_br.gui.draw_tools import (
    DrawStyle,
    ShapeCommand,
    rotate_points,
    shape_rotation_center,
)


class _FakeEvent:
    def __init__(self, canvas, lon, lat, button=1, key=None):
        self.inaxes = canvas.ax
        self.button = button
        self.xdata = lon
        self.ydata = lat
        self.x, self.y = canvas.ax.transData.transform((lon, lat))
        self.dblclick = False
        self.key = key


def _add_shape(canvas, tool, xs, ys, **kw):
    cmd = ShapeCommand(
        tool=tool, points_x=list(xs), points_y=list(ys), style=DrawStyle().to_dict(), **kw
    )
    canvas._rebuild_artist(cmd)
    canvas.history.push(cmd)
    return cmd


def _select(canvas, cmd):
    canvas.set_edit_mode(True)
    canvas._select_command(cmd)


def _rotate_via_handle(canvas, cmd, delta_deg, key=None):
    """Press na alça → motion no ângulo desejado ao redor do centro → release."""
    assert canvas._edit_rotate_handle_xy is not None
    hx, hy = canvas._edit_rotate_handle_xy
    canvas._on_click(_FakeEvent(canvas, hx, hy))
    assert canvas._edit_rotate_start_deg is not None  # alça armou rotação

    cx, cy = shape_rotation_center(cmd)
    r = math.hypot(hx - cx, hy - cy)
    a0 = math.atan2(hy - cy, hx - cx)
    a1 = a0 + math.radians(delta_deg)
    motion = _FakeEvent(canvas, cx + r * math.cos(a1), cy + r * math.sin(a1), key=key)
    canvas._on_motion(motion)
    canvas._on_release(motion)


def test_rotate_points_90_around_center():
    xs, ys = rotate_points([1.0, 0.0], [0.0, 0.0], 0.0, 0.0, 90.0)
    assert xs[0] == pytest.approx(0.0, abs=1e-12)
    assert ys[0] == pytest.approx(1.0)
    assert (xs[1], ys[1]) == (0.0, 0.0)  # o centro fica parado


def test_rotation_centers():
    rect = ShapeCommand(tool="rect", points_x=[-50.0, -40.0], points_y=[-25.0, -15.0], style={})
    assert shape_rotation_center(rect) == (-45.0, -20.0)  # meio da diagonal
    poly = ShapeCommand(
        tool="polygon", points_x=[0.0, 6.0, 3.0], points_y=[0.0, 0.0, 3.0], style={}
    )
    assert shape_rotation_center(poly) == (3.0, 1.0)  # média dos vértices


def test_ellipse_rotated_90_swaps_bbox(canvas):
    """Elipse 10°×4° girada 90°: o bbox do hit vira 4°×10° (gira ao redor do centro)."""
    from cartomet_br.gui.edit_tools import hit_geometry

    cmd = _add_shape(canvas, "ellipse", [-50.0, -40.0], [-22.0, -18.0])
    _select(canvas, cmd)
    _rotate_via_handle(canvas, cmd, 90.0)

    assert cmd.rotation_deg == pytest.approx(90.0, abs=1.0)
    xs, ys = hit_geometry(cmd)
    assert max(xs) - min(xs) == pytest.approx(4.0, abs=0.2)  # largura ↔ altura
    assert max(ys) - min(ys) == pytest.approx(10.0, abs=0.2)
    assert (min(xs) + max(xs)) / 2 == pytest.approx(-45.0, abs=0.05)  # centro parado
    assert (min(ys) + max(ys)) / 2 == pytest.approx(-20.0, abs=0.05)
    # A diagonal crua permanece intocada (rotação NÃO assada em rect/ellipse)
    assert cmd.points_x == [-50.0, -40.0]


def test_rotated_ellipse_hit_follows_rotation(canvas):
    """Depois de girar ~90°, o hit acerta a borda NOVA e erra a borda antiga."""
    cmd = _add_shape(canvas, "ellipse", [-50.0, -40.0], [-22.0, -18.0])
    _select(canvas, cmd)
    _rotate_via_handle(canvas, cmd, 90.0)
    canvas.clear_edit_selection()

    # Borda nova (acima do centro, a ~5° — a semi-largura virou semi-altura)
    ev = _FakeEvent(canvas, -45.0, -20.0 + 4.9)
    canvas._on_click(ev)
    canvas._on_release(ev)
    assert canvas._edit_selected is cmd

    canvas.clear_edit_selection()
    # Borda antiga (à esquerda a ~5° do centro — hoje céu limpo)
    ev2 = _FakeEvent(canvas, -50.0, -20.0)
    canvas._on_click(ev2)
    assert canvas._edit_selected is None


def test_rotate_undo_redo_bit_exact(canvas):
    cmd = _add_shape(canvas, "rect", [-50.0, -40.0], [-25.0, -15.0])
    _select(canvas, cmd)
    _rotate_via_handle(canvas, cmd, 35.0)
    rotated = cmd.rotation_deg
    assert rotated != 0.0

    canvas.undo_line()
    assert cmd.rotation_deg == 0.0  # bit-exato
    canvas.redo_action()
    assert cmd.rotation_deg == rotated


def test_polygon_rotation_is_baked_and_keeps_handles(canvas):
    cmd = _add_shape(canvas, "polygon", [-50.0, -44.0, -47.0], [-25.0, -25.0, -20.0])
    orig_x = list(cmd.points_x)
    orig_y = list(cmd.points_y)
    cx, cy = shape_rotation_center(cmd)
    _select(canvas, cmd)
    n_handles_before = len(canvas._edit_highlight)
    _rotate_via_handle(canvas, cmd, 60.0)

    assert cmd.rotation_deg == 0.0  # assada nos vértices, não no campo
    assert cmd.points_x != orig_x
    # O centro não se move com a rotação
    ncx, ncy = shape_rotation_center(cmd)
    assert ncx == pytest.approx(cx)
    assert ncy == pytest.approx(cy)
    # Handles de vértice continuam expostos após a rotação assada
    assert len(canvas._edit_highlight) == n_handles_before

    canvas.undo_line()  # MoveOp de cópias → bit-exato
    assert cmd.points_x == orig_x
    assert cmd.points_y == orig_y


def test_rotated_rect_hides_vertex_handles(canvas):
    cmd = _add_shape(canvas, "rect", [-50.0, -40.0], [-25.0, -15.0])
    _select(canvas, cmd)
    with_handles = len(canvas._edit_highlight)  # halo + quadradinhos + alça
    _rotate_via_handle(canvas, cmd, 30.0)
    assert len(canvas._edit_highlight) == with_handles - 1  # sem quadradinhos
    # E o press num canto cru não arma arraste de vértice
    assert canvas._find_vertex_handle(cmd, *canvas.ax.transData.transform((-50.0, -25.0))) is None

    canvas.undo_line()  # rotação desfeita → handles voltam
    canvas._select_command(cmd)
    assert len(canvas._edit_highlight) == with_handles


def test_arrow_rotation_bakes_endpoints(canvas):
    cmd = _add_shape(canvas, "arrow", [-50.0, -40.0], [-20.0, -20.0], head_size_deg=1.0)
    _select(canvas, cmd)
    _rotate_via_handle(canvas, cmd, 90.0)
    # Extremos girados 90° ao redor do centro (-45, -20): seta fica vertical
    assert cmd.points_x[0] == pytest.approx(-45.0, abs=0.2)
    assert cmd.points_y[0] == pytest.approx(-25.0, abs=0.2)
    assert cmd.points_x[-1] == pytest.approx(-45.0, abs=0.2)
    assert cmd.points_y[-1] == pytest.approx(-15.0, abs=0.2)
    assert cmd.artist is not None  # ponta reconstruída no novo eixo


def test_shift_snaps_to_15_degrees(canvas):
    cmd = _add_shape(canvas, "rect", [-50.0, -40.0], [-25.0, -15.0])
    _select(canvas, cmd)
    _rotate_via_handle(canvas, cmd, 22.0, key="shift")
    assert cmd.rotation_deg == pytest.approx(15.0)  # 22° trava no passo de 15°


def test_rotation_survives_cmbr_roundtrip(canvas, qapp, tmp_path):
    from cartomet_br.gui import project_io
    from cartomet_br.gui.map_canvas import MapCanvas

    _add_shape(canvas, "ellipse", [-50.0, -40.0], [-22.0, -18.0], rotation_deg=35.0)
    records = canvas.export_drawings_state()
    assert records[0]["rotation_deg"] == 35.0

    data_dir = tmp_path / "d2"
    out_dir = tmp_path / "o2"
    data_dir.mkdir()
    out_dir.mkdir()
    cfg = Config(extent=EXTENT_BRASIL.copy(), data_dir=data_dir, output_dir=out_dir)
    other = MapCanvas(config=cfg)
    other.import_drawings_state(records)
    assert other.history.commands[0].rotation_deg == 35.0

    # Record antigo (v4 sem a chave) abre com 0.0
    rec = dict(records[0])
    del rec["rotation_deg"]
    old = project_io.record_to_command(rec)
    assert old.rotation_deg == 0.0


def test_escape_cancels_rotation_without_commit(canvas):
    cmd = _add_shape(canvas, "ellipse", [-50.0, -40.0], [-22.0, -18.0])
    _select(canvas, cmd)
    hx, hy = canvas._edit_rotate_handle_xy
    canvas._on_click(_FakeEvent(canvas, hx, hy))
    cx, cy = shape_rotation_center(cmd)
    r = math.hypot(hx - cx, hy - cy)
    a1 = math.atan2(hy - cy, hx - cx) + math.radians(45.0)
    canvas._on_motion(_FakeEvent(canvas, cx + r * math.cos(a1), cy + r * math.sin(a1)))
    assert canvas._edit_ghost is not None

    canvas.clear_edit_selection()  # Esc
    assert cmd.rotation_deg == 0.0  # sem commit
    assert canvas._edit_ghost is None
    assert cmd.artist.set_visible is not None  # ainda vivo
    assert canvas.history.undo_count == 1  # só a criação


def test_click_on_handle_without_motion_is_noop(canvas):
    cmd = _add_shape(canvas, "rect", [-50.0, -40.0], [-25.0, -15.0])
    _select(canvas, cmd)
    hx, hy = canvas._edit_rotate_handle_xy
    ev = _FakeEvent(canvas, hx, hy)
    canvas._on_click(ev)
    canvas._on_release(ev)  # sem motion
    assert cmd.rotation_deg == 0.0
    assert canvas.history.undo_count == 1
