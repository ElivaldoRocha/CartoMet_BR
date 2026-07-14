"""Material de estudo 'Espessura 1000–500 hPa' — render puro + fiação da GUI.

Parte 1 (pura, sem Qt): o `.md` do material renderiza para HTML com os marcadores
didáticos-chave e com o banner customizado, sem que LaTeX/tabelas quebrem a conversão.

Parte 2 (offscreen): o helper de caminho resolve o arquivo e ``_show_study_espessura``
monta o diálogo sem erro (com ``QDialog.exec`` neutralizado). Roda sob
``QT_QPA_PLATFORM=offscreen``; se o Qt não puder iniciar, é pulada.
"""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from cartomet_br.gui.methodology import render_methodology_html

_STUDY_MD = Path(__file__).resolve().parents[1] / "docs" / "Estudo_Espessura_1000_500hPa.md"


def test_study_document_exists():
    assert _STUDY_MD.exists(), f"material de estudo ausente: {_STUDY_MD}"


def test_render_contains_key_markers(tmp_path):
    out = render_methodology_html(
        _STUDY_MD,
        out_path=tmp_path / "espessura.html",
        title="Espessura — Teste",
        banner="BANNER-CUSTOMIZADO-ESPESSURA",
    )
    html = out.read_text(encoding="utf-8")
    # Banner e título parametrizados entraram no HTML (não o default LOCZCIT-PA).
    assert "BANNER-CUSTOMIZADO-ESPESSURA" in html
    assert "<title>Espessura — Teste</title>" in html
    assert "LOCZCIT-PA" not in html
    # Conteúdo didático essencial sobreviveu à conversão Markdown.
    for marker in ("vento térmico", "5400", "cavado", "crista", "advec"):
        assert marker.lower() in html.lower(), f"faltou marcador: {marker}"
    # A tabela HS×HN virou <table>; o fluxograma mermaid virou <div class="mermaid">.
    assert "<table" in html
    assert 'class="mermaid"' in html


def test_render_defaults_unchanged(tmp_path):
    """Sem title/banner, o pipeline mantém o texto padrão (chamadas existentes intactas)."""
    out = render_methodology_html(_STUDY_MD, out_path=tmp_path / "def.html")
    html = out.read_text(encoding="utf-8")
    assert "Metodologia científica do Índice LOCZCIT-PA" in html


# --- Parte 2: fiação da GUI (offscreen) -------------------------------------

pytest.importorskip("PyQt6")


@pytest.fixture
def window(qapp, tmp_path):
    from cartomet_br.gui.main_window import MainWindow

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    try:
        return MainWindow(data_dir=data_dir)
    except Exception as exc:  # noqa: BLE001 — ambiente sem render
        pytest.skip(f"MainWindow não pôde ser criada offscreen: {exc}")


def test_study_path_resolves(window):
    p = window._espessura_study_path()
    assert p is not None and p.exists()
    assert p.name == "Estudo_Espessura_1000_500hPa.md"


def test_show_study_dialog_builds(window, monkeypatch):
    # Neutraliza o modal bloqueante: o diálogo deve montar sem levantar exceção.
    from PyQt6.QtWidgets import QDialog

    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    window._show_study_espessura()
