"""Overlay dos Avisos INMET no canvas — distinção hoje × futuro (offscreen).

Avisos EM VIGOR (``quando="hoje"``) saem com contorno sólido; os FUTUROS
(``quando="futuro"`` — emitidos, validade por começar) saem tracejados, com
preenchimento mais leve e sufixo "(futuro)" no rótulo. O filtro dos futuros é
da MainWindow (a lista já chega filtrada ao canvas).
"""

import pytest

pytest.importorskip("PyQt6")

from cartomet_br.data.inmet_avisos import AvisoINMET

# Fixtures qapp/canvas: tests/conftest.py (fonte única).


def _aviso(quando: str, severidade: str = "Perigo") -> AvisoINMET:
    ring = [(-53.0, -26.0), (-51.0, -26.0), (-51.0, -24.0), (-53.0, -24.0)]
    return AvisoINMET(
        severidade=severidade,
        descricao="Tempestade",
        cor="#F96602",
        riscos=[],
        instrucoes=[],
        estados="PR",
        inicio="",
        fim="",
        quando=quando,
        rings=[ring],
    )


def test_future_aviso_is_dashed_and_labeled(canvas):
    """Futuro = contorno tracejado + rótulo com sufixo; hoje = sólido, sem sufixo."""
    from matplotlib.lines import Line2D
    from matplotlib.text import Text

    canvas.render_inmet_avisos([_aviso("hoje"), _aviso("futuro")])
    artists = canvas._inmet_avisos_artists
    lines = [a for a in artists if isinstance(a, Line2D)]
    assert len(lines) == 2
    solid = [ln for ln in lines if ln.get_linestyle() in ("-", "solid")]
    dashed = [ln for ln in lines if ln not in solid]
    assert len(solid) == 1 and len(dashed) == 1, "esperava 1 contorno sólido e 1 tracejado"

    labels = sorted(a.get_text() for a in artists if isinstance(a, Text))
    assert labels == ["Perigo", "Perigo (futuro)"]


def test_future_fill_is_lighter(canvas):
    """O preenchimento do aviso futuro é mais translúcido que o do em vigor."""
    from matplotlib.patches import Polygon

    canvas.render_inmet_avisos([_aviso("hoje"), _aviso("futuro")])
    fills = [a for a in canvas._inmet_avisos_artists if isinstance(a, Polygon)]
    assert len(fills) == 2
    alphas = sorted(float(f.get_alpha()) for f in fills)
    assert alphas[0] < alphas[1], "futuro deveria ser mais leve que o em vigor"


def test_hoje_only_render_has_no_dashed(canvas):
    """Lista já filtrada (só 'hoje') não produz nenhum contorno tracejado."""
    from matplotlib.lines import Line2D

    canvas.render_inmet_avisos([_aviso("hoje"), _aviso("hoje", "Grande Perigo")])
    lines = [a for a in canvas._inmet_avisos_artists if isinstance(a, Line2D)]
    assert lines and all(ln.get_linestyle() in ("-", "solid") for ln in lines)
