"""
Efeitos meteorológicos diversos.

Contém:
- ZCASEffect / ZCITEffect: Zonas de convergência (trilhos paralelos)
- CavadoEffect: Cavado barométrico (traço-ponto)
- Crista: Eixo de crista (zigzag)
- LinhaInstabilidade: Linha de instabilidade (traço-ponto com círculos)
- LinhaSeca: Dryline (semicírculos abertos alternados)
- CorrenteDeJato: Corrente de jato (linha espessa com setas)
"""

import numpy as np
from matplotlib.path import Path
from matplotlib.transforms import IdentityTransform
from matplotlib.colors import to_rgba

from cartomet_br.symbols.base import (
    _FrenteBase,
    make_semicircle,
    make_circle,
    make_arrowhead,
    draw_filled,
    draw_open,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  ZONAS DE CONVERGÊNCIA (ZCAS / ZCIT)
# ═══════════════════════════════════════════════════════════════════════════════

class _TrilhoBase(_FrenteBase):
    """
    Base para ZCAS e ZCIT — Dois trilhos paralelos com travessões (dormentes).
    
    Representa zonas de convergência de umidade.
    """

    def __init__(self, color: str = "green", **kw):
        kw.setdefault("linewidth", 2.0)
        kw.setdefault("symbol_size", 12)
        kw.setdefault("spacing", 18)
        super().__init__(color=color, **kw)

    def draw_path(self, renderer, gc, tpath, affine, rgbFace=None):
        path_px = affine.transform_path(tpath)
        verts = path_px.vertices
        if len(verts) < 2:
            return

        identity = IdentityTransform()
        tx, ty, nx, ny, cum_dist, total = self._path_geometry(verts)

        dpi_k = renderer.dpi / 100.0
        hw = self.symbol_size * dpi_k
        sp = self.spacing * dpi_k
        offset = np.column_stack([nx, ny]) * hw

        gc0 = renderer.new_gc()
        gc0.copy_properties(gc)
        gc0.set_foreground(self.color)
        gc0.set_linewidth(self.linewidth)

        # Desenha os dois trilhos paralelos
        renderer.draw_path(gc0, Path(verts + offset), identity)
        renderer.draw_path(gc0, Path(verts - offset), identity)

        # Desenha os travessões (dormentes)
        for pos in np.arange(sp, total, sp):
            pt, i = self._interp_at(verts, cum_dist, pos)
            n = np.array([nx[i], ny[i]])
            renderer.draw_path(
                gc0,
                Path([pt + hw * n, pt - hw * n], [Path.MOVETO, Path.LINETO]),
                identity,
            )
        gc0.restore()


class ZCASEffect(_TrilhoBase):
    """
    Zona de Convergência do Atlântico Sul — Verde.
    
    Banda de nebulosidade e chuvas que se estende do sul da Amazônia
    até o Atlântico Sul.
    """

    def __init__(self, **kw):
        super().__init__(color="#008000", **kw)


class ZCITEffect(_FrenteBase):
    """
    Zona de Convergência Intertropical — Laranja.

    Dois trilhos retos e paralelos com grupos de traços OBLÍQUOS (/) entre eles.
    O número de traços por grupo indica a intensidade:
      • 1 traço  (/)   → Fraca
      • 2 traços (//)  → Moderada
      • 3 traços (///) → Forte

    Os traços são inclinados ~59° (nunca perpendiculares), no sentido "/",
    e todos os elementos são laranja.
    """

    INTENSITY_LABELS = {1: "Fraca", 2: "Moderada", 3: "Forte"}

    def __init__(self, intensity: int = 1, color: str = "darkorange", **kw):
        kw.setdefault("linewidth", 2.0)
        kw.setdefault("symbol_size", 11)   # meia-largura entre os trilhos
        kw.setdefault("spacing", 36)       # distância entre grupos de traços
        self.intensity = max(1, min(3, int(intensity)))
        super().__init__(color=color, **kw)

    def draw_path(self, renderer, gc, tpath, affine, rgbFace=None):
        path_px = affine.transform_path(tpath)
        verts = path_px.vertices
        if len(verts) < 2:
            return

        identity = IdentityTransform()
        tx, ty, nx, ny, cum_dist, total = self._path_geometry(verts)

        dpi_k = renderer.dpi / 100.0
        hw = self.symbol_size * dpi_k
        sp = self.spacing * dpi_k
        offset = np.column_stack([nx, ny]) * hw

        gc0 = renderer.new_gc()
        gc0.copy_properties(gc)
        gc0.set_foreground(self.color)
        gc0.set_linewidth(self.linewidth)

        # Dois trilhos paralelos (o "contorno" do símbolo)
        renderer.draw_path(gc0, Path(verts + offset), identity)
        renderer.draw_path(gc0, Path(verts - offset), identity)

        # Grupos de traços oblíquos entre os trilhos (1/2/3 = intensidade)
        slant = hw * 0.6           # inclinação (~59° em relação aos trilhos)
        stroke_gap = 5.0 * dpi_k   # separação entre traços de um mesmo grupo
        n = self.intensity
        for pos in np.arange(sp, total, sp):
            pt, i = self._interp_at(verts, cum_dist, pos)
            t = np.array([tx[i], ty[i]])
            nvec = np.array([nx[i], ny[i]])
            for k in range(n):
                shift = (k - (n - 1) / 2.0) * stroke_gap
                c = pt + shift * t
                # Traço oblíquo "/": vai de um trilho ao outro, inclinado p/ frente
                a = c - hw * nvec - slant * t
                b = c + hw * nvec + slant * t
                renderer.draw_path(
                    gc0, Path([a, b], [Path.MOVETO, Path.LINETO]), identity,
                )
        gc0.restore()


# ═══════════════════════════════════════════════════════════════════════════════
#  CAVADO
# ═══════════════════════════════════════════════════════════════════════════════

class CavadoEffect(_FrenteBase):
    """
    Cavado barométrico — Traço-PONTO-traço (dashdot).
    
    Representa uma região alongada de baixa pressão relativa.
    """

    def __init__(self, color: str = "saddlebrown", **kw):
        kw.setdefault("linewidth", 2.8)
        super().__init__(color=color, **kw)

    def draw_path(self, renderer, gc, tpath, affine, rgbFace=None):
        path_px = affine.transform_path(tpath)
        verts = path_px.vertices
        if len(verts) < 2:
            return
        identity = IdentityTransform()
        dpi_k = renderer.dpi / 100.0
        gc0 = renderer.new_gc()
        gc0.copy_properties(gc)
        gc0.set_foreground(self.color)
        gc0.set_linewidth(self.linewidth)
        gc0.set_dashes(0, [d * dpi_k for d in [12, 6, 3, 6]])  # traço-ponto
        renderer.draw_path(gc0, Path(verts), identity)
        gc0.restore()


# ═══════════════════════════════════════════════════════════════════════════════
#  CRISTA (RIDGE AXIS)
# ═══════════════════════════════════════════════════════════════════════════════

class Crista(_FrenteBase):
    """
    Eixo de crista — Zigzag largo.
    
    Representa uma região alongada de alta pressão relativa.
    """

    def __init__(self, color: str = "#005500", **kw):
        kw.setdefault("symbol_size", 10)
        kw.setdefault("spacing", 28)
        kw.setdefault("linewidth", 2.2)
        super().__init__(color=color, **kw)

    def draw_path(self, renderer, gc, tpath, affine, rgbFace=None):
        path_px = affine.transform_path(tpath)
        verts = path_px.vertices
        if len(verts) < 2:
            return

        identity = IdentityTransform()
        tx, ty, nx, ny, cum_dist, total = self._path_geometry(verts)

        dpi_k = renderer.dpi / 100.0
        s = self.symbol_size * dpi_k
        sp = self.spacing * dpi_k
        pts = [verts[0].copy()]
        side = 1

        for pos in np.arange(sp / 2, total - sp / 4, sp / 2):
            pt, i = self._interp_at(verts, cum_dist, pos)
            n = np.array([nx[i], ny[i]])
            pts.append(pt + side * s * n)
            side *= -1

        pts.append(verts[-1].copy())

        gc0 = renderer.new_gc()
        gc0.copy_properties(gc)
        gc0.set_foreground(self.color)
        gc0.set_linewidth(self.linewidth)
        renderer.draw_path(gc0, Path(np.array(pts)), identity)
        gc0.restore()


# ═══════════════════════════════════════════════════════════════════════════════
#  LINHA DE INSTABILIDADE
# ═══════════════════════════════════════════════════════════════════════════════

class LinhaInstabilidade(_FrenteBase):
    """
    Linha de Instabilidade — Traço-ponto com pontos grandes (●).
    
    Representa uma linha de tempestades convectivas.
    """

    def __init__(self, color: str = "#8B0000", **kw):
        kw.setdefault("linewidth", 2.8)
        kw.setdefault("symbol_size", 4)
        kw.setdefault("spacing", 30)
        super().__init__(color=color, **kw)

    def draw_path(self, renderer, gc, tpath, affine, rgbFace=None):
        path_px = affine.transform_path(tpath)
        verts = path_px.vertices
        if len(verts) < 2:
            return

        identity = IdentityTransform()
        tx, ty, nx, ny, cum_dist, total = self._path_geometry(verts)

        dpi_k = renderer.dpi / 100.0
        gc0 = renderer.new_gc()
        gc0.copy_properties(gc)
        gc0.set_foreground(self.color)
        gc0.set_linewidth(self.linewidth)
        dash_len = self.spacing * 0.55 * dpi_k
        gap_len = self.spacing * 0.45 * dpi_k
        gc0.set_dashes(0, [dash_len, gap_len])
        renderer.draw_path(gc0, Path(verts), identity)

        # Desenha círculos preenchidos nos gaps
        r = self.symbol_size * dpi_k
        for pos in np.arange(dash_len + gap_len / 2, total, self.spacing * dpi_k):
            pt, i = self._interp_at(verts, cum_dist, pos)
            raw, codes = make_circle(r)
            circ = raw + pt
            path = Path(circ, codes)
            gc_c = renderer.new_gc()
            gc_c.copy_properties(gc)
            gc_c.set_linewidth(0)
            gc_c.set_foreground(self.color)
            renderer.draw_path(gc_c, path, identity, to_rgba(self.color))
            gc_c.restore()

        gc0.restore()


# ═══════════════════════════════════════════════════════════════════════════════
#  LINHA SECA (DRYLINE)
# ═══════════════════════════════════════════════════════════════════════════════

class LinhaSeca(_FrenteBase):
    """
    Linha Seca (Dryline) — Semicírculos abertos alternando dos dois lados.
    
    Representa o limite entre massas de ar seco e úmido.
    """

    def __init__(self, color: str = "#b5651d", **kw):
        kw.setdefault("symbol_size", 11)
        kw.setdefault("spacing", 26)
        kw.setdefault("linewidth", 2.2)
        super().__init__(color=color, **kw)

    def _draw_symbol(self, renderer, gc, pt, angle, idx, identity):
        s = self.symbol_size
        direction = 1 if idx % 2 == 0 else -1
        raw, codes = make_semicircle(s, direction=direction)
        codes_open = codes[:-1]
        raw_open = raw[:-1]
        draw_open(
            renderer, gc, raw_open, codes_open, pt, angle,
            self.color, identity, self._rotate, lw=2.2
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  CORRENTE DE JATO (JET STREAM)
# ═══════════════════════════════════════════════════════════════════════════════

class CorrenteDeJato(_FrenteBase):
    """
    Corrente de Jato — Linha espessa com setas na direção do fluxo.

    Representa as correntes de jato em altitude (tipicamente 200-300 hPa).
    """

    def __init__(self, **kw):
        kw.setdefault("color", "#0033AA")
        kw.setdefault("linewidth", 4.0)
        kw.setdefault("symbol_size", 14)
        kw.setdefault("spacing", 50)
        super().__init__(**kw)

    def draw_path(self, renderer, gc, tpath, affine, rgbFace=None):
        path_px = affine.transform_path(tpath)
        verts = path_px.vertices
        if len(verts) < 2:
            return

        identity = IdentityTransform()
        tx, ty, nx, ny, cum_dist, total = self._path_geometry(verts)

        dpi_k = renderer.dpi / 100.0
        gc0 = renderer.new_gc()
        gc0.copy_properties(gc)
        gc0.set_foreground(self.color)
        gc0.set_linewidth(self.linewidth)
        renderer.draw_path(gc0, Path(verts), identity)

        # Setas preenchidas ao longo do path, apontando na direção do fluxo
        s = self.symbol_size * dpi_k
        raw, codes = make_arrowhead(s)
        for pos in np.arange(self.spacing * dpi_k / 2, total, self.spacing * dpi_k):
            pt, i = self._interp_at(verts, cum_dist, pos)
            angle = np.arctan2(ty[i], tx[i])
            draw_filled(
                renderer, gc0, raw, codes, pt, angle,
                self.color, identity, self._rotate,
            )

        gc0.restore()
