"""
CartoMet BR — Janela Principal (Orquestrador)

Desenvolvido por: Elivaldo C. Rocha
Idealizador: Prof. Dr. Everaldo Barreiros de Souza
Instituição: PPGGRD-UFPA / FAMET-UFPA / IG-UFPA

Este módulo contém apenas a classe MainWindow (orquestrador) e run_gui().
Os componentes foram extraídos para módulos separados:
  - _constants.py: Metadados da aplicação e asset paths
  - themes.py: Stylesheet QSS e temas de mapa
  - download_dialog.py: Threads de download e diálogo de progresso
  - dialogs.py: Diálogos de inicialização (Welcome, FirstRun)
  - drawing_panel.py: SymbolButton e SymbologyPanel
  - layer_panel.py: SettingsPanel, FieldLayerPanel, SatellitePanel
  - map_canvas.py: MapCanvas (mapa Matplotlib/Cartopy)
"""

import contextlib
import gc
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from PyQt6.QtCore import QEvent, QSettings, QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from cartomet_br.core.config import Config
from cartomet_br.data.ecmwf import (
    VARIABLE_REGISTRY,
    PLFieldData,
    SatelliteData,
    load_olr,
    load_pl_variable,
    load_synoptic_data,
    load_tcwv,
)
from cartomet_br.gui._constants import (
    APP_AUTHOR,
    APP_DESCRIPTION,
    APP_NAME,
    APP_VERSION,
    get_icon_path,
    get_logo_path,
)
from cartomet_br.gui.analysis_engine import (
    CrossSectionWorker,
    InstabilityWorker,
    MeteogramWorker,
)
from cartomet_br.gui.cross_section_panel import CrossSectionPanel
from cartomet_br.gui.dialogs import FirstRunDialog, WelcomeDialog
from cartomet_br.gui.download_dialog import (
    BlockingThread,
    DownloadProgressDialog,
    DownloadThread,
    LoczcitThread,
    PLDownloadThread,
    SatDownloadThread,
    SSTDownloadThread,
    StationDownloadThread,
)
from cartomet_br.gui.drawing_panel import SymbologyPanel
from cartomet_br.gui.layer_panel import FieldLayerPanel, SatellitePanel, SettingsPanel, SSTPanel
from cartomet_br.gui.map_canvas import MapCanvas
from cartomet_br.gui.meteogram_panel import MeteogramPanel
from cartomet_br.gui.sounding_engine import ModelSoundingWorker, SoundingWorker
from cartomet_br.gui.sounding_panel import SoundingPanel
from cartomet_br.gui.themes import DARK_STYLE

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  JANELA PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════


