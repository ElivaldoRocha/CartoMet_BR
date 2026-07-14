"""Ferramentas de desenho livre (caneta) e formas customizáveis do CartoMet BR.

Módulo PURO (matplotlib/numpy, sem PyQt) — testável sem QApplication, como os
módulos de dados. Contém o modelo de dados (estilo + comandos de undo/redo),
os construtores de geometria e as fábricas de artistas usadas pelo MapCanvas.

Doutrina dos códigos poligonais (validada por spike e pelo precedente em
produção ``symbols/point_symbols.py``): contornos são Line2D via ``ax.plot``;
preenchimentos são ``PathPatch`` com códigos EXCLUSIVAMENTE poligonais
(MOVETO/LINETO/CLOSEPOLY) e ``transform=ccrs.PlateCarree()`` explícito.
NUNCA usar patches curvos (Ellipse, FancyArrowPatch, CURVE3/CURVE4) em
GeoAxes do Cartopy — é a incompatibilidade histórica documentada.

Os comandos guardam apenas dados puros (geometria em lon/lat + dict de estilo),
prontos para serialização futura (``dataclasses.asdict``) num "salvar desenhos".
"""

from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass, field

import numpy as np
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath

# Zorders dos desenhos do usuário: acima das linhas de simbologia (20),
# abaixo dos símbolos pontuais/anotações (25). Contorno sobre o próprio fill.
PEN_ZORDER: float = 21.0
SHAPE_FILL_ZORDER: float = 21.0
SHAPE_OUTLINE_ZORDER: float = 21.5

# Decimação do traço da caneta: distância mínima (px) entre pontos consecutivos.
PEN_MIN_PIXEL_DIST: float = 2.0
# Arraste mínimo (px) para uma forma não ser descartada como clique acidental.
SHAPE_MIN_DRAG_PIXELS: float = 3.0

# Ferramentas de forma suportadas.
SHAPE_TOOLS: tuple[str, ...] = ("rect", "ellipse", "arrow", "line", "polygon")

_LINESTYLES: dict[str, str] = {"solid": "-", "dashed": "--", "dotted": ":"}


