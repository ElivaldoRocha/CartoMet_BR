"""Painel de seleção de campos ERA5 (reanálise Copernicus/CDS).

Diferente do fluxo do IFS (rodada + step de previsão), o ERA5 é indexado por
**data/hora absolutas**. Este painel expõe: variável (superfície ou nível de
pressão), nível (quando aplicável), período (data inicial/final), hora UTC e
modo de agregação (instantâneo ou média/máx/mín/soma do período). A **região**
é herdada do painel de Configurações (mesma régua do app).

Emite ``add_era5_layer_requested(var_key, date_start, date_end, hour, level, agg)``;
o download e a validação (incl. o atraso do ERA5T) ficam no ``DataService``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cartomet_br.data.cds_credentials import ERA5_MIN_DELAY_DAYS
from cartomet_br.data.ecmwf import PL_LEVELS
from cartomet_br.data.era5 import (
    _SINGLE_HOUR_AGGS,
    AGG_LABELS,
    AGG_USER_THRESH_MODES,
    ERA5_VARIABLES,
    HOT_DAY_DEFAULT_C,
    default_agg,
    profile_modes,
)

# (rótulo amigável, chave da variável, é nível de pressão?)
_VARIABLE_OPTIONS: list[tuple[str, str, bool]] = [
    # Superfície (Single Levels)
    ("Temp. 2 m", "era5_t2m", False),
    ("Temp. Máxima 2 m", "era5_tmax", False),
    ("Temp. Mínima 2 m", "era5_tmin", False),
    ("Ponto de Orvalho 2 m", "era5_d2m", False),
    ("Vento 10 m (u, v)", "era5_wind10m", False),
    ("Rajada de Vento 10 m", "era5_gust", False),
    ("PNMM", "era5_mslp", False),
    ("Precipitação", "era5_precip", False),
    ("Água Precipitável", "era5_tcwv", False),
    ("Cobertura de Nuvens", "era5_tcc", False),
    # Oceano
    ("TSM bulk (SST)", "era5_sst", False),
    # Radiação
    ("OLR (onda longa emergente)", "era5_olr", False),
    ("Onda Curta no Topo", "era5_toa_sw", False),
    ("Onda Curta à Superfície ↓", "era5_ssrd", False),
    ("Onda Longa à Superfície ↓", "era5_strd", False),
    # Convecção / instabilidade
    ("CAPE", "era5_cape", False),
    ("Índice K", "era5_kindex", False),
    ("Total Totals", "era5_totalx", False),
    # Níveis de pressão (Pressure Levels)
    ("Geopotencial — níveis", "era5pl_gh", True),
    ("Temperatura — níveis", "era5pl_t", True),
    ("Vento — níveis", "era5pl_wind", True),
    ("Umidade Relativa — níveis", "era5pl_r", True),
    ("Umidade Específica — níveis", "era5pl_q", True),
    ("Vel. Vertical ω — níveis", "era5pl_w", True),
    ("Vorticidade — níveis", "era5pl_vo", True),
    ("Divergência — níveis", "era5pl_d", True),
]

# Os modos de agregação ofertados dependem da VARIÁVEL (perfil físico): somar
# temperatura não faz sentido, a unidade da chuva muda com o modo, a rajada quer o
# pico. Por isso o ``agg_combo`` é repovoado a cada troca de variável a partir do
# perfil (``ERA5_VARIABLES[key].agg_profile`` → ``AGG_PROFILES``), com rótulos e
# default vindos do engine (``AGG_LABELS`` / ``default_agg``). ``_SINGLE_HOUR_AGGS``
# (hora/media_hora) habilita o seletor "Hora UTC".

_DEFAULT_LEVEL = 850  # nível inicial do combo (baixos níveis, uso frequente)


class ERA5Panel(QWidget):
    """Painel para adicionar camadas de reanálise ERA5 (superfície e níveis)."""

    add_era5_layer_requested = pyqtSignal(str, str, str, int, int, str, float)
    # (var_key, date_start_iso, date_end_iso, hour, level, agg, thresh)
    series_mode_toggled = pyqtSignal(bool)  # ativa/desativa o clique de série temporal

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._on_var_changed()  # também repovoa a agregação e sincroniza o "Hora UTC"

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel("ERA5 — REANÁLISE (CDS)")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #16A085;"
            "padding: 6px; background-color: #1A252F; border-radius: 5px;"
        )
        layout.addWidget(title)

        info = QLabel(
            "<small style='color:#95A5A6;'>Reanálise Copernicus — não é previsão. "
            f"Publicada com ~{ERA5_MIN_DELAY_DAYS - 1} dias de atraso (ERA5T).</small>"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        group = QGroupBox("Novo campo ERA5")
        form = QFormLayout(group)
        form.setSpacing(6)

        self.var_combo = QComboBox()
        for label, key, _is_pl in _VARIABLE_OPTIONS:
            self.var_combo.addItem(label, key)
        self.var_combo.currentIndexChanged.connect(self._on_var_changed)
        form.addRow("Variável:", self.var_combo)

        self.level_combo = QComboBox()
        for lv in PL_LEVELS:
            self.level_combo.addItem(f"{lv} hPa", lv)
        self.level_combo.setCurrentIndex(PL_LEVELS.index(_DEFAULT_LEVEL))
        self.level_label = QLabel("Nível:")
        form.addRow(self.level_label, self.level_combo)

        # Padrão: um dia seguro (hoje − atraso), início = fim.
        default_day = datetime.now(UTC).date() - timedelta(days=ERA5_MIN_DELAY_DAYS)
        qdefault = QDate(default_day.year, default_day.month, default_day.day)
        qmax = QDate(qdefault)  # nunca deixar escolher datas indisponíveis

        self.date_start = QDateEdit(qdefault)
        self.date_start.setCalendarPopup(True)
        self.date_start.setDisplayFormat("yyyy-MM-dd")
        self.date_start.setMaximumDate(qmax)
        form.addRow("Data inicial:", self.date_start)

        self.date_end = QDateEdit(qdefault)
        self.date_end.setCalendarPopup(True)
        self.date_end.setDisplayFormat("yyyy-MM-dd")
        self.date_end.setMaximumDate(qmax)
        # A Data final nunca pode ser anterior à inicial (evita período invertido).
        self.date_end.setMinimumDate(qdefault)
        self.date_start.dateChanged.connect(self.date_end.setMinimumDate)
        form.addRow("Data final:", self.date_end)

        self.hour_combo = QComboBox()
        for h in range(24):
            self.hour_combo.addItem(f"{h:02d}:00 UTC", h)
        self.hour_combo.setCurrentIndex(12)
        form.addRow("Hora UTC:", self.hour_combo)

        self.agg_combo = QComboBox()  # populado por variável em _on_var_changed
        self.agg_combo.currentIndexChanged.connect(self._on_agg_changed)
        form.addRow("Agregação:", self.agg_combo)

        # Limiar (°C) dos índices de dias quentes/onda de calor — regional, por isso
        # definido pelo usuário. Visível só para esses modos (como o combo de nível).
        self.thresh_spin = QDoubleSpinBox()
        self.thresh_spin.setRange(-10.0, 55.0)
        self.thresh_spin.setDecimals(1)
        self.thresh_spin.setSingleStep(0.5)
        self.thresh_spin.setSuffix(" °C")
        self.thresh_spin.setValue(HOT_DAY_DEFAULT_C)
        self.thresh_label = QLabel("Limiar (Tmax):")
        form.addRow(self.thresh_label, self.thresh_spin)

        layout.addWidget(group)

        self.download_btn = QPushButton("⬇ Baixar campo ERA5")
        self.download_btn.setStyleSheet(
            "QPushButton { background-color: #16A085; padding: 7px;"
            "font-size: 11px; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #1ABC9C; }"
            "QPushButton:disabled { background-color: #566573; }"
        )
        self.download_btn.clicked.connect(self._emit_request)
        layout.addWidget(self.download_btn)

        # Série temporal num ponto (Fase 3): modo de clique exclusivo, como o
        # Meteograma. Usa a variável/nível/período acima; ignora hora e agregação
        # (a série é sempre horária ao longo do período).
        self.series_btn = QPushButton("📉 Série no ponto (clique)")
        self.series_btn.setCheckable(True)
        self.series_btn.setToolTip(
            "Ative e clique num ponto do mapa para ver a série horária da variável\n"
            "selecionada ao longo do período (Data inicial → Data final). A hora e\n"
            "a agregação são ignoradas na série."
        )
        self.series_btn.setStyleSheet(
            "QPushButton { background-color: #117A65; padding: 6px;"
            "font-size: 10px; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #16A085; }"
            "QPushButton:checked { background-color: #0E6251; }"
        )
        self.series_btn.clicked.connect(self.series_mode_toggled.emit)
        layout.addWidget(self.series_btn)

    def _is_pressure_level(self) -> bool:
        idx = self.var_combo.currentIndex()
        return bool(_VARIABLE_OPTIONS[idx][2]) if 0 <= idx < len(_VARIABLE_OPTIONS) else False

    def _current_key(self) -> str:
        idx = self.var_combo.currentIndex()
        return _VARIABLE_OPTIONS[idx][1] if 0 <= idx < len(_VARIABLE_OPTIONS) else ""

    def _on_var_changed(self) -> None:
        """Ajusta o seletor de nível (só PL) e repovoa a agregação pelo perfil."""
        is_pl = self._is_pressure_level()
        self.level_combo.setVisible(is_pl)
        self.level_label.setVisible(is_pl)
        self._repopulate_agg()

    def _repopulate_agg(self) -> None:
        """Preenche o ``agg_combo`` só com os modos válidos da variável atual.

        Os modos e a ordem (1º = default) vêm do perfil físico da variável — some
        o que não faz sentido (ex.: "Soma" para temperatura) antes de o usuário
        ver. O engine segue permissivo; isto filtra apenas a criação na UI.
        """
        var = ERA5_VARIABLES.get(self._current_key())
        profile = var.agg_profile if var is not None else "state"
        modes = profile_modes(profile) or ("media",)
        blocked = self.agg_combo.blockSignals(True)
        self.agg_combo.clear()
        for mode in modes:
            self.agg_combo.addItem(AGG_LABELS.get(mode, mode), mode)
        idx = self.agg_combo.findData(default_agg(profile))
        self.agg_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.agg_combo.blockSignals(blocked)
        self._on_agg_changed()  # sincroniza o estado do "Hora UTC"

    def _on_agg_changed(self) -> None:
        """A hora vale nos modos de hora fixa; a Data final fica SEMPRE editável.

        A Data final define o período tanto da série temporal (📉, em qualquer
        agregação) quanto do campo agregado. O combo "Hora UTC" só é habilitado
        para "Instantâneo" e "Média à hora fixa"; nos modos que varrem 24 h/dia
        (média/máx/mín/soma/diárias) ele é ignorado. O limiar (°C) só aparece nos
        índices de dias quentes/onda de calor.
        """
        agg = self.agg_combo.currentData()
        self.hour_combo.setEnabled(agg in _SINGLE_HOUR_AGGS)
        needs_thresh = agg in AGG_USER_THRESH_MODES
        self.thresh_spin.setVisible(needs_thresh)
        self.thresh_label.setVisible(needs_thresh)

    def set_downloading(self, downloading: bool) -> None:
        self.download_btn.setEnabled(not downloading)
        self.download_btn.setText("⏳ Baixando ERA5..." if downloading else "⬇ Baixar campo ERA5")

    def _emit_request(self) -> None:
        var_key = self.var_combo.currentData()
        agg = self.agg_combo.currentData()
        hour = int(self.hour_combo.currentData())
        level = int(self.level_combo.currentData()) if self._is_pressure_level() else 0
        d_start = self.date_start.date().toString("yyyy-MM-dd")
        d_end = d_start if agg == "hora" else self.date_end.date().toString("yyyy-MM-dd")
        thresh = float(self.thresh_spin.value()) if agg in AGG_USER_THRESH_MODES else 0.0
        self.add_era5_layer_requested.emit(var_key, d_start, d_end, hour, level, agg, thresh)

    # ── getters usados pelo modo de série temporal (clique no mapa) ──────────
    def current_variable(self) -> str:
        return str(self.var_combo.currentData())

    def current_level(self) -> int:
        return int(self.level_combo.currentData()) if self._is_pressure_level() else 0

    def current_period(self) -> tuple[str, str]:
        """(data_inicial, data_final) ISO — a série cobre todo o período."""
        d_start = self.date_start.date().toString("yyyy-MM-dd")
        d_end = self.date_end.date().toString("yyyy-MM-dd")
        return d_start, d_end

    def set_series_checked(self, checked: bool) -> None:
        self.series_btn.setChecked(checked)
