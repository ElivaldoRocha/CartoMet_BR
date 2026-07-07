"""
Canvas Matplotlib com Cartopy para o CartoMet BR.

Contém a classe MapCanvas — mapa interativo com suporte a desenho
de simbologias, anotações, régua, campos sinóticos, PL/OLR e satélite.
Inclui sistema de undo/redo baseado no padrão Command.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

import matplotlib
import numpy as np

# Backend interativo Qt para a GUI. Em ambiente headless (CI, testes, servidor
# sem display) o Matplotlib recusa carregar um backend interativo; nesse caso
# mantém-se o backend não-interativo atual (Agg) sem quebrar o import do módulo.
with contextlib.suppress(ImportError):
    matplotlib.use("QtAgg")
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.patheffects as pe
import matplotlib.ticker as mticker
import matplotlib.transforms as mtransforms
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QSizePolicy

from cartomet_br.charts.interactive import interpolar_pontos
from cartomet_br.charts.synoptic import compute_persistence_maps, plot_maxmin_points
from cartomet_br.core.config import COLORS, LEVELS, Config
from cartomet_br.data.cities import (
    CITY_DENSITY_FACTORS,
    DEFAULT_CITY_DENSITY,
    load_cities,
    select_cities,
)
from cartomet_br.data.ecmwf import (
    VARIABLE_REGISTRY,
    PLFieldData,
    SatelliteData,
    get_ir_colormap,
)
from cartomet_br.data.sst import SSTData
from cartomet_br.data.stations import (
    DEFAULT_OBS_DENSITY,
    OBS_DENSITY_FACTORS,
    thinning_radius,
)
from cartomet_br.gui._constants import APP_VERSION
from cartomet_br.gui.draw_tools import (
    PEN_MIN_PIXEL_DIST,
    SHAPE_MIN_DRAG_PIXELS,
    SHAPE_OUTLINE_ZORDER,
    AnnotationCommand,
    DrawCommand,
    DrawStyle,
    EmojiCommand,
    PenCommand,
    PointCommand,
    ShapeCommand,
    build_preview_ring,
    create_pen_artist,
    create_shape_artist,
    default_arrow_head_size,
)
from cartomet_br.gui.themes import MAP_THEMES
from cartomet_br.symbols import MODOS

logger = logging.getLogger(__name__)

# Larguras dos contornos de contexto (costa, países, estados): (normal, realçada).
# O modo "Destacar contornos" (set_context_emphasis) engrossa as linhas e acende
# um halo de contraste por baixo — pedido operacional: sobre satélite ou campo
# preenchido, a linha fina de estados (0.2) some e o previsor perde a referência.
CONTEXT_LINEWIDTHS: dict[str, tuple[float, float]] = {
    "coastline": (0.6, 1.1),
    "borders": (0.4, 0.9),
    "states": (0.2, 0.8),
}
# Espessura EXTRA do halo em relação à linha realçada (desenho duplo:
# linha larga de contraste por baixo + linha forte do tema por cima).
CONTEXT_HALO_EXTRA: float = 1.8


# ═══════════════════════════════════════════════════════════════════════════════
#  SISTEMA DE UNDO/REDO (Padrão Command)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Os comandos (DrawCommand/PointCommand/AnnotationCommand/EmojiCommand/PenCommand/
# ShapeCommand) são dataclasses PURAS definidas em ``draw_tools`` e reexportadas
# acima — assim ``project_io`` os serializa sem depender da GUI.


class DrawingHistory:
    """Pilha de histórico com suporte a undo/redo."""

    def __init__(self, max_size: int = 50):
        # Comandos: DrawCommand | PointCommand | AnnotationCommand
        #           | PenCommand | ShapeCommand (draw_tools)
        self._undo_stack: list[object] = []
        self._redo_stack: list[object] = []
        self._max_size = max_size

    @property
    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    @property
    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    @property
    def undo_count(self) -> int:
        return len(self._undo_stack)

    @property
    def redo_count(self) -> int:
        return len(self._redo_stack)

    @property
    def commands(self) -> list:
        """Snapshot (cópia) dos comandos ativos, em ordem de criação (p/ salvar projeto)."""
        return list(self._undo_stack)

    def push(self, cmd: DrawCommand | PointCommand | AnnotationCommand) -> None:
        """Registra um comando executado. Limpa a pilha de redo."""
        self._undo_stack.append(cmd)
        self._redo_stack.clear()
        if len(self._undo_stack) > self._max_size:
            self._undo_stack.pop(0)

    def undo(self) -> DrawCommand | PointCommand | AnnotationCommand | None:
        """Remove o último comando e retorna-o para desfazer."""
        if not self._undo_stack:
            return None
        cmd = self._undo_stack.pop()
        self._redo_stack.append(cmd)
        return cmd

    def redo(self) -> DrawCommand | PointCommand | AnnotationCommand | None:
        """Refaz o último comando desfeito."""
        if not self._redo_stack:
            return None
        cmd = self._redo_stack.pop()
        self._undo_stack.append(cmd)
        return cmd

    def remove_last_of(self, types: tuple) -> object | None:
        """Remove e devolve o comando MAIS RECENTE cujo tipo ∈ `types`.

        Busca da ponta para a base da pilha de undo (pode remover do meio, se
        outros desenhos vieram depois). Não interage com a pilha de redo —
        mesma semântica do "desfazer emoji" (sem refazer dedicado).
        """
        for i in range(len(self._undo_stack) - 1, -1, -1):
            if isinstance(self._undo_stack[i], types):
                return self._undo_stack.pop(i)
        return None

    def clear(self) -> None:
        """Limpa todo o histórico."""
        self._undo_stack.clear()
        self._redo_stack.clear()


class MapCanvas(FigureCanvas):
    """Canvas matplotlib com mapa Cartopy e suporte a desenho interativo."""

    point_added = pyqtSignal(float, float)
    coords_updated = pyqtSignal(float, float)
    annotation_requested = pyqtSignal(float, float)
    shape_draft_changed = pyqtSignal(int)  # nº de vértices do polígono em rascunho
    extent_changed = pyqtSignal(list)  # [lon_min, lat_min, lon_max, lat_max] após zoom/recorte
    figure_zoom_requested = pyqtSignal(int)  # +1 ampliar / -1 reduzir a FIGURA (Ctrl+roda)
    vertical_sounding_requested = pyqtSignal(
        object
    )  # estação RAOB ancorada (dict) p/ Sonda Vertical
    model_sounding_requested = pyqtSignal(float, float)  # (lon, lat) p/ pseudo-sondagem do modelo
    meteogram_requested = pyqtSignal(float, float)  # (lon, lat) p/ meteograma (F6)
    cross_section_requested = pyqtSignal(
        float, float, float, float
    )  # (lon_a,lat_a,lon_b,lat_b) p/ corte vertical (F4)

    def __init__(self, parent: QSizePolicy | None = None, config: Config | None = None) -> None:
        self.config: Config = config or Config()
        self.current_theme: str = "Clássico"

        self.fig = Figure(figsize=(12, 8), facecolor="white", dpi=100)
        super().__init__(self.fig)

        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.ax = self.fig.add_subplot(111, projection=ccrs.PlateCarree())

        # Modo de interação: "draw" | "annotate" | "ruler" | None
        self.interaction_mode: str | None = None

        # Estado de desenho
        self.current_symbol: str = "1"
        self.flip: bool = False
        self.zcit_intensity: int = 1  # 1=Fraca, 2=Moderada, 3=Forte (ZCIT)
        self.points_x: list[float] = []
        self.points_y: list[float] = []
        self.lines: list[object] = []
        self.preview_line: object | None = None
        self.synoptic_data: object | None = None

        # Histórico de undo/redo
        self.history = DrawingHistory(max_size=50)

        # Caneta (traço livre) e Formas customizáveis
        self.pen_style = DrawStyle(edge_color="#E74C3C", fill_color=None)
        self.shape_style = DrawStyle(edge_color="#E74C3C", fill_color=None)
        self.shape_tool: str = "rect"  # "rect"|"ellipse"|"arrow"|"line"|"polygon"
        self._pen_active: bool = False
        self._pen_draft_x: list[float] = []
        self._pen_draft_y: list[float] = []
        self._pen_last_px: tuple[float, float] | None = None
        self._shape_anchor: tuple[float, float] | None = None  # lon/lat do press
        self._shape_anchor_px: tuple[float, float] | None = None  # pixels do press
        self._shape_drag_current: tuple[float, float] | None = None  # lon/lat do cursor
        self._shape_last_px: tuple[float, float] | None = None  # pixels do cursor
        self._shape_draft_x: list[float] = []  # vértices do polígono em rascunho
        self._shape_draft_y: list[float] = []
        self._draft_preview = None  # Line2D de preview (caneta/forma)

        # Anotações de texto
        self._annotations: list = []

        # Emojis meteorológicos. _emoji_annotations guarda os artistas vivos;
        # _emoji_records guarda o EmojiCommand paralelo (dados puros) p/ persistir
        # no projeto (.cmbr) — emojis não passam pelo histórico de undo/redo.
        self._emoji_annotations: list = []
        self._emoji_records: list[EmojiCommand] = []
        self.current_emoji: str = "☀"
        self._emoji_fontsize: int = 28

        # Régua de distância
        self._ruler_points = []
        self._ruler_artists = []

        # Mobília de "carta OMM" (F7) — cabeçalho institucional + legenda dos
        # símbolos. Só existe transitoriamente, em volta do export; nunca é
        # desenhada na edição ao vivo. Guardada aqui só p/ poder remover depois.
        self._chart_furniture: list = []

        # Rastreamento de artists por camada sinótica
        self._synoptic_artists = {
            "pnmm": [],
            "thickness": [],
            "centers": [],
        }

        # Camadas PL / OLR
        self._pl_data = {}
        self._pl_artists = {}
        self._pl_wind_types = {}
        self._pl_colorbars = {}
        self._pl_zorder_counter = 7

        # Níveis congelados por camada (animação de steps): quando presentes,
        # _plot_scalar_* usa estes níveis em vez de derivá-los do quadro —
        # a colorbar não "respira" entre frames. Vazio fora da animação.
        self._frozen_levels: dict = {}

        # Opções de plotagem
        self.plot_options = {
            "pnmm": True,
            "thickness": True,
            "centers": True,
            # Filtro orográfico dos centros H/L (Andes/Altiplano: PNMM artefactual)
            "centers_terrain_filter": True,
        }

        # Contornos de contexto (costa/fronteiras/estados) e o modo "Destacar
        # contornos". A flag sobrevive a trocas de tema/região (_setup_base_map
        # reconstrói os artistas e re-aplica o realce se estava ativo).
        self._context_artists: dict[str, object] = {}
        self._context_halo_artists: dict[str, object] = {}
        self._context_emphasis: bool = False

        # Camada "Cidades" (sedes municipais IBGE rotuladas). Como o realce,
        # a flag sobrevive à reconstrução do mapa base.
        self._cities_artists: list = []
        self._cities_enabled: bool = False
        self._city_density_factor: float = CITY_DENSITY_FACTORS[DEFAULT_CITY_DENSITY]

        # Rosa dos ventos (indicador de norte, canto superior direito).
        self._north_arrow_artists: list = []
        self._north_arrow_enabled: bool = False

        # Imagem de satélite
        self._sat_artist = None
        self._sat_data = None

        # TSM (MUR SST)
        self._sst_artist = None
        self._sst_data = None
        self._sst_colorbar = None

        # Observações de superfície (SYNOP / METAR)
        self._station_artists = {"metar": [], "synop": []}
        self._station_data = {"metar": None, "synop": None}
        self._obs_density_factor = OBS_DENSITY_FACTORS[DEFAULT_OBS_DENSITY]

        # Índice LOCZCIT-PA (raster categórico da ZCIT)
        self._loczcit_artist = None
        self._loczcit_colorbar = None
        self._loczcit_axis_artists: list = []  # overlay opcional do eixo (linhas/scatter/nó)

        # Bloqueio atmosférico (anomalia de Z500)
        self._blocking_artists: list = []
        self._blocking_colorbar = None

        # Motor da mesa suspenso? (animação impõe geometria congelada por quadro)
        self._layout_suspended = False
        # Draws intermediários suprimidos? (operações em lote — batch_layout)
        self._draw_suspended = False
        # Cache do ranking topológico dos centros H/L (peak_persistence é caro;
        # o campo não muda entre replots por extent — invalida em set_synoptic_data)
        self._centers_persistence: dict = {}

        # Sonda Vertical (marcador temporário da estação RAOB ancorada)
        self._sounding_marker = None
        # Fonte da sonda: "observed" (Wyoming, ancora na estação) ou "model"
        # (perfil do IFS no ponto clicado). Definida pelo seletor do painel.
        self.sounding_source = "observed"

        # Corte Vertical (F4): ponto A pendente + artistas da sobreposição A→B.
        self._xsec_anchor = None
        self._xsec_overlay: list = []

        # Zoom / navegação
        self._extent_history: list[list[float]] = []  # pilha de extents anteriores
        self._pan_active = False
        self._pan_start: tuple[float, float] | None = None
        self._rect_selector = None

        # Assentamento da vista após scroll/pan: o reflow do layout (que mede
        # bboxes com o renderer) é caro demais por tick de roda/arraste — um
        # timer single-shot re-arma a cada gesto e dispara só no repouso.
        # Sem isso o título fica cortado após zoom de scroll (apply_extent já
        # faz o reflow, mas scroll/pan não passam por ele).
        self._view_settle_timer = QTimer(self)
        self._view_settle_timer.setSingleShot(True)
        self._view_settle_timer.setInterval(180)
        self._view_settle_timer.timeout.connect(self._deferred_view_settle)

        # Conecta eventos
        self.mpl_connect("button_press_event", self._on_click)
        self.mpl_connect("motion_notify_event", self._on_motion)
        self.mpl_connect("button_release_event", self._on_release)
        self.mpl_connect("scroll_event", self._on_scroll)

        self._setup_base_map()

    # ═══════════════════════════════════════════════════════════════════════
    #  MAPA BASE
    # ═══════════════════════════════════════════════════════════════════════

    def _setup_base_map(self) -> None:
        """Configura mapa base com Cartopy. Limpa TUDO e reconstrói."""
        self._synoptic_artists = {k: [] for k in self._synoptic_artists}
        self._pl_artists.clear()
        self._pl_wind_types.clear()
        self._pl_data.clear()
        self._pl_colorbars.clear()
        self._pl_zorder_counter = 7
        self.synoptic_data = None
        self.lines.clear()
        self.preview_line = None
        self.points_x.clear()
        self.points_y.clear()
        # Rascunhos de caneta/forma (troca de tema dá ax.clear() — sem fantasmas)
        self._pen_active = False
        self._pen_draft_x.clear()
        self._pen_draft_y.clear()
        self._pen_last_px = None
        self._shape_anchor = None
        self._shape_anchor_px = None
        self._shape_drag_current = None
        self._shape_last_px = None
        self._shape_draft_x.clear()
        self._shape_draft_y.clear()
        self._draft_preview = None
        self._annotations.clear()
        # ax.clear() abaixo remove os emojis do eixo — sincroniza as listas para
        # não deixar registros fantasmas (que seriam serializados no projeto).
        self._emoji_annotations.clear()
        self._emoji_records.clear()
        self._ruler_points.clear()
        self._ruler_artists.clear()
        self._sat_artist = None
        self._sat_data = None
        self._sst_artist = None
        self._sst_data = None
        self._sst_colorbar = None
        # ax.clear() abaixo remove os artistas de cidades e da rosa dos ventos
        # — só esvazia as listas
        self._cities_artists.clear()
        self._north_arrow_artists.clear()
        self._station_artists = {"metar": [], "synop": []}
        self._station_data = {"metar": None, "synop": None}
        self._loczcit_artist = None
        self._loczcit_colorbar = None
        self._loczcit_axis_artists = []
        self._blocking_artists = []
        self._blocking_colorbar = None
        self.history.clear()

        self.ax.clear()

        extent = self.config.extent
        self.ax.set_extent([extent[0], extent[2], extent[1], extent[3]], crs=ccrs.PlateCarree())

        theme = MAP_THEMES.get(self.current_theme, MAP_THEMES["Clássico"])
        self.ax.set_facecolor(theme["ocean"])

        self.ax.add_feature(
            cfeature.NaturalEarthFeature(
                "physical", "ocean", "50m", facecolor=theme["ocean"], edgecolor="none"
            ),
            zorder=0,
        )
        self.ax.add_feature(
            cfeature.NaturalEarthFeature(
                "physical", "land", "50m", facecolor=theme["land"], edgecolor="none"
            ),
            zorder=1,
        )
        self.ax.add_feature(
            cfeature.NaturalEarthFeature(
                "physical", "lakes", "50m", facecolor=theme["lakes"], edgecolor="none"
            ),
            zorder=1,
        )
        # Contornos geográficos (costa, fronteiras, estados) ficam ACIMA dos
        # campos preenchidos em altitude (PL contourf vai até zorder 14) para
        # que o usuário continue se localizando mesmo com a temperatura/umidade
        # etc. preenchendo a carta. Permanecem abaixo de estações/desenhos (20+).
        # Cada contorno ganha um gêmeo de halo (invisível por padrão, zorder
        # imediatamente abaixo) para o modo "Destacar contornos" — desenho
        # duplo em vez de path effects (proibidos em patches sobre GeoAxes).
        context_features = {
            "coastline": cfeature.NaturalEarthFeature(
                "physical", "coastline", "50m", facecolor="none"
            ),
            "borders": cfeature.NaturalEarthFeature(
                "cultural", "admin_0_boundary_lines_land", "50m", facecolor="none"
            ),
            "states": cfeature.NaturalEarthFeature(
                "cultural", "admin_1_states_provinces_lines", "50m", facecolor="none"
            ),
        }
        context_zorder = {"coastline": 16, "borders": 16, "states": 15}
        halo_theme = theme["emphasis_halo"]
        self._context_halo_artists = {
            # Halo sólido mesmo sob a fronteira tracejada: mais visível e sem
            # o problema de alinhar os traços entre as duas linhas.
            name: self.ax.add_feature(
                feat,
                edgecolor=halo_theme,
                linewidth=CONTEXT_LINEWIDTHS[name][1] + CONTEXT_HALO_EXTRA,
                zorder=context_zorder[name] - 0.1,
                visible=False,
            )
            for name, feat in context_features.items()
        }
        self._context_artists = {
            "coastline": self.ax.add_feature(
                context_features["coastline"],
                edgecolor=theme["coastline"],
                linewidth=CONTEXT_LINEWIDTHS["coastline"][0],
                zorder=16,
            ),
            "borders": self.ax.add_feature(
                context_features["borders"],
                edgecolor=theme["borders"],
                linewidth=CONTEXT_LINEWIDTHS["borders"][0],
                linestyle="--",
                zorder=16,
            ),
            "states": self.ax.add_feature(
                context_features["states"],
                edgecolor=theme["states"],
                linewidth=CONTEXT_LINEWIDTHS["states"][0],
                zorder=15,
            ),
        }
        if self._context_emphasis:
            # Troca de tema/região reconstruiu os artistas — re-aplica o realce
            self._apply_context_emphasis(True)

        gl = self.ax.gridlines(
            draw_labels=True,
            linewidth=0.3,
            color="#CCCCCC",
            alpha=0.8,
            x_inline=False,
            y_inline=False,
        )
        gl.xlocator = mticker.MultipleLocator(10)
        gl.ylocator = mticker.MultipleLocator(10)
        gl.top_labels = False
        gl.right_labels = False
        gl.xlabel_style = {"size": 9, "color": "#333333"}
        gl.ylabel_style = {"size": 9, "color": "#333333"}

        # Dentro da carta (canto inferior direito): fora da linha dos rótulos de
        # longitude, com quem a posição antiga (y=-0.02) colidia em todo export.
        # O halo branco garante leitura sobre qualquer fundo (satélite escuro,
        # TSM, campos preenchidos) — sem ele a marca some em tons próximos.
        self.ax.text(
            0.995,
            0.012,
            f"CartoMet BR v{APP_VERSION}",
            transform=self.ax.transAxes,
            fontsize=8,
            color="#666666",
            ha="right",
            va="bottom",
            style="italic",
            zorder=30,
            path_effects=[pe.withStroke(linewidth=2, foreground="white")],
        )

        # Camada de cidades e rosa dos ventos sobrevivem à reconstrução (tema/região)
        if self._cities_enabled:
            self._replot_cities_for_view()
        if self._north_arrow_enabled:
            self._draw_north_arrow()

        # Maximiza a carta na "mesa branca" (startup e troca de tema)
        self._reflow_layout()
        self.draw()

    def set_theme(self, theme_name: str) -> None:
        """Altera o tema de cores do mapa e redesenha."""
        if theme_name in MAP_THEMES:
            self.current_theme = theme_name
            self._setup_base_map()

    def set_context_emphasis(self, enabled: bool) -> None:
        """Liga/desliga o realce dos contornos de contexto (costa/países/estados).

        Realce = linhas mais grossas na cor forte do tema + halo de contraste
        por baixo (desenho duplo). Mantém o previsor localizado sobre satélite
        e campos preenchidos, onde as linhas finas do mapa base desaparecem.
        """
        if enabled == self._context_emphasis:
            return
        self._context_emphasis = enabled
        self._apply_context_emphasis(enabled)
        self.draw_idle()

    def _apply_context_emphasis(self, enabled: bool) -> None:
        """Aplica/remove o estilo de realce nos artistas de contexto vivos."""
        theme = MAP_THEMES.get(self.current_theme, MAP_THEMES["Clássico"])
        for name, artist in self._context_artists.items():
            normal_lw, strong_lw = CONTEXT_LINEWIDTHS[name]
            artist.set_linewidth(strong_lw if enabled else normal_lw)
            artist.set_edgecolor(theme["emphasis_line"] if enabled else theme[name])
        for halo in self._context_halo_artists.values():
            halo.set_visible(enabled)

    def set_cities_visible(self, enabled: bool) -> None:
        """Liga/desliga a camada de cidades (sedes municipais IBGE com nome)."""
        self._cities_enabled = enabled
        if enabled:
            self._replot_cities_for_view()
        else:
            self._clear_cities()
        self.draw_idle()

    def set_north_arrow_visible(self, enabled: bool) -> None:
        """Liga/desliga a rosa dos ventos (norte geográfico) no canto superior direito."""
        self._north_arrow_enabled = enabled
        if enabled:
            self._draw_north_arrow()
        else:
            self._clear_north_arrow()
        self.draw_idle()

    def _clear_north_arrow(self) -> None:
        for artist in self._north_arrow_artists:
            with contextlib.suppress(Exception):
                artist.remove()
        self._north_arrow_artists.clear()

    def _draw_north_arrow(self) -> None:
        """Triângulo preto + "N" ancorados em coordenadas do eixo (transAxes).

        O triângulo é um MARKER de Line2D (renderizado em pontos): não estica
        com o aspecto do extent e evita patches de seta/annotate(arrowprops=...),
        proibidos em GeoAxes pela doutrina dos códigos poligonais. O "N" fica
        um deslocamento FIXO EM PONTOS abaixo da âncora (offset_copy) pelo
        mesmo motivo. Em PlateCarree sem rotação o norte é sempre "para cima".
        """
        self._clear_north_arrow()
        anchor_x, anchor_y = 0.965, 0.952
        (tri,) = self.ax.plot(
            [anchor_x],
            [anchor_y],
            marker="^",
            markersize=13,
            markerfacecolor="#1A1A1A",
            markeredgecolor="white",
            markeredgewidth=1.2,
            linestyle="none",
            transform=self.ax.transAxes,
            zorder=30,
            clip_on=False,
        )
        txt = self.ax.text(
            anchor_x,
            anchor_y,
            "N",
            transform=mtransforms.offset_copy(
                self.ax.transAxes, fig=self.fig, x=0, y=-9, units="points"
            ),
            ha="center",
            va="top",
            fontsize=11,
            fontweight="bold",
            color="#1A1A1A",
            path_effects=[pe.withStroke(linewidth=2.5, foreground="white")],
            zorder=30,
        )
        self._north_arrow_artists.extend([tri, txt])

    def set_city_density(self, factor: float) -> None:
        """Ajusta a densidade da camada de cidades e replota na hora (sem rede).

        `factor` segue `CITY_DENSITY_FACTORS` (maior → mais rótulos): multiplica
        o teto de cidades e reduz a separação mínima entre rótulos.
        """
        try:
            factor = float(factor)
        except (TypeError, ValueError):
            return
        if factor <= 0 or factor == self._city_density_factor:
            return
        self._city_density_factor = factor
        if self._cities_enabled:
            self._replot_cities_for_view()
            self.draw_idle()

    def _clear_cities(self) -> None:
        for artist in self._cities_artists:
            with contextlib.suppress(Exception):
                artist.remove()
        self._cities_artists.clear()

    def _replot_cities_for_view(self) -> None:
        """Replota as cidades para a VISTA real (``ax.get_extent``).

        Usa a vista, e não ``config.extent``, porque scroll/pan mudam só a
        visualização — a seleção (capital > população, separação mínima) deve
        acompanhar o que o usuário está vendo.
        """
        if not self._cities_enabled:
            return
        self._clear_cities()
        try:
            x0, x1, y0, y1 = self.ax.get_extent(crs=ccrs.PlateCarree())
        except Exception:
            return
        cities = select_cities(
            load_cities(), [x0, y0, x1, y1], density_factor=self._city_density_factor
        )
        label_dy = (y1 - y0) * 0.012
        halo = [pe.withStroke(linewidth=2.5, foreground="white")]
        for city in cities:
            (dot,) = self.ax.plot(
                city.lon,
                city.lat,
                "o",
                markersize=3.0,
                markerfacecolor="#B03A2E",
                markeredgecolor="white",
                markeredgewidth=0.7,
                transform=ccrs.PlateCarree(),
                zorder=21.5,
                clip_on=True,
            )
            txt = self.ax.text(
                city.lon,
                city.lat + label_dy,
                city.name,
                transform=ccrs.PlateCarree(),
                fontsize=8 if city.is_capital else 7,
                fontweight="bold" if city.is_capital else "normal",
                color="#212121",
                ha="center",
                va="bottom",
                zorder=21.5,
                clip_on=True,
                path_effects=halo,
            )
            self._cities_artists.extend([dot, txt])

    # ═══════════════════════════════════════════════════════════════════════
    #  CAMPOS SINÓTICOS (PNMM, Espessura, Centros H/L)
    # ═══════════════════════════════════════════════════════════════════════

    def set_synoptic_data(self, data: object) -> None:
        """Define dados sinóticos e plota — PRESERVA simbologias desenhadas."""
        self.synoptic_data = data
        self._centers_persistence = {}  # ranking topológico é por rodada/step
        self._clear_synoptic_artists()
        self._plot_synoptic_fields()
        # O título (1-2 linhas) acabou de mudar — a mesa precisa re-reservar o
        # topo, senão ele vaza para fora da figura.
        self._reflow_layout()
        self.draw()

    def _clear_synoptic_artists(self) -> None:
        """Remove APENAS os artists de camadas sinóticas."""
        for layer_name, artists in self._synoptic_artists.items():
            for artist in artists:
                with contextlib.suppress(ValueError, AttributeError, NotImplementedError):
                    artist.remove()
            self._synoptic_artists[layer_name] = []
        self.ax.set_title("", loc="left")

    def _clear_single_layer(self, layer_name: str) -> None:
        """Remove artists de UMA camada sinótica específica."""
        if layer_name in self._synoptic_artists:
            for artist in self._synoptic_artists[layer_name]:
                with contextlib.suppress(ValueError, AttributeError, NotImplementedError):
                    artist.remove()
            self._synoptic_artists[layer_name] = []

    def toggle_layer(self, layer_name: str, visible: bool) -> None:
        """Liga/desliga uma camada sinótica individual."""
        if not self.synoptic_data:
            return

        self.plot_options[layer_name] = visible
        self._clear_single_layer(layer_name)

        if visible:
            self._plot_single_layer(layer_name)

        self._update_synoptic_title()
        self.draw()

    def _plot_single_layer(self, layer_name: str) -> None:
        """Plota UMA camada sinótica específica."""
        if self.synoptic_data is None:
            return

        data = self.synoptic_data

        if layer_name == "thickness":
            self._plot_thickness_layer(data)
        elif layer_name == "pnmm":
            self._plot_pnmm_layer(data)
        elif layer_name == "centers":
            self._plot_centers_layer(data)

    def _plot_thickness_layer(self, data: object) -> None:
        """Plota camada de espessura 1000-500 hPa."""
        thickness_levels = np.arange(
            LEVELS["thickness"]["min"], LEVELS["thickness"]["max"], LEVELS["thickness"]["step"]
        )
        thickness_no_5400 = thickness_levels[thickness_levels != 5400]

        cs = self.ax.contour(
            data.lons,
            data.lats,
            data.thickness,
            levels=thickness_no_5400,
            colors=[
                COLORS["thickness_cold"] if lv < 5400 else COLORS["thickness_warm"]
                for lv in thickness_no_5400
            ],
            linestyles="dashed",
            linewidths=0.8,
            transform=ccrs.PlateCarree(),
            zorder=3,
        )
        self._synoptic_artists["thickness"].append(cs)

        clabels = self.ax.clabel(cs, inline=True, fontsize=8, fmt="%1.0f")
        for txt in clabels:
            txt.set_path_effects([pe.withStroke(linewidth=2, foreground="white")])
            self._synoptic_artists["thickness"].append(txt)

        cs_5400 = self.ax.contour(
            data.lons,
            data.lats,
            data.thickness,
            levels=[5400],
            colors=COLORS["thickness_5400"],
            linestyles="solid",
            linewidths=2.5,
            transform=ccrs.PlateCarree(),
            zorder=4,
        )
        self._synoptic_artists["thickness"].append(cs_5400)

        clabels_5400 = self.ax.clabel(cs_5400, inline=True, fontsize=9, fmt="%1.0f")
        for txt in clabels_5400:
            txt.set_path_effects([pe.withStroke(linewidth=3, foreground="white")])
            self._synoptic_artists["thickness"].append(txt)

    def _plot_pnmm_layer(self, data: object) -> None:
        """Plota camada de PNMM."""
        pnmm_levels = np.arange(
            LEVELS["pnmm"]["min"], LEVELS["pnmm"]["max"], LEVELS["pnmm"]["step"]
        )

        cs_pnmm = self.ax.contour(
            data.lons,
            data.lats,
            data.pnmm,
            levels=pnmm_levels,
            colors=COLORS["pnmm_contour"],
            linewidths=1.0,
            transform=ccrs.PlateCarree(),
            zorder=6,
        )
        self._synoptic_artists["pnmm"].append(cs_pnmm)

        clabels_pnmm = self.ax.clabel(cs_pnmm, inline=True, fontsize=9, fmt="%1.0f")
        for txt in clabels_pnmm:
            txt.set_path_effects([pe.withStroke(linewidth=2.5, foreground="white")])
            self._synoptic_artists["pnmm"].append(txt)

    def _plot_centers_layer(self, data: object) -> None:
        """Plota camada de centros H/L."""
        existing_texts = {id(t) for t in self.ax.texts}

        # Máscara orográfica (opcional): veta centros sobre terreno elevado,
        # onde a PNMM é extrapolação (Andes/Altiplano). None = sem filtro.
        exclude = None
        if self.plot_options.get("centers_terrain_filter", True):
            exclude = getattr(data, "highland_mask", None)

        # Ranking topológico cacheado por rodada/step: o campo é o mesmo em
        # todos os replots por extent (zoom/reset) — recalcular custa ~1,2 s.
        if not self._centers_persistence:
            self._centers_persistence = compute_persistence_maps(data.pnmm)

        plot_maxmin_points(
            self.ax,
            data.lon2d,
            data.lat2d,
            data.pnmm,
            extrema="max",
            nsize=80,
            symbol="H",
            color=COLORS["high_pressure"],
            min_distance=25,
            threshold=1018,
            max_points=8,
            exclude_mask=exclude,
            persistence_map=self._centers_persistence["max"],
        )
        plot_maxmin_points(
            self.ax,
            data.lon2d,
            data.lat2d,
            data.pnmm,
            extrema="min",
            nsize=60,
            symbol="L",
            color=COLORS["low_pressure"],
            min_distance=20,
            threshold=1008,
            max_points=10,
            exclude_mask=exclude,
            persistence_map=self._centers_persistence["min"],
        )

        for txt in self.ax.texts:
            if id(txt) not in existing_texts:
                self._synoptic_artists["centers"].append(txt)

    def set_centers_terrain_filter(self, enabled: bool) -> None:
        """Liga/desliga o filtro orográfico dos centros H/L (re-render do cache)."""
        self.plot_options["centers_terrain_filter"] = bool(enabled)
        if self.synoptic_data is None or not self.plot_options.get("centers", True):
            return
        self._clear_single_layer("centers")
        self._plot_single_layer("centers")
        self.draw()

    def _update_synoptic_title(self) -> None:
        """Atalho legado — redireciona para o título dinâmico."""
        self._update_map_title()

    def _active_obs_labels(self) -> list[str]:
        """Rótulos das camadas de observação atualmente plotadas (SYNOP/METAR)."""
        labels = []
        if self._station_artists.get("synop"):
            labels.append("SYNOP")
        if self._station_artists.get("metar"):
            labels.append("METAR")
        return labels

    def _update_map_title(self) -> None:
        """Título dinâmico: reflete a camada visível de maior prioridade.

        Regra de composição:
        1. Identifica a camada PL/superfície visível mais recente.
        2. Se a base sinótica (PNMM) estiver ativa, acrescenta '+ PNMM (hPa)'.
        3. Se nenhuma camada PL visível, mostra só título sinótico ou vazio.
        4. Linha 2: informações cronológicas padronizadas.
        """
        # ── Detecta base sinótica ativa ──
        has_synoptic = self.synoptic_data is not None and self.plot_options.get("pnmm", True)

        # ── Descobre a camada PL visível mais recente ──
        top_data = None
        for lid in reversed(list(self._pl_data)):
            # Camada existe e tem artists renderizados → visível
            if lid in self._pl_artists and self._pl_artists[lid]:
                top_data = self._pl_data[lid]
                break

        # ── Monta Linha 1: descrição dos campos ──
        if top_data is not None:
            var_info = VARIABLE_REGISTRY.get(top_data.variable, {})
            nome = var_info.get("nome", top_data.variable)
            unit = top_data.unit or var_info.get("unit_display", "")

            if top_data.level and top_data.level > 0:
                field_desc = f"{nome} {top_data.level} hPa"
            else:
                field_desc = nome

            if unit:
                field_desc += f" ({unit})"

            if has_synoptic:
                line1 = f"ECMWF IFS — {field_desc} + PNMM (hPa)"
            else:
                line1 = f"ECMWF IFS — {field_desc}"

            # Tempo: prefere metadados da camada PL
            ref_data = top_data

        elif has_synoptic:
            # Só a base sinótica
            parts = []
            if self.plot_options.get("pnmm", True):
                parts.append("PNMM (hPa)")
            if self.plot_options.get("thickness", True):
                parts.append("Espessura 1000-500 hPa (m)")
            line1 = f"ECMWF IFS — {' + '.join(parts)}" if parts else "ECMWF IFS"

            ref_data = self.synoptic_data

        else:
            # ── Verifica se há TSM visível como camada independente ──
            has_sst = (
                self._sst_data is not None
                and self._sst_artist is not None
                and self._sst_artist.get_visible()
            )
            if has_sst:
                line1 = "TSM (°C) — MUR SST 1km (NASA/NOAA)"
                obs_labels = self._active_obs_labels()
                if obs_labels:
                    line1 += " + " + "/".join(obs_labels)
                line2 = f"Data: {self._sst_data.time_str}"
                self.ax.set_title(
                    f"{line1}\n{line2}",
                    fontsize=11,
                    fontweight="bold",
                    loc="left",
                    pad=14,
                )
                return

            # ── Apenas observações (sem modelo/TSM) ──
            obs_labels = self._active_obs_labels()
            if obs_labels:
                self.ax.set_title(
                    "Observações de superfície — " + " + ".join(obs_labels),
                    fontsize=11,
                    fontweight="bold",
                    loc="left",
                    pad=14,
                )
                return

            # Nenhum dado carregado
            self.ax.set_title("", loc="left")
            return

        # ── Complemento TSM se ativa junto com outras camadas ──
        has_sst = (
            self._sst_data is not None
            and self._sst_artist is not None
            and self._sst_artist.get_visible()
        )
        if has_sst:
            line1 += " + TSM"

        # ── Observações de superfície ativas ──
        obs_labels = self._active_obs_labels()
        if obs_labels:
            line1 += " + " + "/".join(obs_labels)

        # ── Monta Linha 2: informações cronológicas padronizadas ──
        rodada = f"Rodada: {ref_data.base_time}" if ref_data.base_time else ""
        step_txt = f"Step: +{ref_data.step}h"
        valido = f"Válido: {ref_data.valid_time} UTC" if ref_data.valid_time else ""

        chrono_parts = [p for p in (rodada, step_txt, valido) if p]
        line2 = " | ".join(chrono_parts)

        self.ax.set_title(
            f"{line1}\n{line2}",
            fontsize=11,
            fontweight="bold",
            loc="left",
            pad=14,
        )

    def _plot_synoptic_fields(self) -> None:
        """Plota campos meteorológicos respeitando o estado dos toggles."""
        if self.synoptic_data is None:
            return

        opts = self.plot_options

        if opts.get("thickness", True):
            self._plot_thickness_layer(self.synoptic_data)

        if opts.get("pnmm", True):
            self._plot_pnmm_layer(self.synoptic_data)

        if opts.get("centers", True):
            self._plot_centers_layer(self.synoptic_data)

        self._update_synoptic_title()
        # SEM self.draw() aqui: os dois chamadores (set_synoptic_data e
        # apply_extent) reflowam a mesa e desenham na sequência — um render
        # pré-reflow seria pago e imediatamente repintado.

    # ═══════════════════════════════════════════════════════════════════════
    #  DESENHO INTERATIVO
    # ═══════════════════════════════════════════════════════════════════════

    def set_symbol(self, key: str) -> None:
        self.current_symbol = key

    def set_flip(self, flip: bool) -> None:
        self.flip = flip
        self._update_preview()

    def set_zcit_intensity(self, intensity: int) -> None:
        """Define a intensidade do ZCIT (1=Fraca, 2=Moderada, 3=Forte)."""
        self.zcit_intensity = max(1, min(3, int(intensity)))
        self._update_preview()

    def set_drawing_mode(self, enabled: bool) -> None:
        if enabled:
            self.interaction_mode = "draw"
        elif self.interaction_mode == "draw":
            self.interaction_mode = None

    def set_annotation_mode(self, enabled: bool) -> None:
        if enabled:
            self.interaction_mode = "annotate"
        elif self.interaction_mode == "annotate":
            self.interaction_mode = None

    def set_ruler_mode(self, enabled: bool) -> None:
        if enabled:
            self.interaction_mode = "ruler"
            self._clear_ruler()
        elif self.interaction_mode == "ruler":
            self.interaction_mode = None
            self._clear_ruler()

    def set_emoji_mode(self, enabled: bool) -> None:
        if enabled:
            self.interaction_mode = "emoji"
        elif self.interaction_mode == "emoji":
            self.interaction_mode = None

    # ─── Caneta (traço livre) e Formas customizáveis ─────────────────────────

    def set_pen_mode(self, enabled: bool) -> None:
        """Modo 'caneta' — pressionar e arrastar desenha um traço livre."""
        if enabled:
            self.interaction_mode = "pen"
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        elif self.interaction_mode == "pen":
            self.interaction_mode = None
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        if not enabled:
            self.cancel_active_draft()

    def set_shape_mode(self, enabled: bool) -> None:
        """Modo 'formas' — arrastar insere a forma atual; polígono é por cliques."""
        if enabled:
            self.interaction_mode = "shape"
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        elif self.interaction_mode == "shape":
            self.interaction_mode = None
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        if not enabled:
            self.cancel_active_draft()

    def set_shape_tool(self, tool: str) -> None:
        """Troca a ferramenta de forma atual (cancela rascunho em andamento)."""
        self.cancel_active_draft()
        self.shape_tool = tool

    def set_pen_style(self, style: dict) -> None:
        self.pen_style = DrawStyle.from_dict(style)

    def set_shape_style(self, style: dict) -> None:
        self.shape_style = DrawStyle.from_dict(style)

    def cancel_active_draft(self) -> None:
        """Cancela qualquer rascunho de caneta/forma (preview + estado)."""
        had_draft = (
            self._draft_preview is not None
            or self._pen_active
            or self._shape_anchor is not None
            or bool(self._shape_draft_x)
        )
        if self._draft_preview is not None:
            with contextlib.suppress(ValueError, AttributeError):
                self._draft_preview.remove()
            self._draft_preview = None
        self._pen_active = False
        self._pen_draft_x.clear()
        self._pen_draft_y.clear()
        self._pen_last_px = None
        self._shape_anchor = None
        self._shape_anchor_px = None
        self._shape_drag_current = None
        self._shape_last_px = None
        if self._shape_draft_x:
            self._shape_draft_x.clear()
            self._shape_draft_y.clear()
            self.shape_draft_changed.emit(0)
        if had_draft:
            self.draw_idle()

    def set_sounding_mode(self, enabled: bool) -> None:
        """Modo 'Sonda Vertical' — o clique ancora na estação RAOB mais próxima."""
        if enabled:
            self.interaction_mode = "vertical_sounding"
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        elif self.interaction_mode == "vertical_sounding":
            self.interaction_mode = None
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def set_meteogram_mode(self, enabled: bool) -> None:
        """Modo 'Meteograma' (F6) — o clique dispara a série temporal do IFS no ponto."""
        if enabled:
            self.interaction_mode = "meteogram"
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        elif self.interaction_mode == "meteogram":
            self.interaction_mode = None
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def set_cross_section_mode(self, enabled: bool) -> None:
        """Modo 'Corte Vertical' (F4) — dois cliques (A→B) definem a reta do corte."""
        if enabled:
            self.interaction_mode = "cross_section"
            self._xsec_anchor = None
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        elif self.interaction_mode == "cross_section":
            self.interaction_mode = None
            self._clear_xsec_overlay()
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    # ═══════════════════════════════════════════════════════════════════════
    #  ZOOM / NAVEGAÇÃO
    # ═══════════════════════════════════════════════════════════════════════

    def set_pan_mode(self, enabled: bool) -> None:
        """Modo 'mover' — arrastar com o botão esquerdo desloca o mapa."""
        if enabled:
            self.interaction_mode = "pan"
            self._enable_rect_selector(False)
        elif self.interaction_mode == "pan":
            self.interaction_mode = None
        self._pan_active = False
        self._pan_start = None

    def set_zoom_area_mode(self, enabled: bool) -> None:
        """Modo 'zoom área' — desenhar um retângulo recorta e replota a carta."""
        if enabled:
            self.interaction_mode = "zoom_area"
            self._enable_rect_selector(True)
        else:
            if self.interaction_mode == "zoom_area":
                self.interaction_mode = None
            self._enable_rect_selector(False)

    def _enable_rect_selector(self, enabled: bool) -> None:
        """Cria (uma vez) e ativa/desativa o RectangleSelector de zoom-área."""
        if enabled:
            if self._rect_selector is None:
                from matplotlib.widgets import RectangleSelector

                self._rect_selector = RectangleSelector(
                    self.ax,
                    self._on_rect_select,
                    useblit=False,
                    button=[1],
                    minspanx=1,
                    minspany=1,
                    spancoords="data",
                    interactive=False,
                    props={
                        "facecolor": "none",
                        "edgecolor": "#E74C3C",
                        "linewidth": 1.5,
                        "linestyle": "--",
                        "zorder": 60,
                    },
                )
            self._rect_selector.set_active(True)
        elif self._rect_selector is not None:
            self._rect_selector.set_active(False)

    def wheelEvent(self, event) -> None:
        """Ctrl+roda = zoom da FIGURA (documento); roda pura = zoom GEOGRÁFICO.

        Com Ctrl, emite ``figure_zoom_requested`` e NÃO repassa ao matplotlib —
        assim o MainWindow amplia a "mesa branca" inteira sem mexer no extent.
        Sem Ctrl, delega ao comportamento padrão (→ scroll_event → _on_scroll).
        """
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta != 0:
                self.figure_zoom_requested.emit(1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def _on_scroll(self, event: object) -> None:
        """Zoom in/out centrado no cursor (apenas visualização, não rebaixa dados)."""
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            return
        scale = 0.8 if getattr(event, "button", "up") == "up" else 1.25
        x0, x1, y0, y1 = self.ax.get_extent(crs=ccrs.PlateCarree())
        cx, cy = event.xdata, event.ydata
        new = [
            cx - (cx - x0) * scale,
            cy - (cy - y0) * scale,
            cx + (x1 - cx) * scale,
            cy + (y1 - cy) * scale,
        ]
        new = self._clamp_extent(new)
        try:
            self.ax.set_extent([new[0], new[2], new[1], new[3]], crs=ccrs.PlateCarree())
            self.draw()
        except Exception:
            return
        self._view_settle_timer.start()

    def _on_release(self, event: object) -> None:
        """Fim do pan, do traço de caneta ou do arraste de forma."""
        if self._pan_active:
            self._pan_active = False
            self._pan_start = None
        if self._pen_active:
            self._end_pen_stroke()
        if self._shape_anchor is not None:
            self._end_shape_drag(event.x, event.y)

    def _pan_to(self, event: object) -> None:
        """Desloca o mapa conforme o arraste (modo pan)."""
        if not self._pan_active or self._pan_start is None:
            return
        if event.xdata is None or event.ydata is None:
            return
        dx = self._pan_start[0] - event.xdata
        dy = self._pan_start[1] - event.ydata
        x0, x1, y0, y1 = self.ax.get_extent(crs=ccrs.PlateCarree())
        new = self._clamp_extent([x0 + dx, y0 + dy, x1 + dx, y1 + dy])
        try:
            self.ax.set_extent([new[0], new[2], new[1], new[3]], crs=ccrs.PlateCarree())
            self.draw()
        except Exception:
            return
        self._view_settle_timer.start()

    def _deferred_view_settle(self) -> None:
        """Assenta a vista no repouso do scroll/pan: reflow (título) + cidades.

        Deliberadamente NÃO toca em ``config.extent`` nem emite
        ``extent_changed`` — scroll/pan continuam sendo só visualização
        (mudar isso dispararia re-thinning de observações e sync de spinboxes
        a cada gesto).
        """
        if self._layout_suspended or self._draw_suspended:
            return
        self._reflow_layout()
        self._replot_cities_for_view()
        self.draw_idle()

    @staticmethod
    def _clamp_extent(extent: list[float]) -> list[float]:
        """Mantém o extent dentro de limites geográficos válidos."""
        lon_min, lat_min, lon_max, lat_max = extent
        lon_min = max(-180.0, min(lon_min, 179.0))
        lon_max = min(180.0, max(lon_max, lon_min + 1.0))
        lat_min = max(-90.0, min(lat_min, 89.0))
        lat_max = min(90.0, max(lat_max, lat_min + 1.0))
        return [lon_min, lat_min, lon_max, lat_max]

    def _on_rect_select(self, eclick: object, erelease: object) -> None:
        """Callback do RectangleSelector: recorta e replota para a área escolhida."""
        x0, x1 = sorted([eclick.xdata, erelease.xdata])
        y0, y1 = sorted([eclick.ydata, erelease.ydata])
        if None in (x0, x1, y0, y1) or (x1 - x0) < 0.5 or (y1 - y0) < 0.5:
            return
        new_extent = self._clamp_extent([x0, y0, x1, y1])
        self.apply_extent(new_extent, push_history=True)

    def apply_extent(self, extent: list[float], push_history: bool = True) -> None:
        """Aplica um novo extent: atualiza config, replota a sinótica e avisa a GUI.

        ``extent`` na ordem do Config: ``[lon_min, lat_min, lon_max, lat_max]`` —
        NÃO a ordem ``[x0, x1, y0, y1]`` do ``set_extent`` do Cartopy (a conversão
        é feita aqui). O extent é clampado antes de aplicar: um intervalo
        degenerado (lat_min == lat_max) tornaria a transformação singular e
        esmagaria a carta silenciosamente.

        Após o `set_extent` (que muda a proporção geométrica do GeoAxes), o layout
        é recalculado para reposicionar as barras de cores e as margens — sem isso,
        ao resetar de um domínio quadrado (ex.: LOCZCIT-PA) para a AmSul o eixo fica
        "esmagado" no canto com colorbars descentralizadas.
        """
        extent = self._clamp_extent(list(extent))
        if push_history:
            self._extent_history.append(list(self.config.extent))
        self.config.extent = list(extent)
        self.ax.set_extent([extent[0], extent[2], extent[1], extent[3]], crs=ccrs.PlateCarree())
        # Recalcula centros H/L para o novo domínio (a partir dos dados já carregados)
        if self.synoptic_data is not None:
            self._clear_synoptic_artists()
            self._plot_synoptic_fields()
        # Re-seleciona as cidades para o novo domínio (thinning por vista)
        self._replot_cities_for_view()
        # Realinha o layout (colorbars/margens) para a nova proporção e centraliza
        self._reflow_layout()
        self.draw()
        self.extent_changed.emit(list(extent))

    # ─── Motor determinístico da "mesa branca" ────────────────────────────
    # Margens em fração da figura. O tight_layout foi APOSENTADO aqui: ele
    # ignora eixos de colorbar criados por fig.colorbar (não são subplots) —
    # um reset com ZCIT+TSM na tela movia só a carta e deixava as colorbars
    # órfãs, com um vão branco no meio da mesa.
    _MESA_LEFT = 0.055  # rótulos do gridliner ("100°W")
    _MESA_BOTTOM = 0.055
    _MESA_RIGHT = 0.012
    _MESA_TOP_MIN = 0.03
    _MESA_VCB_W = 0.018  # largura da barra vertical
    _MESA_VCB_GAP = 0.012  # vão carta → barra
    _MESA_VCB_LABELS = {"loczcit": 0.085, "blocking": 0.055}  # ticks + rótulo
    _MESA_HCB_H = 0.026  # altura da barra horizontal (TSM)
    _MESA_HCB_RESERVE = 0.105  # reserva total inferior (barra+ticks+rótulo)
    _MESA_PL_RESERVE = 0.055  # colorbars inset dos campos PL (x=1.02 + rótulos)

    def draw(self) -> None:
        """Render completo; vira no-op dentro de ``batch_layout()`` (lote)."""
        if self._draw_suspended:
            return
        super().draw()

    @contextlib.contextmanager
    def batch_layout(self):
        """Suspende o motor da mesa e os draws durante operações em LOTE.

        Cada add/remove de camada pagaria reflow + um render completo da pilha;
        num lote (ex.: restauração pós-animação) isso multiplica renders
        idênticos. O chamador faz UM reflow+draw ao sair. Guarda/restaura os
        flags (o controller da animação também usa ``_layout_suspended``).
        """
        prev_layout = self._layout_suspended
        prev_draw = self._draw_suspended
        self._layout_suspended = True
        self._draw_suspended = True
        try:
            yield
        finally:
            self._layout_suspended = prev_layout
            self._draw_suspended = prev_draw

    def _reflow_layout(self) -> None:
        """Motor determinístico da mesa: posiciona carta e colorbars por construção.

        Margens fixas para os rótulos do gridliner, topo medido pelo título
        real (1–2 linhas), colorbars ancoradas à caixa REAL da carta (após o
        apply_aspect do Cartopy) e centralização horizontal do conjunto.
        ``_fit_layout_to_figure`` fecha como rede de segurança (mede as
        tightbboxes verdadeiras e corrige vazamentos residuais).
        """
        if getattr(self, "_layout_suspended", False):
            return  # animação: geometria congelada é imposta pelo controller
        try:
            self._layout_mesa()
        except Exception as e:
            logger.debug("Aviso no layout da mesa: %s", e)
        self._fit_layout_to_figure()

    def _layout_mesa(self) -> None:
        fig = self.fig

        # 1) Margem superior: mede o título real (pode ter 1 ou 2 linhas)
        top_reserve = self._MESA_TOP_MIN
        try:
            title = self.ax.title
            if title.get_text():
                bb = title.get_window_extent(fig.canvas.get_renderer())
                top_reserve += bb.height / fig.bbox.height + 0.012
        except Exception:
            top_reserve = 0.09  # fallback: 2 linhas típicas

        # 2) Reservas pelas colorbars ativas
        vcbs = []
        if self._loczcit_colorbar is not None and self._loczcit_colorbar.ax.get_visible():
            vcbs.append((self._loczcit_colorbar, self._MESA_VCB_LABELS["loczcit"]))
        if self._blocking_colorbar is not None and self._blocking_colorbar.ax.get_visible():
            vcbs.append((self._blocking_colorbar, self._MESA_VCB_LABELS["blocking"]))
        pl_reserve = self._MESA_PL_RESERVE if self._pl_colorbars else 0.0
        right_reserve = (
            self._MESA_RIGHT
            + pl_reserve
            + sum(self._MESA_VCB_GAP + self._MESA_VCB_W + lbl for _, lbl in vcbs)
        )
        hcb = None
        if self._sst_colorbar is not None and self._sst_colorbar.ax.get_visible():
            hcb = self._sst_colorbar
        bottom_reserve = self._MESA_BOTTOM + (self._MESA_HCB_RESERVE if hcb else 0.0)

        # 3) Caixa disponível para a carta (o apply_aspect letterboxa dentro dela)
        self.ax.set_position(
            [
                self._MESA_LEFT,
                bottom_reserve,
                max(0.1, 1.0 - self._MESA_LEFT - right_reserve),
                max(0.1, 1.0 - bottom_reserve - top_reserve),
            ]
        )
        # Materializa o apply_aspect SEM renderizar (mesmo passo que o Cartopy
        # roda em GeoAxes._draw_preprocess). Um canvas.draw() aqui custava um
        # render completo da pilha inteira a cada operação de camada.
        self.ax.apply_aspect()
        pos = self.ax.get_position()

        # 4) Ancora as colorbars na caixa real da carta.
        # set_axes_locator(None): o _ColorbarAxesLocator do matplotlib re-aplica
        # o shrink/aspect DE CRIAÇÃO a cada draw, desfazendo a ancoragem — o
        # motor da mesa assume a posição em definitivo.
        x = pos.x1 + pl_reserve
        for cbar, lbl in vcbs:
            x += self._MESA_VCB_GAP
            cbar.ax.set_axes_locator(None)
            cbar.ax.set_aspect("auto")  # a caixa manda na geometria
            cbar.ax.set_position([x, pos.y0, self._MESA_VCB_W, pos.height])
            x += self._MESA_VCB_W + lbl
        if hcb is not None:
            hcb.ax.set_axes_locator(None)
            hcb.ax.set_aspect("auto")
            hcb.ax.set_position(
                [
                    pos.x0 + 0.2 * pos.width,
                    max(0.04, pos.y0 - self._MESA_HCB_RESERVE + 0.02),
                    0.6 * pos.width,
                    self._MESA_HCB_H,
                ]
            )

        # 5) Centraliza o conjunto (carta + barras verticais) na mesa
        right_edge = x if vcbs else pos.x1 + pl_reserve
        left_edge = pos.x0 - self._MESA_LEFT
        dx = (1.0 - (right_edge - left_edge)) / 2.0 - left_edge
        if abs(dx) >= 1e-3:
            for a in fig.axes:
                p = a.get_position()
                a.set_position([p.x0 + dx, p.y0, p.width, p.height])

    def _fit_layout_to_figure(self, pad: float = 0.008) -> None:
        """Impede que rótulos e colorbars vazem da figura (a "mesa branca").

        A recentragem acima usa as CAIXAS dos eixos, mas os rótulos (ex.: os
        textos "Forte/Moderada/…" da colorbar do LOCZCIT) vivem FORA delas —
        em extents panorâmicos a colorbar saía cortada na borda direita, na
        tela e nos quadros da animação. Aqui mede-se a união das tightbboxes
        (caixas + rótulos): se algo vaza, desloca-se o conjunto; se a união é
        mais larga que a figura, encolhe-se tudo em torno do centro e
        desloca-se de novo. No-op quando tudo já cabe — os layouts que hoje
        estão bons não mudam.
        """
        try:
            # A medição NÃO exige render: GeoAxes.get_tightbbox roda o
            # _draw_preprocess (apply_aspect), o Gridliner do Cartopy regenera
            # os rótulos dentro do próprio get_tightbbox, e textos/coleções
            # medem pelos transforms vivos (set_position propaga na hora).
            # Antes havia um canvas.draw() por passada — um render completo da
            # pilha inteira pago a cada operação de camada.
            for _ in range(2):  # 2ª passada: rótulos têm fonte fixa após o encolhimento
                renderer = self.fig.canvas.get_renderer()
                inv = self.fig.transFigure.inverted()
                boxes = []
                for a in self.fig.axes:
                    if not a.get_visible():
                        continue
                    tight = a.get_tightbbox(renderer)
                    if tight is not None and tight.width > 0:
                        boxes.append(tight.transformed(inv))
                if not boxes:
                    return
                x0 = min(b.x0 for b in boxes)
                x1 = max(b.x1 for b in boxes)
                y0 = min(b.y0 for b in boxes)
                y1 = max(b.y1 for b in boxes)
                x_ok = x0 >= pad - 1e-6 and x1 <= (1.0 - pad) + 1e-6
                y_ok = y0 >= pad - 1e-6 and y1 <= (1.0 - pad) + 1e-6
                if x_ok and y_ok:
                    return  # tudo dentro da mesa — não mexe
                avail = 1.0 - 2.0 * pad
                # Escala uniforme (preserva o aspecto do conjunto); X centraliza,
                # Y apenas EMPURRA para dentro (o título deve seguir no topo,
                # não flutuar centralizado no meio da mesa).
                scale = min(1.0, avail / max(x1 - x0, 1e-9), avail / max(y1 - y0, 1e-9))
                cx_u = (x0 + x1) / 2.0
                cy_u = (y0 + y1) / 2.0
                new_h_u = (y1 - y0) * scale
                new_cy_u = min(max(cy_u, pad + new_h_u / 2.0), 1.0 - pad - new_h_u / 2.0)
                for a in self.fig.axes:
                    p = a.get_position()
                    cx = p.x0 + p.width / 2.0
                    cy = p.y0 + p.height / 2.0
                    new_w = p.width * scale
                    new_h = p.height * scale
                    new_cx = 0.5 + (cx - cx_u) * scale  # escala em torno da união + centraliza
                    new_cy = new_cy_u + (cy - cy_u) * scale
                    a.set_position([new_cx - new_w / 2.0, new_cy - new_h / 2.0, new_w, new_h])
        except Exception as e:
            logger.debug("Aviso ao ajustar o layout à figura: %s", e)

    def previous_extent(self) -> None:
        """Volta ao extent anterior (pilha simples — substitui o Ctrl+Z reservado)."""
        if not self._extent_history:
            return
        prev = self._extent_history.pop()
        self.apply_extent(prev, push_history=False)

    def cancel_rectangle(self) -> None:
        """Cancela retângulo de zoom-área em andamento (Esc)."""
        if self._rect_selector is not None and self.interaction_mode == "zoom_area":
            try:
                self._rect_selector.set_visible(False)
                self.draw()
            except Exception:
                pass

    @property
    def drawing_mode(self):
        return self.interaction_mode == "draw"

    @drawing_mode.setter
    def drawing_mode(self, val):
        if val:
            self.interaction_mode = "draw"
        elif self.interaction_mode == "draw":
            self.interaction_mode = None

    def _on_click(self, event: object) -> None:
        if event.inaxes != self.ax or event.button != 1:
            return

        if self.interaction_mode == "draw":
            modo = MODOS[self.current_symbol]
            if modo.get("ponto", False):
                self._place_point_symbol(event.xdata, event.ydata)
            else:
                self.points_x.append(event.xdata)
                self.points_y.append(event.ydata)
                self.point_added.emit(event.xdata, event.ydata)
                self._update_preview()

        elif self.interaction_mode == "annotate":
            self._request_annotation(event.xdata, event.ydata)

        elif self.interaction_mode == "ruler":
            self._on_ruler_click(event.xdata, event.ydata)

        elif self.interaction_mode == "emoji":
            self.add_emoji(event.xdata, event.ydata, self.current_emoji, self._emoji_fontsize)

        elif self.interaction_mode == "pen":
            self._begin_pen_stroke(event.xdata, event.ydata, event.x, event.y)

        elif self.interaction_mode == "shape":
            if self.shape_tool == "polygon":
                if getattr(event, "dblclick", False):
                    self.finalize_shape()
                else:
                    self._add_polygon_vertex(event.xdata, event.ydata)
            else:
                self._begin_shape_drag(event.xdata, event.ydata, event.x, event.y)

        elif self.interaction_mode == "pan":
            self._pan_active = True
            self._pan_start = (event.xdata, event.ydata)

        elif self.interaction_mode == "vertical_sounding":
            self._on_sounding_click(event.xdata, event.ydata)

        elif self.interaction_mode == "meteogram":
            self._mark_sounding_point(event.xdata, event.ydata, color="#117A65")
            self.meteogram_requested.emit(float(event.xdata), float(event.ydata))

        elif self.interaction_mode == "cross_section":
            self._on_xsec_click(event.xdata, event.ydata)

    def _on_xsec_click(self, lon: float, lat: float) -> None:
        """Captura A→B do corte vertical (F4): 1º clique = A, 2º clique = B → emite.

        Após o 2º clique a reta A→B é desenhada (Line2D, método sancionado em
        GeoAxes) e o sinal ``cross_section_requested`` dispara o worker. Iniciar
        um novo A (ou desligar o modo) descarta a reta/ponto anterior.
        """
        lon, lat = float(lon), float(lat)
        if self._xsec_anchor is None:
            self._clear_xsec_overlay()
            self._xsec_anchor = (lon, lat)
            (mk,) = self.ax.plot(
                lon,
                lat,
                marker="o",
                color="#8E44AD",
                markersize=8,
                markeredgecolor="white",
                markeredgewidth=1.2,
                transform=ccrs.PlateCarree(),
                zorder=27,
            )
            self._xsec_overlay.append(mk)
            self.draw()
            return

        lon_a, lat_a = self._xsec_anchor
        self._xsec_anchor = None
        (line,) = self.ax.plot(
            [lon_a, lon],
            [lat_a, lat],
            color="#8E44AD",
            linewidth=2.0,
            marker="o",
            markersize=6,
            markeredgecolor="white",
            markeredgewidth=1.0,
            transform=ccrs.PlateCarree(),
            zorder=27,
        )
        self._xsec_overlay.append(line)
        self.draw()
        self.cross_section_requested.emit(lon_a, lat_a, lon, lat)

    def _clear_xsec_overlay(self) -> None:
        """Remove a sobreposição A→B do corte vertical e zera o ponto pendente."""
        for art in self._xsec_overlay:
            with contextlib.suppress(ValueError, AttributeError):
                art.remove()
        self._xsec_overlay = []
        self._xsec_anchor = None
        self.draw()

    def _on_sounding_click(self, lon: float, lat: float) -> None:
        """Despacha o clique da Sonda Vertical conforme a fonte selecionada.

        "model" → pseudo-sondagem do IFS no ponto EXATO clicado (qualquer lugar,
        inclusive oceano). "observed" → ancora na estação RAOB mais próxima (Wyoming).
        """
        if self.sounding_source == "model":
            self._mark_sounding_point(float(lon), float(lat), color="#2E86C1")
            self.model_sounding_requested.emit(float(lon), float(lat))
            return

        from cartomet_br.data.raob_stations import nearest_raob

        station = nearest_raob(float(lon), float(lat))
        if station is None:
            return
        self._mark_sounding_point(station["lon"], station["lat"], color="#E74C3C")
        self.vertical_sounding_requested.emit(station)

    def _mark_sounding_point(self, lon: float, lat: float, color: str = "#E74C3C") -> None:
        """Posiciona o marcador temporário (estrela) da sonda em (lon, lat)."""
        self.clear_sounding_marker()
        (marker,) = self.ax.plot(
            lon,
            lat,
            marker="*",
            color=color,
            markersize=20,
            markeredgecolor="white",
            markeredgewidth=1.2,
            transform=ccrs.PlateCarree(),
            zorder=27,
        )
        self._sounding_marker = marker
        self.draw()

    def clear_sounding_marker(self) -> None:
        """Remove o marcador temporário da estação ancorada."""
        if self._sounding_marker is not None:
            with contextlib.suppress(ValueError, AttributeError):
                self._sounding_marker.remove()
            self._sounding_marker = None
            self.draw()

    def _place_point_symbol(self, x: float, y: float) -> None:
        """Coloca um símbolo pontual (ex.: centro de pressão, furacão) com um único clique."""
        modo = MODOS[self.current_symbol]
        cor = modo["cor"]

        if "draw_func" in modo:
            artist = modo["draw_func"](self.ax, x, y, color=cor)
        else:
            label = modo.get("label", "?")
            fontsize = modo.get("fontsize", 22)
            artist = self.ax.text(
                x,
                y,
                label,
                fontsize=fontsize,
                fontweight="bold",
                color=cor,
                ha="center",
                va="center",
                transform=ccrs.PlateCarree(),
                zorder=25,
                clip_on=True,  # texto não é recortado por padrão → flutuaria na mesa
                path_effects=[pe.withStroke(linewidth=3, foreground="white")],
            )

        cmd = PointCommand(
            symbol_key=self.current_symbol,
            x=x,
            y=y,
            artist=artist,
        )
        self.history.push(cmd)
        self.lines.append(artist)
        self.draw()

    def _on_motion(self, event: object) -> None:
        if event.inaxes == self.ax and event.xdata and event.ydata:
            self.coords_updated.emit(event.xdata, event.ydata)
        if self._pan_active and event.inaxes == self.ax:
            self._pan_to(event)
        # Caneta: acumula pontos decimados durante o arraste
        if self._pen_active and event.xdata is not None and event.ydata is not None:
            self._extend_pen_stroke(event.xdata, event.ydata, event.x, event.y)
        # Formas por arraste: rubber-band ao vivo
        if self._shape_anchor is not None and event.xdata is not None and event.ydata is not None:
            self._update_shape_drag(event.xdata, event.ydata, event.x, event.y)

    def _update_preview(self) -> None:
        if self.preview_line:
            with contextlib.suppress(ValueError):
                self.preview_line.remove()
            self.preview_line = None

        m = MODOS[self.current_symbol]
        if len(self.points_x) >= 2 and not m.get("ponto", False):
            xi, yi = interpolar_pontos(self.points_x, self.points_y)
            (line,) = self.ax.plot(
                xi,
                yi,
                color=m["cor"],
                linewidth=1.5,
                path_effects=m["efeito"](flip=self.flip, intensity=self.zcit_intensity),
                transform=ccrs.PlateCarree(),
                zorder=20,
            )
            self.preview_line = line

        self.draw()

    def commit_pending_line(self) -> bool:
        """Finaliza a linha de símbolo em rascunho (≥2 pontos), se houver.

        Usada ao salvar projeto: o traçado em andamento vive em ``points_x`` e só
        entra no histórico via ``finalize_line`` ([Enter]). Sem isto, salvar com
        uma linha não-finalizada gravaria ``drawings: []`` silenciosamente.
        Retorna ``True`` se algo foi commitado.
        """
        if len(self.points_x) >= 2 and self.preview_line is not None:
            self.finalize_line()
            return True
        return False

    def finalize_line(self) -> None:
        if len(self.points_x) >= 2 and self.preview_line:
            cmd = DrawCommand(
                symbol_key=self.current_symbol,
                points_x=list(self.points_x),
                points_y=list(self.points_y),
                flip=self.flip,
                artist=self.preview_line,
                intensity=self.zcit_intensity,
            )
            self.history.push(cmd)
            self.lines.append(self.preview_line)
            self.preview_line = None
        self.points_x.clear()
        self.points_y.clear()
        self.draw()

    # ═══════════════════════════════════════════════════════════════════════
    #  CANETA (traço livre) — motor (APIs internas livres de eventos)
    # ═══════════════════════════════════════════════════════════════════════

    def _begin_pen_stroke(self, lon: float, lat: float, px: float, py: float) -> None:
        """Inicia um traço de caneta no press do mouse/tablet."""
        if lon is None or lat is None:
            return
        self.cancel_active_draft()
        self._pen_active = True
        self._pen_draft_x = [float(lon)]
        self._pen_draft_y = [float(lat)]
        self._pen_last_px = (float(px), float(py))
        # O preview já É o artista final em potencial (promovido no release).
        self._draft_preview = create_pen_artist(
            self.ax,
            self._pen_draft_x,
            self._pen_draft_y,
            self.pen_style,
            transform=ccrs.PlateCarree(),
        )

    def _extend_pen_stroke(self, lon: float, lat: float, px: float, py: float) -> None:
        """Acrescenta um ponto decimado ao traço (chamado a cada motion)."""
        if not self._pen_active or self._draft_preview is None:
            return
        if self._pen_last_px is not None:
            dx = float(px) - self._pen_last_px[0]
            dy = float(py) - self._pen_last_px[1]
            if (dx * dx + dy * dy) ** 0.5 < PEN_MIN_PIXEL_DIST:
                return  # decimação anti-flood (tablet)
        self._pen_draft_x.append(float(lon))
        self._pen_draft_y.append(float(lat))
        self._pen_last_px = (float(px), float(py))
        self._draft_preview.set_data(self._pen_draft_x, self._pen_draft_y)
        self.draw_idle()

    def _end_pen_stroke(self) -> None:
        """Finaliza o traço no release: ≥2 pontos vira PenCommand; senão descarta."""
        if not self._pen_active:
            return
        self._pen_active = False
        artist = self._draft_preview
        self._draft_preview = None
        if artist is None:
            return
        if len(self._pen_draft_x) < 2:  # clique sem arraste → descarte
            with contextlib.suppress(ValueError, AttributeError):
                artist.remove()
            self._pen_draft_x.clear()
            self._pen_draft_y.clear()
            self._pen_last_px = None
            self.draw_idle()
            return
        cmd = PenCommand(
            points_x=list(self._pen_draft_x),
            points_y=list(self._pen_draft_y),
            style=self.pen_style.to_dict(),
            artist=artist,
        )
        self.history.push(cmd)
        self.lines.append(artist)
        self._pen_draft_x.clear()
        self._pen_draft_y.clear()
        self._pen_last_px = None
        self.draw_idle()

    # ═══════════════════════════════════════════════════════════════════════
    #  FORMAS — arraste (rect/elipse/linha/seta) e polígono por cliques
    # ═══════════════════════════════════════════════════════════════════════

    def _begin_shape_drag(self, lon: float, lat: float, px: float, py: float) -> None:
        """Ancora o arraste de uma forma e cria o preview (contorno apenas)."""
        if lon is None or lat is None:
            return
        self.cancel_active_draft()
        self._shape_anchor = (float(lon), float(lat))
        self._shape_anchor_px = (float(px), float(py))
        self._shape_last_px = (float(px), float(py))
        self._shape_drag_current = None
        st = self.shape_style
        (line,) = self.ax.plot(
            [],
            [],
            color=st.edge_color,
            linewidth=st.linewidth,
            linestyle=st.mpl_linestyle(),
            alpha=st.alpha,
            transform=ccrs.PlateCarree(),
            zorder=SHAPE_OUTLINE_ZORDER,
        )
        self._draft_preview = line

    def _update_shape_drag(
        self, lon: float, lat: float, px: float | None = None, py: float | None = None
    ) -> None:
        """Rubber-band ao vivo: reconstrói o anel âncora→cursor via set_data."""
        if self._shape_anchor is None or self._draft_preview is None:
            return
        if px is not None and py is not None:
            self._shape_last_px = (float(px), float(py))
        x0, y0 = self._shape_anchor
        xs, ys = build_preview_ring(self.shape_tool, x0, y0, float(lon), float(lat))
        self._shape_drag_current = (float(lon), float(lat))
        self._draft_preview.set_data(xs, ys)
        self.draw_idle()

    def _end_shape_drag(self, px: float | None = None, py: float | None = None) -> None:
        """Finaliza o arraste: cria a forma final (com fill opcional) e registra."""
        anchor = self._shape_anchor
        current = self._shape_drag_current
        anchor_px = self._shape_anchor_px
        last_px = (
            (float(px), float(py)) if (px is not None and py is not None) else self._shape_last_px
        )
        preview = self._draft_preview
        self._shape_anchor = None
        self._shape_anchor_px = None
        self._shape_drag_current = None
        self._shape_last_px = None
        self._draft_preview = None
        if preview is not None:
            with contextlib.suppress(ValueError, AttributeError):
                preview.remove()
        if anchor is None or current is None:
            self.draw_idle()
            return
        if anchor_px is not None and last_px is not None:
            drag = ((last_px[0] - anchor_px[0]) ** 2 + (last_px[1] - anchor_px[1]) ** 2) ** 0.5
            if drag < SHAPE_MIN_DRAG_PIXELS:  # clique acidental → descarte
                self.draw_idle()
                return
        x0, y0 = anchor
        x1, y1 = current
        head = 0.0
        if self.shape_tool == "arrow":
            ext = self.ax.get_extent(crs=ccrs.PlateCarree())
            head = default_arrow_head_size(ext[1] - ext[0], self.shape_style.linewidth)
        artist = create_shape_artist(
            self.ax,
            self.shape_tool,
            [x0, x1],
            [y0, y1],
            self.shape_style,
            head_size_deg=head,
            transform=ccrs.PlateCarree(),
        )
        cmd = ShapeCommand(
            tool=self.shape_tool,
            points_x=[x0, x1],
            points_y=[y0, y1],
            style=self.shape_style.to_dict(),
            head_size_deg=head,
            artist=artist,
        )
        self.history.push(cmd)
        self.lines.append(artist)
        self.draw_idle()

    def _add_polygon_vertex(self, lon: float, lat: float) -> None:
        """Acrescenta um vértice ao polígono em rascunho (preview = polilinha)."""
        if lon is None or lat is None:
            return
        self._shape_draft_x.append(float(lon))
        self._shape_draft_y.append(float(lat))
        if self._draft_preview is None:
            st = self.shape_style
            (line,) = self.ax.plot(
                [],
                [],
                color=st.edge_color,
                linewidth=st.linewidth,
                linestyle=st.mpl_linestyle(),
                alpha=st.alpha,
                marker="o",
                markersize=3,
                transform=ccrs.PlateCarree(),
                zorder=SHAPE_OUTLINE_ZORDER,
            )
            self._draft_preview = line
        self._draft_preview.set_data(self._shape_draft_x, self._shape_draft_y)
        self.shape_draft_changed.emit(len(self._shape_draft_x))
        self.draw_idle()

    def _pop_polygon_vertex(self) -> bool:
        """Remove o último vértice do rascunho do polígono. True se removeu."""
        if not self._shape_draft_x:
            return False
        self._shape_draft_x.pop()
        self._shape_draft_y.pop()
        if self._draft_preview is not None:
            self._draft_preview.set_data(self._shape_draft_x, self._shape_draft_y)
        self.shape_draft_changed.emit(len(self._shape_draft_x))
        self.draw_idle()
        return True

    def finalize_shape(self) -> None:
        """Fecha o polígono em rascunho (≥3 vértices) e registra no histórico."""
        if len(self._shape_draft_x) < 3:
            return  # no-op: rascunho mantido
        xs = list(self._shape_draft_x)
        ys = list(self._shape_draft_y)
        if self._draft_preview is not None:
            with contextlib.suppress(ValueError, AttributeError):
                self._draft_preview.remove()
            self._draft_preview = None
        self._shape_draft_x.clear()
        self._shape_draft_y.clear()
        artist = create_shape_artist(
            self.ax,
            "polygon",
            xs,
            ys,
            self.shape_style,
            transform=ccrs.PlateCarree(),
        )
        cmd = ShapeCommand(
            tool="polygon",
            points_x=xs,
            points_y=ys,
            style=self.shape_style.to_dict(),
            artist=artist,
        )
        self.history.push(cmd)
        self.lines.append(artist)
        self.shape_draft_changed.emit(0)
        self.draw_idle()

    def _remove_last_drawing_of(self, types: tuple) -> None:
        """Remove o último desenho finalizado do(s) tipo(s) dado(s) (padrão do emoji)."""
        cmd = self.history.remove_last_of(types)
        if cmd is None or cmd.artist is None:
            return
        with contextlib.suppress(ValueError, AttributeError):
            cmd.artist.remove()
        if cmd.artist in self.lines:
            self.lines.remove(cmd.artist)
        cmd.artist = None
        self.draw()

    def remove_last_pen_stroke(self) -> None:
        """Desfaz o último traço da caneta (mesmo que outros desenhos vieram depois)."""
        self._remove_last_drawing_of((PenCommand,))

    def remove_last_shape(self) -> None:
        """Desfaz a última forma finalizada (rascunho de polígono é papel do [Z]/Esc)."""
        self._remove_last_drawing_of((ShapeCommand,))

    def undo_point(self):
        """Desfaz: vértice de polígono em rascunho > ponto da linha atual > histórico."""
        if self._pop_polygon_vertex():
            return
        if self.points_x:
            self.points_x.pop()
            self.points_y.pop()
            self._update_preview()
        elif self.history.can_undo:
            self.undo_line()

    def undo_line(self):
        """Desfaz a última linha/anotação/ponto finalizado via histórico."""
        cmd = self.history.undo()
        if cmd is None:
            return
        if (
            isinstance(cmd, (DrawCommand, PointCommand, PenCommand, ShapeCommand))
            and cmd.artist is not None
        ):
            with contextlib.suppress(ValueError, AttributeError):
                cmd.artist.remove()
            if cmd.artist in self.lines:
                self.lines.remove(cmd.artist)
            cmd.artist = None
        elif isinstance(cmd, AnnotationCommand) and cmd.artist is not None:
            with contextlib.suppress(ValueError, AttributeError):
                cmd.artist.remove()
            if cmd.artist in self._annotations:
                self._annotations.remove(cmd.artist)
            cmd.artist = None
        self.draw()

    def redo_action(self):
        """Refaz a última ação desfeita."""
        cmd = self.history.redo()
        if cmd is None:
            return
        self._rebuild_artist(cmd)
        self.draw()

    def _rebuild_artist(self, cmd) -> None:
        """Cria o artista matplotlib de um comando e o registra na lista interna.

        Fonte ÚNICA de reconstrução, usada pelo *redo* E pela carga de projeto
        (``import_drawings_state``). NÃO chama ``self.draw()`` — o chamador
        desenha uma vez ao final. Emojis não passam por aqui (ver ``add_emoji``).
        """
        if isinstance(cmd, DrawCommand):
            xi, yi = interpolar_pontos(cmd.points_x, cmd.points_y)
            m = MODOS[cmd.symbol_key]
            (line,) = self.ax.plot(
                xi,
                yi,
                color=m["cor"],
                linewidth=1.5,
                path_effects=m["efeito"](flip=cmd.flip, intensity=getattr(cmd, "intensity", 1)),
                transform=ccrs.PlateCarree(),
                zorder=20,
            )
            cmd.artist = line
            self.lines.append(line)
        elif isinstance(cmd, PointCommand):
            m = MODOS[cmd.symbol_key]
            if "draw_func" in m:
                artist = m["draw_func"](self.ax, cmd.x, cmd.y, color=m["cor"])
            else:
                artist = self.ax.text(
                    cmd.x,
                    cmd.y,
                    m.get("label", "?"),
                    fontsize=m.get("fontsize", 22),
                    fontweight="bold",
                    color=m["cor"],
                    ha="center",
                    va="center",
                    transform=ccrs.PlateCarree(),
                    zorder=25,
                    clip_on=True,
                    path_effects=[pe.withStroke(linewidth=3, foreground="white")],
                )
            cmd.artist = artist
            self.lines.append(artist)
        elif isinstance(cmd, PenCommand):
            artist = create_pen_artist(
                self.ax,
                cmd.points_x,
                cmd.points_y,
                DrawStyle.from_dict(cmd.style),
                transform=ccrs.PlateCarree(),
            )
            cmd.artist = artist
            self.lines.append(artist)
        elif isinstance(cmd, ShapeCommand):
            artist = create_shape_artist(
                self.ax,
                cmd.tool,
                cmd.points_x,
                cmd.points_y,
                DrawStyle.from_dict(cmd.style),
                head_size_deg=cmd.head_size_deg,
                transform=ccrs.PlateCarree(),
            )
            cmd.artist = artist
            self.lines.append(artist)
        elif isinstance(cmd, AnnotationCommand):
            txt = self.ax.text(
                cmd.x,
                cmd.y,
                cmd.text,
                fontsize=cmd.fontsize,
                fontweight="bold",
                color=cmd.color,
                ha="center",
                va="center",
                transform=ccrs.PlateCarree(),
                zorder=25,
                clip_on=True,
                bbox={
                    "boxstyle": "round,pad=0.3",
                    "facecolor": "black",
                    "alpha": 0.6,
                    "edgecolor": "none",
                },
                path_effects=[pe.withStroke(linewidth=2, foreground="black")],
            )
            cmd.artist = txt
            self._annotations.append(txt)

    def export_drawings_state(self) -> list[dict]:
        """Serializa TODOS os desenhos do usuário em records (.cmbr), em ordem.

        Comandos do histórico (símbolos, caneta, formas, anotações) primeiro;
        emojis (fora do histórico) por último. O handle ``artist`` vivo nunca é
        serializado.
        """
        from cartomet_br.gui.project_io import command_to_record

        records = [command_to_record(c) for c in self.history.commands]
        records.extend(command_to_record(e) for e in self._emoji_records)
        return records

    def export_layers_state(self) -> list[dict]:
        """Manifesto das camadas de campo ATIVAS — base p/ restaurá-las do cache.

        Guarda só o necessário para REDESENHAR cada camada via loader cache-first
        (rodada/data compartilhadas vêm do ``data_context`` do projeto; o *step* é
        por-camada, pois campos de previsão podem diferir). Os rasters pesados não
        entram no ``.cmbr`` — o desenho é refeito na abertura, sem rede.
        """
        layers: list[dict] = []
        if self.synoptic_data is not None:
            layers.append(
                {
                    "kind": "synoptic",
                    "step": int(getattr(self.synoptic_data, "step", 0)),
                    "visibility": {
                        k: bool(self.plot_options.get(k, True))
                        for k in ("pnmm", "thickness", "centers")
                    },
                }
            )
        for layer_id, data in self._pl_data.items():
            layers.append(
                {
                    "kind": "field",
                    "layer_id": str(layer_id),
                    "variable": str(getattr(data, "variable", "")),
                    "level": int(getattr(data, "level", 0)),
                    "step": int(getattr(data, "step", 0)),
                    "wind_type": str(self._pl_wind_types.get(layer_id, "barbs")),
                }
            )
        if self._sat_data is not None and getattr(self._sat_data, "filename", ""):
            layers.append(
                {
                    "kind": "satellite",
                    "filename": str(self._sat_data.filename),
                }
            )
        if self._sst_data is not None:
            layers.append(
                {
                    "kind": "sst",
                    "time_str": str(getattr(self._sst_data, "time_str", "")),
                    "stride": 5,
                }
            )
        # Camadas COMPUTADAS (memorizadas p/ reativação manual — não recomputam
        # sozinhas ao abrir; o canvas não guarda seus parâmetros de cálculo).
        if self._loczcit_artist is not None:
            layers.append({"kind": "loczcit", "axis": bool(self._loczcit_axis_artists)})
        if self._blocking_artists:
            layers.append({"kind": "blocking"})
        return layers

    def snapshot_composition(self) -> dict:
        """Fotografa a composição viva p/ restaurá-la após a animação de steps.

        Guarda REFERÊNCIAS aos dados originais (não cópias): ``add_pl_layer``
        substitui ``_pl_data[layer_id]`` a cada quadro, então o snapshot deve
        ser tirado ANTES do primeiro frame. Bloqueio/LOCZCIT não entram aqui —
        o canvas guarda só artistas; seus results ficam na MainWindow.
        """
        return {
            "synoptic": self.synoptic_data,
            "plot_options": dict(self.plot_options),
            "pl_data": dict(self._pl_data),
            "wind_types": dict(self._pl_wind_types),
            "zorder_base": self._pl_zorder_counter,
            "extent_xyxy": list(self.ax.get_extent(crs=ccrs.PlateCarree())),
        }

    def restore_composition(self, snap: dict) -> None:
        """Reverte o canvas à composição do snapshot (fim/cancelamento da animação).

        Remove as camadas de modelo re-renderizadas pela animação e re-plota as
        originais; desenhos/satélite/TSM/observações não são tocados (ficaram
        estáticos durante a animação). Bloqueio/LOCZCIT são re-plotados pelo
        chamador a partir dos results memorizados na MainWindow.
        """
        with self.batch_layout():  # um único reflow+render no fim do lote
            for lid in list(self._pl_data):
                self.remove_pl_layer(lid)
            self.remove_blocking()
            self.remove_loczcit()
            self.clear_frozen_levels()

            if snap.get("synoptic") is not None:
                self.set_synoptic_data(snap["synoptic"])
                for k, v in (snap.get("plot_options") or {}).items():
                    self.toggle_layer(k, bool(v))
            else:
                self._clear_synoptic_artists()
                self.synoptic_data = None

            pl_data = snap.get("pl_data") or {}
            # Rebobina o contador para reproduzir a ordem relativa (zorder) original
            self._pl_zorder_counter = max(7, int(snap.get("zorder_base", 7)) - len(pl_data))
            for lid, data in pl_data.items():
                self.add_pl_layer(lid, data, (snap.get("wind_types") or {}).get(lid, "barbs"))

            self.ax.set_extent(snap["extent_xyxy"], crs=ccrs.PlateCarree())

        self._update_map_title()
        self._reflow_layout()
        self.draw()

    def import_drawings_state(self, records: list[dict]) -> None:
        """Reconstrói desenhos a partir de records (.cmbr). NÃO limpa o mapa.

        Desenhos comuns entram no histórico (undo/redo passam a funcionar);
        emojis vão para a lista de emojis (via ``add_emoji``). O chamador
        (abrir projeto) decide se limpa antes. Nunca dispara rede.
        """
        from cartomet_br.gui.project_io import record_to_command

        for rec in records:
            cmd = record_to_command(rec)
            if isinstance(cmd, EmojiCommand):
                self.add_emoji(cmd.x, cmd.y, cmd.emoji, cmd.fontsize)
            else:
                self._rebuild_artist(cmd)
                self.history.push(cmd)
        self.draw()

    def clear_all(self):
        # Cancela rascunhos de caneta/forma antes de varrer os artistas finais
        self.cancel_active_draft()
        for line in self.lines:
            with contextlib.suppress(ValueError):
                line.remove()
        self.lines.clear()
        if self.preview_line:
            with contextlib.suppress(ValueError):
                self.preview_line.remove()
            self.preview_line = None
        self.points_x.clear()
        self.points_y.clear()

        self.clear_annotations()
        self.clear_emojis()
        self._clear_ruler()
        self.history.clear()

        self.draw()

    def clear_map(self) -> None:
        """Remove TODAS as camadas do mapa, voltando ao mapa base limpo.

        Diferente de ``clear_all`` (que limpa só desenhos), remove também as
        camadas sinóticas, campos em altitude (PL), satélite, TSM e observações.
        """
        # Desenhos / anotações / emojis / régua / histórico
        self.clear_all()
        # Campos em altitude (PL)
        for layer_id in list(self._pl_data.keys()):
            self.remove_pl_layer(layer_id)
        # Satélite e TSM
        self.remove_satellite()
        self.remove_sst()
        # Observações de superfície (METAR/SYNOP)
        self.remove_stations()
        # Índice LOCZCIT-PA (raster categórico)
        self.remove_loczcit()
        # Bloqueio atmosférico (anomalia de Z500)
        self.remove_blocking()
        # Marcador temporário da estação de radiossondagem (estrela)
        self.clear_sounding_marker()
        # Camadas sinóticas
        self._clear_synoptic_artists()
        self.synoptic_data = None
        self._update_map_title()
        self.draw()

    def save_figure(
        self, filepath: str | Path, dpi: int = 200, extra_artists: list | None = None
    ) -> None:
        filepath = Path(filepath)
        fmt = filepath.suffix.lstrip(".").lower() or "png"
        # PDF usa o backend dedicado do matplotlib — garante que está carregado
        if fmt == "pdf":
            import matplotlib.backends.backend_pdf  # noqa: F401
        # ``extra_artists`` garante que a mobília de carta (cabeçalho/legenda
        # posicionada FORA do retângulo [0,1] da figura) entre no bbox "tight".
        self.fig.savefig(
            str(filepath),
            dpi=dpi,
            bbox_inches="tight",
            facecolor="white",
            format=fmt,
            bbox_extra_artists=extra_artists or None,
        )

    def freeze_levels(self, mapping: dict) -> None:
        """Fixa níveis de contorno por ``layer_id`` (animação de steps).

        Enquanto ativos, ``_plot_scalar_contourf``/``_plot_scalar_contour``
        usam estes níveis em vez de derivá-los do quadro corrente — escala e
        colorbar idênticas em todos os frames. Limpar com
        ``clear_frozen_levels()`` ao fim da animação.
        """
        self._frozen_levels = dict(mapping)

    def clear_frozen_levels(self) -> None:
        """Desativa os níveis congelados (volta à escala automática por plot)."""
        self._frozen_levels = {}

    def render_frame_png(self, filepath: str | Path, dpi: int = 100) -> None:
        """Salva um QUADRO de animação em PNG com dimensões constantes.

        Diferente de ``save_figure``, NÃO usa ``bbox_inches="tight"`` — o
        bbox "tight" varia com o conteúdo (título/colorbars) e produziria
        quadros de tamanhos diferentes, o que quebra a codificação GIF/MP4.
        """
        self.fig.savefig(str(filepath), dpi=dpi, facecolor="white", format="png")

    def capture_canvas(self, filepath: str | Path, scale: int = 2) -> None:
        """Captura pixel-perfect do canvas como exibido na tela.

        Usa QWidget.grab() + escala via QPixmap, garantindo que a
        imagem salva seja idêntica ao que o usuário vê no monitor.

        Parameters
        ----------
        filepath : str | Path
            Caminho de saída (.png ou .jpg).
        scale : int
            Fator de escala (2 = 2× resolução nativa para alta qualidade).
        """
        filepath = Path(filepath)

        # grab() sem argumentos captura o widget na resolução nativa
        pixmap = self.grab()

        # Escala para maior resolução se solicitado
        if scale > 1:
            scaled_size = pixmap.size() * scale
            pixmap = pixmap.scaled(
                scaled_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        # Determina formato pelo sufixo
        fmt = "PNG"
        suffix = filepath.suffix.lower()
        if suffix in (".jpg", ".jpeg"):
            fmt = "JPEG"

        pixmap.save(str(filepath), fmt, 95)

    # ═══════════════════════════════════════════════════════════════════════
    #  CARTA OMM (F7) — cabeçalho institucional + legenda de simbologia
    # ═══════════════════════════════════════════════════════════════════════

    def get_chart_time_meta(self) -> dict:
        """Validade/rodada/step da camada visível de maior prioridade.

        Mesma regra de prioridade do título dinâmico: a camada PL/superfície
        visível mais recente vence; senão a base sinótica; senão vazio. Serve
        para auto-preencher o bloco de título da carta (F7).
        """
        for lid in reversed(list(self._pl_data)):
            if lid in self._pl_artists and self._pl_artists[lid]:
                d = self._pl_data[lid]
                return {"valid_time": d.valid_time, "base_time": d.base_time, "step": d.step}
        if self.synoptic_data is not None:
            d = self.synoptic_data
            return {
                "valid_time": getattr(d, "valid_time", ""),
                "base_time": getattr(d, "base_time", ""),
                "step": getattr(d, "step", 0),
            }
        return {"valid_time": "", "base_time": "", "step": 0}

    def get_used_symbols(self) -> list[tuple[str, dict]]:
        """Símbolos OMM efetivamente desenhados (p/ a legenda da carta).

        Varre o histórico (linhas e pontuais) e devolve, sem repetição e na
        ordem de uso, os pares (chave, entrada MODOS). Caneta/forma/anotação/
        emoji não são simbologia OMM e ficam de fora.
        """
        used: list[tuple[str, dict]] = []
        seen: set[str] = set()
        for cmd in self.history.commands:
            key = getattr(cmd, "symbol_key", None)
            if key and key not in seen and key in MODOS:
                seen.add(key)
                used.append((key, MODOS[key]))
        return used

    def render_chart_furniture(self, meta: dict) -> list:
        """Compõe o cabeçalho institucional + legenda na figura (modo carta).

        Tudo é posicionado em coordenadas de figura FORA do retângulo [0,1] —
        cabeçalho acima, legenda abaixo —, para NÃO mexer na geometria do mapa
        (Cartopy tem aspecto travado; encolher o eixo dispara o recálculo do
        extent). O ``save_figure(extra_artists=...)`` recorta incluindo esses
        artistas. Devolve a lista criada (p/ o bbox e p/ ``clear_chart_furniture``).
        """
        from matplotlib.patches import Rectangle

        self.clear_chart_furniture()
        added: list = []
        navy = "#16365C"

        # ── Faixa de cabeçalho (acima da figura) ──
        band = Rectangle(
            (0.0, 1.005),
            1.0,
            0.145,
            transform=self.fig.transFigure,
            facecolor="#F2F4F7",
            edgecolor="none",
            zorder=5,
            clip_on=False,
        )
        self.fig.add_artist(band)
        added.append(band)
        accent = Rectangle(
            (0.0, 1.142),
            1.0,
            0.008,
            transform=self.fig.transFigure,
            facecolor=navy,
            edgecolor="none",
            zorder=6,
            clip_on=False,
        )
        self.fig.add_artist(accent)
        added.append(accent)

        # Logo opcional (canto esquerdo do cabeçalho).
        text_left = 0.02
        logo_path = (meta.get("logo_path") or "").strip()
        if logo_path and Path(logo_path).exists():
            try:
                import matplotlib.image as mpimg

                # zorder ALTO: a faixa do cabeçalho é opaca (zorder 5) e cobriria
                # a logo se esta ficasse abaixo. add_axes a desenha por cima.
                logo_ax = self.fig.add_axes([0.02, 1.045, 0.18, 0.085], zorder=20)
                logo_ax.imshow(mpimg.imread(logo_path))
                logo_ax.axis("off")
                added.append(logo_ax)
                text_left = 0.215
            except Exception as exc:  # noqa: BLE001 — logo é decorativo
                logger.debug("Logo da carta ignorado (%s): %s", logo_path, exc)

        def _t(x, y, s, **kw):
            kw.setdefault("transform", self.fig.transFigure)
            kw.setdefault("clip_on", False)
            kw.setdefault("zorder", 7)
            art = self.fig.text(x, y, s, **kw)
            added.append(art)
            return art

        institution = meta.get("institution") or "—"
        chart_type = meta.get("chart_type") or "Carta Sinótica"
        analyst = meta.get("analyst") or ""

        _t(
            text_left,
            1.108,
            institution,
            fontsize=15,
            fontweight="bold",
            color=navy,
            ha="left",
            va="center",
        )
        _t(text_left, 1.068, chart_type, fontsize=11, color="#222222", ha="left", va="center")
        if analyst:
            _t(
                text_left,
                1.034,
                f"Analista: {analyst}",
                fontsize=9,
                color="#555555",
                ha="left",
                va="center",
            )

        # Bloco cronológico à direita.
        emission = meta.get("emission") or ""
        valid_time = meta.get("valid_time") or ""
        base_time = meta.get("base_time") or ""
        step = meta.get("step", 0)
        right = 0.985
        if emission:
            _t(
                right,
                1.108,
                f"Emitida: {emission} UTC",
                fontsize=9,
                color="#222222",
                ha="right",
                va="center",
            )
        if valid_time:
            _t(
                right,
                1.075,
                f"Válida: {valid_time} UTC",
                fontsize=9,
                color="#222222",
                ha="right",
                va="center",
            )
        rod = []
        if base_time:
            rod.append(f"Rodada: {base_time}")
        rod.append(f"Step: +{step}h")
        _t(right, 1.042, " | ".join(rod), fontsize=9, color="#555555", ha="right", va="center")

        # Espaçador inferior invisível (branco sobre fundo branco): garante que o
        # bbox "tight" inclua os rótulos de longitude do gridliner — que o
        # get_tightbbox do Cartopy nem sempre captura — MESMO sem legenda.
        spacer = Rectangle(
            (0.0, -0.035),
            1.0,
            0.035,
            transform=self.fig.transFigure,
            facecolor="white",
            edgecolor="none",
            zorder=0,
            clip_on=False,
        )
        self.fig.add_artist(spacer)
        added.append(spacer)

        # ── Legenda da simbologia OMM (abaixo da figura) ──
        used = self.get_used_symbols()
        if used:
            ncols = 4
            nrows = (len(used) + ncols - 1) // ncols
            leg_h = 0.045 * (nrows + 1.6)  # título + linhas
            leg_ax = self.fig.add_axes([0.04, -0.055 - leg_h, 0.92, leg_h])
            leg_ax.set_xlim(0, 1)
            leg_ax.set_ylim(0, 1)
            leg_ax.axis("off")
            added.append(leg_ax)
            # Título numa linha própria, no topo do eixo (longe dos itens).
            leg_ax.text(
                0.0,
                1.0,
                "Legenda — Simbologia OMM",
                fontsize=9,
                fontweight="bold",
                color=navy,
                ha="left",
                va="top",
            )
            y_top, y_bot = 0.58, 0.12
            for i, (key, modo) in enumerate(used):
                col, row = i % ncols, i // ncols
                cx = col / ncols + 0.01
                cy = y_top if nrows <= 1 else y_top - row * (y_top - y_bot) / (nrows - 1)
                self._draw_legend_swatch(leg_ax, modo, cx, cy)
                leg_ax.text(
                    cx + 0.085,
                    cy,
                    modo.get("nome", key),
                    fontsize=8.5,
                    color="#222222",
                    ha="left",
                    va="center",
                )

        self._chart_furniture = added
        self.draw_idle()
        return added

    def _draw_legend_swatch(self, ax, modo: dict, cx: float, cy: float) -> None:
        """Naco de amostra de um símbolo na legenda (robusto a falhas).

        Símbolos de linha usam os efeitos OMM reais; pontuais com rótulo (A/B)
        usam o caractere; demais pontuais usam um marcador representativo na cor
        — os ``draw_func`` dependem de GeoAxes/ccrs e não cabem num eixo comum.
        """
        cor = modo.get("cor", "#333333")
        if not modo.get("ponto", False):
            try:
                efeito = modo["efeito"](flip=False, intensity=2)
                ax.plot(
                    [cx, cx + 0.07],
                    [cy, cy],
                    color=cor,
                    linewidth=1.6,
                    path_effects=efeito,
                    solid_capstyle="round",
                )
            except Exception:  # noqa: BLE001 — sempre cai numa linha simples
                ax.plot([cx, cx + 0.07], [cy, cy], color=cor, linewidth=2.4)
        elif modo.get("label"):
            ax.text(
                cx + 0.035,
                cy,
                modo["label"],
                fontsize=13,
                fontweight="bold",
                color=cor,
                ha="center",
                va="center",
            )
        else:
            ax.plot(
                [cx + 0.035],
                [cy],
                marker="o",
                markersize=9,
                color=cor,
                markeredgecolor="white",
                markeredgewidth=0.8,
            )

    def clear_chart_furniture(self) -> None:
        """Remove a mobília de carta (cabeçalho/legenda) e limpa o registro."""
        for art in self._chart_furniture:
            try:
                if art in self.fig.axes:
                    self.fig.delaxes(art)
                else:
                    art.remove()
            except (ValueError, AttributeError, KeyError):
                pass
        self._chart_furniture = []
        self.draw_idle()

    # ═══════════════════════════════════════════════════════════════════════
    #  ANOTAÇÕES DE TEXTO NO MAPA
    # ═══════════════════════════════════════════════════════════════════════

    def _request_annotation(self, x: float, y: float) -> None:
        """Emite sinal para a MainWindow abrir o diálogo de texto."""
        self.annotation_requested.emit(x, y)

    def add_annotation(
        self, x: float, y: float, text: str, color: str = "#FFFFFF", fontsize: int = 11
    ) -> None:
        """Adiciona uma anotação de texto no mapa."""
        txt = self.ax.text(
            x,
            y,
            text,
            fontsize=fontsize,
            fontweight="bold",
            color=color,
            ha="center",
            va="center",
            transform=ccrs.PlateCarree(),
            zorder=25,
            clip_on=True,  # texto não é recortado por padrão → flutuaria na mesa
            bbox={
                "boxstyle": "round,pad=0.3",
                "facecolor": "black",
                "alpha": 0.6,
                "edgecolor": "none",
            },
            path_effects=[pe.withStroke(linewidth=2, foreground="black")],
        )
        self._annotations.append(txt)
        self.history.push(
            AnnotationCommand(
                x=x,
                y=y,
                text=text,
                color=color,
                fontsize=fontsize,
                artist=txt,
            )
        )
        self.draw()

    def remove_last_annotation(self) -> None:
        """Remove a última anotação adicionada."""
        if self._annotations:
            txt = self._annotations.pop()
            with contextlib.suppress(ValueError, AttributeError):
                txt.remove()
            self.draw()

    def clear_annotations(self) -> None:
        """Remove todas as anotações."""
        for txt in self._annotations:
            with contextlib.suppress(ValueError, AttributeError):
                txt.remove()
        self._annotations.clear()
        self.draw()

    # ═══════════════════════════════════════════════════════════════════════
    #  EMOJIS METEOROLÓGICOS
    # ═══════════════════════════════════════════════════════════════════════

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _render_emoji_image(emoji: str, fontsize: int) -> np.ndarray | None:
        """Renders an emoji to an RGBA numpy array using Qt's native text renderer.

        Qt uses the OS colour-emoji font (Segoe UI Emoji / Apple Color Emoji /
        Noto Color Emoji), so the image is always the full-colour glyph.
        Returns None if the QPixmap machinery is unavailable.
        """
        try:
            import platform

            from PyQt6.QtCore import Qt as _Qt
            from PyQt6.QtGui import QFont, QImage, QPainter, QPixmap

            px = max(fontsize * 2, 32)  # oversample for crisp rendering
            pixmap = QPixmap(px, px)
            pixmap.fill(_Qt.GlobalColor.transparent)

            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

            sys_name = platform.system()
            if sys_name == "Windows":
                font_family = "Segoe UI Emoji"
            elif sys_name == "Darwin":
                font_family = "Apple Color Emoji"
            else:
                font_family = "Noto Color Emoji"

            font = QFont(font_family, int(px * 0.65))
            painter.setFont(font)
            painter.drawText(pixmap.rect(), _Qt.AlignmentFlag.AlignCenter, emoji)
            painter.end()

            image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            ptr = image.bits()
            ptr.setsize(image.sizeInBytes())
            return np.frombuffer(ptr, dtype=np.uint8).reshape((px, px, 4)).copy()
        except Exception:
            return None

    def add_emoji(self, lon: float, lat: float, emoji: str, fontsize: int = 28) -> None:
        """Coloca um emoji meteorológico no mapa na posição (lon, lat)."""
        from matplotlib.offsetbox import AnnotationBbox, OffsetImage

        arr = self._render_emoji_image(emoji, fontsize)
        if arr is not None:
            # OffsetImage zoom=1 → displayed at fontsize*2 screen pixels;
            # zoom=0.5 brings it back to ~fontsize screen pixels.
            im = OffsetImage(arr, zoom=0.5)
            # xycoords="data" works because the axes projection is PlateCarree,
            # so data coordinates are already lon/lat.  Using "data" (not a
            # cartopy transform object) also lets matplotlib register the proper
            # _remove_method so that artist.remove() works correctly.
            artist: object = AnnotationBbox(
                im,
                (lon, lat),
                xycoords="data",
                frameon=False,
                zorder=26,
                pad=0,
                annotation_clip=True,
            )
            self.ax.add_artist(artist)  # type: ignore[arg-type]
        else:
            # Fallback: plain text (monochrome, but better than nothing)
            artist = self.ax.text(
                lon,
                lat,
                emoji,
                fontsize=fontsize,
                ha="center",
                va="center",
                transform=ccrs.PlateCarree(),
                zorder=26,
                clip_on=True,
            )
        self._emoji_annotations.append(artist)
        self._emoji_records.append(
            EmojiCommand(
                x=float(lon), y=float(lat), emoji=emoji, fontsize=int(fontsize), artist=artist
            )
        )
        self.draw()

    def _remove_emoji_artist(self, artist: object) -> None:
        """Remove um artista emoji do eixo de forma segura.

        AnnotationBbox.remove() lança NotImplementedError quando o eixo foi
        redesenhado após a inserção do artista (ex.: ao carregar campos
        sinóticos). Nesses casos recorre à remoção direta de ax._children.
        """
        try:
            artist.remove()  # type: ignore[union-attr]
        except NotImplementedError:
            with contextlib.suppress(ValueError, AttributeError):
                self.ax._children.remove(artist)
        except (ValueError, AttributeError):
            pass

    def remove_last_emoji(self) -> None:
        """Desfaz o último emoji colocado."""
        if self._emoji_annotations:
            self._remove_emoji_artist(self._emoji_annotations.pop())
            if self._emoji_records:
                self._emoji_records.pop()
            self.draw()

    def clear_emojis(self) -> None:
        """Remove todos os emojis do mapa."""
        for artist in self._emoji_annotations:
            self._remove_emoji_artist(artist)
        self._emoji_annotations.clear()
        self._emoji_records.clear()
        self.draw()

    # ═══════════════════════════════════════════════════════════════════════
    #  RÉGUA DE DISTÂNCIA
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _haversine(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        """Calcula distância em km entre dois pontos (fórmula de Haversine)."""
        R = 6371.0
        dlat = np.radians(lat2 - lat1)
        dlon = np.radians(lon2 - lon1)
        a = (
            np.sin(dlat / 2) ** 2
            + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon / 2) ** 2
        )
        return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

    def _on_ruler_click(self, x: float, y: float) -> None:
        """Processa clique no modo régua."""
        self._ruler_points.append((x, y))

        if len(self._ruler_points) == 1:
            (marker,) = self.ax.plot(
                x,
                y,
                "r+",
                markersize=12,
                markeredgewidth=2,
                transform=ccrs.PlateCarree(),
                zorder=25,
            )
            self._ruler_artists.append(marker)
            self.draw()

        elif len(self._ruler_points) >= 2:
            p1 = self._ruler_points[-2]
            p2 = self._ruler_points[-1]

            dist = self._haversine(p1[0], p1[1], p2[0], p2[1])

            (line,) = self.ax.plot(
                [p1[0], p2[0]],
                [p1[1], p2[1]],
                "r-",
                linewidth=2,
                transform=ccrs.PlateCarree(),
                zorder=25,
            )
            self._ruler_artists.append(line)

            (marker,) = self.ax.plot(
                p2[0],
                p2[1],
                "r+",
                markersize=12,
                markeredgewidth=2,
                transform=ccrs.PlateCarree(),
                zorder=25,
            )
            self._ruler_artists.append(marker)

            mid_x = (p1[0] + p2[0]) / 2
            mid_y = (p1[1] + p2[1]) / 2

            dist_str = f"{dist:,.0f} km" if dist >= 1000 else f"{dist:.1f} km"

            txt = self.ax.text(
                mid_x,
                mid_y,
                dist_str,
                fontsize=10,
                fontweight="bold",
                color="#FF4444",
                ha="center",
                va="bottom",
                transform=ccrs.PlateCarree(),
                zorder=25,
                clip_on=True,
                bbox={
                    "boxstyle": "round,pad=0.2",
                    "facecolor": "white",
                    "alpha": 0.85,
                    "edgecolor": "#FF4444",
                    "linewidth": 1,
                },
            )
            self._ruler_artists.append(txt)
            self.draw()

    def _clear_ruler(self) -> None:
        """Remove todas as marcações da régua."""
        for artist in self._ruler_artists:
            with contextlib.suppress(ValueError, AttributeError):
                artist.remove()
        self._ruler_artists.clear()
        self._ruler_points.clear()
        self.draw()

    # ═══════════════════════════════════════════════════════════════════════
    #  IMAGEM DE SATÉLITE GOES
    # ═══════════════════════════════════════════════════════════════════════

    def plot_satellite(self, sat_data: SatelliteData):
        """Plota imagem de satélite GOES-East no mapa."""
        self.remove_satellite()

        self._sat_data = sat_data

        geos = ccrs.Geostationary(
            central_longitude=sat_data.sat_lon,
            satellite_height=sat_data.sat_h,
            sweep_axis=sat_data.sat_sweep,
        )

        img_extent = (
            sat_data.x.min(),
            sat_data.x.max(),
            sat_data.y.min(),
            sat_data.y.max(),
        )

        ir_cmap = get_ir_colormap()

        self._sat_artist = self.ax.imshow(
            sat_data.data,
            origin="upper",
            extent=img_extent,
            transform=geos,
            cmap=ir_cmap,
            vmin=-103.0,
            vmax=84.0,
            alpha=0.9,
            zorder=2,
            interpolation="nearest",
        )

        self._update_map_title()
        self._reflow_layout()
        self.draw()

    def toggle_satellite(self, visible: bool) -> None:
        """Mostra ou oculta a imagem de satélite."""
        if self._sat_artist is not None:
            self._sat_artist.set_visible(visible)
            self._update_map_title()
            self.draw()

    def remove_satellite(self) -> None:
        """Remove imagem de satélite do mapa."""
        if self._sat_artist is not None:
            with contextlib.suppress(ValueError, AttributeError):
                self._sat_artist.remove()
            self._sat_artist = None
        self._sat_data = None

    # ═══════════════════════════════════════════════════════════════════════
    #  TSM (MUR SST 1 km)
    # ═══════════════════════════════════════════════════════════════════════

    def plot_sst(self, sst_data: SSTData) -> None:
        """Plota campo de TSM (MUR SST) no mapa com colorbar."""
        self.remove_sst()
        self._sst_data = sst_data

        import matplotlib.pyplot as plt

        # Colormap tipo "thermal" para SST — jet funciona bem para oceanografia
        cmap = plt.get_cmap("RdYlBu_r")

        # Limites realistas para TSM oceânica na região tropical/subtropical
        sst_vals = sst_data.sst
        vmin = max(np.nanmin(sst_vals), -2.0)
        vmax = min(np.nanmax(sst_vals), 35.0)

        self._sst_artist = self.ax.pcolormesh(
            sst_data.lons,
            sst_data.lats,
            sst_vals,
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
            transform=ccrs.PlateCarree(),
            alpha=0.85,
            zorder=2,
            shading="auto",
        )

        # Colorbar — cax explícito, ver nota no plot_loczcit_raster
        cax = self.fig.add_axes([0.25, 0.06, 0.5, self._MESA_HCB_H])
        self._sst_colorbar = self.fig.colorbar(
            self._sst_artist,
            cax=cax,
            orientation="horizontal",
            label="TSM (°C) — MUR SST 1km",
        )
        self._sst_colorbar.ax.tick_params(labelsize=8)

        self._update_map_title()
        self._reflow_layout()
        self.draw()

    def toggle_sst(self, visible: bool) -> None:
        """Mostra ou oculta a camada de TSM."""
        if self._sst_artist is not None:
            self._sst_artist.set_visible(visible)
            if self._sst_colorbar is not None:
                self._sst_colorbar.ax.set_visible(visible)
            self._update_map_title()
            self.draw()

    def remove_sst(self) -> None:
        """Remove a camada de TSM do mapa."""
        if self._sst_colorbar is not None:
            cax = self._sst_colorbar.ax
            with contextlib.suppress(ValueError, AttributeError):
                self._sst_colorbar.remove()
            with contextlib.suppress(ValueError, AttributeError, KeyError):
                cax.remove()  # cax explícito não sai sozinho no Colorbar.remove()
            self._sst_colorbar = None
        if self._sst_artist is not None:
            with contextlib.suppress(ValueError, AttributeError):
                self._sst_artist.remove()
            self._sst_artist = None
        self._sst_data = None

    # ═══════════════════════════════════════════════════════════════════════
    #  ÍNDICE LOCZCIT-PA (raster categórico da ZCIT)
    # ═══════════════════════════════════════════════════════════════════════

    def plot_loczcit_raster(self, result) -> None:
        """Injeta o raster categórico do LOCZCIT-PA e auto-enquadra no Atlântico equatorial.

        Raster de 4 classes (0=Cinemática/magenta, 1=Fraca/verde, 2=Moderada/amarelo,
        3=Forte/vermelho). Blindagem #7: pcolormesh + ListedColormap + BoundaryNorm,
        antialiased=False, set_bad(alpha=0) — blocos exatos, sem vazamento de cor. NUNCA
        contourf. zorder=3: ACIMA do satélite (zorder=2) e da TSM, ABAIXO da costa.
        """
        from matplotlib.colors import BoundaryNorm, ListedColormap

        from cartomet_br.data.loczcit_pa_engine import CATEGORY_COLORS, STRICT_EXTENT

        self.remove_loczcit()

        # 4 cores: 0 Magenta, 1 Verde, 2 Amarelo, 3 Vermelho escuro
        cmap = ListedColormap(CATEGORY_COLORS)
        cmap.set_bad(alpha=0.0)  # NaN = transparente
        norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5], ncolors=4)
        masked = np.ma.masked_invalid(result.raster)

        self._loczcit_artist = self.ax.pcolormesh(
            result.lons,
            result.lats,
            masked,
            cmap=cmap,
            norm=norm,
            shading="nearest",
            antialiased=False,
            transform=ccrs.PlateCarree(),
            zorder=3,
        )
        # Endurecimento da Blindagem #7: no mpl 3.10 o kwarg cobre só o caminho
        # rápido do QuadMesh.draw (`_antialiased`); o set explícito preenche também
        # o estado da Collection (`_antialiaseds`), usado pelo caminho de fallback.
        self._loczcit_artist.set_antialiased(False)

        # cax EXPLÍCITO (não ax=): o eixo criado pelo fig.colorbar re-impõe o
        # aspect/shrink de criação dentro do próprio set_position, brigando com
        # o motor da mesa. Um Axes comum obedece — a posição aqui é placeholder,
        # o _layout_mesa ancora na caixa real da carta.
        cax = self.fig.add_axes([0.90, 0.25, self._MESA_VCB_W, 0.5])
        cbar = self.fig.colorbar(
            self._loczcit_artist,
            cax=cax,
            orientation="vertical",
            ticks=[0, 1, 2, 3],
        )
        cbar.ax.set_yticklabels(["Cinemática", "Fraca", "Moderada", "Forte"])
        cbar.ax.tick_params(labelsize=8)
        cbar.set_label("ZCIT (LOCZCIT-PA)", fontsize=9)
        self._loczcit_colorbar = cbar

        # Auto-enquadramento no domínio estrito (Atlântico equatorial)
        self.ax.set_extent(
            [STRICT_EXTENT[0], STRICT_EXTENT[2], STRICT_EXTENT[1], STRICT_EXTENT[3]],
            crs=ccrs.PlateCarree(),
        )

        base = f" | OLR base: {result.base_time}" if result.base_time else ""
        vt = f"Válido: {result.valid_time} UTC" if result.valid_time else ""
        self.ax.set_title(
            f"ZCIT (LOCZCIT-PA) — Potencial Acoplado\n{vt}{base}",
            fontsize=11,
            fontweight="bold",
            loc="left",
            pad=14,
        )
        self._reflow_layout()  # motor da mesa: ancora a colorbar e centraliza
        self.draw()

    def toggle_loczcit(self, visible: bool) -> None:
        """Mostra ou oculta o raster do LOCZCIT-PA (e sua colorbar)."""
        if self._loczcit_artist is not None:
            self._loczcit_artist.set_visible(visible)
        if self._loczcit_colorbar is not None:
            self._loczcit_colorbar.ax.set_visible(visible)
        self.draw()

    def plot_loczcit_axis(self, result) -> None:
        """Desenha o EIXO detectado da ZCIT (overlay opcional) sobre o raster.

        Banda simples: uma linha. Banda dupla: ramos norte/sul divergem e um nó (estrela
        branca) marca a bifurcação. É um GUIA — o meteorologista ainda traça a carta OMM
        (human-in-the-loop). zorder=18: acima do raster (3) e dos campos, abaixo dos
        desenhos do usuário (20+). Sem-op se ``result.axis`` for None.
        """
        self.remove_loczcit_axis()
        axis = getattr(result, "axis", None)
        if axis is None:
            return

        lons = np.asarray(axis.lons, dtype=float)
        north = np.asarray(axis.lat_north, dtype=float)
        south = np.asarray(axis.lat_south, dtype=float)
        halo = [pe.withStroke(linewidth=3.2, foreground="white")]

        self._loczcit_axis_artists += self._plot_axis_segments(lons, north, halo)
        # Ramo sul só onde diverge do norte (banda dupla)
        diverge = np.isfinite(north) & np.isfinite(south) & (np.abs(north - south) > 1e-6)
        if diverge.any():
            self._loczcit_axis_artists += self._plot_axis_segments(
                lons,
                np.where(diverge, south, np.nan),
                halo,
            )
            # Nós de bifurcação: transições simples↔dupla
            edges = np.flatnonzero(np.diff(diverge.astype(int)) != 0)
            for e in edges:
                lon_n, lat_n = lons[e], north[e]
                if np.isfinite(lon_n) and np.isfinite(lat_n):
                    (star,) = self.ax.plot(
                        lon_n,
                        lat_n,
                        marker="*",
                        color="white",
                        markeredgecolor="#111111",
                        markeredgewidth=0.8,
                        markersize=13,
                        zorder=19,
                        transform=ccrs.PlateCarree(),
                    )
                    self._loczcit_axis_artists.append(star)
        self.draw()

    def _plot_axis_segments(self, lons: np.ndarray, lats: np.ndarray, halo) -> list:
        """Plota a polilinha do eixo em SEGMENTOS contíguos finitos (sem NaN).

        Passar NaN ao ``ax.plot`` em GeoAxes faz o shapely emitir RuntimeWarning
        ("invalid value encountered in linestrings") a CADA redraw; quebrar nas
        lacunas e plotar cada corrida finita produz o mesmo visual sem o ruído.
        """
        artists: list = []
        finite = np.isfinite(lons) & np.isfinite(lats)
        # Fronteiras das corridas contíguas de pontos finitos
        edges = np.flatnonzero(np.diff(np.concatenate(([False], finite, [False]))))
        for start, stop in zip(edges[::2], edges[1::2], strict=True):
            if stop - start < 2:
                continue  # ponto isolado não forma linha
            (ln,) = self.ax.plot(
                lons[start:stop],
                lats[start:stop],
                color="#111111",
                linewidth=1.8,
                zorder=18,
                transform=ccrs.PlateCarree(),
                path_effects=halo,
            )
            artists.append(ln)
        return artists

    def toggle_loczcit_axis(self, visible: bool) -> None:
        """Mostra ou oculta o overlay do eixo da ZCIT."""
        for art in self._loczcit_axis_artists:
            art.set_visible(visible)
        self.draw()

    def remove_loczcit_axis(self) -> None:
        """Remove os artistas do overlay do eixo da ZCIT."""
        for art in self._loczcit_axis_artists:
            with contextlib.suppress(ValueError, AttributeError):
                art.remove()
        self._loczcit_axis_artists = []

    def remove_loczcit(self, reflow: bool = False) -> None:
        """Remove o raster do LOCZCIT-PA, sua colorbar e o overlay de eixo.

        ``reflow=True`` (remoção pelo usuário): a mesa reclama o espaço da
        colorbar. Default False — os ciclos internos de replot já reflowam.
        """
        if self._loczcit_colorbar is not None:
            cax = self._loczcit_colorbar.ax
            with contextlib.suppress(ValueError, AttributeError):
                self._loczcit_colorbar.remove()
            with contextlib.suppress(ValueError, AttributeError, KeyError):
                cax.remove()  # cax explícito não sai sozinho no Colorbar.remove()
            self._loczcit_colorbar = None
        if self._loczcit_artist is not None:
            with contextlib.suppress(ValueError, AttributeError):
                self._loczcit_artist.remove()
            self._loczcit_artist = None
        self.remove_loczcit_axis()
        if reflow:
            self._reflow_layout()

    # ═══════════════════════════════════════════════════════════════════════
    #  BLOQUEIO ATMOSFÉRICO (ANOMALIA DE Z500)
    # ═══════════════════════════════════════════════════════════════════════

    def plot_blocking_anomaly(self, result) -> None:
        """Injeta o campo de anomalia de Z500 e auto-enquadra no setor da climatologia.

        Campo CONTÍNUO (≠ raster categórico da ZCIT): contourf divergente RdBu_r
        com níveis fixos (cartas comparáveis entre si) + contorno do zero destacado
        (fronteira crista/cavado anômalos). zorder=3: acima do satélite, abaixo da
        costa. Sem clabel — Texts soltos complicariam toggle/remoção.
        """
        from cartomet_br.data.blocking_engine import ANOM_LEVELS, CLIM_EXTENT

        self.remove_blocking()

        fill = self.ax.contourf(
            result.lons,
            result.lats,
            result.anom,
            levels=ANOM_LEVELS,
            cmap="RdBu_r",
            extend="both",
            transform=ccrs.PlateCarree(),
            zorder=3,
            alpha=0.85,
        )
        zero = self.ax.contour(
            result.lons,
            result.lats,
            result.anom,
            levels=[0.0],
            colors="#3B3B3B",
            linewidths=1.6,
            transform=ccrs.PlateCarree(),
            zorder=3,
        )
        self._blocking_artists = [fill, zero]

        # cax explícito — ver nota no plot_loczcit_raster (motor da mesa manda)
        cax = self.fig.add_axes([0.90, 0.25, self._MESA_VCB_W, 0.5])
        cbar = self.fig.colorbar(fill, cax=cax, orientation="vertical")
        cbar.set_label("Anomalia de Z500 (gpm)", fontsize=9)
        cbar.ax.tick_params(labelsize=8)
        self._blocking_colorbar = cbar

        # Auto-enquadramento no setor completo da climatologia (ordem Config →
        # ordem do set_extent, como no LOCZCIT)
        self.ax.set_extent(
            [CLIM_EXTENT[0], CLIM_EXTENT[2], CLIM_EXTENT[1], CLIM_EXTENT[3]],
            crs=ccrs.PlateCarree(),
        )

        aprox = " (clim. ≈ horário mais próximo)" if result.meta.get("is_approx") else ""
        vt = f"Válido: {result.valid_time} UTC" if result.valid_time else ""
        self.ax.set_title(
            f"Bloqueio Atmosférico — Anomalia de Z500 (IFS − ERA5 1991–2020)\n"
            f"{vt} · clim {result.clim_mmdd[2:]}/{result.clim_mmdd[:2]} "
            f"{result.clim_hour:02d}Z{aprox}",
            fontsize=11,
            fontweight="bold",
            loc="left",
            pad=14,
        )
        self._reflow_layout()  # motor da mesa: ancora a colorbar e centraliza
        self.draw()

    def toggle_blocking(self, visible: bool) -> None:
        """Mostra ou oculta o campo de anomalia de Z500 (e sua colorbar)."""
        for art in self._blocking_artists:
            art.set_visible(visible)
        if self._blocking_colorbar is not None:
            self._blocking_colorbar.ax.set_visible(visible)
        self.draw()

    def remove_blocking(self, reflow: bool = False) -> None:
        """Remove o campo de anomalia de Z500 e sua colorbar.

        ``reflow=True`` (remoção pelo usuário): a mesa reclama o espaço da
        colorbar. Default False — os ciclos internos de replot já reflowam.
        """
        if self._blocking_colorbar is not None:
            cax = self._blocking_colorbar.ax
            with contextlib.suppress(ValueError, AttributeError):
                self._blocking_colorbar.remove()
            with contextlib.suppress(ValueError, AttributeError, KeyError):
                cax.remove()  # cax explícito não sai sozinho no Colorbar.remove()
            self._blocking_colorbar = None
        for art in self._blocking_artists:
            with contextlib.suppress(ValueError, AttributeError, NotImplementedError):
                art.remove()
        self._blocking_artists = []
        if reflow:
            self._reflow_layout()

    # ═══════════════════════════════════════════════════════════════════════
    #  OBSERVAÇÕES DE SUPERFÍCIE (SYNOP / METAR)
    # ═══════════════════════════════════════════════════════════════════════

    # Cores convencionais do station model
    _OBS_COLORS = {
        "metar": {"temp": "#C0392B", "dew": "#1E8449", "main": "#1B2631"},
        "synop": {"temp": "#922B21", "dew": "#196F3D", "main": "#154360"},
    }

    def plot_stations(self, df: object, kind: str = "metar") -> None:
        """Plota observações de superfície (StationPlot) com thinning por densidade.

        O raio do `reduce_point_density` escala com o extent atual: ao recortar
        para um domínio menor, mais estações aparecem (detalhe frontal).
        """
        if kind not in self._station_artists:
            return

        self.remove_stations(kind)
        self._station_data[kind] = df

        if df is None or len(df) == 0:
            self._update_map_title()
            self.draw()
            return

        import metpy.calc as mpcalc
        from metpy.plots import StationPlot

        # ── Thinning por densidade, raio proporcional ao extent × fator do usuário ──
        extent = self.config.extent
        width_deg = abs(extent[2] - extent[0])
        radius = thinning_radius(width_deg, self._obs_density_factor)  # graus

        lons = np.asarray(df["longitude"].values, dtype=float)
        lats = np.asarray(df["latitude"].values, dtype=float)
        try:
            mask = mpcalc.reduce_point_density(np.c_[lons, lats], radius)
        except Exception:
            mask = np.ones(len(df), dtype=bool)

        sub = df[mask]
        if len(sub) == 0:
            self._update_map_title()
            self.draw()
            return

        colors = self._OBS_COLORS.get(kind, self._OBS_COLORS["metar"])
        artists = self._station_artists[kind]

        sp = StationPlot(
            self.ax,
            np.asarray(sub["longitude"].values, dtype=float),
            np.asarray(sub["latitude"].values, dtype=float),
            transform=ccrs.PlateCarree(),
            fontsize=8,
            clip_on=True,
            zorder=22,
        )

        def _track(result):
            if result is None:
                return
            if isinstance(result, (list, tuple)):
                artists.extend(result)
            else:
                artists.append(result)

        # Temperatura (NW) e ponto de orvalho (SW)
        if sub["air_temperature"].notna().any():
            _track(sp.plot_parameter("NW", sub["air_temperature"].values, color=colors["temp"]))
        if sub["dew_point_temperature"].notna().any():
            _track(
                sp.plot_parameter("SW", sub["dew_point_temperature"].values, color=colors["dew"])
            )
        # PNMM (NE)
        if sub["air_pressure_at_sea_level"].notna().any():
            _track(
                sp.plot_parameter(
                    "NE",
                    sub["air_pressure_at_sea_level"].values,
                    formatter=lambda v: format(10 * v, ".0f")[-3:],
                    color=colors["main"],
                )
            )

        # Barbelas de vento (m/s → nós para exibição padrão)
        if sub["eastward_wind"].notna().any() and sub["northward_wind"].notna().any():
            try:
                u_kt = np.asarray(sub["eastward_wind"].values, dtype=float) * 1.94384
                v_kt = np.asarray(sub["northward_wind"].values, dtype=float) * 1.94384
                # Convenção por hemisfério: no Hemisfério Sul as barbelas são
                # espelhadas (flip_barb=True) em relação ao Hemisfério Norte.
                flip = np.asarray(sub["latitude"].values, dtype=float) < 0
                sp.plot_barb(u_kt, v_kt, color=colors["main"], flip_barb=flip)
                # plot_barb não retorna o artist — captura via atributo interno
                if getattr(sp, "barbs", None) is not None:
                    _track(sp.barbs)
            except Exception:
                pass

        # Cobertura de nuvens (centro) e tempo presente (W)
        try:
            from metpy.plots import current_weather, sky_cover

            if sub["cloud_coverage"].notna().any():
                _track(
                    sp.plot_symbol(
                        "C", sub["cloud_coverage"].values, sky_cover, color=colors["main"]
                    )
                )
            if sub["current_wx1_symbol"].notna().any():
                _track(
                    sp.plot_symbol(
                        "W", sub["current_wx1_symbol"].values, current_weather, color=colors["temp"]
                    )
                )
        except Exception:
            pass

        self._update_map_title()
        self.draw()

    def set_observation_density(self, factor: float) -> None:
        """Ajusta a densidade do overlay e re-renderiza a partir do cache (sem rede).

        `factor` segue `OBS_DENSITY_FACTORS` (maior → mais estações). As camadas
        ativas são re-afinadas e replotadas usando os DataFrames já em memória em
        `self._station_data` — nenhum download é disparado.
        """
        try:
            factor = float(factor)
        except (TypeError, ValueError):
            return
        if factor <= 0 or factor == self._obs_density_factor:
            return
        self._obs_density_factor = factor
        # Re-renderiza só as camadas com dados em cache (plot_stations re-afina).
        for kind, df in list(self._station_data.items()):
            if df is not None and len(df) > 0:
                self.plot_stations(df, kind=kind)

    def remove_stations(self, kind: str | None = None) -> None:
        """Remove os artists de observação de um tipo (ou de todos se None)."""
        kinds = [kind] if kind is not None else list(self._station_artists.keys())
        for k in kinds:
            for artist in self._station_artists.get(k, []):
                try:
                    artist.remove()
                except (ValueError, AttributeError, NotImplementedError):
                    with contextlib.suppress(ValueError, AttributeError):
                        self.ax._children.remove(artist)
            self._station_artists[k] = []
            self._station_data[k] = None
        self._update_map_title()

    def toggle_stations(self, kind: str, visible: bool) -> None:
        """Mostra ou oculta uma camada de observação."""
        for artist in self._station_artists.get(kind, []):
            with contextlib.suppress(AttributeError):
                artist.set_visible(visible)
        self.draw()

    # ═══════════════════════════════════════════════════════════════════════
    #  CAMPOS EM NÍVEIS DE PRESSÃO / OLR
    # ═══════════════════════════════════════════════════════════════════════

    def add_pl_layer(self, layer_id: str, data: PLFieldData, wind_type: str = "barbs"):
        """Adiciona uma camada PL/OLR ao mapa."""
        # Substituição in-place: limpa SEM renderizar (o render vem no replot)
        self._remove_pl_layer_state(layer_id)
        self._pl_zorder_counter = min(self._pl_zorder_counter + 1, 14)
        self._pl_data[layer_id] = data
        self._pl_wind_types[layer_id] = wind_type
        self._plot_pl_layer(layer_id)
        self._update_map_title()

    def _plot_pl_layer(self, layer_id: str):
        """Plota uma camada PL/OLR a partir dos dados armazenados."""
        if layer_id not in self._pl_data:
            return

        data = self._pl_data[layer_id]
        var_key = data.variable
        var_info = VARIABLE_REGISTRY.get(var_key, {})
        artists = []

        if var_info.get("category") == "wind":
            wind_type = self._pl_wind_types.get(layer_id, "barbs")
            artists = self._plot_wind_field(data, wind_type)
        elif var_info.get("plot_type") == "axis_line":
            artists = self._plot_axis_line(data, var_info)
        elif var_info.get("plot_type") == "contour":
            artists = self._plot_scalar_contour(data, var_info, self._frozen_levels.get(layer_id))
        else:
            artists = self._plot_scalar_contourf(data, var_info, self._frozen_levels.get(layer_id))

        self._pl_artists[layer_id] = artists
        self._reflow_layout()  # motor da mesa: reserva espaço p/ a colorbar inset
        self.draw()

    # Paleta OLR clássica
    _OLR_COLORS = [
        "#3b71a1",
        "#407bb3",
        "#4483c2",
        "#4e92c7",
        "#569fcc",
        "#61aac9",
        "#66b8c4",
        "#6bc7bc",
        "#78d6a4",
        "#84e38c",
        "#8bed6b",
        "#abf056",
        "#c6f24b",
        "#dbf547",
        "#eef743",
        "#fcf942",
        "#ffef3b",
        "#ffe436",
        "#fcd32d",
        "#fcbf23",
        "#faab19",
        "#f79811",
        "#f5820f",
        "#f26a0f",
        "#ed590e",
        "#e84315",
        "#d93523",
        "#c92435",
        "#b5163e",
        "#a11045",
        "#8f0d47",
        "#800a45",
        "#61063b",
        "#520436",
        "#470334",
        "#3d022e",
        "#330128",
    ]

    # Paleta de precipitação (mm) — branco → azul → roxo
    _PRECIP_COLORS = [
        "#f7fbff",
        "#d8eafc",
        "#b6dbf2",
        "#8fc8e8",
        "#62a8d8",
        "#3f8fcc",
        "#2f7ab8",
        "#2563a3",
        "#2a55a0",
        "#3a3f9e",
        "#5b2e93",
        "#7a1f86",
        "#99127a",
    ]
    _PRECIP_LEVELS = [0.2, 1, 2, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150]

    # Paleta de TSM (°C) — frio (roxo/azul) → quente (vermelho)
    _SST_COLORS = [
        "#3b0f70",
        "#3a2a8c",
        "#2c5aa0",
        "#1f7db0",
        "#2a9db5",
        "#3fb8a8",
        "#74c794",
        "#b7d97a",
        "#ece06b",
        "#f7c044",
        "#f59331",
        "#e85f29",
        "#d62f27",
        "#b3161f",
        "#7a0a16",
    ]

    def _plot_scalar_contourf(self, data: PLFieldData, var_info: dict, fixed_levels=None) -> list:
        """Plota campo escalar com contourf (preenchido) + contour labels.

        ``fixed_levels`` (animação) substitui a derivação de níveis do
        próprio quadro — ver ``freeze_levels()``.
        """
        artists = []
        values = data.values

        cmap_name = var_info.get("cmap", "viridis")
        symmetric = var_info.get("symmetric", False)

        if cmap_name == "olr_classic":
            import matplotlib.colors as mcolors

            cmap = mcolors.LinearSegmentedColormap.from_list("olr_classic", self._OLR_COLORS, N=256)
        elif cmap_name == "precip_classic":
            import matplotlib.colors as mcolors

            cmap = mcolors.LinearSegmentedColormap.from_list(
                "precip_classic", self._PRECIP_COLORS, N=256
            )
        elif cmap_name == "sst_classic":
            import matplotlib.colors as mcolors

            cmap = mcolors.LinearSegmentedColormap.from_list("sst_classic", self._SST_COLORS, N=256)
        else:
            cmap = cmap_name

        if fixed_levels is not None:
            levels = fixed_levels
        elif data.variable == "olr":
            levels = np.linspace(100, 310, 22)
        elif data.variable == "precip":
            # Níveis fixos de precipitação (mm); evita preencher áreas secas
            levels = self._PRECIP_LEVELS
        elif symmetric:
            vmax = max(abs(np.nanmin(values)), abs(np.nanmax(values)))
            vmax = vmax * 0.9
            if vmax < 1e-10:
                vmax = 1.0
            levels = np.linspace(-vmax, vmax, 21)
        else:
            vmin, vmax = np.nanpercentile(values, [2, 98])
            margin = (vmax - vmin) * 0.05
            lv_min = vmin - margin
            lv_max = vmax + margin

            if var_info.get("category") == "wind_speed" or data.variable in (
                "r",
                "q",
                "wind_speed",
                "temp_grad",
                "theta_e_grad",
                "tcwv",
                "sst_grad",
            ):
                lv_min = max(0, lv_min)

            # Evita levels constantes (min == max → matplotlib crash)
            if abs(lv_max - lv_min) < 1e-10:
                lv_min = lv_min - 1.0
                lv_max = lv_max + 1.0

            levels = np.linspace(lv_min, lv_max, 21)

        cs_fill = self.ax.contourf(
            data.lons,
            data.lats,
            values,
            levels=levels,
            cmap=cmap,
            extend="both",
            transform=ccrs.PlateCarree(),
            zorder=self._pl_zorder_counter,
            alpha=0.85,
        )
        artists.append(cs_fill)

        cs_lines = self.ax.contour(
            data.lons,
            data.lats,
            values,
            levels=levels[::2],
            colors="black",
            linewidths=0.3,
            transform=ccrs.PlateCarree(),
            zorder=self._pl_zorder_counter,
        )
        artists.append(cs_lines)

        if data.variable in ("wind_speed", "r", "gh", "olr", "tcwv"):
            label_fmt = "%1.0f"
        else:
            label_fmt = "%1.1f"

        clabels = self.ax.clabel(cs_lines, inline=True, fontsize=7, fmt=label_fmt)
        for txt in clabels:
            txt.set_path_effects([pe.withStroke(linewidth=2, foreground="white")])
            artists.append(txt)

        try:
            cax = self.ax.inset_axes([1.02, 0.15, 0.02, 0.7])
            cb = self.fig.colorbar(
                cs_fill,
                cax=cax,
                orientation="vertical",
                label=f"{data.unit}",
            )
            cb.ax.tick_params(labelsize=7)
            cb_id = f"{data.variable}_{data.level}" if data.level else data.variable
            self._pl_colorbars[cb_id] = cb
        except Exception:
            pass

        return artists

    def _plot_axis_line(self, data: PLFieldData, var_info: dict) -> list:
        """Plota o Eixo da Frente (isolinha TFP=0) como LINHA NEUTRA de apoio.

        Diagnóstico *human-in-the-loop*: o eixo orienta o traçado manual, mas
        NÃO é uma frente classificada (fria/quente) — cor neutra, sem símbolos.
        O campo TFP já vem mascarado (NaN onde |∇θe| é fraco ou sobre os Andes),
        então o contorno só aparece na zona baroclínica real. Sem colorbar; a
        zorder 16 mantém o guia acima de qualquer sombreado ligado depois.
        """
        values = data.values
        if not np.any(np.isfinite(values)):
            return []
        cs = self.ax.contour(
            data.lons,
            data.lats,
            values,
            levels=[0.0],
            colors=["#333333"],
            linewidths=2.4,
            transform=ccrs.PlateCarree(),
            zorder=16,
        )
        return [cs]

    def _plot_scalar_contour(self, data: PLFieldData, var_info: dict, fixed_levels=None) -> list:
        """Plota campo escalar com contour apenas (linhas).

        ``fixed_levels`` (animação) substitui a derivação de níveis do
        próprio quadro — ver ``freeze_levels()``.
        """
        artists = []
        values = data.values

        if fixed_levels is not None:
            levels = fixed_levels
        else:
            vmin, vmax = np.nanpercentile(values, [2, 98])
            if abs(vmax - vmin) < 1e-10:
                return artists  # Campo constante, nada a plotar
            step = max(1, int((vmax - vmin) / 20))
            levels = np.arange(int(vmin), int(vmax) + step, step)
        if len(levels) < 2:
            return artists

        cs = self.ax.contour(
            data.lons,
            data.lats,
            values,
            levels=levels,
            colors="black",
            linewidths=0.8,
            transform=ccrs.PlateCarree(),
            zorder=self._pl_zorder_counter,
        )
        artists.append(cs)

        clabels = self.ax.clabel(cs, inline=True, fontsize=8, fmt="%1.0f")
        for txt in clabels:
            txt.set_path_effects([pe.withStroke(linewidth=2, foreground="white")])
            artists.append(txt)

        return artists

    def _plot_wind_field(self, data: PLFieldData, wind_type: str) -> list:
        """Plota campo de vento: barbelas, vetores ou linhas de corrente."""
        artists = []

        if data.u_values is None or data.v_values is None:
            return artists

        u = data.u_values
        v = data.v_values
        lons = data.lons
        lats = data.lats

        if wind_type == "barbs":
            skip = 8
            lon2d, lat2d = np.meshgrid(lons, lats)
            u_kt = u * 1.94384
            v_kt = v * 1.94384

            flip_flag = np.zeros_like(u_kt)
            flip_flag[lat2d < 0] = 1

            barb = self.ax.barbs(
                lon2d[::skip, ::skip],
                lat2d[::skip, ::skip],
                u_kt[::skip, ::skip],
                v_kt[::skip, ::skip],
                length=5.0,
                sizes={"emptybarb": 0.0, "spacing": 0.2, "height": 0.5},
                linewidth=0.8,
                pivot="middle",
                barbcolor="gray",
                flip_barb=flip_flag[::skip, ::skip],
                transform=ccrs.PlateCarree(),
                zorder=self._pl_zorder_counter,
            )
            artists.append(barb)

        elif wind_type == "quiver":
            skip = 8
            lon2d, lat2d = np.meshgrid(lons, lats)

            qv = self.ax.quiver(
                lon2d[::skip, ::skip],
                lat2d[::skip, ::skip],
                u[::skip, ::skip],
                v[::skip, ::skip],
                color="gray",
                scale=300,
                width=0.002,
                transform=ccrs.PlateCarree(),
                zorder=self._pl_zorder_counter,
            )
            artists.append(qv)

        elif wind_type == "stream":
            # COARSENING antes do streamplot: linhas de corrente são qualitativas,
            # então reduzir a malha de ~0.25° para ~0.75° corta o custo de integração
            # do streamplot em ~4x (11s→3s), sem perda visual em escala sinótica.
            skip = max(1, len(lons) // 130)
            lons_1d = lons[::skip]
            lats_1d = lats[::skip]
            u_stream = u[::skip, ::skip].copy()
            v_stream = v[::skip, ::skip].copy()

            if lats_1d[0] > lats_1d[-1]:
                lats_1d = lats_1d[::-1]
                u_stream = u_stream[::-1, :]
                v_stream = v_stream[::-1, :]

            patches_before = {id(p) for p in self.ax.patches}
            lines_before = {id(ln) for ln in self.ax.lines}
            collections_before = {id(c) for c in self.ax.collections}

            sp = self.ax.streamplot(
                lons_1d,
                lats_1d,
                u_stream,
                v_stream,
                density=[2, 2],
                linewidth=0.7,
                color="gray",
                transform=ccrs.PlateCarree(),
                zorder=self._pl_zorder_counter,
            )

            if sp.lines:
                artists.append(sp.lines)
            if sp.arrows:
                artists.append(sp.arrows)

            for p in self.ax.patches:
                if id(p) not in patches_before:
                    artists.append(p)
            for ln in self.ax.lines:
                if id(ln) not in lines_before:
                    artists.append(ln)
            for c in self.ax.collections:
                if id(c) not in collections_before and c not in artists:
                    artists.append(c)

        return artists

    def _remove_pl_colorbar(self, layer_id: str):
        """Remove colorbar de uma camada PL."""
        if layer_id in self._pl_colorbars:
            try:
                cb = self._pl_colorbars[layer_id]
                cb.ax.remove()
            except Exception:
                pass
            del self._pl_colorbars[layer_id]

    def _remove_pl_layer_state(self, layer_id: str) -> None:
        """Remove artists/colorbar/estado de uma camada PL — SEM re-render."""
        if layer_id in self._pl_artists:
            for artist in self._pl_artists[layer_id]:
                with contextlib.suppress(ValueError, AttributeError, NotImplementedError):
                    artist.remove()
            del self._pl_artists[layer_id]

        self._remove_pl_colorbar(layer_id)

        if layer_id in self._pl_data:
            del self._pl_data[layer_id]
        if layer_id in self._pl_wind_types:
            del self._pl_wind_types[layer_id]

    def remove_pl_layer(self, layer_id: str):
        """Remove completamente uma camada PL."""
        self._remove_pl_layer_state(layer_id)
        self._update_map_title()
        self.draw()

    def is_stream_layer(self, layer_id: str) -> bool:
        """True se a camada PL é vento em modo 'stream' (re-render pesado)."""
        return self._pl_wind_types.get(layer_id) == "stream"

    def toggle_pl_layer(self, layer_id: str, visible: bool):
        """Liga/desliga uma camada PL sem re-download."""
        if layer_id not in self._pl_data:
            return

        if not visible:
            if layer_id in self._pl_artists:
                for artist in self._pl_artists[layer_id]:
                    with contextlib.suppress(ValueError, AttributeError, NotImplementedError):
                        artist.remove()
                self._pl_artists[layer_id] = []
            self._remove_pl_colorbar(layer_id)
        else:
            self._plot_pl_layer(layer_id)

        self._update_map_title()
        self.draw()
