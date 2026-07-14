"""
Renderização da metodologia LOCZCIT-PA (.md) para HTML legível no navegador.

O usuário final NÃO é desenvolvedor: abrir o .md cru num editor (VSCode) é péssima
experiência. Este módulo converte o Markdown em um HTML autocontido e estilizado,
com **MathJax** (equações LaTeX `$...$` / `$$...$$`) e **Mermaid** (fluxogramas),
aberto pelo navegador padrão do sistema — app universal que todo usuário tem.

As equações e diagramas usam MathJax/Mermaid via CDN (a aplicação já requer internet
para o ECMWF). Sem rede, o texto e as justificativas científicas continuam legíveis.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script>
  window.MathJax = {{
    tex: {{ inlineMath: [['$', '$']], displayMath: [['$$', '$$']] }},
    svg: {{ fontCache: 'global' }}
  }};
</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>
  document.addEventListener('DOMContentLoaded', function () {{
    if (window.mermaid) {{ mermaid.initialize({{ startOnLoad: true, theme: 'neutral' }}); }}
  }});
</script>
<style>
  :root {{ --accent: #E67E22; --ink: #1f2a30; --muted: #5a6b73; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         line-height: 1.65; color: var(--ink); max-width: 880px; margin: 0 auto;
         padding: 32px 24px 80px; background: #fbfcfd; }}
  h1, h2, h3 {{ color: #14323f; line-height: 1.25; margin-top: 1.6em; }}
  h1 {{ border-bottom: 3px solid var(--accent); padding-bottom: .25em; }}
  h2 {{ border-bottom: 1px solid #dde3e6; padding-bottom: .2em; }}
  a {{ color: #2563a3; }}
  code {{ background: #eef2f4; padding: .12em .35em; border-radius: 4px;
          font-family: "Cascadia Code", Consolas, monospace; font-size: .92em; }}
  pre code {{ display: block; padding: 12px; overflow-x: auto; }}
  table {{ border-collapse: collapse; margin: 1em 0; width: 100%; }}
  th, td {{ border: 1px solid #cfd8dc; padding: 8px 12px; text-align: left; }}
  th {{ background: #14323f; color: #fff; }}
  tr:nth-child(even) td {{ background: #f1f5f7; }}
  blockquote {{ border-left: 4px solid var(--accent); margin: 1em 0; padding: .4em 1.1em;
               background: #fff7ef; color: #3a3a3a; border-radius: 0 6px 6px 0; }}
  .mermaid {{ text-align: center; margin: 1.5em 0; }}
  .doc-banner {{ background: linear-gradient(90deg, #14323f, #1f6f8b); color: #fff;
                padding: 14px 20px; border-radius: 8px; margin-bottom: 8px; font-size: .95em; }}
  hr {{ border: none; border-top: 1px solid #dde3e6; margin: 2em 0; }}
</style>
</head>
<body>
<div class="doc-banner">{banner}</div>
{body}
</body>
</html>
"""


def _protect(text: str, pattern: str, store: list, prefix: str, flags: int = 0) -> str:
    """Substitui trechos casados por placeholders inertes (guardados em `store`).

    O `prefix` isola o namespace de cada tipo de conteúdo (ex.: ``MM`` p/ mermaid,
    ``MX`` p/ math) para que os tokens **nunca colidam** entre stores diferentes —
    do contrário o primeiro fluxograma e a primeira equação receberiam o mesmo
    token e um sobrescreveria o outro na restauração. Os tokens usam só letras e
    dígitos (sem ``_``, que o Markdown transformaria em ênfase) para sobreviverem
    intactos à conversão.
    """

    def _repl(m: re.Match) -> str:
        store.append(m.group(0))
        return f"@@CMTOK{prefix}{len(store) - 1}@@"

    return re.sub(pattern, _repl, text, flags=flags)


_DEFAULT_TITLE = "Metodologia — Índice LOCZCIT-PA (CartoMet BR)"
_DEFAULT_BANNER = (
    "📘 <b>CartoMet BR v3.0</b> — Metodologia científica do Índice LOCZCIT-PA. "
    "Equações e fluxogramas são renderizados pelo seu navegador."
)


def render_methodology_html(
    md_path: Path,
    out_path: Path | None = None,
    *,
    title: str = _DEFAULT_TITLE,
    banner: str = _DEFAULT_BANNER,
) -> Path:
    """Converte um Markdown científico/didático em HTML estilizado e retorna o caminho.

    LaTeX (`$...$`, `$$...$$`) e blocos ```mermaid``` são protegidos antes da
    conversão Markdown e restaurados depois, evitando que o conversor os corrompa
    (ex.: subscritos `$T_s$` virando itálico).

    ``title`` (aba do navegador) e ``banner`` (faixa no topo da página) são
    parametrizáveis para reusar o mesmo pipeline em outros documentos além da
    metodologia LOCZCIT-PA (ex.: materiais de estudo). Os defaults preservam o
    comportamento das chamadas existentes.
    """
    import markdown as _md

    text = Path(md_path).read_text(encoding="utf-8")

    mermaid: list[str] = []
    math: list[str] = []

    # 1) Protege blocos mermaid (viram <div class="mermaid">), depois math.
    #    Prefixos distintos (MM / MX) evitam colisão de token entre os dois stores.
    text = _protect(text, r"```mermaid\s*\n(.*?)```", mermaid, "MM", flags=re.DOTALL)
    text = _protect(text, r"\$\$.+?\$\$", math, "MX", flags=re.DOTALL)  # display
    text = _protect(text, r"\$[^$\n]+?\$", math, "MX")  # inline

    # 2) Markdown → HTML
    body = _md.markdown(text, extensions=["tables", "fenced_code", "sane_lists", "attr_list"])

    # 3) Restaura mermaid como <div> e math como texto cru (MathJax processa)
    for i, block in enumerate(mermaid):
        inner = re.sub(r"^```mermaid\s*\n", "", block)
        inner = re.sub(r"```$", "", inner).strip()
        div = f'<div class="mermaid">\n{inner}\n</div>'
        body = body.replace(f"@@CMTOKMM{i}@@", div)
        body = body.replace(f"<p>{div}</p>", div)
    for i, expr in enumerate(math):
        body = body.replace(f"@@CMTOKMX{i}@@", expr)

    html = _HTML_TEMPLATE.format(body=body, title=title, banner=banner)

    if out_path is None:
        # Nome derivado do .md de origem: a metodologia do LOCZCIT-PA e a do Bloqueio
        # Z500 usam esta mesma função; sem isso, ambas gravariam no MESMO arquivo
        # temporário e uma sobrescreveria a outra ao abrir no navegador.
        out_path = Path(tempfile.gettempdir()) / f"cartomet_{Path(md_path).stem}.html"
    out_path = Path(out_path)
    out_path.write_text(html, encoding="utf-8")
    return out_path
