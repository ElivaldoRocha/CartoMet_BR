"""Testes do boletim de análise codificado (CODSAS) — bulletin_io.

Cobrem: round-trip de todas as feições, hemisfério sul + longitude LESTE
(o caso que o encoding compactado do WPC não representa), quebra de linha
com continuação, tolerância a prosa/cabeçalho, versionamento, erros claros
e a importação de boletins WPC genuínos via MetPy.
"""

import pytest

from cartomet_br.gui import bulletin_io, project_io
from cartomet_br.gui.bulletin_io import (
    BulletinError,
    commands_bbox,
    dump_bulletin,
    exportable_commands,
    parse_bulletin,
)
from cartomet_br.gui.draw_tools import (
    AnnotationCommand,
    DrawCommand,
    PenCommand,
    PointCommand,
)

# ─── Fixtures de comandos ──────────────────────────────────────────────────────


def _front(key: str, flip: bool = False, intensity: int = 1) -> DrawCommand:
    return DrawCommand(
        symbol_key=key,
        points_x=[-45.25, -48.1, -52.4],
        points_y=[-23.5, -25.0, -27.21],
        flip=flip,
        intensity=intensity,
    )


# Boletim WPC genuíno (CODSUS) em miniatura — estrutura real: header com ano,
# VALID MMDDHH, HIGHS/LOWS com pressão central, frentes com continuação de
# linha e a extensão de latitude sul (prefixo "-").
WPC_SAMPLE = """\
ASUS01 KWBC 281800
CODSUS

CODED SURFACE FRONTAL POSITIONS
NWS WEATHER PREDICTION CENTER COLLEGE PARK MD
608 PM EDT MON JUN 28 2021

VALID 062818Z
HIGHS 1022 4000750 1020 3480880
LOWS 1008 3510750 1002 -2550621
COLD WK 3800800 3500850 3100920
2800990
WARM 4731193 4741190
STNRY 3730930 3731000
TROF 2501000 2701050
"""


# ─── Round-trip CODSAS ─────────────────────────────────────────────────────────


def test_roundtrip_todas_as_linhas():
    """Todas as 13 feições de linha voltam com tipo, flip, intensidade e coords."""
    cmds = [
        _front("1", flip=True),
        _front("2"),
        _front("3", flip=True),
        _front("4"),
        _front("5"),
        _front("6", intensity=3),
        _front("7"),
        _front("8"),
        _front("9"),
        _front("0"),
        _front("e", flip=True),
        _front("d"),
        _front("j"),
    ]
    text = dump_bulletin(cmds, valid_time="2026-07-06T12:00")
    parsed = parse_bulletin(text)

    assert parsed.fmt == "CODSAS"
    assert parsed.valid_time == "2026-07-06T12:00"
    assert len(parsed.commands) == len(cmds)
    by_key = {c.symbol_key: c for c in parsed.commands}
    assert set(by_key) == {c.symbol_key for c in cmds}
    assert by_key["1"].flip is True
    assert by_key["2"].flip is False
    assert by_key["6"].intensity == 3
    assert by_key["6"].points_x == pytest.approx([-45.25, -48.1, -52.4], abs=0.005)
    assert by_key["6"].points_y == pytest.approx([-23.5, -25.0, -27.21], abs=0.005)


