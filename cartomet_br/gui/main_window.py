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

import gc
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QStatusBar, QDockWidget, QLabel, QPushButton,
    QFrame, QSizePolicy, QMessageBox, QFileDialog,
    QProgressBar, QScrollArea, QDialog, QInputDialog,
)
from PyQt6.QtCore import Qt, QSize, QSettings, QTimer
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QPixmap

from cartomet_br.core.config import Config
from cartomet_br.data.ecmwf import (
    VARIABLE_REGISTRY, PLFieldData, SatelliteData,
    load_synoptic_data, load_pl_variable, load_olr, load_tcwv,
)

from cartomet_br.gui._constants import (
    APP_NAME, APP_VERSION, APP_AUTHOR, APP_DESCRIPTION,
    get_icon_path, get_logo_path,
)
from cartomet_br.gui.themes import DARK_STYLE
from cartomet_br.gui.download_dialog import (
    DownloadThread, PLDownloadThread, SatDownloadThread, SSTDownloadThread,
    DownloadProgressDialog,
)
from cartomet_br.gui.dialogs import WelcomeDialog, FirstRunDialog
from cartomet_br.gui.drawing_panel import SymbologyPanel
from cartomet_br.gui.layer_panel import SettingsPanel, FieldLayerPanel, SatellitePanel, SSTPanel
from cartomet_br.gui.map_canvas import MapCanvas

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

        self.setStyleSheet(DARK_STYLE)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Canvas
        self.canvas = MapCanvas(config=self.config)
        main_layout.addWidget(self.canvas, stretch=1)

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

        print_action = QAction("Capturar Tela do Mapa...", self)
        print_action.setShortcut(QKeySequence.StandardKey.Print)
        print_action.triggered.connect(self._print_canvas)
        file_menu.addAction(print_action)

        file_menu.addSeparator()

        config_dir_action = QAction("Configurar Diretório de Dados...", self)
        config_dir_action.triggered.connect(self._configure_data_dir)
        file_menu.addAction(config_dir_action)

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
            logo_label.setPixmap(pixmap.scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
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

        toolbar.addSeparator()

        export_btn = QPushButton("📤 Exportar")
        export_btn.setStyleSheet("background-color: #9B59B6; padding: 6px 14px; font-size: 11px;")
        export_btn.clicked.connect(self._save_figure)
        toolbar.addWidget(export_btn)

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
        self.symbol_panel.finalize_requested.connect(self._finalize_line)
        self.symbol_panel.clear_requested.connect(self.canvas.clear_all)
        self.symbol_panel.undo_requested.connect(self.canvas.undo_point)
        self.symbol_panel.redo_requested.connect(self.canvas.redo_action)
        self.symbol_panel.emoji_mode_toggled.connect(self._on_emoji_mode_toggled)
        self.symbol_panel.emoji_selected.connect(lambda e: setattr(self.canvas, 'current_emoji', e))
        self.symbol_panel.emoji_size_changed.connect(lambda s: setattr(self.canvas, '_emoji_fontsize', s))

        self.canvas.point_added.connect(self._on_point_added)
        self.canvas.coords_updated.connect(self._on_coords_updated)

        self.settings_panel.update_requested.connect(self._download_data)
        self.settings_panel.region_changed.connect(self._on_region_changed)
        self.settings_panel.theme_changed.connect(self._on_theme_changed)
        self.settings_panel.layers_changed.connect(self._on_layer_toggled)

        self.field_panel.add_layer_requested.connect(self._on_add_pl_layer)
        self.field_panel.toggle_layer_requested.connect(self._on_toggle_pl_layer)
        self.field_panel.remove_layer_requested.connect(self._on_remove_pl_layer)
        self.field_panel.preset_requested.connect(self._on_preset_requested)

        self.canvas.annotation_requested.connect(self._on_annotation_requested)

        self.satellite_panel.download_requested.connect(self._on_sat_download)
        self.satellite_panel.toggle_requested.connect(self.canvas.toggle_satellite)

        self.sst_panel.download_requested.connect(self._on_sst_download)
        self.sst_panel.toggle_requested.connect(self.canvas.toggle_sst)

    # ═══════════════════════════════════════════════════════════════════════
    #  MODOS DE INTERAÇÃO
    # ═══════════════════════════════════════════════════════════════════════

    def _toggle_draw_mode(self, checked):
        if checked:
            self.annotate_btn.setChecked(False)
            self.ruler_btn.setChecked(False)
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

    def _on_annotation_requested(self, x, y):
        """Abre diálogo para o usuário digitar texto da anotação."""
        from PyQt6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(
            self, "Anotação no Mapa",
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
        self.sat_download_thread.progress.connect(
            lambda msg: self._update_sat_dialog_status(msg)
        )
        self.sat_download_thread.download_percent.connect(
            self._sat_dl_dialog.update_percent
        )
        self.sat_download_thread.cache_hit.connect(self._on_sat_cache_hit)
        self.sat_download_thread.finished_ok.connect(self._on_sat_download_ok)
        self.sat_download_thread.finished_error.connect(self._on_sat_download_error)
        self.sat_download_thread.start()

        self._sat_dl_dialog.show()

    def _update_sat_dialog_status(self, msg):
        self.status_label.setText(f"● {msg}")
        if hasattr(self, '_sat_dl_dialog') and self._sat_dl_dialog:
            self._sat_dl_dialog.update_status(msg)

    def _on_sat_cache_hit(self, filename):
        if hasattr(self, '_sat_dl_dialog') and self._sat_dl_dialog:
            self._sat_dl_dialog.update_status(
                f"✓ Arquivo já existe no cache!\n{filename}"
            )
            self._sat_dl_dialog.update_percent(100)

    def _cancel_sat_download(self):
        if self.sat_download_thread and self.sat_download_thread.isRunning():
            self.sat_download_thread.terminate()
            self.sat_download_thread.wait(3000)
            self.sat_download_thread = None

        self.satellite_panel.set_downloading(False)

        if hasattr(self, '_sat_dl_dialog') and self._sat_dl_dialog:
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

        if hasattr(self, '_sat_dl_dialog') and self._sat_dl_dialog:
            self._sat_dl_dialog.finish_ok()
            self._sat_dl_dialog = None

    def _on_sat_download_error(self, error_msg):
        self.satellite_panel.set_downloading(False)
        self.status_label.setText("● Erro no download do satélite")
        self.status_label.setStyleSheet("color: #E74C3C;")

        if hasattr(self, '_sat_dl_dialog') and self._sat_dl_dialog:
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
        self.sst_download_thread.progress.connect(
            lambda msg: self._update_sst_dialog_status(msg)
        )
        self.sst_download_thread.download_percent.connect(
            self._sst_dl_dialog.update_percent
        )
        self.sst_download_thread.finished_ok.connect(self._on_sst_download_ok)
        self.sst_download_thread.finished_error.connect(self._on_sst_download_error)
        self.sst_download_thread.start()

        self._sst_dl_dialog.show()

    def _update_sst_dialog_status(self, msg):
        self.status_label.setText(f"● {msg}")
        if hasattr(self, '_sst_dl_dialog') and self._sst_dl_dialog:
            self._sst_dl_dialog.update_status(msg)

    def _cancel_sst_download(self):
        if self.sst_download_thread and self.sst_download_thread.isRunning():
            self.sst_download_thread.terminate()
            self.sst_download_thread.wait(3000)
            self.sst_download_thread = None

        self.sst_panel.set_downloading(False)

        if hasattr(self, '_sst_dl_dialog') and self._sst_dl_dialog:
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

        if hasattr(self, '_sst_dl_dialog') and self._sst_dl_dialog:
            self._sst_dl_dialog.finish_ok()
            self._sst_dl_dialog = None

    def _on_sst_download_error(self, error_msg):
        self.sst_panel.set_downloading(False)
        self.status_label.setText("● Erro no download da TSM")
        self.status_label.setStyleSheet("color: #E74C3C;")

        if hasattr(self, '_sst_dl_dialog') and self._sst_dl_dialog:
            self._sst_dl_dialog.finish_error()
            self._sst_dl_dialog = None

        QMessageBox.warning(self, "Erro TSM", error_msg)

    # ═══════════════════════════════════════════════════════════════════════
    #  HANDLERS DE DESENHO
    # ═══════════════════════════════════════════════════════════════════════

    def _on_point_added(self, x, y):
        self.symbol_panel.update_points(len(self.canvas.points_x))

    def _on_coords_updated(self, x, y):
        self.coords_label.setText(f"Lat: {y:.2f}° Lon: {x:.2f}°")

    def _finalize_line(self):
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

        self._dl_existing_files = set(self.config.data_dir.glob("*.grib2")) if self.config.data_dir else set()

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
        if hasattr(self, '_dl_dialog') and self._dl_dialog:
            self._dl_dialog.update_status(msg)

    def _on_download_ok(self, data):
        self.settings_panel.set_downloading(False)
        self.progress_bar.setVisible(False)

        if hasattr(self, '_dl_dialog') and self._dl_dialog:
            self._dl_dialog.finish_ok()
            self._dl_dialog = None

        self.canvas.set_synoptic_data(data)
        self.settings_panel.update_rodada_from_data(data)

        rodada_info = f"Rodada: {data.base_time}" if data.base_time else ""
        self.status_label.setText(
            f"● Dados carregados  |  {rodada_info}  |  "
            f"Válido: {data.valid_time} UTC  |  Step: +{data.step}h"
        )
        self.status_label.setStyleSheet("color: #27AE60;")

    def _on_download_error(self, error_msg):
        self.settings_panel.set_downloading(False)
        self.progress_bar.setVisible(False)

        if hasattr(self, '_dl_dialog') and self._dl_dialog:
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

            existing = getattr(self, '_dl_existing_files', set())
            self._cleanup_new_files(existing)

        self.settings_panel.set_downloading(False)
        self.progress_bar.setVisible(False)

        if hasattr(self, '_dl_dialog') and self._dl_dialog:
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

            existing = getattr(self, '_pl_existing_files', set())
            self._cleanup_new_files(existing)

        self.progress_bar.setVisible(False)

        if hasattr(self, '_pl_dl_dialog') and self._pl_dl_dialog:
            self._pl_dl_dialog.accept()
            self._pl_dl_dialog = None

        self.status_label.setText("● Download cancelado")
        self.status_label.setStyleSheet("color: #F39C12;")

    def _cleanup_new_files(self, existing_before: set):
        """Remove arquivos GRIB2 criados durante o download cancelado."""
        if not self.config.data_dir or not self.config.data_dir.exists():
            return

        current = set(self.config.data_dir.glob("*.grib2"))
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
                self, "Aguarde",
                "Um download já está em andamento. Aguarde concluir."
            )
            return

        step = self.settings_panel.get_step()
        if var_key == "olr" and step < 3:
            QMessageBox.warning(
                self, "OLR requer step >= 3h",
                "A variável OLR (ttr) é acumulada e vale zero no step 0.\n\n"
                "Altere o step para +3h ou mais antes de adicionar OLR."
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

        self._pl_existing_files = set(self.config.data_dir.glob("*.grib2")) if self.config.data_dir else set()

        self._pl_dl_dialog = DownloadProgressDialog(
            f"Baixando {nome}", parent=self
        )
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
        if hasattr(self, '_pl_dl_dialog') and self._pl_dl_dialog:
            self._pl_dl_dialog.update_status(msg)

    def _on_pl_download_ok(self, layer_id: str, data, wind_type: str):
        self.progress_bar.setVisible(False)

        if hasattr(self, '_pl_dl_dialog') and self._pl_dl_dialog:
            self._pl_dl_dialog.finish_ok()
            self._pl_dl_dialog = None

        self.field_panel.remove_layer_entry(layer_id)
        self.canvas.add_pl_layer(layer_id, data, wind_type)

        var_info = VARIABLE_REGISTRY.get(data.variable, {})
        nome = var_info.get("nome", data.variable)

        if data.level > 0:
            label = f"{nome} {data.level} hPa"
        else:
            label = nome

        detail = data.unit
        if data.variable == "wind":
            detail = wind_type

        self.field_panel.add_layer_entry(layer_id, label, detail)

        self.status_label.setText(
            f"● {label} carregado  |  {data.valid_time} UTC"
        )
        self.status_label.setStyleSheet("color: #27AE60;")

    def _on_pl_download_error(self, error_msg: str):
        self.progress_bar.setVisible(False)

        if hasattr(self, '_pl_dl_dialog') and self._pl_dl_dialog:
            self._pl_dl_dialog.finish_error()
            self._pl_dl_dialog = None

        self.status_label.setText("● Erro ao baixar campo")
        self.status_label.setStyleSheet("color: #E74C3C;")

        if "429" in error_msg or "Limite de requisições" in error_msg:
            QMessageBox.warning(self, "Limite de Requisições (429)", error_msg)
        else:
            QMessageBox.warning(self, "Erro ao Baixar Campo", error_msg)

    def _on_toggle_pl_layer(self, layer_id: str, visible: bool):
        self.canvas.toggle_pl_layer(layer_id, visible)

    def _on_remove_pl_layer(self, layer_id: str):
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

    def _process_preset_queue(self):
        """Processa a próxima camada da fila de preset."""
        if not hasattr(self, '_preset_queue') or not self._preset_queue:
            self.status_label.setText(
                f"● Preset '{getattr(self, '_preset_name', '')}' completo!"
            )
            self.status_label.setStyleSheet("color: #27AE60;")
            return

        if self.pl_download_thread and self.pl_download_thread.isRunning():
            QTimer.singleShot(500, self._process_preset_queue)
            return

        var_key, level, wind_type = self._preset_queue.pop(0)
        self._on_add_pl_layer(var_key, level, wind_type)

        try:
            self.pl_download_thread.finished_ok.connect(
                lambda *_: self._process_preset_queue(),
                Qt.ConnectionType.SingleShotConnection
            )
            self.pl_download_thread.finished_error.connect(
                lambda *_: self._process_preset_queue(),
                Qt.ConnectionType.SingleShotConnection
            )
        except Exception:
            QTimer.singleShot(3000, self._process_preset_queue)

    # ═══════════════════════════════════════════════════════════════════════
    #  OPERAÇÕES DE ARQUIVO
    # ═══════════════════════════════════════════════════════════════════════

    def _new_project(self):
        """Reinicia o programa para um estado completamente limpo."""
        reply = QMessageBox.question(
            self, "Novo Projeto",
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

    def _save_figure(self):
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Salvar Imagem",
            str(self.config.output_dir / f"cartomet_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"),
            "PNG (*.png);;JPEG (*.jpg);;PDF (*.pdf)"
        )
        if not filepath:
            return

        # Seletor de resolução
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
            2,      # índice padrão: 200 DPI
            False,  # não editável
        )
        if not ok:
            return

        dpi = dpi_map[escolha]
        try:
            self.canvas.save_figure(filepath, dpi=dpi)
            self.status_label.setText(f"● Salvo ({dpi} DPI): {Path(filepath).name}")
            self.status_label.setStyleSheet("color: #27AE60;")
        except Exception as e:
            QMessageBox.critical(self, "Erro ao Exportar", f"Não foi possível salvar o arquivo:\n\n{e}")
            self.status_label.setText("● Erro ao exportar")
            self.status_label.setStyleSheet("color: #E74C3C;")

    def _print_canvas(self):
        """Captura pixel-perfect do mapa (Ctrl+P) — idêntica à tela."""
        default_name = f"cartomet_print_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Capturar Tela do Mapa",
            str(self.config.output_dir / default_name),
            "PNG (*.png);;JPEG (*.jpg)",
        )
        if filepath:
            self.canvas.capture_canvas(filepath)
            self.status_label.setText(f"● Captura salva: {Path(filepath).name}")
            self.status_label.setStyleSheet("color: #27AE60;")

    def _configure_data_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Selecione o Diretório de Dados", str(self.data_dir.parent))
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
                    self, "Formato não suportado",
                    f"Extensão '{suffix}' não é suportada.\n\n"
                    "Formatos aceitos: .grib2, .grib, .nc, .netcdf"
                )
        except Exception as e:
            logger.exception("Erro ao importar arquivo local: %s", file_path)
            QMessageBox.critical(
                self, "Erro ao importar",
                f"Não foi possível carregar o arquivo:\n{file_path.name}\n\n"
                f"Erro: {e}"
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
        pat_pl = re.compile(
            r"^ecmwf_(.+?)_(\d{8})_(latest|\d{2}Z)_(\d+)hPa_f(\d{3})\.grib2?$"
        )
        pat_sfc = re.compile(
            r"^ecmwf_(.+?)_(\d{8})_(latest|\d{2}Z)_f(\d{3})\.grib2?$"
        )

        m_pl = pat_pl.match(fname)
        m_sfc = pat_sfc.match(fname)

        if not m_pl and not m_sfc:
            QMessageBox.warning(
                self, "Nome de arquivo não reconhecido",
                f"O arquivo '{fname}' não segue o padrão ECMWF do CartoMet:\n\n"
                f"  ecmwf_{{param}}_{{YYYYMMDD}}_{{ciclo}}_f{{step}}.grib2\n"
                f"  ecmwf_{{param}}_{{YYYYMMDD}}_{{ciclo}}_{{nível}}hPa_f{{step}}.grib2\n\n"
                f"Renomeie o arquivo seguindo o padrão acima\n"
                f"ou use dados baixados diretamente pelo CartoMet."
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
                date_str, cycle_tag, step, data_dir,
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
                self, "Variável não reconhecida",
                f"O parâmetro '{param_str}' extraído do nome do arquivo\n"
                f"não corresponde a nenhuma variável suportada.\n\n"
                f"Parâmetros reconhecidos: {available}"
            )
            return

        logger.info(
            "Importando campo: %s level=%s step=%d date=%s cycle=%s dir=%s",
            reg_key, level, step, date_str, cycle_tag, data_dir,
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
                    self, "Nível não identificado",
                    f"A variável '{reg_key}' requer nível de pressão,\n"
                    f"mas o nome do arquivo não contém '{{nível}}hPa'.\n\n"
                    f"Padrão esperado:\n"
                    f"  ecmwf_{param_str}_YYYYMMDD_ciclo_NNNhPa_fSSS.grib2"
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

            if reg_key == "wind":
                layer_id = f"wind_{level}_barbs"
            else:
                layer_id = f"{reg_key}_{level}"

        # Injeta via pipeline oficial (mesmo que _on_pl_download_ok)
        self.field_panel.remove_layer_entry(layer_id)
        self.canvas.add_pl_layer(layer_id, data, wind_type)

        var_info = VARIABLE_REGISTRY.get(data.variable, {})
        nome = var_info.get("nome", data.variable)

        if data.level > 0:
            label = f"{nome} {data.level} hPa"
        else:
            label = nome

        detail = data.unit
        if data.variable == "wind":
            detail = wind_type

        self.field_panel.add_layer_entry(layer_id, label, detail)

        self.status_label.setText(
            f"● Importado: {label}  |  {data.valid_time} UTC"
        )
        self.status_label.setStyleSheet("color: #27AE60;")

    def _import_netcdf(self, file_path: Path):
        """Importa arquivo NetCDF local (satélite ou campo genérico)."""
        import xarray as xr
        import numpy as np

        ds = xr.open_dataset(file_path)

        # Heurística: se tem coordenadas de satélite (x, y, goes_imager_projection)
        if "goes_imager_projection" in ds or "Sectorized_CMI" in ds.data_vars:
            self._import_satellite_nc(ds, file_path)
            ds.close()
            return

        # Campo genérico NetCDF
        var_names = list(ds.data_vars)
        extent = self.settings_panel.get_extent()

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
                self, "Sem dados",
                f"Nenhuma variável 2D reconhecida em:\n{file_path.name}"
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

            self.canvas.set_satellite(sat_data)
            self.satellite_panel.toggle_check.setVisible(True)
            self.satellite_panel.toggle_check.setChecked(True)
            self.satellite_panel.status_label.setText(f"✓ Local: {file_path.name}")
            self.satellite_panel.status_label.setStyleSheet("font-size: 10px; color: #27AE60;")

            self.status_label.setText(f"● Satélite importado: {file_path.name}")
            self.status_label.setStyleSheet("color: #27AE60;")
        except Exception as e:
            raise RuntimeError(f"Erro ao processar NetCDF de satélite: {e}") from e

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
            logo_label.setPixmap(pixmap.scaled(120, 120, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
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
