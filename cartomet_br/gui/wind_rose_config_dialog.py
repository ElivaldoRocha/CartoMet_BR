"""Diálogo de configuração da Rosa dos Ventos (Fase 3).

Coleta os parâmetros que exigem re-fetch/recomputo: nível vertical (10 m ou um
nível de pressão do IFS), faixas de velocidade, limiar de calmaria e o toggle
de estatísticas (vento médio · rumo predominante). O nº de setores NÃO mora
aqui — o combo "Setores" do painel re-bina em memória, sem rede.

Persistência: os valores aceitos viram defaults via ``QSettings`` (mesmo padrão
do ``ChartHeaderDialog``); ``load_wind_rose_config()`` lê esses defaults no
startup — toda a lógica de persistência da config da rosa vive neste módulo.
O diálogo em si inicializa do dict ATIVO passado pelo chamador (o que a rosa
está usando agora), nunca direto do QSettings — evita mostrar uma config que
não é a vigente.
"""

from __future__ import annotations

import contextlib
import math

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from cartomet_br.data.ecmwf import PL_LEVELS
from cartomet_br.data.wind_rose import CALM_THRESHOLD, DEFAULT_SPEED_BINS, level_label
from cartomet_br.gui._constants import APP_NAME

_SETTINGS_ORG = "PPGGRD-UFPA"

_DEFAULT_BINS_LABEL = "Padrão (0,5–2–4–6–8–10+ m/s)"
_CUSTOM_BINS_LABEL = "Personalizado…"


def _default_config() -> dict:
    return {
        "level_hpa": None,
        "speed_bin_edges": DEFAULT_SPEED_BINS,
        "calm_threshold": CALM_THRESHOLD,
        "show_stats": True,
    }


def _edges_to_csv(edges) -> str:
    """Bordas FINITAS em CSV (a ``inf`` final é implícita); "" se forem as default."""
    if tuple(edges) == tuple(DEFAULT_SPEED_BINS):
        return ""
    return ", ".join(f"{e:g}" for e in edges if math.isfinite(e))


def _parse_edges_csv(text: str) -> tuple[float, ...] | None:
    """CSV → bordas (com ``inf`` acrescida no topo); ``None`` se inválido.

    Exige ≥ 2 valores finitos, não-negativos e estritamente ascendentes.
    Aceita vírgula decimal (pt-BR) quando os valores vêm separados por ";".
    """
    text = text.strip()
    if not text:
        return None
    sep = ";" if ";" in text else ","
    parts = [p.strip().replace(",", ".") for p in text.split(sep) if p.strip()]
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None
    if len(values) < 2:
        return None
    if any(not math.isfinite(v) or v < 0 for v in values):
        return None
    if any(b <= a for a, b in zip(values, values[1:], strict=False)):
        return None
    return (*values, math.inf)


def load_wind_rose_config() -> dict:
    """Config inicial da rosa: defaults do módulo sobrepostos pelo QSettings."""
    cfg = _default_config()
    settings = QSettings(_SETTINGS_ORG, APP_NAME)
    level = settings.value("wind_rose/level_hpa", "", str)
    if level:
        with contextlib.suppress(ValueError):
            cfg["level_hpa"] = float(level)
    edges = _parse_edges_csv(settings.value("wind_rose/speed_bins", "", str))
    if edges is not None:
        cfg["speed_bin_edges"] = edges
    with contextlib.suppress(TypeError, ValueError):
        cfg["calm_threshold"] = float(settings.value("wind_rose/calm_threshold", CALM_THRESHOLD))
    cfg["show_stats"] = settings.value("wind_rose/show_stats", True, bool)
    return cfg