def test_roundtrip_pontos_e_anotacao():
    """Símbolos pontuais e anotação (acentos + aspas) sobrevivem ao round-trip."""
    cmds = [
        PointCommand(symbol_key="a", x=-20.0, y=-30.5),
        PointCommand(symbol_key="b", x=-60.2, y=-25.5),
        PointCommand(symbol_key="h", x=-35.0, y=-15.0),
        PointCommand(symbol_key="t", x=-40.0, y=-18.0),
        PointCommand(symbol_key="v", x=-38.0, y=-8.0),
        AnnotationCommand(
            x=-42.0,
            y=-18.0,
            text='Ciclone "Yakecan" — vento ≥ 60 kt',
            color="#FFD700",
            fontsize=14,
        ),
    ]
    parsed = parse_bulletin(dump_bulletin(cmds))

    points = [c for c in parsed.commands if isinstance(c, PointCommand)]
    notes = [c for c in parsed.commands if isinstance(c, AnnotationCommand)]
    assert [p.symbol_key for p in points] == ["a", "b", "h", "t", "v"]
    assert points[1].x == pytest.approx(-60.2)
    assert points[1].y == pytest.approx(-25.5)
    assert len(notes) == 1
    assert notes[0].text == 'Ciclone "Yakecan" — vento ≥ 60 kt'
    assert notes[0].color == "#FFD700"
    assert notes[0].fontsize == 14


def test_longitude_leste_preservada():
    """ZCIT cruzando Greenwich (até 15°E) — o caso irrepresentável no WPC."""
    zcit = DrawCommand(
        symbol_key="6",
        points_x=[-10.0, 5.0, 15.0],
        points_y=[2.0, 1.5, 3.0],
        flip=False,
        intensity=2,
    )
    parsed = parse_bulletin(dump_bulletin([zcit]))
    (cmd,) = parsed.commands
    assert cmd.points_x == pytest.approx([-10.0, 5.0, 15.0])
    assert cmd.points_y == pytest.approx([2.0, 1.5, 3.0])
    assert cmd.intensity == 2


def test_quebra_de_linha_com_continuacao():
    """Frente com muitos vértices quebra em ~76 colunas e reparseia inteira."""
    n = 40
    cmd = DrawCommand(
        symbol_key="1",
        points_x=[-70.0 + i * 0.7 for i in range(n)],
        points_y=[-30.0 + i * 0.3 for i in range(n)],
        flip=False,
    )
    text = dump_bulletin([cmd])
    assert max(len(line) for line in text.splitlines()) <= 76
    # há pelo menos uma linha de continuação (começa com coordenada)
    assert any(
        bulletin_io._COORD_RE.match(line.split()[0]) for line in text.splitlines() if line.strip()
    )
    parsed = parse_bulletin(text)
    (out,) = parsed.commands
    assert len(out.points_x) == n
    assert out.points_x == pytest.approx(cmd.points_x, abs=0.005)
    assert out.points_y == pytest.approx(cmd.points_y, abs=0.005)


def test_prosa_e_flags_manuais():
    """Boletim escrito à mão: prosa/comentários ignorados, flags reconhecidas."""
    text = """
    Análise da tarde — rascunho do plantonista.
    # comentário
    CODSAS V1
    VALID 2026-07-06T12:00Z

    COLD FLIP -23.50,-45.20 -25.00,-48.10
    ZCIT INT2 2.00,-45.00 1.50,-40.00
    HIGH -30.00,-20.00
    """
    parsed = parse_bulletin(text)
    assert parsed.valid_time == "2026-07-06T12:00"
    fria, zcit, alta = parsed.commands
    assert isinstance(fria, DrawCommand) and fria.symbol_key == "1" and fria.flip
    assert isinstance(zcit, DrawCommand) and zcit.intensity == 2
    assert isinstance(alta, PointCommand) and alta.symbol_key == "a"


# ─── Erros e versionamento ─────────────────────────────────────────────────────


def test_versao_futura_recusada():
    with pytest.raises(BulletinError, match="versão mais nova"):
        parse_bulletin("CODSAS V2\nCOLD -10.00,-40.00 -12.00,-42.00\n")


def test_linha_com_um_unico_ponto():
    with pytest.raises(BulletinError, match="2 coordenadas"):
        parse_bulletin("CODSAS V1\nCOLD -10.00,-40.00\n")


def test_ponto_sem_coordenada():
    with pytest.raises(BulletinError, match="sem coordenada"):
        parse_bulletin("CODSAS V1\nHIGH\nLOW -25.00,-60.00\n")


