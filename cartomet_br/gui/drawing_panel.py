"""
Painel de simbologias e ferramentas de desenho do CartoMet BR.

Contém SymbolButton (botão de seleção de símbolo) e SymbologyPanel
(painel lateral esquerdo com grid de símbolos e controles).
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPointF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cartomet_br.gui._constants import APP_AUTHOR, APP_VERSION, get_logo_path
from cartomet_br.symbols import MODOS

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

# (emoji, tooltip)  —  6 colunas × 6 linhas = 36 símbolos
WEATHER_EMOJIS: list[tuple[str, str]] = [
    # ── Céu e nuvens ──────────────────────────────────────────────────────
    ("☀", "Sol"),
    ("🌤", "Sol com nuvem"),
    ("⛅", "Parcialmente nublado"),
    ("🌥", "Bastante nublado"),
    ("☁", "Nublado"),
    ("🌫", "Névoa / Nevoeiro"),
    # ── Precipitação ──────────────────────────────────────────────────────
    ("🌦", "Chuva e sol"),
    ("🌧", "Chuva"),
    ("⛈", "Trovoada"),
    ("🌩", "Relâmpago"),
    ("🌨", "Neve"),
    ("💧", "Precipitação / Gota"),
    # ── Fenômenos extremos ────────────────────────────────────────────────
    ("⚡", "Atividade elétrica"),
    ("🌪", "Tornado / Ciclone"),
    ("🌀", "Ciclone tropical"),
    ("🌊", "Maré / Onda alta"),
    ("🔥", "Fogo / Queimada"),
    ("🌋", "Vulcão / Cinzas"),
    # ── Frio e inverno ────────────────────────────────────────────────────
    ("❄", "Geada / Frio"),
    ("⛄", "Neve / Inverno"),
    ("🏔", "Neve em altitude"),
    ("🥶", "Frio extremo"),
    # ── Calor e seca ──────────────────────────────────────────────────────
    ("🌵", "Cacto / Seca"),
    ("🏜", "Deserto / Aridez"),
    ("🥵", "Calor extremo"),
    ("🌞", "Sol forte / Calor"),
    # ── Estações do ano ───────────────────────────────────────────────────
    ("🌸", "Primavera / Floração"),
    ("🌻", "Verão"),
    ("🍂", "Outono"),
    ("🌿", "Vegetação / Úmido"),
    # ── Vento e outros ────────────────────────────────────────────────────
    ("💨", "Vento"),
    ("🌬", "Vento forte"),
    ("🌡", "Temperatura"),
    ("🌈", "Arco-íris"),
    ("🌾", "Lavoura / Safra"),
    ("🌙", "Noite / Lua"),
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
    intensity_changed = pyqtSignal(int)  # intensidade ZCIT (1/2/3)
    finalize_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    emoji_mode_toggled = pyqtSignal(bool)  # ativou / desativou modo emoji
    emoji_selected = pyqtSignal(str)  # emoji escolhido
    emoji_size_changed = pyqtSignal(int)  # tamanho em pontos (20/28/40)
    emoji_undo_requested = pyqtSignal()  # desfazer o último emoji colocado
    pen_mode_toggled = pyqtSignal(bool)  # ativou / desativou a caneta (traço livre)
    pen_style_changed = pyqtSignal(dict)  # estilo da caneta (cor/espessura/opacidade)
    pen_undo_requested = pyqtSignal()  # desfazer o último traço da caneta
    shape_mode_toggled = pyqtSignal(bool)  # ativou / desativou o modo formas
    shape_tool_changed = pyqtSignal(str)  # rect|ellipse|arrow|line|polygon
    shape_style_changed = pyqtSignal(dict)  # estilo das formas (borda/fill/etc.)
    shape_undo_requested = pyqtSignal()  # desfazer a última forma colocada

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
        self.status_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #1a6faf; padding: 4px;"
        )
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

        # Intensidade (visível só para símbolos que a suportam, ex.: ZCIT)
        self.intensity_row = QWidget()
        intensity_layout = QHBoxLayout(self.intensity_row)
        intensity_layout.setContentsMargins(0, 0, 0, 0)
        intensity_layout.addWidget(QLabel("Intensidade:"))
        self.intensity_combo = QComboBox()
        # (texto, valor) — o índice 0/1/2 mapeia para 1/2/3
        self.intensity_combo.addItem("Fraca  (/)", 1)
        self.intensity_combo.addItem("Moderada  (//)", 2)
        self.intensity_combo.addItem("Forte  (///)", 3)
        self.intensity_combo.currentIndexChanged.connect(self._on_intensity_changed)
        intensity_layout.addWidget(self.intensity_combo)
        intensity_layout.addStretch()
        self.intensity_row.setVisible(False)  # só aparece quando aplicável
        layout.addWidget(self.intensity_row)

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
            ebtn = QPushButton()
            ebtn.setCheckable(True)
            ebtn.setFixedSize(40, 40)
            ebtn.setToolTip(f"{char}  {tip}")
            # Render the emoji as a full-colour QIcon via Qt's native font stack
            pix = _make_emoji_pixmap(char, 28)
            ebtn.setIcon(QIcon(pix))
            ebtn.setIconSize(QSize(28, 28))
            ebtn.setStyleSheet("""
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
            ebtn.clicked.connect(lambda _, c=char: self._on_emoji_btn_clicked(c))
            self._emoji_btn_group.addButton(ebtn, idx)
            emoji_grid.addWidget(ebtn, idx // 6, idx % 6)

        # Seleciona o primeiro por padrão
        first_emoji = self._emoji_btn_group.button(0)
        if first_emoji is not None:
            first_emoji.setChecked(True)
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

        # Botão de desfazer o último emoji colocado (independente do undo de desenhos)
        self._emoji_undo_btn = QPushButton("↩ Desfazer emoji")
        self._emoji_undo_btn.setToolTip("Remove o último emoji colocado na carta")
        self._emoji_undo_btn.setStyleSheet("""
            QPushButton { font-size: 10px; font-weight: bold; padding: 3px;
                          border: 1px solid #5D6D7E; border-radius: 4px; }
            QPushButton:hover { background-color: #34495E; }
        """)
        self._emoji_undo_btn.clicked.connect(self.emoji_undo_requested.emit)
        size_row.addWidget(self._emoji_undo_btn)
        eg_layout.addLayout(size_row)

        layout.addWidget(self._emoji_group)

        # ─── CANETA (traço livre) e FORMAS customizáveis ─────────────────────
        self._build_pen_group(layout)
        self._build_shapes_group(layout)

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
            scaled = pixmap.scaled(
                130,
                130,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
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
        for key in MODOS:
            QShortcut(QKeySequence(key), self).activated.connect(
                lambda k=key: self._select_symbol(k)
            )
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
        # Caneta/Formas: desmarca com sinais VIVOS (o False desliga o modo no canvas)
        if getattr(self, "_pen_group", None) is not None:
            self._pen_group.setChecked(False)
        if getattr(self, "_shapes_group", None) is not None:
            self._shapes_group.setChecked(False)

        modo = MODOS[key]
        nome = modo["nome"]
        if modo.get("ponto", False):
            nome += "  (clique)"
        self.status_label.setText(nome)
        self.status_label.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {modo['cor']}; padding: 4px;"
        )

        # Desabilita flip para símbolos pontuais
        is_point = modo.get("ponto", False)
        self.flip_check.setEnabled(not is_point)
        if is_point:
            self.flip_check.setChecked(False)

        # Mostra o seletor de intensidade só para símbolos que o suportam (ZCIT)
        self.intensity_row.setVisible(modo.get("tem_intensidade", False))

        self.symbol_changed.emit(key)

    def _on_flip_changed(self, state: int) -> None:
        self.flip_changed.emit(state == Qt.CheckState.Checked.value)

    def _on_intensity_changed(self, index: int) -> None:
        value = self.intensity_combo.itemData(index)
        self.intensity_changed.emit(int(value) if value is not None else 1)

    def _toggle_flip(self) -> None:
        self.flip_check.setChecked(not self.flip_check.isChecked())

    def update_points(self, count: int) -> None:
        self.points_label.setText(f"Pontos: {count}")

    def _on_emoji_group_toggled(self, checked: bool) -> None:
        """Ativa/desativa modo emoji; desativa simbologias/caneta/formas ao ativar."""
        if checked:
            # Desativa todos os botões de simbologia
            for btn in self.buttons.values():
                btn.setChecked(False)
            # Caneta/Formas: sinais vivos (o False desliga o modo no canvas)
            if getattr(self, "_pen_group", None) is not None:
                self._pen_group.setChecked(False)
            if getattr(self, "_shapes_group", None) is not None:
                self._shapes_group.setChecked(False)
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

    def reset_emoji_mode(self) -> None:
        """Alias usado pelo MainWindow ao entrar em zoom/pan/sonda (conserta no-op)."""
        self.deactivate_emoji_mode()

    # ═══════════════════════════════════════════════════════════════════════
    #  CANETA (traço livre) e FORMAS customizáveis
    # ═══════════════════════════════════════════════════════════════════════

    _PRESET_COLORS = [
        "#E74C3C",
        "#2980B9",
        "#27AE60",
        "#8E44AD",
        "#F39C12",
        "#16A085",
        "#000000",
        "#FFFFFF",
    ]

    _GROUP_QSS = """
        QGroupBox {{ font-size: 11px; font-weight: bold; color: {cor};
                    border: 1px solid #5D6D7E; border-radius: 5px;
                    margin-top: 6px; padding-top: 4px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 8px; }}
        QGroupBox::indicator:checked {{ background: {cor}; border-radius: 3px; }}
    """

    def _make_color_row(self, initial: str, on_color) -> QHBoxLayout:
        """Linha de swatches de cor (8 presets exclusivos + '⋯' = QColorDialog)."""
        row = QHBoxLayout()
        row.setSpacing(3)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for c in self._PRESET_COLORS:
            b = QPushButton()
            b.setCheckable(True)
            b.setFixedSize(22, 22)
            b.setToolTip(c)
            b.setStyleSheet(f"""
                QPushButton {{ background-color: {c}; border: 1px solid #5D6D7E;
                              border-radius: 3px; }}
                QPushButton:checked {{ border: 2px solid #F1C40F; }}
            """)
            if c.lower() == initial.lower():
                b.setChecked(True)
            b.clicked.connect(lambda _, col=c: on_color(col))
            group.addButton(b)
            row.addWidget(b)

        custom = QPushButton("⋯")
        custom.setCheckable(True)
        custom.setFixedSize(22, 22)
        custom.setToolTip("Cor personalizada…")
        custom.setStyleSheet("""
            QPushButton { border: 1px solid #5D6D7E; border-radius: 3px;
                          font-weight: bold; color: #ECF0F1; }
            QPushButton:checked { border: 2px solid #F1C40F; }
        """)

        def _pick_custom() -> None:
            col = QColorDialog.getColor(QColor(initial), self, "Escolher cor")
            if col.isValid():
                custom.setStyleSheet(f"""
                    QPushButton {{ background-color: {col.name()};
                                  border: 2px solid #F1C40F; border-radius: 3px; }}
                """)
                on_color(col.name())

        custom.clicked.connect(_pick_custom)
        group.addButton(custom)
        row.addWidget(custom)
        row.addStretch()
        return row

    @staticmethod
    def _make_opacity_row(initial_pct: int, on_change) -> QHBoxLayout:
        """Linha 'Opacidade: [slider] NN%' (10–100)."""
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Opacidade:"))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(10, 100)
        slider.setValue(initial_pct)
        pct = QLabel(f"{initial_pct}%")
        pct.setMinimumWidth(34)

        def _changed(v: int) -> None:
            pct.setText(f"{v}%")
            on_change(v / 100.0)

        slider.valueChanged.connect(_changed)
        row.addWidget(slider, stretch=1)
        row.addWidget(pct)
        return row

    def _build_pen_group(self, layout: QVBoxLayout) -> None:
        """Grupo recolhível '✏ Caneta' — traço livre p/ mesa digitalizadora/mouse."""
        self._pen_style_state: dict[str, Any] = {
            "edge_color": "#E74C3C",
            "fill_color": None,
            "linewidth": 2.0,
            "linestyle": "solid",
            "alpha": 1.0,
        }
        g = QGroupBox("✏ Caneta (traço livre)")
        g.setCheckable(True)
        g.setChecked(False)
        g.setStyleSheet(self._GROUP_QSS.format(cor="#1ABC9C"))
        g.toggled.connect(self._on_pen_group_toggled)
        v = QVBoxLayout(g)
        v.setSpacing(4)
        v.setContentsMargins(4, 8, 4, 6)

        cor_row = QHBoxLayout()
        cor_row.addWidget(QLabel("Cor:"))
        cor_row.addLayout(
            self._make_color_row(
                self._pen_style_state["edge_color"], lambda c: self._set_pen_style("edge_color", c)
            )
        )
        v.addLayout(cor_row)

        w_row = QHBoxLayout()
        w_row.setSpacing(6)
        w_row.addWidget(QLabel("Espessura:"))
        spin = QSpinBox()
        spin.setRange(1, 10)
        spin.setValue(2)
        spin.setSuffix(" pt")
        spin.valueChanged.connect(lambda v_: self._set_pen_style("linewidth", float(v_)))
        w_row.addWidget(spin)
        w_row.addStretch()
        v.addLayout(w_row)

        v.addLayout(self._make_opacity_row(100, lambda a: self._set_pen_style("alpha", a)))

        v.addWidget(
            self._make_undo_button(
                "↩ Desfazer traço", "Remove o último traço da caneta", self.pen_undo_requested
            )
        )

        layout.addWidget(g)
        self._pen_group = g

    def _build_shapes_group(self, layout: QVBoxLayout) -> None:
        """Grupo recolhível '⬜ Formas' — rect/elipse/seta/linha/polígono."""
        self._shape_style_state: dict[str, Any] = {
            "edge_color": "#E74C3C",
            "fill_color": None,
            "linewidth": 2.0,
            "linestyle": "solid",
            "alpha": 1.0,
        }
        self._current_shape_tool = "rect"
        self._shape_fill_color = "#F39C12"  # cor do fill quando habilitado

        g = QGroupBox("⬜ Formas")
        g.setCheckable(True)
        g.setChecked(False)
        g.setStyleSheet(self._GROUP_QSS.format(cor="#9B59B6"))
        g.toggled.connect(self._on_shapes_group_toggled)
        v = QVBoxLayout(g)
        v.setSpacing(4)
        v.setContentsMargins(4, 8, 4, 6)

        # Ferramentas (botões exclusivos)
        tools_row = QHBoxLayout()
        tools_row.setSpacing(3)
        self._shape_tool_btns = QButtonGroup(self)
        self._shape_tool_btns.setExclusive(True)
        tools = [
            ("rect", "Retângulo (arraste)"),
            ("ellipse", "Elipse / Círculo (arraste)"),
            ("arrow", "Seta (arraste do início à ponta)"),
            ("line", "Linha reta (arraste)"),
            ("polygon", "Polígono livre (clique nos vértices + Enter)"),
        ]
        for key, tip in tools:
            b = QPushButton()
            b.setCheckable(True)
            b.setFixedSize(40, 32)
            b.setToolTip(tip)
            # Ícone DESENHADO (QPainter) — glifos Unicode variam com a fonte do SO
            # e ficavam ambíguos (elipse virava arco, polígono virava caixa).
            b.setIcon(self._make_shape_icon(key))
            b.setIconSize(QSize(28, 22))
            b.setStyleSheet("""
                QPushButton { border: 1px solid #5D6D7E;
                              border-radius: 5px; background-color: #2C3E50; }
                QPushButton:hover { background-color: #34495E; }
                QPushButton:checked { border: 2px solid #9B59B6;
                                      background-color: #2E2240; }
            """)
            b.clicked.connect(lambda _, k=key: self._on_shape_tool_clicked(k))
            self._shape_tool_btns.addButton(b)
            tools_row.addWidget(b)
        self._shape_tool_btns.buttons()[0].setChecked(True)
        tools_row.addStretch()
        v.addLayout(tools_row)

        # Cor da borda
        borda_row = QHBoxLayout()
        borda_row.addWidget(QLabel("Borda:"))
        borda_row.addLayout(
            self._make_color_row(
                self._shape_style_state["edge_color"],
                lambda c: self._set_shape_style("edge_color", c),
            )
        )
        v.addLayout(borda_row)

        # Preenchimento opcional
        fill_row = QHBoxLayout()
        fill_row.setSpacing(6)
        self._fill_check = QCheckBox("Preencher")
        self._fill_check.stateChanged.connect(self._on_fill_toggled)
        fill_row.addWidget(self._fill_check)
        self._fill_btn = QPushButton()
        self._fill_btn.setFixedSize(22, 22)
        self._fill_btn.setToolTip("Cor do preenchimento…")
        self._fill_btn.setEnabled(False)
        self._update_fill_btn_color()
        self._fill_btn.clicked.connect(self._pick_fill_color)
        fill_row.addWidget(self._fill_btn)
        fill_row.addStretch()
        v.addLayout(fill_row)

        # Espessura + estilo de linha
        ws_row = QHBoxLayout()
        ws_row.setSpacing(6)
        ws_row.addWidget(QLabel("Espessura:"))
        spin = QSpinBox()
        spin.setRange(1, 10)
        spin.setValue(2)
        spin.setSuffix(" pt")
        spin.valueChanged.connect(lambda v_: self._set_shape_style("linewidth", float(v_)))
        ws_row.addWidget(spin)
        ws_row.addWidget(QLabel("Estilo:"))
        combo = QComboBox()
        combo.addItem("Sólida", "solid")
        combo.addItem("Tracejada", "dashed")
        combo.addItem("Pontilhada", "dotted")
        combo.currentIndexChanged.connect(
            lambda i: self._set_shape_style("linestyle", combo.itemData(i))
        )
        ws_row.addWidget(combo)
        ws_row.addStretch()
        v.addLayout(ws_row)

        v.addLayout(self._make_opacity_row(100, lambda a: self._set_shape_style("alpha", a)))

        hint = QLabel("ⓘ Polígono: clique nos vértices e pressione Enter")
        hint.setStyleSheet("font-size: 8px; color: #7F8C8D;")
        v.addWidget(hint)

        v.addWidget(
            self._make_undo_button(
                "↩ Desfazer forma",
                "Remove a última forma colocada na carta",
                self.shape_undo_requested,
            )
        )

        layout.addWidget(g)
        self._shapes_group = g

    @staticmethod
    def _make_shape_icon(tool: str, w: int = 28, h: int = 22) -> QIcon:
        """Desenha o ícone da ferramenta de forma com QPainter (nítido em qualquer SO).

        Substitui os glifos Unicode (▭ ◯ ➤ ╱ ⬠), que dependem da fonte do sistema
        e renderizavam ambíguos no Windows. Traço claro sobre fundo transparente —
        legível nos estados normal (#2C3E50) e marcado (#2E2240) do botão.
        """
        pix = QPixmap(w, h)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ECF0F1"))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)

        if tool == "rect":
            p.drawRect(4, 4, w - 8, h - 8)
        elif tool == "ellipse":
            p.drawEllipse(4, 3, w - 8, h - 6)
        elif tool == "line":
            p.drawLine(5, h - 5, w - 5, 5)
        elif tool == "arrow":
            # Haste diagonal + ponta triangular preenchida
            p.drawLine(4, h - 4, w - 9, 8)
            p.setBrush(QBrush(QColor("#ECF0F1")))
            p.drawPolygon(
                QPolygonF(
                    [
                        QPointF(w - 4, 4),
                        QPointF(w - 8.5, 10.5),
                        QPointF(w - 12, 5.5),
                    ]
                )
            )
        elif tool == "polygon":
            # Pentágono irregular — sugere o contorno livre por vértices
            p.drawPolygon(
                QPolygonF(
                    [
                        QPointF(w / 2.0, 3),
                        QPointF(w - 4, 9),
                        QPointF(w - 7, h - 3),
                        QPointF(7, h - 3),
                        QPointF(4, 8),
                    ]
                )
            )
        p.end()
        return QIcon(pix)

    @staticmethod
    def _make_undo_button(text: str, tooltip: str, signal) -> QPushButton:
        """Botão 'desfazer último X' no estilo do '↩ Desfazer emoji'."""
        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setStyleSheet("""
            QPushButton { font-size: 10px; font-weight: bold; padding: 3px;
                          border: 1px solid #5D6D7E; border-radius: 4px; }
            QPushButton:hover { background-color: #34495E; }
        """)
        btn.clicked.connect(signal.emit)
        return btn

    # ─── Estado/sinais de estilo ─────────────────────────────────────────────

    def _set_pen_style(self, key: str, value) -> None:
        self._pen_style_state[key] = value
        self.pen_style_changed.emit(dict(self._pen_style_state))

    def _set_shape_style(self, key: str, value) -> None:
        self._shape_style_state[key] = value
        self.shape_style_changed.emit(dict(self._shape_style_state))

    def _update_fill_btn_color(self) -> None:
        self._fill_btn.setStyleSheet(f"""
            QPushButton {{ background-color: {self._shape_fill_color};
                          border: 1px solid #5D6D7E; border-radius: 3px; }}
            QPushButton:disabled {{ background-color: #34495E; }}
        """)

    def _on_fill_toggled(self, state: int) -> None:
        enabled = state == Qt.CheckState.Checked.value
        self._fill_btn.setEnabled(enabled)
        self._set_shape_style("fill_color", self._shape_fill_color if enabled else None)

    def _pick_fill_color(self) -> None:
        col = QColorDialog.getColor(QColor(self._shape_fill_color), self, "Cor do preenchimento")
        if col.isValid():
            self._shape_fill_color = col.name()
            self._update_fill_btn_color()
            if self._fill_check.isChecked():
                self._set_shape_style("fill_color", self._shape_fill_color)

    def _on_shape_tool_clicked(self, key: str) -> None:
        self._current_shape_tool = key
        self.shape_tool_changed.emit(key)
        # Garante que o grupo está ativo (padrão dos botões de emoji)
        if not self._shapes_group.isChecked():
            self._shapes_group.setChecked(True)

    # ─── Toggle dos grupos (exclusividade tripla) ────────────────────────────

    def _on_pen_group_toggled(self, checked: bool) -> None:
        """Ativa/desativa a caneta; desativa simbologias/emoji/formas ao ativar."""
        if checked:
            for btn in self.buttons.values():
                btn.setChecked(False)
            # Sinais VIVOS: o False propaga e desliga o modo no canvas
            self._emoji_group.setChecked(False)
            if getattr(self, "_shapes_group", None) is not None:
                self._shapes_group.setChecked(False)
        self.pen_mode_toggled.emit(checked)
        if checked:
            self.pen_style_changed.emit(dict(self._pen_style_state))

    def _on_shapes_group_toggled(self, checked: bool) -> None:
        """Ativa/desativa as formas; desativa simbologias/emoji/caneta ao ativar."""
        if checked:
            for btn in self.buttons.values():
                btn.setChecked(False)
            self._emoji_group.setChecked(False)
            if getattr(self, "_pen_group", None) is not None:
                self._pen_group.setChecked(False)
        self.shape_mode_toggled.emit(checked)
        if checked:
            self.shape_tool_changed.emit(self._current_shape_tool)
            self.shape_style_changed.emit(dict(self._shape_style_state))

    def reset_pen_mode(self) -> None:
        """Desmarca o grupo da caneta SEM emitir sinais (reset vindo do MainWindow)."""
        self._pen_group.blockSignals(True)
        self._pen_group.setChecked(False)
        self._pen_group.blockSignals(False)

    def reset_shapes_mode(self) -> None:
        """Desmarca o grupo de formas SEM emitir sinais (reset vindo do MainWindow)."""
        self._shapes_group.blockSignals(True)
        self._shapes_group.setChecked(False)
        self._shapes_group.blockSignals(False)
