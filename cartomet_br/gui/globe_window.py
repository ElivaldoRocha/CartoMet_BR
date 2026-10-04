"""Vista de Globo em tela cheia — a Terra vista do espaço com os campos ativos.

Janela independente (snapshot da sessão): projeção ``ccrs.Orthographic`` sobre
fundo estrelado, com a superfície substituída pela textura composta dos campos
ativos da carta (``services/globe_compose.py`` — mesma escala de cores, sem
rede), pela imagem GOES full disk (se carregada) ou pelo relevo natural.

Interação no padrão gesto-leve/repouso-caro do MapCanvas: arrastar gira o
globo em modo rascunho (~6 fps — costa + grade), e 180 ms após soltar o render
completo entra (textura + isolinhas + colorbars + carimbo). Scroll aproxima
(sem recriar a projeção), duplo-clique centraliza, setas giram, Esc fecha.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal, cast

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.patches import Circle
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
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

from cartomet_br.services.field_style import mask_low_signal
from cartomet_br.services.globe_compose import GlobeScene

logger = logging.getLogger(__name__)

# Raio do disco Orthographic em metros de projeção (≈ raio da Terra).
_ORTHO_HALF = 6.4e6
# Qualidade → regrid_shape do imshow reprojetado (medido: 600 ≈ 0,5 s/frame).
_QUALITY_REGRID = {"Rascunho": 400, "Equilibrado": 600, "Alta": 900}

_PELE_CAMPOS = "Campos ativos"
_PELE_GOES = "Satélite GOES"
_PELE_RELEVO = "Relevo natural"


class GlobeCanvas(FigureCanvas):
    """Motor matplotlib do globo: projeção, texturas, gestos e timers."""

    def __init__(
        self,
        scene: GlobeScene,
        *,
        center: tuple[float, float],
        satellite: Any = None,  # SatelliteData | None (full disk GOES)
        stamp_lines: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        self.fig = Figure(facecolor="black")
        super().__init__(self.fig)
        self.setParent(parent)
        self._scene = scene
        self._satellite = satellite
        self._stamp_lines = stamp_lines or []
        self._home = (float(center[0]), float(center[1]))
        self._center_lon, self._center_lat = self._home
        self._zoom = 1.0
        self._skin = _PELE_CAMPOS if scene.texture is not None else _PELE_RELEVO
        self._quality = "Equilibrado"
        self._ax = None
        self._dragging = False
        self._drag_px: tuple[float, float] = (0.0, 0.0)
        self._drag_center0: tuple[float, float] = self._home
        self._draft_busy = False
        # Estrelas determinísticas (semente fixa = sem cintilação entre frames)
        rng = np.random.default_rng(42)
        self._stars = (rng.random(900), rng.random(900), rng.power(3.0, 900) * 2.2 + 0.2)

        # Repouso: 180 ms após o último gesto, o render completo entra
        # (espelho do _view_settle_timer do MapCanvas).
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(180)
        self._settle_timer.timeout.connect(self.render_full)

        # Modo apresentação: giro lento contínuo em modo rascunho.
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(120)
        self._spin_timer.timeout.connect(self._spin_tick)

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
        if self._scene.texture is not None:
            skins.append(_PELE_CAMPOS)
        if self._satellite is not None:
            skins.append(_PELE_GOES)
        skins.append(_PELE_RELEVO)
        return skins

    def set_skin(self, skin: str) -> None:
        if skin in self.available_skins() and skin != self._skin:
            self._skin = skin
            self.render_full()

    def set_quality(self, quality: str) -> None:
        if quality in _QUALITY_REGRID and quality != self._quality:
            self._quality = quality
            self.render_full()

    def set_spinning(self, on: bool) -> None:
        if on:
            self._settle_timer.stop()
            self._spin_timer.start()
        else:
            self._spin_timer.stop()
            self.render_full()

    @property
    def spinning(self) -> bool:
        return self._spin_timer.isActive()

    def rotate_by(self, dlon: float, dlat: float) -> None:
        self._set_center(self._center_lon + dlon, self._center_lat + dlat)
        self.render_full()

    def go_home(self) -> None:
        self._set_center(*self._home)
        self._zoom = 1.0
        self.render_full()

    def shutdown(self) -> None:
        """Para timers e desconecta eventos (chamado no closeEvent)."""
        self._settle_timer.stop()
        self._spin_timer.stop()
        for cid in self._cids:
            self.mpl_disconnect(cid)
        self.fig.clf()

    def _set_center(self, lon: float, lat: float) -> None:
        self._center_lon = ((lon + 180.0) % 360.0) - 180.0
        self._center_lat = float(np.clip(lat, -89.0, 89.0))

    # ─── Renders ─────────────────────────────────────────────────────────────

    def _new_globe_axes(self):
        self.fig.clf()
        self._draw_background()
        ax = self.fig.add_axes(
            [0.06, 0.05, 0.88, 0.90],
            projection=ccrs.Orthographic(self._center_lon, self._center_lat),
        )
        ax.set_global()
        ax.patch.set_facecolor("#060a14")  # oceano noturno sob campos translúcidos
        ax.spines["geo"].set_edgecolor("#2a3b5c")
        self._ax = ax
        return ax

    def _draw_background(self) -> None:
        """Espaço: preto + estrelas fixas + halo atmosférico azulado."""
        bg = self.fig.add_axes((0.0, 0.0, 1.0, 1.0), zorder=-10)
        bg.set_axis_off()
        bg.set_xlim(0, 1)
        bg.set_ylim(0, 1)
        xs, ys, sizes = self._stars
        bg.scatter(xs, ys, s=sizes, c="white", alpha=0.75, linewidths=0)
        # Halo: anéis concêntricos com alpha decrescente ao redor do disco.
        for r, a in ((0.462, 0.20), (0.472, 0.12), (0.484, 0.06), (0.498, 0.03)):
            bg.add_patch(
                Circle((0.5, 0.5), r, facecolor="none", edgecolor="#4da3ff", linewidth=6, alpha=a)
            )

    def _apply_zoom(self) -> None:
        if self._ax is None:
            return
        half = _ORTHO_HALF / self._zoom
        self._ax.set_xlim(-half, half)
        self._ax.set_ylim(-half, half)

    def render_draft(self) -> None:
        """Frame leve do gesto (~150 ms): costa + grade, sem textura."""
        if self._draft_busy:
            return
        self._draft_busy = True
        try:
            ax = self._new_globe_axes()
            ax.add_feature(
                cfeature.COASTLINE.with_scale("110m"), linewidth=0.5, edgecolor="#cfd8e3"
            )
            ax.gridlines(color="#3a4a66", linewidth=0.3)
            self._apply_zoom()
            self.draw()
        finally:
            self._draft_busy = False

    def render_full(self) -> None:
        """Render completo do repouso: pele + isolinhas + colorbars + carimbo."""
        ax = self._new_globe_axes()
        regrid = _QUALITY_REGRID[self._quality]

        if self._skin == _PELE_CAMPOS and self._scene.texture is not None:
            ax.imshow(
                self._scene.texture,
                extent=[-180, 180, -90, 90],
                transform=ccrs.PlateCarree(),
                origin="upper",
                regrid_shape=regrid,
                zorder=2,
                interpolation="nearest",
            )
        elif self._skin == _PELE_GOES and self._satellite is not None:
            self._draw_goes(ax)
        else:
            ax.stock_img()

        ax.add_feature(cfeature.COASTLINE.with_scale("50m"), linewidth=0.45, edgecolor="#e8edf5")
        ax.gridlines(color="#55657f", linewidth=0.25, alpha=0.7)

        if self._skin == _PELE_CAMPOS:
            self._draw_contour_layers(ax)
            self._draw_colorbars()
        self._draw_stamp()

        self._apply_zoom()
        self.draw_idle()

    def _draw_goes(self, ax) -> None:
        from cartomet_br.data.ecmwf import get_ir_colormap

        sat = self._satellite
        geos = ccrs.Geostationary(
            central_longitude=sat.sat_lon,
            satellite_height=sat.sat_h,
            sweep_axis=sat.sat_sweep,
        )
        ax.imshow(
            sat.data,
            origin="upper",
            extent=(sat.x.min(), sat.x.max(), sat.y.min(), sat.y.max()),
            transform=geos,
            cmap=get_ir_colormap(),
            vmin=-103.0,
            vmax=84.0,
            zorder=2,
            interpolation="nearest",
            regrid_shape=_QUALITY_REGRID[self._quality],
        )

    def _draw_contour_layers(self, ax) -> None:
        """Isolinhas vetoriais (plot_type=="contour", ex.: gh500) — só no repouso."""
        for layer in self._scene.contour_layers:
            data = layer.data
            values = mask_low_signal(data.variable, data.unit, data.values)
            try:
                cs = ax.contour(
                    data.lons,
                    data.lats,
                    values,
                    levels=np.asarray(layer.style.levels)[::2],
                    colors="#f2f5fa",
                    linewidths=0.7,
                    transform=ccrs.PlateCarree(),
                    zorder=6,
                )
                ax.clabel(cs, inline=True, fontsize=6, fmt="%1.0f")
            except Exception as exc:  # noqa: BLE001 — isolinha é adorno, não derruba o globo
                logger.warning("Isolinhas de %s falharam no globo: %s", layer.layer_id, exc)

    def _draw_colorbars(self) -> None:
        """Colorbars compactas (mesma escala da carta) na base da figura."""
        import matplotlib as mpl
        from matplotlib.cm import ScalarMappable
        from matplotlib.colors import BoundaryNorm

        layers = self._scene.filled_layers[:2]  # no máximo duas — legibilidade
        for i, layer in enumerate(layers):
            levels = np.asarray(layer.style.levels, dtype=float)
            cmap = layer.style.cmap
            if isinstance(cmap, str):
                cmap = mpl.colormaps[cmap]
            extend = cast('Literal["neither", "both", "min", "max"]', layer.style.extend)
            norm = BoundaryNorm(levels, ncolors=cmap.N, extend=extend)
            cax = self.fig.add_axes((0.14 + i * 0.40, 0.035, 0.30, 0.016))
            cbar = self.fig.colorbar(
                ScalarMappable(norm=norm, cmap=cmap), cax=cax, orientation="horizontal"
            )
            nome = layer.var_info.get("nome", layer.data.variable)
            unidade = layer.data.unit
            sufixo = "" if layer.is_global else " (recorte regional)"
            cbar.set_label(f"{nome} ({unidade}){sufixo}", color="#dfe6f0", fontsize=8)
            cbar.ax.tick_params(labelsize=6.5, colors="#b9c3d4")
            cbar.outline.set_edgecolor("#55657f")  # type: ignore[operator] # Spine no stub

    def _draw_stamp(self) -> None:
        """Carimbo honesto no canto superior esquerdo + avisos no rodapé."""
        y = 0.975
        for i, linha in enumerate(self._stamp_lines):
            self.fig.text(
                0.015,
                y - i * 0.028,
                linha,
                color="#dfe6f0" if i == 0 else "#aab6c8",
                fontsize=10 if i == 0 else 8,
                ha="left",
                va="top",
            )
        for j, aviso in enumerate(self._scene.warnings[:3]):
            self.fig.text(
                0.985,
                0.025 + j * 0.024,
                aviso,
                color="#f0b45a",
                fontsize=7.5,
                ha="right",
                va="bottom",
            )

    # ─── Gestos ──────────────────────────────────────────────────────────────

    def _on_press(self, event) -> None:
        if event.button != 1:
            return
        if self._spin_timer.isActive():
            self._spin_timer.stop()
        if getattr(event, "dblclick", False):
            self._recenter_at(event)
            return
        self._dragging = True
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
        fator = 1.25 if event.button == "up" else 0.8
        self._zoom = float(np.clip(self._zoom * fator, 1.0, 8.0))
        self._apply_zoom()
        self.draw()
        self._settle_timer.start()

    def _recenter_at(self, event) -> None:
        if self._ax is None or event.inaxes is not self._ax or event.xdata is None:
            return
        lonlat = ccrs.PlateCarree().transform_point(event.xdata, event.ydata, self._ax.projection)
        if not (np.isfinite(lonlat[0]) and np.isfinite(lonlat[1])):
            return  # clique fora do disco
        self._set_center(float(lonlat[0]), float(lonlat[1]))
        self.render_full()

    def _spin_tick(self) -> None:
        self._set_center(self._center_lon - 1.2, self._center_lat)
        self.render_draft()


class GlobeWindow(QMainWindow):
    """Casca fullscreen do globo: barra de controles + GlobeCanvas.

    SEM ``WA_DeleteOnClose``: o Qt deletaria o C++ do FigureCanvas com os
    wrappers matplotlib ainda vivos e o GC do Python estouraria num access
    violation depois (visto em teste). Fechar esconde + ``closed`` avisa o
    dono, que solta a referência — o Python destrói na ordem certa.
    """

    closed = pyqtSignal()

    def __init__(
        self,
        scene: GlobeScene,
        *,
        center: tuple[float, float],
        satellite: Any = None,
        stamp_lines: list[str] | None = None,
        output_dir: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        from cartomet_br.gui.themes import DARK_STYLE

        self.setWindowTitle("Vista de Globo — CartoMet BR")
        self.setStyleSheet(DARK_STYLE)
        self._output_dir = output_dir

        self.globe = GlobeCanvas(
            scene, center=center, satellite=satellite, stamp_lines=stamp_lines, parent=self
        )

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
        self.spin_btn.setToolTip("Gira o globo sozinho, devagar — qualquer gesto pausa.")
        self.spin_btn.toggled.connect(self.globe.set_spinning)
        lay.addWidget(self.spin_btn)

        lay.addWidget(QLabel("Pele:"))
        self.skin_combo = QComboBox()
        self.skin_combo.addItems(self.globe.available_skins())
        self.skin_combo.currentTextChanged.connect(self.globe.set_skin)
        lay.addWidget(self.skin_combo)

        lay.addWidget(QLabel("Qualidade:"))
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(list(_QUALITY_REGRID))
        self.quality_combo.setCurrentText("Equilibrado")
        self.quality_combo.currentTextChanged.connect(self.globe.set_quality)
        lay.addWidget(self.quality_combo)

        png_btn = QPushButton("💾 PNG")
        png_btn.setToolTip("Salva a vista atual do globo (fundo estrelado incluso).")
        png_btn.clicked.connect(self._save_png)
        lay.addWidget(png_btn)

        self.status = QLabel("Arraste para girar · scroll aproxima · duplo clique centraliza")
        self.status.setStyleSheet("color: #95A5A6; font-size: 11px;")
        lay.addWidget(self.status, stretch=1)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(barra)
        v.addWidget(self.globe, stretch=1)
        self.setCentralWidget(central)

        # Esc fecha com o foco em QUALQUER filho (atalho de janela).
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.activated.connect(self.close)

    def show_fullscreen_on_parent_screen(self) -> None:
        """Tela cheia no monitor do MainWindow (multi-monitor correto)."""
        parent = self.parentWidget()
        screen = parent.screen() if parent is not None else None
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
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
        try:
            self.globe.fig.savefig(destino, dpi=150, facecolor="black")
        except OSError as exc:
            self.status.setText(f"Erro ao salvar PNG: {exc}")
            return
        self.status.setText(f"Globo salvo: {destino}")

    def closeEvent(self, event) -> None:  # noqa: N802 — override Qt
        self.globe.shutdown()
        self.closed.emit()
        super().closeEvent(event)
