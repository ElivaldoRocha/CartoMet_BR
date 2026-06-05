"""
Painéis de configuração e camadas do CartoMet BR.

Contém SettingsPanel (região, tema, rodada ECMWF, step),
FieldLayerPanel (campos em altitude / OLR), SatellitePanel (GOES)
e SSTPanel (TSM MUR SST 1km).
"""

from datetime import datetime, timedelta, timezone

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QSlider, QGroupBox, QDateEdit,
    QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QDate

from cartomet_br.core.config import (
    EXTENT_BRASIL, EXTENT_AMSUL, EXTENT_NORDESTE, EXTENT_SUDESTE, EXTENT_SUL,
)
from cartomet_br.data.ecmwf import (
    estimate_available_cycles, VARIABLE_REGISTRY, PL_LEVELS,
)
from cartomet_br.gui._constants import VALID_STEPS


# ═══════════════════════════════════════════════════════════════════════════════
#  PAINEL DE SATÉLITE (esquerda, abaixo das simbologias)
# ═══════════════════════════════════════════════════════════════════════════════

class SatellitePanel(QWidget):
    """Painel para download e controle de imagem de satélite GOES."""

    download_requested = pyqtSignal(object)   # datetime alvo
    toggle_requested = pyqtSignal(bool)       # mostrar/ocultar

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
        now_utc = datetime.now(timezone.utc)
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

        target = datetime(
            qdate.year(), qdate.month(), qdate.day(),
            hora, minuto, 0, tzinfo=timezone.utc
        )

        now_utc = datetime.now(timezone.utc)
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
        self.status_label.setText(f"✓ {time_str}")
        self.status_label.setStyleSheet("font-size: 10px; color: #27AE60;")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAINEL DE TSM (MUR SST 1 km — NASA/NOAA)
# ═══════════════════════════════════════════════════════════════════════════════

