"""Fixtures compartilhadas para testes do CartoMet BR."""

import pytest
import tempfile
from pathlib import Path

from cartomet_br.core.config import (
    Config,
    EXTENT_BRASIL,
    EXTENT_AMSUL,
    EXTENT_NORDESTE,
    EXTENT_SUDESTE,
    EXTENT_SUL,
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
