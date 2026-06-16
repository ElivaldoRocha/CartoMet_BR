"""
Simbologias de frentes meteorológicas.

Contém:
- FrenteFria: Triângulos azuis
- FrenteQuente: Semicírculos vermelhos
- FrenteEstacionaria: Alternância azul/vermelho em lados opostos
- FrenteOclusa: Alternância triângulo/semicírculo no mesmo lado (roxo)
- Frontogenese: Triângulos num lado + pontos entre eles (intensificação)
- Frontolise: Triângulos num lado + barras / entre eles (enfraquecimento)
"""

import numpy as np
from matplotlib.colors import to_rgba
from matplotlib.path import Path
from matplotlib.transforms import IdentityTransform

from cartomet_br.symbols.base import (
    _FrenteBase,
    draw_filled,
    make_circle,
    make_semicircle,
    make_triangle,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  FRENTE FRIA
# ═══════════════════════════════════════════════════════════════════════════════

class FrenteFria(_FrenteBase):
    """
    Frente Fria — Triângulos preenchidos azuis perpendiculares ao path.
    
    Representa a borda dianteira de uma massa de ar frio que avança
    e empurra o ar quente para cima.
    """

    def __init__(self, **kw):
        kw.setdefault("color", "#1a6faf")
        kw.setdefault("symbol_size", 12)
        kw.setdefault("spacing", 30)
        kw.setdefault("linewidth", 2.5)
        super().__init__(**kw)

    def _draw_symbol(self, renderer, gc, pt, angle, idx, identity):
        raw, codes = make_triangle(self.symbol_size)
        draw_filled(
            renderer, gc, raw, codes, pt, angle,
            self.color, identity, self._rotate
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  FRENTE QUENTE
# ═══════════════════════════════════════════════════════════════════════════════

class FrenteQuente(_FrenteBase):
    """
    Frente Quente — Semicírculos preenchidos vermelhos perpendiculares ao path.
    
    Representa a borda dianteira de uma massa de ar quente que avança
    sobre o ar frio mais denso.
    """

    def __init__(self, **kw):
        kw.setdefault("color", "#c0392b")
        kw.setdefault("symbol_size", 12)
        kw.setdefault("spacing", 30)
        kw.setdefault("linewidth", 2.5)
        super().__init__(**kw)

    def _draw_symbol(self, renderer, gc, pt, angle, idx, identity):
        raw, codes = make_semicircle(self.symbol_size)
        draw_filled(
            renderer, gc, raw, codes, pt, angle,
            self.color, identity, self._rotate
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  FRENTE ESTACIONÁRIA
# ═══════════════════════════════════════════════════════════════════════════════

class FrenteEstacionaria(_FrenteBase):
    """
    Frente Estacionária — Padrão WMO.
    
    Segmentos alternando azul (triângulo no lado +normal)
    e vermelho (semicírculo no lado -normal).
    O flip inverte AMBOS os lados simultaneamente.
    """

    _COR_FRIA = "#1a6faf"
    _COR_QUENTE = "#c0392b"

    def __init__(self, **kw):
        kw.setdefault("color", "#6a0dad")
        kw.setdefault("symbol_size", 11)
        kw.setdefault("spacing", 30)
        kw.setdefault("linewidth", 2.5)
        super().__init__(**kw)

    def draw_path(self, renderer, gc, tpath, affine, rgbFace=None):
        path_px = affine.transform_path(tpath)
        verts = path_px.vertices
        if len(verts) < 2:
            return

        identity = IdentityTransform()
        tx, ty, nx, ny, cum_dist, total = self._path_geometry(verts)

        dpi_k = renderer.dpi / 100.0
        f = self._flip_sign()

        sym_positions = list(np.arange(self.spacing * dpi_k / 2, total, self.spacing * dpi_k))

        cut_positions = [0.0]
        for k in range(len(sym_positions)):
            if k + 1 < len(sym_positions):
                cut_positions.append(
                    (sym_positions[k] + sym_positions[k + 1]) / 2
                )
            else:
                cut_positions.append(total)

        for k, sym_pos in enumerate(sym_positions):
            is_fria = (k % 2 == 0)
            cor = self._COR_FRIA if is_fria else self._COR_QUENTE

            p_start = cut_positions[k]
            p_end = cut_positions[k + 1]

            seg_verts = []
            pt0, _ = self._interp_at(verts, cum_dist, p_start)
            seg_verts.append(pt0)
            for j in range(len(cum_dist)):
                if p_start < cum_dist[j] < p_end:
                    seg_verts.append(verts[j])
            pt1, _ = self._interp_at(verts, cum_dist, min(p_end, total))
            seg_verts.append(pt1)

            if len(seg_verts) >= 2:
                gc_l = renderer.new_gc()
                gc_l.copy_properties(gc)
                gc_l.set_foreground(cor)
                gc_l.set_linewidth(self.linewidth)
                renderer.draw_path(gc_l, Path(np.array(seg_verts)), identity)
                gc_l.restore()

            pt, i = self._interp_at(verts, cum_dist, sym_pos)
            angle = np.arctan2(ty[i], tx[i])
            s = self.symbol_size * dpi_k

            # Flip inverte qual lado cada símbolo aponta
            dir_tri = +1 * f
            dir_semi = -1 * f

            if is_fria:
                raw, codes = make_triangle(s, direction=dir_tri)
                draw_filled(
                    renderer, gc, raw, codes, pt, angle,
                    self._COR_FRIA, identity, self._rotate
                )
            else:
                raw, codes = make_semicircle(s, direction=dir_semi)
                draw_filled(
                    renderer, gc, raw, codes, pt, angle,
                    self._COR_QUENTE, identity, self._rotate
                )


# ═══════════════════════════════════════════════════════════════════════════════
#  FRENTE OCLUSA
# ═══════════════════════════════════════════════════════════════════════════════

class FrenteOclusa(_FrenteBase):
    """
    Frente Oclusa — Alternância triângulo ↔ semicírculo no MESMO lado.
    
    Cor roxa. Representa quando uma frente fria alcança e se funde
    com uma frente quente.
    """

    def __init__(self, **kw):
        kw.setdefault("color", "#8e44ad")
        kw.setdefault("symbol_size", 11)
        kw.setdefault("spacing", 30)
        kw.setdefault("linewidth", 2.5)
        super().__init__(**kw)

    def _draw_symbol(self, renderer, gc, pt, angle, idx, identity):
        s = self.symbol_size
        if idx % 2 == 0:
            raw, codes = make_triangle(s)
        else:
            raw, codes = make_semicircle(s)
        draw_filled(
            renderer, gc, raw, codes, pt, angle,
            self.color, identity, self._rotate
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  FRONTOGÊNESE
# ═══════════════════════════════════════════════════════════════════════════════

class Frontogenese(_FrenteBase):
    """
    Frontogênese — Triângulos azuis todos no mesmo lado + pontos (●)
    preenchidos entre eles, sobre a linha principal.

    Representa regiões onde o gradiente térmico está aumentando,
    indicando formação ou intensificação de uma frente.
    """

    def __init__(self, **kw):
        kw.setdefault("color", "#1a6faf")
        kw.setdefault("symbol_size", 11)
        kw.setdefault("spacing", 30)
        kw.setdefault("linewidth", 2.5)
        super().__init__(**kw)

    def draw_path(self, renderer, gc, tpath, affine, rgbFace=None):
        path_px = affine.transform_path(tpath)
        verts = path_px.vertices
        if len(verts) < 2:
            return

        identity = IdentityTransform()
        tx, ty, nx, ny, cum_dist, total = self._path_geometry(verts)

        # Linha principal
        gc0 = renderer.new_gc()
        gc0.copy_properties(gc)
        gc0.set_foreground(self.color)
        gc0.set_linewidth(self.linewidth)
        renderer.draw_path(gc0, Path(verts), identity)

        # Triângulos — todos no mesmo lado (flip controla o lado)
        dpi_k = renderer.dpi / 100.0
        s = self.symbol_size * dpi_k
        tri_positions = list(np.arange(self.spacing * dpi_k / 2, total, self.spacing * dpi_k))
        for pos in tri_positions:
            pt, i = self._interp_at(verts, cum_dist, pos)
            angle = np.arctan2(ty[i], tx[i])
            angle_sym = angle + (np.pi if self.flip else 0)
            raw, codes = make_triangle(s)
            draw_filled(
                renderer, gc0, raw, codes, pt, angle_sym,
                self.color, identity, self._rotate,
            )

        # Pontos preenchidos entre triângulos consecutivos, sobre a linha
        dot_r = 3.0 * dpi_k
        for k in range(len(tri_positions) - 1):
            mid_pos = (tri_positions[k] + tri_positions[k + 1]) / 2
            pt, _ = self._interp_at(verts, cum_dist, mid_pos)
            raw_c, codes_c = make_circle(dot_r)
            gc_d = renderer.new_gc()
            gc_d.copy_properties(gc)
            gc_d.set_linewidth(0)
            gc_d.set_foreground(self.color)
            renderer.draw_path(
                gc_d, Path(raw_c + pt, codes_c), identity,
                to_rgba(self.color),
            )
            gc_d.restore()

        gc0.restore()


# ═══════════════════════════════════════════════════════════════════════════════
#  FRONTÓLISE
# ═══════════════════════════════════════════════════════════════════════════════

class Frontolise(_FrenteBase):
    """
    Frontólise — Triângulos azuis todos no mesmo lado + barras inclinadas (/)
    entre eles, cruzando a linha principal.

    Representa regiões onde o gradiente térmico está diminuindo,
    indicando dissipação ou enfraquecimento de uma frente.
    """

    def __init__(self, **kw):
        kw.setdefault("color", "#1a6faf")
        kw.setdefault("symbol_size", 11)
        kw.setdefault("spacing", 30)
        kw.setdefault("linewidth", 2.5)
        super().__init__(**kw)

    def draw_path(self, renderer, gc, tpath, affine, rgbFace=None):
        path_px = affine.transform_path(tpath)
        verts = path_px.vertices
        if len(verts) < 2:
            return

        identity = IdentityTransform()
        tx, ty, nx, ny, cum_dist, total = self._path_geometry(verts)

        # Linha principal
        gc0 = renderer.new_gc()
        gc0.copy_properties(gc)
        gc0.set_foreground(self.color)
        gc0.set_linewidth(self.linewidth)
        renderer.draw_path(gc0, Path(verts), identity)

        # Triângulos — todos no mesmo lado (flip controla o lado)
        dpi_k = renderer.dpi / 100.0
        s = self.symbol_size * dpi_k
        tri_positions = list(np.arange(self.spacing * dpi_k / 2, total, self.spacing * dpi_k))
        for pos in tri_positions:
            pt, i = self._interp_at(verts, cum_dist, pos)
            angle = np.arctan2(ty[i], tx[i])
            angle_sym = angle + (np.pi if self.flip else 0)
            raw, codes = make_triangle(s)
            draw_filled(
                renderer, gc0, raw, codes, pt, angle_sym,
                self.color, identity, self._rotate,
            )

        # Barras inclinadas (/) entre triângulos, cruzando a linha
        slash_len = s * 0.5
        for k in range(len(tri_positions) - 1):
            mid_pos = (tri_positions[k] + tri_positions[k + 1]) / 2
            pt, i = self._interp_at(verts, cum_dist, mid_pos)
            # Ângulo da barra: tangente + 60° (inclinação tipo /)
            slash_angle = np.arctan2(ty[i], tx[i]) + np.pi / 3
            delta = slash_len * np.array([np.cos(slash_angle), np.sin(slash_angle)])
            slash_path = Path(
                [pt - delta, pt + delta],
                [Path.MOVETO, Path.LINETO],
            )
            renderer.draw_path(gc0, slash_path, identity)

        gc0.restore()
