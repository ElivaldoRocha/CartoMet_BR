"""Manual de Sinótica Operacional (Onda 0a) — conteúdo oficial + fiação da GUI.

Parte 1 (pura, sem Qt): o HTML existe em docs/, não carrega resíduos do material
pessoal (pasta git-ignored, tom de treino individual) e as alegações factuais
sobre o app (níveis, símbolos, presets, colormaps) continuam verdadeiras — estes
testes são travas de deriva: se o app mudar, eles cobram a atualização do manual.

Parte 2 (offscreen): o helper de caminho resolve o arquivo e
``_show_study_sinotica`` monta o diálogo sem erro (``QDialog.exec``
neutralizado). Roda sob ``QT_QPA_PLATFORM=offscreen``.
"""

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_MANUAL = Path(__file__).resolve().parents[1] / "docs" / "Manual_Sinotica_Operacional.html"


def _html() -> str:
    return _MANUAL.read_text(encoding="utf-8")


def test_manual_exists():
    assert _MANUAL.exists(), f"manual ausente: {_MANUAL}"


def test_sem_residuos_do_material_pessoal():
    """O manual virou material oficial: nada da oficina pessoal pode sobrar."""
    html = _html()
    for proibido in (
        "_rascunhos",  # pasta git-ignored não existe na instalação
        "material de estudo pessoal",
        "Treinamento pessoal",
        "skill disponível",  # resíduo de conversa, não é termo do produto
        "fora do produto",
    ):
        assert proibido not in html, f"resíduo do material pessoal: {proibido!r}"
    assert "Material de estudo oficial do CartoMet BR" in html


def test_marcadores_didaticos_essenciais():
    html = _html()
    for marker in ("Buys-Ballot", "VCAN", "RdBu_r", "Hemisfério Sul", "ZCAS", "jet streak"):
        assert marker in html, f"faltou marcador didático: {marker}"


def test_contagem_de_niveis_confere():
    """'O CartoMet entrega N níveis' deve bater com PL_LEVELS."""
    from cartomet_br.data.ecmwf import PL_LEVELS

    m = re.search(r"entrega (\d+) níveis", _html())
    assert m, "manual não declara a contagem de níveis"
    assert int(m.group(1)) == len(PL_LEVELS), (
        f"manual diz {m.group(1)} níveis; PL_LEVELS tem {len(PL_LEVELS)} — "
        "atualize docs/Manual_Sinotica_Operacional.html"
    )


def test_contagem_de_simbolos_confere():
    """'Os N símbolos e suas teclas são os reais do app' deve bater com MODOS."""
    from cartomet_br.symbols import MODOS

    m = re.search(r"Os (\d+) símbolos", _html())
    assert m, "manual não declara a contagem de símbolos"
    assert int(m.group(1)) == len(MODOS), (
        f"manual diz {m.group(1)} símbolos; MODOS tem {len(MODOS)} — "
        "atualize docs/Manual_Sinotica_Operacional.html"
    )


def test_teclas_citadas_existem():
    """Toda tecla <span class="key">X</span> citada no manual é um modo real."""
    from cartomet_br.symbols import MODOS

    teclas = set(re.findall(r'class="key">([^<]+)</span>', _html()))
    assert teclas, "manual não cita teclas de símbolo"
    fantasma = teclas - set(MODOS)
    assert not fantasma, f"teclas citadas que não existem em MODOS: {sorted(fantasma)}"


def test_presets_citados_e_completos():
    """Cada preset de Análises Prontas aparece no manual (e vice-versa não deriva)."""
    from cartomet_br.gui.layer_panel import FieldLayerPanel

    html = _html()
    for nome in FieldLayerPanel.ANALYSIS_PRESETS:
        assert nome in html, (
            f"preset {nome!r} não citado no manual — atualize docs/Manual_Sinotica_Operacional.html"
        )


def test_colormaps_citados_conferem():
    """O cheat-sheet hardcoda cmaps; se o registry mudar, o manual tem de mudar."""
    from cartomet_br.data.ecmwf import VARIABLE_REGISTRY

    esperado = {
        "t": "RdBu_r",
        "temp_adv": "RdBu_r",
        "vo": "RdBu_r",
        "d": "RdBu_r",
        "frontogenesis": "RdBu_r",
        "w": "RdBu",  # invertido de propósito: vermelho = ω negativo = ascensão
        "mfc": "BrBG",
        "r": "BrBG",
        "q": "BrBG",
        "wind_speed": "YlOrRd",
        "tcwv": "YlGnBu",
        "cape": "YlOrRd",
        "kindex": "YlOrRd",
        "li": "RdBu_r",
        "olr": "olr_classic",
    }
    for var, cmap in esperado.items():
        real = VARIABLE_REGISTRY[var]["cmap"]
        assert real == cmap, (
            f"registry mudou: {var} usa {real!r} (manual assume {cmap!r}) — "
            "atualize docs/Manual_Sinotica_Operacional.html"
        )


def test_unidade_de_omega_confere():
    """O manual afirma 'Unidade no CartoMet: hPa/h (já convertido)'."""
    from cartomet_br.data.ecmwf import VARIABLE_REGISTRY

    assert VARIABLE_REGISTRY["w"]["unit_display"] == "hPa/h"
    assert "hPa/h" in _html()


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


def test_manual_path_resolves(window):
    p = window._sinotica_manual_path()
    assert p is not None and p.exists()
    assert p.name == "Manual_Sinotica_Operacional.html"


def test_show_manual_dialog_builds(window, monkeypatch):
    # Neutraliza o modal bloqueante: o diálogo deve montar sem levantar exceção.
    from PyQt6.QtWidgets import QDialog

    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    window._show_study_sinotica()