# ═══════════════════════════════════════════════════════════════════════════════
#  ESTILO
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class DrawStyle:
    """Estilo de um traço de caneta ou de uma forma (dados puros)."""

    edge_color: str = "#E74C3C"
    fill_color: str | None = None  # None = sem preenchimento
    linewidth: float = 2.0
    linestyle: str = "solid"  # "solid" | "dashed" | "dotted"
    alpha: float = 1.0  # 0.1–1.0 (contorno e fill compartilham)

    def mpl_linestyle(self) -> str:
        return _LINESTYLES.get(self.linestyle, "-")

    def to_dict(self) -> dict:
        return {
            "edge_color": self.edge_color,
            "fill_color": self.fill_color,
            "linewidth": self.linewidth,
            "linestyle": self.linestyle,
            "alpha": self.alpha,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DrawStyle:
        return cls(
            edge_color=d.get("edge_color", "#E74C3C"),
            fill_color=d.get("fill_color"),
            linewidth=float(d.get("linewidth", 2.0)),
            linestyle=d.get("linestyle", "solid"),
            alpha=float(d.get("alpha", 1.0)),
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  COMANDOS DE HISTÓRICO (padrão Command — modelo de dados PURO de todo desenho)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Todos os comandos guardam APENAS dados puros (geometria lon/lat + parâmetros),
# sem estado oculto — prontos para serialização (salvar/abrir projeto .cmbr) e
# reconstrução determinística. O campo ``artist`` é o handle matplotlib vivo
# (``repr=False``, nunca serializado). Vivem aqui (módulo puro, sem PyQt) para
# que ``project_io`` os serialize sem depender da GUI; ``map_canvas`` os reexporta.


@dataclass
class DrawCommand:
    """Símbolo OMM de linha (frentes, ZCAS, ZCIT, cavado, crista, jato…)."""

    symbol_key: str
    points_x: list[float]
    points_y: list[float]
    flip: bool
    artist: object = field(default=None, repr=False)
    intensity: int = 1  # usado por símbolos com intensidade (ex.: ZCIT)


@dataclass
class PointCommand:
    """Símbolo OMM pontual (clique único: A/B de pressão, furacão, vórtice…)."""

    symbol_key: str
    x: float
    y: float
    artist: object = field(default=None, repr=False)


@dataclass
class AnnotationCommand:
    """Anotação de texto livre."""

    x: float
    y: float
    text: str
    color: str
    fontsize: int
    artist: object = field(default=None, repr=False)


@dataclass
class EmojiCommand:
    """Emoji meteorológico posicionado em lon/lat."""

    x: float
    y: float
    emoji: str
    fontsize: int = 28
    artist: object = field(default=None, repr=False)


@dataclass
class PenCommand:
    """Traço livre da caneta (pontos lon/lat já decimados)."""

    points_x: list[float]
    points_y: list[float]
    style: dict  # DrawStyle.to_dict()
    pressures: list[float] | None = None  # v1 sempre None; gancho p/ pressão
    artist: object = field(default=None, repr=False)


@dataclass
class ShapeCommand:
    """Forma inserida (rect/ellipse/arrow/line: [x0,x1]; polygon: vértices).

    ``rotation_deg`` só é usado por rect/ellipse (geometria presa à diagonal
    alinhada a eixos): o anel é girado ao redor do ponto médio da diagonal no
    render/hit. line/arrow/polygon têm vértices livres — a rotação é ASSADA
    direto em ``points_x/points_y`` e o campo permanece 0.
    """

    tool: str
    points_x: list[float]
    points_y: list[float]
    style: dict
    head_size_deg: float = 0.0  # escala da ponta da seta congelada no finalize
    rotation_deg: float = 0.0  # rect/ellipse: giro anti-horário ao redor do centro
    artist: object = field(default=None, repr=False)


# ═══════════════════════════════════════════════════════════════════════════════
#  OPERAÇÕES DE HISTÓRICO (modo edição — undo/redo sobre desenhos JÁ finalizados)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Com o modo edição, a pilha de undo/redo deixa de guardar os comandos em si e
# passa a guardar OPERAÇÕES sobre eles: criar, apagar, mover, editar vértice.
# O documento (conjunto vivo de desenhos) fica separado, em ``DrawingHistory``.
# As operações de geometria guardam CÓPIAS COMPLETAS (não deltas): soma de
# float não é bit-exata e quebraria as junções do ímã ao desfazer um arraste.


@dataclass
class CreateOp:
    """Criação de um desenho (o push clássico)."""

    cmd: object


@dataclass
class DeleteOp:
    """Remoção de um desenho específico (modo edição)."""

    cmd: object
    index: int  # posição no documento, p/ reinserção na ordem original no undo


@dataclass
class MoveOp:
    """Translação de um desenho inteiro (modo edição)."""

    cmd: object
    old_x: list[float]
    old_y: list[float]
    new_x: list[float]
    new_y: list[float]


@dataclass
class EditVertexOp:
    """Reposicionamento de UM vértice de um desenho (modo edição)."""

    cmd: object
    vertex: int
    old_xy: tuple[float, float]
    new_xy: tuple[float, float]


@dataclass
class RotateOp:
    """Rotação de uma forma rect/ellipse (campo ``rotation_deg``; modo edição).

    Formas de vértices livres (line/arrow/polygon) NÃO usam esta operação: a
    rotação delas é assada nos pontos e desfeita por ``MoveOp`` (cópias).
    """

    cmd: object
    old_deg: float
    new_deg: float


def get_command_geometry(cmd: object) -> tuple[list[float], list[float]]:
    """Geometria CRUA (cópia) de um comando, como par de listas lon/lat.

    Comandos de linha devolvem seus vértices; os pontuais (símbolo, texto,
    emoji), a âncora como lista de 1 elemento. Par simétrico de
    ``set_command_geometry`` — juntos são a interface única do executor de
    operações e do modo edição.
    """
    if isinstance(cmd, (DrawCommand, PenCommand, ShapeCommand)):
        return list(cmd.points_x), list(cmd.points_y)
    if isinstance(cmd, (PointCommand, AnnotationCommand, EmojiCommand)):
        return [float(cmd.x)], [float(cmd.y)]
    raise TypeError(f"comando sem geometria editável: {type(cmd).__name__}")


def set_command_geometry(cmd: object, xs: list[float], ys: list[float]) -> None:
    """Aplica uma geometria COPIADA a um comando (listas ou âncora escalar)."""
    if isinstance(cmd, (DrawCommand, PenCommand, ShapeCommand)):
        cmd.points_x = list(xs)
        cmd.points_y = list(ys)
    elif isinstance(cmd, (PointCommand, AnnotationCommand, EmojiCommand)):
        cmd.x = float(xs[0])
        cmd.y = float(ys[0])
    else:
        raise TypeError(f"comando sem geometria editável: {type(cmd).__name__}")


# ═══════════════════════════════════════════════════════════════════════════════
#  GEOMETRIA (fonte única: preview, finalize e redo usam os MESMOS construtores)
# ═══════════════════════════════════════════════════════════════════════════════


def build_rectangle_ring(
    x0: float, y0: float, x1: float, y1: float
) -> tuple[list[float], list[float]]:
    """Anel fechado de 5 pontos do retângulo definido pela diagonal (x0,y0)-(x1,y1)."""
    xs = [x0, x1, x1, x0, x0]
    ys = [y0, y0, y1, y1, y0]
    return xs, ys


def build_ellipse_ring(
    x0: float, y0: float, x1: float, y1: float, n: int = 120
) -> tuple[list[float], list[float]]:
    """Anel paramétrico fechado da elipse inscrita na caixa (x0,y0)-(x1,y1).

    n pontos com fechamento (último == primeiro) — só segmentos retos, sem
    códigos curvos (doutrina poligonal).
    """
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    rx, ry = abs(x1 - x0) / 2.0, abs(y1 - y0) / 2.0
    t = np.linspace(0.0, 2.0 * np.pi, n)
    xs = (cx + rx * np.cos(t)).tolist()
    ys = (cy + ry * np.sin(t)).tolist()
    xs[-1], ys[-1] = xs[0], ys[0]  # fechamento exato
    return xs, ys


def build_arrow_geometry(x0: float, y0: float, x1: float, y1: float, head_size_deg: float):
    """Haste + triângulo da ponta da seta (3 vértices), em coords de dados.

    O GeoAxes PlateCarree tem aspecto igual em graus, então o ângulo em dados
    coincide com o ângulo na tela. Retorna (shaft_xs, shaft_ys, head_verts),
    onde head_verts é a lista fechada [(x,y), ...] do triângulo.
    """
    ang = math.atan2(y1 - y0, x1 - x0)
    half = math.radians(25.0)  # meia-abertura da ponta
    base1 = (x1 - head_size_deg * math.cos(ang - half), y1 - head_size_deg * math.sin(ang - half))
    base2 = (x1 - head_size_deg * math.cos(ang + half), y1 - head_size_deg * math.sin(ang + half))
    # Haste termina na base da ponta (não fura o triângulo)
    bx = x1 - head_size_deg * 0.8 * math.cos(ang)
    by = y1 - head_size_deg * 0.8 * math.sin(ang)
    shaft_xs, shaft_ys = [x0, bx], [y0, by]
    head_verts = [(x1, y1), base1, base2, (x1, y1)]
    return shaft_xs, shaft_ys, head_verts


def close_polygon_ring(xs: list[float], ys: list[float]) -> tuple[list[float], list[float]]:
    """Fecha o anel do polígono repetindo o primeiro vértice no final."""
    if not xs:
        return list(xs), list(ys)
    out_x, out_y = list(xs), list(ys)
    if out_x[0] != out_x[-1] or out_y[0] != out_y[-1]:
        out_x.append(out_x[0])
        out_y.append(out_y[0])
    return out_x, out_y


def default_arrow_head_size(extent_width_deg: float, linewidth: float) -> float:
    """Escala padrão da ponta da seta, proporcional ao domínio e à espessura."""
    return float(extent_width_deg) * 0.018 * max(1.0, float(linewidth) / 2.0)


def rotate_points(
    xs: list[float], ys: list[float], cx: float, cy: float, deg: float
) -> tuple[list[float], list[float]]:
    """Gira os pontos ``deg`` graus (anti-horário) ao redor de (cx, cy).

    Rotação em COORDENADAS DE DADOS: no GeoAxes PlateCarree o aspecto é igual
    em graus (mesmo precedente de ``build_arrow_geometry``), então o ângulo em
    dados coincide com o ângulo na tela.
    """
    if not deg:
        return list(xs), list(ys)
    rad = math.radians(deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    out_x, out_y = [], []
    for x, y in zip(xs, ys, strict=True):
        dx, dy = x - cx, y - cy
        out_x.append(cx + dx * cos_r - dy * sin_r)
        out_y.append(cy + dx * sin_r + dy * cos_r)
    return out_x, out_y


def shape_rotation_center(cmd: ShapeCommand) -> tuple[float, float]:
    """Centro de rotação de uma forma: meio da diagonal/extremos; polygon = média."""
    if cmd.tool == "polygon":
        n = max(1, len(cmd.points_x))
        return sum(cmd.points_x) / n, sum(cmd.points_y) / n
    return (
        (cmd.points_x[0] + cmd.points_x[-1]) / 2.0,
        (cmd.points_y[0] + cmd.points_y[-1]) / 2.0,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  ARTISTAS
# ═══════════════════════════════════════════════════════════════════════════════


class ShapeArtistGroup:
    """Agrupa os artistas de uma forma (contorno + fill + ponta de seta).

    Mesma interface dos artistas compostos de ``point_symbols.py``: ``remove()``
    e ``set_visible()`` exception-safe — compatível com o loop ``line.remove()``
    de ``clear_all()`` e com ``undo_line()``.
    """

    def __init__(self, artists: list):
        self._artists = [a for a in artists if a is not None]

    def remove(self) -> None:
        for art in self._artists:
            with contextlib.suppress(ValueError, AttributeError):
                art.remove()
        self._artists = []

    def set_visible(self, visible: bool) -> None:
        for art in self._artists:
            with contextlib.suppress(AttributeError):
                art.set_visible(visible)


def _polygonal_fill_patch(
    ax, xs: list[float], ys: list[float], fill_color: str, alpha: float, transform=None
) -> PathPatch:
    """PathPatch de preenchimento com códigos SÓ poligonais (doutrina validada)."""
    verts = list(zip(xs, ys, strict=True))
    codes = [MplPath.MOVETO] + [MplPath.LINETO] * (len(verts) - 2) + [MplPath.CLOSEPOLY]
    kwargs = {
        "facecolor": fill_color,
        "edgecolor": "none",
        "alpha": alpha,
        "zorder": SHAPE_FILL_ZORDER,
    }
    if transform is not None:
        kwargs["transform"] = transform
    patch = PathPatch(MplPath(verts, codes), **kwargs)
    ax.add_patch(patch)
    return patch


def create_pen_artist(
    ax, points_x: list[float], points_y: list[float], style: DrawStyle, transform=None
):
    """Cria o Line2D final de um traço de caneta (cantos arredondados)."""
    kwargs = {
        "color": style.edge_color,
        "linewidth": style.linewidth,
        "linestyle": style.mpl_linestyle(),
        "alpha": style.alpha,
        "solid_capstyle": "round",
        "solid_joinstyle": "round",
        "zorder": PEN_ZORDER,
    }
    if transform is not None:
        kwargs["transform"] = transform
    (line,) = ax.plot(points_x, points_y, **kwargs)
    return line


def create_shape_artist(
    ax,
    tool: str,
    points_x: list[float],
    points_y: list[float],
    style: DrawStyle,
    head_size_deg: float = 0.0,
    rotation_deg: float = 0.0,
    transform=None,
):
    """Cria o(s) artista(s) finais de uma forma. Fonte única p/ finalize E redo.

    rect/ellipse/line/arrow: points = [x0, x1] / [y0, y1] (diagonal ou extremos).
    polygon: points = vértices clicados (anel é fechado aqui).
    ``rotation_deg`` gira o anel de rect/ellipse ao redor do meio da diagonal
    (nos demais a rotação já vem assada nos pontos e o parâmetro é ignorado).
    Retorna Line2D (forma simples sem fill) ou ShapeArtistGroup (composta).
    """
    line_kwargs = {
        "color": style.edge_color,
        "linewidth": style.linewidth,
        "linestyle": style.mpl_linestyle(),
        "alpha": style.alpha,
        "solid_capstyle": "round",
        "zorder": SHAPE_OUTLINE_ZORDER,
    }
    if transform is not None:
        line_kwargs["transform"] = transform

    if tool == "line":
        (line,) = ax.plot(points_x, points_y, **line_kwargs)
        return line

    if tool == "arrow":
        x0, y0, x1, y1 = points_x[0], points_y[0], points_x[-1], points_y[-1]
        shaft_xs, shaft_ys, head_verts = build_arrow_geometry(x0, y0, x1, y1, head_size_deg)
        (shaft,) = ax.plot(shaft_xs, shaft_ys, **line_kwargs)
        head = _polygonal_fill_patch(
            ax,
            [v[0] for v in head_verts],
            [v[1] for v in head_verts],
            fill_color=style.edge_color,
            alpha=style.alpha,
            transform=transform,
        )
        head.set_zorder(SHAPE_OUTLINE_ZORDER)
        return ShapeArtistGroup([shaft, head])

    if tool == "rect":
        xs, ys = build_rectangle_ring(points_x[0], points_y[0], points_x[-1], points_y[-1])
    elif tool == "ellipse":
        xs, ys = build_ellipse_ring(points_x[0], points_y[0], points_x[-1], points_y[-1])
    elif tool == "polygon":
        xs, ys = close_polygon_ring(points_x, points_y)
    else:
        raise ValueError(f"Ferramenta de forma desconhecida: {tool!r}")

    # Rotação (só rect/ellipse chegam aqui com rotation_deg ≠ 0): o contorno e
    # o fill compartilham o MESMO anel girado ao redor do meio da diagonal.
    if rotation_deg and tool in ("rect", "ellipse"):
        cx = (points_x[0] + points_x[-1]) / 2.0
        cy = (points_y[0] + points_y[-1]) / 2.0
        xs, ys = rotate_points(xs, ys, cx, cy, rotation_deg)

    (outline,) = ax.plot(xs, ys, **line_kwargs)
    if style.fill_color:
        fill = _polygonal_fill_patch(ax, xs, ys, style.fill_color, style.alpha, transform=transform)
        return ShapeArtistGroup([outline, fill])
    return outline


def build_preview_ring(tool: str, x0: float, y0: float, x1: float, y1: float):
    """Geometria do rubber-band durante o arraste (contorno apenas, sem fill).

    Para a seta, o preview é só a linha diagonal — a ponta entra no finalize.
    """
    if tool == "rect":
        return build_rectangle_ring(x0, y0, x1, y1)
    if tool == "ellipse":
        return build_ellipse_ring(x0, y0, x1, y1)
    # line / arrow: segmento simples
    return [x0, x1], [y0, y1]
