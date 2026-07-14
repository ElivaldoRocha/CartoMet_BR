"""Fixtures compartilhadas para testes do CartoMet BR."""

# Ambiente headless (CI Linux sem display): força a plataforma Qt "offscreen" e o
# backend "Agg" do Matplotlib ANTES de qualquer import de PyQt6/GUI. Sem isso, os
# testes que importam `cartomet_br.gui.*` quebram na coleta no runner do GitHub.
# `setdefault` preserva uma escolha explícita do desenvolvedor (ex.: rodar com tela).
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


import pytest

from cartomet_br.core.config import (
    EXTENT_AMSUL,
    EXTENT_BRASIL,
    Config,
)


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Diretório temporário para dados de teste."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Diretório temporário para saída de teste."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return output_dir


@pytest.fixture
def config_brasil(tmp_data_dir, tmp_output_dir):
    """Config padrão para o Brasil."""
    return Config(
        extent=EXTENT_BRASIL.copy(),
        data_dir=tmp_data_dir,
        output_dir=tmp_output_dir,
    )


@pytest.fixture
def config_amsul(tmp_data_dir, tmp_output_dir):
    """Config para América do Sul."""
    return Config(
        extent=EXTENT_AMSUL.copy(),
        data_dir=tmp_data_dir,
        output_dir=tmp_output_dir,
    )


@pytest.fixture(scope="session")
def qapp():
    """QApplication offscreen compartilhada (singleton do processo)."""
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PyQt6 indisponível: {exc}")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def canvas(qapp, config_brasil):
    """MapCanvas offscreen sobre o config Brasil — fixture única dos testes de GUI.

    Fonte única (era copiada em ~12 arquivos): mudanças na construção do
    MapCanvas se propagam daqui.
    """
    from cartomet_br.gui.map_canvas import MapCanvas

    try:
        return MapCanvas(config=config_brasil)
    except Exception as exc:  # noqa: BLE001 — ambiente sem render
        pytest.skip(f"MapCanvas não pôde ser criado offscreen: {exc}")