class SSTPanel(QWidget):
    """Painel para download e controle de TSM (MUR SST 1 km)."""

    download_requested = pyqtSignal(object)   # datetime alvo
    toggle_requested = pyqtSignal(bool)       # mostrar/ocultar

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
        default_date = datetime.now(timezone.utc) - timedelta(days=2)
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
            qdate.year(), qdate.month(), qdate.day(),
            0, 0, 0, tzinfo=timezone.utc,
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
    update_requested = pyqtSignal()
    layers_changed = pyqtSignal(str, bool)  # (nome_camada, visível)
    observations_changed = pyqtSignal(str, bool)  # (kind: "metar"|"synop", ativo)

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
        for name in ["Clássico", "Branco", "Pastel", "Tons de cinza", "Terra", "Escuro"]:
            self.theme_combo.addItem(name)
        self.theme_combo.currentTextChanged.connect(
            lambda name: self.theme_changed.emit(name)
        )
        theme_row.addWidget(self.theme_combo)
        region_layout.addLayout(theme_row)

        layout.addWidget(region_group)

        # ═══ 2. RODADA ECMWF ═══
        rodada_group = QGroupBox("Rodada ECMWF")
        rodada_layout = QVBoxLayout(rodada_group)
        rodada_layout.setSpacing(4)

        self.cycle_combo = QComboBox()
        self.cycle_combo.addItem("Mais recente (auto)", None)
        self.cycle_combo.setMinimumWidth(130)
        rodada_layout.addWidget(self.cycle_combo)

        self.rodada_info_label = QLabel(
            "<small style='color: #95A5A6;'>"
            "Clique Verificar para ver rodadas</small>"
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
            lambda state: self.layers_changed.emit("thickness", state == Qt.CheckState.Checked.value)
        )
        options_layout.addWidget(self.thickness_check)

        self.centers_check = QCheckBox("Centros H/L")
        self.centers_check.setChecked(True)
        self.centers_check.stateChanged.connect(
            lambda state: self.layers_changed.emit("centers", state == Qt.CheckState.Checked.value)
        )
        options_layout.addWidget(self.centers_check)

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
            lambda state: self.observations_changed.emit("synop", state == Qt.CheckState.Checked.value)
        )
        obs_layout.addWidget(self.synop_check)

        self.metar_check = QCheckBox("METAR (horário)")
        self.metar_check.setToolTip(
            "Observações de aeródromos (NOAA AWC), atualizadas de hora em hora."
        )
        self.metar_check.stateChanged.connect(
            lambda state: self.observations_changed.emit("metar", state == Qt.CheckState.Checked.value)
        )
        obs_layout.addWidget(self.metar_check)

        self.obs_time_label = QLabel(
            "<small style='color: #95A5A6;'>Carregue um modelo para sincronizar "
            "as observações ao horário da carta.</small>"
        )
        self.obs_time_label.setWordWrap(True)
        obs_layout.addWidget(self.obs_time_label)

        layout.addWidget(obs_group)

        # ── Observações só fazem sentido na ANÁLISE (+0h): bloqueia em previsões ──
        # As estações refletem o presente/passado; em steps futuros não existem.
        self._obs_ref_dt = None
        self.step_combo.currentIndexChanged.connect(self._update_observations_ui)
        self._update_observations_ui()   # estado inicial coerente com o step atual

    def _on_region_changed(self, name):
        if name in self.REGIONS:
            extent = self.REGIONS[name]
            self.lon_min.setValue(int(extent[0]))
            self.lat_min.setValue(int(extent[1]))
            self.lon_max.setValue(int(extent[2]))
            self.lat_max.setValue(int(extent[3]))
            self.region_changed.emit(extent)

    def _on_apply_region(self):
        self.region_changed.emit(self.get_extent())

    def _on_smooth_changed(self, value):
        sigma = value / 10.0
        self.smooth_label.setText(f"{sigma:.1f}")

    def get_extent(self):
        return [self.lon_min.value(), self.lat_min.value(), self.lon_max.value(), self.lat_max.value()]

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
        synop_hour = (dt.hour // 6) * 6
        date_str = dt.strftime("%d/%m")
        self.obs_time_label.setText(
            f"<small style='color: #95A5A6;'>"
            f"SYNOP → <b>{synop_hour:02d}Z {date_str}</b> · "
            f"METAR → <b>{dt.hour:02d}Z {date_str}</b> (mais recente)"
            f"</small>"
        )

    def _update_observations_ui(self):
        """Habilita SYNOP/METAR só na análise (+0h); em previsões, desmarca e bloqueia.

        Ao desmarcar automaticamente, o sinal `stateChanged` das checkboxes dispara
        a remoção dos artists de estação no MapCanvas (limpeza ao avançar o tempo).
        """
        step = self.get_step() or 0
        if step == 0:
            self.synop_check.setEnabled(True)
            self.metar_check.setEnabled(True)
            self._render_obs_hint()
        else:
            # Desmarca (emite o sinal → remove overlay) e desabilita
            self.synop_check.setChecked(False)
            self.metar_check.setChecked(False)
            self.synop_check.setEnabled(False)
            self.metar_check.setEnabled(False)
            self.obs_time_label.setText(
                "<small style='color: #E67E22;'>⚠️ Observações reais não estão "
                "disponíveis para previsões. Selecione o Step +0h (Análise).</small>"
            )

    def set_downloading(self, downloading: bool):
        self.update_btn.setEnabled(not downloading)
        self.update_btn.setText("⏳ Baixando..." if downloading else "⬇ Baixar Dados ECMWF")

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
        """Verifica quais rodadas ECMWF estão disponíveis e popula o seletor."""
        try:
            info = estimate_available_cycles()

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
                self.cycle_combo.addItem(
                    f"Mais recente ({latest['label']})", None
                )

                for c in info["available"]:
                    max_step_str = f"+{c['max_step']}h"
                    cycle_data = {
                        "cycle": c["cycle"],
                        "date_ymd": c["base_datetime"].strftime("%Y%m%d"),
                    }
                    self.cycle_combo.addItem(
                        f"{c['label']} {c['date_str']} (até {max_step_str})",
                        cycle_data
                    )

                if current_data is not None and isinstance(current_data, dict):
                    for i in range(self.cycle_combo.count()):
                        d = self.cycle_combo.itemData(i)
                        if isinstance(d, dict) and d == current_data:
                            self.cycle_combo.setCurrentIndex(i)
                            break

                avail_str = ", ".join(
                    f"{c['label']}" for c in info["available"]
                )
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
            self.rodada_info_label.setText(
                f"<p style='color: #E74C3C;'>Erro: {str(e)[:50]}</p>"
            )

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
SURFACE_VARS = {"olr", "tcwv"}

class FieldLayerPanel(QWidget):
    """Painel para adicionar/gerenciar campos em altitude e superfície."""

    add_layer_requested = pyqtSignal(str, int, str)   # (var_key, level, wind_type)
    toggle_layer_requested = pyqtSignal(str, bool)     # (layer_id, visible)
    remove_layer_requested = pyqtSignal(str)           # (layer_id)
    preset_requested = pyqtSignal(str)                 # (preset_name)
    loczcit_requested = pyqtSignal()                   # índice ZCIT (LOCZCIT-PA)

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
        ("t",          "Temperatura (t)"),
        ("r",          "Umidade Relativa (r)"),
        ("q",          "Umidade Específica (q)"),
        ("gh",         "Geopotencial (gh)"),
        ("wind",       "Vento (u, v)"),
        ("wind_speed", "Isotacas (vel. vento)"),
        ("w",          "Vel. Vertical ω (w)"),
        ("d",          "Divergência (d)"),
        ("vo",         "Vorticidade (vo)"),
        ("temp_adv",       "Advecção de Temperatura"),
        ("temp_grad",      "Gradiente de Temperatura"),
        ("frontogenesis",  "Frontogênese (Petterssen)"),
        ("mfc",            "Convergência de Umidade (MFC)"),
    ]

    # Campos de superfície / integrados (sem nível)
    SFC_VAR_OPTIONS = [
        ("tcwv",      "Água Precipitável (mm)"),
        ("olr",       "OLR (desacumulada, W/m²)"),
        ("precip",    "Precipitação (3h, mm)"),
        ("sst_model", "TSM modelo IFS (°C)"),
        ("sst_grad",  "Gradiente de TSM (°C/100km)"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layer_widgets = {}
        self._setup_ui()

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

        # Tipo de vento
        self.wind_group = QWidget()
        wind_layout = QHBoxLayout(self.wind_group)
        wind_layout.setContentsMargins(0, 0, 0, 0)
        wind_layout.setSpacing(3)

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

        wind_layout.addWidget(self.btn_barbs)
        wind_layout.addWidget(self.btn_quiver)
        wind_layout.addWidget(self.btn_stream)

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
        wind_type = self._get_wind_type() if var_key == "wind" else "barbs"
        self.add_layer_requested.emit(var_key, level, wind_type)

    def _on_add_sfc_clicked(self):
        var_key = self.sfc_var_combo.currentData()
        self.add_layer_requested.emit(var_key, 0, "barbs")

    def add_layer_entry(self, layer_id: str, label: str, detail: str):
        """Adiciona uma entrada na lista de camadas ativas."""
        self.no_layers_label.setVisible(False)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 2, 4, 2)
        row_layout.setSpacing(4)
        row.setStyleSheet("background-color: #34495E; border-radius: 4px;")

        cb = QCheckBox()
        cb.setChecked(True)
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
        self._layer_widgets[layer_id] = {"widget": row, "checkbox": cb}

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
