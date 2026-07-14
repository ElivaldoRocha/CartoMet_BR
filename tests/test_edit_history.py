"""Documento × pilhas de operações (DrawingHistory v2) — offscreen.

O documento (desenhos vivos, alimenta o save) é separado das pilhas de
undo/redo, que guardam OPERAÇÕES (criar/apagar/mover/vértice). Apagar um
desenho do meio e desfazer o reinsere NA POSIÇÃO original; remoções por fora
das pilhas ("desfazer traço") expurgam as operações órfãs; o teto de 50
operações nunca expulsa um desenho do salvamento.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from cartomet_br.core.config import EXTENT_BRASIL, Config
from cartomet_br.gui.draw_tools import PointCommand


def _draw_front(canvas, xs, ys, symbol="1"):
    canvas.current_symbol = symbol
    canvas.points_x.extend(xs)
    canvas.points_y.extend(ys)
    canvas._update_preview()
    canvas.finalize_line()


def test_history_trim_keeps_document():
    """55 pushes: as pilhas respeitam o teto (50) mas o documento guarda TUDO.

    Regressão do bug latente antigo: o 51º push expulsava o desenho mais velho
    do salvamento, deixando o artista órfão no mapa.
    """
    from cartomet_br.gui.map_canvas import DrawingHistory

    hist = DrawingHistory(max_size=50)
    for i in range(55):
        hist.push(PointCommand(symbol_key="a", x=float(i), y=0.0))
    assert len(hist.commands) == 55
    assert hist.undo_count == 50


def test_delete_middle_undo_reinserts_in_order(canvas):
    _draw_front(canvas, [-50.0, -46.0], [-30.0, -26.0], symbol="1")  # A
    _draw_front(canvas, [-44.0, -40.0], [-24.0, -20.0], symbol="2")  # B
    _draw_front(canvas, [-38.0, -34.0], [-18.0, -14.0], symbol="3")  # C
    a, b, c = canvas.history.commands

    canvas._select_command(b)
    canvas.delete_selected_drawing()
    assert canvas.history.commands == [a, c]
    assert b.artist is None

    canvas.undo_line()  # ressuscita B na posição original
    assert canvas.history.commands == [a, b, c]
    assert b.artist is not None

    canvas.redo_action()  # re-apaga
    assert canvas.history.commands == [a, c]


def test_external_removal_purges_ops(canvas):
    """ "Desfazer traço" após delete+undo: as ops órfãs somem, nada quebra."""
    from cartomet_br.gui.draw_tools import DrawStyle, PenCommand

    cmd = PenCommand(
        points_x=[-50.0, -49.0, -48.0],
        points_y=[-20.0, -20.5, -21.0],
        style=DrawStyle().to_dict(),
    )
    canvas._rebuild_artist(cmd)
    canvas.history.push(cmd)

    canvas._select_command(cmd)
    canvas.delete_selected_drawing()
    canvas.undo_line()  # caneta viva de novo; DeleteOp está no redo
    assert canvas.history.commands == [cmd]

    canvas.remove_last_pen_stroke()  # remoção POR FORA das pilhas
    assert canvas.history.commands == []
    assert not canvas.history.can_undo  # CreateOp expurgada
    assert not canvas.history.can_redo  # DeleteOp expurgada
    canvas.undo_line()  # no-ops seguros
    canvas.redo_action()
    assert canvas.history.commands == []


def test_removed_annotation_does_not_resurrect_on_save(canvas):
    """Regressão: anotação desfeita não pode voltar no export (bug antigo)."""
    canvas.add_annotation(-40.0, -10.0, "corrigir", "#FFFFFF", 12)
    assert len(canvas.export_drawings_state()) == 1
    canvas.remove_last_annotation()
    assert canvas.export_drawings_state() == []
    assert canvas._annotations == []


def test_delete_survives_cmbr_roundtrip(canvas, qapp, tmp_path):
    """B apaga um sistema de A e salva: o .cmbr resultante não o contém."""
    from cartomet_br.gui.map_canvas import MapCanvas

    _draw_front(canvas, [-50.0, -46.0], [-30.0, -26.0], symbol="1")
    _draw_front(canvas, [-44.0, -40.0], [-24.0, -20.0], symbol="2")
    errada = canvas.history.commands[0]
    canvas._select_command(errada)
    canvas.delete_selected_drawing()

    records = canvas.export_drawings_state()
    data_dir = tmp_path / "d2"
    out_dir = tmp_path / "o2"
    data_dir.mkdir()
    out_dir.mkdir()
    cfg = Config(extent=EXTENT_BRASIL.copy(), data_dir=data_dir, output_dir=out_dir)
    other = MapCanvas(config=cfg)
    other.import_drawings_state(records)
    keys = [c.symbol_key for c in other.history.commands]
    assert keys == ["2"]
