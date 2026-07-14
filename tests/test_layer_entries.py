"""Entradas do painel de Camadas — detail atualizado IN PLACE preserva o toggle.

Regressão do filtro de avisos futuros: o re-render não pode resetar o checkbox
de visibilidade escolhido pelo usuário nem mover a linha (era remove+add).
"""

import pytest

pytest.importorskip("PyQt6")


def test_set_layer_detail_preserves_toggle(qapp):
    from cartomet_br.gui.layer_panel import FieldLayerPanel

    panel = FieldLayerPanel()
    panel.add_layer_entry("x", "Camada X", "detalhe 1")
    entry = panel._layer_widgets["x"]
    entry["checkbox"].setChecked(False)  # usuário escondeu a camada

    assert panel.set_layer_detail("x", "detalhe 2") is True
    assert entry["detail"].text() == "detalhe 2"
    assert panel.layer_entry_checked("x") is False  # toggle preservado

    # Camada não listada: consultas respondem "não existe", sem estourar.
    assert panel.layer_entry_checked("nope") is None
    assert panel.set_layer_detail("nope", "y") is False


def test_inmet_future_enabled_reflects_checkbox(qapp):
    from cartomet_br.gui.layer_panel import FieldLayerPanel

    panel = FieldLayerPanel()
    assert panel.inmet_future_enabled() is True  # default: futuros incluídos
    panel.inmet_future_check.setChecked(False)
    assert panel.inmet_future_enabled() is False