class MainWindow(QMainWindow):
    """Janela principal do CartoMet BR."""

    def __init__(self, data_dir: Path):
        super().__init__()

        self.data_dir = data_dir
        self.config = Config(data_dir=data_dir, output_dir=data_dir / "output")
        self.download_thread = None
        self.pl_download_thread = None
        self.sat_download_thread = None
        self.sst_download_thread = None
        self.station_download_thread = None
        self.sounding_worker = None
        self._active_sounding_station = None  # estação RAOB ancorada na Sonda Vertical
        self._active_model_point = None  # (lon, lat) da pseudo-sondagem do modelo
        self._meteogram_worker = None  # worker do meteograma (F6)
        self._active_meteogram_point = None  # (lon, lat) do meteograma ativo
        self._xsec_worker = None  # worker do corte vertical (F4)
        self._active_xsec = None  # (lon_a, lat_a, lon_b, lat_b) ativo
        self._instability_worker = None  # worker dos campos de instabilidade (F9)
        self._last_valid_time = None  # datetime do modelo carregado (sync de obs)

        self.setWindowTitle(f"{APP_NAME} — {APP_DESCRIPTION}")
        self.setMinimumSize(1200, 800)

        icon_path = get_icon_path()
        if icon_path and icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._setup_ui()
        self._setup_menu()
        self._setup_toolbar()
        self._setup_statusbar()
        self._connect_signals()
        self._setup_zoom_shortcuts()

        self.setStyleSheet(DARK_STYLE)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Canvas dentro de um QScrollArea → permite zoom/arraste da FIGURA inteira
        # ("mesa branca") como num visualizador de documento, sem mexer no extent.
        self.canvas = MapCanvas(config=self.config)
        self.canvas_scroll = QScrollArea()
        self.canvas_scroll.setWidget(self.canvas)
        self.canvas_scroll.setWidgetResizable(True)  # 100% = preenche o viewport
        self.canvas_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas_scroll.setStyleSheet("QScrollArea { border: none; background: white; }")
        main_layout.addWidget(self.canvas_scroll, stretch=1)

        self._fig_zoom = 1.0
        self._fig_base_size = None
        self.canvas.figure_zoom_requested.connect(self._on_figure_zoom_step)
        # Pan da mesa (quando ampliada): arraste com o botão do MEIO. O filtro fica
        # no próprio canvas (recebe o mouse antes do matplotlib) e consome só o meio,
        # deixando esquerdo/direito para o desenho/régua.
        self.canvas.installEventFilter(self)
        self._mesa_pan_active = False
        self._mesa_pan_origin = None

        # Painel esquerdo — Simbologias + Satélite + TSM + Créditos
        self.symbol_panel = SymbologyPanel()
        self.satellite_panel = SatellitePanel()
        self.sst_panel = SSTPanel()

        sym_layout = self.symbol_panel.layout()
        sym_layout.insertWidget(self.symbol_panel._sat_insert_index, self.satellite_panel)
        sym_layout.insertWidget(self.symbol_panel._sat_insert_index + 1, self.sst_panel)

        left_scroll = QScrollArea()
        left_scroll.setWidget(self.symbol_panel)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        left_dock = QDockWidget("Simbologias", self)
        left_dock.setWidget(left_scroll)
        left_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        left_dock.setMinimumWidth(260)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, left_dock)

        # Painel direito — Configurações + Campos PL
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.settings_panel = SettingsPanel()
        right_layout.addWidget(self.settings_panel)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #5D6D7E; margin: 4px 8px;")
        right_layout.addWidget(sep)

        self.field_panel = FieldLayerPanel()
        right_layout.addWidget(self.field_panel)

        right_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(right_container)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        right_dock = QDockWidget("Configurações", self)
        right_dock.setWidget(scroll)
        right_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        right_dock.setMinimumWidth(290)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, right_dock)

        # Sonda Vertical (Skew-T) — dock direito deslizante, oculto até o 1º clique.
        # O mapa central encolhe graciosamente à esquerda (comportamento nativo do dock).
        self.sounding_panel = SoundingPanel("Sondagem Vertical (Skew-T)", self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.sounding_panel)
        self.sounding_panel.hide()

        # Meteograma (F6) — dock direito deslizante, oculto até o 1º clique.
        self.meteogram_panel = MeteogramPanel("Meteograma (Ponto)", self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.meteogram_panel)
        self.meteogram_panel.hide()

        # Corte Vertical (F4) — dock direito deslizante, oculto até o corte A→B.
        self.cross_section_panel = CrossSectionPanel("Corte Vertical (A→B)", self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.cross_section_panel)
        self.cross_section_panel.hide()

    def _setup_menu(self):
        menubar = self.menuBar()

        # Arquivo
        file_menu = menubar.addMenu("Arquivo")

        new_action = QAction("Novo", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self._new_project)
        file_menu.addAction(new_action)

        file_menu.addSeparator()

        save_action = QAction("Salvar Imagem...", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_figure)
        file_menu.addAction(save_action)

        export_chart_action = QAction("Exportar Carta (OMM)...", self)
        export_chart_action.triggered.connect(self._export_chart)
        file_menu.addAction(export_chart_action)

        print_action = QAction("Capturar Tela do Mapa...", self)
        print_action.setShortcut(QKeySequence.StandardKey.Print)
        print_action.triggered.connect(self._print_canvas)
        file_menu.addAction(print_action)

        file_menu.addSeparator()

        save_project_action = QAction("Salvar Projeto...", self)
        save_project_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_project_action.triggered.connect(self._save_project)
        file_menu.addAction(save_project_action)

        open_project_action = QAction("Abrir Projeto...", self)
        open_project_action.setShortcut(QKeySequence("Ctrl+Shift+O"))
        open_project_action.triggered.connect(self._open_project)
        file_menu.addAction(open_project_action)

        file_menu.addSeparator()

        config_dir_action = QAction("Configurar Diretório de Dados...", self)
        config_dir_action.triggered.connect(self._configure_data_dir)
        file_menu.addAction(config_dir_action)

        open_data_action = QAction("Abrir Pasta de Dados", self)
        open_data_action.triggered.connect(self._open_data_folder)
        file_menu.addAction(open_data_action)

        open_charts_action = QAction("Abrir Pasta de Cartas", self)
        open_charts_action.triggered.connect(self._open_charts_folder)
        file_menu.addAction(open_charts_action)

        clear_data_action = QAction("Limpar Dados Baixados...", self)
        clear_data_action.triggered.connect(self._clear_downloaded_data)
        file_menu.addAction(clear_data_action)

        file_menu.addSeparator()

        exit_action = QAction("Sair", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Editar
        edit_menu = menubar.addMenu("Editar")

        undo_action = QAction("Desfazer", self)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.triggered.connect(self.canvas.undo_point)
        edit_menu.addAction(undo_action)

        redo_action = QAction("Refazer", self)
        redo_action.setShortcut("Ctrl+Y")
        redo_action.triggered.connect(self.canvas.redo_action)
        edit_menu.addAction(redo_action)

        edit_menu.addSeparator()

        clear_action = QAction("Limpar Tudo", self)
        clear_action.triggered.connect(self.canvas.clear_all)
        edit_menu.addAction(clear_action)

        # Dados
        data_menu = menubar.addMenu("Dados")

        download_action = QAction("Baixar Dados ECMWF", self)
        download_action.triggered.connect(self._download_data)
        data_menu.addAction(download_action)

        data_menu.addSeparator()

        import_action = QAction("Importar Arquivo Local...", self)
        import_action.setShortcut("Ctrl+I")
        import_action.triggered.connect(self._import_local_file)
        data_menu.addAction(import_action)

        # Ajuda
        help_menu = menubar.addMenu("Ajuda")

        zcit_about_action = QAction("Sobre o Índice ZCIT (LOCZCIT-PA)", self)
        zcit_about_action.triggered.connect(self._show_about_loczcit)
        help_menu.addAction(zcit_about_action)

        blocking_about_action = QAction("Sobre a Análise de Bloqueio (Z500)", self)
        blocking_about_action.triggered.connect(self._show_about_blocking)
        help_menu.addAction(blocking_about_action)

        help_menu.addSeparator()

        about_action = QAction("Sobre", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_toolbar(self):
        toolbar = QToolBar("Principal")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(24, 24))
        self.addToolBar(toolbar)

        logo_path = get_logo_path()
        if logo_path and logo_path.exists():
            logo_label = QLabel()
            pixmap = QPixmap(str(logo_path))
            logo_label.setPixmap(
                pixmap.scaled(
                    28,
                    28,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            logo_label.setStyleSheet("padding: 2px 8px 2px 4px;")
            toolbar.addWidget(logo_label)

        download_btn = QPushButton("⬇ ECMWF")
        download_btn.setStyleSheet("background-color: #27AE60; padding: 6px 14px; font-size: 11px;")
        download_btn.clicked.connect(self._download_data)
        toolbar.addWidget(download_btn)

        toolbar.addSeparator()

        self.draw_mode_btn = QPushButton("✏ Modo Desenho")
        self.draw_mode_btn.setCheckable(True)
        self.draw_mode_btn.setStyleSheet("""
            QPushButton { background-color: #3498DB; padding: 6px 14px; font-size: 11px; }
            QPushButton:checked { background-color: #E74C3C; }
        """)
        self.draw_mode_btn.clicked.connect(self._toggle_draw_mode)
        toolbar.addWidget(self.draw_mode_btn)

        self.annotate_btn = QPushButton("Aa Anotar")
        self.annotate_btn.setCheckable(True)
        self.annotate_btn.setStyleSheet("""
            QPushButton { background-color: #F39C12; padding: 6px 14px; font-size: 11px; }
            QPushButton:checked { background-color: #E74C3C; }
        """)
        self.annotate_btn.clicked.connect(self._toggle_annotate_mode)
        toolbar.addWidget(self.annotate_btn)

        self.ruler_btn = QPushButton("📏 Régua")
        self.ruler_btn.setCheckable(True)
        self.ruler_btn.setStyleSheet("""
            QPushButton { background-color: #1ABC9C; padding: 6px 14px; font-size: 11px; }
            QPushButton:checked { background-color: #E74C3C; }
        """)
        self.ruler_btn.clicked.connect(self._toggle_ruler_mode)
        toolbar.addWidget(self.ruler_btn)

        self.sounding_mode_btn = QPushButton("📍 Sonda Vertical")
        self.sounding_mode_btn.setCheckable(True)
        self.sounding_mode_btn.setToolTip(
            "Clique no mapa para ancorar na estação de radiossonda mais próxima e abrir o Skew-T"
        )
        self.sounding_mode_btn.setStyleSheet("""
            QPushButton { background-color: #16A085; padding: 6px 14px; font-size: 11px; }
            QPushButton:checked { background-color: #E74C3C; }
        """)
        self.sounding_mode_btn.clicked.connect(self._toggle_sounding_mode)
        toolbar.addWidget(self.sounding_mode_btn)

        self.meteogram_mode_btn = QPushButton("📈 Meteograma")
        self.meteogram_mode_btn.setCheckable(True)
        self.meteogram_mode_btn.setToolTip(
            "Clique num ponto do mapa para ver a série temporal do modelo IFS "
            "(+0…+72h): T, vento, precip, PNMM e água precipitável"
        )
        self.meteogram_mode_btn.setStyleSheet("""
            QPushButton { background-color: #117A65; padding: 6px 14px; font-size: 11px; }
            QPushButton:checked { background-color: #E74C3C; }
        """)
        self.meteogram_mode_btn.clicked.connect(self._toggle_meteogram_mode)
        toolbar.addWidget(self.meteogram_mode_btn)

        self.xsec_mode_btn = QPushButton("🔪 Corte Vertical")
        self.xsec_mode_btn.setCheckable(True)
        self.xsec_mode_btn.setToolTip(
            "Clique em DOIS pontos (A → B) para o corte vertical do modelo IFS: "
            "ω (ascendência), temperatura, umidade e vento × pressão"
        )
        self.xsec_mode_btn.setStyleSheet("""
            QPushButton { background-color: #8E44AD; padding: 6px 14px; font-size: 11px; }
            QPushButton:checked { background-color: #E74C3C; }
        """)
        self.xsec_mode_btn.clicked.connect(self._toggle_xsec_mode)
        toolbar.addWidget(self.xsec_mode_btn)

        toolbar.addSeparator()

        # ── Zoom / navegação ──
        self.zoom_area_btn = QPushButton("🔍 Zoom área")
        self.zoom_area_btn.setCheckable(True)
        self.zoom_area_btn.setToolTip("Desenhe um retângulo para recortar e replotar a carta")
        self.zoom_area_btn.setStyleSheet("""
            QPushButton { background-color: #8E44AD; padding: 6px 14px; font-size: 11px; }
            QPushButton:checked { background-color: #E74C3C; }
        """)
        self.zoom_area_btn.clicked.connect(self._toggle_zoom_area_mode)
        toolbar.addWidget(self.zoom_area_btn)

        self.pan_btn = QPushButton("✋ Mover")
        self.pan_btn.setCheckable(True)
        self.pan_btn.setToolTip("Arraste para deslocar o mapa (roda do mouse = zoom)")
        self.pan_btn.setStyleSheet("""
            QPushButton { background-color: #2C3E50; padding: 6px 14px; font-size: 11px; }
            QPushButton:checked { background-color: #E74C3C; }
        """)
        self.pan_btn.clicked.connect(self._toggle_pan_mode)
        toolbar.addWidget(self.pan_btn)

        prev_extent_btn = QPushButton("↩ Anterior")
        prev_extent_btn.setToolTip("Volta ao extent anterior")
        prev_extent_btn.setStyleSheet(
            "background-color: #34495E; padding: 6px 12px; font-size: 11px;"
        )
        prev_extent_btn.clicked.connect(self._on_previous_extent)
        toolbar.addWidget(prev_extent_btn)

        home_btn = QPushButton("🏠 Resetar")
        home_btn.setToolTip("Volta ao extent padrão da região (Home)")
        home_btn.setStyleSheet("background-color: #34495E; padding: 6px 12px; font-size: 11px;")
        home_btn.clicked.connect(self._on_home_extent)
        toolbar.addWidget(home_btn)

        toolbar.addSeparator()

        # ─── Zoom da FIGURA ("mesa branca") — distinto do zoom geográfico ───
        _zoom_btn_css = (
            "background-color: #34495E; padding: 6px 10px; font-size: 12px; font-weight: bold;"
        )
        fz_out = QPushButton("🔎−")
        fz_out.setToolTip("Reduzir a figura (Ctrl + roda do mouse)")
        fz_out.setStyleSheet(_zoom_btn_css)
        fz_out.clicked.connect(self._figure_zoom_out)
        toolbar.addWidget(fz_out)

        self._zoom_label = QPushButton("100%")
        self._zoom_label.setToolTip("Zoom da figura — clique para Ajustar (100%)")
        self._zoom_label.setStyleSheet(
            "background-color: #2C3E50; padding: 6px 8px; font-size: 11px; min-width: 46px;"
        )
        self._zoom_label.clicked.connect(self._figure_zoom_fit)
        toolbar.addWidget(self._zoom_label)

        fz_in = QPushButton("🔎+")
        fz_in.setToolTip(
            "Ampliar a figura (Ctrl + roda do mouse). Arraste com o botão do meio para mover."
        )
        fz_in.setStyleSheet(_zoom_btn_css)
        fz_in.clicked.connect(self._figure_zoom_in)
        toolbar.addWidget(fz_in)

        fz_fit = QPushButton("⛶")
        fz_fit.setToolTip("Ajustar a figura à janela (Ctrl+0)")
        fz_fit.setStyleSheet(_zoom_btn_css)
        fz_fit.clicked.connect(self._figure_zoom_fit)
        toolbar.addWidget(fz_fit)

        toolbar.addSeparator()

        export_btn = QPushButton("📤 Exportar")
        export_btn.setStyleSheet("background-color: #9B59B6; padding: 6px 14px; font-size: 11px;")
        export_btn.clicked.connect(self._save_figure)
        toolbar.addWidget(export_btn)

        clear_map_btn = QPushButton("🗑 Limpar mapa")
        clear_map_btn.setToolTip(
            "Remove TODAS as camadas (sinótica, campos, satélite, TSM, "
            "observações e desenhos) e volta ao mapa base"
        )
        clear_map_btn.setStyleSheet(
            "background-color: #C0392B; padding: 6px 14px; font-size: 11px;"
        )
        clear_map_btn.clicked.connect(self._on_clear_map)
        toolbar.addWidget(clear_map_btn)

    def _setup_statusbar(self):
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)

        self.status_label = QLabel("● Pronto")
        self.status_label.setStyleSheet("color: #27AE60;")
        self.statusbar.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setMaximumHeight(16)
        self.progress_bar.setVisible(False)
        self.statusbar.addWidget(self.progress_bar)

        self.coords_label = QLabel("Lat: -- Lon: --")
        self.statusbar.addPermanentWidget(self.coords_label)

        self.data_dir_label = QLabel(f"📁 {self.data_dir}")
        self.data_dir_label.setStyleSheet("color: #7F8C8D; font-size: 9px;")
        self.statusbar.addPermanentWidget(self.data_dir_label)

        self.version_label = QLabel(f"{APP_NAME} v{APP_VERSION}")
        self.version_label.setStyleSheet("color: #5D6D7E;")
        self.statusbar.addPermanentWidget(self.version_label)

    def _connect_signals(self):
        self.symbol_panel.symbol_changed.connect(self.canvas.set_symbol)
        self.symbol_panel.flip_changed.connect(self.canvas.set_flip)
        self.symbol_panel.intensity_changed.connect(self.canvas.set_zcit_intensity)
        self.symbol_panel.finalize_requested.connect(self._finalize_line)
        self.symbol_panel.clear_requested.connect(self.canvas.clear_all)
        self.symbol_panel.undo_requested.connect(self.canvas.undo_point)
        self.symbol_panel.redo_requested.connect(self.canvas.redo_action)
        self.symbol_panel.emoji_mode_toggled.connect(self._on_emoji_mode_toggled)
        self.symbol_panel.emoji_selected.connect(lambda e: setattr(self.canvas, "current_emoji", e))
        self.symbol_panel.emoji_size_changed.connect(
            lambda s: setattr(self.canvas, "_emoji_fontsize", s)
        )
        self.symbol_panel.emoji_undo_requested.connect(self.canvas.remove_last_emoji)
        self.symbol_panel.pen_mode_toggled.connect(self._on_pen_mode_toggled)
        self.symbol_panel.pen_style_changed.connect(self.canvas.set_pen_style)
        self.symbol_panel.pen_undo_requested.connect(self.canvas.remove_last_pen_stroke)
        self.symbol_panel.shape_mode_toggled.connect(self._on_shape_mode_toggled)
        self.symbol_panel.shape_tool_changed.connect(self._on_shape_tool_changed)
        self.symbol_panel.shape_style_changed.connect(self.canvas.set_shape_style)
        self.symbol_panel.shape_undo_requested.connect(self.canvas.remove_last_shape)
        self.canvas.shape_draft_changed.connect(self._on_shape_draft_changed)

        self.canvas.point_added.connect(self._on_point_added)
        self.canvas.coords_updated.connect(self._on_coords_updated)
        self.canvas.extent_changed.connect(self._on_extent_changed)

        self.settings_panel.update_requested.connect(self._download_data)
        self.settings_panel.region_changed.connect(self._on_region_changed)
        self.settings_panel.theme_changed.connect(self._on_theme_changed)
        self.settings_panel.layers_changed.connect(self._on_layer_toggled)

        self.field_panel.add_layer_requested.connect(self._on_add_pl_layer)
        self.field_panel.toggle_layer_requested.connect(self._on_toggle_pl_layer)
        self.field_panel.remove_layer_requested.connect(self._on_remove_pl_layer)
        self.field_panel.preset_requested.connect(self._on_preset_requested)
        self.field_panel.loczcit_requested.connect(self._on_loczcit_requested)
        self.field_panel.blocking_requested.connect(self._on_blocking_requested)
        self.field_panel.instability_requested.connect(self._launch_instability)

        self.canvas.annotation_requested.connect(self._on_annotation_requested)

        self.satellite_panel.download_requested.connect(self._on_sat_download)
        self.satellite_panel.toggle_requested.connect(self.canvas.toggle_satellite)

        self.sst_panel.download_requested.connect(self._on_sst_download)
        self.sst_panel.toggle_requested.connect(self.canvas.toggle_sst)

        self.settings_panel.observations_changed.connect(self._on_observations_toggled)
        self.settings_panel.observation_density_changed.connect(self.canvas.set_observation_density)
        # Alinha a densidade inicial do canvas ao padrão do painel.
        self.canvas.set_observation_density(self.settings_panel.get_observation_density())

        # Sonda Vertical: clique ancorado → abre/atualiza o painel;
        # mudança de Step → recarrega a sondagem (sincronia temporal mestra).
        self.canvas.vertical_sounding_requested.connect(self._on_sounding_station_selected)
        self.canvas.model_sounding_requested.connect(self._on_model_sounding_point)
        self.sounding_panel.source_changed.connect(self._on_sounding_source_changed)
        self.canvas.meteogram_requested.connect(self._on_meteogram_point)
        self.canvas.cross_section_requested.connect(self._on_cross_section_request)
        self.settings_panel.step_combo.currentIndexChanged.connect(self._on_sounding_step_changed)

    # ═══════════════════════════════════════════════════════════════════════
    #  MODOS DE INTERAÇÃO
    # ═══════════════════════════════════════════════════════════════════════

    def _toggle_draw_mode(self, checked):
        if checked:
            self.annotate_btn.setChecked(False)
            self.ruler_btn.setChecked(False)
            self._uncheck_zoom_buttons()
        self.canvas.set_drawing_mode(checked)
        if checked:
            self.status_label.setText("● Modo Desenho — clique para adicionar pontos")
            self.status_label.setStyleSheet("color: #E74C3C;")
            self.draw_mode_btn.setText("✏ DESENHO ATIVO")
        else:
            self.status_label.setText("● Pronto")
            self.status_label.setStyleSheet("color: #27AE60;")
            self.draw_mode_btn.setText("✏ Modo Desenho")

    def _toggle_annotate_mode(self, checked):
        if checked:
            self.draw_mode_btn.setChecked(False)
            self.ruler_btn.setChecked(False)
            self._uncheck_zoom_buttons()
            self.canvas.set_annotation_mode(True)
            self.status_label.setText("● Modo Anotação — clique no mapa para inserir texto")
            self.status_label.setStyleSheet("color: #F39C12;")
        else:
            self.canvas.set_annotation_mode(False)
            self.status_label.setText("● Pronto")
            self.status_label.setStyleSheet("color: #27AE60;")

    def _toggle_ruler_mode(self, checked):
        if checked:
            self.draw_mode_btn.setChecked(False)
            self.annotate_btn.setChecked(False)
            self._uncheck_zoom_buttons()
            self.canvas.set_ruler_mode(True)
            self.status_label.setText("● Régua — clique em dois pontos para medir distância")
            self.status_label.setStyleSheet("color: #1ABC9C;")
        else:
            self.canvas.set_ruler_mode(False)
            self.status_label.setText("● Pronto")
            self.status_label.setStyleSheet("color: #27AE60;")

    def _on_emoji_mode_toggled(self, enabled: bool) -> None:
        """Ativa/desativa modo emoji no canvas; desativa outros modos de interação."""
        if enabled:
            self.draw_mode_btn.setChecked(False)
            self.annotate_btn.setChecked(False)
            self.ruler_btn.setChecked(False)
            self.canvas.set_drawing_mode(False)
            self.canvas.set_annotation_mode(False)
            self.canvas.set_ruler_mode(False)
            self.canvas.set_emoji_mode(True)
            self.status_label.setText("● Modo Emoji — clique no mapa para inserir")
            self.status_label.setStyleSheet("color: #F39C12;")
        else:
            self.canvas.set_emoji_mode(False)
            self.status_label.setText("● Pronto")
            self.status_label.setStyleSheet("color: #27AE60;")
        if enabled:
            self._uncheck_zoom_buttons()

    _SHAPE_TOOL_NAMES = {
        "rect": "Retângulo",
        "ellipse": "Elipse",
        "arrow": "Seta",
        "line": "Linha reta",
        "polygon": "Polígono",
    }

    def _on_pen_mode_toggled(self, enabled: bool) -> None:
        """Ativa/desativa a caneta; desativa os demais modos de interação."""
        if enabled:
            self.draw_mode_btn.setChecked(False)
            self.annotate_btn.setChecked(False)
            self.ruler_btn.setChecked(False)
            self.canvas.set_drawing_mode(False)
            self.canvas.set_annotation_mode(False)
            self.canvas.set_ruler_mode(False)
            self.canvas.set_emoji_mode(False)
            self.canvas.set_shape_mode(False)
            self._uncheck_zoom_buttons()
            self.canvas.set_pen_mode(True)
            self.status_label.setText("● Modo Caneta — pressione e arraste para desenhar")
            self.status_label.setStyleSheet("color: #1ABC9C;")
        else:
            self.canvas.set_pen_mode(False)
            self.status_label.setText("● Pronto")
            self.status_label.setStyleSheet("color: #27AE60;")

    def _on_shape_mode_toggled(self, enabled: bool) -> None:
        """Ativa/desativa o modo formas; desativa os demais modos de interação."""
        if enabled:
            self.draw_mode_btn.setChecked(False)
            self.annotate_btn.setChecked(False)
            self.ruler_btn.setChecked(False)
            self.canvas.set_drawing_mode(False)
            self.canvas.set_annotation_mode(False)
            self.canvas.set_ruler_mode(False)
            self.canvas.set_emoji_mode(False)
            self.canvas.set_pen_mode(False)
            self._uncheck_zoom_buttons()
            self.canvas.set_shape_mode(True)
            self._show_shape_status(self.canvas.shape_tool)
        else:
            self.canvas.set_shape_mode(False)
            self.status_label.setText("● Pronto")
            self.status_label.setStyleSheet("color: #27AE60;")

    def _on_shape_tool_changed(self, tool: str) -> None:
        self.canvas.set_shape_tool(tool)
        if self.canvas.interaction_mode == "shape":
            self._show_shape_status(tool)

    def _show_shape_status(self, tool: str) -> None:
        nome = self._SHAPE_TOOL_NAMES.get(tool, tool)
        if tool == "polygon":
            msg = f"● Formas ({nome}) — clique nos vértices e pressione Enter"
        else:
            msg = f"● Formas ({nome}) — arraste do início ao fim"
        self.status_label.setText(msg)
        self.status_label.setStyleSheet("color: #9B59B6;")

    def _on_shape_draft_changed(self, n: int) -> None:
        """Contador de vértices do polígono em rascunho na barra de status."""
        if n > 0:
            self.status_label.setText(f"● Polígono: {n} vértice(s) — Enter finaliza")
            self.status_label.setStyleSheet("color: #9B59B6;")
        elif self.canvas.interaction_mode == "shape":
            self._show_shape_status(self.canvas.shape_tool)

    def _on_escape(self) -> None:
        """Esc cancela o retângulo de zoom E qualquer rascunho de caneta/forma."""
        self.canvas.cancel_rectangle()
        self.canvas.cancel_active_draft()

    def _uncheck_zoom_buttons(self) -> None:
        """Desativa botões de zoom/pan (exclusividade com desenho/anotação/etc.)."""
        for btn in (getattr(self, "zoom_area_btn", None), getattr(self, "pan_btn", None)):
            if btn is not None and btn.isChecked():
                btn.setChecked(False)
        self.canvas.set_zoom_area_mode(False)
        self.canvas.set_pan_mode(False)
        self._uncheck_sounding_button()

    def _uncheck_draw_buttons(self) -> None:
        """Desativa botões de desenho/anotação/régua/emoji ao entrar em zoom/pan."""
        for btn in (self.draw_mode_btn, self.annotate_btn, self.ruler_btn):
            if btn.isChecked():
                btn.setChecked(False)
        self.canvas.set_drawing_mode(False)
        self.canvas.set_annotation_mode(False)
        self.canvas.set_ruler_mode(False)
        self.canvas.set_emoji_mode(False)
        self.canvas.set_pen_mode(False)
        self.canvas.set_shape_mode(False)
        # Botões de emoji/caneta/formas vivem no painel de simbologias
        if hasattr(self.symbol_panel, "reset_emoji_mode"):
            self.symbol_panel.reset_emoji_mode()
        if hasattr(self.symbol_panel, "reset_pen_mode"):
            self.symbol_panel.reset_pen_mode()
        if hasattr(self.symbol_panel, "reset_shapes_mode"):
            self.symbol_panel.reset_shapes_mode()
        self._uncheck_sounding_button()

    def _uncheck_sounding_button(self) -> None:
        """Desativa as análises de clique (Sonda Vertical, Meteograma, Corte).

        Chamada pelos modos de desenho/zoom/pan ao ativar — garante exclusividade
        mútua entre TODAS as ferramentas de clique.
        """
        analysis = (
            ("sounding_mode_btn", self.canvas.set_sounding_mode),
            ("meteogram_mode_btn", self.canvas.set_meteogram_mode),
            ("xsec_mode_btn", self.canvas.set_cross_section_mode),
        )
        for attr, setter in analysis:
            btn = getattr(self, attr, None)
            if btn is not None and btn.isChecked():
                btn.setChecked(False)
            setter(False)

    def _disable_other_click_modes(self, keep: str = "") -> None:
        """Desliga TODOS os modos de clique exceto ``keep`` (desenho/zoom/pan/análises).

        ``keep`` ∈ {"sounding", "meteogram", "cross_section"} para preservar a
        análise que está sendo ativada.
        """
        # Desenho/anotação/régua/emoji/caneta/formas.
        for btn in (self.draw_mode_btn, self.annotate_btn, self.ruler_btn):
            if btn.isChecked():
                btn.setChecked(False)
        self.canvas.set_drawing_mode(False)
        self.canvas.set_annotation_mode(False)
        self.canvas.set_ruler_mode(False)
        self.canvas.set_emoji_mode(False)
        self.canvas.set_pen_mode(False)
        self.canvas.set_shape_mode(False)
        for reset in ("reset_emoji_mode", "reset_pen_mode", "reset_shapes_mode"):
            if hasattr(self.symbol_panel, reset):
                getattr(self.symbol_panel, reset)()
        # Zoom/pan.
        for btn in (getattr(self, "zoom_area_btn", None), getattr(self, "pan_btn", None)):
            if btn is not None and btn.isChecked():
                btn.setChecked(False)
        self.canvas.set_zoom_area_mode(False)
        self.canvas.set_pan_mode(False)
        # Análises docadas (sonda/meteograma/corte) — menos a mantida.
        analysis = {
            "sounding": ("sounding_mode_btn", self.canvas.set_sounding_mode),
            "meteogram": ("meteogram_mode_btn", self.canvas.set_meteogram_mode),
            "cross_section": ("xsec_mode_btn", self.canvas.set_cross_section_mode),
        }
        for key, (attr, setter) in analysis.items():
            if key == keep:
                continue
            btn = getattr(self, attr, None)
            if btn is not None and btn.isChecked():
                btn.setChecked(False)
            setter(False)

    def _toggle_zoom_area_mode(self, checked: bool) -> None:
        if checked:
            self.pan_btn.setChecked(False)
            self.canvas.set_pan_mode(False)
            self._uncheck_draw_buttons()
            self.canvas.set_zoom_area_mode(True)
            self.status_label.setText("● Zoom área — desenhe um retângulo para recortar e replotar")
            self.status_label.setStyleSheet("color: #8E44AD;")
        else:
            self.canvas.set_zoom_area_mode(False)
            self.status_label.setText("● Pronto")
            self.status_label.setStyleSheet("color: #27AE60;")

    def _toggle_pan_mode(self, checked: bool) -> None:
        if checked:
            self.zoom_area_btn.setChecked(False)
            self.canvas.set_zoom_area_mode(False)
            self._uncheck_draw_buttons()
            self.canvas.set_pan_mode(True)
            self.status_label.setText("● Mover — arraste o mapa (roda do mouse = zoom)")
            self.status_label.setStyleSheet("color: #2C3E50;")
        else:
            self.canvas.set_pan_mode(False)
            self.status_label.setText("● Pronto")
            self.status_label.setStyleSheet("color: #27AE60;")

    def _toggle_sounding_mode(self, checked: bool) -> None:
        """Liga/desliga a Sonda Vertical, exclusiva dos demais modos de clique."""
        if checked:
            self._disable_other_click_modes(keep="sounding")
            self.canvas.set_sounding_mode(True)
            # Mostra o painel já na ativação: o seletor de Fonte (Observada × Modelo)
            # precisa estar acessível ANTES do primeiro clique.
            self.sounding_panel.show()
            self.sounding_panel.raise_()
            self.status_label.setText(
                "● Sonda Vertical — escolha a Fonte (Observada/Modelo) e clique no mapa"
            )
            self.status_label.setStyleSheet("color: #16A085;")
        else:
            self.canvas.set_sounding_mode(False)
            self.status_label.setText("● Pronto")
            self.status_label.setStyleSheet("color: #27AE60;")

    def _toggle_meteogram_mode(self, checked: bool) -> None:
        """Liga/desliga o Meteograma (F6), exclusivo dos demais modos de clique."""
        if checked:
            self._disable_other_click_modes(keep="meteogram")
            self.canvas.set_meteogram_mode(True)
            self.meteogram_panel.show()
            self.meteogram_panel.raise_()
            self.status_label.setText(
                "● Meteograma — clique num ponto do mapa para a série do modelo (+0…+72h)"
            )
            self.status_label.setStyleSheet("color: #117A65;")
        else:
            self.canvas.set_meteogram_mode(False)
            self.status_label.setText("● Pronto")
            self.status_label.setStyleSheet("color: #27AE60;")

    def _toggle_xsec_mode(self, checked: bool) -> None:
        """Liga/desliga o Corte Vertical (F4), exclusivo dos demais modos de clique."""
        if checked:
            self._disable_other_click_modes(keep="cross_section")
            self.canvas.set_cross_section_mode(True)
            self.status_label.setText("● Corte Vertical — clique em DOIS pontos do mapa (A → B)")
            self.status_label.setStyleSheet("color: #8E44AD;")
        else:
            self.canvas.set_cross_section_mode(False)
            self.status_label.setText("● Pronto")
            self.status_label.setStyleSheet("color: #27AE60;")

    # ═══════════════════════════════════════════════════════════════════════
    #  SONDA VERTICAL (RADIOSSONDAGEM / SKEW-T)
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _nearest_synoptic(dt):
        """Arredonda um datetime UTC para o horário sinótico (00Z/12Z) mais próximo.

        A radiossonda só é lançada às 00Z e 12Z; o painel busca a sondagem do
        horário sinótico mais próximo do valid_time do modelo.
        """
        from datetime import timedelta

        base = dt.replace(minute=0, second=0, microsecond=0)
        # Candidatos: 00Z e 12Z do dia, e 00Z do dia seguinte (para horas ≥ 18Z)
        candidates = [
            base.replace(hour=0),
            base.replace(hour=12),
            base.replace(hour=0) + timedelta(days=1),
        ]
        return min(candidates, key=lambda c: abs((dt - c).total_seconds()))

    def _on_sounding_station_selected(self, station: dict) -> None:
        """Clique ancorado numa estação RAOB → abre o painel e dispara o download."""
        self._active_sounding_station = station
        self.sounding_panel.show()
        self.sounding_panel.raise_()
        self._launch_sounding()

    def _on_sounding_step_changed(self) -> None:
        """Step mudou → recarrega a sondagem ativa (e o corte vertical, se houver)."""
        # Corte Vertical (F4): re-dispara ao mudar step/rodada se A→B está ativo.
        if self.cross_section_panel.isVisible() and self._active_xsec is not None:
            self._launch_cross_section()
        if not self.sounding_panel.isVisible():
            return
        if self.canvas.sounding_source == "model":
            if self._active_model_point is not None:
                self._launch_model_sounding()
        elif self._active_sounding_station is not None:
            self._launch_sounding()

    def _launch_sounding(self) -> None:
        """Calcula o horário-alvo, blinda o caso futuro e dispara o SoundingWorker."""
        from datetime import datetime

        station = self._active_sounding_station
        if station is None:
            return

        # Sem rodada carregada ainda: usa o sinótico mais próximo de "agora".
        base_time = self._last_valid_time or datetime.now(UTC)
        target = self._nearest_synoptic(base_time)
        station_label = f"{station.get('name', station['wmo'])} ({station['wmo']})"
        time_label = target.strftime("%d/%m/%Y %H:%MZ")

        # Fail-state "viagem ao futuro": o balão ainda não foi lançado.
        # NÃO toca no servidor de Wyoming.
        now = datetime.now(UTC)
        if target > now:
            self.sounding_panel.show_future(time_label)
            return

        # Evita corridas: se já há um worker rodando, deixa-o terminar.
        if self.sounding_worker is not None and self.sounding_worker.isRunning():
            return

        self.sounding_panel.show_loading(station_label, time_label)
        self.status_label.setText(f"● Baixando sondagem de {station_label}...")
        self.status_label.setStyleSheet("color: #16A085;")

        worker = SoundingWorker(station=station, target_time=target, parent=self)
        worker.progress.connect(self._on_sounding_progress)
        worker.finished_ok.connect(self._on_sounding_ok)
        worker.finished_error.connect(self._on_sounding_error)
        self.sounding_worker = worker
        worker.start()

    def _on_sounding_progress(self, msg: str) -> None:
        self.status_label.setText(f"● {msg}")

    def _on_sounding_ok(self, result) -> None:
        self.sounding_panel.render(result)
        self.status_label.setText("● Pronto")
        self.status_label.setStyleSheet("color: #27AE60;")

    def _on_sounding_error(self, msg: str) -> None:
        self.sounding_panel.show_error(msg)
        self.status_label.setText("● Sondagem indisponível")
        self.status_label.setStyleSheet("color: #E67E22;")

    # ── Pseudo-sondagem do MODELO (IFS) — F1 ─────────────────────────────────
    def _on_sounding_source_changed(self, source: str) -> None:
        """Combo de Fonte do painel → fonte de clique do canvas (observed | model)."""
        self.canvas.sounding_source = source

    def _on_model_sounding_point(self, lon: float, lat: float) -> None:
        """Clique no modo Sonda+Modelo → abre o painel e dispara a pseudo-sondagem."""
        self._active_model_point = (float(lon), float(lat))
        self._active_sounding_station = None  # alterna para o modo modelo
        self.sounding_panel.show()
        self.sounding_panel.raise_()
        self._launch_model_sounding()

    def _launch_model_sounding(self) -> None:
        """Dispara o ModelSoundingWorker no ponto ativo usando a rodada/step atuais.

        Diferente do Wyoming, usa o STEP EXATO (modo previsão) e qualquer ponto.
        Baixa a coluna do IFS em thread (cache-first) — a GUI nunca trava.
        """
        from datetime import datetime

        if self._active_model_point is None:
            return
        lon, lat = self._active_model_point
        cycle = self.settings_panel.get_cycle()
        cycle_date = self.settings_panel.get_cycle_date()
        step = self.settings_panel.get_step() or 0

        target = self._last_valid_time or datetime.now(UTC)
        ns = "N" if lat >= 0 else "S"
        ew = "E" if lon >= 0 else "W"
        label = f"Modelo IFS — {abs(lat):.1f}°{ns} {abs(lon):.1f}°{ew}"
        time_label = target.strftime("%d/%m/%Y %H:%MZ")

        if self.sounding_worker is not None and self.sounding_worker.isRunning():
            return

        self.sounding_panel.show_loading(label, time_label)
        self.status_label.setText(f"● Extraindo perfil do modelo em {label}...")
        self.status_label.setStyleSheet("color: #16A085;")

        worker = ModelSoundingWorker(
            lon=lon,
            lat=lat,
            target_time=target,
            cycle=cycle,
            # Mesma subpasta dos demais GRIBs (config.grib_dir) — nada solto na
            # raiz nem arquivo duplicado; download_ecmwf reusa o cache se já existe.
            cycle_date=cycle_date,
            step=step,
            data_dir=self.config.grib_dir,
            parent=self,
        )
        worker.progress.connect(self._on_sounding_progress)
        worker.finished_ok.connect(self._on_sounding_ok)
        worker.finished_error.connect(self._on_sounding_error)
        self.sounding_worker = worker
        worker.start()

    # ── Meteograma (série temporal num ponto) — F6 ───────────────────────────
    def _on_meteogram_point(self, lon: float, lat: float) -> None:
        """Clique no modo Meteograma → abre o painel e dispara a série temporal."""
        self._active_meteogram_point = (float(lon), float(lat))
        self.meteogram_panel.show()
        self.meteogram_panel.raise_()
        self._launch_meteogram()

    def _launch_meteogram(self) -> None:
        """Dispara o MeteogramWorker no ponto ativo usando a rodada atual.

        A série é multi-step (+0…+72h): baixa step a step em thread (serializado,
        cache-first). Diferente da sonda, NÃO re-dispara ao mudar o step — o eixo
        já É o tempo; o usuário re-clica para refletir outra rodada.
        """
        if self._active_meteogram_point is None:
            return
        lon, lat = self._active_meteogram_point
        cycle = self.settings_panel.get_cycle()
        cycle_date = self.settings_panel.get_cycle_date()
        ns = "N" if lat >= 0 else "S"
        ew = "E" if lon >= 0 else "W"
        label = f"Modelo IFS — {abs(lat):.1f}°{ns} {abs(lon):.1f}°{ew}"

        if self._meteogram_worker is not None and self._meteogram_worker.isRunning():
            return

        self.meteogram_panel.show_loading(label, "+0…+72h")
        self.status_label.setText(f"● Meteograma em {label} — baixando série…")
        self.status_label.setStyleSheet("color: #117A65;")

        worker = MeteogramWorker(
            lon=lon,
            lat=lat,
            cycle=cycle,
            cycle_date=cycle_date,
            # Mesma subpasta dos demais GRIBs — nada solto/duplicado na raiz.
            data_dir=self.config.grib_dir,
            parent=self,
        )
        worker.progress.connect(self._on_meteogram_progress)
        worker.finished_ok.connect(self._on_meteogram_ok)
        worker.finished_error.connect(self._on_meteogram_error)
        self._meteogram_worker = worker
        worker.start()

    def _on_meteogram_progress(self, msg: str) -> None:
        self.status_label.setText(f"● {msg}")
        self.status_label.setStyleSheet("color: #117A65;")

    def _on_meteogram_ok(self, ts) -> None:
        self.meteogram_panel.render(ts)
        self.status_label.setText("● Meteograma pronto")
        self.status_label.setStyleSheet("color: #27AE60;")

    def _on_meteogram_error(self, msg: str) -> None:
        self.meteogram_panel.show_error(msg)
        self.status_label.setText("● Meteograma indisponível")
        self.status_label.setStyleSheet("color: #E74C3C;")

    # ── Corte Vertical (cross-section A→B) — F4 ──────────────────────────────
    def _on_cross_section_request(
        self, lon_a: float, lat_a: float, lon_b: float, lat_b: float
    ) -> None:
        """Segundo clique (B) → abre o painel e dispara o corte vertical A→B."""
        self._active_xsec = (float(lon_a), float(lat_a), float(lon_b), float(lat_b))
        self.cross_section_panel.show()
        self.cross_section_panel.raise_()
        self._launch_cross_section()

    def _launch_cross_section(self) -> None:
        """Dispara o CrossSectionWorker na reta ativa usando a rodada/step atuais."""
        if self._active_xsec is None:
            return
        lon_a, lat_a, lon_b, lat_b = self._active_xsec
        cycle = self.settings_panel.get_cycle()
        cycle_date = self.settings_panel.get_cycle_date()
        step = self.settings_panel.get_step() or 0

        if self._xsec_worker is not None and self._xsec_worker.isRunning():
            return

        self.cross_section_panel.show_loading("Corte Vertical A→B", f"+{step}h")
        self.status_label.setText("● Corte vertical — baixando e interpolando…")
        self.status_label.setStyleSheet("color: #8E44AD;")

        worker = CrossSectionWorker(
            lon_a=lon_a,
            lat_a=lat_a,
            lon_b=lon_b,
            lat_b=lat_b,
            step=step,
            cycle=cycle,
            cycle_date=cycle_date,
            # Mesma subpasta dos demais GRIBs — nada solto/duplicado na raiz.
            data_dir=self.config.grib_dir,
            parent=self,
        )
        worker.progress.connect(self._on_xsec_progress)
        worker.finished_ok.connect(self._on_xsec_ok)
        worker.finished_error.connect(self._on_xsec_error)
        self._xsec_worker = worker
        worker.start()

    def _on_xsec_progress(self, msg: str) -> None:
        self.status_label.setText(f"● {msg}")
        self.status_label.setStyleSheet("color: #8E44AD;")

    def _on_xsec_ok(self, xs) -> None:
        self.cross_section_panel.render(xs)
        self.status_label.setText("● Corte vertical pronto")
        self.status_label.setStyleSheet("color: #27AE60;")

    def _on_xsec_error(self, msg: str) -> None:
        self.cross_section_panel.show_error(msg)
        self.status_label.setText("● Corte vertical indisponível")
        self.status_label.setStyleSheet("color: #E74C3C;")

    # ── Campos de instabilidade (CAPE/CIN/LI/K) — F9 ─────────────────────────
    def _launch_instability(self, indices) -> None:
        """Dispara o InstabilityWorker sobre o extent atual (rodada/step atuais).

        CPU pesada (ascensão de parcela no CAPE/LI) → roda em thread com barra de
        progresso; a GUI nunca congela. Os campos voltam como camadas PL.
        """
        if self._instability_worker is not None and self._instability_worker.isRunning():
            return
        cycle = self.settings_panel.get_cycle()
        cycle_date = self.settings_panel.get_cycle_date()
        step = self.settings_panel.get_step() or 0
        extent = list(self.config.extent)

        self.progress_bar.setVisible(True)
        self.status_label.setText("● Instabilidade — baixando e calculando (pode demorar)…")
        self.status_label.setStyleSheet("color: #C0392B;")

        worker = InstabilityWorker(
            extent=extent,
            step=step,
            cycle=cycle,
            cycle_date=cycle_date,
            # Mesma subpasta dos demais GRIBs — nada solto/duplicado na raiz.
            data_dir=self.config.grib_dir,
            indices=indices,
            stride=4,
            parent=self,
        )
        worker.progress.connect(self._on_instability_progress)
        worker.finished_ok.connect(self._on_instability_ok)
        worker.finished_error.connect(self._on_instability_error)
        self._instability_worker = worker
        worker.start()

    def _on_instability_progress(self, msg: str) -> None:
        self.status_label.setText(f"● {msg}")
        self.status_label.setStyleSheet("color: #C0392B;")

    def _on_instability_ok(self, fields) -> None:
        self.progress_bar.setVisible(False)
        for layer_id, data in fields.items():
            self.field_panel.remove_layer_entry(layer_id)
            self.canvas.add_pl_layer(layer_id, data)
            var_info = VARIABLE_REGISTRY.get(data.variable, {})
            label = var_info.get("nome", data.variable)
            self.field_panel.add_layer_entry(layer_id, label, data.unit)
        self.status_label.setText("● Instabilidade carregada")
        self.status_label.setStyleSheet("color: #27AE60;")

    def _on_instability_error(self, msg: str) -> None:
        self.progress_bar.setVisible(False)
        self.status_label.setText("● Instabilidade indisponível")
        self.status_label.setStyleSheet("color: #E74C3C;")
        QMessageBox.warning(self, "Instabilidade", msg)

    def _setup_zoom_shortcuts(self) -> None:
        """Atalhos de zoom sem colisão (Ctrl+Z/Ctrl+Y reservados ao desenho)."""
        from PyQt6.QtGui import QShortcut

        home = QShortcut(QKeySequence("Home"), self)
        home.activated.connect(self._on_home_extent)
        # Ctrl+0 = Ajustar a FIGURA (convenção de visualizador); reset geográfico = Home / 🏠
        ctrl0 = QShortcut(QKeySequence("Ctrl+0"), self)
        ctrl0.activated.connect(self._figure_zoom_fit)
        esc = QShortcut(QKeySequence("Esc"), self)
        esc.activated.connect(self._on_escape)

    def _on_previous_extent(self) -> None:
        self.canvas.previous_extent()

    def _on_home_extent(self) -> None:
        """Reseta para o extent padrão da região atualmente selecionada."""
        region = self.settings_panel.region_combo.currentText()
        default = self.settings_panel.REGIONS.get(region)
        if default is not None:
            self.canvas.apply_extent(list(default), push_history=True)

    # ═══════════════════════════════════════════════════════════════════════
    #  ZOOM DA FIGURA ("mesa branca") — documento, distinto do zoom geográfico
    # ═══════════════════════════════════════════════════════════════════════

    _FIG_ZOOM_STEP = 1.25
    _FIG_ZOOM_MAX = 5.0

    def _on_figure_zoom_step(self, direction: int) -> None:
        """Ctrl+roda: amplia (+1) ou reduz (-1) a figura inteira."""
        factor = self._FIG_ZOOM_STEP if direction > 0 else 1.0 / self._FIG_ZOOM_STEP
        self._set_figure_zoom(self._fig_zoom * factor)

    def _figure_zoom_in(self) -> None:
        self._set_figure_zoom(self._fig_zoom * self._FIG_ZOOM_STEP)

    def _figure_zoom_out(self) -> None:
        self._set_figure_zoom(self._fig_zoom / self._FIG_ZOOM_STEP)

    def _figure_zoom_fit(self) -> None:
        """Volta a 100% — a figura volta a preencher o viewport (Ajustar)."""
        self._set_figure_zoom(1.0)

    def _set_figure_zoom(self, zoom: float) -> None:
        """Aplica o zoom de figura redimensionando o canvas dentro do QScrollArea.

        Como as posições dos eixos/colorbars são FRAÇÃO da figura, escalar o
        canvas uniformemente amplia tudo junto, sem reflow. 1.0 = ajuste (fit).
        """
        zoom = max(1.0, min(self._FIG_ZOOM_MAX, zoom))
        self._fig_zoom = zoom
        if zoom <= 1.0001:
            # Fit: o canvas volta a acompanhar o viewport
            self.canvas.setMinimumSize(0, 0)
            self.canvas.setMaximumSize(16777215, 16777215)  # QWIDGETSIZE_MAX
            self.canvas_scroll.setWidgetResizable(True)
            self._fig_base_size = None
        else:
            # Base FIXA = tamanho de ajuste (viewport cheio), capturada ao sair do
            # fit — evita que a base "encolha" quando as scrollbars aparecem.
            if self._fig_base_size is None:
                self._fig_base_size = self.canvas_scroll.viewport().size()
            self.canvas_scroll.setWidgetResizable(False)
            b = self._fig_base_size
            self.canvas.setFixedSize(int(b.width() * zoom), int(b.height() * zoom))
            # Mantém o centro da carta em vista ao ampliar
            for bar in (
                self.canvas_scroll.horizontalScrollBar(),
                self.canvas_scroll.verticalScrollBar(),
            ):
                bar.setValue((bar.minimum() + bar.maximum()) // 2)
        if hasattr(self, "_zoom_label"):
            self._zoom_label.setText(f"{int(round(zoom * 100))}%")

    def eventFilter(self, obj, event):
        """Pan da mesa com o botão do MEIO (quando a figura está ampliada)."""
        if getattr(self, "canvas", None) is not None and obj is self.canvas:
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.MiddleButton:
                self._mesa_pan_active = True
                self._mesa_pan_origin = (
                    event.position(),
                    self.canvas_scroll.horizontalScrollBar().value(),
                    self.canvas_scroll.verticalScrollBar().value(),
                )
                self.canvas_scroll.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                return True
            if et == QEvent.Type.MouseMove and self._mesa_pan_active and self._mesa_pan_origin:
                pos0, h0, v0 = self._mesa_pan_origin
                dx = event.position().x() - pos0.x()
                dy = event.position().y() - pos0.y()
                self.canvas_scroll.horizontalScrollBar().setValue(int(h0 - dx))
                self.canvas_scroll.verticalScrollBar().setValue(int(v0 - dy))
                return True
            if (
                et == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.MiddleButton
            ):
                self._mesa_pan_active = False
                self._mesa_pan_origin = None
                self.canvas_scroll.viewport().unsetCursor()
                return True
        return super().eventFilter(obj, event)

    def _on_extent_changed(self, extent: list) -> None:
        """Sincroniza a GUI após zoom-área/recorte: atualiza spinboxes e observações."""
        sp = self.settings_panel
        for spin, val in (
            (sp.lon_min, extent[0]),
            (sp.lat_min, extent[1]),
            (sp.lon_max, extent[2]),
            (sp.lat_max, extent[3]),
        ):
            spin.blockSignals(True)
            spin.setValue(int(round(val)))
            spin.blockSignals(False)
        self.config.extent = list(extent)
        # Re-thinning das observações ativas para o novo domínio
        self._refresh_active_observations()

    def _on_annotation_requested(self, x, y):
        """Abre diálogo para o usuário digitar texto da anotação."""
        from PyQt6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(
            self,
            "Anotação no Mapa",
            "Texto da anotação:",
        )
        if ok and text.strip():
            self.canvas.add_annotation(x, y, text.strip())

    # ═══════════════════════════════════════════════════════════════════════
    #  HANDLERS PARA SATÉLITE GOES
    # ═══════════════════════════════════════════════════════════════════════

    def _on_sat_download(self, target_time):
        """Inicia download de imagem GOES-East."""
        if self.sat_download_thread and self.sat_download_thread.isRunning():
            return

        self.satellite_panel.set_downloading(True)
        self.status_label.setText(
            f"● Baixando GOES-East para {target_time.strftime('%d/%m/%Y %HZ')}..."
        )
        self.status_label.setStyleSheet("color: #1ABC9C;")

        self._sat_dl_dialog = DownloadProgressDialog(
            f"Baixando GOES-East — {target_time.strftime('%d/%m/%Y %HZ')}",
            parent=self,
        )
        self._sat_dl_dialog.setStyleSheet(DARK_STYLE)
        self._sat_dl_dialog.cancel_requested.connect(self._cancel_sat_download)

        self.sat_download_thread = SatDownloadThread(
            config=self.config,
            target_time=target_time,
            parent=self,
        )
        self.sat_download_thread.progress.connect(lambda msg: self._update_sat_dialog_status(msg))
        self.sat_download_thread.download_percent.connect(self._sat_dl_dialog.update_percent)
        self.sat_download_thread.cache_hit.connect(self._on_sat_cache_hit)
        self.sat_download_thread.finished_ok.connect(self._on_sat_download_ok)
        self.sat_download_thread.finished_error.connect(self._on_sat_download_error)
        self.sat_download_thread.start()

        self._sat_dl_dialog.show()

    def _update_sat_dialog_status(self, msg):
        self.status_label.setText(f"● {msg}")
        if hasattr(self, "_sat_dl_dialog") and self._sat_dl_dialog:
            self._sat_dl_dialog.update_status(msg)

    def _on_sat_cache_hit(self, filename):
        if hasattr(self, "_sat_dl_dialog") and self._sat_dl_dialog:
            self._sat_dl_dialog.update_status(f"✓ Arquivo já existe no cache!\n{filename}")
            self._sat_dl_dialog.update_percent(100)

    def _cancel_sat_download(self):
        if self.sat_download_thread and self.sat_download_thread.isRunning():
            self.sat_download_thread.terminate()
            self.sat_download_thread.wait(3000)
            self.sat_download_thread = None

        self.satellite_panel.set_downloading(False)

        if hasattr(self, "_sat_dl_dialog") and self._sat_dl_dialog:
            self._sat_dl_dialog.accept()
            self._sat_dl_dialog = None

        self.status_label.setText("● Download do satélite cancelado")
        self.status_label.setStyleSheet("color: #F39C12;")

    def _on_sat_download_ok(self, sat_data):
        self.satellite_panel.set_downloading(False)
        self.satellite_panel.set_loaded(sat_data.time_str)
        self.canvas.plot_satellite(sat_data)
        self.status_label.setText(f"● Satélite: {sat_data.time_str}")
        self.status_label.setStyleSheet("color: #27AE60;")

        if hasattr(self, "_sat_dl_dialog") and self._sat_dl_dialog:
            self._sat_dl_dialog.finish_ok()
            self._sat_dl_dialog = None

    def _on_sat_download_error(self, error_msg):
        self.satellite_panel.set_downloading(False)
        self.status_label.setText("● Erro no download do satélite")
        self.status_label.setStyleSheet("color: #E74C3C;")

        if hasattr(self, "_sat_dl_dialog") and self._sat_dl_dialog:
            self._sat_dl_dialog.finish_error()
            self._sat_dl_dialog = None

        QMessageBox.warning(self, "Erro GOES", error_msg)

    # ═══════════════════════════════════════════════════════════════════════
    #  HANDLERS PARA TSM (MUR SST)
    # ═══════════════════════════════════════════════════════════════════════

    def _on_sst_download(self, target_date):
        """Inicia download de dados MUR SST."""
        if self.sst_download_thread and self.sst_download_thread.isRunning():
            return

        self.sst_panel.set_downloading(True)
        date_str = target_date.strftime("%d/%m/%Y")
        self.status_label.setText(f"● Baixando TSM — MUR SST para {date_str}...")
        self.status_label.setStyleSheet("color: #E67E22;")

        self._sst_dl_dialog = DownloadProgressDialog(
            f"Baixando TSM — MUR SST {date_str}",
            parent=self,
        )
        self._sst_dl_dialog.setStyleSheet(DARK_STYLE)
        self._sst_dl_dialog.cancel_requested.connect(self._cancel_sst_download)

        self.sst_download_thread = SSTDownloadThread(
            config=self.config,
            target_date=target_date,
            parent=self,
        )
        self.sst_download_thread.progress.connect(lambda msg: self._update_sst_dialog_status(msg))
        self.sst_download_thread.download_percent.connect(self._sst_dl_dialog.update_percent)
        self.sst_download_thread.finished_ok.connect(self._on_sst_download_ok)
        self.sst_download_thread.finished_error.connect(self._on_sst_download_error)
        self.sst_download_thread.start()

        self._sst_dl_dialog.show()

    def _update_sst_dialog_status(self, msg):
        self.status_label.setText(f"● {msg}")
        if hasattr(self, "_sst_dl_dialog") and self._sst_dl_dialog:
            self._sst_dl_dialog.update_status(msg)

    def _cancel_sst_download(self):
        if self.sst_download_thread and self.sst_download_thread.isRunning():
            self.sst_download_thread.terminate()
            self.sst_download_thread.wait(3000)
            self.sst_download_thread = None

        self.sst_panel.set_downloading(False)

        if hasattr(self, "_sst_dl_dialog") and self._sst_dl_dialog:
            self._sst_dl_dialog.accept()
            self._sst_dl_dialog = None

        self.status_label.setText("● Download TSM cancelado")
        self.status_label.setStyleSheet("color: #F39C12;")

    def _on_sst_download_ok(self, sst_data):
        self.sst_panel.set_downloading(False)
        self.sst_panel.set_loaded(sst_data.time_str)
        self.canvas.plot_sst(sst_data)
        self.status_label.setText(f"● TSM: {sst_data.time_str}")
        self.status_label.setStyleSheet("color: #27AE60;")

        if hasattr(self, "_sst_dl_dialog") and self._sst_dl_dialog:
            self._sst_dl_dialog.finish_ok()
            self._sst_dl_dialog = None

    def _on_sst_download_error(self, error_msg):
        self.sst_panel.set_downloading(False)
        self.status_label.setText("● Erro no download da TSM")
        self.status_label.setStyleSheet("color: #E74C3C;")

        if hasattr(self, "_sst_dl_dialog") and self._sst_dl_dialog:
            self._sst_dl_dialog.finish_error()
            self._sst_dl_dialog = None

        QMessageBox.warning(self, "Erro TSM", error_msg)

    # ═══════════════════════════════════════════════════════════════════════
    #  OBSERVAÇÕES DE SUPERFÍCIE (SYNOP / METAR)
    # ═══════════════════════════════════════════════════════════════════════

    def _store_valid_time(self, data) -> None:
        """Guarda o valid_time do modelo (datetime UTC) para sincronizar obs."""
        from datetime import datetime

        vt = getattr(data, "valid_time", None)
        self._last_valid_time = None
        if vt:
            for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
                try:
                    self._last_valid_time = datetime.strptime(vt, fmt).replace(tzinfo=UTC)
                    break
                except (ValueError, TypeError):
                    continue
        # Atualiza o rótulo de horários de observação no painel
        self.settings_panel.set_obs_reference_time(self._last_valid_time)

    def _on_observations_toggled(self, kind: str, enabled: bool) -> None:
        """Liga/desliga um overlay de observação (re-renderiza só o overlay)."""
        if not enabled:
            self.canvas.remove_stations(kind)
            self.canvas.draw()
            return
        self._start_station_download(
            want_metar=(kind == "metar"),
            want_synop=(kind == "synop"),
        )

    def _refresh_active_observations(self) -> None:
        """Recarrega os overlays ativos para sincronizar com o novo valid_time."""
        obs = self.settings_panel.get_observations()
        if obs.get("metar") or obs.get("synop"):
            self._start_station_download(
                want_metar=obs.get("metar", False),
                want_synop=obs.get("synop", False),
            )

    def _start_station_download(self, want_metar: bool, want_synop: bool) -> None:
        """Inicia o download de observações em thread, sincronizado ao valid_time."""
        if not (want_metar or want_synop):
            return
        if self.station_download_thread and self.station_download_thread.isRunning():
            return

        # Mantém o extent atual no config
        self.config.extent = self.settings_panel.get_extent()

        kinds = []
        if want_metar:
            kinds.append("METAR")
        if want_synop:
            kinds.append("SYNOP")
        title = "Baixando " + " + ".join(kinds)

        self.status_label.setText(f"● {title}...")
        self.status_label.setStyleSheet("color: #E67E22;")

        # Diálogo de progresso indeterminado (a tela não fica em branco sem aviso)
        self._obs_dl_dialog = DownloadProgressDialog(title, parent=self)
        self._obs_dl_dialog.setStyleSheet(DARK_STYLE)
        self._obs_dl_dialog.set_indeterminate()
        self._obs_dl_dialog.update_status("Conectando aos servidores de observação...")
        self._obs_dl_dialog.cancel_btn.setEnabled(False)  # fetch curto; sem cancelamento

        self.station_download_thread = StationDownloadThread(
            config=self.config,
            want_metar=want_metar,
            want_synop=want_synop,
            target_time=self._last_valid_time,
            parent=self,
        )
        self.station_download_thread.progress.connect(self._on_stations_progress)
        self.station_download_thread.finished_ok.connect(self._on_stations_ok)
        self.station_download_thread.finished_error.connect(self._on_stations_error)
        self.station_download_thread.start()

        self._obs_dl_dialog.show()

    def _on_stations_progress(self, msg: str) -> None:
        self.status_label.setText(f"● {msg}")
        if getattr(self, "_obs_dl_dialog", None):
            self._obs_dl_dialog.update_status(msg)

    def _close_obs_dialog(self) -> None:
        if getattr(self, "_obs_dl_dialog", None):
            self._obs_dl_dialog.finish_ok()
            self._obs_dl_dialog = None

    def _on_stations_ok(self, result: dict) -> None:
        self.station_download_thread = None
        self._close_obs_dialog()
        counts = []
        empty_kinds = []
        for kind in ("metar", "synop"):
            df = result.get(kind)
            if df is None:
                continue
            self.canvas.plot_stations(df, kind=kind)
            counts.append(f"{kind.upper()}: {len(df)}")
            if len(df) == 0:
                empty_kinds.append(kind.upper())

        # Aviso discreto na barra de status (sem popup repetitivo)
        if empty_kinds:
            self.status_label.setText(
                "● Sem estações "
                + "/".join(empty_kinds)
                + " para esta região/horário — tente outra região ou rodada"
            )
            self.status_label.setStyleSheet("color: #F39C12;")
        elif counts:
            self.status_label.setText("● Observações: " + " | ".join(counts))
            self.status_label.setStyleSheet("color: #27AE60;")

    def _on_stations_error(self, error_msg: str) -> None:
        self.station_download_thread = None
        if getattr(self, "_obs_dl_dialog", None):
            self._obs_dl_dialog.finish_error()
            self._obs_dl_dialog = None
        self.status_label.setText("● Erro nas observações")
        self.status_label.setStyleSheet("color: #E74C3C;")
        QMessageBox.warning(self, "Erro nas observações", error_msg)

    # ═══════════════════════════════════════════════════════════════════════
    #  HANDLERS DE DESENHO
    # ═══════════════════════════════════════════════════════════════════════

    def _on_point_added(self, x, y):
        self.symbol_panel.update_points(len(self.canvas.points_x))

    def _on_coords_updated(self, x, y):
        self.coords_label.setText(f"Lat: {y:.2f}° Lon: {x:.2f}°")

    def _finalize_line(self):
        # No modo formas, Enter fecha o polígono em rascunho (≥3 vértices)
        if self.canvas.interaction_mode == "shape" and self.canvas.shape_tool == "polygon":
            self.canvas.finalize_shape()
            return
        self.canvas.finalize_line()
        self.symbol_panel.update_points(0)

    # ═══════════════════════════════════════════════════════════════════════
    #  CONFIGURAÇÃO (região, tema, camadas)
    # ═══════════════════════════════════════════════════════════════════════

    def _on_region_changed(self, extent):
        self.config.extent = extent
        self.canvas.config.extent = extent

        for lid in list(self.field_panel._layer_widgets.keys()):
            self.field_panel.remove_layer_entry(lid)

        self.canvas._setup_base_map()
        self.status_label.setText("● Região alterada — dados limpos")
        self.status_label.setStyleSheet("color: #F39C12;")

    def _on_theme_changed(self, theme_name: str):
        for lid in list(self.field_panel._layer_widgets.keys()):
            self.field_panel.remove_layer_entry(lid)

        self.canvas.set_theme(theme_name)

    def _on_layer_toggled(self, layer_name: str, visible: bool):
        self.canvas.toggle_layer(layer_name, visible)

    # ═══════════════════════════════════════════════════════════════════════
    #  DOWNLOAD DE DADOS SINÓTICOS
    # ═══════════════════════════════════════════════════════════════════════

    def _download_data(self):
        """Baixa dados ECMWF em thread separada com diálogo de progresso."""
        if self.download_thread and self.download_thread.isRunning():
            return

        self.config.extent = self.settings_panel.get_extent()
        self.config.smoothing_sigma = self.settings_panel.get_smoothing()
        self.canvas.config = self.config

        step = self.settings_panel.get_step()
        cycle = self.settings_panel.get_cycle()
        cycle_date = self.settings_panel.get_cycle_date()

        self.settings_panel.set_downloading(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self._dl_existing_files = (
            set(self.config.grib_dir.glob("*.grib2")) if self.config.data_dir else set()
        )

        self._dl_dialog = DownloadProgressDialog("Baixando dados sinóticos", parent=self)
        self._dl_dialog.setStyleSheet(DARK_STYLE)
        self._dl_dialog.cancel_requested.connect(self._cancel_download)

        self.download_thread = DownloadThread(
            self.config, step, cycle, cycle_date=cycle_date, parent=self
        )
        self.download_thread.progress.connect(self._on_download_progress)
        self.download_thread.download_percent.connect(self._dl_dialog.update_percent)
        self.download_thread.finished_ok.connect(self._on_download_ok)
        self.download_thread.finished_error.connect(self._on_download_error)
        self.download_thread.start()

        self._dl_dialog.show()

    def _on_download_progress(self, msg):
        is_retry = "429" in msg or "tentativa" in msg or "Aguardando" in msg
        color = "#E74C3C" if is_retry else "#F39C12"
        self.status_label.setText(f"● {msg}")
        self.status_label.setStyleSheet(f"color: {color};")
        if hasattr(self, "_dl_dialog") and self._dl_dialog:
            self._dl_dialog.update_status(msg)

    def _on_download_ok(self, data):
        self.settings_panel.set_downloading(False)
        self.progress_bar.setVisible(False)

        if hasattr(self, "_dl_dialog") and self._dl_dialog:
            self._dl_dialog.finish_ok()
            self._dl_dialog = None

        self.canvas.set_synoptic_data(data)
        self.settings_panel.update_rodada_from_data(data)
        self._store_valid_time(data)
        self._refresh_active_observations()

        rodada_info = f"Rodada: {data.base_time}" if data.base_time else ""
        self.status_label.setText(
            f"● Dados carregados  |  {rodada_info}  |  "
            f"Válido: {data.valid_time} UTC  |  Step: +{data.step}h"
        )
        self.status_label.setStyleSheet("color: #27AE60;")

    def _on_download_error(self, error_msg):
        self.settings_panel.set_downloading(False)
        self.progress_bar.setVisible(False)

        if hasattr(self, "_dl_dialog") and self._dl_dialog:
            self._dl_dialog.finish_error()
            self._dl_dialog = None

        self.status_label.setText("● Erro no download")
        self.status_label.setStyleSheet("color: #E74C3C;")

        if "429" in error_msg or "Limite de requisições" in error_msg:
            QMessageBox.warning(self, "Limite de Requisições (429)", error_msg)
        else:
            QMessageBox.warning(self, "Erro ao Baixar Dados", error_msg)

    def _cancel_download(self):
        """Cancela download sinótico e limpa arquivos parciais."""
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.terminate()
            self.download_thread.wait(3000)
            self.download_thread = None

            gc.collect()

            existing = getattr(self, "_dl_existing_files", set())
            self._cleanup_new_files(existing)

        self.settings_panel.set_downloading(False)
        self.progress_bar.setVisible(False)

        if hasattr(self, "_dl_dialog") and self._dl_dialog:
            self._dl_dialog.accept()
            self._dl_dialog = None

        self.status_label.setText("● Download cancelado")
        self.status_label.setStyleSheet("color: #F39C12;")

    def _cancel_pl_download(self):
        """Cancela download PL/OLR e limpa arquivos parciais."""
        if self.pl_download_thread and self.pl_download_thread.isRunning():
            self.pl_download_thread.terminate()
            self.pl_download_thread.wait(3000)
            self.pl_download_thread = None

            gc.collect()

            existing = getattr(self, "_pl_existing_files", set())
            self._cleanup_new_files(existing)

        self.progress_bar.setVisible(False)

        if hasattr(self, "_pl_dl_dialog") and self._pl_dl_dialog:
            self._pl_dl_dialog.accept()
            self._pl_dl_dialog = None

        self.status_label.setText("● Download cancelado")
        self.status_label.setStyleSheet("color: #F39C12;")

    def _cleanup_new_files(self, existing_before: set):
        """Remove arquivos GRIB2 criados durante o download cancelado."""
        grib_dir = self.config.grib_dir
        if not grib_dir or not grib_dir.exists():
            return

        current = set(grib_dir.glob("*.grib2"))
        new_files = current - existing_before

        for f in new_files:
            try:
                f.unlink()
                logger.info("Arquivo parcial removido: %s", f.name)
            except PermissionError:
                logger.warning("Não foi possível remover (em uso): %s", f.name)
            except OSError as e:
                logger.warning("Erro ao remover %s: %s", f.name, e)

    # ═══════════════════════════════════════════════════════════════════════
    #  HANDLERS PARA CAMPOS EM ALTITUDE (PL / OLR)
    # ═══════════════════════════════════════════════════════════════════════

    def _on_add_pl_layer(self, var_key: str, level: int, wind_type: str):
        """Usuário clicou 'Adicionar camada' no FieldLayerPanel."""
        if self.pl_download_thread and self.pl_download_thread.isRunning():
            QMessageBox.information(
                self, "Aguarde", "Um download já está em andamento. Aguarde concluir."
            )
            return

        step = self.settings_panel.get_step()
        technique = self.field_panel.get_technique()

        # Variáveis desacumuláveis (OLR/precip) no modo Direto exigem step ≥ 3h.
        # No modo Estabilizada (Técnica B) o step 0 é permitido (usa rodada anterior).
        if var_key in ("olr", "precip") and technique == "direct" and step < 3:
            nome = VARIABLE_REGISTRY.get(var_key, {}).get("nome", var_key)
            QMessageBox.warning(
                self,
                f"{nome} no modo Direto requer step ≥ 3h",
                f"No modo Direto, {nome} usa a janela de 3h da rodada atual e "
                "vale zero no step 0.\n\n"
                "Use step +3h ou mais, OU mude o Método para "
                '"Estabilizada (mitiga spin-up)" para obter o campo já no '
                "horário da análise.",
            )
            return

        cycle = self.settings_panel.get_cycle()
        cycle_date = self.settings_panel.get_cycle_date()

        var_info = VARIABLE_REGISTRY.get(var_key, {})
        nome = var_info.get("nome", var_key)

        self.status_label.setText("● Baixando campo em altitude...")
        self.status_label.setStyleSheet("color: #9B59B6;")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self._pl_existing_files = (
            set(self.config.grib_dir.glob("*.grib2")) if self.config.data_dir else set()
        )

        self._pl_dl_dialog = DownloadProgressDialog(f"Baixando {nome}", parent=self)
        self._pl_dl_dialog.setStyleSheet(DARK_STYLE)
        self._pl_dl_dialog.cancel_requested.connect(self._cancel_pl_download)

        self.pl_download_thread = PLDownloadThread(
            variable_key=var_key,
            level=level,
            step=step,
            cycle=cycle,
            config=self.config,
            wind_type=wind_type,
            cycle_date=cycle_date,
            technique=technique,
            parent=self,
        )
        self.pl_download_thread.progress.connect(self._on_pl_progress)
        self.pl_download_thread.download_percent.connect(self._pl_dl_dialog.update_percent)
        self.pl_download_thread.finished_ok.connect(
            lambda lid, data, wt=wind_type: self._on_pl_download_ok(lid, data, wt)
        )
        self.pl_download_thread.finished_error.connect(self._on_pl_download_error)
        self.pl_download_thread.start()

        self._pl_dl_dialog.show()

    def _on_pl_progress(self, msg: str):
        is_retry = "429" in msg or "tentativa" in msg or "Aguardando" in msg
        color = "#E74C3C" if is_retry else "#9B59B6"
        self.status_label.setText(f"● {msg}")
        self.status_label.setStyleSheet(f"color: {color};")
        if hasattr(self, "_pl_dl_dialog") and self._pl_dl_dialog:
            self._pl_dl_dialog.update_status(msg)

    def _on_pl_download_ok(self, layer_id: str, data, wind_type: str):
        self.progress_bar.setVisible(False)

        # As linhas de corrente (streamplot) são pesadas e renderizam na thread da
        # UI — sem aviso, a tela "congela" e o usuário pensa que travou. Mantemos o
        # diálogo aberto com mensagem clara + cursor de espera durante a renderização.
        is_stream = data.variable == "wind" and wind_type == "stream"
        dlg = getattr(self, "_pl_dl_dialog", None)
        if is_stream and dlg:
            dlg.set_indeterminate()
            dlg.update_status("Gerando linhas de corrente — pode levar alguns segundos...")
            dlg.cancel_btn.setEnabled(False)
            dlg.repaint()  # repaint do widget (NÃO processEvents — evita re-entrância)
        if is_stream:
            self.status_label.setText("● Renderizando linhas de corrente...")
            self.status_label.setStyleSheet("color: #E67E22;")
            self.status_label.repaint()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            self.field_panel.remove_layer_entry(layer_id)
            self.canvas.add_pl_layer(layer_id, data, wind_type)
        finally:
            QApplication.restoreOverrideCursor()
            if getattr(self, "_pl_dl_dialog", None):
                self._pl_dl_dialog.finish_ok()
                self._pl_dl_dialog = None

        var_info = VARIABLE_REGISTRY.get(data.variable, {})
        nome = var_info.get("nome", data.variable)

        label = f"{nome} {data.level} hPa" if data.level > 0 else nome

        detail = data.unit
        if data.variable == "wind":
            detail = wind_type

        self.field_panel.add_layer_entry(layer_id, label, detail)

        self.status_label.setText(f"● {label} carregado  |  {data.valid_time} UTC")
        self.status_label.setStyleSheet("color: #27AE60;")

    def _on_pl_download_error(self, error_msg: str):
        self.progress_bar.setVisible(False)

        if hasattr(self, "_pl_dl_dialog") and self._pl_dl_dialog:
            self._pl_dl_dialog.finish_error()
            self._pl_dl_dialog = None

        self.status_label.setText("● Erro ao baixar campo")
        self.status_label.setStyleSheet("color: #E74C3C;")

        if "429" in error_msg or "Limite de requisições" in error_msg:
            QMessageBox.warning(self, "Limite de Requisições (429)", error_msg)
        else:
            QMessageBox.warning(self, "Erro ao Baixar Campo", error_msg)

    def _on_toggle_pl_layer(self, layer_id: str, visible: bool):
        if layer_id == "loczcit":
            self.canvas.toggle_loczcit(visible)
            return
        if layer_id == "loczcit_axis":
            self.canvas.toggle_loczcit_axis(visible)
            return
        if layer_id == "blocking":
            self.canvas.toggle_blocking(visible)
            return
        # Re-habilitar linhas de corrente re-renderiza o streamplot (pesado) —
        # mesmo aviso do download para o usuário não achar que travou.
        is_stream = visible and self.canvas.is_stream_layer(layer_id)
        if is_stream:
            self.status_label.setText("● Renderizando linhas de corrente — aguarde...")
            self.status_label.setStyleSheet("color: #E67E22;")
            self.status_label.repaint()  # repaint local, sem re-entrar no event loop
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.canvas.toggle_pl_layer(layer_id, visible)
        finally:
            if is_stream:
                QApplication.restoreOverrideCursor()
                self.status_label.setText("● Linhas de corrente prontas")
                self.status_label.setStyleSheet("color: #27AE60;")

    def _on_remove_pl_layer(self, layer_id: str):
        if layer_id == "loczcit":
            self.canvas.remove_loczcit()
            self.canvas.draw()
        elif layer_id == "loczcit_axis":
            self.canvas.remove_loczcit_axis()
            self.canvas.draw()
        elif layer_id == "blocking":
            self.canvas.remove_blocking()
            self.canvas.draw()
        else:
            self.canvas.remove_pl_layer(layer_id)

    # ═══════════════════════════════════════════════════════════════════════
    #  PRESETS DE ANÁLISE
    # ═══════════════════════════════════════════════════════════════════════

    def _on_preset_requested(self, preset_name: str):
        """Executa um preset de análise: adiciona camadas em sequência."""
        presets = FieldLayerPanel.ANALYSIS_PRESETS
        if preset_name not in presets:
            return

        layers = presets[preset_name]

        self._preset_queue = list(layers)
        self._preset_name = preset_name

        self.status_label.setText(f"● Preset: {preset_name} — carregando camadas...")
        self.status_label.setStyleSheet("color: #9B59B6;")

        self._process_preset_queue()

    # ─── Índice ZCIT (LOCZCIT-PA) ───────────────────────────────────────────

    @staticmethod
    def _spatial_available() -> bool:
        """True se o extra 'spatial' (esda/libpysal) estiver instalado."""
        import importlib.util

        return (
            importlib.util.find_spec("esda") is not None
            and importlib.util.find_spec("libpysal") is not None
        )

    def _ask_loczcit_filter_method(self) -> str | None:
        """Modal: método de delimitação da banda. Retorna 'iqr'|'coherence'|None.

        Coletado na thread da GUI ANTES de disparar o cálculo (Blindagem #6: a
        thread de cálculo nunca abre diálogos). Ver Integracao_UX_LOCZCIT-PA.
        """
        box = QMessageBox(self)
        box.setWindowTitle("ZCIT (LOCZCIT-PA) — Método de delimitação")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("Como deseja delimitar a banda da ZCIT?")
        box.setInformativeText(
            "Filtro Espacial (IQR) — Rápido. Limpa o campo por estatística de "
            "latitude (Tukey). Padrão recomendado.\n\n"
            "Coerência Espacial (LISA) — Avançado. Isola o envelope físico da "
            "ZCIT por hotspots confirmados (convecção cercada por convecção). "
            "Mais robusto contra sistemas órfãos (VCAN isolado), porém usa mais "
            "CPU/RAM (centenas de simulações de Monte Carlo)."
        )
        box.setStyleSheet(DARK_STYLE)
        iqr_btn = box.addButton("Filtro Espacial (IQR)", QMessageBox.ButtonRole.AcceptRole)
        lisa_btn = box.addButton("Coerência Espacial (LISA)", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(iqr_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is iqr_btn:
            return "iqr"
        if clicked is lisa_btn:
            return "coherence"
        return None

    def _on_loczcit_requested(self):
        """Calcula o índice LOCZCIT-PA (ZCIT) em thread e injeta o raster categórico."""
        if getattr(self, "loczcit_thread", None) and self.loczcit_thread.isRunning():
            return

        cycle = self.settings_panel.get_cycle()
        cycle_date = self.settings_panel.get_cycle_date()

        # A Técnica B precisa fixar a data; resolve a rodada mais recente se "auto".
        if cycle is None or not cycle_date:
            try:
                from cartomet_br.data.ecmwf import estimate_available_cycles

                latest = estimate_available_cycles().get("latest")
                if latest:
                    cycle = latest["cycle"]
                    cycle_date = latest["base_datetime"].strftime("%Y%m%d")
            except Exception:
                pass
        if cycle is None or not cycle_date:
            QMessageBox.warning(
                self,
                "ZCIT (LOCZCIT-PA)",
                "Não foi possível determinar a rodada do ECMWF.\n\n"
                'Clique em "Verificar Rodadas" e selecione uma rodada primeiro.',
            )
            return

        # Escolha do método de delimitação da banda (coletada ANTES de disparar a thread)
        filter_method = self._ask_loczcit_filter_method()
        if filter_method is None:
            return  # usuário cancelou a escolha
        if filter_method == "coherence" and not self._spatial_available():
            QMessageBox.information(
                self,
                "Coerência Espacial (LISA)",
                "A Coerência Espacial não está disponível nesta instalação;\n"
                "usando o filtro IQR.\n\n"
                "Para habilitar, instale o extra 'spatial':\n    uv sync --extra spatial",
            )
            filter_method = "iqr"

        self.status_label.setText("● Calculando índice ZCIT (LOCZCIT-PA)...")
        self.status_label.setStyleSheet("color: #E67E22;")

        self._loczcit_cancelled = False
        self._loczcit_dl_dialog = DownloadProgressDialog("Índice ZCIT (LOCZCIT-PA)", parent=self)
        self._loczcit_dl_dialog.setStyleSheet(DARK_STYLE)
        self._loczcit_dl_dialog.set_indeterminate()
        if filter_method == "coherence":
            self._loczcit_dl_dialog.update_status(
                "Coerência espacial (LISA): após o download, roda simulações "
                "estatísticas — pode levar alguns segundos..."
            )
        else:
            self._loczcit_dl_dialog.update_status("Preparando forçantes (TSM, vento, OLR)...")
        self._loczcit_dl_dialog.cancel_requested.connect(self._cancel_loczcit)

        # Horizonte de previsão do slider → valid_time = rodada + step (desacum. dinâmica)
        step = self.settings_panel.get_step()
        self.loczcit_thread = LoczcitThread(
            config=self.config,
            cycle=cycle,
            cycle_date=cycle_date,
            step=step,
            filter_method=filter_method,
            parent=self,
        )
        self.loczcit_thread.progress.connect(self._on_loczcit_progress)
        self.loczcit_thread.finished_ok.connect(self._on_loczcit_ok)
        self.loczcit_thread.finished_error.connect(self._on_loczcit_error)
        self.loczcit_thread.finished_cancelled.connect(self._on_loczcit_cancelled)
        self.loczcit_thread.start()
        self._loczcit_dl_dialog.show()

    def _cancel_loczcit(self):
        """Cancela o cálculo: fecha o diálogo já (UX) e aborta a thread no próximo passo."""
        self._loczcit_cancelled = True
        if getattr(self, "loczcit_thread", None):
            self.loczcit_thread.cancel()
        if getattr(self, "_loczcit_dl_dialog", None):
            self._loczcit_dl_dialog.update_status("Cancelando...")
            self._loczcit_dl_dialog.reject()
            self._loczcit_dl_dialog = None
        self.status_label.setText("● ZCIT (LOCZCIT-PA) cancelado")
        self.status_label.setStyleSheet("color: #F39C12;")

    def _on_loczcit_cancelled(self):
        self.loczcit_thread = None
        if getattr(self, "_loczcit_dl_dialog", None):
            self._loczcit_dl_dialog.reject()
            self._loczcit_dl_dialog = None

    def _on_loczcit_progress(self, msg: str):
        self.status_label.setText(f"● {msg}")
        if getattr(self, "_loczcit_dl_dialog", None):
            self._loczcit_dl_dialog.update_status(msg)

    def _on_loczcit_ok(self, result):
        self.loczcit_thread = None
        if getattr(self, "_loczcit_cancelled", False):
            return  # usuário cancelou; ignora resultado tardio
        if getattr(self, "_loczcit_dl_dialog", None):
            self._loczcit_dl_dialog.finish_ok()
            self._loczcit_dl_dialog = None
        self.canvas.plot_loczcit_raster(result)
        m = result.meta
        method_tag = "LISA" if m.get("filter_method") == "coherence" else "IQR"

        # Registra como camada ativa (toggle/remover); recria a entrada se já existia
        self.field_panel.remove_layer_entry("loczcit")
        self.field_panel.add_layer_entry(
            "loczcit",
            "ZCIT (LOCZCIT-PA)",
            f"F{m.get('n_strong', 0)} M{m.get('n_moderate', 0)} "
            f"f{m.get('n_weak', 0)} C{m.get('n_cinematica', 0)} · {method_tag}",
        )

        # Overlay opcional do EIXO (banda simples/dupla) — calculado, desenhado e
        # OCULTO por padrão (human-in-the-loop). O usuário liga pelo toggle da camada.
        self.field_panel.remove_layer_entry("loczcit_axis")
        if getattr(result, "axis", None) is not None:
            self.canvas.plot_loczcit_axis(result)
            self.canvas.toggle_loczcit_axis(False)
            dbl = "dupla" if m.get("is_double") else "simples"
            self.field_panel.add_layer_entry(
                "loczcit_axis",
                "ZCIT — Eixo (auto)",
                f"banda {dbl}",
                checked=False,
            )

        # Salva o produto (raster + índice contínuo) em NetCDF na subpasta loczcit_pa/
        saved_name = ""
        try:
            from cartomet_br.data.loczcit_pa_engine import save_loczcit_netcdf

            saved = save_loczcit_netcdf(result, self.config.loczcit_dir)
            saved_name = f" · salvo: loczcit_pa/{saved.name}"
        except Exception as exc:
            logger.warning("Falha ao salvar produto LOCZCIT-PA: %s", exc)

        self.status_label.setText(
            f"● ZCIT (LOCZCIT-PA · {method_tag}): Forte {m.get('n_strong', 0)} | "
            f"Moderada {m.get('n_moderate', 0)} | Fraca {m.get('n_weak', 0)} | "
            f"Cinemática {m.get('n_cinematica', 0)}{saved_name}"
        )
        self.status_label.setStyleSheet("color: #27AE60;")

    def _on_loczcit_error(self, error_msg: str):
        self.loczcit_thread = None
        if getattr(self, "_loczcit_cancelled", False):
            return  # cancelado pelo usuário; não mostra erro
        if getattr(self, "_loczcit_dl_dialog", None):
            self._loczcit_dl_dialog.finish_error()
            self._loczcit_dl_dialog = None
        self.status_label.setText("● Erro no índice ZCIT")
        self.status_label.setStyleSheet("color: #E74C3C;")
        QMessageBox.warning(self, "Erro no Índice ZCIT (LOCZCIT-PA)", error_msg)

    # ═══════════════════════════════════════════════════════════════════════
    #  BLOQUEIO ATMOSFÉRICO (ANOMALIA DE Z500)
    # ═══════════════════════════════════════════════════════════════════════

    def _on_blocking_requested(self):
        """Calcula a anomalia de Z500 (bloqueio) em thread e injeta o campo no mapa."""
        if getattr(self, "blocking_thread", None) and self.blocking_thread.isRunning():
            return

        cycle = self.settings_panel.get_cycle()
        cycle_date = self.settings_panel.get_cycle_date()

        # A climatologia precisa da DATA do valid_time; resolve a rodada se "auto".
        if cycle is None or not cycle_date:
            try:
                from cartomet_br.data.ecmwf import estimate_available_cycles

                latest = estimate_available_cycles().get("latest")
                if latest:
                    cycle = latest["cycle"]
                    cycle_date = latest["base_datetime"].strftime("%Y%m%d")
            except Exception:
                pass
        if cycle is None or not cycle_date:
            QMessageBox.warning(
                self,
                "Bloqueio Atmosférico (Z500)",
                "Não foi possível determinar a rodada do ECMWF.\n\n"
                'Clique em "Verificar Rodadas" e selecione uma rodada primeiro.',
            )
            return

        self.status_label.setText("● Calculando anomalia de Z500 (bloqueio)...")
        self.status_label.setStyleSheet("color: #2980B9;")

        self._blocking_cancelled = False
        self._blocking_dl_dialog = DownloadProgressDialog(
            "Bloqueio Atmosférico (Z500)",
            parent=self,
        )
        self._blocking_dl_dialog.setStyleSheet(DARK_STYLE)
        self._blocking_dl_dialog.set_indeterminate()
        self._blocking_dl_dialog.update_status("Preparando gh 500 hPa (IFS) e climatologia ERA5...")
        self._blocking_dl_dialog.cancel_requested.connect(self._cancel_blocking)

        step = self.settings_panel.get_step()
        self.blocking_thread = BlockingThread(
            config=self.config,
            cycle=cycle,
            cycle_date=cycle_date,
            step=step,
            parent=self,
        )
        self.blocking_thread.progress.connect(self._on_blocking_progress)
        self.blocking_thread.finished_ok.connect(self._on_blocking_ok)
        self.blocking_thread.finished_error.connect(self._on_blocking_error)
        self.blocking_thread.finished_cancelled.connect(self._on_blocking_cancelled)
        self.blocking_thread.start()
        self._blocking_dl_dialog.show()

    def _cancel_blocking(self):
        """Cancela a análise: fecha o diálogo já (UX) e aborta a thread no próximo passo."""
        self._blocking_cancelled = True
        if getattr(self, "blocking_thread", None):
            self.blocking_thread.cancel()
        if getattr(self, "_blocking_dl_dialog", None):
            self._blocking_dl_dialog.update_status("Cancelando...")
            self._blocking_dl_dialog.reject()
            self._blocking_dl_dialog = None
        self.status_label.setText("● Bloqueio (Z500) cancelado")
        self.status_label.setStyleSheet("color: #F39C12;")

    def _on_blocking_cancelled(self):
        self.blocking_thread = None
        if getattr(self, "_blocking_dl_dialog", None):
            self._blocking_dl_dialog.reject()
            self._blocking_dl_dialog = None

    def _on_blocking_progress(self, msg: str):
        self.status_label.setText(f"● {msg}")
        if getattr(self, "_blocking_dl_dialog", None):
            self._blocking_dl_dialog.update_status(msg)

    def _on_blocking_ok(self, result):
        self.blocking_thread = None
        if getattr(self, "_blocking_cancelled", False):
            return  # usuário cancelou; ignora resultado tardio
        if getattr(self, "_blocking_dl_dialog", None):
            self._blocking_dl_dialog.finish_ok()
            self._blocking_dl_dialog = None
        self.canvas.plot_blocking_anomaly(result)
        m = result.meta
        approx_tag = "≈" if m.get("is_approx") else ""

        # Registra como camada ativa (toggle/remover); recria a entrada se já existia
        self.field_panel.remove_layer_entry("blocking")
        self.field_panel.add_layer_entry(
            "blocking",
            "Bloqueio (anom. Z500)",
            f"{result.clim_hour:02d}Z{approx_tag} · "
            f"{m.get('anom_min', 0):.0f}/{m.get('anom_max', 0):+.0f} gpm",
        )

        self.status_label.setText(
            f"● Bloqueio (Z500): anomalia {m.get('anom_min', 0):.0f} a "
            f"{m.get('anom_max', 0):+.0f} gpm · clim {result.clim_mmdd[2:]}/"
            f"{result.clim_mmdd[:2]} {result.clim_hour:02d}Z{approx_tag}"
        )
        self.status_label.setStyleSheet("color: #27AE60;")

    def _on_blocking_error(self, error_msg: str):
        self.blocking_thread = None
        if getattr(self, "_blocking_cancelled", False):
            return  # cancelado pelo usuário; não mostra erro
        if getattr(self, "_blocking_dl_dialog", None):
            self._blocking_dl_dialog.finish_error()
            self._blocking_dl_dialog = None
        self.status_label.setText("● Erro na análise de bloqueio (Z500)")
        self.status_label.setStyleSheet("color: #E74C3C;")
        QMessageBox.warning(self, "Erro no Bloqueio Atmosférico (Z500)", error_msg)

    def _process_preset_queue(self):
        """Processa a próxima camada da fila de preset."""
        if not hasattr(self, "_preset_queue") or not self._preset_queue:
            self.status_label.setText(f"● Preset '{getattr(self, '_preset_name', '')}' completo!")
            self.status_label.setStyleSheet("color: #27AE60;")
            return

        if self.pl_download_thread and self.pl_download_thread.isRunning():
            QTimer.singleShot(500, self._process_preset_queue)
            return

        var_key, level, wind_type = self._preset_queue.pop(0)
        self._on_add_pl_layer(var_key, level, wind_type)

        try:
            self.pl_download_thread.finished_ok.connect(
                lambda *_: self._process_preset_queue(), Qt.ConnectionType.SingleShotConnection
            )
            self.pl_download_thread.finished_error.connect(
                lambda *_: self._process_preset_queue(), Qt.ConnectionType.SingleShotConnection
            )
        except Exception:
            QTimer.singleShot(3000, self._process_preset_queue)

    # ═══════════════════════════════════════════════════════════════════════
    #  OPERAÇÕES DE ARQUIVO
    # ═══════════════════════════════════════════════════════════════════════

    def _new_project(self):
        """Reinicia o programa para um estado completamente limpo."""
        reply = QMessageBox.question(
            self,
            "Novo Projeto",
            "Isso irá reiniciar o CartoMet BR.\n\n"
            "Todas as camadas e simbologias serão perdidas.\n"
            "Deseja continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            if self.download_thread and self.download_thread.isRunning():
                self.download_thread.terminate()
                self.download_thread.wait(2000)
            if self.pl_download_thread and self.pl_download_thread.isRunning():
                self.pl_download_thread.terminate()
                self.pl_download_thread.wait(2000)

            gc.collect()

            subprocess.Popen([sys.executable, "-m", "cartomet_br", "gui"])
            QApplication.quit()

    def _default_chart_name(self, ext: str = "png") -> str:
        """Nome inteligente para a carta, derivado da rodada/step carregados.

        Ex.: 'carta_18Z_30-05-2026_step+0h.png'. Sem modelo carregado,
        cai para um carimbo de data/hora.
        """
        data = getattr(self.canvas, "synoptic_data", None)
        base_time = getattr(data, "base_time", "") if data is not None else ""
        if base_time:
            bt = base_time.replace(" ", "_").replace("/", "-").replace(":", "")
            step = getattr(data, "step", 0)
            return f"carta_{bt}_step+{step}h.{ext}"
        return f"carta_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"

    def _ask_export_dpi(self) -> int | None:
        """Seletor de resolução (DPI) compartilhado pelos fluxos de export."""
        opcoes = [
            "100 DPI — Rascunho (rápido, menor arquivo)",
            "150 DPI — Qualidade média",
            "200 DPI — Alta qualidade (padrão)",
            "300 DPI — Impressão profissional",
            "600 DPI — Ultra alta resolução",
        ]
        dpi_map = {opcoes[0]: 100, opcoes[1]: 150, opcoes[2]: 200, opcoes[3]: 300, opcoes[4]: 600}
        escolha, ok = QInputDialog.getItem(
            self,
            "Resolução da Exportação",
            "Escolha a resolução (DPI):",
            opcoes,
            2,  # índice padrão: 200 DPI
            False,  # não editável
        )
        if not ok:
            return None
        return dpi_map[escolha]

    def _save_figure(self):
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar Imagem",
            str(self.config.charts_dir / self._default_chart_name("png")),
            "PNG (*.png);;JPEG (*.jpg);;PDF (*.pdf)",
        )
        if not filepath:
            return

        dpi = self._ask_export_dpi()
        if dpi is None:
            return
        try:
            self.canvas.save_figure(filepath, dpi=dpi)
            self.status_label.setText(f"● Salvo ({dpi} DPI): {Path(filepath).name}")
            self.status_label.setStyleSheet("color: #27AE60;")
        except Exception as e:
            QMessageBox.critical(
                self, "Erro ao Exportar", f"Não foi possível salvar o arquivo:\n\n{e}"
            )
            self.status_label.setText("● Erro ao exportar")
            self.status_label.setStyleSheet("color: #E74C3C;")

    def _export_chart(self):
        """Exporta no 'modo carta OMM': cabeçalho institucional + legenda.

        Diferente de ``_save_figure`` (export cru): abre o diálogo de metadados,
        compõe a mobília (cabeçalho/legenda) só na figura exportada e a remove em
        seguida — a edição ao vivo nunca é tocada.
        """
        from cartomet_br.gui.chart_export import compose_chart_metadata
        from cartomet_br.gui.chart_header_dialog import ChartHeaderDialog

        dialog = ChartHeaderDialog(self)
        if not dialog.exec():  # Cancelado
            return
        header = dialog.metadata()

        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar Carta (OMM)",
            str(self.config.charts_dir / self._default_chart_name("png")),
            "PNG (*.png);;PDF (*.pdf);;JPEG (*.jpg)",
        )
        if not filepath:
            return

        dpi = self._ask_export_dpi()
        if dpi is None:
            return

        meta = compose_chart_metadata(header, self.canvas.get_chart_time_meta())
        try:
            extra = self.canvas.render_chart_furniture(meta)
            self.canvas.save_figure(filepath, dpi=dpi, extra_artists=extra)
            self.status_label.setText(f"● Carta exportada ({dpi} DPI): {Path(filepath).name}")
            self.status_label.setStyleSheet("color: #27AE60;")
        except Exception as e:
            QMessageBox.critical(
                self, "Erro ao Exportar", f"Não foi possível exportar a carta:\n\n{e}"
            )
            self.status_label.setText("● Erro ao exportar carta")
            self.status_label.setStyleSheet("color: #E74C3C;")
        finally:
            self.canvas.clear_chart_furniture()

    # ═══════════════════════════════════════════════════════════════════════
    #  PROJETO DE ANÁLISE (.cmbr) — salvar/abrir o traçado + estado do mapa
    # ═══════════════════════════════════════════════════════════════════════

    def _save_project(self):
        """Salva o traçado manual + estado do mapa num arquivo .cmbr (JSON)."""
        from cartomet_br.gui import project_io

        # Não perder traçado em rascunho: uma linha de símbolo só entra no
        # histórico com [Enter]. Finaliza a pendente ANTES de exportar.
        self.canvas.commit_pending_line()
        drawings = self.canvas.export_drawings_state()
        if not drawings:
            resp = QMessageBox.question(
                self,
                "Salvar Projeto",
                "Nenhum traçado finalizado para salvar.\n\n"
                "Dica: feche cada linha com [Enter] antes de salvar.\n\n"
                "Deseja salvar mesmo assim (apenas o enquadramento)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                self.status_label.setText("● Salvar projeto cancelado")
                self.status_label.setStyleSheet("color: #E67E22;")
                return

        default = (
            self.config.projects_dir
            / f"analise_{datetime.now():%Y%m%d_%H%M}{project_io.PROJECT_EXTENSION}"
        )
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar Projeto de Análise",
            str(default),
            f"Projeto CartoMet BR (*{project_io.PROJECT_EXTENSION});;Todos (*)",
        )
        if not filepath:
            return
        path = Path(filepath)
        if path.suffix == "":
            path = path.with_suffix(project_io.PROJECT_EXTENSION)

        # Manifesto das camadas ativas. O canvas conhece sinótica/campos/satélite/
        # TSM/LOCZCIT/bloqueio; as observações vêm do painel de configurações.
        layers = self.canvas.export_layers_state()
        obs = self.settings_panel.get_observations()
        if obs.get("metar") or obs.get("synop"):
            layers.append(
                {
                    "kind": "observations",
                    "metar": bool(obs.get("metar")),
                    "synop": bool(obs.get("synop")),
                }
            )

        project = project_io.build_project(
            extent=list(self.config.extent),
            theme=self.canvas.current_theme,
            data_context={
                "cycle": self.settings_panel.get_cycle(),
                "cycle_date": self.settings_panel.get_cycle_date(),
                "step": self.settings_panel.get_step(),
                # Técnica de desacumulação (OLR/precip) — necessária p/ refazer
                # esses campos do cache na mesma janela ao abrir.
                "technique": self.field_panel.get_technique(),
            },
            # Camadas ativas: as de cache são redesenhadas SÓ do cache na abertura
            # (ver _restore_layers_from_cache); as computadas/externas ficam
            # memorizadas p/ reativação manual — nunca disparam rede.
            layers=layers,
            drawings=drawings,
            app_version=APP_VERSION,
        )
        try:
            path.write_text(project_io.dump_project(project), encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(
                self, "Erro ao Salvar Projeto", f"Não foi possível salvar o projeto:\n\n{e}"
            )
            self.status_label.setText("● Erro ao salvar projeto")
            self.status_label.setStyleSheet("color: #E74C3C;")
            return
        self.status_label.setText(f"● Projeto salvo: {path.name}")
        self.status_label.setStyleSheet("color: #27AE60;")

    def _open_project(self):
        """Abre um .cmbr e restaura o traçado + enquadramento. NÃO baixa dados."""
        from cartomet_br.gui import project_io

        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Abrir Projeto de Análise",
            str(self.config.projects_dir),
            f"Projeto CartoMet BR (*{project_io.PROJECT_EXTENSION});;Todos (*)",
        )
        if not filepath:
            return
        try:
            data = project_io.load_project(Path(filepath).read_text(encoding="utf-8"))
            records = data.get("drawings", [])
            # Valida/converte cedo: erro claro ANTES de mexer no mapa.
            project_io.records_to_commands(records)
        except (OSError, project_io.ProjectError) as e:
            QMessageBox.warning(self, "Abrir Projeto", f"Não foi possível abrir o projeto:\n\n{e}")
            return

        mp = data.get("map", {}) or {}
        theme = mp.get("theme") or self.canvas.current_theme
        extent = mp.get("extent")

        # Limpa as linhas de camada do painel (espelha _on_theme_changed) e
        # reconstrói o mapa base; set_theme já zera desenhos/camadas/histórico.
        for lid in list(self.field_panel._layer_widgets.keys()):
            self.field_panel.remove_layer_entry(lid)
        if isinstance(extent, list) and len(extent) == 4:
            self.config.extent = [float(v) for v in extent]
            self.canvas.config = self.config
        self.canvas.set_theme(theme)
        if isinstance(extent, list) and len(extent) == 4:
            self.canvas.apply_extent([float(v) for v in extent], push_history=False)

        ctx = data.get("data_context", {}) or {}
        # Redesenha as camadas SÓ a partir do cache (nunca baixa) ANTES dos
        # desenhos — assim o traçado OMM fica por cima dos campos.
        n_layers, missed, reactivate = self._restore_layers_from_cache(
            data.get("layers", []),
            ctx,
        )

        self.canvas.import_drawings_state(records)

        self._show_project_context(
            ctx,
            Path(filepath).name,
            len(records),
            n_layers,
            missed,
            reactivate,
        )
        self.status_label.setText(f"● Projeto aberto: {Path(filepath).name}")
        self.status_label.setStyleSheet("color: #27AE60;")

    def _show_project_context(
        self,
        ctx: dict,
        name: str,
        n_drawings: int = 0,
        n_layers: int = 0,
        missed: list | None = None,
        reactivate: list | None = None,
    ) -> None:
        """Resumo da abertura: traçados restaurados, camadas redesenhadas do cache,
        o que ficou fora do cache, as camadas memorizadas p/ reativação manual, e a
        rodada/step do projeto. Nunca baixa nada."""
        missed = missed or []
        reactivate = reactivate or []
        plural = lambda n: "s" if n != 1 else ""  # noqa: E731

        if n_drawings > 0:
            linhas = [f"{n_drawings} traçado{plural(n_drawings)} restaurado{plural(n_drawings)}."]
        else:
            linhas = ["Este projeto não contém traçados."]

        if n_layers > 0:
            linhas.append(
                f"{n_layers} camada{plural(n_layers)} "
                f"redesenhada{plural(n_layers)} a partir do cache (nada foi baixado)."
            )
        if missed:
            linhas.append(
                "Fora do cache (recarregue pelo painel se precisar): " + ", ".join(missed) + "."
            )
        if reactivate:
            linhas.append(
                "Estavam ativas e foram memorizadas — reative pelo painel "
                "(são camadas calculadas/externas): " + ", ".join(reactivate) + "."
            )
        restaurado = "\n".join(linhas)

        cycle, cdate, step = ctx.get("cycle"), ctx.get("cycle_date"), ctx.get("step")
        if cycle is None and cdate is None and step is None:
            QMessageBox.information(self, "Projeto aberto", restaurado)
            return

        partes = [f"Rodada: {'automática' if cycle is None else f'{int(cycle):02d}Z'}"]
        if cdate:
            partes.append(f"Data: {cdate}")
        if step is not None:
            partes.append(f"Step: +{int(step)}h")

        # Só repete o aviso "recarregue manualmente" quando NADA de camada voltou.
        nota = ""
        if n_layers == 0 and not missed and not reactivate:
            nota = (
                "\n\nNenhum campo do modelo foi redesenhado (não havia camadas "
                "salvas ou não estavam no cache). Recarregue a rodada/step pelo "
                "painel para sobrepô-los ao traçado."
            )
        QMessageBox.information(
            self,
            "Projeto aberto",
            f"{restaurado}\n\nProjeto feito para:\n  {' · '.join(partes)}{nota}",
        )

    def _layer_label(self, spec: dict) -> str:
        """Rótulo humano de uma camada (p/ as listas de status da abertura)."""
        kind = spec.get("kind")
        if kind == "synoptic":
            return "Carta sinótica (PNMM/espessura/centros)"
        if kind == "satellite":
            return "Satélite GOES"
        if kind == "sst":
            ts = spec.get("time_str", "")
            return f"TSM — MUR SST {ts}".strip()
        if kind == "loczcit":
            return "ZCIT (LOCZCIT-PA)" + (" + eixo" if spec.get("axis") else "")
        if kind == "blocking":
            return "Bloqueio atmosférico (Z500)"
        if kind == "observations":
            kinds = [k.upper() for k in ("metar", "synop") if spec.get(k)]
            return "Observações (" + "/".join(kinds) + ")" if kinds else "Observações"
        var = spec.get("variable", "campo")
        nome = VARIABLE_REGISTRY.get(var, {}).get("nome", var)
        lvl = spec.get("level") or 0
        return f"{nome} {lvl} hPa" if lvl else nome

    def _add_field_panel_entry(self, layer_id: str, data, wind_type: str) -> None:
        """Recria a entrada da camada no FieldLayerPanel (espelha _on_pl_download_ok)."""
        var_info = VARIABLE_REGISTRY.get(data.variable, {})
        nome = var_info.get("nome", data.variable)
        label = f"{nome} {data.level} hPa" if data.level > 0 else nome
        detail = wind_type if data.variable == "wind" else data.unit
        self.field_panel.remove_layer_entry(layer_id)
        self.field_panel.add_layer_entry(layer_id, label, detail)

    def _restore_satellite_from_cache(self, filename: str) -> bool:
        """Relê o GOES do .nc em cache (sem rede) e replota. True se restaurou.

        Usa ``load_goes_netcdf`` — a MESMA rotina do download —, que converte
        ``x``/``y`` de radianos→metros. (Reler pelo caminho genérico de NetCDF
        local deixava a imagem num quadro submilimétrico, invisível no mapa.)
        """
        if not filename:
            return False
        path = self.config.satellite_dir / filename
        if not path.exists():
            return False
        try:
            from cartomet_br.data.ecmwf import load_goes_netcdf

            sat_data = load_goes_netcdf(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Satélite em cache ilegível (%s): %s", filename, exc)
            return False
        self.canvas.plot_satellite(sat_data)
        with contextlib.suppress(Exception):
            self.satellite_panel.set_loaded(sat_data.time_str or path.stem)
        return True

    def _restore_sst_from_cache(self, spec: dict) -> bool:
        """Relê a TSM (MUR SST) do .nc em cache, sem rede. True se restaurou."""
        time_str = spec.get("time_str", "")
        stride = int(spec.get("stride", 5))
        if not time_str:
            return False
        path = self.config.sst_dir / f"mur_sst_{time_str}_s{stride}.nc"
        if not path.exists():
            return False
        try:
            from cartomet_br.data.sst import _load_sst_from_file

            data = _load_sst_from_file(path)
            self.canvas.plot_sst(data)
            with contextlib.suppress(Exception):
                self.sst_panel.set_loaded(data.time_str)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao restaurar TSM %s: %s", time_str, exc)
            return False

    def _restore_layers_from_cache(
        self,
        layers: list,
        ctx: dict,
    ) -> tuple[int, list[str], list[str]]:
        """Recria as camadas salvas. As de cache (sinótica/campos/satélite/TSM) são
        redesenhadas SOMENTE do cache (nunca baixam); as computadas/externas
        (LOCZCIT, bloqueio, observações) são apenas memorizadas p/ reativação manual.

        Os campos ECMWF rodam dentro de ``cache_only_mode()``: cache miss vira *miss*,
        jamais download (human-in-the-loop). Retorna
        ``(n_restauradas, [fora do cache], [reativar pelo painel])``.
        """
        if not layers:
            return 0, [], []

        from cartomet_br.data.ecmwf import cache_only_mode
        from cartomet_br.services.data_service import DataService

        cycle = ctx.get("cycle")
        cdate = ctx.get("cycle_date")
        technique = ctx.get("technique") or "direct"
        svc = DataService(self.config)
        restored = 0
        missed: list[str] = []
        reactivate: list[str] = []

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            with cache_only_mode():
                for spec in layers:
                    kind = spec.get("kind")
                    try:
                        if kind == "synoptic":
                            data = svc.load_synoptic(int(spec.get("step", 0)), cycle, cdate)
                            self.canvas.set_synoptic_data(data)
                            vis = spec.get("visibility", {}) or {}
                            for k in ("pnmm", "thickness", "centers"):
                                self.canvas.toggle_layer(k, bool(vis.get(k, True)))
                            restored += 1
                        elif kind == "field":
                            wind_type = spec.get("wind_type", "barbs")
                            layer_id, data = svc.load_field(
                                spec.get("variable", ""),
                                spec.get("level") or None,
                                int(spec.get("step", 0)),
                                cycle,
                                cdate,
                                wind_type=wind_type,
                                technique=technique,
                            )
                            self.canvas.add_pl_layer(layer_id, data, wind_type)
                            self._add_field_panel_entry(layer_id, data, wind_type)
                            restored += 1
                        elif kind == "satellite":
                            if self._restore_satellite_from_cache(spec.get("filename", "")):
                                restored += 1
                            else:
                                missed.append("Satélite GOES")
                        elif kind == "sst":
                            if self._restore_sst_from_cache(spec):
                                restored += 1
                            else:
                                missed.append(self._layer_label(spec))
                        elif kind in ("loczcit", "blocking", "observations"):
                            # Calculadas/externas: memorizadas, reativação manual.
                            reactivate.append(self._layer_label(spec))
                        else:
                            missed.append(self._layer_label(spec))
                    except Exception as exc:  # noqa: BLE001 — cache-only: falha = não restaurou
                        logger.info("Camada fora do cache (%s): %s", spec.get("kind"), exc)
                        missed.append(self._layer_label(spec))
        finally:
            QApplication.restoreOverrideCursor()

        self.canvas.draw()
        return restored, missed, reactivate

    def _print_canvas(self):
        """Captura pixel-perfect do mapa (Ctrl+P) — idêntica à tela."""
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Capturar Tela do Mapa",
            str(self.config.charts_dir / self._default_chart_name("png")),
            "PNG (*.png);;JPEG (*.jpg)",
        )
        if filepath:
            self.canvas.capture_canvas(filepath)
            self.status_label.setText(f"● Captura salva: {Path(filepath).name}")
            self.status_label.setStyleSheet("color: #27AE60;")

    def _on_clear_map(self):
        """Remove todas as camadas da carta e reseta os painéis (UX 'recomeçar')."""
        resp = QMessageBox.question(
            self,
            "Limpar mapa",
            "Remover TODAS as camadas da carta?\n\n"
            "Isso apaga campos, satélite, TSM, observações e desenhos "
            "atualmente no mapa (os dados em disco e as cartas salvas são "
            "preservados).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        # 1) Limpa o canvas (todas as camadas + desenhos)
        self.canvas.clear_map()

        # 2) Reseta o estado dos painéis para refletir o mapa vazio
        with contextlib.suppress(Exception):
            self.field_panel.clear_all_layers()

        # Observações: desmarca sem disparar novo download
        for chk in (
            getattr(self.settings_panel, "synop_check", None),
            getattr(self.settings_panel, "metar_check", None),
        ):
            if chk is not None:
                chk.blockSignals(True)
                chk.setChecked(False)
                chk.blockSignals(False)
        if hasattr(self.settings_panel, "set_obs_reference_time"):
            self.settings_panel.set_obs_reference_time(None)

        # Satélite / TSM: desmarca os toggles dos painéis, se existirem
        for panel in (getattr(self, "satellite_panel", None), getattr(self, "sst_panel", None)):
            chk = getattr(panel, "toggle_check", None) if panel is not None else None
            if chk is not None:
                chk.blockSignals(True)
                chk.setChecked(False)
                chk.blockSignals(False)

        # 3) Desliga modos de interação e solta os botões da toolbar
        for btn in (
            getattr(self, "draw_mode_btn", None),
            getattr(self, "annotate_btn", None),
            getattr(self, "ruler_btn", None),
            getattr(self, "zoom_area_btn", None),
            getattr(self, "pan_btn", None),
        ):
            if btn is not None:
                btn.setChecked(False)
        self.canvas.set_drawing_mode(False)
        self.canvas.set_annotation_mode(False)
        self.canvas.set_ruler_mode(False)
        self.canvas.set_zoom_area_mode(False)
        self.canvas.set_pan_mode(False)

        self.status_label.setText("● Mapa limpo")
        self.status_label.setStyleSheet("color: #27AE60;")

    # ─── Gerenciamento da pasta de dados ─────────────────────────────────

    @staticmethod
    def _format_bytes(num: float) -> str:
        """Formata bytes em unidade legível (KB/MB/GB)."""
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if num < 1024.0:
                return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
            num /= 1024.0
        return f"{num:.1f} PB"

    def _cache_size_bytes(self) -> int:
        """Soma o tamanho dos dados em cache (grib/satelite/tsm/observacoes)."""
        total = 0
        base = self.config.data_dir
        for sub in Config.CACHE_SUBDIRS:
            d = base / sub
            if d.exists():
                for f in d.rglob("*"):
                    if f.is_file():
                        with contextlib.suppress(OSError):
                            total += f.stat().st_size
        return total

    def _open_folder(self, path: Path) -> None:
        """Abre uma pasta no explorador de arquivos do sistema."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        with contextlib.suppress(OSError):
            path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_data_folder(self):
        self._open_folder(self.config.data_dir)

    def _open_charts_folder(self):
        self._open_folder(self.config.charts_dir)

    def _clear_downloaded_data(self):
        """Apaga o cache em disco (grib/satelite/tsm/observacoes), preservando cartas."""
        size = self._cache_size_bytes()
        if size == 0:
            QMessageBox.information(
                self,
                "Limpar Dados Baixados",
                "Não há dados em cache para limpar.\n\n"
                "As cartas salvas (pasta 'cartas') são sempre preservadas.",
            )
            return

        resp = QMessageBox.question(
            self,
            "Limpar Dados Baixados",
            f"Isso vai liberar aproximadamente {self._format_bytes(size)} "
            f"apagando os dados baixados em cache:\n\n"
            f"  • GRIB do ECMWF  • Satélite  • TSM  • Observações\n\n"
            f"As cartas salvas (pasta 'cartas') NÃO serão afetadas.\n"
            f"Os dados podem ser baixados novamente quando necessário.\n\n"
            f"Deseja continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        removed, errors = 0, 0
        base = self.config.data_dir
        for sub in Config.CACHE_SUBDIRS:
            d = base / sub
            if not d.exists():
                continue
            for f in sorted(d.rglob("*"), reverse=True):  # arquivos antes dos dirs
                try:
                    if f.is_file():
                        f.unlink()
                        removed += 1
                    elif f.is_dir():
                        f.rmdir()
                except OSError:
                    errors += 1

        msg = f"Cache limpo: {self._format_bytes(size)} liberados."
        if errors:
            msg += f"\n\n{errors} arquivo(s) não puderam ser removidos (em uso)."
        QMessageBox.information(self, "Limpar Dados Baixados", msg)
        self.status_label.setText(f"● Cache limpo ({self._format_bytes(size)})")
        self.status_label.setStyleSheet("color: #27AE60;")

    def _configure_data_dir(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Selecione o Diretório de Dados", str(self.data_dir.parent)
        )
        if dir_path:
            new_dir = Path(dir_path)
            try:
                new_dir.mkdir(parents=True, exist_ok=True)
                self.data_dir = new_dir
                self.config.data_dir = new_dir
                self.config.output_dir = new_dir / "output"
                self.config.output_dir.mkdir(exist_ok=True)

                settings = QSettings("PPGGRD-UFPA", APP_NAME)
                settings.setValue("data_dir", str(new_dir))

                self.data_dir_label.setText(f"📁 {new_dir}")
                QMessageBox.information(self, "Sucesso", f"Diretório alterado para:\n{new_dir}")
            except Exception as e:
                QMessageBox.critical(self, "Erro", f"Erro ao configurar diretório:\n{e}")

    # ─── Importar Arquivo Local ────────────────────────────────────────────

    def _import_local_file(self):
        """Importa arquivo GRIB2 ou NetCDF local sem download."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar Arquivo Local",
            str(self.data_dir),
            "Dados meteorológicos (*.grib2 *.nc *.grib *.netcdf);;Todos (*)",
        )
        if not file_path:
            return

        file_path = Path(file_path)
        suffix = file_path.suffix.lower()

        try:
            if suffix == ".nc" or suffix == ".netcdf":
                self._import_netcdf(file_path)
            elif suffix in (".grib2", ".grib"):
                self._import_grib(file_path)
            else:
                QMessageBox.warning(
                    self,
                    "Formato não suportado",
                    f"Extensão '{suffix}' não é suportada.\n\n"
                    "Formatos aceitos: .grib2, .grib, .nc, .netcdf",
                )
        except Exception as e:
            logger.exception("Erro ao importar arquivo local: %s", file_path)
            QMessageBox.critical(
                self,
                "Erro ao importar",
                f"Não foi possível carregar o arquivo:\n{file_path.name}\n\nErro: {e}",
            )

    def _import_grib(self, file_path: Path):
        """Restaurador de Contexto Offline — importa GRIB2 via pipeline oficial.

        Dois caminhos:
        1. MSL → reconstrói carta sinótica completa (PNMM + Espessura)
        2. PL/Superfície → injeta camada via add_pl_layer + add_layer_entry
        """
        import re

        fname = file_path.name

        # ─── Regex para nome padrão ECMWF do CartoMet ───
        # PL:  ecmwf_{param}_{YYYYMMDD}_{cycle}_{level}hPa_f{step}.grib2
        # SFC: ecmwf_{param}_{YYYYMMDD}_{cycle}_f{step}.grib2
        pat_pl = re.compile(r"^ecmwf_(.+?)_(\d{8})_(latest|\d{2}Z)_(\d+)hPa_f(\d{3})\.grib2?$")
        pat_sfc = re.compile(r"^ecmwf_(.+?)_(\d{8})_(latest|\d{2}Z)_f(\d{3})\.grib2?$")

        m_pl = pat_pl.match(fname)
        m_sfc = pat_sfc.match(fname)

        if not m_pl and not m_sfc:
            QMessageBox.warning(
                self,
                "Nome de arquivo não reconhecido",
                f"O arquivo '{fname}' não segue o padrão ECMWF do CartoMet:\n\n"
                f"  ecmwf_{{param}}_{{YYYYMMDD}}_{{ciclo}}_f{{step}}.grib2\n"
                f"  ecmwf_{{param}}_{{YYYYMMDD}}_{{ciclo}}_{{nível}}hPa_f{{step}}.grib2\n\n"
                f"Renomeie o arquivo seguindo o padrão acima\n"
                f"ou use dados baixados diretamente pelo CartoMet.",
            )
            return

        # ─── Parse dos metadados do nome do arquivo ───
        if m_pl:
            param_str = m_pl.group(1)
            date_str = m_pl.group(2)
            cycle_tag = m_pl.group(3)
            level = int(m_pl.group(4))
            step = int(m_pl.group(5))
        else:
            param_str = m_sfc.group(1)
            date_str = m_sfc.group(2)
            cycle_tag = m_sfc.group(3)
            level = None
            step = int(m_sfc.group(4))

        cycle = None if cycle_tag == "latest" else int(cycle_tag.replace("Z", ""))
        data_dir = file_path.parent
        extent = self.settings_panel.get_extent()
        smoothing = self.config.smoothing_sigma

        # ─── Mapa reverso: param do filename → chave VARIABLE_REGISTRY ───
        grib_param_to_key = {}
        for key, info in VARIABLE_REGISTRY.items():
            if info.get("category") == "derived":
                continue
            param_tag = "_".join(info["param"])
            # Primeira entrada tem prioridade (wind > wind_speed para "u_v")
            if param_tag not in grib_param_to_key:
                grib_param_to_key[param_tag] = key

        # ═══════════════════════════════════════════════════════════════
        #  CAMINHO 1: MSL → Carta Sinótica Completa (PNMM + Espessura)
        # ═══════════════════════════════════════════════════════════════
        if param_str == "msl":
            logger.info(
                "Importando MSL como carta sinótica: date=%s cycle=%s step=%d dir=%s",
                date_str,
                cycle_tag,
                step,
                data_dir,
            )

            data = load_synoptic_data(
                extent=extent,
                step=step,
                cycle=cycle,
                cycle_date=date_str,
                data_dir=data_dir,
                smoothing_sigma=smoothing,
                force_download=False,
            )

            # Injeta via pipeline oficial (mesmo que _on_download_ok)
            self.canvas.set_synoptic_data(data)
            self.settings_panel.update_rodada_from_data(data)

            rodada_info = f"Rodada: {data.base_time}" if data.base_time else ""
            self.status_label.setText(
                f"● Importado: Carta Sinótica  |  {rodada_info}  |  "
                f"Válido: {data.valid_time} UTC  |  Step: +{data.step}h"
            )
            self.status_label.setStyleSheet("color: #27AE60;")
            return

        # ═══════════════════════════════════════════════════════════════
        #  CAMINHO 2: PL / Superfície → Pipeline oficial de camadas
        # ═══════════════════════════════════════════════════════════════
        reg_key = grib_param_to_key.get(param_str)
        if reg_key is None:
            available = ", ".join(sorted(grib_param_to_key.keys()))
            QMessageBox.warning(
                self,
                "Variável não reconhecida",
                f"O parâmetro '{param_str}' extraído do nome do arquivo\n"
                f"não corresponde a nenhuma variável suportada.\n\n"
                f"Parâmetros reconhecidos: {available}",
            )
            return

        logger.info(
            "Importando campo: %s level=%s step=%d date=%s cycle=%s dir=%s",
            reg_key,
            level,
            step,
            date_str,
            cycle_tag,
            data_dir,
        )

        # Dispatch para a função de carga correta
        wind_type = "barbs"

        if reg_key == "olr":
            data = load_olr(
                extent=extent,
                step=step,
                cycle=cycle,
                cycle_date=date_str,
                data_dir=data_dir,
                smoothing_sigma=smoothing,
                force_download=False,
            )
            layer_id = "olr"

        elif reg_key == "tcwv":
            data = load_tcwv(
                extent=extent,
                step=step,
                cycle=cycle,
                cycle_date=date_str,
                data_dir=data_dir,
                smoothing_sigma=smoothing,
                force_download=False,
            )
            layer_id = "tcwv"

        else:
            # Variável em nível de pressão
            if level is None:
                QMessageBox.warning(
                    self,
                    "Nível não identificado",
                    f"A variável '{reg_key}' requer nível de pressão,\n"
                    f"mas o nome do arquivo não contém '{{nível}}hPa'.\n\n"
                    f"Padrão esperado:\n"
                    f"  ecmwf_{param_str}_YYYYMMDD_ciclo_NNNhPa_fSSS.grib2",
                )
                return

            data = load_pl_variable(
                variable_key=reg_key,
                level=level,
                extent=extent,
                step=step,
                cycle=cycle,
                cycle_date=date_str,
                data_dir=data_dir,
                smoothing_sigma=smoothing,
                force_download=False,
            )

            layer_id = f"wind_{level}_barbs" if reg_key == "wind" else f"{reg_key}_{level}"

        # Injeta via pipeline oficial (mesmo que _on_pl_download_ok)
        self.field_panel.remove_layer_entry(layer_id)
        self.canvas.add_pl_layer(layer_id, data, wind_type)

        var_info = VARIABLE_REGISTRY.get(data.variable, {})
        nome = var_info.get("nome", data.variable)

        label = f"{nome} {data.level} hPa" if data.level > 0 else nome

        detail = data.unit
        if data.variable == "wind":
            detail = wind_type

        self.field_panel.add_layer_entry(layer_id, label, detail)

        self.status_label.setText(f"● Importado: {label}  |  {data.valid_time} UTC")
        self.status_label.setStyleSheet("color: #27AE60;")

    def _import_netcdf(self, file_path: Path):
        """Importa arquivo NetCDF local (satélite ou campo genérico)."""
        import xarray as xr

        ds = xr.open_dataset(file_path)

        # Heurística: se tem coordenadas de satélite (x, y, goes_imager_projection)
        if "goes_imager_projection" in ds or "Sectorized_CMI" in ds.data_vars:
            self._import_satellite_nc(ds, file_path)
            ds.close()
            return

        # Campo genérico NetCDF
        var_names = list(ds.data_vars)
        self.settings_panel.get_extent()

        loaded = []
        for vname in var_names:
            da = ds[vname]
            if da.ndim < 2:
                continue

            # Tenta selecionar região se tem lon/lat
            lons = lats = None
            for coord_name in ("longitude", "lon"):
                if coord_name in da.coords:
                    lons = da[coord_name].values
                    break
            for coord_name in ("latitude", "lat"):
                if coord_name in da.coords:
                    lats = da[coord_name].values
                    break

            if lons is None or lats is None:
                continue

            values = da.values
            if values.ndim > 2:
                values = values[0]  # Pega primeiro time step / nível

            data = PLFieldData(
                values=values,
                lons=lons,
                lats=lats,
                variable=vname,
                level=0,
                unit=da.attrs.get("units", ""),
                valid_time="",
                base_time="",
                step=0,
            )

            layer_id = f"local_{vname}"
            self.field_panel.remove_layer_entry(layer_id)
            self.canvas.add_pl_layer(layer_id, data, "barbs")
            self.field_panel.add_layer_entry(layer_id, vname, f"Local: {file_path.name}")
            loaded.append(vname)

        ds.close()

        if loaded:
            self.status_label.setText(f"● Importado: {', '.join(loaded)}")
            self.status_label.setStyleSheet("color: #27AE60;")
        else:
            QMessageBox.information(
                self, "Sem dados", f"Nenhuma variável 2D reconhecida em:\n{file_path.name}"
            )

    def _import_satellite_nc(self, ds, file_path: Path):
        """Importa NetCDF de satélite GOES no formato padrão."""
        import numpy as np

        try:
            if "Sectorized_CMI" in ds.data_vars:
                data_arr = ds["Sectorized_CMI"].values
            elif "CMI" in ds.data_vars:
                data_arr = ds["CMI"].values
            else:
                first_var = list(ds.data_vars)[0]
                data_arr = ds[first_var].values

            proj = ds.get("goes_imager_projection", None)
            if proj is not None:
                sat_lon = float(proj.attrs.get("longitude_of_projection_origin", -75.0))
                sat_h = float(proj.attrs.get("perspective_point_height", 35786023.0))
                sat_sweep = proj.attrs.get("sweep_angle_axis", "x")
            else:
                sat_lon, sat_h, sat_sweep = -75.0, 35786023.0, "x"

            x = ds["x"].values if "x" in ds.coords else np.arange(data_arr.shape[1])
            y = ds["y"].values if "y" in ds.coords else np.arange(data_arr.shape[0])

            sat_data = SatelliteData(
                data=data_arr - 273.15 if np.nanmean(data_arr) > 200 else data_arr,
                x=x,
                y=y,
                sat_lon=sat_lon,
                sat_h=sat_h,
                sat_sweep=sat_sweep,
                time_str=file_path.stem,
                filename=file_path.name,
            )

            self.canvas.plot_satellite(sat_data)
            self.satellite_panel.toggle_check.setVisible(True)
            self.satellite_panel.toggle_check.setChecked(True)
            self.satellite_panel.status_label.setText(f"✓ Local: {file_path.name}")
            self.satellite_panel.status_label.setStyleSheet("font-size: 10px; color: #27AE60;")

            self.status_label.setText(f"● Satélite importado: {file_path.name}")
            self.status_label.setStyleSheet("color: #27AE60;")
        except Exception as e:
            raise RuntimeError(f"Erro ao processar NetCDF de satélite: {e}") from e

    def _loczcit_methodology_path(self):
        """Localiza o docs/Metodologia_LOCZCIT-PA.md (dev ou empacotado) ou None."""
        candidates = [
            Path(__file__).resolve().parents[2] / "docs" / "Metodologia_LOCZCIT-PA.md",
            Path.cwd() / "docs" / "Metodologia_LOCZCIT-PA.md",
        ]
        if getattr(sys, "frozen", False):  # PyInstaller: docs/ empacotado em _MEIPASS
            candidates.insert(0, Path(sys._MEIPASS) / "docs" / "Metodologia_LOCZCIT-PA.md")
        for p in candidates:
            if p.exists():
                return p
        return None

    def _show_about_loczcit(self):
        """Diálogo 'Sobre o Índice ZCIT (LOCZCIT-PA)' — categorias, cores, limiares."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtWidgets import QTextBrowser

        from cartomet_br.data.loczcit_pa_engine import (
            CATEGORY_COLORS,
            OCEAN_MASK_THRESHOLD,
            OLR_THRESHOLD_MODERATE,
            OLR_THRESHOLD_STRONG,
            OLR_THRESHOLD_WEAK,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("Sobre o Índice ZCIT (LOCZCIT-PA)")
        dlg.setMinimumSize(580, 560)
        dlg.setStyleSheet(DARK_STYLE)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        m, g, y, r = CATEGORY_COLORS  # 0 Magenta, 1 Verde, 2 Amarelo, 3 Vermelho
        html = f"""
        <h2 style='color:#E67E22; margin-bottom:2px;'>Índice LOCZCIT-PA</h2>
        <p style='color:#BDC3C7; margin-top:0;'><i>Localização da ZCIT — Potencial Acoplado</i></p>
        <p style='color:#ECF0F1;'>Funde <b>três forçantes geofísicas</b> que precisam coexistir
        espacialmente para a Zona de Convergência Intertropical existir:</p>
        <ul style='color:#ECF0F1;'>
          <li><b>∇TSM</b> — gradiente térmico do oceano (skin temperature do IFS, máscara
              <code>lsm&nbsp;&le;&nbsp;{OCEAN_MASK_THRESHOLD}</code>)</li>
          <li><b>C</b> — convergência do vento de baixos níveis (10&nbsp;m)</li>
          <li><b>F<sub>OLR</sub></b> — radiação de onda longa <b>desacumulada</b>
              (Técnica B: rodada anterior madura, mitigação de <i>spin-up</i>)</li>
        </ul>
        <p style='color:#ECF0F1;'>As forçantes são normalizadas (Min-Max meridional) e acopladas
        por <b>média aritmética</b> (Navalha de Ockham). A banda é definida pela <b>máscara
        ativa</b> <code>oceano &and; (OLR&lt;240 &or; C&gt;C<sub>THR</sub>)</code> — que resgata o
        ramo sul da ZCIT — e por um <b>envelope sazonal de latitude</b>, depois classificadas
        pelos limiares físicos de OLR:</p>
        <table cellpadding='6' style='color:#ECF0F1; border-collapse:collapse;'>
          <tr style='background:#1A252F;'>
            <th>Categoria</th><th>Cor</th><th>Limiar de F<sub>OLR</sub></th></tr>
          <tr><td><b>Forte</b></td>
            <td><span style='background:{r}; color:{r};'>&nbsp;&nbsp;&nbsp;</span> Vermelho</td>
            <td>F<sub>OLR</sub> &le; {OLR_THRESHOLD_STRONG:.0f} W/m²</td></tr>
          <tr><td><b>Moderada</b></td>
            <td><span style='background:{y}; color:{y};'>&nbsp;&nbsp;&nbsp;</span> Amarelo</td>
            <td>{OLR_THRESHOLD_STRONG:.0f} &lt; F<sub>OLR</sub> &le; {OLR_THRESHOLD_MODERATE:.0f}</td></tr>
          <tr><td><b>Fraca</b></td>
            <td><span style='background:{g}; color:{g};'>&nbsp;&nbsp;&nbsp;</span> Verde</td>
            <td>{OLR_THRESHOLD_MODERATE:.0f} &lt; F<sub>OLR</sub> &le; {OLR_THRESHOLD_WEAK:.0f}</td></tr>
          <tr><td><b>Cinemática</b></td>
            <td><span style='background:{m}; color:{m};'>&nbsp;&nbsp;&nbsp;</span> Magenta</td>
            <td>F<sub>OLR</sub> &gt; {OLR_THRESHOLD_WEAK:.0f}, banda ativa por convergência (ramo sul)</td></tr>
          <tr><td><i>Nulo</i></td><td>transparente</td>
            <td>fora da máscara ativa / do envelope (céu limpo verdadeiro)</td></tr>
        </table>
        <p style='color:#ECF0F1; margin-top:10px;'>O raster <b>não traça a carta</b> — ele
        quantifica e espacializa o potencial de acoplamento físico, orientando o traçado manual
        com a simbologia <b>[6] ZCIT</b> (<i>human-in-the-loop</i>).</p>
        <hr style='border-color:#5D6D7E;'>
        <p style='color:#95A5A6; font-size:11px;'>Linhagem científica:
        <b>Rocha (2022)</b> — TCC, UFPA (LOCZCIT-IQR); <b>Ferreira et al. (2005)</b>;
        Gadgil &amp; Guruprasad (1990); Lindzen &amp; Nigam (1987).
        Forçantes do <b>ECMWF IFS Cycle 50r1</b>.</p>
        """
        browser = QTextBrowser()
        browser.setHtml(html)
        browser.setOpenExternalLinks(True)
        layout.addWidget(browser)

        btn_row = QHBoxLayout()
        open_btn = QPushButton("📖 Abrir Metodologia Completa")
        open_btn.setStyleSheet(
            "QPushButton{background:#E67E22;padding:7px 14px;font-weight:bold;border-radius:4px;}"
            "QPushButton:hover{background:#F39C12;}"
        )

        def _open_methodology():
            p = self._loczcit_methodology_path()
            if p is None:
                QMessageBox.information(
                    dlg,
                    "Metodologia",
                    "O documento da metodologia não foi encontrado nesta instalação.\n"
                    "Ele está disponível no repositório do CartoMet BR (docs/).",
                )
                return
            # Renderiza o .md como HTML legível (equações + diagramas) e abre no navegador
            try:
                from cartomet_br.gui.methodology import render_methodology_html

                html_path = render_methodology_html(p)
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(html_path)))
            except Exception as exc:
                logger.warning("Falha ao renderizar metodologia em HTML: %s", exc)
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))  # fallback: abre o .md

        open_btn.clicked.connect(_open_methodology)
        close_btn = QPushButton("Fechar")
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(open_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dlg.exec()

    def _blocking_methodology_path(self):
        """Localiza o docs/Metodologia_Bloqueio_Z500.md (dev ou empacotado) ou None."""
        candidates = [
            Path(__file__).resolve().parents[2] / "docs" / "Metodologia_Bloqueio_Z500.md",
            Path.cwd() / "docs" / "Metodologia_Bloqueio_Z500.md",
        ]
        if getattr(sys, "frozen", False):  # PyInstaller: docs/ empacotado em _MEIPASS
            candidates.insert(0, Path(sys._MEIPASS) / "docs" / "Metodologia_Bloqueio_Z500.md")
        for p in candidates:
            if p.exists():
                return p
        return None

    def _show_about_blocking(self):
        """Diálogo 'Sobre a Análise de Bloqueio (Z500)' — fórmula, climatologia, leitura."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtWidgets import QTextBrowser

        dlg = QDialog(self)
        dlg.setWindowTitle("Sobre a Análise de Bloqueio (Z500)")
        dlg.setMinimumSize(580, 560)
        dlg.setStyleSheet(DARK_STYLE)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        html = """
        <h2 style='color:#2980B9; margin-bottom:2px;'>Bloqueio Atmosférico (Z500)</h2>
        <p style='color:#BDC3C7; margin-top:0;'><i>Anomalia de altura geopotencial em
        500&nbsp;hPa</i></p>
        <p style='color:#ECF0F1;'>A carta mostra o desvio do escoamento em relação ao
        estado médio do dia:</p>
        <p style='color:#ECF0F1; text-align:center;'>
        <b>Anomalia&nbsp;=&nbsp;Z<sub>500</sub> (IFS, rodada&nbsp;+&nbsp;step)
        &nbsp;&minus;&nbsp; Z&#772;<sub>500</sub> (ERA5 1991&ndash;2020, dia-do-ano,
        hora)</b></p>
        <ul style='color:#ECF0F1;'>
          <li><b>Climatologia:</b> ERA5 1991&ndash;2020 (normal OMM), 00Z/12Z, média
              anual + 4 harmônicos (FFT) — ciclo sazonal removido sem ruído;</li>
          <li><b>Grade idêntica</b> ao IFS Open Data (0,25°) — subtração direta,
              sem regrid;</li>
          <li><b>Download por dia:</b> só o NetCDF do dia (~800&nbsp;KB) é baixado do
              repositório do CartoMet BR, com verificação <code>sha256</code> e cache
              local.</li>
        </ul>
        <p style='color:#ECF0F1;'><b>Como ler:</b></p>
        <table cellpadding='6' style='color:#ECF0F1; border-collapse:collapse;'>
          <tr style='background:#1A252F;'><th>Assinatura</th><th>Leitura</th></tr>
          <tr><td><span style='background:#B2182B; color:#B2182B;'>&nbsp;&nbsp;&nbsp;</span>
            anomalia <b>positiva</b> &gtrsim&nbsp;+100&nbsp;gpm, persistente, em
            latitudes médias-altas</td>
            <td>crista anômala — <b>candidata a bloqueio</b></td></tr>
          <tr><td><span style='background:#2166AC; color:#2166AC;'>&nbsp;&nbsp;&nbsp;</span>
            anomalias <b>negativas</b> ladeando a positiva (~25&ndash;35°S)</td>
            <td>dipolo do <b>ômega invertido</b> (padrão A&ndash;B)</td></tr>
          <tr><td>linha <b>cinza-escura</b></td>
            <td>contorno do zero — fronteira crista/cavado anômalos</td></tr>
        </table>
        <p style='color:#ECF0F1; margin-top:10px;'>A <b>persistência</b> é essencial:
        avalie vários <i>steps</i> consecutivos. O campo orienta — a identificação
        final do bloqueio é do previsor (<i>human-in-the-loop</i>).</p>
        <p style='color:#95A5A6; font-size:11px;'>Horários 06Z/18Z usam o horário
        climatológico mais próximo (ciclo diurno de Z500 é pequeno) — a carta marca a
        aproximação com "≈".</p>
        <hr style='border-color:#5D6D7E;'>
        <p style='color:#95A5A6; font-size:11px;'>Climatologia: <b>Hersbach et al.
        (2020)</b>, doi:10.1002/qj.3803. <i>Generated using Copernicus Climate Change
        Service information [2026]</i>. Previsões: ECMWF Open Data (CC&nbsp;BY&nbsp;4.0).
        Conceito: Rex (1950); Tibaldi &amp; Molteni (1990).</p>
        """
        browser = QTextBrowser()
        browser.setHtml(html)
        browser.setOpenExternalLinks(True)
        layout.addWidget(browser)

        btn_row = QHBoxLayout()
        open_btn = QPushButton("📖 Abrir Metodologia Completa")
        open_btn.setStyleSheet(
            "QPushButton{background:#2980B9;padding:7px 14px;font-weight:bold;border-radius:4px;}"
            "QPushButton:hover{background:#3498DB;}"
        )

        def _open_methodology():
            p = self._blocking_methodology_path()
            if p is None:
                QMessageBox.information(
                    dlg,
                    "Metodologia",
                    "O documento da metodologia não foi encontrado nesta instalação.\n"
                    "Ele está disponível no repositório do CartoMet BR (docs/).",
                )
                return
            # Renderiza o .md como HTML legível (equações) e abre no navegador
            try:
                from cartomet_br.gui.methodology import render_methodology_html

                html_path = render_methodology_html(p)
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(html_path)))
            except Exception as exc:
                logger.warning("Falha ao renderizar metodologia em HTML: %s", exc)
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))  # fallback: abre o .md

        open_btn.clicked.connect(_open_methodology)
        close_btn = QPushButton("Fechar")
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(open_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dlg.exec()

    def _show_about(self):
        about_dialog = QDialog(self)
        about_dialog.setWindowTitle(f"Sobre {APP_NAME}")
        about_dialog.setMinimumWidth(420)
        about_dialog.setStyleSheet(DARK_STYLE)

        layout = QVBoxLayout(about_dialog)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        logo_path = get_logo_path()
        if logo_path and logo_path.exists():
            logo_label = QLabel()
            pixmap = QPixmap(str(logo_path))
            logo_label.setPixmap(
                pixmap.scaled(
                    120,
                    120,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(logo_label)

        info_html = f"""
        <div style='text-align: center;'>
            <h2 style='color: #3498DB; margin-bottom: 4px;'>{APP_NAME}</h2>
            <p style='color: #BDC3C7;'>{APP_DESCRIPTION}</p>
            <p><b>Versão:</b> {APP_VERSION}</p>
            <hr style='border-color: #5D6D7E;'>
            <p style='color: #1ABC9C; font-weight: bold;'>Desenvolvedor &amp; Idealizador</p>
            <p><b>{APP_AUTHOR}</b><br/>
            <small style='color: #95A5A6;'>Meteorologista | Mestre PPGGRD/UFPA<br/>
            Analista e Desenvolvedor de Sistemas</small></p>
            <p style='margin-top: 8px; color: #1ABC9C; font-weight: bold;'>Idealizador</p>
            <p><b>Prof. Dr. Everaldo Barreiros de Souza</b><br/>
            <small style='color: #95A5A6;'>Professor Titular — IG/UFPA<br/>
            Doutor em Meteorologia — USP/IAG</small></p>
            <hr style='border-color: #5D6D7E;'>
            <p style='font-size: 10px;'><b>Instituições</b><br/>
            UFPA — Universidade Federal do Pará<br/>
            IG — Instituto de Geociências<br/>
            FAMET — Faculdade de Meteorologia<br/>
            PPGGRD — Gestão de Riscos e Desastres na Amazônia</p>
            <hr style='border-color: #5D6D7E;'>
            <p style='color: #7F8C8D; font-size: 10px;'>
            Dados: ECMWF Open Data (CC BY 4.0)<br/>
            Satélite: NOAA GOES-East (Domínio Público)</p>
        </div>
        """
        info = QLabel(info_html)
        info.setWordWrap(True)
        layout.addWidget(info)

        btn = QPushButton("OK")
        btn.setMinimumWidth(100)
        btn.clicked.connect(about_dialog.accept)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        about_dialog.exec()


# ═══════════════════════════════════════════════════════════════════════════════
#  FUNÇÃO PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════


def run_gui():
    """Inicia a aplicação GUI."""
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("PPGGRD-UFPA")

    icon_path = get_icon_path()
    if icon_path and icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Janela de Boas-Vindas
    welcome = WelcomeDialog()
    if welcome.exec() != QDialog.DialogCode.Accepted:
        sys.exit(0)

    # Configurações salvas
    settings = QSettings("PPGGRD-UFPA", APP_NAME)
    saved_dir = settings.value("data_dir")

    if saved_dir and Path(saved_dir).exists():
        data_dir = Path(saved_dir)
    else:
        dialog = FirstRunDialog()
        dialog.setStyleSheet(DARK_STYLE)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            data_dir = dialog.data_dir
            settings.setValue("data_dir", str(data_dir))
        else:
            sys.exit(0)

    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "output").mkdir(exist_ok=True)

    window = MainWindow(data_dir)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    run_gui()
