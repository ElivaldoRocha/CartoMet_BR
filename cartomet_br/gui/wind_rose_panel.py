"""Painel da Rosa dos Ventos (distribuição direção×velocidade num ponto).

Dock deslizante que desenha a rosa do vento PREVISTO (IFS 10 m) ao longo dos
steps da rodada num ponto. Puramente de RENDER — os dados chegam prontos do
``WindRoseWorker`` (um ``WindRoseResult``). Carrega o badge de honestidade: é a
distribuição da PREVISÃO, não uma climatologia. Um combo "Setores" re-bina a
série já baixada (8/16/36 rumos) sem tocar na rede.
"""

from __future__ import annotations

import contextlib
import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cartomet_br.charts.wind_rose_plot import render_wind_rose
from cartomet_br.data.wind_rose import compute_wind_rose
from cartomet_br.gui.analysis_panel import AnalysisDock

logger = logging.getLogger(__name__)

_PLACEHOLDER = (
    "🌹 Ative a Rosa dos Ventos e clique num ponto do mapa.\n\n"
    "Distribuição do vento previsto (IFS, 10 m ou nível de pressão) ao longo\n"
    "dos steps da rodada naquele ponto: de onde sopra e com que intensidade."
)
_BADGE = (
    "DISTRIBUIÇÃO DO VENTO PREVISTO (IFS {level}) ao longo de {n} steps da rodada — "
    "NÃO é climatologia. Mistura padrão sinótico + ciclo diurno de horas locais distintas."
)
_TEXT_COLOR = "#212121"
_GRID_COLOR = "#C8CDD2"


class WindRosePanel(AnalysisDock):
    """Dock direito com a rosa dos ventos (um eixo polar + combo de setores)."""

    #: Fixar a rosa atual no mapa (payload dict com lon/lat/rose/metadados).
    pin_requested = pyqtSignal(object)
    #: Remover todas as rosas fixadas no mapa.
    clear_pins_requested = pyqtSignal()
    #: Abrir o diálogo de configuração (a MainWindow é dona do estado/diálogo).
    config_requested = pyqtSignal()

    def __init__(self, title: str = "Rosa dos Ventos (Ponto)", parent=None) -> None:
        super().__init__(title, parent, min_width=440, figsize=(6.4, 5.6), placeholder=_PLACEHOLDER)
        self._last_result = None
        self._current_rose = None  # rosa exibida (reflete o combo de setores)
        self._show_stats = True  # linha "vento médio · predominante" sob a rosa

        # Linha de controles (setores + fixar/limpar) inserida acima do canvas.
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(6)
        h.addWidget(QLabel("Setores:"))
        self.sectors_combo = QComboBox()
        for n in (8, 16, 36):
            self.sectors_combo.addItem(str(n), n)
        self.sectors_combo.setCurrentIndex(1)  # 16 rumos (padrão)
        self.sectors_combo.setToolTip("Nº de rumos da rosa (re-bina sem baixar de novo).")
        self.sectors_combo.currentIndexChanged.connect(self._on_sectors_changed)
        h.addWidget(self.sectors_combo)
        # Rótulos TEXTUAIS nos botões: glifos como ⚙/🗑 viram "!" em fontes sem
        # o símbolo (visto em campo no Windows) — o usuário não sabia p/ que serviam.
        self.config_btn = QPushButton("Configurar…")
        self.config_btn.setToolTip("Configurar a rosa (nível, faixas de velocidade, calmaria).")
        self.config_btn.clicked.connect(self.config_requested.emit)
        h.addWidget(self.config_btn)
        h.addStretch(1)
        self.pin_btn = QPushButton("📌 Fixar no mapa")
        self.pin_btn.setToolTip("Fixa esta rosa como inset ancorado no ponto (georreferenciado).")
        self.pin_btn.clicked.connect(self._on_pin_clicked)
        h.addWidget(self.pin_btn)
        self.clear_pins_btn = QPushButton("Limpar")
        self.clear_pins_btn.setToolTip("Remove todas as rosas fixadas no mapa.")
        self.clear_pins_btn.clicked.connect(self.clear_pins_requested.emit)
        h.addWidget(self.clear_pins_btn)
        self._controls_row = row
        row.hide()  # só aparece quando há dados

        container = self.widget()
        layout = container.layout() if container is not None else None
        if isinstance(layout, QVBoxLayout):
            layout.insertWidget(max(layout.indexOf(self.canvas), 0), row)

    def render(self, result) -> None:  # type: ignore[override]
        try:
            self._last_result = result
            self._controls_row.show()
            self._draw(result.rose, result)
        except Exception as e:  # blindagem final — nunca derruba a GUI
            logger.exception("Falha ao renderizar a rosa dos ventos")
            self.show_error(f"Erro ao desenhar a rosa dos ventos: {e}")

    def current_n_sectors(self) -> int:
        """Nº de rumos escolhido no combo (a MainWindow monta o worker com ele)."""
        return int(self.sectors_combo.currentData())

    def set_show_stats(self, enabled: bool) -> None:
        """Liga/desliga a linha de estatísticas; redesenha na hora (sem re-fetch)."""
        self._show_stats = bool(enabled)
        if self._last_result is not None and self._current_rose is not None:
            with contextlib.suppress(Exception):
                self._draw(self._current_rose, self._last_result)

    def _on_sectors_changed(self) -> None:
        """Re-bina a série JÁ baixada com o novo nº de setores (sem rede).

        Preserva faixas de velocidade e calmaria da rosa ativa — sem isso,
        trocar Setores resetaria silenciosamente a config custom aos defaults.
        """
        if self._last_result is None:
            return
        prev = self._last_result.rose
        rose = compute_wind_rose(
            self._last_result.speed,
            self._last_result.direction,
            n_sectors=self.current_n_sectors(),
            speed_bin_edges=prev.speed_bin_edges,
            calm_threshold=prev.calm_threshold,
        )
        with contextlib.suppress(Exception):
            self._draw(rose, self._last_result)

    def _on_pin_clicked(self) -> None:
        """Emite o pedido de fixar a rosa ATUAL (reflete o combo) no mapa."""
        if self._last_result is None or self._current_rose is None:
            return
        r = self._last_result
        self.pin_requested.emit(
            {
                "lon": r.lon,
                "lat": r.lat,
                "grid_lon": r.grid_lon,
                "grid_lat": r.grid_lat,
                "level": r.level,
                "base_time": r.base_time,
                "rose": self._current_rose,
            }
        )

    def _draw(self, rose, result) -> None:
        self._current_rose = rose
        ns = "N" if result.grid_lat >= 0 else "S"
        ew = "E" if result.grid_lon >= 0 else "W"
        label = (
            f"IFS {result.level} — {abs(result.grid_lat):.1f}°{ns} {abs(result.grid_lon):.1f}°{ew}"
        )
        self.set_header(f"🌹 {label}")
        self.set_badge(_BADGE.format(level=result.level, n=rose.n_total))

        title = label
        if result.base_time:
            title += f"\nRodada {result.base_time}"

        self.fig.clear()
        self.fig.set_facecolor("white")
        ax = self.fig.add_subplot(111, projection="polar")
        render_wind_rose(
            ax,
            rose,
            title=title,
            text_color=_TEXT_COLOR,
            grid_color=_GRID_COLOR,
            show_stats=self._show_stats,
        )
        self.fig.subplots_adjust(left=0.05, right=0.80, top=0.84, bottom=0.10)
        self.canvas.draw_idle()
