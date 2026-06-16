"""Diálogo do cabeçalho da carta OMM (F7) — modo carta no export.

Coleta o bloco institucional (instituição, analista, tipo de carta, logo). Os
defaults são persistidos via ``QSettings`` (mesmo mecanismo do resto do app) para
não redigitar a cada carta. Validade/rodada/step e hora de emissão NÃO são
pedidos aqui — a ``MainWindow`` os preenche a partir do estado do mapa.
"""

from __future__ import annotations

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from cartomet_br.gui._constants import (
    APP_INSTITUTION,
    APP_NAME,
    get_institutional_logos_path,
)

CHART_TYPES = [
    "Carta Sinótica de Superfície",
    "Carta de Altitude",
    "Carta de Análise",
    "Carta de Prognóstico",
]

_SETTINGS_ORG = "PPGGRD-UFPA"


class ChartHeaderDialog(QDialog):
    """Coleta o cabeçalho institucional da carta (persistido em QSettings)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Exportar Carta (OMM) — Cabeçalho")
        self.setMinimumWidth(480)
        self._settings = QSettings(_SETTINGS_ORG, APP_NAME)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Preencha o bloco de título institucional. Validade, rodada/step e "
            "hora de emissão são preenchidos automaticamente a partir da carta "
            "carregada."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.institution_edit = QLineEdit(
            self._settings.value("chart/institution", APP_INSTITUTION, str)
        )
        self.analyst_edit = QLineEdit(self._settings.value("chart/analyst", "", str))
        self.analyst_edit.setPlaceholderText("Digite seu nome")
        self.chart_type_edit = QComboBox()
        self.chart_type_edit.setEditable(True)
        self.chart_type_edit.addItems(CHART_TYPES)
        self.chart_type_edit.setCurrentText(
            self._settings.value("chart/chart_type", CHART_TYPES[0], str)
        )

        form.addRow("Instituição:", self.institution_edit)
        form.addRow("Analista:", self.analyst_edit)
        form.addRow("Tipo de carta:", self.chart_type_edit)

        # Logo: caminho persistido; default cai na logo institucional empacotada.
        default_logo = self._settings.value("chart/logo_path", "", str)
        if not default_logo:
            inst_logo = get_institutional_logos_path()
            default_logo = str(inst_logo) if inst_logo else ""
        self.logo_edit = QLineEdit(default_logo)
        logo_btn = QPushButton("Procurar…")
        logo_btn.clicked.connect(self._choose_logo)
        clear_btn = QPushButton("Sem logo")
        clear_btn.clicked.connect(self.logo_edit.clear)
        logo_row = QHBoxLayout()
        logo_row.addWidget(self.logo_edit, 1)
        logo_row.addWidget(logo_btn)
        logo_row.addWidget(clear_btn)
        form.addRow("Logo (opcional):", logo_row)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar Logo",
            self.logo_edit.text() or "",
            "Imagens (*.png *.jpg *.jpeg);;Todos (*)",
        )
        if path:
            self.logo_edit.setText(path)

    def metadata(self) -> dict:
        """Devolve os campos coletados e persiste os defaults para a próxima vez."""
        meta = {
            "institution": self.institution_edit.text().strip(),
            "analyst": self.analyst_edit.text().strip(),
            "chart_type": self.chart_type_edit.currentText().strip(),
            "logo_path": self.logo_edit.text().strip(),
        }
        self._settings.setValue("chart/institution", meta["institution"])
        self._settings.setValue("chart/analyst", meta["analyst"])
        self._settings.setValue("chart/chart_type", meta["chart_type"])
        self._settings.setValue("chart/logo_path", meta["logo_path"])
        return meta
