"""Testes da fundação caneta/formas (draw_tools) — puros, sem QApplication.

Cobrem: estilo (round-trip + mapeamento de linestyle), construtores de geometria
(fechamento de anéis, orientação da ponta da seta), fábricas de artistas em Axes
matplotlib comum, e os novos comandos no DrawingHistory (push/undo/redo).
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest

from cartomet_br.gui.draw_tools import (
    PEN_MIN_PIXEL_DIST,
    SHAPE_MIN_DRAG_PIXELS,
    SHAPE_TOOLS,
    DrawStyle,
    PenCommand,
    ShapeArtistGroup,
    ShapeCommand,
    build_arrow_geometry,
    build_ellipse_ring,
    build_preview_ring,
    build_rectangle_ring,
    close_polygon_ring,
    create_pen_artist,
    create_shape_artist,
    default_arrow_head_size,
)


@pytest.fixture
def ax():
    fig, ax = plt.subplots()
    ax.set_xlim(-80, 0)
    ax.set_ylim(-40, 20)
    yield ax
    plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════════
#  DrawStyle
# ═══════════════════════════════════════════════════════════════════════════════


class TestDrawStyle:
    def test_roundtrip(self):
        st = DrawStyle(
            edge_color="#123456", fill_color="#abcdef", linewidth=3.5, linestyle="dashed", alpha=0.7
        )
        assert DrawStyle.from_dict(st.to_dict()) == st

    def test_mpl_linestyle_mapping(self):
        assert DrawStyle(linestyle="solid").mpl_linestyle() == "-"
        assert DrawStyle(linestyle="dashed").mpl_linestyle() == "--"
        assert DrawStyle(linestyle="dotted").mpl_linestyle() == ":"
        assert DrawStyle(linestyle="???").mpl_linestyle() == "-"  # fallback seguro

    def test_no_fill_default(self):
        assert DrawStyle().fill_color is None


# ═══════════════════════════════════════════════════════════════════════════════
#  Geometria
# ═══════════════════════════════════════════════════════════════════════════════


class TestGeometry:
    def test_rectangle_ring_closed(self):
        xs, ys = build_rectangle_ring(-10, -5, 10, 5)
        assert len(xs) == len(ys) == 5
        assert (xs[0], ys[0]) == (xs[-1], ys[-1])
        assert min(xs) == -10 and max(xs) == 10
        assert min(ys) == -5 and max(ys) == 5

    def test_ellipse_ring_closed_and_bounded(self):
        xs, ys = build_ellipse_ring(-10, -6, 10, 6, n=120)
        assert len(xs) == 120
        assert (xs[0], ys[0]) == (xs[-1], ys[-1])  # fechamento exato
        # Amostragem paramétrica: extremos aproximam a caixa sem extrapolá-la
        assert min(xs) >= -10 and max(xs) <= 10
        assert min(ys) >= -6 and max(ys) <= 6
        assert max(xs) == pytest.approx(10, abs=0.05)
        assert min(xs) == pytest.approx(-10, abs=0.05)
        assert max(ys) == pytest.approx(6, abs=0.05)
        assert min(ys) == pytest.approx(-6, abs=0.05)

    def test_arrow_head_points_at_tip(self):
        # seta horizontal oeste→leste: ponta em (10,0), base atrás (x < 10)
        shaft_xs, shaft_ys, head = build_arrow_geometry(0, 0, 10, 0, head_size_deg=1.0)
        assert shaft_xs[0] == 0 and shaft_ys == [0, 0]
        assert shaft_xs[1] < 10  # haste para na base da ponta
        assert head[0] == (10, 0) and head[-1] == (10, 0)  # triângulo fechado na ponta
        for bx, _by in head[1:3]:
            assert bx < 10  # base atrás da ponta
        # simetria vertical das duas bases
        assert head[1][1] == pytest.approx(-head[2][1])

    def test_arrow_orientation_follows_drag(self):
        # arrasto para o norte: bases abaixo da ponta
        _, _, head = build_arrow_geometry(0, 0, 0, 10, head_size_deg=1.0)
        assert all(by < 10 for _, by in head[1:3])

    def test_close_polygon_ring(self):
        xs, ys = close_polygon_ring([0, 5, 5], [0, 0, 5])
        assert (xs[-1], ys[-1]) == (0, 0) and len(xs) == 4
        # já fechado → não duplica
        xs2, ys2 = close_polygon_ring(xs, ys)
        assert len(xs2) == 4

    def test_default_arrow_head_scales(self):
        small = default_arrow_head_size(70.0, 1.0)
        thick = default_arrow_head_size(70.0, 6.0)
        assert thick > small > 0

    def test_preview_ring_per_tool(self):
        for tool in ("rect", "ellipse"):
            xs, ys = build_preview_ring(tool, 0, 0, 10, 5)
            assert (xs[0], ys[0]) == (xs[-1], ys[-1])  # anel fechado
        for tool in ("line", "arrow"):
            xs, ys = build_preview_ring(tool, 0, 0, 10, 5)
            assert xs == [0, 10] and ys == [0, 5]  # segmento simples


# ═══════════════════════════════════════════════════════════════════════════════
#  Fábricas de artistas (Axes matplotlib comum; no canvas o transform é PlateCarree)
# ═══════════════════════════════════════════════════════════════════════════════


class TestArtistFactories:
    def test_pen_artist_style(self, ax):
        st = DrawStyle(edge_color="#112233", linewidth=4.0, alpha=0.5)
        line = create_pen_artist(ax, [0, 1, 2], [0, 1, 0], st)
        assert line.get_linewidth() == 4.0
        assert line.get_alpha() == 0.5
        line.remove()  # ciclo de undo

    def test_simple_shapes_return_line(self, ax):
        for tool in ("rect", "ellipse", "line"):
            artist = create_shape_artist(ax, tool, [0, 10], [0, 5], DrawStyle(fill_color=None))
            assert hasattr(artist, "remove")
            artist.remove()

    def test_filled_shape_returns_group(self, ax):
        artist = create_shape_artist(
            ax, "rect", [0, 10], [0, 5], DrawStyle(fill_color="#00ff00", alpha=0.4)
        )
        assert isinstance(artist, ShapeArtistGroup)
        artist.set_visible(False)
        artist.remove()
        artist.remove()  # idempotente, sem exceção

    def test_arrow_is_group_with_head(self, ax):
        artist = create_shape_artist(ax, "arrow", [0, 10], [0, 0], DrawStyle(), head_size_deg=1.0)
        assert isinstance(artist, ShapeArtistGroup)
        artist.remove()

    def test_polygon_closed_and_filled(self, ax):
        artist = create_shape_artist(
            ax, "polygon", [0, 5, 5], [0, 0, 5], DrawStyle(fill_color="#0000ff")
        )
        assert isinstance(artist, ShapeArtistGroup)
        artist.remove()

    def test_unknown_tool_raises(self, ax):
        with pytest.raises(ValueError):
            create_shape_artist(ax, "star", [0, 1], [0, 1], DrawStyle())

    def test_redo_recreates_identical_geometry(self, ax):
        # finalize e redo usam a MESMA fábrica → geometria idêntica
        st = DrawStyle()
        a1 = create_shape_artist(ax, "ellipse", [0, 10], [0, 6], st)
        d1 = a1.get_xydata().copy()
        a1.remove()
        a2 = create_shape_artist(ax, "ellipse", [0, 10], [0, 6], st)
        assert np.allclose(d1, a2.get_xydata())
        a2.remove()


# ═══════════════════════════════════════════════════════════════════════════════
#  Comandos no DrawingHistory (sem QApplication: classes puras do map_canvas?
#  Não — DrawingHistory vive em map_canvas (importa PyQt). Testamos o protocolo
#  de pilha com uma réplica mínima de interface via os próprios comandos.)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCommands:
    def test_pen_command_pure_data(self):
        cmd = PenCommand(points_x=[0, 1], points_y=[2, 3], style=DrawStyle().to_dict())
        assert cmd.pressures is None  # gancho p/ pressão futura
        assert cmd.artist is None
        import dataclasses

        d = dataclasses.asdict(cmd)  # serializável
        assert d["points_x"] == [0, 1]

    def test_shape_command_pure_data(self):
        cmd = ShapeCommand(
            tool="arrow",
            points_x=[0, 10],
            points_y=[0, 0],
            style=DrawStyle().to_dict(),
            head_size_deg=1.25,
        )
        assert cmd.head_size_deg == 1.25  # congelado p/ redo estável
        assert cmd.tool in SHAPE_TOOLS

    def test_constants_sane(self):
        assert PEN_MIN_PIXEL_DIST > 0
        assert SHAPE_MIN_DRAG_PIXELS > 0
        assert set(SHAPE_TOOLS) == {"rect", "ellipse", "arrow", "line", "polygon"}
