"""Persistência do "projeto de análise" do CartoMet BR (``.cmbr``).

Módulo **PURO** (sem PyQt/matplotlib) — serializa o estado de uma análise
(desenhos + estado do mapa) para **JSON versionado**, testável sem GUI.

Filosofia: o ``.cmbr`` guarda **apenas dados puros** (geometria lon/lat +
parâmetros). A reconstrução dos artistas matplotlib é responsabilidade do
``MapCanvas.import_drawings_state`` — que reusa o mesmo caminho do *redo* —,
preservando o *human-in-the-loop*: abrir um projeto **nunca** dispara rede.

O formato é estável e versionado (``schema_version``): um arquivo de versão
mais nova que a suportada é recusado com mensagem clara (em vez de corromper a
leitura silenciosamente).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from cartomet_br.gui.draw_tools import (
    AnnotationCommand,
    DrawCommand,
    EmojiCommand,
    PenCommand,
    PointCommand,
    ShapeCommand,
)

PROJECT_SCHEMA_VERSION = 1
PROJECT_EXTENSION = ".cmbr"


class ProjectError(Exception):
    """Erro ao ler/validar um arquivo de projeto (``.cmbr``)."""


# ═══════════════════════════════════════════════════════════════════════════════
#  Comando ↔ record (dict type-tagged, JSON-serializável)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Cada record carrega uma chave ``type`` e os campos PUROS do comando (sem o
# handle ``artist``). A geometria é sempre lon/lat; a reconstrução é determinística.


def _floats(seq: Any) -> list[float]:
    return [float(v) for v in seq]


def command_to_record(cmd: object) -> dict[str, Any]:
    """Converte um comando de desenho num record JSON (descarta o ``artist`` vivo)."""
    if isinstance(cmd, DrawCommand):
        return {
            "type": "symbol_line",
            "symbol_key": cmd.symbol_key,
            "points_x": _floats(cmd.points_x),
            "points_y": _floats(cmd.points_y),
            "flip": bool(cmd.flip),
            "intensity": int(cmd.intensity),
        }
    if isinstance(cmd, PointCommand):
        return {
            "type": "symbol_point",
            "symbol_key": cmd.symbol_key,
            "x": float(cmd.x),
            "y": float(cmd.y),
        }
    if isinstance(cmd, AnnotationCommand):
        return {
            "type": "annotation",
            "x": float(cmd.x),
            "y": float(cmd.y),
            "text": str(cmd.text),
            "color": str(cmd.color),
            "fontsize": int(cmd.fontsize),
        }
    if isinstance(cmd, EmojiCommand):
        return {
            "type": "emoji",
            "x": float(cmd.x),
            "y": float(cmd.y),
            "emoji": str(cmd.emoji),
            "fontsize": int(cmd.fontsize),
        }
    if isinstance(cmd, PenCommand):
        return {
            "type": "pen",
            "points_x": _floats(cmd.points_x),
            "points_y": _floats(cmd.points_y),
            "style": dict(cmd.style),
        }
    if isinstance(cmd, ShapeCommand):
        return {
            "type": "shape",
            "tool": str(cmd.tool),
            "points_x": _floats(cmd.points_x),
            "points_y": _floats(cmd.points_y),
            "style": dict(cmd.style),
            "head_size_deg": float(cmd.head_size_deg),
        }
    raise ProjectError(f"Tipo de comando não serializável: {type(cmd).__name__}")


def record_to_command(rec: dict) -> object:
    """Reconstrói um comando (sem ``artist``) a partir de um record JSON."""
    if not isinstance(rec, dict):
        raise ProjectError("Record de desenho inválido (não é um objeto JSON).")
    kind = rec.get("type")
    try:
        if kind == "symbol_line":
            return DrawCommand(
                symbol_key=str(rec["symbol_key"]),
                points_x=_floats(rec["points_x"]),
                points_y=_floats(rec["points_y"]),
                flip=bool(rec.get("flip", False)),
                intensity=int(rec.get("intensity", 1)),
            )
        if kind == "symbol_point":
            return PointCommand(
                symbol_key=str(rec["symbol_key"]), x=float(rec["x"]), y=float(rec["y"])
            )
        if kind == "annotation":
            return AnnotationCommand(
                x=float(rec["x"]),
                y=float(rec["y"]),
                text=str(rec.get("text", "")),
                color=str(rec.get("color", "#000000")),
                fontsize=int(rec.get("fontsize", 11)),
            )
        if kind == "emoji":
            return EmojiCommand(
                x=float(rec["x"]),
                y=float(rec["y"]),
                emoji=str(rec.get("emoji", "")),
                fontsize=int(rec.get("fontsize", 28)),
            )
        if kind == "pen":
            return PenCommand(
                points_x=_floats(rec["points_x"]),
                points_y=_floats(rec["points_y"]),
                style=dict(rec.get("style", {})),
            )
        if kind == "shape":
            return ShapeCommand(
                tool=str(rec["tool"]),
                points_x=_floats(rec["points_x"]),
                points_y=_floats(rec["points_y"]),
                style=dict(rec.get("style", {})),
                head_size_deg=float(rec.get("head_size_deg", 0.0)),
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectError(f"Record de desenho '{kind}' malformado: {exc}") from exc
    raise ProjectError(f"Record de desenho desconhecido: {kind!r}")


def commands_to_records(commands: list) -> list[dict]:
    """Serializa uma lista de comandos (ignora itens não serializáveis? não — falha cedo)."""
    return [command_to_record(c) for c in commands]


def records_to_commands(records: Any) -> list:
    """Desserializa a lista de records de desenho em comandos."""
    if not isinstance(records, list):
        raise ProjectError("'drawings' deve ser uma lista.")
    return [record_to_command(r) for r in records]


# ═══════════════════════════════════════════════════════════════════════════════
#  Envelope do projeto (JSON versionado)
# ═══════════════════════════════════════════════════════════════════════════════


def build_project(
    *,
    extent: Any,
    theme: str | None,
    data_context: dict | None,
    layers: list | None,
    drawings: list[dict],
    app_version: str = "",
) -> dict:
    """Monta o dict do projeto a partir das partes já prontas (records de desenho)."""
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "app_version": app_version,
        "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "map": {
            "extent": list(extent) if extent is not None else None,
            "theme": theme,
        },
        "data_context": dict(data_context or {}),
        "layers": list(layers or []),
        "drawings": list(drawings or []),
    }


def dump_project(project: dict) -> str:
    """Serializa o dict do projeto em JSON (UTF-8 amigável, indentado)."""
    return json.dumps(project, ensure_ascii=False, indent=2)


def load_project(text: str) -> dict:
    """Carrega e valida o JSON de um projeto. ``ProjectError`` em qualquer inconsistência.

    Valida: JSON bem-formado, raiz objeto, ``schema_version`` inteiro e **não
    superior** à versão suportada (arquivo de versão futura é recusado).
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProjectError(f"Arquivo de projeto inválido (JSON): {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectError("Projeto inválido: a raiz não é um objeto JSON.")
    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ProjectError("Projeto inválido: 'schema_version' ausente ou não inteiro.")
    if version > PROJECT_SCHEMA_VERSION:
        raise ProjectError(
            f"Projeto criado numa versão mais nova (schema {version} > "
            f"{PROJECT_SCHEMA_VERSION}). Atualize o CartoMet BR para abri-lo."
        )
    return data
