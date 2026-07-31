"""
Painéis de configuração e camadas do CartoMet BR.

Contém SettingsPanel (região, tema, rodada ECMWF, step),
FieldLayerPanel (campos em altitude / OLR), SatellitePanel (GOES)
e SSTPanel (TSM MUR SST 1km).
"""

from datetime import UTC, datetime, timedelta

from PyQt6.QtCore import QDate, QSettings, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cartomet_br.core.config import (
    EXTENT_AMSUL,
    EXTENT_BRASIL,
    EXTENT_NORDESTE,
    EXTENT_SUDESTE,
    EXTENT_SUL,
    EXTENT_UFS,
    UF_NOMES,
)
from cartomet_br.data.cities import CITY_DENSITY_FACTORS, DEFAULT_CITY_DENSITY
from cartomet_br.data.ecmwf import (
    PL_LEVELS,
    VARIABLE_REGISTRY,
    estimate_available_cycles,
)
from cartomet_br.data.hydrography import DEFAULT_HYDRO_DETAIL, HYDRO_DETAIL_LEVELS
from cartomet_br.data.stations import (
    DEFAULT_OBS_DENSITY,
    OBS_DENSITY_FACTORS,
    OBS_MODE_ANALYSIS,
    OBS_MODE_LATEST,
    synop_slot,
)
from cartomet_br.gui._constants import (
    AIFS_VALID_STEPS,
    APP_NAME,
    ENS_PROB_THRESHOLDS_MM,
    VALID_STEPS,
)
from cartomet_br.gui.wind_style import (
    DEFAULT_WIND_COLOR,
    DEFAULT_WIND_DENSITY,
    WindStyleControls,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  PAINEL DE SATÉLITE (esquerda, abaixo das simbologias)
# ═══════════════════════════════════════════════════════════════════════════════


class SatellitePanel(QWidget):
    """Painel para download e controle de imagem de satélite GOES."""

    download_requested = pyqtSignal(object)  # datetime alvo
    toggle_requested = pyqtSignal(bool)  # mostrar/ocultar
    detect_cells_requested = pyqtSignal(float)  # detectar células convectivas (limiar °C)
    cells_toggle_requested = pyqtSignal(bool)  # mostrar/ocultar as células

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Título
        title = QLabel("SATÉLITE GOES")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 13px; font-weight: bold; color: #1ABC9C;")
        layout.addWidget(title)

        info = QLabel("Banda 13 — IR 10.3μm")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet("font-size: 10px; color: #999;")
        layout.addWidget(info)

        # Seletor de data
        date_row = QHBoxLayout()
        date_row.addWidget(QLabel("Data:"))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        date_row.addWidget(self.date_edit)
        layout.addLayout(date_row)

        # Seletor de horário sinótico
        hora_row = QHBoxLayout()
        hora_row.addWidget(QLabel("Hora:"))
        self.hora_combo = QComboBox()
        for h in range(24):
            self.hora_combo.addItem(f"{h:02d}Z", h)
        now_utc = datetime.now(UTC)
        self.hora_combo.setCurrentIndex(now_utc.hour)
        hora_row.addWidget(self.hora_combo)
        layout.addLayout(hora_row)

        # Seletor de minuto
        min_row = QHBoxLayout()
        min_row.addWidget(QLabel("Minuto:"))
        self.min_combo = QComboBox()
        for m in (0, 10, 20, 30, 40, 50):
            self.min_combo.addItem(f"{m:02d}", m)
        self.min_combo.setCurrentIndex(0)
        min_row.addWidget(self.min_combo)
        layout.addLayout(min_row)

        # Botão download
        self.download_btn = QPushButton("🛰️ Baixar Imagem IR")
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #1ABC9C; padding: 8px;
                font-size: 12px; font-weight: bold; border-radius: 6px;
            }
            QPushButton:hover { background-color: #16A085; }
            QPushButton:disabled { background-color: #555; }
        """)
        self.download_btn.clicked.connect(self._on_download_clicked)
        layout.addWidget(self.download_btn)

        # Toggle mostrar/ocultar
        self.toggle_check = QCheckBox("Mostrar imagem")
        self.toggle_check.setChecked(True)
        self.toggle_check.setVisible(False)
        self.toggle_check.stateChanged.connect(
            lambda state: self.toggle_requested.emit(state == Qt.CheckState.Checked.value)
        )
        layout.addWidget(self.toggle_check)

        # ── Detecção de Células Convectivas (inspirado na TATHU/INPE) ──
        thr_row = QHBoxLayout()
        thr_row.addWidget(QLabel("Limiar de topo:"))
        self.cells_threshold_combo = QComboBox()
        for t in (-40.0, -50.0, -60.0, -70.0):
            self.cells_threshold_combo.addItem(f"{t:.0f} °C", t)
        self.cells_threshold_combo.setCurrentIndex(1)  # -50 °C
        self.cells_threshold_combo.setToolTip(
            "Temperatura de topo abaixo da qual o pixel é convecção profunda.\n"
            "−40 °C franja · −50/−60 °C convecção profunda · −70 °C overshooting."
        )
        thr_row.addWidget(self.cells_threshold_combo)
        layout.addLayout(thr_row)

        self.detect_cells_btn = QPushButton("⛈ Detectar Células Convectivas")
        self.detect_cells_btn.setStyleSheet("""
            QPushButton {
                background-color: #8E44AD; padding: 7px;
                font-size: 11px; font-weight: bold; border-radius: 4px;
            }
            QPushButton:hover { background-color: #9B59B6; }
            QPushButton:disabled { background-color: #555; }
        """)
        self.detect_cells_btn.setToolTip(
            "Contorna os núcleos convectivos (topos frios) na imagem IR e rotula\n"
            "com temperatura mínima e área aproximada. Detecta na ÁREA VISÍVEL —\n"
            "dê zoom na região de interesse para acelerar e refinar. Roda em\n"
            "segundo plano (com Cancelar). Guia objetivo — o previsor traça por cima."
        )
        self.detect_cells_btn.setEnabled(False)  # habilita quando há imagem
        self.detect_cells_btn.clicked.connect(
            lambda: self.detect_cells_requested.emit(
                float(self.cells_threshold_combo.currentData())
            )
        )
        layout.addWidget(self.detect_cells_btn)

        self.cells_toggle_check = QCheckBox("Mostrar células")
        self.cells_toggle_check.setChecked(True)
        self.cells_toggle_check.setVisible(False)
        self.cells_toggle_check.stateChanged.connect(
            lambda state: self.cells_toggle_requested.emit(state == Qt.CheckState.Checked.value)
        )
        layout.addWidget(self.cells_toggle_check)

        # Status
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 10px; color: #7F8C8D;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def _on_download_clicked(self):
        """Monta o datetime alvo e valida se não é futuro."""
        qdate = self.date_edit.date()
        hora = self.hora_combo.currentData()
        minuto = self.min_combo.currentData()

        target = datetime(qdate.year(), qdate.month(), qdate.day(), hora, minuto, 0, tzinfo=UTC)

        now_utc = datetime.now(UTC)
        if target > now_utc:
            QMessageBox.warning(
                self,
                "Data/hora futura",
                f"O horário selecionado ({target.strftime('%d/%m/%Y %H:%MZ')})\n"
                f"ainda não ocorreu.\n\n"
                f"Horário UTC atual: {now_utc.strftime('%d/%m/%Y %H:%MZ')}\n\n"
                f"Selecione uma data/hora no passado.",
            )
            return

        self.status_label.setText("")
        self.download_requested.emit(target)

    def set_downloading(self, downloading: bool):
        if downloading:
            self.download_btn.setText("Baixando...")
            self.download_btn.setEnabled(False)
        else:
            self.download_btn.setText("🛰️ Baixar Imagem IR")
            self.download_btn.setEnabled(True)

    def set_loaded(self, time_str: str):
        self.toggle_check.setVisible(True)
        self.toggle_check.setChecked(True)
        self.detect_cells_btn.setEnabled(True)  # há imagem: já dá p/ detectar
        self.status_label.setText(f"✓ {time_str}")
        self.status_label.setStyleSheet("font-size: 10px; color: #27AE60;")

    def set_cells_detected(self, detected: bool):
        """Revela o checkbox 'Mostrar células' após uma detecção bem-sucedida."""
        self.cells_toggle_check.setVisible(detected)
        if detected:
            self.cells_toggle_check.setChecked(True)

    def reset_state(self):
        """Volta ao estado 'sem imagem' (mapa limpo / tema trocado), sem emitir sinais.

        Espelha o canvas vazio: toggles ocultos (e re-armados para o próximo
        download), botão de detecção desabilitado e status limpo.
        """
        for chk in (self.toggle_check, self.cells_toggle_check):
            chk.blockSignals(True)
            chk.setChecked(True)
            chk.setVisible(False)
            chk.blockSignals(False)
        self.detect_cells_btn.setEnabled(False)
        self.status_label.setText("")
        self.status_label.setStyleSheet("font-size: 10px; color: #7F8C8D;")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAINEL DE TSM (MUR SST 1 km — NASA/NOAA)
# ═══════════════════════════════════════════════════════════════════════════════


class SSTPanel(QWidget):
    """Painel para download e controle de TSM (MUR SST 1 km)."""

    download_requested = pyqtSignal(object)  # datetime alvo
    toggle_requested = pyqtSignal(bool)  # mostrar/ocultar

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Título
        title = QLabel("TSM — MUR SST 1km")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 13px; font-weight: bold; color: #E67E22;")
        layout.addWidget(title)

        info = QLabel("NASA/NOAA — ERDDAP OPeNDAP")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet("font-size: 10px; color: #999;")
        layout.addWidget(info)

        # Seletor de data
        date_row = QHBoxLayout()
        date_row.addWidget(QLabel("Data:"))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        # MUR SST tem ~2 dias de latência
        default_date = datetime.now(UTC) - timedelta(days=2)
        self.date_edit.setDate(QDate(default_date.year, default_date.month, default_date.day))
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        date_row.addWidget(self.date_edit)
        layout.addLayout(date_row)

        # Botão download
        self.download_btn = QPushButton("🌊 Baixar TSM")
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #E67E22; padding: 8px;
                font-size: 12px; font-weight: bold; border-radius: 6px;
            }
            QPushButton:hover { background-color: #D35400; }
            QPushButton:disabled { background-color: #555; }
        """)
        self.download_btn.clicked.connect(self._on_download_clicked)
        layout.addWidget(self.download_btn)

        # Toggle mostrar/ocultar
        self.toggle_check = QCheckBox("Mostrar TSM")
        self.toggle_check.setChecked(True)
        self.toggle_check.setVisible(False)
        self.toggle_check.stateChanged.connect(
            lambda state: self.toggle_requested.emit(state == Qt.CheckState.Checked.value)
        )
        layout.addWidget(self.toggle_check)

        # Status
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 10px; color: #7F8C8D;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def _on_download_clicked(self):
        """Monta o datetime alvo e emite sinal."""
        qdate = self.date_edit.date()
        target = datetime(
            qdate.year(),
            qdate.month(),
            qdate.day(),
            0,
            0,
            0,
            tzinfo=UTC,
        )
        self.status_label.setText("")
        self.download_requested.emit(target)

    def set_downloading(self, downloading: bool):
        if downloading:
            self.download_btn.setText("Baixando...")
            self.download_btn.setEnabled(False)
        else:
            self.download_btn.setText("🌊 Baixar TSM")
            self.download_btn.setEnabled(True)

    def set_loaded(self, time_str: str):
        self.toggle_check.setVisible(True)
        self.toggle_check.setChecked(True)
        self.status_label.setText(f"✓ TSM {time_str}")
        self.status_label.setStyleSheet("font-size: 10px; color: #27AE60;")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAINEL DE CONFIGURAÇÕES (direita)
# ═══════════════════════════════════════════════════════════════════════════════


class SettingsPanel(QWidget):
    """Painel de configurações com região, step e opções."""

    region_changed = pyqtSignal(list)
    theme_changed = pyqtSignal(str)
    model_changed = pyqtSignal(str)  # "ifs" | "aifs" — modelo global do ECMWF
    update_requested = pyqtSignal()
    layers_changed = pyqtSignal(str, bool)  # (nome_camada, visível)
    observations_changed = pyqtSignal(str, bool)  # (kind: "metar"|"synop", ativo)
    observation_density_changed = pyqtSignal(float)  # fator de densidade do overlay
    animate_requested = pyqtSignal()  # animação de steps (GIF/MP4)
    terrain_filter_changed = pyqtSignal(bool)  # filtro orográfico dos centros H/L
    context_emphasis_changed = pyqtSignal(bool)  # realce de costa/fronteiras/estados
    uf_extent_requested = pyqtSignal(list)  # recorte por estado (preserva dados)
    cities_changed = pyqtSignal(bool)  # camada de cidades rotuladas (IBGE)
    city_density_changed = pyqtSignal(float)  # fator de densidade das cidades
    north_arrow_changed = pyqtSignal(bool)  # rosa dos ventos (indicador de norte)
    hydrography_changed = pyqtSignal(bool)  # camada de hidrografia (rios/lagos)
    hydrography_detail_changed = pyqtSignal(str)  # nível: "Principais" | "Detalhado"
    obs_time_mode_changed = pyqtSignal(str)  # horário das obs: "analysis" | "latest"
    obs_refresh_requested = pyqtSignal()  # 🔄 re-baixa as obs ignorando o cache

    REGIONS = {
        "América do Sul": EXTENT_AMSUL,
        "Brasil": EXTENT_BRASIL,
        "Nordeste": EXTENT_NORDESTE,
        "Sudeste": EXTENT_SUDESTE,
        "Sul": EXTENT_SUL,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        # ═══ 1. REGIÃO ═══
        region_group = QGroupBox("Região")
        region_layout = QVBoxLayout(region_group)
        region_layout.setSpacing(6)

        self.region_combo = QComboBox()
        self.region_combo.addItems(self.REGIONS.keys())
        self.region_combo.currentTextChanged.connect(self._on_region_changed)
        region_layout.addWidget(self.region_combo)

        # Recorte por estado. Diferente do combo Região (que TROCA o domínio de
        # trabalho e limpa os dados), selecionar uma UF apenas RECORTA a carta
        # atual — os campos carregados são preservados e replotados.
        uf_row = QHBoxLayout()
        uf_row.addWidget(QLabel("Estado:"))
        self.uf_combo = QComboBox()
        self.uf_combo.addItem("—", None)
        for sigla, nome in sorted(UF_NOMES.items(), key=lambda kv: kv[1]):
            self.uf_combo.addItem(f"{nome} ({sigla})", sigla)
        self.uf_combo.setToolTip(
            "Recorta a carta atual para o estado escolhido, preservando os dados\n"
            "carregados (diferente do combo Região acima, que troca o domínio e\n"
            "limpa os dados). Zoom/pan manual desmarca a seleção."
        )
        self.uf_combo.currentIndexChanged.connect(self._on_uf_selected)
        uf_row.addWidget(self.uf_combo, 1)
        region_layout.addLayout(uf_row)

        extent_grid = QGridLayout()
        extent_grid.setSpacing(4)
        extent_grid.setContentsMargins(0, 4, 0, 0)

        lon_label = QLabel("Lon:")
        lon_label.setStyleSheet("font-weight: bold; font-size: 10px;")
        extent_grid.addWidget(lon_label, 0, 0)

        self.lon_min = QSpinBox()
        self.lon_min.setRange(-180, 180)
        self.lon_min.setValue(-100)
        self.lon_min.setPrefix("Min:")
        self.lon_min.setSuffix("°")
        self.lon_min.setMinimumWidth(80)
        extent_grid.addWidget(self.lon_min, 0, 1)

        self.lon_max = QSpinBox()
        self.lon_max.setRange(-180, 180)
        self.lon_max.setValue(0)
        self.lon_max.setPrefix("Max:")
        self.lon_max.setSuffix("°")
        self.lon_max.setMinimumWidth(80)
        extent_grid.addWidget(self.lon_max, 0, 2)

        lat_label = QLabel("Lat:")
        lat_label.setStyleSheet("font-weight: bold; font-size: 10px;")
        extent_grid.addWidget(lat_label, 1, 0)

        self.lat_min = QSpinBox()
        self.lat_min.setRange(-90, 90)
        self.lat_min.setValue(-75)
        self.lat_min.setPrefix("Min:")
        self.lat_min.setSuffix("°")
        self.lat_min.setMinimumWidth(80)
        extent_grid.addWidget(self.lat_min, 1, 1)

        self.lat_max = QSpinBox()
        self.lat_max.setRange(-90, 90)
        self.lat_max.setValue(15)
        self.lat_max.setPrefix("Max:")
        self.lat_max.setSuffix("°")
        self.lat_max.setMinimumWidth(80)
        extent_grid.addWidget(self.lat_max, 1, 2)

        region_layout.addLayout(extent_grid)

        apply_btn = QPushButton("↻  Aplicar Região")
        apply_btn.setToolTip("Atualiza o mapa com as coordenadas definidas acima")
        apply_btn.setStyleSheet("""
            QPushButton {
                background-color: #2980B9; color: white;
                font-size: 10px; font-weight: bold;
                padding: 5px; border-radius: 4px;
            }
            QPushButton:hover { background-color: #3498DB; }
            QPushButton:pressed { background-color: #1F618D; }
        """)
        apply_btn.clicked.connect(self._on_apply_region)
        region_layout.addWidget(apply_btn)

        # Tema de cores do mapa
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Tema:"))
        self.theme_combo = QComboBox()
        for name in [
            "Clássico",
            "Branco",
            "Pastel",
            "Tons de cinza",
            "Terra",
            "Escuro",
            "Relevo Natural",
        ]:
            self.theme_combo.addItem(name)
        # Seleciona o tema padrão antes de conectar o sinal (não dispara emit na construção).
        self.theme_combo.setCurrentText("Relevo Natural")
        self.theme_combo.currentTextChanged.connect(lambda name: self.theme_changed.emit(name))
        theme_row.addWidget(self.theme_combo)
        region_layout.addLayout(theme_row)

        layout.addWidget(region_group)

        # ═══ 2. MODELO E RODADA ECMWF ═══
        rodada_group = QGroupBox("Modelo e Rodada ECMWF")
        rodada_layout = QVBoxLayout(rodada_group)
        rodada_layout.setSpacing(4)

        model_row = QHBoxLayout()
        model_row.setSpacing(6)
        model_row.addWidget(QLabel("Modelo:"))
        self.model_combo = QComboBox()
        self.model_combo.addItem("IFS (físico)", "ifs")
        self.model_combo.addItem("AIFS (IA)", "aifs")
        self.model_combo.setToolTip(
            "IFS: o modelo físico operacional do ECMWF (padrão).\n"
            "AIFS: o modelo de INTELIGÊNCIA ARTIFICIAL do ECMWF (aifs-single),\n"
            "steps de 6/6 h até +360 h; algumas variáveis não existem (UR,\n"
            "vorticidade, divergência, OLR, água precipitável...).\n"
            "O seletor vale para a carta principal (base sinótica e campos);\n"
            "sondagens, meteograma, corte e análises calculadas (ZCIT,\n"
            "bloqueio, instabilidade) seguem no IFS por enquanto."
        )
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        model_row.addWidget(self.model_combo, 1)
        rodada_layout.addLayout(model_row)

        self.cycle_combo = QComboBox()
        self.cycle_combo.addItem("Mais recente (auto)", None)
        self.cycle_combo.setMinimumWidth(130)
        rodada_layout.addWidget(self.cycle_combo)

        self.rodada_info_label = QLabel(
            "<small style='color: #95A5A6;'>Clique Verificar para ver rodadas</small>"
        )
        self.rodada_info_label.setWordWrap(True)
        rodada_layout.addWidget(self.rodada_info_label)

        self.rodada_detail_label = QLabel("")
        self.rodada_detail_label.setWordWrap(True)
        self.rodada_detail_label.setStyleSheet("font-size: 10px;")
        rodada_layout.addWidget(self.rodada_detail_label)

        check_btn = QPushButton("Verificar Rodadas")
        check_btn.setStyleSheet("""
            QPushButton {
                background-color: #34495E; padding: 5px;
                font-size: 10px; border: 1px solid #5D6D7E;
            }
            QPushButton:hover { background-color: #4A6785; }
        """)
        check_btn.clicked.connect(self._check_cycles)
        rodada_layout.addWidget(check_btn)

        layout.addWidget(rodada_group)

        # ═══ 3. PREVISÃO (STEP) + SUAVIZAÇÃO ═══
        step_group = QGroupBox("Previsão")
        step_layout = QVBoxLayout(step_group)
        step_layout.setSpacing(4)

        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("Step:"))
        self.step_combo = QComboBox()
        # Janela completa do ECMWF Open Data: 0–144h de 3/3h, 150–240h de 6/6h.
        # (rodadas 00Z/12Z chegam a +240h; 06Z/18Z a +144h — o backend avisa via
        #  LoczcitDataError/404 se o step exceder o alcance da rodada escolhida).
        for step in VALID_STEPS:
            self.step_combo.addItem(f"+{step}h", step)
        self.step_combo.setMinimumWidth(90)
        step_row.addWidget(self.step_combo)
        step_row.addStretch()
        step_layout.addLayout(step_row)

        smooth_row = QHBoxLayout()
        smooth_row.addWidget(QLabel("Suavização:"))
        self.smooth_slider = QSlider(Qt.Orientation.Horizontal)
        self.smooth_slider.setRange(0, 50)
        self.smooth_slider.setValue(15)
        self.smooth_slider.valueChanged.connect(self._on_smooth_changed)
        smooth_row.addWidget(self.smooth_slider)
        self.smooth_label = QLabel("1.5")
        self.smooth_label.setStyleSheet("font-size: 10px; color: #BDC3C7; min-width: 22px;")
        smooth_row.addWidget(self.smooth_label)
        step_layout.addLayout(smooth_row)

        self.animate_btn = QPushButton("🎬 Animar Steps...")
        self.animate_btn.setToolTip(
            "Gera GIF/MP4 da composição atual do mapa ao longo dos steps de previsão"
        )
        self.animate_btn.setStyleSheet("""
            QPushButton {
                background-color: #34495E; padding: 5px;
                font-size: 10px; border: 1px solid #5D6D7E;
            }
            QPushButton:hover { background-color: #4A6785; }
        """)
        self.animate_btn.clicked.connect(self.animate_requested.emit)
        step_layout.addWidget(self.animate_btn)

        layout.addWidget(step_group)

        # ═══ 4. BOTÃO BAIXAR ═══
        self.update_btn = QPushButton("⬇ Baixar Dados ECMWF")
        self.update_btn.setStyleSheet("""
            QPushButton {
                background-color: #27AE60; padding: 10px;
                font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #2ECC71; }
            QPushButton:disabled { background-color: #5D6D7E; }
        """)
        self.update_btn.clicked.connect(self.update_requested.emit)
        layout.addWidget(self.update_btn)

        # ═══ 5. CAMADAS BASE ═══
        options_group = QGroupBox("Camadas sinóticas")
        options_layout = QVBoxLayout(options_group)
        options_layout.setSpacing(4)

        self.pnmm_check = QCheckBox("PNMM (isolinhas)")
        self.pnmm_check.setChecked(True)
        self.pnmm_check.stateChanged.connect(
            lambda state: self.layers_changed.emit("pnmm", state == Qt.CheckState.Checked.value)
        )
        options_layout.addWidget(self.pnmm_check)

        self.thickness_check = QCheckBox("Espessura 1000-500 hPa")
        self.thickness_check.setChecked(True)
        self.thickness_check.stateChanged.connect(
            lambda state: self.layers_changed.emit(
                "thickness", state == Qt.CheckState.Checked.value
            )
        )
        options_layout.addWidget(self.thickness_check)

        self.centers_check = QCheckBox("Centros H/L")
        self.centers_check.setChecked(True)
        self.centers_check.stateChanged.connect(
            lambda state: self.layers_changed.emit("centers", state == Qt.CheckState.Checked.value)
        )
        options_layout.addWidget(self.centers_check)

        # Sub-opção dos centros: filtro orográfico (ligado por padrão).
        # Sobre o Altiplano/Andes a PNMM é extrapolação e cria H/L falsos.
        terrain_row = QHBoxLayout()
        terrain_row.addSpacing(18)  # indenta sob "Centros H/L"
        self.terrain_filter_check = QCheckBox("⛰ Filtrar terreno elevado")
        self.terrain_filter_check.setChecked(True)
        self.terrain_filter_check.setToolTip(
            "Ignora centros H/L sobre terreno elevado (> ~1500 m — Andes/Altiplano),\n"
            "onde a redução da pressão ao nível do mar é extrapolação e gera\n"
            "máximos/mínimos artefactuais. Recomendado manter ligado."
        )
        self.terrain_filter_check.stateChanged.connect(
            lambda state: self.terrain_filter_changed.emit(state == Qt.CheckState.Checked.value)
        )
        terrain_row.addWidget(self.terrain_filter_check)
        options_layout.addLayout(terrain_row)

        self.emphasis_check = QCheckBox("🗺 Destacar contornos")
        self.emphasis_check.setToolTip(
            "Engrossa costa, fronteiras e divisas de estados, com halo de contraste.\n"
            "Útil sobre imagem de satélite e campos preenchidos, onde as linhas finas\n"
            "do mapa base somem. Liga sozinho ao ativar o satélite."
        )
        self.emphasis_check.stateChanged.connect(
            lambda state: self.context_emphasis_changed.emit(state == Qt.CheckState.Checked.value)
        )
        options_layout.addWidget(self.emphasis_check)

        self.hydrography_check = QCheckBox("🌊 Hidrografia (rios e lagos)")
        self.hydrography_check.setToolTip(
            "Desenha rios e lagos da América do Sul como referência geográfica.\n"
            "Principais: Natural Earth 50m, coerente com o mapa base (rodando do\n"
            "código-fonte pode baixar 1x da internet; no instalador já vem tudo).\n"
            "Detalhado: HydroRIVERS/LakeATLAS embarcados — a espessura cresce com\n"
            "a ordem do rio. Em escala continental, prefira Principais.\n"
            "A preferência fica salva entre sessões."
        )
        options_layout.addWidget(self.hydrography_check)

        # Sub-opção da hidrografia: nível de detalhe (padrão da densidade de cidades)
        hydro_row = QHBoxLayout()
        hydro_row.addSpacing(18)  # indenta sob "Hidrografia"
        hydro_row.addWidget(QLabel("Nível:"))
        self.hydro_detail_combo = QComboBox()
        for name in HYDRO_DETAIL_LEVELS:
            self.hydro_detail_combo.addItem(name)
        self.hydro_detail_combo.setToolTip(
            "Principais: hidrografia Natural Earth 50m (leve).\n"
            "Detalhado: rios com ordem de Strahler ≥ 5 e lagos ≥ 10 km²\n"
            "(HydroRIVERS/LakeATLAS) — o estilo do mapa de referência amazônico."
        )
        hydro_row.addWidget(self.hydro_detail_combo, 1)
        options_layout.addLayout(hydro_row)

        # Preferência persistida (QSettings), restaurada ANTES dos connects —
        # setChecked/setCurrentText não emitem aqui (padrão do theme_combo); o
        # MainWindow empurra o estado restaurado ao canvas depois da fiação.
        settings = QSettings("PPGGRD-UFPA", APP_NAME)
        self.hydrography_check.setChecked(settings.value("map/hydrography", False, bool))
        saved_level = settings.value("map/hydrography_detail", DEFAULT_HYDRO_DETAIL, str)
        if saved_level not in HYDRO_DETAIL_LEVELS:
            saved_level = DEFAULT_HYDRO_DETAIL
        self.hydro_detail_combo.setCurrentText(saved_level)
        self.hydrography_check.stateChanged.connect(
            lambda state: self.hydrography_changed.emit(state == Qt.CheckState.Checked.value)
        )
        self.hydro_detail_combo.currentTextChanged.connect(
            lambda level: self.hydrography_detail_changed.emit(level)
        )

        self.cities_check = QCheckBox("🏙 Cidades")
        self.cities_check.setToolTip(
            "Plota sedes municipais (IBGE) com o nome — capitais e cidades maiores\n"
            "primeiro, com densidade ajustada ao zoom. Ideal para mapas regionais\n"
            "(ex.: recorte de um estado no combo acima)."
        )
        self.cities_check.stateChanged.connect(
            lambda state: self.cities_changed.emit(state == Qt.CheckState.Checked.value)
        )
        options_layout.addWidget(self.cities_check)

        # Sub-opção das cidades: densidade dos rótulos (padrão das observações)
        city_density_row = QHBoxLayout()
        city_density_row.addSpacing(18)  # indenta sob "Cidades"
        city_density_row.addWidget(QLabel("Densidade:"))
        self.city_density_combo = QComboBox()
        for name, factor in CITY_DENSITY_FACTORS.items():
            self.city_density_combo.addItem(name, factor)
        self.city_density_combo.setCurrentText(DEFAULT_CITY_DENSITY)
        self.city_density_combo.setToolTip(
            "Quantidade de cidades rotuladas no mapa. Maior densidade mostra mais\n"
            "sedes municipais (rótulos mais próximos entre si). Aplica na hora,\n"
            "sem rede — a prioridade capital > população é mantida."
        )
        self.city_density_combo.currentIndexChanged.connect(
            lambda _: self.city_density_changed.emit(self.get_city_density())
        )
        city_density_row.addWidget(self.city_density_combo, 1)
        options_layout.addLayout(city_density_row)

        self.north_arrow_check = QCheckBox("🧭 Rosa dos ventos (N)")
        self.north_arrow_check.setToolTip(
            "Desenha o indicador de norte geográfico (triângulo preto + N) no\n"
            "canto superior direito da carta — padrão cartográfico para mapas\n"
            "exportados. Nesta projeção o norte é sempre o topo da carta."
        )
        self.north_arrow_check.stateChanged.connect(
            lambda state: self.north_arrow_changed.emit(state == Qt.CheckState.Checked.value)
        )
        options_layout.addWidget(self.north_arrow_check)

        layout.addWidget(options_group)

        # ═══ 6. OBSERVAÇÕES DE SUPERFÍCIE (SYNOP / METAR) ═══
        obs_group = QGroupBox("Observações de superfície")
        obs_layout = QVBoxLayout(obs_group)
        obs_layout.setSpacing(4)

        self.synop_check = QCheckBox("SYNOP (00/06/12/18 UTC)")
        self.synop_check.setToolTip(
            "Observações sinóticas (OGIMET). Reportadas nos horários principais "
            "00Z, 06Z, 12Z e 18Z — o overlay usa o sinótico mais próximo do modelo."
        )
        self.synop_check.stateChanged.connect(
            lambda state: self.observations_changed.emit(
                "synop", state == Qt.CheckState.Checked.value
            )
        )
        obs_layout.addWidget(self.synop_check)

        self.metar_check = QCheckBox("METAR (horário)")
        self.metar_check.setToolTip(
            "Observações de aeródromos (NOAA AWC), atualizadas de hora em hora."
        )
        self.metar_check.stateChanged.connect(
            lambda state: self.observations_changed.emit(
                "metar", state == Qt.CheckState.Checked.value
            )
        )
        obs_layout.addWidget(self.metar_check)

        # ── Horário das observações: análise da rodada × mais recente (agora) ──
        time_row = QHBoxLayout()
        time_row.setSpacing(6)
        time_row.addSpacing(18)  # indenta sob SYNOP/METAR
        time_row.addWidget(QLabel("Horário:"))
        self.obs_time_combo = QComboBox()
        self.obs_time_combo.addItem("Análise da rodada (+0h)", OBS_MODE_ANALYSIS)
        self.obs_time_combo.addItem("Mais recente (agora)", OBS_MODE_LATEST)
        self.obs_time_combo.setToolTip(
            "Análise da rodada: observações da janela da análise (+0h) — casam\n"
            "com o Válido da carta e ficam congeladas (carta reprodutível).\n"
            "Mais recente: busca a observação mais atual disponível — independe\n"
            "da rodada e libera SYNOP/METAR em qualquer step; o horário real é\n"
            "carimbado no título da carta. A preferência fica salva."
        )
        time_row.addWidget(self.obs_time_combo, 1)
        self.obs_refresh_btn = QPushButton("🔄")
        self.obs_refresh_btn.setFixedWidth(30)
        self.obs_refresh_btn.setToolTip(
            "Atualizar observações agora (ignora o cache de 10 min).\n"
            'Disponível no horário "Mais recente".'
        )
        time_row.addWidget(self.obs_refresh_btn)
        obs_layout.addLayout(time_row)

        # Preferência do modo persistida (QSettings), restaurada ANTES dos
        # connects (padrão da hidrografia): setCurrentIndex aqui não emite; o
        # MainWindow grava a chave no handler do sinal.
        obs_settings = QSettings("PPGGRD-UFPA", APP_NAME)
        saved_mode = obs_settings.value("map/obs_time_mode", OBS_MODE_ANALYSIS, str)
        if saved_mode not in (OBS_MODE_ANALYSIS, OBS_MODE_LATEST):
            saved_mode = OBS_MODE_ANALYSIS
        mode_idx = self.obs_time_combo.findData(saved_mode)
        if mode_idx >= 0:
            self.obs_time_combo.setCurrentIndex(mode_idx)
        self.obs_time_combo.currentIndexChanged.connect(self._on_obs_time_mode_changed)
        self.obs_refresh_btn.clicked.connect(self.obs_refresh_requested.emit)

        # ── Densidade do overlay (afina SYNOP+METAR; re-renderiza sem rebaixar) ──
        density_row = QHBoxLayout()
        density_row.setSpacing(6)
        density_row.addWidget(QLabel("Densidade:"))
        self.obs_density_combo = QComboBox()
        for name, factor in OBS_DENSITY_FACTORS.items():
            self.obs_density_combo.addItem(name, factor)
        self.obs_density_combo.setCurrentText(DEFAULT_OBS_DENSITY)
        self.obs_density_combo.setToolTip(
            "Quantidade de estações plotadas (SYNOP e METAR). Maior densidade mostra "
            "mais observações, estilo GEMPAK. Aplica na hora, sem baixar de novo."
        )
        self.obs_density_combo.currentIndexChanged.connect(
            lambda _: self.observation_density_changed.emit(self.get_observation_density())
        )
        density_row.addWidget(self.obs_density_combo, 1)
        obs_layout.addLayout(density_row)

        self.obs_time_label = QLabel(
            "<small style='color: #95A5A6;'>Carregue um modelo para sincronizar "
            "as observações ao horário da carta.</small>"
        )
        self.obs_time_label.setWordWrap(True)
        obs_layout.addWidget(self.obs_time_label)

        layout.addWidget(obs_group)

        # ── Gate por modo de horário: no modo "análise" as observações só fazem
        # sentido no +0h; no modo "mais recente" elas independem do step.
        self._obs_ref_dt = None
        self._obs_actual_times: dict = {}  # horários REAIS plotados (df.attrs)
        self.step_combo.currentIndexChanged.connect(self._update_observations_ui)
        self._update_observations_ui()  # estado inicial coerente com step e modo

    def _on_region_changed(self, name):
        if name in self.REGIONS:
            extent = self.REGIONS[name]
            self.lon_min.setValue(int(extent[0]))
            self.lat_min.setValue(int(extent[1]))
            self.lon_max.setValue(int(extent[2]))
            self.lat_max.setValue(int(extent[3]))
            self.reset_uf_combo()
            self.region_changed.emit(extent)

    def _on_apply_region(self):
        self.reset_uf_combo()
        self.region_changed.emit(self.get_extent())

    def _on_uf_selected(self, _index: int) -> None:
        sigla = self.uf_combo.currentData()
        if sigla:
            self.uf_extent_requested.emit(list(EXTENT_UFS[sigla]))

    def reset_uf_combo(self) -> None:
        """Volta o combo Estado para "—" sem reemitir o recorte."""
        if self.uf_combo.currentIndex() != 0:
            self.uf_combo.blockSignals(True)
            self.uf_combo.setCurrentIndex(0)
            self.uf_combo.blockSignals(False)

    def sync_uf_combo(self, extent: list[float]) -> None:
        """Desmarca a UF quando o extent atual deixa de ser o preset dela.

        Chamado pela janela principal a cada ``extent_changed`` — zoom-área,
        reset e "anterior" desmarcam sozinhos; aplicar a própria UF não
        desmarca (os extents coincidem após o arredondamento dos spinboxes).
        """
        sigla = self.uf_combo.currentData()
        if not sigla:
            return
        preset = [int(v) for v in EXTENT_UFS[sigla]]
        if [int(round(v)) for v in extent] != preset:
            self.reset_uf_combo()

    def _on_smooth_changed(self, value):
        sigma = value / 10.0
        self.smooth_label.setText(f"{sigma:.1f}")

    def get_extent(self):
        return [
            self.lon_min.value(),
            self.lat_min.value(),
            self.lon_max.value(),
            self.lat_max.value(),
        ]

    def get_smoothing(self):
        return self.smooth_slider.value() / 10.0

    def get_step(self):
        return self.step_combo.currentData()

    def get_options(self):
        return {
            "pnmm": self.pnmm_check.isChecked(),
            "thickness": self.thickness_check.isChecked(),
            "centers": self.centers_check.isChecked(),
            "synop": self.synop_check.isChecked(),
            "metar": self.metar_check.isChecked(),
        }

    def get_observations(self):
        """Retorna {'metar': bool, 'synop': bool} — overlays de observação ativos."""
        return {
            "metar": self.metar_check.isChecked(),
            "synop": self.synop_check.isChecked(),
        }

    def get_observation_density(self) -> float:
        """Fator de densidade do overlay selecionado (ver OBS_DENSITY_FACTORS)."""
        factor = self.obs_density_combo.currentData()
        return float(factor) if factor is not None else OBS_DENSITY_FACTORS[DEFAULT_OBS_DENSITY]

    def get_city_density(self) -> float:
        """Fator de densidade da camada de cidades (ver CITY_DENSITY_FACTORS)."""
        factor = self.city_density_combo.currentData()
        return float(factor) if factor is not None else CITY_DENSITY_FACTORS[DEFAULT_CITY_DENSITY]

    def get_hydrography_detail(self) -> str:
        """Nível selecionado da camada de hidrografia (ver HYDRO_DETAIL_LEVELS)."""
        return str(self.hydro_detail_combo.currentText())

    def get_obs_time_mode(self) -> str:
        """Modo de horário das observações (OBS_MODE_ANALYSIS | OBS_MODE_LATEST)."""
        mode = self.obs_time_combo.currentData()
        return mode if mode in (OBS_MODE_ANALYSIS, OBS_MODE_LATEST) else OBS_MODE_ANALYSIS

    def _on_obs_time_mode_changed(self, _index: int) -> None:
        # Horários reais do modo anterior não valem mais — evita hint defasado
        # até o próximo fetch reabastecer via set_obs_actual_times.
        self._obs_actual_times = {}
        self._update_observations_ui()
        self.obs_time_mode_changed.emit(self.get_obs_time_mode())

    def set_obs_actual_times(self, times: dict) -> None:
        """Recebe os horários REAIS plotados (df.attrs do fetch) e refaz o hint.

        `times` mescla parcialmente: {"metar": datetime|None, "synop":
        datetime|None, "synop_fallback": bool}. `None` limpa a entrada (camada
        desligada ou fetch vazio).
        """
        self._obs_actual_times.update(times)
        self._update_observations_ui()

    def set_obs_reference_time(self, dt):
        """Guarda o valid_time do modelo (datetime UTC ou None) e atualiza o painel.

        O rótulo só mostra os horários SYNOP/METAR quando o Step é +0h (análise);
        em previsões futuras, prevalece o aviso de indisponibilidade.
        """
        self._obs_ref_dt = dt
        self._update_observations_ui()

    def _render_obs_hint(self):
        """Renderiza o rótulo normal (sincronização) a partir do valid_time guardado."""
        dt = getattr(self, "_obs_ref_dt", None)
        if dt is None:
            self.obs_time_label.setText(
                "<small style='color: #95A5A6;'>Observações prontas para o horário "
                "da análise — carregue um modelo para sincronizar.</small>"
            )
            return
        synop_hour = synop_slot(dt, 6).hour
        date_str = dt.strftime("%d/%m")
        self.obs_time_label.setText(
            f"<small style='color: #95A5A6;'>"
            f"SYNOP → <b>{synop_hour:02d}Z {date_str}</b> · "
            f"METAR → <b>{dt.hour:02d}Z {date_str}</b> (janela da análise)"
            f"</small>"
        )

    def _render_latest_hint(self):
        """Hint do modo "mais recente": horários REAIS plotados, quando houver."""
        actual = self._obs_actual_times
        parts = []
        metar_dt = actual.get("metar")
        if metar_dt is not None:
            parts.append(f"METAR <b>{metar_dt.strftime('%H:%MZ %d/%m')}</b>")
        synop_dt = actual.get("synop")
        if synop_dt is not None:
            txt = f"SYNOP <b>{synop_dt.strftime('%HZ %d/%m')}</b>"
            if actual.get("synop_fallback"):
                txt += " (recuo de slot)"
            parts.append(txt)
        if parts:
            self.obs_time_label.setText(
                "<small style='color: #95A5A6;'>Plotado: " + " · ".join(parts) + "</small>"
            )
        else:
            self.obs_time_label.setText(
                "<small style='color: #95A5A6;'>Mostra as observações mais recentes "
                "disponíveis — independem da rodada carregada.</small>"
            )

    def _update_observations_ui(self):
        """Gate dos overlays de observação por modo de horário.

        Análise: SYNOP/METAR só no step +0h (as estações refletem o presente).
        Mais recente: liberados em qualquer step — a obs se descola da rodada e
        o horário real é carimbado no título/hint (honestidade garantida).
        Ao desmarcar automaticamente, o sinal `stateChanged` das checkboxes
        dispara a remoção dos artists de estação no MapCanvas.
        """
        mode = self.get_obs_time_mode()
        step = self.get_step() or 0
        if mode == OBS_MODE_LATEST:
            self.synop_check.setEnabled(True)
            self.metar_check.setEnabled(True)
            self.obs_refresh_btn.setEnabled(True)
            self._render_latest_hint()
        elif step == 0:
            self.synop_check.setEnabled(True)
            self.metar_check.setEnabled(True)
            self.obs_refresh_btn.setEnabled(False)
            self._render_obs_hint()
        else:
            # Desmarca (emite o sinal → remove overlay) e desabilita
            self.synop_check.setChecked(False)
            self.metar_check.setChecked(False)
            self.synop_check.setEnabled(False)
            self.metar_check.setEnabled(False)
            self.obs_refresh_btn.setEnabled(False)
            self.obs_time_label.setText(
                '<small style="color: #E67E22;">⚠️ No horário "Análise", as '
                "observações só existem no Step +0h. Selecione +0h ou troque o "
                'horário para "Mais recente".</small>'
            )

    def set_downloading(self, downloading: bool):
        self.update_btn.setEnabled(not downloading)
        self.update_btn.setText("⏳ Baixando..." if downloading else "⬇ Baixar Dados ECMWF")

    def get_model(self) -> str:
        """Modelo global do ECMWF selecionado ("ifs" | "aifs")."""
        model = self.model_combo.currentData()
        return model if model in ("ifs", "aifs") else "ifs"

    def set_model(self, model: str) -> None:
        """Restaura o seletor de modelo SEM emitir (abertura de projeto)."""
        idx = self.model_combo.findData(model if model in ("ifs", "aifs") else "ifs")
        if idx >= 0 and idx != self.model_combo.currentIndex():
            self.model_combo.blockSignals(True)
            self.model_combo.setCurrentIndex(idx)
            self.model_combo.blockSignals(False)
            self._repopulate_steps(self.get_model())

    def _on_model_changed(self, _index: int) -> None:
        model = self.get_model()
        self._repopulate_steps(model)
        self.model_changed.emit(model)

    def _repopulate_steps(self, model: str) -> None:
        """Regrada o combo de steps para o modelo, preservando o step mais próximo.

        IFS: 3/3 h até 144 + 6/6 até 240; AIFS: 6/6 h até 360. Sinais
        bloqueados durante a reconstrução (evita cascata de handlers por item);
        o gate de observações é re-avaliado explicitamente no final.
        """
        steps = AIFS_VALID_STEPS if model == "aifs" else VALID_STEPS
        current = self.get_step() or 0
        closest = min(steps, key=lambda s: abs(s - current))
        self.step_combo.blockSignals(True)
        self.step_combo.clear()
        for step in steps:
            self.step_combo.addItem(f"+{step}h", step)
        self.step_combo.setCurrentIndex(self.step_combo.findData(closest))
        self.step_combo.blockSignals(False)
        self._update_observations_ui()

    def get_cycle(self):
        """Retorna a rodada selecionada (int ou None para auto)."""
        data = self.cycle_combo.currentData()
        if isinstance(data, dict):
            return data["cycle"]
        return None

    def get_cycle_date(self):
        """Retorna a data da rodada selecionada ('YYYYMMDD' ou None para auto/hoje)."""
        data = self.cycle_combo.currentData()
        if isinstance(data, dict):
            return data["date_ymd"]
        return None

    def _check_cycles(self):
        """Verifica quais rodadas do modelo vigente estão disponíveis e popula o seletor."""
        try:
            info = estimate_available_cycles(self.get_model())

            if info["latest"]:
                latest = info["latest"]
                html = (
                    f"<p style='color: #27AE60; font-weight: bold;'>"
                    f"Rodada mais recente: {latest['label']} {latest['date_str']}</p>"
                    f"<p style='color: #BDC3C7; font-size: 10px;'>"
                    f"Alcance máximo: +{latest['max_step']}h</p>"
                )

                if info["next"]:
                    n = info["next"]
                    html += (
                        f"<p style='color: #F39C12; font-size: 10px;'>"
                        f"Próxima ({n['label']}): ~{n['estimated_time']}"
                        f" (~{n['wait_minutes']}min)</p>"
                    )

                self.rodada_info_label.setText(html)

                current_data = self.cycle_combo.currentData()

                self.cycle_combo.clear()
                self.cycle_combo.addItem(f"Mais recente ({latest['label']})", None)

                for c in info["available"]:
                    max_step_str = f"+{c['max_step']}h"
                    cycle_data = {
                        "cycle": c["cycle"],
                        "date_ymd": c["base_datetime"].strftime("%Y%m%d"),
                    }
                    self.cycle_combo.addItem(
                        f"{c['label']} {c['date_str']} (até {max_step_str})", cycle_data
                    )

                if current_data is not None and isinstance(current_data, dict):
                    for i in range(self.cycle_combo.count()):
                        d = self.cycle_combo.itemData(i)
                        if isinstance(d, dict) and d == current_data:
                            self.cycle_combo.setCurrentIndex(i)
                            break

                avail_str = ", ".join(f"{c['label']}" for c in info["available"])
                self.rodada_detail_label.setText(
                    f"<small style='color: #7F8C8D;'>"
                    f"Hora UTC: {info['utc_now']}<br>"
                    f"Disponíveis: {avail_str}</small>"
                )
            else:
                self.rodada_info_label.setText(
                    "<p style='color: #E74C3C;'>Nenhuma rodada identificada</p>"
                )
        except Exception as e:
            self.rodada_info_label.setText(f"<p style='color: #E74C3C;'>Erro: {str(e)[:50]}</p>")

    def update_rodada_from_data(self, data):
        """Atualiza info da rodada após download bem-sucedido."""
        if not data:
            return

        rodada_str = data.base_time if data.base_time else "Não identificada"
        valid_str = data.valid_time if data.valid_time else ""

        self.rodada_info_label.setText(
            f"<p style='color: #27AE60; font-weight: bold;'>"
            f"Rodada: {rodada_str}</p>"
            f"<p style='color: #3498DB; font-size: 11px;'>"
            f"Válido: {valid_str} UTC</p>"
            f"<p style='color: #BDC3C7; font-size: 10px;'>"
            f"Step: +{data.step}h</p>"
        )
        self.rodada_detail_label.setText("")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAINEL DE CAMPOS (ALTITUDE + SUPERFÍCIE)
# ═══════════════════════════════════════════════════════════════════════════════

# Variáveis de superfície/integradas (sem seletor de nível)
SURFACE_VARS = {"olr", "tcwv", "tmax2m", "tmin2m"}


class FieldLayerPanel(QWidget):
    """Painel para adicionar/gerenciar campos em altitude e superfície."""

    add_layer_requested = pyqtSignal(
        str, int, str, str, str
    )  # (var_key, level, wind_type, color, density)
    toggle_layer_requested = pyqtSignal(str, bool)  # (layer_id, visible)
    remove_layer_requested = pyqtSignal(str)  # (layer_id)
    restyle_layer_requested = pyqtSignal(str)  # (layer_id) — editar cor/densidade do vento
    preset_requested = pyqtSignal(str)  # (preset_name)
    loczcit_requested = pyqtSignal()  # índice ZCIT (LOCZCIT-PA)
    blocking_requested = pyqtSignal()  # bloqueio atmosférico (anom. Z500)
    instability_requested = pyqtSignal(object)  # campos de instabilidade (lista de índices)
    baroclinic_requested = pyqtSignal()  # preset Diagnóstico Baroclínico (θe/TFP)
    run_compare_requested = pyqtSignal()  # comparação de rodadas (Δ novo − antigo)
    ens_requested = pyqtSignal(str, float)  # Ensemble ENS: (produto, limiar mm)
    inmet_avisos_requested = pyqtSignal()  # avisos meteorológicos ativos do INMET
    # Filtro dos avisos INMET: incluir os "futuros" (emitidos, validade por
    # começar)? Re-renderiza a última busca na hora — sem nova consulta.
    inmet_future_toggled = pyqtSignal(bool)

    ANALYSIS_PRESETS = {
        "Sinótica clássica": [
            ("wind", 850, "quiver"),
            ("temp_adv", 850, ""),
        ],
        "Jato e divergência": [
            ("wind", 250, "stream"),
            ("wind_speed", 250, ""),
            ("d", 200, ""),
        ],
        "Baixos níveis": [
            ("wind", 925, "quiver"),
            ("temp_adv", 925, ""),
            ("r", 850, ""),
        ],
        "Convecção profunda": [
            ("d", 200, ""),
            ("w", 500, ""),
            ("vo", 850, ""),
        ],
        "ZCAS (Escobar)": [
            ("tcwv", 0, ""),
            ("wind", 850, "quiver"),
            ("w", 500, ""),
        ],
    }

    # Campos que requerem nível de pressão
    PL_VAR_OPTIONS = [
        ("t", "Temperatura (t)"),
        ("r", "Umidade Relativa (r)"),
        ("q", "Umidade Específica (q)"),
        ("gh", "Geopotencial (gh)"),
        ("wind", "Vento (u, v)"),
        ("wind_speed", "Isotacas (vel. vento)"),
        ("w", "Vel. Vertical ω (w)"),
        ("d", "Divergência (d)"),
        ("vo", "Vorticidade (vo)"),
        ("temp_adv", "Advecção de Temperatura"),
        ("temp_grad", "Gradiente de Temperatura"),
        ("frontogenesis", "Frontogênese (Petterssen)"),
        ("mfc", "Convergência de Umidade (MFC)"),
        # Diagnóstico Baroclínico (também empilhados de uma vez pelo preset abaixo)
        ("theta_e_grad", "Gradiente de θe"),
        ("tfp_axis", "Eixo da Frente (TFP)"),
        ("theta_e_adv", "Advecção de θe"),
        ("theta_e", "θe (Temp. Pot. Equiv.)"),
    ]

    # Campos de superfície / integrados (sem nível)
    SFC_VAR_OPTIONS = [
        ("tcwv", "Água Precipitável (mm)"),
        ("olr", "OLR (desacumulada, W/m²)"),
        ("precip", "Precipitação (3h, mm)"),
        ("tmax2m", "Temp. Máxima 2 m (°C, janela 3h/6h)"),
        ("tmin2m", "Temp. Mínima 2 m (°C, janela 3h/6h)"),
        ("sst_model", "TSM modelo IFS (°C)"),
        ("sst_grad", "Gradiente de TSM (°C/100km)"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layer_widgets = {}
        self._setup_ui()

    def set_model_gating(self, model: str) -> None:
        """Desabilita nos combos as variáveis sem equivalente no modelo.

        O item fica cinza com tooltip explicativo — o usuário vê O QUE falta
        no AIFS em vez de descobrir num erro de download.
        """
        from cartomet_br.data.ecmwf import variable_available

        for combo in (self.var_combo, self.sfc_var_combo):
            item_model = combo.model()
            for i in range(combo.count()):
                key = str(combo.itemData(i))
                ok = variable_available(key, model)
                item = item_model.item(i)
                if item is not None:
                    item.setEnabled(ok)
                combo.setItemData(
                    i,
                    "" if ok else "Indisponível no AIFS — use o modelo IFS",
                    Qt.ItemDataRole.ToolTipRole,
                )
            # Seleção atual caiu numa variável desabilitada → volta p/ a 1ª válida
            current_key = str(combo.currentData())
            if not variable_available(current_key, model):
                for i in range(combo.count()):
                    if variable_available(str(combo.itemData(i)), model):
                        combo.setCurrentIndex(i)
                        break
        # Ensemble ENS é do IFS — sob AIFS o grupo inteiro fica cinza.
        is_ifs = model != "aifs"
        if hasattr(self, "ens_group"):
            self.ens_group.setEnabled(is_ifs)
            self.ens_group.setToolTip(
                ""
                if is_ifs
                else "O Ensemble ENS é do IFS (físico) — o aifs-ens ainda não é suportado"
            )

    def _on_ens_product_changed(self) -> None:
        """Limiar de chuva só faz sentido no produto de probabilidade."""
        self.ens_thr_combo.setEnabled(self.ens_product_combo.currentData() == "prob")

    def _on_ens_add(self) -> None:
        product = str(self.ens_product_combo.currentData())
        thr = float(self.ens_thr_combo.currentData() or 10.0)
        self.ens_requested.emit(product, thr)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel("CAMPOS METEOROLÓGICOS")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("""
            font-size: 12px; font-weight: bold; color: #9B59B6;
            padding: 6px; background-color: #1A252F; border-radius: 5px;
        """)
        layout.addWidget(title)

        # ─── Presets de análise ───
        preset_group = QGroupBox("Análises prontas")
        preset_layout = QGridLayout(preset_group)
        preset_layout.setSpacing(4)

        preset_styles = {
            "Sinótica clássica": "#2E86C1",
            "Jato e divergência": "#8E44AD",
            "Baixos níveis": "#27AE60",
            "Convecção profunda": "#E74C3C",
            "ZCAS (Escobar)": "#F39C12",
        }

        for i, (name, color) in enumerate(preset_styles.items()):
            btn = QPushButton(name)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color}; padding: 5px;
                    font-size: 10px; font-weight: bold; border-radius: 4px;
                }}
                QPushButton:hover {{ background-color: {color}CC; }}
            """)
            btn.setToolTip(self._preset_tooltip(name))
            btn.clicked.connect(lambda _, n=name: self.preset_requested.emit(n))
            preset_layout.addWidget(btn, i // 2, i % 2)

        layout.addWidget(preset_group)

        # ─── Índice ZCIT (LOCZCIT-PA) — raster categórico calculado ───
        zcit_btn = QPushButton("🛰 ZCIT (LOCZCIT-PA)")
        zcit_btn.setStyleSheet("""
            QPushButton {
                background-color: #E67E22; padding: 7px;
                font-size: 11px; font-weight: bold; border-radius: 4px;
            }
            QPushButton:hover { background-color: #F39C12; }
        """)
        zcit_btn.setToolTip(
            "Índice integrado da ZCIT (LOCZCIT-PA):\n"
            "∇TSM + convergência de baixos níveis + OLR desacumulada (Técnica B).\n"
            "Gera um raster categórico Fraca/Moderada/Forte no Atlântico equatorial,\n"
            "que orienta o traçado manual com a simbologia [6] ZCIT."
        )
        zcit_btn.clicked.connect(self.loczcit_requested.emit)
        layout.addWidget(zcit_btn)

        # ─── Bloqueio Atmosférico (anomalia de Z500) ───
        blocking_btn = QPushButton("🌀 Bloqueio Atmosférico (Z500)")
        blocking_btn.setStyleSheet("""
            QPushButton {
                background-color: #2980B9; padding: 7px;
                font-size: 11px; font-weight: bold; border-radius: 4px;
            }
            QPushButton:hover { background-color: #3498DB; }
        """)
        blocking_btn.setToolTip(
            "Anomalia de altura geopotencial em 500 hPa:\n"
            "gh do IFS (rodada + step) − climatologia ERA5 1991–2020 (00Z/12Z,\n"
            "média + 4 harmônicos), setor 150°W–30°E / 75°S–15°N.\n"
            "Anomalias positivas intensas e persistentes (≳ +100 gpm) em latitudes\n"
            "médias-altas sugerem bloqueio; o padrão ômega aparece como dipolo A–B."
        )
        blocking_btn.clicked.connect(self.blocking_requested.emit)
        layout.addWidget(blocking_btn)

        # ─── Comparação de rodadas (consistência run-to-run) ───
        run_compare_btn = QPushButton("🔀 Comparar Rodadas")
        run_compare_btn.setStyleSheet("""
            QPushButton {
                background-color: #5D6D7E; padding: 7px;
                font-size: 11px; font-weight: bold; border-radius: 4px;
            }
            QPushButton:hover { background-color: #85929E; }
        """)
        run_compare_btn.setToolTip(
            "Δ entre a rodada atual e uma anterior, no MESMO horário de validade:\n"
            "rodada nova − rodada antiga (step + defasagem na antiga).\n"
            "Vermelho = intensificou; azul = enfraqueceu. Diferenças grandes\n"
            "sinalizam baixa consistência entre rodadas (menos confiança)."
        )
        run_compare_btn.clicked.connect(self.run_compare_requested.emit)
        layout.addWidget(run_compare_btn)

        # ─── Ensemble ENS (51 membros = 50 perturbados + controle) ───
        self.ens_group = QGroupBox("Ensemble ENS (51 membros)")
        ens_layout = QVBoxLayout(self.ens_group)
        ens_layout.setSpacing(4)

        ens_row = QHBoxLayout()
        self.ens_product_combo = QComboBox()
        for key, label in (
            ("prob", "P(chuva 24h > limiar)"),
            ("msl", "PNMM — média ± σ"),
            ("gh500", "Z500 — média ± σ"),
        ):
            self.ens_product_combo.addItem(label, key)
        self.ens_product_combo.setToolTip(
            "Produtos calculados dos 51 membros do ENS:\n"
            "• P(chuva 24h > limiar): % dos membros com R24h acima do limiar\n"
            "• média ± σ: média em linhas + dispersão sombreada (incerteza)"
        )
        ens_row.addWidget(self.ens_product_combo, stretch=1)
        self.ens_thr_combo = QComboBox()
        for thr in ENS_PROB_THRESHOLDS_MM:
            self.ens_thr_combo.addItem(f"{thr:g} mm", float(thr))
        self.ens_thr_combo.setCurrentIndex(2)  # 10 mm
        self.ens_thr_combo.setToolTip("Limiar de chuva acumulada em 24 h")
        ens_row.addWidget(self.ens_thr_combo)
        ens_layout.addLayout(ens_row)

        ens_btn = QPushButton("🎲 Adicionar camada ENS")
        ens_btn.setStyleSheet("""
            QPushButton {
                background-color: #7D3C98; padding: 7px;
                font-size: 11px; font-weight: bold; border-radius: 4px;
            }
            QPushButton:hover { background-color: #9B59B6; }
        """)
        ens_btn.setToolTip(
            "Baixa os 50 membros perturbados (enfo) do campo escolhido e reusa o\n"
            "controle do cache das cartas normais (oper) — 51 membros no total.\n"
            "≈ 25–75 MB por camada nova; o ENS publica ~8 h após a rodada.\n"
            "Probabilidade de chuva requer step ≥ +24h (janela de 24 h)."
        )
        ens_btn.clicked.connect(self._on_ens_add)
        ens_layout.addWidget(ens_btn)
        self.ens_product_combo.currentIndexChanged.connect(self._on_ens_product_changed)
        self._on_ens_product_changed()
        layout.addWidget(self.ens_group)

        # ─── Diagnóstico Baroclínico (apoio ao traçado MANUAL de frentes) ───
        baroclinic_btn = QPushButton("🌡 Diagnóstico Baroclínico")
        baroclinic_btn.setStyleSheet("""
            QPushButton {
                background-color: #16A085; padding: 7px;
                font-size: 11px; font-weight: bold; border-radius: 4px;
            }
            QPushButton:hover { background-color: #1ABC9C; }
        """)
        baroclinic_btn.setToolTip(
            "Empilha campos diagnósticos para o traçado MANUAL de frentes\n"
            "(human-in-the-loop) no nível escolhido (850 hPa padrão):\n"
            "• Gradiente de θe (sombreado) — ligado\n"
            "• Eixo da Frente / TFP (linha neutra-guia) — ligado\n"
            "• Advecção de θe, θe e Frontogênese de Petterssen — disponíveis (desligados)\n\n"
            "O eixo TFP orienta, mas quem classifica (fria/quente) e traça é o previsor."
        )
        baroclinic_btn.clicked.connect(self.baroclinic_requested.emit)
        layout.addWidget(baroclinic_btn)

        # ─── Avisos INMET (overlay de contexto — polígonos de alerta ao vivo) ───
        inmet_btn = QPushButton("⚠ Avisos INMET (ativos)")
        inmet_btn.setStyleSheet("""
            QPushButton {
                background-color: #E67E22; padding: 7px;
                font-size: 11px; font-weight: bold; border-radius: 4px;
            }
            QPushButton:hover { background-color: #F39C12; }
        """)
        inmet_btn.setToolTip(
            "Baixa os avisos meteorológicos ATIVOS do INMET e os desenha como\n"
            "polígonos coloridos por severidade (amarelo=Perigo Potencial,\n"
            "laranja=Perigo, vermelho=Grande Perigo).\n\n"
            "Camada de ORIENTAÇÃO (contexto) — quem traça a carta é o previsor.\n"
            "Fonte: INMET (apiprevmet3). Requer conexão; mostra os avisos\n"
            "publicados no momento, sem histórico. Avisos EM VIGOR saem com\n"
            "contorno sólido; os FUTUROS (validade por começar), tracejados —\n"
            "e podem ser ocultados pelo filtro abaixo."
        )
        inmet_btn.clicked.connect(self.inmet_avisos_requested.emit)
        layout.addWidget(inmet_btn)

        # Filtro: avisos "futuros" (já emitidos, validade ainda por começar).
        self.inmet_future_check = QCheckBox("Incluir avisos futuros (tracejados)")
        self.inmet_future_check.setChecked(True)
        self.inmet_future_check.setToolTip(
            "A API do INMET entrega avisos EM VIGOR e avisos já emitidos cuja\n"
            "validade ainda vai começar ('futuros'). Ligado: os futuros aparecem\n"
            "com contorno TRACEJADO, preenchimento mais leve e rótulo '(futuro)'.\n"
            "Desligado: só o que está em vigor agora. A troca re-renderiza a\n"
            "última busca na hora — sem nova consulta ao INMET."
        )
        # toggled(bool) já entrega o booleano — sem lambda decodificando CheckState.
        self.inmet_future_check.toggled.connect(self.inmet_future_toggled)
        layout.addWidget(self.inmet_future_check)

        # ─── Instabilidade (CAPE/CIN/LI/K) — campos derivados do modelo (F9) ───
        instab_group = QGroupBox("Instabilidade (modelo IFS — aprox.)")
        instab_layout = QGridLayout(instab_group)
        instab_layout.setSpacing(5)
        instab_buttons = (
            ("K-Index", ["kindex"], "Índice K na grade nativa (vetorizado, rápido)."),
            ("Lifted Index", ["li"], "Lifted Index — ascensão de parcela em grade engrossada."),
            (
                "CAPE / CIN",
                ["cape", "cin"],
                "CAPE e CIN de superfície — grade engrossada (mais lento).",
            ),
            ("Total Totals", ["totaltotals"], "Índice Total Totals (TT) na grade nativa (rápido)."),
            (
                "LCL",
                ["lcl"],
                "Nível de Condensação por Levantamento — altura da base da nuvem (m, MSL).",
            ),
            (
                "LFC",
                ["lfc"],
                "LFC (m) — Nível de Convecção Livre. Altura a partir da qual a "
                "parcela sobe sozinha.",
            ),
            (
                "EL",
                ["el"],
                "Nível de Equilíbrio — altura do topo convectivo/bigorna (m, MSL).",
            ),
            (
                "Cisalh. 0–6 km",
                ["shear"],
                "Cisalhamento do vento 0–6 km — |V(base+6 km) − V(base)| (m/s). "
                "Organiza tempestades severas; mais relevante no Sul/Sudeste. "
                "Baixa um 2º GRIB (u, v).",
            ),
        )
        for i, (label, idxs, tip) in enumerate(instab_buttons):
            btn = QPushButton(label)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #C0392B; padding: 6px;
                    font-size: 10px; font-weight: bold; border-radius: 4px;
                }
                QPushButton:hover { background-color: #E74C3C; }
            """)
            btn.setToolTip(tip + "\n\nModelo IFS, 13 níveis — produto APROXIMADO (não observação).")
            btn.clicked.connect(lambda _checked, idx=idxs: self.instability_requested.emit(idx))
            # Grade com quebra: 3 botões por linha (K/LI/CAPE-CIN | TT/LCL/EL).
            row, col = divmod(i, 3)
            instab_layout.addWidget(btn, row, col)
        layout.addWidget(instab_group)

        # ─── Seção 1: Campos em Altitude ───
        alt_group = QGroupBox("Campos em Altitude")
        alt_layout = QVBoxLayout(alt_group)
        alt_layout.setSpacing(6)

        row_var = QHBoxLayout()
        row_var.addWidget(QLabel("Variável:"))
        self.var_combo = QComboBox()
        for key, label in self.PL_VAR_OPTIONS:
            self.var_combo.addItem(label, key)
        self.var_combo.setMinimumWidth(130)
        self.var_combo.currentIndexChanged.connect(self._on_var_changed)
        row_var.addWidget(self.var_combo)
        alt_layout.addLayout(row_var)

        self.level_row = QHBoxLayout()
        self.level_label = QLabel("Nível:")
        self.level_row.addWidget(self.level_label)
        self.level_combo = QComboBox()
        for lv in PL_LEVELS:
            self.level_combo.addItem(f"{lv} hPa", lv)
        self.level_combo.setCurrentIndex(2)  # 850 hPa default
        self.level_combo.setMinimumWidth(100)
        self.level_row.addWidget(self.level_combo)
        alt_layout.addLayout(self.level_row)

        # Tipo de vento + estilo (cor/densidade)
        self.wind_group = QWidget()
        wind_layout = QVBoxLayout(self.wind_group)
        wind_layout.setContentsMargins(0, 0, 0, 0)
        wind_layout.setSpacing(3)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(3)

        self.btn_barbs = QPushButton("Barbelas")
        self.btn_barbs.setCheckable(True)
        self.btn_barbs.setChecked(True)
        self.btn_barbs.setStyleSheet(self._wind_btn_style(True))
        self.btn_barbs.setToolTip("Barbelas de vento (rápido)")
        self.btn_barbs.clicked.connect(lambda: self._select_wind_type("barbs"))

        self.btn_quiver = QPushButton("Vetores")
        self.btn_quiver.setCheckable(True)
        self.btn_quiver.setStyleSheet(self._wind_btn_style(False))
        self.btn_quiver.setToolTip("Setas de vento (rápido)")
        self.btn_quiver.clicked.connect(lambda: self._select_wind_type("quiver"))

        self.btn_stream = QPushButton("Correntes")
        self.btn_stream.setCheckable(True)
        self.btn_stream.setStyleSheet(self._wind_btn_style(False))
        self.btn_stream.setToolTip(
            "Linhas de corrente (streamplot). Render mais PESADO — pode levar "
            "alguns segundos para aparecer na carta; o programa não travou."
        )
        self.btn_stream.clicked.connect(lambda: self._select_wind_type("stream"))

        btn_row.addWidget(self.btn_barbs)
        btn_row.addWidget(self.btn_quiver)
        btn_row.addWidget(self.btn_stream)
        wind_layout.addLayout(btn_row)

        # Cor (3 representações) + densidade (só barbelas/vetores)
        self.wind_style = WindStyleControls()
        wind_layout.addWidget(self.wind_style)

        self.wind_group.setVisible(False)
        alt_layout.addWidget(self.wind_group)

        alt_add_btn = QPushButton("+ Adicionar campo em altitude")
        alt_add_btn.setStyleSheet("""
            QPushButton {
                background-color: #9B59B6; padding: 8px;
                font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background-color: #A569BD; }
        """)
        alt_add_btn.clicked.connect(self._on_add_clicked)
        alt_layout.addWidget(alt_add_btn)

        layout.addWidget(alt_group)

        # ─── Seção 2: Campos de Superfície / Integrados ───
        sfc_group = QGroupBox("Campos de Superfície / Integrados")
        sfc_layout = QVBoxLayout(sfc_group)
        sfc_layout.setSpacing(6)

        row_sfc = QHBoxLayout()
        row_sfc.addWidget(QLabel("Variável:"))
        self.sfc_var_combo = QComboBox()
        for key, label in self.SFC_VAR_OPTIONS:
            self.sfc_var_combo.addItem(label, key)
        self.sfc_var_combo.setMinimumWidth(130)
        self.sfc_var_combo.currentIndexChanged.connect(self._on_sfc_var_changed)
        row_sfc.addWidget(self.sfc_var_combo)
        sfc_layout.addLayout(row_sfc)

        # Método de desacumulação (só para OLR/Precipitação)
        self.technique_row = QWidget()
        tech_layout = QHBoxLayout(self.technique_row)
        tech_layout.setContentsMargins(0, 0, 0, 0)
        tech_layout.addWidget(QLabel("Método:"))
        self.technique_combo = QComboBox()
        self.technique_combo.addItem("Direta (rodada atual)", "direct")
        self.technique_combo.addItem("Estabilizada (mitiga spin-up)", "stabilized")
        self.technique_combo.setToolTip(
            "Desacumulação de OLR e Precipitação (variáveis ACUMULADAS desde o início "
            "da rodada). O valor da janela é a diferença entre dois steps — sempre "
            "≥ 0, pois o acúmulo é monotônico.\n\n"
            "• Direta (padrão): janela de 3h da RODADA ATUAL — chuva = tp[step] − "
            "tp[step−3]. Reflete a previsão da rodada selecionada.\n"
            "• Estabilizada (Técnica B): usa a rodada anterior madura (12h antes), "
            "eliminando o ruído de spin-up da microfísica — recomendada para "
            "convecção e posicionamento da ZCIT."
        )
        tech_layout.addWidget(self.technique_combo)
        tech_layout.addStretch()
        self.technique_row.setVisible(False)  # aparece só p/ olr/precip
        sfc_layout.addWidget(self.technique_row)

        sfc_add_btn = QPushButton("+ Adicionar campo de superfície")
        sfc_add_btn.setStyleSheet("""
            QPushButton {
                background-color: #1ABC9C; padding: 8px;
                font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background-color: #16A085; }
        """)
        sfc_add_btn.clicked.connect(self._on_add_sfc_clicked)
        sfc_layout.addWidget(sfc_add_btn)

        layout.addWidget(sfc_group)

        # ─── Camadas ativas ───
        self.layers_group = QGroupBox("Camadas ativas")
        self.layers_layout = QVBoxLayout(self.layers_group)
        self.layers_layout.setSpacing(3)

        self.no_layers_label = QLabel(
            "<small style='color: #7F8C8D;'>Nenhuma camada adicionada</small>"
        )
        self.no_layers_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layers_layout.addWidget(self.no_layers_label)

        layout.addWidget(self.layers_group)

        layout.addStretch()

        note = QLabel(
            "<small style='color: #7F8C8D;'>"
            "Cada camada é independente.<br>"
            "Toggle não apaga simbologias.</small>"
        )
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setWordWrap(True)
        layout.addWidget(note)

    def _wind_btn_style(self, active: bool) -> str:
        bg = "#27AE60" if active else "#5D6D7E"
        return f"""
            QPushButton {{
                background-color: {bg}; padding: 5px;
                font-size: 10px; border-radius: 4px;
            }}
        """

    def _select_wind_type(self, wtype: str):
        self.btn_barbs.setChecked(wtype == "barbs")
        self.btn_quiver.setChecked(wtype == "quiver")
        self.btn_stream.setChecked(wtype == "stream")
        self.btn_barbs.setStyleSheet(self._wind_btn_style(wtype == "barbs"))
        self.btn_quiver.setStyleSheet(self._wind_btn_style(wtype == "quiver"))
        self.btn_stream.setStyleSheet(self._wind_btn_style(wtype == "stream"))
        # Correntes = só cor (esconde densidade)
        self.wind_style.set_wind_type(wtype)

    def _get_wind_type(self) -> str:
        if self.btn_quiver.isChecked():
            return "quiver"
        if self.btn_stream.isChecked():
            return "stream"
        return "barbs"

    def _on_var_changed(self, idx):
        key = self.var_combo.currentData()
        is_wind = key == "wind"
        self.wind_group.setVisible(is_wind)

    def _on_sfc_var_changed(self, idx):
        """Mostra o seletor de método só para variáveis desacumuláveis (OLR/precip)."""
        from cartomet_br.data.ecmwf import VARIABLE_REGISTRY

        key = self.sfc_var_combo.currentData()
        tem_tecnica = VARIABLE_REGISTRY.get(key, {}).get("tem_tecnica", False)
        self.technique_row.setVisible(tem_tecnica)

    def get_technique(self) -> str:
        """Método de desacumulação selecionado ('direct' ou 'stabilized')."""
        return self.technique_combo.currentData() or "direct"

    def _on_add_clicked(self):
        var_key = self.var_combo.currentData()
        level = self.level_combo.currentData()
        if var_key == "wind":
            wind_type = self._get_wind_type()
            color, density = self.wind_style.get_style()
        else:
            wind_type, color, density = "barbs", DEFAULT_WIND_COLOR, DEFAULT_WIND_DENSITY
        self.add_layer_requested.emit(var_key, level, wind_type, color, density)

    def _on_add_sfc_clicked(self):
        var_key = self.sfc_var_combo.currentData()
        self.add_layer_requested.emit(var_key, 0, "barbs", DEFAULT_WIND_COLOR, DEFAULT_WIND_DENSITY)

    def add_layer_entry(
        self,
        layer_id: str,
        label: str,
        detail: str,
        checked: bool = True,
        is_wind: bool = False,
    ):
        """Adiciona uma entrada na lista de camadas ativas.

        ``checked``: estado inicial do toggle (False = camada começa oculta, ex.: o
        overlay opcional do eixo da ZCIT).
        ``is_wind``: acende o botão 🎨 de edição de cor/densidade (só campos de vento).
        """
        self.no_layers_label.setVisible(False)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 2, 4, 2)
        row_layout.setSpacing(4)
        row.setStyleSheet("background-color: #34495E; border-radius: 4px;")

        cb = QCheckBox()
        cb.setChecked(checked)
        cb.stateChanged.connect(
            lambda state, lid=layer_id: self.toggle_layer_requested.emit(
                lid, state == Qt.CheckState.Checked.value
            )
        )
        row_layout.addWidget(cb)

        lbl = QLabel(label)
        lbl.setStyleSheet("font-size: 11px;")
        row_layout.addWidget(lbl, stretch=1)

        detail_lbl = QLabel(detail)
        detail_lbl.setStyleSheet("font-size: 10px; color: #3498DB;")
        row_layout.addWidget(detail_lbl)

        if is_wind:
            style_btn = QPushButton("🎨")
            style_btn.setMaximumWidth(30)
            style_btn.setMaximumHeight(22)
            style_btn.setToolTip("Editar cor/densidade do vento")
            style_btn.setStyleSheet("""
                QPushButton {
                    background-color: #9B59B6; color: white;
                    font-size: 11px; border-radius: 3px;
                    padding: 2px 4px; border: none;
                }
                QPushButton:hover { background-color: #A569BD; }
            """)
            style_btn.clicked.connect(
                lambda _, lid=layer_id: self.restyle_layer_requested.emit(lid)
            )
            row_layout.addWidget(style_btn)

        remove_btn = QPushButton("Remover")
        remove_btn.setMaximumWidth(55)
        remove_btn.setMaximumHeight(22)
        remove_btn.setStyleSheet("""
            QPushButton {
                background-color: #E74C3C; color: white;
                font-size: 9px; border-radius: 3px;
                padding: 2px 4px; border: none;
            }
            QPushButton:hover { background-color: #FF6666; }
        """)
        remove_btn.clicked.connect(lambda _, lid=layer_id: self._on_remove(lid))
        row_layout.addWidget(remove_btn)

        self.layers_layout.addWidget(row)
        self._layer_widgets[layer_id] = {"widget": row, "checkbox": cb, "detail": detail_lbl}

    def inmet_future_enabled(self) -> bool:
        """Filtro 'Incluir avisos futuros' — API pública (não ler o widget de fora)."""
        return bool(self.inmet_future_check.isChecked())

    def layer_entry_checked(self, layer_id: str) -> bool | None:
        """Estado do toggle de visibilidade de uma entrada (None se não listada)."""
        entry = self._layer_widgets.get(layer_id)
        if entry is None:
            return None
        return bool(entry["checkbox"].isChecked())

    def set_layer_detail(self, layer_id: str, detail: str) -> bool:
        """Atualiza o texto de detalhe de uma entrada IN PLACE (True se existia).

        Evita o remove+add que resetaria o toggle escolhido pelo usuário e
        jogaria a linha para o fim da lista.
        """
        entry = self._layer_widgets.get(layer_id)
        if entry is None or "detail" not in entry:
            return False
        entry["detail"].setText(detail)
        return True

    def remove_layer_entry(self, layer_id: str):
        """Remove a entrada da lista (sem emitir sinal de remove)."""
        if layer_id in self._layer_widgets:
            w = self._layer_widgets[layer_id]["widget"]
            self.layers_layout.removeWidget(w)
            w.deleteLater()
            del self._layer_widgets[layer_id]

        if not self._layer_widgets:
            self.no_layers_label.setVisible(True)

    def clear_all_layers(self) -> None:
        """Remove todas as entradas da lista (usado pelo 'Limpar mapa')."""
        for layer_id in list(self._layer_widgets.keys()):
            self.remove_layer_entry(layer_id)

    def set_layer_checked(self, layer_id: str, checked: bool) -> None:
        """Marca/desmarca o checkbox de uma camada já na pilha.

        Alterar o estado emite ``toggle_layer_requested`` — usado pelo preset
        Diagnóstico Baroclínico para deixar camadas de apoio (Advecção/θe/
        Frontogênese) empilhadas porém DESLIGADAS por padrão.
        """
        entry = self._layer_widgets.get(layer_id)
        if entry is not None:
            entry["checkbox"].setChecked(checked)

    def _on_remove(self, layer_id: str):
        self.remove_layer_entry(layer_id)
        self.remove_layer_requested.emit(layer_id)

    def _preset_tooltip(self, name: str) -> str:
        """Gera tooltip descritivo para um preset."""
        if name not in self.ANALYSIS_PRESETS:
            return ""
        layers = self.ANALYSIS_PRESETS[name]
        parts = []
        for var_key, level, wind_type in layers:
            var_info = VARIABLE_REGISTRY.get(var_key, {})
            nome = var_info.get("nome", var_key)
            suffix = f" ({wind_type})" if wind_type else ""
            lv_str = f" {level}hPa" if var_key not in SURFACE_VARS else ""
            parts.append(f"{nome}{lv_str}{suffix}")
        return "\n".join(parts)
