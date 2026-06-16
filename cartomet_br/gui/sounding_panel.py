"""
Painel da Sonda Vertical (Skew-T Log-P) — CartoMet BR v3.0.

``QDockWidget`` lateral DESLIZANTE (não um pop-up): o meteorologista vê o mapa 2D à
esquerda e o perfil vertical à direita, ao mesmo tempo (regra de ouro da spec — UX
Single Page, sem janelas flutuantes que escondam o contexto).

Conteúdo do canvas Matplotlib (adaptado de _rascunhos/radiossondagens_na_nuvem.py):
    • Skew-T Log-P principal (metpy.plots.SkewT) com perfil da parcela + CAPE/CIN
    • Hodógrafo no canto superior direito (metpy.plots.Hodograph)
    • Tabela de índices termodinâmicos na parte inferior direita

Estados visuais: placeholder → loading → render | error | future.
O painel é puramente de RENDER — os dados já vêm prontos do SoundingWorker.
"""

from __future__ import annotations

import contextlib
import logging

import matplotlib.gridspec as gridspec
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class SoundingPanel(QDockWidget):
    """Dock direito com o diagrama Skew-T e os estados de carregamento/erro/futuro."""

    source_changed = pyqtSignal(str)  # "observed" (Wyoming) | "model" (IFS)

    _SOURCES = (("Observada (Wyoming)", "observed"), ("Modelo (IFS)", "model"))

    def __init__(self, title: str = "Sondagem Vertical (Skew-T)", parent=None) -> None:
        super().__init__(title, parent)
        # Painel deslizante: pode mover/flutuar/fechar (≠ docks fixos da janela)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.setMinimumWidth(420)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._header = QLabel("Sonda Vertical")
        self._header.setStyleSheet("font-weight: bold; font-size: 12px; padding: 2px;")
        self._header.setWordWrap(True)
        layout.addWidget(self._header)

        # Seletor de FONTE: radiossonda observada (Wyoming) × perfil do modelo (IFS).
        src_row = QWidget()
        src_layout = QHBoxLayout(src_row)
        src_layout.setContentsMargins(0, 0, 0, 0)
        src_layout.setSpacing(4)
        src_layout.addWidget(QLabel("Fonte:"))
        self._source_combo = QComboBox()
        for label, key in self._SOURCES:
            self._source_combo.addItem(label, key)
        self._source_combo.setToolTip(
            "Observada: radiossonda real (Wyoming), ancora na estação mais próxima, só análise (+0h).\n"
            "Modelo: perfil do IFS em QUALQUER ponto (oceano, previsão) — pseudo-sondagem (13 níveis)."
        )
        self._source_combo.currentIndexChanged.connect(
            lambda _i: self.source_changed.emit(self.source())
        )
        src_layout.addWidget(self._source_combo, stretch=1)
        layout.addWidget(src_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminado (spinner)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.hide()
        layout.addWidget(self._progress)

        # Badge de honestidade (preenchido só na pseudo-sondagem do modelo).
        self._source_badge = QLabel()
        self._source_badge.setWordWrap(True)
        self._source_badge.setStyleSheet(
            "background:#FFF3CD; color:#7A5C00; border:1px solid #FFE08A;"
            "border-radius:4px; padding:3px 6px; font-size:10px;"
        )
        self._source_badge.hide()
        layout.addWidget(self._source_badge)

        self.fig = Figure(figsize=(6, 8), facecolor="white", dpi=100)
        self.canvas = FigureCanvas(self.fig)
        layout.addWidget(self.canvas, stretch=1)

        self.setWidget(container)
        self._show_centered_message(
            "📍 Ative a Sonda Vertical e clique no mapa.\n\n"
            "• Fonte “Observada”: ancora na radiossonda mais próxima (Wyoming).\n"
            "• Fonte “Modelo”: perfil do IFS no ponto clicado — qualquer lugar,\n"
            "  inclusive oceano e previsão (pseudo-sondagem).",
            color="#555555",
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  Fonte (observada × modelo)
    # ─────────────────────────────────────────────────────────────────────────
    def source(self) -> str:
        """Retorna a fonte selecionada: "observed" ou "model"."""
        return self._source_combo.currentData() or "observed"

    def set_source(self, key: str) -> None:
        """Define a fonte programaticamente (sincroniza o combo)."""
        idx = self._source_combo.findData(key)
        if idx >= 0:
            self._source_combo.setCurrentIndex(idx)

    # ─────────────────────────────────────────────────────────────────────────
    #  Helpers de desenho
    # ─────────────────────────────────────────────────────────────────────────
    def _show_centered_message(
        self, text: str, color: str = "#333333", facecolor: str = "white"
    ) -> None:
        """Limpa a figura e escreve uma mensagem centralizada."""
        self.fig.clear()
        self.fig.set_facecolor(facecolor)
        ax = self.fig.add_subplot(111)
        ax.axis("off")
        ax.text(
            0.5,
            0.5,
            text,
            ha="center",
            va="center",
            wrap=True,
            fontsize=12,
            color=color,
            transform=ax.transAxes,
        )
        self.canvas.draw_idle()

    # ─────────────────────────────────────────────────────────────────────────
    #  Estados públicos
    # ─────────────────────────────────────────────────────────────────────────
    def show_loading(self, station_label: str, time_label: str) -> None:
        """Estado de espera enquanto o SoundingWorker baixa/calcula."""
        self._source_badge.hide()
        self._header.setText(f"⏳ {station_label} — {time_label}")
        self._progress.show()
        self._show_centered_message(
            f"Baixando radiossondagem…\n{station_label}\n{time_label}",
            color="#1A6FAF",
        )

    def show_error(self, message: str) -> None:
        """Falha de rede / sem dados — mensagem amigável, GUI nunca quebra."""
        self._progress.hide()
        self._source_badge.hide()
        self._header.setText("⚠️ Sondagem indisponível")
        self._show_centered_message(f"⚠️ {message}", color="#B00020")

    def show_future(self, time_label: str) -> None:
        """Fail-state 'viagem ao futuro': o balão ainda não foi lançado.

        NÃO há chamada de rede neste caso — o orquestrador nem cria a thread.
        """
        self._progress.hide()
        self._source_badge.hide()
        self._header.setText("⚠️ Observação futura indisponível")
        self.fig.clear()
        self.fig.set_facecolor("#202020")
        ax = self.fig.add_subplot(111)
        ax.axis("off")
        ax.text(
            0.5,
            0.5,
            f"⚠️ Observações futuras indisponíveis.\n\n"
            f"O balão de {time_label}\nainda não foi lançado.",
            ha="center",
            va="center",
            fontsize=13,
            color="#FFD700",
            transform=ax.transAxes,
            fontweight="bold",
        )
        self.canvas.draw_idle()

    def render(self, result) -> None:
        """Desenha o painel completo a partir de um SoundingResult."""
        self._progress.hide()
        note = getattr(result, "source_note", "")
        icon = "🛰" if note else "🎈"  # 🛰 = perfil do modelo · 🎈 = radiossonda
        self._header.setText(f"{icon} {result.station_label} — {result.time_label}")
        if note:
            self._source_badge.setText(f"⚠️ {note}")
            self._source_badge.show()
        else:
            self._source_badge.hide()
        try:
            self._render_skewt(result)
        except Exception as e:  # blindagem final — nunca derruba a GUI
            logger.exception("Falha ao renderizar o Skew-T")
            self.show_error(f"Erro ao desenhar o diagrama: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    #  Render do Skew-T + hodógrafo + tabela
    # ─────────────────────────────────────────────────────────────────────────
    def _render_skewt(self, result) -> None:
        import metpy.calc as mpcalc
        from metpy.plots import Hodograph, SkewT
        from metpy.units import units

        p = result.pressure_q
        T = result.temperature_q
        Td = result.dewpoint_q

        self.fig.clear()
        self.fig.set_facecolor("white")
        gs = gridspec.GridSpec(3, 3, figure=self.fig)

        # ── Skew-T (2/3 esquerda) ────────────────────────────────────────────
        skew = SkewT(self.fig, rotation=45, subplot=gs[0:2, :2])
        skew.plot(p, T, color="red", linewidth=1.5)
        skew.plot(p, Td, color="green", linewidth=1.5)

        if result.parcel_q is not None:
            skew.plot(p, result.parcel_q, color="black", linewidth=1.5)
            try:
                skew.shade_cin(p, T, result.parcel_q, Td)
                skew.shade_cape(p, T, result.parcel_q)
            except Exception:  # noqa: BLE001 — sombreamento é cosmético
                pass

        # Barbelas de vento reamostradas por nível (opcional)
        niveis = None
        if result.has_wind:
            try:
                alvo = np.arange(100, 1050, 50) * units.hPa
                niveis = mpcalc.resample_nn_1d(p, alvo)
                skew.plot_barbs(p[niveis], result.u_wind_q[niveis], result.v_wind_q[niveis])
            except Exception as e:  # noqa: BLE001
                logger.debug("Barbelas indisponíveis: %s", e)
                niveis = None

        try:
            skew.plot_dry_adiabats(alpha=0.25)
            skew.plot_moist_adiabats(alpha=0.25)
            skew.plot_mixing_lines(alpha=0.25)
        except Exception:  # noqa: BLE001
            pass

        skew.ax.axvline(0, color="cyan", linestyle="--", linewidth=1)
        skew.ax.set_xlim(-30, 40)
        skew.ax.set_xlabel("Temperatura (°C)", fontsize=9)
        skew.ax.set_ylabel("Pressão (hPa)", fontsize=9)
        skew.ax.tick_params(labelsize=8)

        # ── Hodógrafo (canto superior direito) ───────────────────────────────
        ax_hod = self.fig.add_subplot(gs[0, -1])
        if result.has_wind and niveis is not None:
            try:
                hod = Hodograph(ax_hod, component_range=80.0)
                hod.add_grid(increment=20)
                hod.plot_colormapped(
                    result.u_wind_q[niveis],
                    result.v_wind_q[niveis],
                    p[niveis],
                    cmap="BuPu_r",
                )
                ax_hod.set_xlim(-40, 40)
                ax_hod.set_ylim(-40, 40)
                ax_hod.tick_params(labelsize=6)
                ax_hod.set_title("Hodógrafo", fontsize=8)
            except Exception as e:  # noqa: BLE001 — vento parcial não derruba o resto
                logger.debug("Hodógrafo indisponível: %s", e)
                ax_hod.axis("off")
                ax_hod.text(
                    0.5,
                    0.5,
                    "Vento\nindisponível",
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="#999999",
                    transform=ax_hod.transAxes,
                )
        else:
            ax_hod.axis("off")
            ax_hod.text(
                0.5,
                0.5,
                "Sem dados\nde vento",
                ha="center",
                va="center",
                fontsize=8,
                color="#999999",
                transform=ax_hod.transAxes,
            )

        # ── Tabela de índices (inferior direita) ─────────────────────────────
        ax_tbl = self.fig.add_subplot(gs[1:, -1])
        ax_tbl.axis("off")
        ax_tbl.set_title("Índices", fontsize=9, loc="left", fontweight="bold")
        n = max(len(result.indices), 1)
        for i, (label, value) in enumerate(result.indices):
            y = 0.95 - i * (0.9 / n)
            ax_tbl.text(0.02, y, label, fontsize=8, va="top")
            ax_tbl.text(0.98, y, value, fontsize=8, va="top", ha="right", fontweight="bold")

        self.fig.suptitle(
            f"{result.station_label}   •   {result.time_label}",
            fontsize=11,
            fontweight="bold",
        )
        with contextlib.suppress(Exception):
            self.fig.tight_layout(rect=(0, 0, 1, 0.96))
        self.canvas.draw_idle()
