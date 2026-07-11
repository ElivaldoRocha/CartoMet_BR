"""Estrela da Sonda Vertical no MapCanvas — offscreen.

Regressão: desativar o modo Sonda Vertical (``set_sounding_mode(False)``) deve
remover a estrela (``_sounding_marker``) da carta. Antes, o marcador só sumia
via *Limpar o mapa* (``clear_map``), persistindo ao desligar a feature.

Roda sob ``QT_QPA_PLATFORM=offscreen``; se o Qt não puder iniciar, é pulado.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from cartomet_br.core.config import EXTENT_BRASIL, Config


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


def test_marker_appears_then_clears_on_deactivate(canvas):
    canvas.set_sounding_mode(True)
    canvas._mark_sounding_point(-48.0, -2.0)
    assert canvas._sounding_marker is not None  # estrela posta

    canvas.set_sounding_mode(False)  # usuário desativa a feature
    assert canvas._sounding_marker is None  # estrela sumiu (regressão do bug)
    assert canvas.interaction_mode is None


def test_deactivate_is_noop_when_no_marker(canvas):
    # Desligar sem ter posto estrela nem estar no modo: não deve explodir.
    assert canvas._sounding_marker is None
    canvas.set_sounding_mode(False)
    assert canvas._sounding_marker is None
