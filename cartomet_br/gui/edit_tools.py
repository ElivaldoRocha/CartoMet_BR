"""Hit-testing do modo edição (selecionar um desenho já finalizado por clique).

Módulo PURO (numpy, sem PyQt) — testável sem QApplication. O hit opera sobre a
GEOMETRIA DO COMANDO, nunca sobre o artista matplotlib: para símbolos OMM de
linha usa a MESMA interpolação spline do render (o usuário clica no que vê);
para formas, os MESMOS construtores de anel dos artistas. A conversão para
pixels de tela (``ax.transData``) fica a cargo do chamador (``MapCanvas``) —
aqui só entram coordenadas.

Raios de captura em PIXELS (independem do zoom, padrão do ímã de vértices):
linhas/contornos usam ``EDIT_PICK_RADIUS_PX``; desenhos pontuais (símbolo,
texto, emoji) usam ``ANCHOR_PICK_RADIUS_PX`` no ponto âncora — maior, para
aproximar o glifo desenhado em volta (o extent real do texto varia com o
renderer e não vale o custo de medi-lo).
"""

from __future__ import annotations

import numpy as np

from cartomet_br.charts.interactive import interpolar_pontos
from cartomet_br.gui.draw_tools import (
    AnnotationCommand,
    DrawCommand,
    EmojiCommand,
    PenCommand,
    PointCommand,
    ShapeCommand,
    build_arrow_geometry,
    build_ellipse_ring,
    build_rectangle_ring,
    close_polygon_ring,
    rotate_points,
)

# A distância é medida até a LINHA-BASE do comando, mas o desenho visível
# inclui os glifos do efeito (semicírculos da Linha Seca, triângulos de frente,
# setas do jato), que saltam PERPENDICULARES ao traçado em até ``symbol_size``
# pixels (Dryline: 11 px; Jato: 14 px, × fator de DPI). O raio precisa cobrir
# essa banda — clicar NO glifo tem que selecionar (caso real: Linha Seca
# "invisível" à edição com raio 10). Vence sempre o mais próximo, então um
# raio generoso não seleciona errado.
EDIT_PICK_RADIUS_PX: float = 16.0
ANCHOR_PICK_RADIUS_PX: float = 16.0


def is_anchor_command(cmd: object) -> bool:
    """Comandos pontuais: o hit é por raio no âncora, não por polilinha."""
    return isinstance(cmd, (PointCommand, AnnotationCommand, EmojiCommand))


def hit_geometry(cmd: object) -> tuple[list[float], list[float]]:
    """Polilinha lon/lat que representa o comando NA TELA (hit + highlight).

    - ``DrawCommand``: a spline interpolada dos vértices crus (idêntica à
      plotada) — clicar entre dois vértices, sobre a curva, acerta.
    - ``PenCommand``: os pontos crus (já densos, decimados a ~2 px).
    - ``ShapeCommand``: o anel de contorno pelos construtores dos artistas.
    - Pontuais: lista de 1 elemento (o âncora).
    """
    if isinstance(cmd, DrawCommand):
        xi, yi = interpolar_pontos(cmd.points_x, cmd.points_y)
        return [float(v) for v in xi], [float(v) for v in yi]
    if isinstance(cmd, PenCommand):
        return list(cmd.points_x), list(cmd.points_y)
    if isinstance(cmd, ShapeCommand):
        return _shape_ring(cmd)
    if isinstance(cmd, (PointCommand, AnnotationCommand, EmojiCommand)):
        return [float(cmd.x)], [float(cmd.y)]
    raise TypeError(f"comando sem geometria de hit: {type(cmd).__name__}")


def _shape_ring(cmd: ShapeCommand) -> tuple[list[float], list[float]]:
    """Contorno de uma forma, pelos MESMOS construtores usados no render.

    rect/ellipse aplicam ``rotation_deg`` ao redor do meio da diagonal — o hit
    (e o halo/fantasma, que consomem esta geometria) acompanha a forma girada.
    """
    if cmd.tool == "polygon":
        return close_polygon_ring(cmd.points_x, cmd.points_y)
    x0, x1 = cmd.points_x[0], cmd.points_x[-1]
    y0, y1 = cmd.points_y[0], cmd.points_y[-1]
    if cmd.tool in ("rect", "ellipse"):
        if cmd.tool == "rect":
            xs, ys = build_rectangle_ring(x0, y0, x1, y1)
        else:
            xs, ys = build_ellipse_ring(x0, y0, x1, y1)
        rot = getattr(cmd, "rotation_deg", 0.0)
        if rot:
            xs, ys = rotate_points(xs, ys, (x0 + x1) / 2.0, (y0 + y1) / 2.0, rot)
        return xs, ys
    if cmd.tool == "arrow":
        shaft_xs, shaft_ys, head = build_arrow_geometry(x0, y0, x1, y1, cmd.head_size_deg)
        # Haste + triângulo da ponta concatenados: o segmento de emenda
        # (base da ponta → vértice) corre sobre o eixo da seta — inofensivo.
        return (
            list(shaft_xs) + [p[0] for p in head],
            list(shaft_ys) + [p[1] for p in head],
        )
    # "line" e qualquer ferramenta futura: a polilinha dos próprios pontos.
    return list(cmd.points_x), list(cmd.points_y)


def polyline_dist2_px(px: float, py: float, pts_px: np.ndarray) -> float:
    """Menor distância² do pixel (px, py) à polilinha ``pts_px`` (N×2, pixels).

    Distância ponto→segmento vetorizada; pontos não-finitos (fora da projeção)
    são descartados. Polilinha de 1 ponto degenera para distância ao ponto.
    """
    pts = np.asarray(pts_px, dtype=float)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if pts.shape[0] == 0:
        return float("inf")
    p = np.array([px, py], dtype=float)
    if pts.shape[0] == 1:
        return float(((pts[0] - p) ** 2).sum())
    a, b = pts[:-1], pts[1:]
    ab = b - a
    denom = (ab**2).sum(axis=1)
    t = np.where(denom > 0, ((p - a) * ab).sum(axis=1) / np.maximum(denom, 1e-12), 0.0)
    proj = a + np.clip(t, 0.0, 1.0)[:, None] * ab
    d2 = ((proj - p) ** 2).sum(axis=1)
    return float(d2.min())


def pick_command(
    candidates: list[object],
    px: float,
    py: float,
    to_pixels,
) -> object | None:
    """Comando mais próximo do clique (px, py), dentro do raio de captura.

    ``candidates`` em ordem de criação (documento + emojis) — empate em
    distância fica com o MAIS RECENTE (topo visual). ``to_pixels`` converte um
    array N×2 lon/lat em pixels de tela (``ax.transData.transform``).
    """
    best: object | None = None
    best_d2 = float("inf")
    for cmd in candidates:
        xs, ys = hit_geometry(cmd)
        if not xs:
            continue
        pts = to_pixels(np.column_stack([xs, ys]))
        radius = ANCHOR_PICK_RADIUS_PX if is_anchor_command(cmd) else EDIT_PICK_RADIUS_PX
        d2 = polyline_dist2_px(px, py, pts)
        if d2 <= radius**2 and d2 <= best_d2:
            best, best_d2 = cmd, d2
    return best
