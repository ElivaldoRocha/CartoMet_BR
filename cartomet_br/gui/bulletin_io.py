"""Boletim de análise codificado (CODSAS) — exportar/importar o traçado manual.

Módulo **PURO** (sem PyQt/matplotlib) — converte os comandos de desenho do
CartoMet BR num boletim de texto compartilhável, no espírito do *coded
surface bulletin* do WPC/NOAA (CODSUS), e faz o caminho inverso.

Por que um formato próprio (CODSAS) em vez do CODSUS do WPC?
O encoding compactado do WPC (``4731193``) nasceu para a América do Norte:
a longitude é SEMPRE em graus oeste (o parser do MetPy a nega
incondicionalmente) e a latitude sul só existe por extensão (prefixo ``-``).
O domínio operacional do CartoMet cruza Greenwich (a ZCIT chega a 15°E) —
irrepresentável naquele encoding. O CODSAS preserva a ESTRUTURA do boletim
(cabeçalho + ``VALID`` + uma feição por linha com a sequência de coordenadas,
com continuação por quebra de linha) mas usa pares decimais assinados
``lat,lon`` (sul/oeste negativos) — legíveis e sem ambiguidade de hemisfério.

A importação aceita AMBOS os formatos: CODSAS (este módulo) e boletins WPC
genuínos (ex.: ``WPC_sfc_fronts_20210628_1800.txt``), estes decodificados via
``metpy.io.parse_wpc_surface_bulletin``. A detecção é automática: o texto que
não render nenhuma feição CODSAS é tentado como WPC.

Escopo: só feições meteorológicas — linhas OMM (``LINE_KEYWORDS``), símbolos
pontuais (``POINT_KEYWORDS``) e anotações de texto (``TEXT``). Caneta, formas
e emojis NÃO entram no boletim; ficam no projeto ``.cmbr`` (``project_io``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from cartomet_br.gui.draw_tools import (
    AnnotationCommand,
    DrawCommand,
    PointCommand,
)

BULLETIN_VERSION = 1
BULLETIN_EXTENSION = ".txt"

# Chave interna (dict MODOS) → palavra-chave do boletim. As quatro frentes e o
# cavado usam os MESMOS nomes do CODSUS do WPC (COLD/WARM/STNRY/OCFNT/TROF);
# as demais são a extensão sul-americana do formato.
LINE_KEYWORDS: dict[str, str] = {
    "1": "COLD",
    "2": "WARM",
    "3": "STNRY",
    "4": "OCFNT",
    "5": "ZCAS",
    "6": "ZCIT",
    "7": "TROF",
    "8": "RIDGE",
    "9": "SQUALL",
    "0": "DRYLINE",
    "e": "FGEN",
    "d": "FLYS",
    "j": "JET",
}
POINT_KEYWORDS: dict[str, str] = {
    "a": "HIGH",
    "b": "LOW",
    "h": "HURCN",
    "t": "TSTORM",
    "v": "VORTEX",
}
_TEXT_KEYWORD = "TEXT"

_KEYWORD_TO_LINE = {v: k for k, v in LINE_KEYWORDS.items()}
_KEYWORD_TO_POINT = {v: k for k, v in POINT_KEYWORDS.items()}

# Feição do DataFrame do MetPy (boletim WPC genuíno) → chave interna.
_WPC_FEATURE_TO_KEY: dict[str, str] = {
    "COLD": "1",
    "WARM": "2",
    "STNRY": "3",
    "OCFNT": "4",
    "TROF": "7",
    "HIGH": "a",
    "LOW": "b",
}
# Cores dos rótulos de pressão central importados de boletins WPC (= MODOS).
_WPC_STRENGTH_COLORS = {"a": "#0066CC", "b": "#CC0000"}

# Par "lat,lon" decimal assinado — o token de coordenada do CODSAS.
_COORD_RE = re.compile(r"^[+-]?\d{1,2}(?:\.\d+)?,[+-]?\d{1,3}(?:\.\d+)?$")
_VALID_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})")
_MAX_LINE_WIDTH = 76


class BulletinError(Exception):
    """Erro ao gerar/decodificar um boletim de análise codificado."""


@dataclass
class ParsedBulletin:
    """Resultado da importação: comandos puros + metadados do boletim."""

    commands: list = field(default_factory=list)
    valid_time: str = ""  # "YYYY-MM-DDTHH:MM" (convenção do app) ou ""
    fmt: str = "CODSAS"  # "CODSAS" | "WPC"


# ═══════════════════════════════════════════════════════════════════════════════
#  Seleção e geometria (helpers para a GUI)
# ═══════════════════════════════════════════════════════════════════════════════


def exportable_commands(commands: list) -> tuple[list, int]:
    """Separa os comandos que entram no boletim dos que não são feições.

    Retorna ``(exportáveis, n_descartados)`` — caneta, formas e afins ficam de
    fora (permanecem no ``.cmbr``); a ordem original é preservada.
    """
    keep: list = []
    skipped = 0
    for cmd in commands:
        if (
            (isinstance(cmd, DrawCommand) and cmd.symbol_key in LINE_KEYWORDS)
            or (isinstance(cmd, PointCommand) and cmd.symbol_key in POINT_KEYWORDS)
            or isinstance(cmd, AnnotationCommand)
        ):
            keep.append(cmd)
        else:
            skipped += 1
    return keep, skipped


def commands_bbox(commands: list) -> tuple[float, float, float, float] | None:
    """Caixa envolvente ``(lon_min, lat_min, lon_max, lat_max)`` das feições.

    ``None`` se não houver coordenadas — usada pelo auto-enquadre da importação.
    """
    lons: list[float] = []
    lats: list[float] = []
    for cmd in commands:
        if isinstance(cmd, DrawCommand):
            lons.extend(cmd.points_x)
            lats.extend(cmd.points_y)
        elif isinstance(cmd, (PointCommand, AnnotationCommand)):
            lons.append(cmd.x)
            lats.append(cmd.y)
    if not lons:
        return None
    return (min(lons), min(lats), max(lons), max(lats))


# ═══════════════════════════════════════════════════════════════════════════════
#  Exportação (comandos → texto CODSAS)
# ═══════════════════════════════════════════════════════════════════════════════


def _fmt_coord(lat: float, lon: float) -> str:
    return f"{lat:.2f},{lon:.2f}"


def _norm_valid(valid_time: str) -> str:
    """Normaliza o valid_time do app ("YYYY-MM-DDTHH:MM") para a linha VALID."""
    vt = valid_time.strip()
    return vt if vt.endswith("Z") else f"{vt}Z"


def _wrap_feature(head: str, coords: list[str]) -> list[str]:
    """Quebra a linha da feição em ~76 colunas; continuações começam com coordenada."""
    lines: list[str] = []
    cur = head
    for tok in coords:
        if len(cur) + 1 + len(tok) > _MAX_LINE_WIDTH:
            lines.append(cur)
            cur = tok
        else:
            cur = f"{cur} {tok}"
    lines.append(cur)
    return lines


def dump_bulletin(
    commands: list,
    *,
    valid_time: str = "",
    base_time: str = "",
    step: int | None = None,
    app_version: str = "",
) -> str:
    """Serializa feições meteorológicas num boletim CODSAS V1 (texto).

    ``commands`` deve conter apenas feições exportáveis (use
    ``exportable_commands``); um comando estranho é erro de programação e
    dispara ``BulletinError`` cedo, em vez de sumir silenciosamente.
    """
    points: list[PointCommand] = []
    lines_cmds: list[DrawCommand] = []
    texts: list[AnnotationCommand] = []
    for cmd in commands:
        if isinstance(cmd, DrawCommand) and cmd.symbol_key in LINE_KEYWORDS:
            lines_cmds.append(cmd)
        elif isinstance(cmd, PointCommand) and cmd.symbol_key in POINT_KEYWORDS:
            points.append(cmd)
        elif isinstance(cmd, AnnotationCommand):
            texts.append(cmd)
        else:
            raise BulletinError(f"Comando não exportável em boletim: {type(cmd).__name__}")

    out: list[str] = [
        "CARTOMET BR - BOLETIM DE ANALISE DE SUPERFICIE CODIFICADA",
        f"CODSAS V{BULLETIN_VERSION}" + (f" CARTOMET-{app_version}" if app_version else ""),
    ]
    if valid_time:
        out.append(f"VALID {_norm_valid(valid_time)}")
    if base_time:
        src = f"SOURCE ECMWF IFS RODADA {base_time}"
        if step is not None:
            src += f" STEP {int(step)}"
        out.append(src)
    out.append("COORDS LAT,LON GRAUS DECIMAIS (SUL/OESTE NEGATIVOS)")
    out.append("")

    # Agrupado por tipo (na ordem das tabelas), como no boletim do WPC —
    # a ordem intra-grupo preserva a ordem de traçado.
    for key, keyword in POINT_KEYWORDS.items():
        for cmd in points:
            if cmd.symbol_key == key:
                out.append(f"{keyword} {_fmt_coord(cmd.y, cmd.x)}")
    for key, keyword in LINE_KEYWORDS.items():
        for cmd in lines_cmds:
            if cmd.symbol_key != key:
                continue
            flags: list[str] = []
            if cmd.flip:
                flags.append("FLIP")
            if key == "6":
                flags.append(f"INT{int(cmd.intensity)}")
            head = " ".join([keyword, *flags])
            coords = [
                _fmt_coord(lat, lon) for lat, lon in zip(cmd.points_y, cmd.points_x, strict=True)
            ]
            out.extend(_wrap_feature(head, coords))
    for cmd in texts:
        quoted = json.dumps(cmd.text, ensure_ascii=False)
        out.append(
            f"{_TEXT_KEYWORD} {_fmt_coord(cmd.y, cmd.x)} {quoted} "
            f"COLOR={cmd.color} SIZE={int(cmd.fontsize)}"
        )
    out.append("")
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════════════════════════
#  Importação (texto → comandos)
# ═══════════════════════════════════════════════════════════════════════════════


def _parse_coord(tok: str, lineno: int) -> tuple[float, float]:
    """Decodifica um token ``lat,lon`` (validado) em ``(lat, lon)``."""
    lat_s, lon_s = tok.split(",", 1)
    lat, lon = float(lat_s), float(lon_s)
    if lon > 180.0:  # normaliza grade 0–360
        lon -= 360.0
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        raise BulletinError(f"linha {lineno}: coordenada fora do intervalo: {tok!r}")
    return lat, lon


def _parse_text_line(line: str, lineno: int) -> AnnotationCommand:
    """Decodifica ``TEXT lat,lon "..." [COLOR=#…] [SIZE=n]``."""
    parts = line.split(maxsplit=2)
    if len(parts) < 3 or not _COORD_RE.match(parts[1]):
        raise BulletinError(f"linha {lineno}: TEXT requer 'lat,lon' e o texto entre aspas.")
    lat, lon = _parse_coord(parts[1], lineno)
    rest = parts[2]
    qidx = rest.find('"')
    if qidx < 0:
        raise BulletinError(f"linha {lineno}: TEXT sem texto entre aspas.")
    try:
        text, end = json.JSONDecoder().raw_decode(rest[qidx:])
    except json.JSONDecodeError as exc:
        raise BulletinError(f"linha {lineno}: texto da anotação malformado: {exc}") from exc
    color, fontsize = "#FFFFFF", 11
    for tok in rest[qidx + end :].split():
        up = tok.upper()
        if up.startswith("COLOR="):
            color = tok[6:]
        elif up.startswith("SIZE="):
            try:
                fontsize = int(tok[5:])
            except ValueError as exc:
                raise BulletinError(f"linha {lineno}: SIZE inválido: {tok!r}") from exc
    return AnnotationCommand(x=lon, y=lat, text=str(text), color=color, fontsize=fontsize)


@dataclass
class _PendingLine:
    """Feição de linha em construção (pode continuar nas linhas seguintes)."""

    symbol_key: str
    flip: bool
    intensity: int
    pts: list[tuple[float, float]]  # (lat, lon)
    lineno: int


def _parse_codsas(text: str) -> ParsedBulletin:
    """Decodifica um boletim CODSAS. Linhas de prosa/cabeçalho são ignoradas."""
    commands: list = []
    valid = ""
    pending: _PendingLine | None = None

    def _flush() -> None:
        nonlocal pending
        if pending is None:
            return
        if len(pending.pts) == 1:
            raise BulletinError(
                f"linha {pending.lineno}: feição {LINE_KEYWORDS[pending.symbol_key]} "
                "com um único ponto — uma linha requer ≥ 2 coordenadas."
            )
        if pending.pts:
            commands.append(
                DrawCommand(
                    symbol_key=pending.symbol_key,
                    points_x=[lon for _, lon in pending.pts],
                    points_y=[lat for lat, _ in pending.pts],
                    flip=pending.flip,
                    intensity=pending.intensity,
                )
            )
        pending = None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        toks = line.split()
        first = toks[0].upper()

        # Continuação (estilo WPC): linha que começa com coordenada estende
        # a feição de linha pendente; fora desse contexto é prosa.
        if _COORD_RE.match(toks[0]):
            if pending is not None:
                pending.pts.extend(_parse_coord(t, lineno) for t in toks if _COORD_RE.match(t))
            continue

        if first == "CODSAS":
            m = re.fullmatch(r"V(\d+)", toks[1].upper()) if len(toks) > 1 else None
            if m and int(m.group(1)) > BULLETIN_VERSION:
                raise BulletinError(
                    f"Boletim criado numa versão mais nova (CODSAS V{m.group(1)} > "
                    f"V{BULLETIN_VERSION}). Atualize o CartoMet BR para importá-lo."
                )
            continue
        if first == "VALID":
            m = _VALID_RE.search(line)
            if m:
                valid = f"{m.group(1)}T{m.group(2)}:{m.group(3)}"
            continue
        if first == _TEXT_KEYWORD:
            _flush()
            commands.append(_parse_text_line(line, lineno))
            continue
        if first in _KEYWORD_TO_POINT:
            _flush()
            coords = [_parse_coord(t, lineno) for t in toks[1:] if _COORD_RE.match(t)]
            if not coords:
                raise BulletinError(f"linha {lineno}: {first} sem coordenada 'lat,lon'.")
            commands.extend(
                PointCommand(symbol_key=_KEYWORD_TO_POINT[first], x=lon, y=lat)
                for lat, lon in coords
            )
            continue
        if first in _KEYWORD_TO_LINE:
            _flush()
            flip, intensity = False, 1
            pts: list[tuple[float, float]] = []
            for t in toks[1:]:
                if _COORD_RE.match(t):
                    pts.append(_parse_coord(t, lineno))
                elif t.upper() == "FLIP":
                    flip = True
                elif m := re.fullmatch(r"INT([123])", t.upper()):
                    intensity = int(m.group(1))
                # Demais tokens (ex.: WK/MDT/STG) são tolerados e ignorados.
            pending = _PendingLine(_KEYWORD_TO_LINE[first], flip, intensity, pts, lineno)
            continue
        # Linha de prosa/cabeçalho — ignorada.
    _flush()
    return ParsedBulletin(commands=commands, valid_time=valid, fmt="CODSAS")


def _parse_wpc(text: str) -> ParsedBulletin:
    """Decodifica um boletim WPC genuíno (CODSUS) via MetPy.

    Frentes/cavados viram ``DrawCommand``; HIGH/LOW viram ``PointCommand`` e,
    quando o boletim traz a pressão central, um rótulo ``AnnotationCommand``
    logo abaixo do símbolo (como na carta do WPC).
    """
    import io as _io

    from metpy.io import parse_wpc_surface_bulletin

    try:
        df = parse_wpc_surface_bulletin(_io.BytesIO(text.encode("utf-8")))
    except Exception as exc:  # parser externo — modos de falha variados
        raise BulletinError(f"Falha ao decodificar como boletim WPC: {exc}") from exc
    if df.empty:
        raise BulletinError(
            "Nenhuma feição reconhecida — o arquivo não parece um boletim "
            "CODSAS (CartoMet) nem um boletim codificado do WPC."
        )

    commands: list = []
    for row in df.itertuples(index=False):
        key = _WPC_FEATURE_TO_KEY.get(str(row.feature))
        if key is None:
            continue
        geom: Any = row.geometry
        if key in POINT_KEYWORDS:
            commands.append(PointCommand(symbol_key=key, x=float(geom.x), y=float(geom.y)))
            try:
                # int(nan) dispara ValueError — pressão ausente vira None.
                press: int | None = int(float(row.strength))
            except (AttributeError, TypeError, ValueError):
                press = None
            if press is not None:
                commands.append(
                    AnnotationCommand(
                        x=float(geom.x),
                        y=float(geom.y) - 1.5,
                        text=str(press),
                        color=_WPC_STRENGTH_COLORS[key],
                        fontsize=9,
                    )
                )
        elif getattr(geom, "geom_type", "") == "LineString":
            lons, lats = zip(*geom.coords, strict=True)
            commands.append(
                DrawCommand(
                    symbol_key=key,
                    points_x=[float(v) for v in lons],
                    points_y=[float(v) for v in lats],
                    flip=False,
                )
            )
        # Frente degenerada num único ponto (geom_type == "Point"): sem linha
        # a traçar — ignorada.

    valid = ""
    if len(df):
        ts = df["valid"].iloc[0]
        try:
            valid = ts.strftime("%Y-%m-%dT%H:%M")
        except (AttributeError, ValueError):
            valid = ""
    return ParsedBulletin(commands=commands, valid_time=valid, fmt="WPC")


def parse_bulletin(text: str) -> ParsedBulletin:
    """Decodifica um boletim (CODSAS ou WPC, detecção automática).

    Tenta CODSAS primeiro; um boletim WPC não rende NENHUMA feição CODSAS
    (os tokens compactados ``4731193`` não casam com ``lat,lon``), então o
    fallback para o MetPy é inequívoco. ``BulletinError`` se nada for
    reconhecido em nenhum dos dois formatos.
    """
    if not text or not text.strip():
        raise BulletinError("Arquivo de boletim vazio.")
    parsed = _parse_codsas(text)
    if parsed.commands:
        return parsed
    return _parse_wpc(text)
