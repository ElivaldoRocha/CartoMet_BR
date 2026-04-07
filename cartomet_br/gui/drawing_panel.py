"""
Painel de simbologias e ferramentas de desenho do CartoMet BR.

Contém SymbolButton (botão de seleção de símbolo) e SymbologyPanel
(painel lateral esquerdo com grid de símbolos e controles).
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QGroupBox, QCheckBox, QFrame, QButtonGroup,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import (
    QKeySequence, QShortcut, QPixmap,
    QFont, QColor, QPainter, QIcon,
)

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

# ═══════════════════════════════════════════════════════════════════════════════
#  EMOJIS METEOROLÓGICOS
# ═══════════════════════════════════════════════════════════════════════════════

# (emoji, tooltip)
WEATHER_EMOJIS: list[tuple[str, str]] = [
    ("☀",  "Sol"),
    ("🌤", "Sol com nuvem"),
    ("⛅", "Parcialmente nublado"),
    ("🌥", "Bastante nublado"),
    ("☁",  "Nublado"),
    ("🌫", "Névoa / Nevoeiro"),
    ("🌦", "Chuva e sol"),
    ("🌧", "Chuva"),
    ("⛈", "Trovoada"),
    ("🌩", "Relâmpago"),
    ("🌨", "Neve"),
    ("❄",  "Frio / Geada"),
    ("💨", "Vento"),
    ("🌪", "Tornado / Ciclone"),
    ("🌊", "Maré / Onda"),
    ("🌡", "Temperatura"),
    ("🌈", "Arco-íris"),
    ("⚡", "Atividade elétrica"),
]


def _make_emoji_pixmap(char: str, size: int = 28) -> QPixmap:
    """Renders an emoji to a QPixmap using Qt's native text renderer.

    Qt uses the OS color-emoji font (Segoe UI Emoji on Windows, Apple Color
    Emoji on macOS, Noto Color Emoji on Linux), so the result is always the
    full-colour glyph that the user expects.
    """
    import platform

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    sys_name = platform.system()
    if sys_name == "Windows":
        font_family = "Segoe UI Emoji"
    elif sys_name == "Darwin":
        font_family = "Apple Color Emoji"
    else:
        font_family = "Noto Color Emoji"

    font = QFont(font_family, int(size * 0.65))
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, char)
    painter.end()
    return pixmap


class SymbologyPanel(QWidget):
    """Painel lateral com simbologias, logo e créditos."""

    symbol_changed = pyqtSignal(str)
    flip_changed = pyqtSignal(bool)
    finalize_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    emoji_mode_toggled = pyqtSignal(bool)   # ativou / desativou modo emoji
    emoji_selected = pyqtSignal(str)        # emoji escolhido
    emoji_size_changed = pyqtSignal(int)    # tamanho em pontos (20/28/40)

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

        # ─── EMOJIS METEOROLÓGICOS ───────────────────────────────────────────
        self._emoji_group = QGroupBox("☁ Emojis Meteorológicos")
        self._emoji_group.setCheckable(True)
        self._emoji_group.setChecked(False)
        self._emoji_group.setStyleSheet("""
            QGroupBox { font-size: 11px; font-weight: bold; color: #F39C12;
                        border: 1px solid #5D6D7E; border-radius: 5px;
                        margin-top: 6px; padding-top: 4px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; }
            QGroupBox::indicator:checked { background: #F39C12; border-radius: 3px; }
        """)
        self._emoji_group.toggled.connect(self._on_emoji_group_toggled)

        eg_layout = QVBoxLayout(self._emoji_group)
        eg_layout.setSpacing(4)
        eg_layout.setContentsMargins(4, 8, 4, 6)

        # Grid de emojis (6 colunas)
        emoji_grid = QGridLayout()
        emoji_grid.setSpacing(3)
        self._emoji_btn_group = QButtonGroup(self)
        self._emoji_btn_group.setExclusive(True)
        self._current_emoji_char = WEATHER_EMOJIS[0][0]

        for idx, (char, tip) in enumerate(WEATHER_EMOJIS):
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setFixedSize(40, 40)
            btn.setToolTip(f"{char}  {tip}")
            # Render the emoji as a full-colour QIcon via Qt's native font stack
            pix = _make_emoji_pixmap(char, 28)
            btn.setIcon(QIcon(pix))
            btn.setIconSize(QSize(28, 28))
            btn.setStyleSheet("""
                QPushButton {
                    border: 1px solid #5D6D7E;
                    border-radius: 5px; background-color: #2C3E50;
                }
                QPushButton:hover { background-color: #34495E; }
                QPushButton:checked {
                    border: 2px solid #F39C12;
                    background-color: #3D2B00;
                }
            """)
            btn.clicked.connect(lambda _, c=char: self._on_emoji_btn_clicked(c))
            self._emoji_btn_group.addButton(btn, idx)
            emoji_grid.addWidget(btn, idx // 6, idx % 6)

        # Seleciona o primeiro por padrão
        self._emoji_btn_group.button(0).setChecked(True)
        eg_layout.addLayout(emoji_grid)

        # Seletor de tamanho
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Tamanho:"))
        self._emoji_size_map = {"P": 20, "M": 28, "G": 40}
        self._emoji_size_btns = QButtonGroup(self)
        self._emoji_size_btns.setExclusive(True)
        for label, size in self._emoji_size_map.items():
            sb = QPushButton(label)
            sb.setCheckable(True)
            sb.setFixedSize(32, 26)
            sb.setStyleSheet("""
                QPushButton { font-size: 10px; font-weight: bold;
                              border: 1px solid #5D6D7E; border-radius: 4px; }
                QPushButton:checked { background: #F39C12; color: black; }
            """)
            sb.clicked.connect(lambda _, s=size: self.emoji_size_changed.emit(s))
            self._emoji_size_btns.addButton(sb)
            size_row.addWidget(sb)
        self._emoji_size_btns.buttons()[1].setChecked(True)  # M = padrão
        size_row.addStretch()
        eg_layout.addLayout(size_row)

        layout.addWidget(self._emoji_group)

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
        # Desativa emoji mode ao selecionar simbologia
        self.deactivate_emoji_mode()

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

    def _on_emoji_group_toggled(self, checked: bool) -> None:
        """Ativa/desativa modo emoji; desativa simbologias ao ativar."""
        if checked:
            # Desativa todos os botões de simbologia
            for btn in self.buttons.values():
                btn.setChecked(False)
        self.emoji_mode_toggled.emit(checked)
        if checked:
            self.emoji_selected.emit(self._current_emoji_char)

    def _on_emoji_btn_clicked(self, char: str) -> None:
        self._current_emoji_char = char
        self.emoji_selected.emit(char)
        # Garante que o grupo está ativo
        if not self._emoji_group.isChecked():
            self._emoji_group.setChecked(True)

    def deactivate_emoji_mode(self) -> None:
        """Desativa o grupo de emojis (chamado quando outra simbologia é escolhida)."""
        self._emoji_group.blockSignals(True)
        self._emoji_group.setChecked(False)
        self._emoji_group.blockSignals(False)
