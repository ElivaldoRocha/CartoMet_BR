"""Testes do conversor Markdown→HTML da metodologia (gui/methodology.py) — offline.

Guardam a regressão do **token-collision**: antes do fix, o store de mermaid e o de
math cunhavam placeholders com o mesmo esquema (``@@CMTOKEN{i}@@``), então o primeiro
fluxograma e a primeira equação recebiam o mesmo token — a restauração do mermaid
sobrescrevia a equação, duplicando o diagrama e apagando a fórmula (sintoma relatado
no diálogo "Sobre o Índice ZCIT"). Estes testes não usam rede (o HTML é gerado
localmente; MathJax/Mermaid só rodam no navegador).
"""

from pathlib import Path

import pytest

from cartomet_br.gui.methodology import render_methodology_html

_DOCS = Path(__file__).resolve().parents[1] / "docs" / "Metodologia_LOCZCIT-PA.md"


def _render(tmp_path: Path, md_text: str) -> str:
    src = tmp_path / "doc.md"
    src.write_text(md_text, encoding="utf-8")
    out = render_methodology_html(src, out_path=tmp_path / "out.html")
    return Path(out).read_text(encoding="utf-8")


def test_mermaid_e_equacao_nao_colidem(tmp_path: Path) -> None:
    """Mermaid vindo ANTES de uma equação não pode sobrescrevê-la (bug original)."""
    md = (
        "# Título\n\n"
        "## Sumário\n\n"
        "```mermaid\n"
        "flowchart TD\n"
        '    A["Entrada"] --> B["Saída"]\n'
        "```\n\n"
        "## Seção\n\n"
        "A magnitude do gradiente:\n\n"
        "$$\n\\nabla \\text{TSM} = \\sqrt{a^2 + b^2}\n$$\n\n"
        "onde $T_s$ é a temperatura.\n"
    )
    html = _render(tmp_path, md)

    # O fluxograma aparece UMA vez (não duplicado dentro da seção).
    assert html.count('class="mermaid"') == 1
    assert html.count("flowchart TD") == 1
    # A equação de bloco sobreviveu (não foi engolida pelo mermaid).
    assert "\\nabla \\text{TSM} = \\sqrt{a^2 + b^2}" in html
    # A math inline também sobreviveu.
    assert "$T_s$" in html
    # Nenhum placeholder vazou para o HTML final.
    assert "@@CMTOK" not in html


def test_doc_real_renderiza_pipeline_uma_vez_e_preserva_gradiente(tmp_path: Path) -> None:
    """Smoke sobre o doc oficial: pipeline único e equação ∇TSM da §2.1 presente."""
    if not _DOCS.exists():
        pytest.skip("Metodologia_LOCZCIT-PA.md ausente neste checkout")

    out = render_methodology_html(_DOCS, out_path=tmp_path / "metodologia.html")
    html = Path(out).read_text(encoding="utf-8")

    assert html.count("flowchart TD") == 1
    assert html.count('class="mermaid"') == 1
    assert "\\nabla \\text{TSM} = \\sqrt" in html
    assert "@@CMTOK" not in html
