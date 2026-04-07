"""
Canvas Matplotlib com Cartopy para o CartoMet BR.

Contém a classe MapCanvas — mapa interativo com suporte a desenho
de simbologias, anotações, régua, campos sinóticos, PL/OLR e satélite.
Inclui sistema de undo/redo baseado no padrão Command.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.patheffects as pe
import matplotlib.ticker as mticker

import cartopy.crs as ccrs
import cartopy.feature as cfeature

from PyQt6.QtWidgets import QSizePolicy
from PyQt6.QtCore import pyqtSignal, Qt

from cartomet_br.core.config import Config, COLORS, LEVELS
from cartomet_br.symbols import MODOS
from cartomet_br.data.ecmwf import (
    VARIABLE_REGISTRY, PLFieldData, SatelliteData, get_ir_colormap,
)
from cartomet_br.data.sst import SSTData
from cartomet_br.charts.interactive import interpolar_pontos
from cartomet_br.charts.synoptic import plot_maxmin_points
from cartomet_br.gui._constants import APP_VERSION
from cartomet_br.gui.themes import MAP_THEMES

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  SISTEMA DE UNDO/REDO (Padrão Command)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DrawCommand:
    """Comando de desenho armazenando metadados para undo/redo."""
    symbol_key: str
    points_x: list[float]
    points_y: list[float]
    flip: bool
    artist: object = field(default=None, repr=False)


@dataclass
class PointCommand:
    """Comando de símbolo pontual (clique único, ex.: centros de pressão)."""
    symbol_key: str
    x: float
    y: float
    artist: object = field(default=None, repr=False)


@dataclass
class AnnotationCommand:
    """Comando de anotação de texto."""
    x: float
    y: float
    text: str
    color: str
    fontsize: int
    artist: object = field(default=None, repr=False)


class DrawingHistory:
    """Pilha de histórico com suporte a undo/redo."""

    def __init__(self, max_size: int = 50):
        self._undo_stack: list[DrawCommand | PointCommand | AnnotationCommand] = []
        self._redo_stack: list[DrawCommand | PointCommand | AnnotationCommand] = []
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

    def clear(self) -> None:
        """Limpa todo o histórico."""
        self._undo_stack.clear()
        self._redo_stack.clear()


class MapCanvas(FigureCanvas):
    """Canvas matplotlib com mapa Cartopy e suporte a desenho interativo."""

    point_added = pyqtSignal(float, float)
    coords_updated = pyqtSignal(float, float)
    annotation_requested = pyqtSignal(float, float)

    def __init__(self, parent: QSizePolicy | None = None, config: Config | None = None) -> None:
        self.config: Config = config or Config()
        self.current_theme: str = "Clássico"

        self.fig = Figure(figsize=(12, 8), facecolor='white', dpi=100)
        super().__init__(self.fig)

        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.ax = self.fig.add_subplot(111, projection=ccrs.PlateCarree())

        # Modo de interação: "draw" | "annotate" | "ruler" | None
        self.interaction_mode: str | None = None

        # Estado de desenho
        self.current_symbol: str = "1"
        self.flip: bool = False
        self.points_x: list[float] = []
        self.points_y: list[float] = []
        self.lines: list[object] = []
        self.preview_line: object | None = None
        self.synoptic_data: object | None = None

        # Histórico de undo/redo
        self.history = DrawingHistory(max_size=50)

        # Anotações de texto
        self._annotations: list = []

        # Emojis meteorológicos
        self._emoji_annotations: list = []
        self.current_emoji: str = "☀"
        self._emoji_fontsize: int = 28

        # Régua de distância
        self._ruler_points = []
        self._ruler_artists = []

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

        # Opções de plotagem
        self.plot_options = {"pnmm": True, "thickness": True, "centers": True}

        # Imagem de satélite
        self._sat_artist = None
        self._sat_data = None

        # TSM (MUR SST)
        self._sst_artist = None
        self._sst_data = None
        self._sst_colorbar = None

        # Conecta eventos
        self.mpl_connect('button_press_event', self._on_click)
        self.mpl_connect('motion_notify_event', self._on_motion)

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
        self._annotations.clear()
        self._ruler_points.clear()
        self._ruler_artists.clear()
        self._sat_artist = None
        self._sat_data = None
        self._sst_artist = None
        self._sst_data = None
        self._sst_colorbar = None
        self.history.clear()

        self.ax.clear()

        extent = self.config.extent
        self.ax.set_extent([extent[0], extent[2], extent[1], extent[3]], crs=ccrs.PlateCarree())

        theme = MAP_THEMES.get(self.current_theme, MAP_THEMES["Clássico"])
        self.ax.set_facecolor(theme["ocean"])

        self.ax.add_feature(
            cfeature.NaturalEarthFeature("physical", "ocean", "50m",
                                         facecolor=theme["ocean"], edgecolor="none"),
            zorder=0
        )
        self.ax.add_feature(
            cfeature.NaturalEarthFeature("physical", "land", "50m",
                                         facecolor=theme["land"], edgecolor="none"),
            zorder=1
        )
        self.ax.add_feature(
            cfeature.NaturalEarthFeature("physical", "lakes", "50m",
                                         facecolor=theme["lakes"], edgecolor="none"),
            zorder=1
        )
        self.ax.add_feature(
            cfeature.NaturalEarthFeature("physical", "coastline", "50m",
                                         facecolor="none", edgecolor=theme["coastline"]),
            linewidth=0.6, zorder=5
        )
        self.ax.add_feature(
            cfeature.NaturalEarthFeature("cultural", "admin_0_boundary_lines_land", "50m",
                                         facecolor="none", edgecolor=theme["borders"]),
            linewidth=0.4, linestyle="--", zorder=5
        )
        self.ax.add_feature(
            cfeature.NaturalEarthFeature("cultural", "admin_1_states_provinces_lines", "50m",
                                         facecolor="none", edgecolor=theme["states"]),
            linewidth=0.2, zorder=4
        )

        gl = self.ax.gridlines(
            draw_labels=True, linewidth=0.3, color="#CCCCCC", alpha=0.8,
            x_inline=False, y_inline=False
        )
        gl.xlocator = mticker.MultipleLocator(10)
        gl.ylocator = mticker.MultipleLocator(10)
        gl.top_labels = False
        gl.right_labels = False
        gl.xlabel_style = {"size": 9, "color": "#333333"}
        gl.ylabel_style = {"size": 9, "color": "#333333"}

        self.ax.text(
            1.0, -0.02, f"CartoMet BR v{APP_VERSION}",
            transform=self.ax.transAxes,
            fontsize=8, color="#999999", ha="right", va="top",
            style="italic",
        )

        self.draw()

    def set_theme(self, theme_name: str) -> None:
        """Altera o tema de cores do mapa e redesenha."""
        if theme_name in MAP_THEMES:
            self.current_theme = theme_name
            self._setup_base_map()

    # ═══════════════════════════════════════════════════════════════════════
    #  CAMPOS SINÓTICOS (PNMM, Espessura, Centros H/L)
    # ═══════════════════════════════════════════════════════════════════════

    def set_synoptic_data(self, data: object) -> None:
        """Define dados sinóticos e plota — PRESERVA simbologias desenhadas."""
        self.synoptic_data = data
        self._clear_synoptic_artists()
        self._plot_synoptic_fields()

    def _clear_synoptic_artists(self) -> None:
        """Remove APENAS os artists de camadas sinóticas."""
        for layer_name, artists in self._synoptic_artists.items():
            for artist in artists:
                try:
                    artist.remove()
                except (ValueError, AttributeError, NotImplementedError):
                    pass
            self._synoptic_artists[layer_name] = []
        self.ax.set_title("", loc="left")

    def _clear_single_layer(self, layer_name: str) -> None:
        """Remove artists de UMA camada sinótica específica."""
        if layer_name in self._synoptic_artists:
            for artist in self._synoptic_artists[layer_name]:
                try:
                    artist.remove()
                except (ValueError, AttributeError, NotImplementedError):
                    pass
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
            data.lons, data.lats, data.thickness,
            levels=thickness_no_5400,
            colors=[COLORS["thickness_cold"] if lv < 5400 else COLORS["thickness_warm"] for lv in thickness_no_5400],
            linestyles="dashed", linewidths=0.8, transform=ccrs.PlateCarree(), zorder=3
        )
        self._synoptic_artists["thickness"].append(cs)

        clabels = self.ax.clabel(cs, inline=True, fontsize=8, fmt="%1.0f")
        for txt in clabels:
            txt.set_path_effects([pe.withStroke(linewidth=2, foreground="white")])
            self._synoptic_artists["thickness"].append(txt)

        cs_5400 = self.ax.contour(
            data.lons, data.lats, data.thickness,
            levels=[5400], colors=COLORS["thickness_5400"],
            linestyles="solid", linewidths=2.5, transform=ccrs.PlateCarree(), zorder=4
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
            data.lons, data.lats, data.pnmm,
            levels=pnmm_levels, colors=COLORS["pnmm_contour"],
            linewidths=1.0, transform=ccrs.PlateCarree(), zorder=6
        )
        self._synoptic_artists["pnmm"].append(cs_pnmm)

        clabels_pnmm = self.ax.clabel(cs_pnmm, inline=True, fontsize=9, fmt="%1.0f")
        for txt in clabels_pnmm:
            txt.set_path_effects([pe.withStroke(linewidth=2.5, foreground="white")])
            self._synoptic_artists["pnmm"].append(txt)

    def _plot_centers_layer(self, data: object) -> None:
        """Plota camada de centros H/L."""
        existing_texts = set(id(t) for t in self.ax.texts)

        plot_maxmin_points(
            self.ax, data.lon2d, data.lat2d, data.pnmm,
            extrema="max", nsize=80, symbol="H", color=COLORS["high_pressure"],
            min_distance=25, threshold=1018, max_points=8
        )
        plot_maxmin_points(
            self.ax, data.lon2d, data.lat2d, data.pnmm,
            extrema="min", nsize=60, symbol="L", color=COLORS["low_pressure"],
            min_distance=20, threshold=1008, max_points=10
        )

        for txt in self.ax.texts:
            if id(txt) not in existing_texts:
                self._synoptic_artists["centers"].append(txt)

    def _update_synoptic_title(self) -> None:
        """Atalho legado — redireciona para o título dinâmico."""
        self._update_map_title()

    def _update_map_title(self) -> None:
        """Título dinâmico: reflete a camada visível de maior prioridade.

        Regra de composição:
        1. Identifica a camada PL/superfície visível mais recente.
        2. Se a base sinótica (PNMM) estiver ativa, acrescenta '+ PNMM (hPa)'.
        3. Se nenhuma camada PL visível, mostra só título sinótico ou vazio.
        4. Linha 2: informações cronológicas padronizadas.
        """
        # ── Detecta base sinótica ativa ──
        has_synoptic = (
            self.synoptic_data is not None
            and self.plot_options.get("pnmm", True)
        )

        # ── Descobre a camada PL visível mais recente ──
        top_layer_id = None
        top_data = None
        for lid in reversed(list(self._pl_data)):
            # Camada existe e tem artists renderizados → visível
            if lid in self._pl_artists and self._pl_artists[lid]:
                top_layer_id = lid
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
                line1 = f"TSM (°C) — MUR SST 1km (NASA/NOAA)"
                line2 = f"Data: {self._sst_data.time_str}"
                self.ax.set_title(
                    f"{line1}\n{line2}",
                    fontsize=11, fontweight="bold", loc="left", pad=14,
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

        # ── Monta Linha 2: informações cronológicas padronizadas ──
        rodada = f"Rodada: {ref_data.base_time}" if ref_data.base_time else ""
        step_txt = f"Step: +{ref_data.step}h"
        valido = f"Válido: {ref_data.valid_time} UTC" if ref_data.valid_time else ""

        chrono_parts = [p for p in (rodada, step_txt, valido) if p]
        line2 = " | ".join(chrono_parts)

        self.ax.set_title(
            f"{line1}\n{line2}",
            fontsize=11, fontweight="bold", loc="left", pad=14,
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
        self.draw()

    # ═══════════════════════════════════════════════════════════════════════
    #  DESENHO INTERATIVO
    # ═══════════════════════════════════════════════════════════════════════

    def set_symbol(self, key: str) -> None:
        self.current_symbol = key

    def set_flip(self, flip: bool) -> None:
        self.flip = flip
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
                x, y, label,
                fontsize=fontsize, fontweight="bold", color=cor,
                ha="center", va="center",
                transform=ccrs.PlateCarree(), zorder=25,
                path_effects=[pe.withStroke(linewidth=3, foreground="white")],
            )

        cmd = PointCommand(
            symbol_key=self.current_symbol,
            x=x, y=y,
            artist=artist,
        )
        self.history.push(cmd)
        self.lines.append(artist)
        self.draw()

    def _on_motion(self, event: object) -> None:
        if event.inaxes == self.ax and event.xdata and event.ydata:
            self.coords_updated.emit(event.xdata, event.ydata)

    def _update_preview(self) -> None:
        if self.preview_line:
            try:
                self.preview_line.remove()
            except ValueError:
                pass
            self.preview_line = None

        m = MODOS[self.current_symbol]
        if len(self.points_x) >= 2 and not m.get("ponto", False):
            xi, yi = interpolar_pontos(self.points_x, self.points_y)
            line, = self.ax.plot(
                xi, yi, color=m["cor"], linewidth=1.5,
                path_effects=m["efeito"](flip=self.flip),
                transform=ccrs.PlateCarree(), zorder=20
            )
            self.preview_line = line

        self.draw()

    def finalize_line(self) -> None:
        if len(self.points_x) >= 2 and self.preview_line:
            cmd = DrawCommand(
                symbol_key=self.current_symbol,
                points_x=list(self.points_x),
                points_y=list(self.points_y),
                flip=self.flip,
                artist=self.preview_line,
            )
            self.history.push(cmd)
            self.lines.append(self.preview_line)
            self.preview_line = None
        self.points_x.clear()
        self.points_y.clear()
        self.draw()

    def undo_point(self):
        """Desfaz: remove último ponto da linha atual, ou desfaz última linha finalizada."""
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
        if isinstance(cmd, (DrawCommand, PointCommand)) and cmd.artist is not None:
            try:
                cmd.artist.remove()
            except (ValueError, AttributeError):
                pass
            if cmd.artist in self.lines:
                self.lines.remove(cmd.artist)
            cmd.artist = None
        elif isinstance(cmd, AnnotationCommand) and cmd.artist is not None:
            try:
                cmd.artist.remove()
            except (ValueError, AttributeError):
                pass
            if cmd.artist in self._annotations:
                self._annotations.remove(cmd.artist)
            cmd.artist = None
        self.draw()

    def redo_action(self):
        """Refaz a última ação desfeita."""
        cmd = self.history.redo()
        if cmd is None:
            return
        if isinstance(cmd, DrawCommand):
            xi, yi = interpolar_pontos(cmd.points_x, cmd.points_y)
            m = MODOS[cmd.symbol_key]
            line, = self.ax.plot(
                xi, yi, color=m["cor"], linewidth=1.5,
                path_effects=m["efeito"](flip=cmd.flip),
                transform=ccrs.PlateCarree(), zorder=20,
            )
            cmd.artist = line
            self.lines.append(line)
        elif isinstance(cmd, PointCommand):
            m = MODOS[cmd.symbol_key]
            if "draw_func" in m:
                artist = m["draw_func"](self.ax, cmd.x, cmd.y, color=m["cor"])
            else:
                artist = self.ax.text(
                    cmd.x, cmd.y, m.get("label", "?"),
                    fontsize=m.get("fontsize", 22), fontweight="bold", color=m["cor"],
                    ha="center", va="center",
                    transform=ccrs.PlateCarree(), zorder=25,
                    path_effects=[pe.withStroke(linewidth=3, foreground="white")],
                )
            cmd.artist = artist
            self.lines.append(artist)
        elif isinstance(cmd, AnnotationCommand):
            txt = self.ax.text(
                cmd.x, cmd.y, cmd.text,
                fontsize=cmd.fontsize, fontweight="bold", color=cmd.color,
                ha="center", va="center",
                transform=ccrs.PlateCarree(), zorder=25,
                bbox=dict(
                    boxstyle="round,pad=0.3",
                    facecolor="black", alpha=0.6,
                    edgecolor="none",
                ),
                path_effects=[pe.withStroke(linewidth=2, foreground="black")],
            )
            cmd.artist = txt
            self._annotations.append(txt)
        self.draw()

    def clear_all(self):
        for line in self.lines:
            try:
                line.remove()
            except ValueError:
                pass
        self.lines.clear()
        if self.preview_line:
            try:
                self.preview_line.remove()
            except ValueError:
                pass
            self.preview_line = None
        self.points_x.clear()
        self.points_y.clear()

        self.clear_annotations()
        self.clear_emojis()
        self._clear_ruler()
        self.history.clear()

        self.draw()

    def save_figure(self, filepath: str | Path, dpi: int = 200) -> None:
        filepath = Path(filepath)
        fmt = filepath.suffix.lstrip(".").lower() or "png"
        # PDF usa o backend dedicado do matplotlib — garante que está carregado
        if fmt == "pdf":
            import matplotlib.backends.backend_pdf  # noqa: F401
        self.fig.savefig(str(filepath), dpi=dpi, bbox_inches="tight", facecolor="white", format=fmt)

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
    #  ANOTAÇÕES DE TEXTO NO MAPA
    # ═══════════════════════════════════════════════════════════════════════

    def _request_annotation(self, x: float, y: float) -> None:
        """Emite sinal para a MainWindow abrir o diálogo de texto."""
        self.annotation_requested.emit(x, y)

    def add_annotation(self, x: float, y: float, text: str, color: str = "#FFFFFF", fontsize: int = 11) -> None:
        """Adiciona uma anotação de texto no mapa."""
        txt = self.ax.text(
            x, y, text,
            fontsize=fontsize, fontweight="bold", color=color,
            ha="center", va="center",
            transform=ccrs.PlateCarree(), zorder=25,
            bbox=dict(
                boxstyle="round,pad=0.3",
                facecolor="black", alpha=0.6,
                edgecolor="none",
            ),
            path_effects=[pe.withStroke(linewidth=2, foreground="black")],
        )
        self._annotations.append(txt)
        self.history.push(AnnotationCommand(
            x=x, y=y, text=text, color=color, fontsize=fontsize, artist=txt,
        ))
        self.draw()

    def remove_last_annotation(self) -> None:
        """Remove a última anotação adicionada."""
        if self._annotations:
            txt = self._annotations.pop()
            try:
                txt.remove()
            except (ValueError, AttributeError):
                pass
            self.draw()

    def clear_annotations(self) -> None:
        """Remove todas as anotações."""
        for txt in self._annotations:
            try:
                txt.remove()
            except (ValueError, AttributeError):
                pass
        self._annotations.clear()
        self.draw()

    # ═══════════════════════════════════════════════════════════════════════
    #  EMOJIS METEOROLÓGICOS
    # ═══════════════════════════════════════════════════════════════════════

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _render_emoji_image(emoji: str, fontsize: int) -> "np.ndarray | None":
        """Renders an emoji to an RGBA numpy array using Qt's native text renderer.

        Qt uses the OS colour-emoji font (Segoe UI Emoji / Apple Color Emoji /
        Noto Color Emoji), so the image is always the full-colour glyph.
        Returns None if the QPixmap machinery is unavailable.
        """
        try:
            import platform
            from PyQt6.QtCore import Qt as _Qt, QSize as _QSize
            from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap, QImage

            px = max(fontsize * 2, 32)          # oversample for crisp rendering
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
                im, (lon, lat),
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
                lon, lat, emoji,
                fontsize=fontsize,
                ha="center", va="center",
                transform=ccrs.PlateCarree(),
                zorder=26,
            )
        self._emoji_annotations.append(artist)
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
            try:
                self.ax._children.remove(artist)
            except (ValueError, AttributeError):
                pass
        except (ValueError, AttributeError):
            pass

    def remove_last_emoji(self) -> None:
        """Desfaz o último emoji colocado."""
        if self._emoji_annotations:
            self._remove_emoji_artist(self._emoji_annotations.pop())
            self.draw()

    def clear_emojis(self) -> None:
        """Remove todos os emojis do mapa."""
        for artist in self._emoji_annotations:
            self._remove_emoji_artist(artist)
        self._emoji_annotations.clear()
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
        a = (np.sin(dlat / 2)**2 +
             np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) *
             np.sin(dlon / 2)**2)
        return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

    def _on_ruler_click(self, x: float, y: float) -> None:
        """Processa clique no modo régua."""
        self._ruler_points.append((x, y))

        if len(self._ruler_points) == 1:
            marker, = self.ax.plot(
                x, y, "r+", markersize=12, markeredgewidth=2,
                transform=ccrs.PlateCarree(), zorder=25,
            )
            self._ruler_artists.append(marker)
            self.draw()

        elif len(self._ruler_points) >= 2:
            p1 = self._ruler_points[-2]
            p2 = self._ruler_points[-1]

            dist = self._haversine(p1[0], p1[1], p2[0], p2[1])

            line, = self.ax.plot(
                [p1[0], p2[0]], [p1[1], p2[1]],
                "r-", linewidth=2, transform=ccrs.PlateCarree(), zorder=25,
            )
            self._ruler_artists.append(line)

            marker, = self.ax.plot(
                p2[0], p2[1], "r+", markersize=12, markeredgewidth=2,
                transform=ccrs.PlateCarree(), zorder=25,
            )
            self._ruler_artists.append(marker)

            mid_x = (p1[0] + p2[0]) / 2
            mid_y = (p1[1] + p2[1]) / 2

            if dist >= 1000:
                dist_str = f"{dist:,.0f} km"
            else:
                dist_str = f"{dist:.1f} km"

            txt = self.ax.text(
                mid_x, mid_y, dist_str,
                fontsize=10, fontweight="bold", color="#FF4444",
                ha="center", va="bottom",
                transform=ccrs.PlateCarree(), zorder=25,
                bbox=dict(
                    boxstyle="round,pad=0.2",
                    facecolor="white", alpha=0.85,
                    edgecolor="#FF4444", linewidth=1,
                ),
            )
            self._ruler_artists.append(txt)
            self.draw()

    def _clear_ruler(self) -> None:
        """Remove todas as marcações da régua."""
        for artist in self._ruler_artists:
            try:
                artist.remove()
            except (ValueError, AttributeError):
                pass
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
            sat_data.x.min(), sat_data.x.max(),
            sat_data.y.min(), sat_data.y.max(),
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
            try:
                self._sat_artist.remove()
            except (ValueError, AttributeError):
                pass
            self._sat_artist = None
        self._sat_data = None

    # ═══════════════════════════════════════════════════════════════════════
    #  TSM (MUR SST 1 km)
    # ═══════════════════════════════════════════════════════════════════════

    def plot_sst(self, sst_data: SSTData) -> None:
        """Plota campo de TSM (MUR SST) no mapa com colorbar."""
        self.remove_sst()
        self._sst_data = sst_data

        import matplotlib.colors as mcolors
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

        # Colorbar
        self._sst_colorbar = self.fig.colorbar(
            self._sst_artist,
            ax=self.ax,
            orientation="horizontal",
            fraction=0.046,
            pad=0.06,
            shrink=0.6,
            label="TSM (°C) — MUR SST 1km",
        )
        self._sst_colorbar.ax.tick_params(labelsize=8)

        self._update_map_title()
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
            try:
                self._sst_colorbar.remove()
            except (ValueError, AttributeError):
                pass
            self._sst_colorbar = None
        if self._sst_artist is not None:
            try:
                self._sst_artist.remove()
            except (ValueError, AttributeError):
                pass
            self._sst_artist = None
        self._sst_data = None

    # ═══════════════════════════════════════════════════════════════════════
    #  CAMPOS EM NÍVEIS DE PRESSÃO / OLR
    # ═══════════════════════════════════════════════════════════════════════

    def add_pl_layer(self, layer_id: str, data: PLFieldData, wind_type: str = "barbs"):
        """Adiciona uma camada PL/OLR ao mapa."""
        self.remove_pl_layer(layer_id)
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
        elif var_info.get("plot_type") == "contour":
            artists = self._plot_scalar_contour(data, var_info)
        else:
            artists = self._plot_scalar_contourf(data, var_info)

        self._pl_artists[layer_id] = artists
        self.draw()

    # Paleta OLR clássica
    _OLR_COLORS = [
        "#3b71a1", "#407bb3", "#4483c2", "#4e92c7", "#569fcc",
        "#61aac9", "#66b8c4", "#6bc7bc", "#78d6a4", "#84e38c",
        "#8bed6b", "#abf056", "#c6f24b", "#dbf547", "#eef743",
        "#fcf942", "#ffef3b", "#ffe436", "#fcd32d", "#fcbf23",
        "#faab19", "#f79811", "#f5820f", "#f26a0f", "#ed590e",
        "#e84315", "#d93523", "#c92435", "#b5163e", "#a11045",
        "#8f0d47", "#800a45", "#61063b", "#520436", "#470334",
        "#3d022e", "#330128",
    ]

    def _plot_scalar_contourf(self, data: PLFieldData, var_info: dict) -> list:
        """Plota campo escalar com contourf (preenchido) + contour labels."""
        artists = []
        values = data.values

        cmap_name = var_info.get("cmap", "viridis")
        symmetric = var_info.get("symmetric", False)

        if cmap_name == "olr_classic":
            import matplotlib.colors as mcolors
            cmap = mcolors.LinearSegmentedColormap.from_list(
                "olr_classic", self._OLR_COLORS, N=256
            )
        else:
            cmap = cmap_name

        if data.variable == "olr":
            levels = np.linspace(100, 310, 22)
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

            if var_info.get("category") == "wind_speed" or \
               data.variable in ("r", "q", "wind_speed", "temp_grad", "tcwv"):
                lv_min = max(0, lv_min)

            # Evita levels constantes (min == max → matplotlib crash)
            if abs(lv_max - lv_min) < 1e-10:
                lv_min = lv_min - 1.0
                lv_max = lv_max + 1.0

            levels = np.linspace(lv_min, lv_max, 21)

        cs_fill = self.ax.contourf(
            data.lons, data.lats, values,
            levels=levels, cmap=cmap, extend="both",
            transform=ccrs.PlateCarree(), zorder=self._pl_zorder_counter, alpha=0.85,
        )
        artists.append(cs_fill)

        cs_lines = self.ax.contour(
            data.lons, data.lats, values,
            levels=levels[::2], colors="black", linewidths=0.3,
            transform=ccrs.PlateCarree(), zorder=self._pl_zorder_counter,
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
                cs_fill, cax=cax, orientation="vertical",
                label=f"{data.unit}",
            )
            cb.ax.tick_params(labelsize=7)
            cb_id = f"{data.variable}_{data.level}" if data.level else data.variable
            self._pl_colorbars[cb_id] = cb
        except Exception:
            pass

        return artists

    def _plot_scalar_contour(self, data: PLFieldData, var_info: dict) -> list:
        """Plota campo escalar com contour apenas (linhas)."""
        artists = []
        values = data.values

        vmin, vmax = np.nanpercentile(values, [2, 98])
        if abs(vmax - vmin) < 1e-10:
            return artists  # Campo constante, nada a plotar
        step = max(1, int((vmax - vmin) / 20))
        levels = np.arange(int(vmin), int(vmax) + step, step)
        if len(levels) < 2:
            return artists

        cs = self.ax.contour(
            data.lons, data.lats, values,
            levels=levels, colors="black", linewidths=0.8,
            transform=ccrs.PlateCarree(), zorder=self._pl_zorder_counter,
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
                lon2d[::skip, ::skip], lat2d[::skip, ::skip],
                u_kt[::skip, ::skip], v_kt[::skip, ::skip],
                length=5.0, sizes=dict(emptybarb=0.0, spacing=0.2, height=0.5),
                linewidth=0.8, pivot="middle", barbcolor="gray",
                flip_barb=flip_flag[::skip, ::skip],
                transform=ccrs.PlateCarree(), zorder=self._pl_zorder_counter,
            )
            artists.append(barb)

        elif wind_type == "quiver":
            skip = 8
            lon2d, lat2d = np.meshgrid(lons, lats)

            qv = self.ax.quiver(
                lon2d[::skip, ::skip], lat2d[::skip, ::skip],
                u[::skip, ::skip], v[::skip, ::skip],
                color="gray", scale=300, width=0.002,
                transform=ccrs.PlateCarree(), zorder=self._pl_zorder_counter,
            )
            artists.append(qv)

        elif wind_type == "stream":
            lons_1d = lons
            lats_1d = lats
            u_stream = u.copy()
            v_stream = v.copy()

            if lats_1d[0] > lats_1d[-1]:
                lats_1d = lats_1d[::-1]
                u_stream = u_stream[::-1, :]
                v_stream = v_stream[::-1, :]

            patches_before = set(id(p) for p in self.ax.patches)
            lines_before = set(id(l) for l in self.ax.lines)
            collections_before = set(id(c) for c in self.ax.collections)

            sp = self.ax.streamplot(
                lons_1d, lats_1d, u_stream, v_stream,
                density=[2, 2], linewidth=0.7, color="gray",
                transform=ccrs.PlateCarree(), zorder=self._pl_zorder_counter,
            )

            if sp.lines:
                artists.append(sp.lines)
            if sp.arrows:
                artists.append(sp.arrows)

            for p in self.ax.patches:
                if id(p) not in patches_before:
                    artists.append(p)
            for l in self.ax.lines:
                if id(l) not in lines_before:
                    artists.append(l)
            for c in self.ax.collections:
                if id(c) not in collections_before:
                    if c not in artists:
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

    def remove_pl_layer(self, layer_id: str):
        """Remove completamente uma camada PL."""
        if layer_id in self._pl_artists:
            for artist in self._pl_artists[layer_id]:
                try:
                    artist.remove()
                except (ValueError, AttributeError, NotImplementedError):
                    pass
            del self._pl_artists[layer_id]

        self._remove_pl_colorbar(layer_id)

        if layer_id in self._pl_data:
            del self._pl_data[layer_id]
        if layer_id in self._pl_wind_types:
            del self._pl_wind_types[layer_id]

        self._update_map_title()
        self.draw()

    def toggle_pl_layer(self, layer_id: str, visible: bool):
        """Liga/desliga uma camada PL sem re-download."""
        if layer_id not in self._pl_data:
            return

        if not visible:
            if layer_id in self._pl_artists:
                for artist in self._pl_artists[layer_id]:
                    try:
                        artist.remove()
                    except (ValueError, AttributeError, NotImplementedError):
                        pass
                self._pl_artists[layer_id] = []
            self._remove_pl_colorbar(layer_id)
        else:
            self._plot_pl_layer(layer_id)

        self._update_map_title()
        self.draw()
