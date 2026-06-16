"""
Funções de desenho para símbolos meteorológicos pontuais.

Contém:
- draw_hurricane: Furacão OMM (círculo preenchido + braços curvos preenchidos)
- draw_tropical_storm: Tempestade Tropical (braços iguais, círculo vazado)
- draw_vortex: Vórtice ciclônico (triskele de três braços)
"""

import cartopy.crs as ccrs
import numpy as np
from matplotlib.patches import PathPatch
from matplotlib.path import Path

# ═══════════════════════════════════════════════════════════════════════════════
#  UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════


class _CompoundArtist:
    """Agrupa vários artists matplotlib em um só para undo/redo."""

    def __init__(self, artists):
        self._artists = list(artists)

    def remove(self):
        for a in self._artists:
            try:
                a.remove()
            except (ValueError, AttributeError):
                pass

    def set_visible(self, visible):
        for a in self._artists:
            a.set_visible(visible)


def _symbol_size(ax, frac: float = 0.035) -> float:
    """Calcula tamanho do símbolo baseado na extensão horizontal do mapa."""
    xlim = ax.get_xlim()
    return abs(xlim[1] - xlim[0]) * frac


def _make_circle_path(cx: float, cy: float, radius: float, n_pts: int = 24) -> Path:
    """Cria path circular fechado."""
    angles = np.linspace(0, 2 * np.pi, n_pts, endpoint=False)
    verts = np.column_stack(
        [
            cx + radius * np.cos(angles),
            cy + radius * np.sin(angles),
        ]
    )
    verts = np.vstack([verts, verts[0]])
    codes = [Path.MOVETO] + [Path.LINETO] * (n_pts - 1) + [Path.CLOSEPOLY]
    return Path(verts, codes)


def _make_arm_polygon(r: float, s: float, n: int = 20) -> np.ndarray:
    """
    Gera polígono de um braço cônico (vírgula/foice) do símbolo de furacão.

    O braço sai da borda do círculo central (raio *r*), afunila até a ponta,
    percorrendo uma curva Bézier cúbica de comprimento proporcional a *s*.
    Retorna vértices relativos à origem (0, 0).

    O braço padrão sai do topo-direita do círculo, curva para a direita
    e desce (sentido horário). Negando os pontos obtém-se o braço oposto.
    """
    t = np.linspace(0, 1, n)

    # Linha central do braço (Bézier cúbico)
    p0 = np.array([r * 0.3, r * 0.85])
    p1 = np.array([s * 0.50, s * 0.40])
    p2 = np.array([s * 0.50, -s * 0.05])
    p3 = np.array([s * 0.18, -s * 0.50])

    t3 = t[:, None]
    center = (
        (1 - t3) ** 3 * p0 + 3 * (1 - t3) ** 2 * t3 * p1 + 3 * (1 - t3) * t3**2 * p2 + t3**3 * p3
    )

    # Tangente → normal
    dt = np.gradient(center, axis=0)
    mag = np.hypot(dt[:, 0], dt[:, 1])
    mag = np.where(mag < 1e-10, 1e-10, mag)
    normals = np.column_stack([-dt[:, 1] / mag, dt[:, 0] / mag])

    # Largura afunila de largo (junto ao círculo) para estreito (na ponta)
    widths = np.linspace(r * 0.55, r * 0.05, n)

    outer = center + normals * widths[:, None]
    inner = center - normals * widths[:, None]

    return np.vstack([outer, inner[::-1]])


def _arm_to_path(arm_pts: np.ndarray, cx: float, cy: float) -> Path:
    """Converte pontos do braço em Path fechado, transladado para (cx, cy)."""
    pts = arm_pts + np.array([cx, cy])
    closed = np.vstack([pts, pts[0]])
    codes = [Path.MOVETO] + [Path.LINETO] * (len(closed) - 2) + [Path.CLOSEPOLY]
    return Path(closed, codes)


# ═══════════════════════════════════════════════════════════════════════════════
#  FURACÃO (OMM)
# ═══════════════════════════════════════════════════════════════════════════════


def draw_hurricane(ax, x: float, y: float, color: str = "#CC0000"):
    """
    Furacão — Símbolo clássico OMM.

    Círculo central **preenchido** + dois braços curvos (vírgulas/foices)
    preenchidos, formando um giro ciclônico.  Um braço sai do topo e vai
    para a direita/baixo; o outro sai da base e vai para a esquerda/cima.

    Retorna um único PathPatch (compound) para undo/redo.
    """
    s = _symbol_size(ax)
    r = s * 0.15

    circle_path = _make_circle_path(x, y, r)

    arm_raw = _make_arm_polygon(r, s)
    arm1_path = _arm_to_path(arm_raw, x, y)
    arm2_path = _arm_to_path(-arm_raw, x, y)

    compound = Path.make_compound_path(circle_path, arm1_path, arm2_path)
    patch = PathPatch(
        compound,
        facecolor=color,
        edgecolor=color,
        linewidth=0.5,
        transform=ccrs.PlateCarree(),
        zorder=25,
    )
    ax.add_patch(patch)
    return patch


