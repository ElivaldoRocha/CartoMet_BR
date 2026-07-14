"""Modo edição — seleção por clique e apagamento seletivo (offscreen).

O hit-testing opera sobre a geometria do COMANDO (spline interpolada para
linhas OMM, âncora para pontuais/texto/emoji), em raio de PIXELS. Grupos com a
visibilidade desligada não são selecionáveis; empate fica com o mais recente;
Delete é desfazível com [Z].
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")


class _FakeEvent:
    """Evento de mouse mínimo (inaxes/button/xdata/ydata/x/y) p/ _on_click."""

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
    canvas.current_symbol = symbol
    canvas.points_x.extend(xs)
    canvas.points_y.extend(ys)
    canvas._update_preview()
    canvas.finalize_line()


_COLD_XS = [-50.0, -44.0, -38.0]
_COLD_YS = [-30.0, -24.0, -22.0]


def test_click_selects_line_on_interpolated_spline(canvas):
    """Clique ENTRE vértices crus (sobre a curva desenhada) acerta a frente."""
    from cartomet_br.charts.interactive import interpolar_pontos

    _draw_front(canvas, _COLD_XS, _COLD_YS)
    cmd = canvas.history.commands[0]
    canvas.set_edit_mode(True)

    xi, yi = interpolar_pontos(cmd.points_x, cmd.points_y)
    mid_lon, mid_lat = float(xi[75]), float(yi[75])  # meio da spline
    canvas._on_click(_event_near_pixel(canvas, mid_lon, mid_lat, 3, 3))
    assert canvas._edit_selected is cmd
    assert canvas._edit_highlight  # halo aceso

    # Clique no vazio desseleciona
    canvas._on_click(_FakeEvent(canvas, -70.0, 5.0))
    assert canvas._edit_selected is None
    assert canvas._edit_highlight == []


def test_click_selects_point_symbol_and_annotation(canvas):
    canvas.current_symbol = "a"
    canvas._place_point_symbol(-45.0, -15.0)
    canvas.add_annotation(-60.0, -5.0, "obs", "#FFFFFF", 12)
    ponto, nota = canvas.history.commands
    canvas.set_edit_mode(True)

    canvas._on_click(_event_near_pixel(canvas, -45.0, -15.0, 5, 5))
    assert canvas._edit_selected is ponto
    canvas._on_click(_event_near_pixel(canvas, -60.0, -5.0, -5, 4))
    assert canvas._edit_selected is nota


def test_hidden_group_is_not_selectable(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    canvas.set_edit_mode(True)
    canvas.set_drawings_visible("symbology", False)
    canvas._on_click(_event_near_pixel(canvas, _COLD_XS[0], _COLD_YS[0], 2, 2))
    assert canvas._edit_selected is None  # não se edita o que não se vê
    canvas.set_drawings_visible("symbology", True)
    canvas._on_click(_event_near_pixel(canvas, _COLD_XS[0], _COLD_YS[0], 2, 2))
    assert canvas._edit_selected is canvas.history.commands[0]


def test_tie_selects_most_recent(canvas):
    """Duas frentes compartilhando o vértice: o clique fica com a mais nova."""
    _draw_front(canvas, _COLD_XS, _COLD_YS, symbol="1")
    _draw_front(canvas, [_COLD_XS[-1], -34.0], [_COLD_YS[-1], -20.0], symbol="3")
    nova = canvas.history.commands[-1]
    canvas.set_edit_mode(True)
    canvas._on_click(_event_near_pixel(canvas, _COLD_XS[-1], _COLD_YS[-1], 1, 1))
    assert canvas._edit_selected is nova


def test_delete_selected_and_undo_redo(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    cmd = canvas.history.commands[0]
    canvas.set_edit_mode(True)
    canvas._on_click(_event_near_pixel(canvas, _COLD_XS[1], _COLD_YS[1], 2, 2))
    assert canvas._edit_selected is cmd

    canvas.delete_selected_drawing()
    assert canvas._edit_selected is None
    assert canvas.export_drawings_state() == []
    assert cmd.artist is None

    canvas.undo_line()  # [Z] ressuscita
    assert len(canvas.export_drawings_state()) == 1
    assert cmd.artist is not None
    canvas.redo_action()  # [Y] re-apaga
    assert canvas.export_drawings_state() == []


def test_delete_emoji_and_undo(canvas):
    canvas.add_emoji(-50.0, -10.0, "⛈", 28)
    cmd = canvas._emoji_records[0]
    canvas.set_edit_mode(True)
    canvas._on_click(_event_near_pixel(canvas, -50.0, -10.0, 4, 4))
    assert canvas._edit_selected is cmd

    canvas.delete_selected_drawing()
    assert canvas._emoji_records == []
    assert canvas._emoji_annotations == []

    canvas.undo_line()
    assert canvas._emoji_records == [cmd]
    assert len(canvas._emoji_annotations) == 1
    assert cmd.artist is canvas._emoji_annotations[0]


def test_delete_without_selection_is_noop(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    canvas.set_edit_mode(True)
    canvas.delete_selected_drawing()  # nada selecionado
    assert len(canvas.history.commands) == 1


def test_exit_edit_mode_clears_selection(canvas):
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    canvas.set_edit_mode(True)
    canvas._on_click(_event_near_pixel(canvas, _COLD_XS[0], _COLD_YS[0], 2, 2))
    assert canvas._edit_selected is not None
    canvas.set_edit_mode(False)
    assert canvas._edit_selected is None
    assert canvas._edit_highlight == []
    assert canvas.interaction_mode is None


def test_selection_signal_and_clear_all(canvas):
    got: list[str] = []
    canvas.edit_selection_changed.connect(got.append)
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    canvas.set_edit_mode(True)
    canvas._on_click(_event_near_pixel(canvas, _COLD_XS[0], _COLD_YS[0], 2, 2))
    assert got and got[-1] == "Frente Fria"
    canvas.clear_all()
    assert got[-1] == ""
    assert canvas._edit_selected is None


def _all_modos_keys():
    from cartomet_br.symbols import MODOS

    return sorted(MODOS.keys())


@pytest.mark.parametrize("key", _all_modos_keys())
def test_every_modos_symbol_is_selectable(canvas, key):
    """TODA simbologia de MODOS (linha e pontual) responde ao clique de edição.

    Regressão do caso real: Linha Seca (Dryline) 'invisível' à edição.
    """
    from cartomet_br.symbols import MODOS

    canvas.current_symbol = key
    if MODOS[key].get("ponto"):
        canvas._place_point_symbol(-45.0, -15.0)
        alvo = (-45.0, -15.0)
    else:
        _draw_front(canvas, _COLD_XS, _COLD_YS, symbol=key)
        alvo = (_COLD_XS[1], _COLD_YS[1])
    cmd = canvas.history.commands[0]
    canvas.set_edit_mode(True)
    ev = _event_near_pixel(canvas, *alvo, 3, 3)
    canvas._on_click(ev)
    canvas._on_release(ev)
    assert canvas._edit_selected is cmd, f"[{key}] {MODOS[key]['nome']} não selecionou"


def test_dryline_click_on_scallop_glyph_selects(canvas):
    """Clique NO SEMICÍRCULO da Linha Seca (≈11 px perpendicular à linha-base).

    Caso real do usuário: com raio 10 px o glifo ficava fora do alcance e a
    dryline parecia invisível à edição — o raio precisa cobrir a banda visual.
    """
    _draw_front(canvas, [-50.0, -40.0], [-25.0, -25.0], symbol="0")  # reta W→E
    cmd = canvas.history.commands[0]
    canvas.set_edit_mode(True)
    # 12 px PERPENDICULARES ao meio do traçado = apex do semicírculo
    ev = _event_near_pixel(canvas, -45.0, -25.0, 0, 12)
    canvas._on_click(ev)
    canvas._on_release(ev)
    assert canvas._edit_selected is cmd


def test_ellipse_selectable_on_edge_not_corner(canvas):
    """Elipse: seleciona pela BORDA; o canto da caixa (vazio) não seleciona."""
    from cartomet_br.gui.draw_tools import DrawStyle, ShapeCommand

    cmd = ShapeCommand(
        tool="ellipse",
        points_x=[-50.0, -40.0],
        points_y=[-25.0, -15.0],
        style=DrawStyle().to_dict(),
    )
    canvas._rebuild_artist(cmd)
    canvas.history.push(cmd)
    canvas.set_edit_mode(True)

    ev = _event_near_pixel(canvas, -50.0, -20.0, 2, 0)  # meio da borda esquerda
    canvas._on_click(ev)
    canvas._on_release(ev)
    assert canvas._edit_selected is cmd

    canvas.clear_edit_selection()
    canvas._on_click(_FakeEvent(canvas, -49.3, -24.3))  # canto da caixa (fora do anel)
    assert canvas._edit_selected is None


def test_undo_of_selected_creation_deselects(canvas):
    """[Z] que remove o próprio desenho selecionado também desfaz a seleção."""
    _draw_front(canvas, _COLD_XS, _COLD_YS)
    canvas.set_edit_mode(True)
    canvas._on_click(_event_near_pixel(canvas, _COLD_XS[0], _COLD_YS[0], 2, 2))
    assert canvas._edit_selected is not None
    canvas.undo_line()  # desfaz a criação da frente selecionada
    assert canvas._edit_selected is None
    assert canvas._edit_highlight == []


def test_out_of_stack_removal_releases_selection(canvas):
    """Regressão: "desfazer última forma" com a forma SELECIONADA desmarca a seleção.

    Antes, a remoção por fora das pilhas deixava o halo órfão e um Delete em
    seguida estourava ValueError (comando fora do documento) — fechava o app.
    """
    from cartomet_br.gui.draw_tools import DrawStyle, ShapeCommand

    cmd = ShapeCommand(
        tool="rect",
        points_x=[-50.0, -40.0],
        points_y=[-25.0, -15.0],
        style=DrawStyle().to_dict(),
    )
    canvas._rebuild_artist(cmd)
    canvas.history.push(cmd)
    canvas.set_edit_mode(True)
    canvas._select_command(cmd)

    canvas.remove_last_shape()  # remoção por fora das pilhas
    assert canvas._edit_selected is None
    assert canvas._edit_highlight == []
    canvas.delete_selected_drawing()  # no-op — não pode estourar
    assert all(c is not cmd for c in canvas.history.commands)


def test_remove_last_emoji_releases_selection(canvas):
    canvas.add_emoji(-50.0, -20.0, "⛈", fontsize=20)
    cmd = canvas._emoji_records[0]
    canvas.set_edit_mode(True)
    canvas._select_command(cmd)

    canvas.remove_last_emoji()
    assert canvas._edit_selected is None
    canvas.delete_selected_drawing()  # no-op — antes: ValueError em _emoji_records


def test_clear_annotations_releases_selection(canvas):
    canvas.add_annotation(-60.0, -5.0, "obs", "#FFFFFF", 12)
    cmd = canvas.history.commands[0]
    canvas.set_edit_mode(True)
    canvas._select_command(cmd)

    canvas.clear_annotations()
    assert canvas._edit_selected is None
    canvas.delete_selected_drawing()  # no-op — seleção já foi liberada
