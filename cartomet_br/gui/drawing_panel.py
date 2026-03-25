"""
Painel de simbologias e ferramentas de desenho do CartoMet BR.

Contém SymbolButton (botão de seleção de símbolo) e SymbologyPanel
(painel lateral esquerdo com grid de símbolos e controles).
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QGroupBox, QCheckBox, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut, QPixmap

from cartomet_br.symbols import MODOS
from cartomet_br.gui._constants import APP_AUTHOR, APP_VERSION, get_logo_path


# ═══════════════════════════════════════════════════════════════════════════════
#  BOTÃO DE SIMBOLOGIA
# ═══════════════════════════════════════════════════════════════════════════════

class SymbolButton(QPushButton):
    """Botão colorido para seleção de simbologia."""

    key: str
    modo: dict[str, Any]

    def __init__(self, key: str, modo: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.modo = modo

        self.setText(f"[{key}] {modo['nome']}")
        self.setCheckable(True)
        self.setMinimumHeight(34)
        self.setMaximumHeight(38)

        cor = modo["cor"]
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {cor};
                border: 2px solid {cor};
                border-radius: 5px;
                color: white;
                font-weight: bold;
                font-size: 10px;
                text-align: left;
                padding-left: 8px;
            }}
            QPushButton:hover {{ border-color: white; }}
            QPushButton:checked {{ border-color: #F1C40F; border-width: 3px; }}
        """)


# ═══════════════════════════════════════════════════════════════════════════════
#  PAINEL DE SIMBOLOGIAS (esquerda)
# ═══════════════════════════════════════════════════════════════════════════════