# ═══════════════════════════════════════════════════════════════════════════════
#  TEMPESTADE TROPICAL
# ═══════════════════════════════════════════════════════════════════════════════


def draw_tropical_storm(ax, x: float, y: float, color: str = "#CC0000"):
    """
    Tempestade Tropical — Mesmos braços preenchidos do furacão,
    porém o círculo central é **vazado** (facecolor branco, borda colorida).

    Retorna um _CompoundArtist (braços + círculo) para undo/redo.
    """
    s = _symbol_size(ax)
    r = s * 0.15

    # Braços preenchidos (idênticos ao furacão)
    arm_raw = _make_arm_polygon(r, s)
    arm1_path = _arm_to_path(arm_raw, x, y)
    arm2_path = _arm_to_path(-arm_raw, x, y)

    arms_compound = Path.make_compound_path(arm1_path, arm2_path)
    arms_patch = PathPatch(
        arms_compound,
        facecolor=color,
        edgecolor=color,
        linewidth=0.5,
        transform=ccrs.PlateCarree(),
        zorder=25,
    )
    ax.add_patch(arms_patch)

    # Círculo vazado (branco por dentro, borda colorida) — desenhado POR CIMA
    circle_path = _make_circle_path(x, y, r)
    circle_patch = PathPatch(
        circle_path,
        facecolor="white",
        edgecolor=color,
        linewidth=1.5,
        transform=ccrs.PlateCarree(),
        zorder=26,
    )
    ax.add_patch(circle_patch)

    return _CompoundArtist([arms_patch, circle_patch])


# ═══════════════════════════════════════════════════════════════════════════════
#  VÓRTICE CICLÔNICO
# ═══════════════════════════════════════════════════════════════════════════════


def _make_vortex_blade(s: float, n: int = 20) -> np.ndarray:
    """
    Gera polígono de uma pá do vórtice (vírgula afilada).

    Semelhante a _make_arm_polygon mas partindo do centro (0,0)
    e com proporções adequadas para 3 pás.
    """
    t = np.linspace(0, 1, n)

    # Linha central da pá (Bézier cúbico)
    p0 = np.array([0.0, 0.0])
    p1 = np.array([s * 0.35, s * 0.15])
    p2 = np.array([s * 0.50, s * 0.45])
    p3 = np.array([s * 0.12, s * 0.65])

    t3 = t[:, None]
    center = (
        (1 - t3) ** 3 * p0 + 3 * (1 - t3) ** 2 * t3 * p1 + 3 * (1 - t3) * t3**2 * p2 + t3**3 * p3
    )

    # Tangente → normal
    dt = np.gradient(center, axis=0)
    mag = np.hypot(dt[:, 0], dt[:, 1])
    mag = np.where(mag < 1e-10, 1e-10, mag)
    normals = np.column_stack([-dt[:, 1] / mag, dt[:, 0] / mag])

    # Largura: grossa no centro, afunila na ponta
    widths = np.linspace(s * 0.10, s * 0.01, n)

    outer = center + normals * widths[:, None]
    inner = center - normals * widths[:, None]

    return np.vstack([outer, inner[::-1]])


def draw_vortex(ax, x: float, y: float, color: str = "#CC0000"):
    """
    Vórtice ciclônico — Hélice preenchida de três pás.

    Cada pá é um polígono cônico (fino na ponta, grosso no centro),
    rotacionado 120° entre si. Totalmente preenchido.

    Retorna um PathPatch (compound) para undo/redo.
    """
    s = _symbol_size(ax)
    blade_raw = _make_vortex_blade(s)

    blade_paths = []
    for angle in [0.0, 2 * np.pi / 3, 4 * np.pi / 3]:
        cos_a, sin_a = np.cos(angle), np.sin(angle)
        rot = np.array([[cos_a, -sin_a], [sin_a, cos_a]])
        rotated = blade_raw @ rot.T
        blade_paths.append(_arm_to_path(rotated, x, y))

    compound = Path.make_compound_path(*blade_paths)
    patch = PathPatch(
        compound,
        facecolor=color,
        edgecolor=color,
        linewidth=0.5,
        transform=ccrs.PlateCarree(),
        zorder=25,
    )
    ax.add_patch(patch)
    return patch
