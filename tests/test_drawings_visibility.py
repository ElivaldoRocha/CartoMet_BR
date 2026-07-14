"""Toggles de visibilidade dos desenhos do usuário (aba Simbologias) — offscreen.

Esconder ≠ apagar: ``set_drawings_visible`` aplica ``set_visible`` por grupo
(simbologia OMM/caneta/formas + régua, emojis, anotações) sem tocar nas listas
nem no histórico; artistas criados com o grupo oculto (finalize/redo/import)
nascem invisíveis via ``_apply_drawing_visibility``.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")


def _draw_symbol_line(canvas):
    """Traça uma linha de simbologia OMM pelo fluxo real (preview → finalize)."""
    canvas.points_x.extend([-50.0, -48.0])
    canvas.points_y.extend([-20.0, -19.0])
    canvas._update_preview()
    canvas.finalize_line()


def _populate(canvas):
    """Um desenho de cada grupo: linha OMM, emoji e anotação."""
    _draw_symbol_line(canvas)
    canvas.add_emoji(-45.0, -15.0, "CB", 28)
    canvas.add_annotation(-40.0, -10.0, "frente em deslocamento")
    assert canvas.lines and canvas._emoji_annotations and canvas._annotations


def test_hide_each_group_independently(canvas):
    _populate(canvas)
    canvas.set_drawings_visible("emojis", False)
    assert all(not a.get_visible() for a in canvas._emoji_annotations)
    assert all(a.get_visible() for a in canvas.lines)
    assert all(a.get_visible() for a in canvas._annotations)

    canvas.set_drawings_visible("symbology", False)
    canvas.set_drawings_visible("annotations", False)
    assert all(not a.get_visible() for a in canvas.lines)
    assert all(not a.get_visible() for a in canvas._annotations)

    # Mostrar de volta: nada foi apagado no caminho
    for kind in ("symbology", "emojis", "annotations"):
        canvas.set_drawings_visible(kind, True)
    assert all(a.get_visible() for a in canvas.lines)
    assert all(a.get_visible() for a in canvas._emoji_annotations)
    assert all(a.get_visible() for a in canvas._annotations)


def test_hiding_does_not_delete(canvas):
    _populate(canvas)
    n = (len(canvas.lines), len(canvas._emoji_annotations), len(canvas._annotations))
    for kind in ("symbology", "emojis", "annotations"):
        canvas.set_drawings_visible(kind, False)
    assert (len(canvas.lines), len(canvas._emoji_annotations), len(canvas._annotations)) == n
    assert canvas.history.can_undo  # histórico intacto — esconder não mexe no undo


def test_new_artist_born_hidden(canvas):
    """Artista criado com o grupo oculto herda o toggle (não 'vaza' visível)."""
    canvas.set_drawings_visible("annotations", False)
    canvas.add_annotation(-40.0, -10.0, "texto")
    assert not canvas._annotations[-1].get_visible()

    canvas.set_drawings_visible("emojis", False)
    canvas.add_emoji(-45.0, -15.0, "CB", 28)
    assert not canvas._emoji_annotations[-1].get_visible()

    canvas.set_drawings_visible("symbology", False)
    _draw_symbol_line(canvas)
    assert not canvas.lines[-1].get_visible()


def test_redo_respects_hidden_group(canvas):
    """Regressão: _rebuild_artist recriava sempre visível — redo com o grupo
    oculto reintroduzia um artista visível no meio dos escondidos."""
    _draw_symbol_line(canvas)
    canvas.undo_line()
    assert not canvas.lines
    canvas.set_drawings_visible("symbology", False)
    canvas.redo_action()
    assert len(canvas.lines) == 1
    assert not canvas.lines[0].get_visible()


def test_clear_all_rearms_flags(canvas):
    """Apagar os desenhos re-arma os toggles (recomeço limpo)."""
    _populate(canvas)
    canvas.set_drawings_visible("emojis", False)
    canvas.clear_all()
    assert canvas._drawings_visible == {
        "symbology": True,
        "emojis": True,
        "annotations": True,
    }


def test_symbology_panel_checkboxes(qapp):
    """Painel: checkbox emite (kind, visível); sync programático NÃO re-emite."""
    from cartomet_br.gui.drawing_panel import SymbologyPanel

    panel = SymbologyPanel()
    got: list = []
    panel.drawings_visibility_toggled.connect(lambda k, v: got.append((k, v)))
    panel._visibility_checks["emojis"].setChecked(False)
    assert got == [("emojis", False)]
    panel.set_visibility_checked("emojis", True)  # sync silencioso (blockSignals)
    assert got == [("emojis", False)]
    assert panel._visibility_checks["emojis"].isChecked()