class SymbologyPanel(QWidget):
    """Painel lateral com simbologias, logo e créditos."""

    symbol_changed = pyqtSignal(str)
    flip_changed = pyqtSignal(bool)
    finalize_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()

    current_key: str
    buttons: dict[str, SymbolButton]
    status_group: QGroupBox
    status_label: QLabel
    points_label: QLabel
    flip_check: QCheckBox
    _sat_insert_index: int

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_key = "1"
        self.buttons = {}
        self._setup_ui()
        self._setup_shortcuts()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        # Título
        title = QLabel("SIMBOLOGIAS")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("""
            font-size: 13px; font-weight: bold; color: #3498DB;
            padding: 8px; background-color: #1A252F; border-radius: 5px;
        """)
        layout.addWidget(title)

        # Grid de botões
        grid = QGridLayout()
        grid.setSpacing(4)

        for i, (key, modo) in enumerate(MODOS.items()):
            btn = SymbolButton(key, modo)
            btn.clicked.connect(lambda checked, k=key: self._on_button_clicked(k))
            self.buttons[key] = btn
            grid.addWidget(btn, i // 2, i % 2)

        layout.addLayout(grid)

        # Seleção atual
        self.status_group = QGroupBox("Selecionado")
        status_layout = QVBoxLayout(self.status_group)
        status_layout.setSpacing(4)

        self.status_label = QLabel("Frente Fria")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #1a6faf; padding: 4px;")
        status_layout.addWidget(self.status_label)

        self.points_label = QLabel("Pontos: 0")
        self.points_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(self.points_label)

        layout.addWidget(self.status_group)

        # Flip
        flip_layout = QHBoxLayout()
        flip_layout.addWidget(QLabel("[F] Inverter:"))
        self.flip_check = QCheckBox()
        self.flip_check.stateChanged.connect(self._on_flip_changed)
        flip_layout.addWidget(self.flip_check)
        flip_layout.addStretch()
        layout.addLayout(flip_layout)

        # Botões de ação
        action_layout = QHBoxLayout()
        action_layout.setSpacing(4)

        clear_btn = QPushButton("[C] Limpar")
        clear_btn.setStyleSheet("background-color: #E74C3C; font-size: 10px; padding: 6px;")
        clear_btn.clicked.connect(self.clear_requested.emit)
        action_layout.addWidget(clear_btn)

        undo_btn = QPushButton("[Z] Desfazer")
        undo_btn.setStyleSheet("background-color: #9B59B6; font-size: 10px; padding: 6px;")
        undo_btn.clicked.connect(self.undo_requested.emit)
        action_layout.addWidget(undo_btn)

        redo_btn = QPushButton("[Y] Refazer")
        redo_btn.setStyleSheet("background-color: #8E44AD; font-size: 10px; padding: 6px;")
        redo_btn.clicked.connect(self.redo_requested.emit)
        action_layout.addWidget(redo_btn)

        layout.addLayout(action_layout)

        finalize_btn = QPushButton("[Enter] Finalizar Linha")
        finalize_btn.setStyleSheet("background-color: #27AE60; font-size: 11px; padding: 8px;")
        finalize_btn.clicked.connect(self.finalize_requested.emit)
        layout.addWidget(finalize_btn)

        # Ponto de inserção para painel de satélite
        self._sat_insert_index = layout.count()

        # ─── LOGO E CRÉDITOS DO DESENVOLVEDOR ───
        layout.addStretch()

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background-color: #5D6D7E; margin: 10px 0;")
        layout.addWidget(separator)

        logo_path = get_logo_path()
        if logo_path and logo_path.exists():
            logo_label = QLabel()
            pixmap = QPixmap(str(logo_path))
            scaled = pixmap.scaled(130, 130, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            logo_label.setPixmap(scaled)
            logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(logo_label)

        credits_html = f"""
        <div style='text-align: center; color: #BDC3C7; font-size: 9px; line-height: 1.4;'>
            <p style='color: #3498DB; font-weight: bold; margin-bottom: 4px;'>Desenvolvido por</p>
            <p style='font-weight: bold; font-size: 10px;'>{APP_AUTHOR}</p>
            <p style='margin-top: 6px; color: #3498DB; font-weight: bold;'>Idealizador</p>
            <p style='font-size: 9px;'>Prof. Dr. Everaldo B. de Souza<br/>
            <span style='font-size: 8px; color: #999;'>Professor Titular — IG/UFPA</span></p>
            <p style='margin-top: 6px; color: #3498DB; font-weight: bold;'>Instituições</p>
            <p>PPGGRD-UFPA<br>
            <span style='font-size: 8px;'>Gestão de Riscos e Desastres na Amazônia</span></p>
            <p style='margin-top: 2px;'>FAMET-UFPA<br>
            <span style='font-size: 8px;'>Faculdade de Meteorologia</span></p>
            <p style='margin-top: 8px; color: #7F8C8D;'>Versão {APP_VERSION}</p>
        </div>
        """
        credits_label = QLabel(credits_html)
        credits_label.setWordWrap(True)
        layout.addWidget(credits_label)

        # Seleciona primeiro botão
        self.buttons["1"].setChecked(True)

    def _setup_shortcuts(self) -> None:
        for key in MODOS.keys():
            QShortcut(QKeySequence(key), self).activated.connect(lambda k=key: self._select_symbol(k))
        QShortcut(QKeySequence("F"), self).activated.connect(self._toggle_flip)
        QShortcut(QKeySequence("Return"), self).activated.connect(self.finalize_requested.emit)
        QShortcut(QKeySequence("Z"), self).activated.connect(self.undo_requested.emit)
        QShortcut(QKeySequence("Y"), self).activated.connect(self.redo_requested.emit)
        QShortcut(QKeySequence("C"), self).activated.connect(self.clear_requested.emit)

    def _on_button_clicked(self, key: str) -> None:
        self._select_symbol(key)

    def _select_symbol(self, key: str) -> None:
        for btn in self.buttons.values():
            btn.setChecked(False)
        self.buttons[key].setChecked(True)
        self.current_key = key

        modo = MODOS[key]
        nome = modo["nome"]
        if modo.get("ponto", False):
            nome += "  (clique)"
        self.status_label.setText(nome)
        self.status_label.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {modo['cor']}; padding: 4px;")

        # Desabilita flip para símbolos pontuais
        is_point = modo.get("ponto", False)
        self.flip_check.setEnabled(not is_point)
        if is_point:
            self.flip_check.setChecked(False)

        self.symbol_changed.emit(key)

    def _on_flip_changed(self, state: int) -> None:
        self.flip_changed.emit(state == Qt.CheckState.Checked.value)

    def _toggle_flip(self) -> None:
        self.flip_check.setChecked(not self.flip_check.isChecked())

    def update_points(self, count: int) -> None:
        self.points_label.setText(f"Pontos: {count}")
