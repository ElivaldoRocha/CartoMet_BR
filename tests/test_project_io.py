"""Testes do persistidor de projeto (.cmbr) — gui/project_io.py.

Puros: não instanciam QApplication nem matplotlib. Cobrem o round-trip
comando→record→comando de cada tipo de desenho, o envelope JSON versionado e a
validação (JSON corrompido, raiz inválida, schema ausente, versão futura).
"""

import json

import pytest

from cartomet_br.gui.draw_tools import (
    AnnotationCommand,
    DrawCommand,
    EmojiCommand,
    PenCommand,
    PointCommand,
    ShapeCommand,
)
from cartomet_br.gui.project_io import (
    PROJECT_SCHEMA_VERSION,
    ProjectError,
    build_project,
    command_to_record,
    commands_to_records,
    dump_project,
    load_project,
    record_to_command,
    records_to_commands,
)


def _sample_commands() -> list:
    return [
        DrawCommand(symbol_key="6", points_x=[-40.0, -35.0, -30.0],
                    points_y=[2.0, 1.5, 1.0], flip=True, intensity=3),
        PointCommand(symbol_key="a", x=-45.0, y=-10.0),
        AnnotationCommand(x=-50.0, y=5.0, text="ZCIT forte", color="#CC0000", fontsize=12),
        EmojiCommand(x=-38.0, y=0.5, emoji="⛈", fontsize=40),
        PenCommand(points_x=[-20.0, -21.0, -22.0], points_y=[-3.0, -3.5, -4.0],
                   style={"edge_color": "#2E86C1", "fill_color": None,
                          "linewidth": 2.0, "linestyle": "dashed", "alpha": 0.8}),
        ShapeCommand(tool="arrow", points_x=[-10.0, -5.0], points_y=[0.0, 4.0],
                     style={"edge_color": "#000000", "fill_color": None,
                            "linewidth": 3.0, "linestyle": "solid", "alpha": 1.0},
                     head_size_deg=1.25),
    ]


@pytest.mark.parametrize("cmd", _sample_commands())
def test_command_record_roundtrip(cmd):
    """Cada comando sobrevive a command_to_record → JSON → record_to_command."""
    rec = command_to_record(cmd)
    # O record precisa ser JSON-serializável (sem objetos vivos).
    rec2 = json.loads(json.dumps(rec, ensure_ascii=False))
    back = record_to_command(rec2)
    assert type(back) is type(cmd)
    # Compara campos puros (ignora o handle 'artist', que não é serializado).
    for f in vars(cmd):
        if f == "artist":
            continue
        assert getattr(back, f) == getattr(cmd, f), f"campo {f} divergiu"


def test_intensity_and_flip_preserved():
    rec = command_to_record(DrawCommand("6", [-1.0], [-1.0], flip=True, intensity=2))
    back = record_to_command(rec)
    assert back.flip is True
    assert back.intensity == 2


def test_full_project_roundtrip():
    drawings = commands_to_records(_sample_commands())
    proj = build_project(
        extent=[-55.0, -15.0, 15.0, 15.0], theme="Clássico",
        data_context={"cycle": 0, "cycle_date": "20260614", "step": 24},
        layers=[{"type": "pl_field", "variable": "wind", "level": 850,
                 "wind_type": "barbs", "visible": True}],
        drawings=drawings, app_version="3.0.0",
    )
    text = dump_project(proj)
    loaded = load_project(text)
    assert loaded["schema_version"] == PROJECT_SCHEMA_VERSION
    assert loaded["map"]["extent"] == [-55.0, -15.0, 15.0, 15.0]
    assert loaded["map"]["theme"] == "Clássico"
    assert loaded["data_context"]["step"] == 24
    cmds = records_to_commands(loaded["drawings"])
    assert len(cmds) == len(_sample_commands())
    assert isinstance(cmds[0], DrawCommand)


def test_layers_manifest_roundtrips_for_cache_restore():
    """O manifesto de camadas (e a 'technique') sobrevive ao round-trip JSON —
    é o que o open-project usa para redesenhar os campos a partir do cache."""
    layers = [
        {"kind": "synoptic", "step": 0,
         "visibility": {"pnmm": True, "thickness": False, "centers": True}},
        {"kind": "field", "layer_id": "wind_850_barbs", "variable": "wind",
         "level": 850, "step": 24, "wind_type": "barbs"},
        {"kind": "field", "layer_id": "olr", "variable": "olr",
         "level": 0, "step": 12, "wind_type": "barbs"},
        {"kind": "satellite", "filename": "OR_ABI-L2-CMIPF-M6C13_G19_s2026.nc"},
    ]
    proj = build_project(
        extent=[-55.0, -15.0, 15.0, 15.0], theme="Branco",
        data_context={"cycle": 12, "cycle_date": "20260614", "step": 0,
                      "technique": "stabilized"},
        layers=layers, drawings=[], app_version="3.0.0",
    )
    loaded = load_project(dump_project(proj))
    assert loaded["layers"] == layers
    assert loaded["data_context"]["technique"] == "stabilized"
    # tipos preservados (JSON não deve converter ints em str etc.)
    assert loaded["layers"][1]["level"] == 850
    assert loaded["layers"][0]["visibility"]["thickness"] is False


def test_command_to_record_rejects_unknown_object():
    with pytest.raises(ProjectError):
        command_to_record(object())


def test_record_to_command_rejects_unknown_type():
    with pytest.raises(ProjectError):
        record_to_command({"type": "telepatia", "x": 0, "y": 0})


def test_record_to_command_rejects_malformed():
    with pytest.raises(ProjectError):
        record_to_command({"type": "symbol_point"})  # faltam x/y/symbol_key


def test_records_to_commands_requires_list():
    with pytest.raises(ProjectError):
        records_to_commands({"not": "a list"})


def test_load_project_rejects_bad_json():
    with pytest.raises(ProjectError):
        load_project("{nao é json}")


def test_load_project_rejects_non_dict_root():
    with pytest.raises(ProjectError):
        load_project("[1, 2, 3]")


def test_load_project_rejects_missing_version():
    with pytest.raises(ProjectError):
        load_project(json.dumps({"map": {}}))


def test_load_project_rejects_future_version():
    text = json.dumps({"schema_version": PROJECT_SCHEMA_VERSION + 1})
    with pytest.raises(ProjectError):
        load_project(text)


def test_load_project_rejects_bool_version():
    # True é instância de int em Python — não pode passar como schema_version.
    with pytest.raises(ProjectError):
        load_project(json.dumps({"schema_version": True}))