def test_coordenada_fora_do_intervalo():
    with pytest.raises(BulletinError, match="fora do intervalo"):
        parse_bulletin("CODSAS V1\nLOW 95.00,-40.00\n")


def test_vazio_ou_prosa_pura():
    with pytest.raises(BulletinError):
        parse_bulletin("")
    with pytest.raises(BulletinError):
        parse_bulletin("lorem ipsum\nsó prosa, nenhuma feição\n")


# ─── Boletim WPC genuíno (via MetPy) ───────────────────────────────────────────


def test_wpc_genuino():
    """O exemplo motivador: um CODSUS real decodifica em comandos do CartoMet."""
    parsed = parse_bulletin(WPC_SAMPLE)
    assert parsed.fmt == "WPC"
    assert parsed.valid_time == "2021-06-28T18:00"

    points = [c for c in parsed.commands if isinstance(c, PointCommand)]
    notes = [c for c in parsed.commands if isinstance(c, AnnotationCommand)]
    lines = [c for c in parsed.commands if isinstance(c, DrawCommand)]

    # 2 altas + 2 baixas (uma no hemisfério sul, via prefixo "-")
    assert [p.symbol_key for p in points] == ["a", "a", "b", "b"]
    assert points[0].x == pytest.approx(-75.0)
    assert points[0].y == pytest.approx(40.0)
    assert points[3].x == pytest.approx(-62.1)
    assert points[3].y == pytest.approx(-25.5)
    # pressões centrais viram rótulos (como na carta do WPC)
    assert [n.text for n in notes] == ["1022", "1020", "1008", "1002"]

    # COLD (com linha de continuação) + WARM (hires 7 dígitos) + STNRY + TROF
    assert [ln.symbol_key for ln in lines] == ["1", "2", "3", "7"]
    cold = lines[0]
    assert len(cold.points_x) == 4  # a continuação "2800990" foi absorvida
    assert cold.points_x[0] == pytest.approx(-80.0)
    assert cold.points_y[0] == pytest.approx(38.0)
    warm = lines[1]
    assert warm.points_x[0] == pytest.approx(-119.3)
    assert warm.points_y[0] == pytest.approx(47.3)


def test_wpc_reexportado_como_codsas():
    """Conversão WPC → CODSAS → reimport: mesmas feições (ponte entre formatos)."""
    parsed = parse_bulletin(WPC_SAMPLE)
    text = dump_bulletin(parsed.commands, valid_time=parsed.valid_time)
    again = parse_bulletin(text)
    assert again.fmt == "CODSAS"
    assert again.valid_time == "2021-06-28T18:00"
    assert len(again.commands) == len(parsed.commands)
    assert {type(c).__name__ for c in again.commands} == {type(c).__name__ for c in parsed.commands}


# ─── Helpers para a GUI ────────────────────────────────────────────────────────


def test_exportable_commands_filtra_nao_feicoes():
    pen = PenCommand(points_x=[-40.0, -41.0], points_y=[-10.0, -11.0], style={})
    cmds = [_front("1"), pen, PointCommand(symbol_key="b", x=-50.0, y=-20.0)]
    keep, skipped = exportable_commands(cmds)
    assert [type(c).__name__ for c in keep] == ["DrawCommand", "PointCommand"]
    assert skipped == 1


def test_commands_bbox():
    cmds = [_front("1"), PointCommand(symbol_key="a", x=-10.0, y=5.0)]
    assert commands_bbox(cmds) == pytest.approx((-52.4, -27.21, -10.0, 5.0))
    assert commands_bbox([]) is None


def test_ponte_com_project_io():
    """O caminho da GUI: comandos importados viram records .cmbr válidos."""
    parsed = parse_bulletin(WPC_SAMPLE)
    records = project_io.commands_to_records(parsed.commands)
    back = project_io.records_to_commands(records)
    assert len(back) == len(parsed.commands)
