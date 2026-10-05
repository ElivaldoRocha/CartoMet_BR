"""Vista de Globo em tela cheia — a Terra vista do espaço com os campos ativos.

Janela independente (snapshot da sessão): projeção ``ccrs.Orthographic`` sobre
fundo estrelado, com a superfície substituída pelos campos ativos da carta
(``services/globe_compose.py`` — mesma escala de cores, sem rede), pela imagem
GOES full disk (se carregada) ou pelo relevo natural.

Três camadas de render, um worker (lições de campo em três atos):
- RASCUNHO (arraste, síncrono, ~0,3 s): base do tema + costa + grade.
- MOVIMENTO (apresentação, worker em pipeline, ~0,7 s/frame): textura de
  movimento (campos + isolinhas + rótulos assados — suaves, imperceptível em
  giro) — o giro mostra os CAMPOS, não um globo preto.
- REPOUSO (worker, ~2,5–4 s): frame vetorial pleno (nitidez da carta), com
  temas, fronteiras/estados, declutter de rótulos e vento.
A GUI nunca bloqueia: frames chegam prontos por sinal e um gesto novo
descarta o frame obsoleto. Preferências persistem em QSettings (globe/*).
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, Literal, cast

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.patches import Circle
from PyQt6.QtCore import QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cartomet_br.gui.themes import MAP_THEMES
from cartomet_br.services.globe_compose import (
    GlobeScene,
    bake_motion_texture,
    draw_contour_overlay,
    draw_synoptic_overlay,
)

logger = logging.getLogger(__name__)

# Raio do disco Orthographic em metros de projeção (≈ raio da Terra).
_ORTHO_HALF = 6.4e6
# Qualidade → regrid_shape do imshow reprojetado (textura de campos/GOES).
_QUALITY_REGRID = {"Rascunho": 400, "Equilibrado": 600, "Alta": 900}
# Regrid da textura de MOVIMENTO (frames do giro — velocidade > nitidez).
_MOTION_REGRID = 350
# Retângulo do eixo do globo na figura — compartilhado pelo rascunho, pelo
# worker e pela inversão pixel→lon/lat do duplo-clique.
_AX_RECT = (0.06, 0.05, 0.88, 0.90)
# Rótulos "no miolo": raio máximo (fração do disco) — mata a pilha de rótulos
# esticados no limbo/polos (reclamação de qualidade do teste de campo).
_LABEL_CORE_RADIUS = 0.75

_PELE_CAMPOS = "Campos ativos"
_PELE_GOES = "Satélite GOES"
_PELE_RELEVO = "Relevo natural"

_TEMA_RELEVO = "Relevo Natural"

# Workers vivos: a referência Python precisa sobreviver até o finished (QThread
# destruída com C++ rodando = crash); o slot descarta resultados obsoletos.
_LIVE_WORKERS: set[QThread] = set()


@dataclass(frozen=True)
class GlobeOptions:
    """Preferências do globo — personalização do usuário, persistida (globe/*)."""

    theme_name: str = _TEMA_RELEVO  # "Relevo Natural" ou chave de MAP_THEMES
    show_coast: bool = True
    show_borders: bool = True
    show_states: bool = False
    show_grid: bool = True
    show_stars: bool = True
    label_mode: str = "miolo"  # "todos" | "miolo" | "nenhum"
    drag_light: bool = False  # arraste sem base de tema (wireframe)
    spin_step: float = 2.0  # graus por frame da apresentação
    spin_quality: str = "fluida"  # "fluida" (textura) | "completa" (vetorial)
    streams_enabled: bool = True  # linhas de corrente no frame nítido
    quality: str = "Equilibrado"  # Rascunho | Equilibrado | Alta

    _BOOLS = (
        "show_coast",
        "show_borders",
        "show_states",
        "show_grid",
        "show_stars",
        "drag_light",
        "streams_enabled",
    )

    @classmethod
    def load(cls, settings: QSettings, default_theme: str) -> GlobeOptions:
        base = cls(theme_name=default_theme if default_theme in MAP_THEMES else _TEMA_RELEVO)
        kw: dict[str, Any] = {}
        for nome, padrao in asdict(base).items():
            bruto = settings.value(f"globe/{nome}")
            if bruto is None:
                kw[nome] = padrao
            elif nome in cls._BOOLS:
                kw[nome] = str(bruto).lower() in ("true", "1")
            elif isinstance(padrao, float):
                try:
                    kw[nome] = float(bruto)
                except (TypeError, ValueError):
                    kw[nome] = padrao
            else:
                kw[nome] = str(bruto)
        if kw.get("theme_name") not in (_TEMA_RELEVO, *MAP_THEMES):
            kw["theme_name"] = base.theme_name
        if kw.get("label_mode") not in ("todos", "miolo", "nenhum"):
            kw["label_mode"] = "miolo"
        if kw.get("spin_quality") not in ("fluida", "completa"):
            kw["spin_quality"] = "fluida"
        if kw.get("quality") not in _QUALITY_REGRID:
            kw["quality"] = "Equilibrado"
        return cls(**kw)

    def save(self, settings: QSettings) -> None:
        for nome, valor in asdict(self).items():
            settings.setValue(f"globe/{nome}", valor)


def _visible_from(lon: float, lat: float, center: tuple[float, float]) -> bool:
    """True se (lon, lat) está no hemisfério visível do globo centrado em center.

    Símbolos pontuais do lado OCULTO não podem ser desenhados: a reprojeção
    dos seus polígonos vira lixo visual (bug pego em teste de campo).
    """
    lam1, phi1 = math.radians(center[0]), math.radians(center[1])
    lam2, phi2 = math.radians(lon), math.radians(lat)
    cosd = math.sin(phi1) * math.sin(phi2) + math.cos(phi1) * math.cos(phi2) * math.cos(lam2 - lam1)
    return cosd > 0.03


def _label_keep(x: float, y: float) -> bool:
    """Filtro "miolo": rótulo além de 75% do raio do disco é descartado."""
    return math.hypot(x, y) <= _LABEL_CORE_RADIUS * _ORTHO_HALF


def _paint_background(fig, stars, *, show_stars: bool = True) -> None:
    """Espaço: preto + estrelas fixas + halo atmosférico azulado."""
    if not show_stars:
        return
    bg = fig.add_axes((0.0, 0.0, 1.0, 1.0), zorder=-10)
    bg.set_axis_off()
    bg.set_xlim(0, 1)
    bg.set_ylim(0, 1)
    xs, ys, sizes = stars
    bg.scatter(xs, ys, s=sizes, c="white", alpha=0.75, linewidths=0)
    # Halo: anéis concêntricos com alpha decrescente ao redor do disco.
    for r, a in ((0.462, 0.20), (0.472, 0.12), (0.484, 0.06), (0.498, 0.03)):
        bg.add_patch(
            Circle((0.5, 0.5), r, facecolor="none", edgecolor="#4da3ff", linewidth=6, alpha=a)
        )


def _theme_colors(theme_name: str) -> dict:
    """Cores do tema p/ o globo (fallbacks claros p/ o Relevo Natural)."""
    tema = cast("dict[str, Any]", MAP_THEMES.get(theme_name) or {})
    return {
        "land": tema.get("land", "#e8e4d8"),
        "ocean": tema.get("ocean", "#060a14"),
        "coastline": tema.get("coastline", "#e8edf5"),
        "borders": tema.get("borders", "#c9d3e0"),
        "states": tema.get("states", "#9aa7b8"),
    }


def _paint_theme_base(ax, theme_name: str, scale: str, *, allow_stock: bool = True) -> None:
    """Base do globo: relevo natural (stock) ou terra/oceano do tema (0,15 s).

    ``allow_stock=False`` (rascunho do arraste): o stock_img custa ~0,55 s —
    caro demais p/ gesto; o Relevo Natural cai em cores neutras ali.
    """
    if theme_name == _TEMA_RELEVO and allow_stock:
        ax.stock_img()
        return
    cores = _theme_colors(theme_name)
    ax.patch.set_facecolor(cores["ocean"] if theme_name != _TEMA_RELEVO else "#1b2b3a")
    ax.add_feature(
        cfeature.LAND.with_scale(scale),
        facecolor=cores["land"] if theme_name != _TEMA_RELEVO else "#4a5a4e",
        zorder=1,
    )


def _paint_boundaries(ax, opts: GlobeOptions, scale: str) -> None:
    """Costa/países/estados/grade conforme as preferências e o tema."""
    cores = _theme_colors(opts.theme_name)
    if opts.show_coast:
        ax.add_feature(
            cfeature.COASTLINE.with_scale(scale),
            linewidth=0.45,
            edgecolor=cores["coastline"] if opts.theme_name != _TEMA_RELEVO else "#e8edf5",
            zorder=16,
        )
    if opts.show_borders:
        ax.add_feature(
            cfeature.BORDERS.with_scale(scale),
            linewidth=0.4,
            linestyle="--",
            edgecolor=cores["borders"],
            zorder=16,
        )
    if opts.show_states:
        ax.add_feature(
            cfeature.STATES.with_scale(scale),
            linewidth=0.25,
            edgecolor=cores["states"],
            zorder=15,
        )
    if opts.show_grid:
        ax.gridlines(color="#55657f", linewidth=0.25, alpha=0.7)


def _paint_goes(ax, satellite, regrid: int) -> None:
    from cartomet_br.data.ecmwf import get_ir_colormap

    geos = ccrs.Geostationary(
        central_longitude=satellite.sat_lon,
        satellite_height=satellite.sat_h,
        sweep_axis=satellite.sat_sweep,
    )
    ax.imshow(
        satellite.data,
        origin="upper",
        extent=(satellite.x.min(), satellite.x.max(), satellite.y.min(), satellite.y.max()),
        transform=geos,
        cmap=get_ir_colormap(),
        vmin=-103.0,
        vmax=84.0,
        zorder=2,
        interpolation="nearest",
        regrid_shape=regrid,
    )


def _paint_colorbars(fig, scene: GlobeScene) -> list:
    """Colorbars que dizem EXATAMENTE o que a textura mostra.

    Armadilha pega em revisão: ``BoundaryNorm(levels, cmap.N)`` distribui as
    bandas UNIFORMEMENTE pelo colormap, mas o contourf da carta (e a textura,
    via ``band_colors``) colore pelo ponto MÉDIO normalizado — para níveis
    irregulares (precipitação!) as duas regras divergem em várias categorias.
    A colorbar é montada com as cores REAIS das bandas, achatadas sobre
    branco como a textura.
    """
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import BoundaryNorm, ListedColormap

    from cartomet_br.services.globe_compose import band_colors

    def achatada(cor) -> tuple[float, float, float, float]:
        arr = np.asarray(cor, dtype=float)
        return (*(arr[:3] * 0.85 + 0.15), 1.0)  # 0.85·cor + 0.15·branco

    colorbars: list = []
    layers = scene.filled_layers[:2]  # no máximo duas — legibilidade
    for i, layer in enumerate(layers):
        levels = np.asarray(layer.style.levels, dtype=float)
        interior, under, over = band_colors(layer.style)
        lcmap = ListedColormap([achatada(c) for c in interior])
        if under is not None:
            lcmap.set_under(achatada(under))
        if over is not None:
            lcmap.set_over(achatada(over))
        norm = BoundaryNorm(levels, ncolors=lcmap.N)  # banda i → cor i, exata
        extend = cast('Literal["neither", "both", "min", "max"]', layer.style.extend)
        cax = fig.add_axes((0.14 + i * 0.40, 0.035, 0.30, 0.016))
        cbar = fig.colorbar(
            ScalarMappable(norm=norm, cmap=lcmap),
            cax=cax,
            orientation="horizontal",
            extend=extend,
        )
        nome = layer.var_info.get("nome", layer.data.variable)
        unidade = layer.data.unit
        sufixo = "" if layer.is_global else " (recorte regional)"
        cbar.set_label(f"{nome} ({unidade}){sufixo}", color="#dfe6f0", fontsize=8)
        cbar.ax.tick_params(labelsize=6.5, colors="#b9c3d4")
        cbar.outline.set_edgecolor("#55657f")
        colorbars.append(cbar)
    return colorbars


def _paint_drawings(ax, drawings: list[dict], center: tuple[float, float]) -> None:
    """Traçado do usuário (records .cmbr) sobre o globo, em todas as peles.

    Mesma fonte de construção da carta (``build_drawing_artist``); emojis
    dependem do pixmap Qt do MapCanvas e ficam fora (aviso na cena). Símbolos
    pontuais e anotações do lado OCULTO são descartados (``_visible_from``).
    """
    if not drawings:
        return
    from cartomet_br.gui.map_canvas import build_drawing_artist
    from cartomet_br.gui.project_io import record_to_command

    for rec in drawings:
        kind = rec.get("type")
        if kind == "emoji":
            continue  # aviso único já entra na cena (MainWindow)
        if kind in ("symbol_point", "annotation") and not _visible_from(
            float(rec.get("x", 0.0)), float(rec.get("y", 0.0)), center
        ):
            continue  # lado oculto do globo
        try:
            build_drawing_artist(ax, record_to_command(rec))
        except Exception as exc:  # noqa: BLE001 — um desenho ruim não derruba o globo
            logger.warning("Desenho %s falhou no globo: %s", kind, exc)


def _stamp_for_skin(scene: GlobeScene, skin: str, satellite, stamp_lines: list[str]) -> list[str]:
    """Linhas do carimbo HONESTAS para a pele em exibição.

    Com a pele GOES o que se vê é a IMAGEM DE SATÉLITE (horário próprio, não
    o step do modelo); com o relevo não há dado do dia nenhum — o carimbo dos
    campos só vale na pele de campos.
    """
    titulo = stamp_lines[:1] or ["CartoMet BR — Vista de Globo"]
    if skin == _PELE_GOES and satellite is not None:
        quando = getattr(satellite, "time_str", "")
        return [*titulo, f"Satélite GOES-East — Banda 13 — {quando}"]
    if skin == _PELE_CAMPOS:
        return stamp_lines
    return [*titulo, "pele: relevo natural (imagem de referência, não é dado do dia)"]


def _paint_stamp(fig, scene: GlobeScene, skin: str, satellite, stamp_lines: list[str]) -> None:
    """Carimbo honesto no canto superior esquerdo + avisos no rodapé."""
    y = 0.975
    for i, linha in enumerate(_stamp_for_skin(scene, skin, satellite, stamp_lines)):
        fig.text(
            0.015,
            y - i * 0.028,
            linha,
            color="#dfe6f0" if i == 0 else "#aab6c8",
            fontsize=10 if i == 0 else 8,
            ha="left",
            va="top",
        )
    if skin != _PELE_CAMPOS:
        return  # avisos de camadas só fazem sentido na pele de campos
    for j, aviso in enumerate(scene.warnings[:3]):
        fig.text(
            0.985,
            0.025 + j * 0.024,
            aviso,
            color="#f0b45a",
            fontsize=7.5,
            ha="right",
            va="bottom",
        )


def compose_globe_frame(
    fig,
    *,
    scene: GlobeScene,
    satellite: Any,
    skin: str,
    center: tuple[float, float],
    zoom: float,
    stars,
    stamp_lines: list[str],
    drawings: list[dict],
    opts: GlobeOptions,
    mode: str = "crisp",
) -> dict:
    """Compõe UM frame do globo numa Figure (GUI ou Agg offscreen).

    ``mode="crisp"``: vetorial pleno (repouso — nitidez da carta).
    ``mode="motion"``: frame do giro/apresentação — textura de movimento
    (campos + isolinhas + rótulos assados) num único imshow; sem vetores
    pesados. Fonte única do worker e dos testes; devolve ``{"ax",
    "colorbars"}`` p/ inspeção.
    """
    fig.clf()
    _paint_background(fig, stars, show_stars=opts.show_stars)
    ax = fig.add_axes(_AX_RECT, projection=ccrs.Orthographic(center[0], center[1]))
    ax.set_global()
    ax.patch.set_facecolor("#060a14")  # oceano noturno (tema pode sobrepor)
    ax.spines["geo"].set_edgecolor("#2a3b5c")
    regrid = _QUALITY_REGRID[opts.quality]
    escala = "50m" if opts.quality == "Alta" else "110m"
    pc = ccrs.PlateCarree()
    colorbars: list = []

    if skin == _PELE_CAMPOS:
        _paint_theme_base(ax, opts.theme_name, escala)
        if mode == "motion":
            textura = scene.motion_texture if scene.motion_texture is not None else scene.texture
            if textura is not None:
                ax.imshow(
                    textura,
                    extent=[-180, 180, -90, 90],
                    transform=pc,
                    origin="upper",
                    regrid_shape=_MOTION_REGRID,
                    zorder=2,
                    interpolation="bilinear",
                )
        else:
            if scene.texture is not None:
                ax.imshow(
                    scene.texture,
                    extent=[-180, 180, -90, 90],
                    transform=pc,
                    origin="upper",
                    regrid_shape=regrid,
                    zorder=2,
                    interpolation="nearest",
                )
            labels = opts.label_mode != "nenhum"
            keep = _label_keep if opts.label_mode == "miolo" else None
            if scene.synoptic is not None:
                draw_synoptic_overlay(
                    ax,
                    scene.synoptic,
                    scene.synoptic_kinds,
                    transform=pc,
                    labels=labels,
                    label_keep=keep,
                )
            draw_contour_overlay(
                ax, scene.contour_layers, transform=pc, labels=labels, label_keep=keep
            )
        colorbars = _paint_colorbars(fig, scene)
    elif skin == _PELE_GOES and satellite is not None:
        _paint_goes(ax, satellite, regrid)
    else:
        ax.stock_img()

    _paint_boundaries(ax, opts, escala)
    # O traçado do usuário aparece em TODAS as peles (como na carta, que o
    # desenha sobre campos E satélite).
    _paint_drawings(ax, drawings, center)
    _paint_stamp(fig, scene, skin, satellite, stamp_lines)

    half = _ORTHO_HALF / zoom
    ax.set_xlim(-half, half)
    ax.set_ylim(-half, half)
    return {"ax": ax, "colorbars": colorbars}


class GlobeFrameWorker(QThread):
    """Renderiza um frame completo do globo num Agg offscreen (fora da GUI).

    A Figure/canvas nascem e morrem DENTRO do ``run()`` (thread-safe no Agg).
    O resultado chega por sinal com a geração do pedido — o slot descarta
    frames obsoletos (o usuário pode ter girado de novo nesse meio tempo).
    """

    frame_ready = pyqtSignal(int, object)  # (geração, np.ndarray RGBA | None)

    def __init__(self, gen: int, size_px: tuple[int, int], params: dict, parent=None) -> None:
        super().__init__(parent)
        self._gen = gen
        self._size_px = size_px
        self._params = params

    def run(self) -> None:  # noqa: D102 — contrato da QThread
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg

            w, h = self._size_px
            fig = Figure(figsize=(max(w, 2) / 100.0, max(h, 2) / 100.0), dpi=100, facecolor="black")
            canvas = FigureCanvasAgg(fig)
            compose_globe_frame(fig, **self._params)
            canvas.draw()
            frame = np.asarray(canvas.buffer_rgba()).copy()
        except Exception as exc:  # noqa: BLE001 — frame com erro não derruba a janela
            logger.warning("Frame do globo falhou no worker: %s", exc)
            frame = None
        self.frame_ready.emit(self._gen, frame)


class MotionBakeWorker(QThread):
    """Assa a textura de movimento (0,6 s) fora da GUI, uma vez ao abrir."""

    bake_ready = pyqtSignal(object)  # np.ndarray | None

    def __init__(self, scene: GlobeScene, parent=None) -> None:
        super().__init__(parent)
        self._scene = scene

    def run(self) -> None:  # noqa: D102 — contrato da QThread
        try:
            textura = bake_motion_texture(self._scene)
        except Exception as exc:  # noqa: BLE001 — sem textura, o giro cai no fallback
            logger.warning("Forno da textura de movimento falhou: %s", exc)
            textura = None
        self.bake_ready.emit(textura)


class GlobeCanvas(FigureCanvas):
    """Motor do globo na GUI: rascunho dos gestos + troca de frames do worker."""

    render_state = pyqtSignal(str)  # feedback p/ a barra de status da janela
    spin_stopped = pyqtSignal()  # gesto pausou a apresentação (sincroniza o botão)

    def __init__(
        self,
        scene: GlobeScene,
        *,
        center: tuple[float, float],
        satellite: Any = None,  # SatelliteData | None (full disk GOES)
        stamp_lines: list[str] | None = None,
        drawings: list[dict] | None = None,  # records (.cmbr) do traçado do usuário
        opts: GlobeOptions | None = None,
        parent: QWidget | None = None,
    ) -> None:
        self.fig = Figure(facecolor="black")
        super().__init__(self.fig)
        self.setParent(parent)
        self._scene = scene
        self._satellite = satellite
        self._stamp_lines = stamp_lines or []
        self._drawings = list(drawings or [])
        self.opts = opts or GlobeOptions()
        self._home = (float(center[0]), float(center[1]))
        self._center_lon, self._center_lat = self._home
        self._zoom = 1.0
        if scene.has_fields():
            self._skin = _PELE_CAMPOS
        elif satellite is not None:
            self._skin = _PELE_GOES
        else:
            self._skin = _PELE_RELEVO
        self._ax: Any = None  # GeoAxes do RASCUNHO (frames prontos são imagem crua)
        self._dragging = False
        self._drag_px: tuple[float, float] = (0.0, 0.0)
        self._drag_center0: tuple[float, float] = self._home
        self._draft_busy = False
        self._closed = False
        self._spinning = False
        # Frames assíncronos: geração corrente, worker vivo, pedido pendente,
        # último frame pronto e a vista (centro/zoom) a que ele corresponde.
        self._frame_gen = 0
        self._worker: GlobeFrameWorker | None = None
        self._pending_mode: str | None = None
        self._last_frame: np.ndarray | None = None
        self._frame_view: tuple[tuple[float, float], float] = (self._home, 1.0)
        self._image_ax: Any = None
        # Estrelas determinísticas (semente fixa = sem cintilação entre frames)
        rng = np.random.default_rng(42)
        self._stars = (rng.random(900), rng.random(900), rng.power(3.0, 900) * 2.2 + 0.2)

        # Repouso: 180 ms após o último gesto, pede o frame nítido ao worker
        # (espelho do _view_settle_timer do MapCanvas).
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(180)
        self._settle_timer.timeout.connect(self.render_full)

        # Forno da textura de movimento (apresentação) — assíncrono, 1× ao abrir.
        if scene.has_fields():
            bake = MotionBakeWorker(scene)
            bake.bake_ready.connect(self._on_bake_ready)
            bake.finished.connect(lambda wk=bake: _LIVE_WORKERS.discard(wk))
            _LIVE_WORKERS.add(bake)
            bake.start()

        self._cids = [
            self.mpl_connect("button_press_event", self._on_press),
            self.mpl_connect("motion_notify_event", self._on_motion),
            self.mpl_connect("button_release_event", self._on_release),
            self.mpl_connect("scroll_event", self._on_scroll),
        ]
        self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))

    # ─── Estado consultável/ajustável pela janela ────────────────────────────

    @property
    def center(self) -> tuple[float, float]:
        return (self._center_lon, self._center_lat)

    def available_skins(self) -> list[str]:
        skins = []
        if self._scene.has_fields():
            skins.append(_PELE_CAMPOS)
        if self._satellite is not None:
            skins.append(_PELE_GOES)
        skins.append(_PELE_RELEVO)
        return skins

    def set_skin(self, skin: str) -> None:
        if skin in self.available_skins() and skin != self._skin:
            self._skin = skin
            self.render_full()

    def update_opts(self, **changes: Any) -> None:
        """Aplica mudanças de preferências e re-renderiza no modo vigente."""
        self.opts = replace(self.opts, **changes)
        if self._spinning:
            self._request_frame(self._spin_mode())
        else:
            self.render_full()

    def _spin_mode(self) -> str:
        return "motion" if self.opts.spin_quality == "fluida" else "crisp"

    def set_spinning(self, on: bool) -> None:
        if on == self._spinning:
            return
        if on:
            self._spinning = True
            self._settle_timer.stop()
            self.render_state.emit("Apresentação: girando...")
            self._request_frame(self._spin_mode())
        else:
            self._spinning = False
            self.render_full()

    @property
    def spinning(self) -> bool:
        return self._spinning

    def rotate_by(self, dlon: float, dlat: float) -> None:
        self._set_center(self._center_lon + dlon, self._center_lat + dlat)
        self.render_draft()
        self._settle_timer.start()

    def go_home(self) -> None:
        self._set_center(*self._home)
        self._zoom = 1.0
        self.render_draft()
        self._settle_timer.start()

    def shutdown(self) -> None:
        """Para timers, desconecta eventos e SOLTA a cena (chamado no close).

        A cena global (textura + campos 0.25° inteiros + full disk GOES) é o
        grosso da memória. O worker em voo não é esperado: a geração avança e
        o resultado dele é descartado no slot (referência viva em
        ``_LIVE_WORKERS`` até o finished — QThread não pode morrer rodando).
        """
        self._closed = True
        self._spinning = False
        self._frame_gen += 1  # qualquer frame em voo fica obsoleto
        self._settle_timer.stop()
        for cid in self._cids:
            self.mpl_disconnect(cid)
        self.fig.clf()
        self._scene = GlobeScene(texture=None)
        self._satellite = None
        self._last_frame = None
        self._ax = None
        self._image_ax = None

    def _set_center(self, lon: float, lat: float) -> None:
        self._center_lon = ((lon + 180.0) % 360.0) - 180.0
        self._center_lat = float(np.clip(lat, -89.0, 89.0))

    def _on_bake_ready(self, textura) -> None:
        if self._closed:
            return
        self._scene.motion_texture = textura
        if self._spinning and self.opts.spin_quality == "fluida":
            self._request_frame("motion")  # giro passa a mostrar os campos assados

    # ─── Rascunho (GUI, síncrono e barato) ───────────────────────────────────

    def render_draft(self) -> None:
        """Frame leve do gesto (~0,15–0,3 s): base do tema + costa + grade."""
        if self._draft_busy:
            return
        self._draft_busy = True
        try:
            self.fig.clf()
            self._image_ax = None
            _paint_background(self.fig, self._stars, show_stars=self.opts.show_stars)
            ax: Any = self.fig.add_axes(
                _AX_RECT, projection=ccrs.Orthographic(self._center_lon, self._center_lat)
            )
            ax.set_global()
            ax.patch.set_facecolor("#060a14")
            ax.spines["geo"].set_edgecolor("#2a3b5c")
            if not self.opts.drag_light and self._skin != _PELE_GOES:
                _paint_theme_base(ax, self.opts.theme_name, "110m", allow_stock=False)
            ax.add_feature(
                cfeature.COASTLINE.with_scale("110m"), linewidth=0.5, edgecolor="#cfd8e3"
            )
            if self.opts.show_grid:
                ax.gridlines(color="#3a4a66", linewidth=0.3)
            half = _ORTHO_HALF / self._zoom
            ax.set_xlim(-half, half)
            ax.set_ylim(-half, half)
            self._ax = ax
            self.draw()
        finally:
            self._draft_busy = False

    # ─── Frames (worker assíncrono) ──────────────────────────────────────────

    def render_full(self) -> None:
        """Pede ao worker o frame vetorial nítido da vista atual (não bloqueia)."""
        self._request_frame("crisp")

    def _request_frame(self, mode: str) -> None:
        if self._closed:
            return
        self._frame_gen += 1
        if self._worker is not None:
            self._pending_mode = mode  # o worker atual termina; pedimos de novo
            return
        self._start_worker(mode)

    def _start_worker(self, mode: str) -> None:
        self._pending_mode = None
        w = max(2, int(self.fig.bbox.width))
        h = max(2, int(self.fig.bbox.height))
        params = {
            "scene": self._scene,
            "satellite": self._satellite,
            "skin": self._skin,
            "center": (self._center_lon, self._center_lat),
            "zoom": self._zoom,
            "stars": self._stars,
            "stamp_lines": self._stamp_lines,
            "drawings": self._drawings,
            "opts": self.opts,
            "mode": mode,
        }
        worker = GlobeFrameWorker(self._frame_gen, (w, h), params)
        worker.frame_ready.connect(self._on_frame_ready)
        worker.finished.connect(lambda wk=worker: self._on_worker_finished(wk))
        _LIVE_WORKERS.add(worker)
        self._worker = worker
        if not self._spinning:
            self.render_state.emit("Renderizando em alta qualidade...")
        worker.start()

    def _on_worker_finished(self, worker: GlobeFrameWorker) -> None:
        _LIVE_WORKERS.discard(worker)
        if self._worker is worker:
            self._worker = None
        if self._pending_mode is not None and not self._closed:
            self._start_worker(self._pending_mode)

    def _on_frame_ready(self, gen: int, frame) -> None:
        if self._closed or gen != self._frame_gen:
            return  # obsoleto: o usuário girou de novo (ou a janela fechou)
        if frame is None:
            self.render_state.emit("Falha no render — veja o log")
            return
        self._last_frame = frame
        self._frame_view = ((self._center_lon, self._center_lat), self._zoom)
        self._show_frame(frame)
        if self._spinning:
            # Pipeline da apresentação: mostra → avança → pede o próximo.
            # O ritmo nasce do próprio render (máquina rápida = giro liso).
            self._set_center(self._center_lon - self.opts.spin_step, self._center_lat)
            self._request_frame(self._spin_mode())
        else:
            self.render_state.emit("")

    def _show_frame(self, frame: np.ndarray) -> None:
        """Troca a tela pelo frame pronto (imagem crua 1:1 — ~50 ms)."""
        self.fig.clf()
        self._ax = None
        ax = self.fig.add_axes((0.0, 0.0, 1.0, 1.0))
        ax.set_axis_off()
        ax.imshow(frame, interpolation="nearest")
        self._image_ax = ax
        self.draw_idle()

    # ─── Gestos ──────────────────────────────────────────────────────────────

    def _px_to_lonlat(self, x: float, y: float) -> tuple[float, float] | None:
        """Pixel do canvas → lon/lat, válido no rascunho E no frame pronto.

        Usa a VISTA DO QUE ESTÁ NA TELA (frame pronto pode ser de instantes
        atrás): inverte o retângulo ``_AX_RECT`` manualmente — não dependemos
        de um GeoAxes vivo (o frame é imagem crua).
        """
        if self._image_ax is not None:
            (center, zoom) = self._frame_view
        else:
            center, zoom = (self._center_lon, self._center_lat), self._zoom
        wf = float(self.fig.bbox.width)
        hf = float(self.fig.bbox.height)
        if wf < 2 or hf < 2:
            return None
        fx = (x / wf - _AX_RECT[0]) / _AX_RECT[2]
        fy = (y / hf - _AX_RECT[1]) / _AX_RECT[3]
        if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
            return None
        half = _ORTHO_HALF / zoom
        data_x = (fx - 0.5) * 2.0 * half
        data_y = (fy - 0.5) * 2.0 * half
        proj = ccrs.Orthographic(center[0], center[1])
        lon, lat = ccrs.PlateCarree().transform_point(data_x, data_y, proj)
        if not (np.isfinite(lon) and np.isfinite(lat)):
            return None  # fora do disco
        return float(lon), float(lat)

    def _pause_spin_by_gesture(self) -> None:
        if self._spinning:
            self._spinning = False
            self.spin_stopped.emit()  # sincroniza o botão da janela

    def _on_press(self, event) -> None:
        if event.button != 1:
            return
        self._pause_spin_by_gesture()
        if getattr(event, "dblclick", False):
            alvo = self._px_to_lonlat(float(event.x), float(event.y))
            if alvo is not None:
                self._set_center(*alvo)
                self.render_draft()
                self._settle_timer.start()
            return
        self._dragging = True
        self._frame_gen += 1  # frame em voo (ex.: do giro pausado) fica obsoleto
        self._drag_px = (float(event.x), float(event.y))
        self._drag_center0 = (self._center_lon, self._center_lat)
        self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))

    def _on_motion(self, event) -> None:
        if not self._dragging:
            return
        # Pixels, nunca xdata: em Orthographic xdata é metros e None fora do
        # disco — pixels funcionam durante todo o arraste.
        dx = float(event.x) - self._drag_px[0]
        dy = float(event.y) - self._drag_px[1]
        # Sensibilidade: meio disco (~90°) por meia altura do canvas; a
        # superfície SEGUE a mão (arrastar p/ leste gira o centro p/ oeste).
        h = max(1.0, float(self.fig.bbox.height))
        k = 180.0 / h / self._zoom
        lon0, lat0 = self._drag_center0
        novo_lon = lon0 - dx * k
        novo_lat = lat0 + dy * k
        if abs(novo_lon - self._center_lon) < 0.4 and abs(novo_lat - self._center_lat) < 0.4:
            return
        self._set_center(novo_lon, novo_lat)
        self.render_draft()

    def _on_release(self, event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        self._settle_timer.start()

    def _on_scroll(self, event) -> None:
        self._pause_spin_by_gesture()
        fator = 1.25 if event.button == "up" else 0.8
        self._zoom = float(np.clip(self._zoom * fator, 1.0, 8.0))
        if self._image_ax is not None:
            self._crop_zoom_image()
        elif self._ax is not None:
            half = _ORTHO_HALF / self._zoom
            self._ax.set_xlim(-half, half)
            self._ax.set_ylim(-half, half)
        # draw_idle coalesce ticks consecutivos da rodinha.
        self.draw_idle()
        self._settle_timer.start()

    def _crop_zoom_image(self) -> None:
        """Zoom instantâneo por RECORTE do frame pronto (nítido chega no repouso)."""
        if self._image_ax is None or self._last_frame is None:
            return
        h, w = self._last_frame.shape[:2]
        rel = self._zoom / max(self._frame_view[1], 1e-6)
        half_w = w / 2.0 / rel
        half_h = h / 2.0 / rel
        self._image_ax.set_xlim(w / 2.0 - half_w, w / 2.0 + half_w)
        self._image_ax.set_ylim(h / 2.0 + half_h, h / 2.0 - half_h)  # origem no topo

    # ─── Exportação ──────────────────────────────────────────────────────────

    def save_png(self, destino) -> bool:
        """Salva o ÚLTIMO frame nítido (ou a figura atual como fallback)."""
        try:
            if self._last_frame is not None:
                import matplotlib.image as mpimg

                mpimg.imsave(str(destino), self._last_frame)
            else:
                self.fig.savefig(destino, dpi=150, facecolor="black")
            return True
        except OSError as exc:
            logger.warning("PNG do globo falhou: %s", exc)
            return False


class GlobeWindow(QMainWindow):
    """Casca fullscreen do globo: barra de controles + GlobeCanvas.

    SEM ``WA_DeleteOnClose``: o Qt deletaria o C++ do FigureCanvas com os
    wrappers matplotlib ainda vivos e o GC do Python estouraria num access
    violation depois (visto em teste). Fechar esconde + ``closed`` avisa o
    dono, que solta a referência — o Python destrói na ordem certa. Para
    isso funcionar a janela precisa nascer SEM parent Qt (revisão pegou o
    vazamento: com parent, a posse C++ é do MainWindow e soltar a referência
    Python não destrói nada — cada ciclo abrir/fechar acumulava uma janela).
    A tela do fullscreen vem por parâmetro em ``show_fullscreen_on_screen``.
    """

    closed = pyqtSignal()

    _DICA = "Arraste para girar · scroll aproxima · duplo clique centraliza"

    def __init__(
        self,
        scene: GlobeScene,
        *,
        center: tuple[float, float],
        satellite: Any = None,
        stamp_lines: list[str] | None = None,
        drawings: list[dict] | None = None,
        output_dir: Any = None,
        chart_theme: str = _TEMA_RELEVO,
        settings: QSettings | None = None,  # injetável (testes usam .ini em tmp)
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        from cartomet_br.gui._constants import APP_NAME
        from cartomet_br.gui.themes import DARK_STYLE

        self.setWindowTitle("Vista de Globo — CartoMet BR")
        self.setStyleSheet(DARK_STYLE)
        self._output_dir = output_dir
        self._settings = settings if settings is not None else QSettings("PPGGRD-UFPA", APP_NAME)
        opts = GlobeOptions.load(self._settings, chart_theme)

        self.globe = GlobeCanvas(
            scene,
            center=center,
            satellite=satellite,
            stamp_lines=stamp_lines,
            drawings=drawings,
            opts=opts,
            parent=self,
        )
        self.globe.render_state.connect(self._on_render_state)
        self.globe.spin_stopped.connect(self._on_spin_stopped_by_gesture)

        barra = QWidget()
        lay = QHBoxLayout(barra)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)

        voltar = QPushButton("⟵ Voltar (Esc)")
        voltar.clicked.connect(self.close)
        lay.addWidget(voltar)

        home = QPushButton("🏠 Centro")
        home.setToolTip("Volta o globo ao centro da sua região (tecla Home).")
        home.clicked.connect(self.globe.go_home)
        lay.addWidget(home)

        self.spin_btn = QPushButton("▶ Apresentação")
        self.spin_btn.setCheckable(True)
        self.spin_btn.setToolTip(
            "Gira o globo sozinho COM os campos visíveis — qualquer gesto pausa.\n"
            "Velocidade e qualidade do giro: painel ⚙ Personalizar."
        )
        self.spin_btn.toggled.connect(self.globe.set_spinning)
        lay.addWidget(self.spin_btn)

        lay.addWidget(QLabel("Pele:"))
        self.skin_combo = QComboBox()
        self.skin_combo.addItems(self.globe.available_skins())
        self.skin_combo.currentTextChanged.connect(self.globe.set_skin)
        lay.addWidget(self.skin_combo)

        self.custom_btn = QPushButton("⚙ Personalizar")
        self.custom_btn.setCheckable(True)
        self.custom_btn.setToolTip("Tema, camadas, rótulos, apresentação e desempenho.")
        lay.addWidget(self.custom_btn)

        png_btn = QPushButton("💾 PNG")
        png_btn.setToolTip("Salva a vista atual do globo (fundo estrelado incluso).")
        png_btn.clicked.connect(self._save_png)
        lay.addWidget(png_btn)

        self.status = QLabel(self._DICA)
        self.status.setStyleSheet("color: #95A5A6; font-size: 11px;")
        lay.addWidget(self.status, stretch=1)

        corpo = QWidget()
        hbox = QHBoxLayout(corpo)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)
        hbox.addWidget(self.globe, stretch=1)
        self.drawer = self._build_drawer()
        self.drawer.setVisible(False)
        hbox.addWidget(self.drawer)
        self.custom_btn.toggled.connect(self.drawer.setVisible)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(barra)
        v.addWidget(corpo, stretch=1)
        self.setCentralWidget(central)

        # Esc fecha com o foco em QUALQUER filho (atalho de janela).
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.activated.connect(self.close)

    # ─── Gaveta de personalização ────────────────────────────────────────────

    def _build_drawer(self) -> QWidget:
        from PyQt6.QtWidgets import QCheckBox, QGroupBox, QScrollArea

        opts = self.globe.opts
        painel = QWidget()
        painel.setFixedWidth(290)
        vlay = QVBoxLayout(painel)
        vlay.setContentsMargins(8, 8, 8, 8)
        vlay.setSpacing(8)

        def _combo(parent_lay, rotulo, itens, atual, on_change):
            parent_lay.addWidget(QLabel(rotulo))
            combo = QComboBox()
            combo.addItems(itens)
            if atual in itens:
                combo.setCurrentText(atual)
            combo.currentTextChanged.connect(on_change)
            parent_lay.addWidget(combo)
            return combo

        def _check(parent_lay, rotulo, atual, chave):
            chk = QCheckBox(rotulo)
            chk.setChecked(atual)
            chk.toggled.connect(lambda v, k=chave: self._apply_opt(**{k: v}))
            parent_lay.addWidget(chk)
            return chk

        g_tema = QGroupBox("Tema do globo")
        l_tema = QVBoxLayout(g_tema)
        self.theme_combo = _combo(
            l_tema,
            "Base do planeta:",
            [_TEMA_RELEVO, *MAP_THEMES.keys()],
            opts.theme_name,
            lambda t: self._apply_opt(theme_name=t),
        )
        vlay.addWidget(g_tema)

        g_cam = QGroupBox("Camadas do mapa-base")
        l_cam = QVBoxLayout(g_cam)
        _check(l_cam, "Linha de costa", opts.show_coast, "show_coast")
        _check(l_cam, "Fronteiras de países", opts.show_borders, "show_borders")
        _check(l_cam, "Divisas de estados", opts.show_states, "show_states")
        _check(l_cam, "Grade de meridianos/paralelos", opts.show_grid, "show_grid")
        _check(l_cam, "Estrelas e halo atmosférico", opts.show_stars, "show_stars")
        vlay.addWidget(g_cam)

        g_rot = QGroupBox("Rótulos de isolinhas")
        l_rot = QVBoxLayout(g_rot)
        mapa_rot = {"Todos": "todos", "Só no miolo do disco": "miolo", "Nenhum": "nenhum"}
        inv_rot = {v: k for k, v in mapa_rot.items()}
        _combo(
            l_rot,
            "Onde rotular:",
            list(mapa_rot),
            inv_rot.get(opts.label_mode, "Só no miolo do disco"),
            lambda t: self._apply_opt(label_mode=mapa_rot[t]),
        )
        vlay.addWidget(g_rot)

        g_vento = QGroupBox("Vento")
        l_vento = QVBoxLayout(g_vento)
        _check(
            l_vento, "Linhas de corrente no frame nítido", opts.streams_enabled, "streams_enabled"
        )
        nota = QLabel("Cor e densidade seguem o estilo da carta.")
        nota.setStyleSheet("color: #95A5A6; font-size: 10px;")
        nota.setWordWrap(True)
        l_vento.addWidget(nota)
        vlay.addWidget(g_vento)

        g_apres = QGroupBox("Apresentação")
        l_apres = QVBoxLayout(g_apres)
        mapa_vel = {"Lenta (1°/quadro)": 1.0, "Média (2°/quadro)": 2.0, "Rápida (4°/quadro)": 4.0}
        inv_vel = {v: k for k, v in mapa_vel.items()}
        _combo(
            l_apres,
            "Velocidade do giro:",
            list(mapa_vel),
            inv_vel.get(opts.spin_step, "Média (2°/quadro)"),
            lambda t: self._apply_opt(spin_step=mapa_vel[t]),
        )
        mapa_q = {"Fluida (campos em textura)": "fluida", "Completa (vetorial, lenta)": "completa"}
        inv_q = {v: k for k, v in mapa_q.items()}
        _combo(
            l_apres,
            "Qualidade do giro:",
            list(mapa_q),
            inv_q.get(opts.spin_quality, "Fluida (campos em textura)"),
            lambda t: self._apply_opt(spin_quality=mapa_q[t]),
        )
        vlay.addWidget(g_apres)

        g_perf = QGroupBox("Desempenho")
        l_perf = QVBoxLayout(g_perf)
        _combo(
            l_perf,
            "Qualidade do repouso:",
            list(_QUALITY_REGRID),
            opts.quality,
            lambda t: self._apply_opt(quality=t),
        )
        _check(l_perf, "Arraste leve (sem base de tema)", opts.drag_light, "drag_light")
        vlay.addWidget(g_perf)

        vlay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(painel)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(310)
        return scroll

    def _apply_opt(self, **changes: Any) -> None:
        self.globe.update_opts(**changes)
        self.globe.opts.save(self._settings)

    def _on_spin_stopped_by_gesture(self) -> None:
        # Botão volta a "solto" sem re-disparar set_spinning; o próprio gesto
        # que pausou já cuida do render (rascunho → repouso).
        self.spin_btn.blockSignals(True)
        self.spin_btn.setChecked(False)
        self.spin_btn.blockSignals(False)

    def _on_render_state(self, msg: str) -> None:
        self.status.setText(msg or self._DICA)

    def show_fullscreen_on_screen(self, screen=None) -> None:
        """Tela cheia no monitor dado (o do MainWindow — multi-monitor correto).

        Rascunho IMEDIATO (nada de tela preta) + frame nítido chega do worker.
        """
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.globe.render_draft()
        self.globe.render_full()

    def keyPressEvent(self, event) -> None:  # noqa: N802 — override Qt
        key = event.key()
        if key == Qt.Key.Key_Escape:
            # Redundante com o QShortcut de janela — mas o atalho exige
            # janela ATIVA (nem sempre verdade em offscreen/multi-monitor).
            self.close()
        elif key == Qt.Key.Key_Left:
            self.globe.rotate_by(-10.0, 0.0)
        elif key == Qt.Key.Key_Right:
            self.globe.rotate_by(10.0, 0.0)
        elif key == Qt.Key.Key_Up:
            self.globe.rotate_by(0.0, 10.0)
        elif key == Qt.Key.Key_Down:
            self.globe.rotate_by(0.0, -10.0)
        elif key == Qt.Key.Key_Home:
            self.globe.go_home()
        else:
            super().keyPressEvent(event)

    def _save_png(self) -> None:
        from pathlib import Path

        out_dir = Path(self._output_dir) if self._output_dir else Path.cwd()
        out_dir.mkdir(parents=True, exist_ok=True)
        destino = out_dir / f"globo_{datetime.now():%Y%m%d_%H%M%S}.png"
        if self.globe.save_png(destino):
            self.status.setText(f"Globo salvo: {destino}")
        else:
            self.status.setText("Erro ao salvar PNG — veja o log")

    def closeEvent(self, event) -> None:  # noqa: N802 — override Qt
        self.globe.shutdown()
        self.closed.emit()
        super().closeEvent(event)
