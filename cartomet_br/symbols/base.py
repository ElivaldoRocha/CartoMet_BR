"""
Classes base e helpers geométricos para simbologias meteorológicas.

Este módulo contém:
- _FrenteBase: Classe abstrata base para todas as frentes
- Funções para criar formas geométricas (triângulos, semicírculos, etc.)
- Funções para desenhar formas preenchidas e abertas
"""

import matplotlib.patheffects as pe
import numpy as np
from matplotlib.colors import to_rgba
from matplotlib.path import Path
from matplotlib.transforms import IdentityTransform

# ═══════════════════════════════════════════════════════════════════════════════
#  CLASSE BASE PARA FRENTES
# ═══════════════════════════════════════════════════════════════════════════════

class _FrenteBase(pe.AbstractPathEffect):
    """
    Classe-base para path effects meteorológicos.

    O parâmetro `flip` (bool) inverte a direção normal do path:
      flip=False → símbolos à ESQUERDA do sentido de desenho
      flip=True  → símbolos à DIREITA do sentido de desenho

    Isso é equivalente a multiplicar o vetor normal por -1,
    sem alterar os pontos ou a interpolação spline.
    """

    def __init__(
        self,
        color: str = "blue",
        linewidth: float = 2.5,
        symbol_size: float = 10,
        spacing: float = 30,
        flip: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.color = color
        self.linewidth = linewidth
        self.symbol_size = symbol_size
        self.spacing = spacing
        self.flip = flip

    @staticmethod
    def _path_geometry(verts: np.ndarray):
        """
        Calcula geometria do path: tangentes, normais, distâncias.
        
        Retorna
        -------
        tx, ty : np.ndarray
            Componentes x e y do vetor tangente unitário
        nx, ny : np.ndarray
            Componentes x e y do vetor normal unitário
        cum_dist : np.ndarray
            Distância acumulada ao longo do path
        total : float
            Comprimento total do path
        """
        diffs = np.diff(verts, axis=0)
        diffs = np.vstack([diffs, diffs[-1]])
        norms = np.hypot(diffs[:, 0], diffs[:, 1])
        norms = np.where(norms < 1e-10, 1e-10, norms)
        tx = diffs[:, 0] / norms
        ty = diffs[:, 1] / norms
        nx, ny = -ty, tx
        seg_lens = np.hypot(np.diff(verts[:, 0]), np.diff(verts[:, 1]))
        cum_dist = np.concatenate([[0], np.cumsum(seg_lens)])
        total = cum_dist[-1]
        return tx, ty, nx, ny, cum_dist, total

    def _flip_sign(self) -> int:
        """Retorna -1 se flip está ativo, +1 caso contrário."""
        return -1 if self.flip else 1

    @staticmethod
    def _interp_at(verts: np.ndarray, cum_dist: np.ndarray, pos: float):
        """
        Interpola posição ao longo do path.
        
        Retorna
        -------
        pt : np.ndarray
            Ponto interpolado [x, y]
        i : int
            Índice do segmento
        """
        i = int(np.clip(
            np.searchsorted(cum_dist, pos, side="right") - 1,
            0, len(verts) - 2,
        ))
        seg = cum_dist[i + 1] - cum_dist[i]
        frac = (pos - cum_dist[i]) / max(seg, 1e-10)
        pt = verts[i] + frac * (verts[i + 1] - verts[i])
        return pt, i

    @staticmethod
    def _rotate(xy: np.ndarray, angle: float) -> np.ndarray:
        """Rotaciona pontos xy pelo ângulo dado (em radianos)."""
        c, s = np.cos(angle), np.sin(angle)
        rot = np.array([[c, -s], [s, c]])
        return xy @ rot.T

    def _draw_line(self, renderer, gc, verts, identity):
        """Desenha a linha base do path."""
        renderer.draw_path(gc, Path(verts), identity)

    def _draw_symbol(self, renderer, gc, pt, angle, idx, identity):
        """Método a ser sobrescrito pelas subclasses."""
        raise NotImplementedError("Subclasses devem implementar _draw_symbol()")

    def draw_path(self, renderer, gc, tpath, affine, rgbFace=None):
        """Método principal de renderização."""
        path_px = affine.transform_path(tpath)
        verts = path_px.vertices
        if len(verts) < 2:
            return

        # Escala símbolo e espaçamento pelo DPI do renderer.
        # Na tela (100 DPI) o fator é 1.0; em savefig(dpi=200) é 2.0.
        # Isso garante que triângulos/semicírculos fiquem proporcionais
        # independente do DPI de exportação.
        dpi_k = renderer.dpi / 100.0
        _orig_size = self.symbol_size
        _orig_spacing = self.spacing
        self.symbol_size = self.symbol_size * dpi_k
        self.spacing = self.spacing * dpi_k

        try:
            identity = IdentityTransform()

            gc0 = renderer.new_gc()
            gc0.copy_properties(gc)
            gc0.set_foreground(self.color)
            gc0.set_linewidth(self.linewidth)
            self._draw_line(renderer, gc0, verts, identity)

            tx, ty, nx, ny, cum_dist, total = self._path_geometry(verts)

            # FLIP: inverte a normal se necessário
            f = self._flip_sign()
            nx, ny = nx * f, ny * f

            idx_sym = 0
            for pos in np.arange(self.spacing / 2, total, self.spacing):
                pt, i = self._interp_at(verts, cum_dist, pos)
                # Ângulo da tangente determina orientação
                angle = np.arctan2(ty[i], tx[i])
                # Ajusta ângulo se flip está ativo
                angle_sym = angle + (np.pi if self.flip else 0)
                self._draw_symbol(renderer, gc0, pt, angle_sym, idx_sym, identity)
                idx_sym += 1

            gc0.restore()
        finally:
            self.symbol_size = _orig_size
            self.spacing = _orig_spacing


# ═══════════════════════════════════════════════════════════════════════════════
#  FUNÇÕES PARA CRIAR FORMAS GEOMÉTRICAS
# ═══════════════════════════════════════════════════════════════════════════════

def make_triangle(size: float, direction: int = 1) -> tuple[np.ndarray, list]:
    """
    Cria um triângulo equilátero.
    
    Parâmetros
    ----------
    size : float
        Tamanho do triângulo
    direction : int
        1 = aponta para cima, -1 = aponta para baixo
        
    Retorna
    -------
    verts : np.ndarray
        Vértices do triângulo
    codes : list
        Códigos do Path
    """
    h = size * 1.0 * direction
    w = size * 0.55
    verts = np.array([[-w, 0], [w, 0], [0, h], [-w, 0]])
    codes = [Path.MOVETO, Path.LINETO, Path.LINETO, Path.CLOSEPOLY]
    return verts, codes


def make_semicircle(size: float, n_pts: int = 18, direction: int = 1) -> tuple[np.ndarray, list]:
    """
    Cria um semicírculo.
    
    Parâmetros
    ----------
    size : float
        Diâmetro do semicírculo
    n_pts : int
        Número de pontos para aproximação
    direction : int
        1 = abre para cima, -1 = abre para baixo
        
    Retorna
    -------
    verts : np.ndarray
        Vértices do semicírculo
    codes : list
        Códigos do Path
    """
    angles = np.linspace(0, np.pi, n_pts)
    r = size * 0.5
    x = np.cos(angles) * r
    y = np.sin(angles) * r * direction
    verts = np.column_stack([x, y])
    verts = np.vstack([verts, verts[0]])
    codes = [Path.MOVETO] + [Path.LINETO] * (n_pts - 1) + [Path.CLOSEPOLY]
    return verts, codes


def make_circle(radius: float, n_pts: int = 20) -> tuple[np.ndarray, list]:
    """
    Cria um círculo completo.
    
    Parâmetros
    ----------
    radius : float
        Raio do círculo
    n_pts : int
        Número de pontos para aproximação
        
    Retorna
    -------
    verts : np.ndarray
        Vértices do círculo
    codes : list
        Códigos do Path
    """
    angles = np.linspace(0, 2 * np.pi, n_pts, endpoint=False)
    x = np.cos(angles) * radius
    y = np.sin(angles) * radius
    verts = np.column_stack([x, y])
    verts = np.vstack([verts, verts[0]])
    codes = [Path.MOVETO] + [Path.LINETO] * (n_pts - 1) + [Path.CLOSEPOLY]
    return verts, codes


def make_arrowhead(size: float) -> tuple[np.ndarray, list]:
    """
    Cria uma seta (ponta de flecha) apontando para a DIREITA em coords locais.

    Quando rotacionada pelo ângulo da tangente, aponta na direção do path.

    Parâmetros
    ----------
    size : float
        Comprimento da seta

    Retorna
    -------
    verts : np.ndarray
        Vértices da seta
    codes : list
        Códigos do Path
    """
    w = size * 1.0
    h = size * 0.45
    verts = np.array([[-w, -h], [0, 0], [-w, h], [-w, -h]])
    codes = [Path.MOVETO, Path.LINETO, Path.LINETO, Path.CLOSEPOLY]
    return verts, codes


# ═══════════════════════════════════════════════════════════════════════════════
#  FUNÇÕES PARA DESENHAR FORMAS
# ═══════════════════════════════════════════════════════════════════════════════

def draw_filled(
    renderer,
    gc,
    raw_verts: np.ndarray,
    codes: list,
    pt: np.ndarray,
    angle: float,
    color: str,
    identity,
    rotate_fn,
    lw: float = 0.5,
):
    """Desenha uma forma preenchida."""
    rot = rotate_fn(raw_verts, angle) + pt
    path = Path(rot, codes)
    gc_s = renderer.new_gc()
    gc_s.copy_properties(gc)
    gc_s.set_linewidth(lw)
    gc_s.set_foreground(color)
    renderer.draw_path(gc_s, path, identity, to_rgba(color))
    gc_s.restore()


def draw_open(
    renderer,
    gc,
    raw_verts: np.ndarray,
    codes: list,
    pt: np.ndarray,
    angle: float,
    color: str,
    identity,
    rotate_fn,
    lw: float = 2.0,
):
    """Desenha uma forma aberta (sem preenchimento)."""
    rot = rotate_fn(raw_verts, angle) + pt
    path = Path(rot, codes)
    gc_s = renderer.new_gc()
    gc_s.copy_properties(gc)
    gc_s.set_linewidth(lw)
    gc_s.set_foreground(color)
    renderer.draw_path(gc_s, path, identity)
    gc_s.restore()
