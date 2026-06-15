"""Mobília de carta OMM (F7) no MapCanvas — offscreen.

Verifica que ``render_chart_furniture`` adiciona cabeçalho/legenda e que
``clear_chart_furniture`` remove tudo sem vazar (contagem de textos/eixos volta
ao baseline). Roda sob ``QT_QPA_PLATFORM=offscreen``; se o Qt não puder iniciar,
o módulo é pulado.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from cartomet_br.core.config import EXTENT_BRASIL, Config
from cartomet_br.gui.draw_tools import DrawCommand, PenCommand, PointCommand
from cartomet_br.gui.project_io import commands_to_records

_PEN_STYLE = {"edge_color": "#2E86C1", "fill_color": None, "linewidth": 2.0,
              "linestyle": "dashed", "alpha": 0.8}


@pytest.fixture(scope="module")
def qapp():
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PyQt6 indisponível: {exc}")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def canvas(qapp, tmp_path):
    from cartomet_br.gui.map_canvas import MapCanvas
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "out"
    data_dir.mkdir()
    out_dir.mkdir()
    cfg = Config(extent=EXTENT_BRASIL.copy(), data_dir=data_dir, output_dir=out_dir)
    try:
        return MapCanvas(config=cfg)
    except Exception as exc:  # noqa: BLE001 — ambiente sem render
        pytest.skip(f"MapCanvas não pôde ser criado offscreen: {exc}")


def test_get_chart_time_meta_empty_when_no_data(canvas):
    assert canvas.get_chart_time_meta() == {"valid_time": "", "base_time": "", "step": 0}


def test_get_used_symbols_filters_and_dedups(canvas):
    cmds = [
        DrawCommand("6", [-40.0, -35.0, -30.0], [2.0, 1.0, 0.0], flip=True, intensity=3),
        DrawCommand("1", [-50.0, -45.0], [0.0, -2.0], flip=False),
        DrawCommand("6", [-20.0, -15.0], [3.0, 2.0], flip=False, intensity=2),  # dup
        PointCommand("a", -45.0, -10.0),
        PenCommand([-20.0, -21.0], [-3.0, -4.0], dict(_PEN_STYLE)),             # não-OMM
    ]
    canvas.import_drawings_state(commands_to_records(cmds))

    keys = [k for k, _ in canvas.get_used_symbols()]
    assert keys == ["6", "1", "a"]          # caneta fora, dedup, ordem preservada


def test_render_then_clear_roundtrips_no_leak(canvas):
    base_axes = len(canvas.fig.axes)
    base_texts = len(canvas.fig.texts)
    base_artists = len(canvas.fig.artists)

    canvas.import_drawings_state(
        commands_to_records([DrawCommand("1", [-50.0, -45.0], [0.0, -2.0], flip=False)])
    )
    meta = {"institution": "UFPA", "analyst": "F", "chart_type": "Carta",
            "logo_path": "", "valid_time": "14-06-2026 12:00",
            "base_time": "00Z 14-06-2026", "step": 12, "emission": "14-06-2026 12:00"}

    added = canvas.render_chart_furniture(meta)
    assert added                                   # algo foi criado
    assert len(canvas.fig.texts) > base_texts      # textos do cabeçalho
    assert len(canvas.fig.axes) == base_axes + 1   # eixo da legenda (há símbolo)
    assert len(canvas.fig.artists) > base_artists  # faixa/accent do cabeçalho

    canvas.clear_chart_furniture()
    assert len(canvas.fig.texts) == base_texts
    assert len(canvas.fig.axes) == base_axes
    assert len(canvas.fig.artists) == base_artists
    assert canvas._chart_furniture == []


def test_render_without_symbols_has_no_legend_axes(canvas):
    base_axes = len(canvas.fig.axes)
    canvas.render_chart_furniture({"institution": "UFPA"})
    # Sem traçado → sem eixo de legenda; só cabeçalho.
    assert len(canvas.fig.axes) == base_axes
    canvas.clear_chart_furniture()
    assert len(canvas.fig.axes) == base_axes