class WindRoseConfigDialog(QDialog):
    """Config da rosa dos ventos (nível, faixas, calmaria, estatísticas)."""

    def __init__(self, current: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rosa dos Ventos — Configuração")
        self.setMinimumWidth(420)
        self._settings = QSettings(_SETTINGS_ORG, APP_NAME)
        self._config = dict(current) if current else _default_config()

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Mudar o nível refaz o download da série (cache-first). Faixas e "
            "calmaria re-binam a série do ponto ativo."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()

        # Nível vertical: 10 m (superfície) ou um nível de pressão do IFS.
        self.level_combo = QComboBox()
        self.level_combo.addItem("10 m (superfície)", None)
        for lv in PL_LEVELS:
            self.level_combo.addItem(level_label(float(lv)), float(lv))
        cur_level = self._config.get("level_hpa")
        idx = self.level_combo.findData(None if cur_level is None else float(cur_level))
        self.level_combo.setCurrentIndex(max(idx, 0))
        self.level_combo.setToolTip(
            "Vento de 10 m (superfície) ou u/v num nível isobárico do IFS.\n"
            "Sobre relevo alto, níveis baixos podem não existir (steps pulados)."
        )
        form.addRow("Nível:", self.level_combo)

        # Faixas de velocidade: padrão ou CSV custom (última faixa sempre aberta).
        self.bins_combo = QComboBox()
        self.bins_combo.addItem(_DEFAULT_BINS_LABEL)
        self.bins_combo.addItem(_CUSTOM_BINS_LABEL)
        self.bins_edit = QLineEdit()
        self.bins_edit.setPlaceholderText("bordas em m/s, ex.: 0.5, 2, 4, 6, 8, 10")
        self.bins_edit.setToolTip(
            "Bordas ascendentes das faixas (m/s), separadas por vírgula.\n"
            "A última faixa é aberta (≥ último valor)."
        )
        cur_edges = tuple(self._config.get("speed_bin_edges", DEFAULT_SPEED_BINS))
        if cur_edges != tuple(DEFAULT_SPEED_BINS):
            self.bins_combo.setCurrentIndex(1)
            self.bins_edit.setText(_edges_to_csv(cur_edges))
        self.bins_combo.currentIndexChanged.connect(self._on_bins_mode_changed)
        form.addRow("Faixas de velocidade:", self.bins_combo)
        form.addRow("", self.bins_edit)

        # Calmaria: abaixo deste limiar a direção não tem sentido físico.
        self.calm_spin = QDoubleSpinBox()
        self.calm_spin.setRange(0.0, 5.0)
        self.calm_spin.setSingleStep(0.1)
        self.calm_spin.setDecimals(1)
        self.calm_spin.setSuffix(" m/s")
        self.calm_spin.setValue(float(self._config.get("calm_threshold", CALM_THRESHOLD)))
        self.calm_spin.setToolTip(
            "Amostras abaixo do limiar contam como calmaria (centro da rosa).\n"
            "Na Amazônia (ventos fracos) este valor muda muito a leitura."
        )
        form.addRow("Limiar de calmaria:", self.calm_spin)

        self.stats_check = QCheckBox("Mostrar vento médio e rumo predominante")
        self.stats_check.setChecked(bool(self._config.get("show_stats", True)))
        form.addRow("", self.stats_check)

        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #C0392B;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._on_bins_mode_changed()

    def _on_bins_mode_changed(self) -> None:
        custom = self.bins_combo.currentIndex() == 1
        self.bins_edit.setVisible(custom)
        self.error_label.hide()

    def accept(self) -> None:
        """Valida as faixas customizadas — OK bloqueado com mensagem se inválidas."""
        if self.bins_combo.currentIndex() == 1:
            edges = _parse_edges_csv(self.bins_edit.text())
            if edges is None:
                self.error_label.setText(
                    "Faixas inválidas: informe ≥ 2 bordas não-negativas em ordem "
                    "estritamente crescente (ex.: 0.5, 2, 4, 6, 8, 10)."
                )
                self.error_label.show()
                return
        super().accept()

    def config(self) -> dict:
        """Devolve a config escolhida e persiste como default (QSettings)."""
        if self.bins_combo.currentIndex() == 1:
            edges = _parse_edges_csv(self.bins_edit.text()) or tuple(DEFAULT_SPEED_BINS)
        else:
            edges = tuple(DEFAULT_SPEED_BINS)
        level = self.level_combo.currentData()
        cfg = {
            "level_hpa": None if level is None else float(level),
            "speed_bin_edges": edges,
            "calm_threshold": float(self.calm_spin.value()),
            "show_stats": self.stats_check.isChecked(),
        }
        self._settings.setValue(
            "wind_rose/level_hpa", "" if cfg["level_hpa"] is None else f"{cfg['level_hpa']:g}"
        )
        self._settings.setValue("wind_rose/speed_bins", _edges_to_csv(edges))
        self._settings.setValue("wind_rose/calm_threshold", cfg["calm_threshold"])
        self._settings.setValue("wind_rose/show_stats", cfg["show_stats"])
        return cfg
