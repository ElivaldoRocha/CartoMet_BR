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
    fill_color: str | None = None      # None = sem preenchimento
    linewidth: float = 2.0
    linestyle: str = "solid"           # "solid" | "dashed" | "dotted"
    alpha: float = 1.0                 # 0.1–1.0 (contorno e fill compartilham)

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
#  COMANDOS DE HISTÓRICO (padrão Command, espelham DrawCommand/PointCommand)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PenCommand:
    """Traço livre da caneta (pontos lon/lat já decimados)."""

    points_x: list[float]
    points_y: list[float]
    style: dict                                  # DrawStyle.to_dict()
    pressures: list[float] | None = None         # v1 sempre None; gancho p/ pressão
    artist: object = field(default=None, repr=False)


@dataclass
class ShapeCommand:
    """Forma inserida (rect/ellipse/arrow/line: [x0,x1]; polygon: vértices)."""

    tool: str
    points_x: list[float]
    points_y: list[float]
    style: dict
    head_size_deg: float = 0.0    # escala da ponta da seta congelada no finalize
    artist: object = field(default=None, repr=False)


# ═══════════════════════════════════════════════════════════════════════════════
#  GEOMETRIA (fonte única: preview, finalize e redo usam os MESMOS construtores)
# ═══════════════════════════════════════════════════════════════════════════════

def build_rectangle_ring(x0: float, y0: float, x1: float, y1: float):
    """Anel fechado de 5 pontos do retângulo definido pela diagonal (x0,y0)-(x1,y1)."""
    xs = [x0, x1, x1, x0, x0]
    ys = [y0, y0, y1, y1, y0]
    return xs, ys


def build_ellipse_ring(x0: float, y0: float, x1: float, y1: float, n: int = 120):
    """Anel paramétrico fechado da elipse inscrita na caixa (x0,y0)-(x1,y1).

    n pontos com fechamento (último == primeiro) — só segmentos retos, sem
    códigos curvos (doutrina poligonal).
    """
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    rx, ry = abs(x1 - x0) / 2.0, abs(y1 - y0) / 2.0
    t = np.linspace(0.0, 2.0 * np.pi, n)
    xs = (cx + rx * np.cos(t)).tolist()
    ys = (cy + ry * np.sin(t)).tolist()
    xs[-1], ys[-1] = xs[0], ys[0]                  # fechamento exato
    return xs, ys


def build_arrow_geometry(x0: float, y0: float, x1: float, y1: float,
                         head_size_deg: float):
    """Haste + triângulo da ponta da seta (3 vértices), em coords de dados.

    O GeoAxes PlateCarree tem aspecto igual em graus, então o ângulo em dados
    coincide com o ângulo na tela. Retorna (shaft_xs, shaft_ys, head_verts),
    onde head_verts é a lista fechada [(x,y), ...] do triângulo.
    """
    ang = math.atan2(y1 - y0, x1 - x0)
    half = math.radians(25.0)                      # meia-abertura da ponta
    base1 = (x1 - head_size_deg * math.cos(ang - half),
             y1 - head_size_deg * math.sin(ang - half))
    base2 = (x1 - head_size_deg * math.cos(ang + half),
             y1 - head_size_deg * math.sin(ang + half))
    # Haste termina na base da ponta (não fura o triângulo)
    bx = x1 - head_size_deg * 0.8 * math.cos(ang)
    by = y1 - head_size_deg * 0.8 * math.sin(ang)
    shaft_xs, shaft_ys = [x0, bx], [y0, by]
    head_verts = [(x1, y1), base1, base2, (x1, y1)]
    return shaft_xs, shaft_ys, head_verts


def close_polygon_ring(xs: list[float], ys: list[float]):
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


def _polygonal_fill_patch(ax, xs: list[float], ys: list[float], fill_color: str,
                          alpha: float, transform=None) -> PathPatch:
    """PathPatch de preenchimento com códigos SÓ poligonais (doutrina validada)."""
    verts = list(zip(xs, ys, strict=True))
    codes = [MplPath.MOVETO] + [MplPath.LINETO] * (len(verts) - 2) + [MplPath.CLOSEPOLY]
    kwargs = {"facecolor": fill_color, "edgecolor": "none", "alpha": alpha,
                  "zorder": SHAPE_FILL_ZORDER}
    if transform is not None:
        kwargs["transform"] = transform
    patch = PathPatch(MplPath(verts, codes), **kwargs)
    ax.add_patch(patch)
    return patch


def create_pen_artist(ax, points_x: list[float], points_y: list[float],
                      style: DrawStyle, transform=None):
    """Cria o Line2D final de um traço de caneta (cantos arredondados)."""
    kwargs = {
        "color": style.edge_color, "linewidth": style.linewidth,
        "linestyle": style.mpl_linestyle(), "alpha": style.alpha,
        "solid_capstyle": "round", "solid_joinstyle": "round", "zorder": PEN_ZORDER,
    }
    if transform is not None:
        kwargs["transform"] = transform
    line, = ax.plot(points_x, points_y, **kwargs)
    return line


def create_shape_artist(ax, tool: str, points_x: list[float], points_y: list[float],
                        style: DrawStyle, head_size_deg: float = 0.0, transform=None):
    """Cria o(s) artista(s) finais de uma forma. Fonte única p/ finalize E redo.

    rect/ellipse/line/arrow: points = [x0, x1] / [y0, y1] (diagonal ou extremos).
    polygon: points = vértices clicados (anel é fechado aqui).
    Retorna Line2D (forma simples sem fill) ou ShapeArtistGroup (composta).
    """
    line_kwargs = {
        "color": style.edge_color, "linewidth": style.linewidth,
        "linestyle": style.mpl_linestyle(), "alpha": style.alpha,
        "solid_capstyle": "round", "zorder": SHAPE_OUTLINE_ZORDER,
    }
    if transform is not None:
        line_kwargs["transform"] = transform

    if tool == "line":
        line, = ax.plot(points_x, points_y, **line_kwargs)
        return line

    if tool == "arrow":
        x0, y0, x1, y1 = points_x[0], points_y[0], points_x[-1], points_y[-1]
        shaft_xs, shaft_ys, head_verts = build_arrow_geometry(
            x0, y0, x1, y1, head_size_deg)
        shaft, = ax.plot(shaft_xs, shaft_ys, **line_kwargs)
        head = _polygonal_fill_patch(
            ax, [v[0] for v in head_verts], [v[1] for v in head_verts],
            fill_color=style.edge_color, alpha=style.alpha, transform=transform)
        head.set_zorder(SHAPE_OUTLINE_ZORDER)
        return ShapeArtistGroup([shaft, head])

    if tool == "rect":
        xs, ys = build_rectangle_ring(points_x[0], points_y[0],
                                      points_x[-1], points_y[-1])
    elif tool == "ellipse":
        xs, ys = build_ellipse_ring(points_x[0], points_y[0],
                                    points_x[-1], points_y[-1])
    elif tool == "polygon":
        xs, ys = close_polygon_ring(points_x, points_y)
    else:
        raise ValueError(f"Ferramenta de forma desconhecida: {tool!r}")

    outline, = ax.plot(xs, ys, **line_kwargs)
    if style.fill_color:
        fill = _polygonal_fill_patch(ax, xs, ys, style.fill_color,
                                     style.alpha, transform=transform)
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
