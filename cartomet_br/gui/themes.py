"""
Temas visuais do CartoMet BR.

Contém o stylesheet QSS para modo escuro e os temas de cores
do mapa base (Cartopy).
"""

# ═══════════════════════════════════════════════════════════════════════════════
#  TEMA ESCURO — Stylesheet QSS para PyQt6
# ═══════════════════════════════════════════════════════════════════════════════

DARK_STYLE = """
QMainWindow, QDialog {
    background-color: #1A252F;
}
QWidget {
    background-color: #2C3E50;
    color: #ECF0F1;
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 11px;
}
QMenuBar {
    background-color: #2C3E50;
    color: #BDC3C7;
    border-bottom: 1px solid #1A252F;
}
QMenuBar::item:selected {
    background-color: #34495E;
}
QMenu {
    background-color: #2C3E50;
    border: 1px solid #1A252F;
}
QMenu::item:selected {
    background-color: #3498DB;
}
QToolBar {
    background-color: #34495E;
    border: none;
    spacing: 5px;
    padding: 5px;
}
QPushButton {
    background-color: #3498DB;
    border: none;
    border-radius: 4px;
    padding: 8px 16px;
    color: white;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #2980B9;
}
QPushButton:pressed {
    background-color: #1F618D;
}
QPushButton:disabled {
    background-color: #5D6D7E;
    color: #95A5A6;
}
QPushButton:checked {
    background-color: #E74C3C;
}
QGroupBox {
    border: 1px solid #5D6D7E;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 8px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: #3498DB;
}
QComboBox {
    background-color: #34495E;
    border: 1px solid #5D6D7E;
    border-radius: 4px;
    padding: 5px 8px;
    min-width: 100px;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #34495E;
    selection-background-color: #3498DB;
}
QSpinBox, QDoubleSpinBox {
    background-color: #34495E;
    border: 1px solid #5D6D7E;
    border-radius: 4px;
    padding: 4px;
    padding-right: 20px;   /* espaço p/ os botões de incremento */
    min-width: 55px;
}
/* Ao estilizar a caixa, é OBRIGATÓRIO estilizar os sub-controles, senão o Qt
   renderiza os botões incorretamente (a seta para cima fica inclicável). */
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 18px;
    border-left: 1px solid #5D6D7E;
    border-top-right-radius: 4px;
    background-color: #3D566E;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 18px;
    border-left: 1px solid #5D6D7E;
    border-bottom-right-radius: 4px;
    background-color: #3D566E;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #4A6582;
}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {
    background-color: #3498DB;
}
/* Setas desenhadas via triângulos CSS (sem depender de imagens/assets) */
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 6px solid #ECF0F1;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid #ECF0F1;
}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {
    border-bottom-color: #5D6D7E;
}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {
    border-top-color: #5D6D7E;
}
QSlider::groove:horizontal {
    border: 1px solid #5D6D7E;
    height: 6px;
    background: #34495E;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #3498DB;
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
}
QSlider::sub-page:horizontal {
    background: #3498DB;
    border-radius: 3px;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 3px;
    border: 1px solid #5D6D7E;
    background-color: #34495E;
}
QCheckBox::indicator:checked {
    background-color: #27AE60;
    border-color: #27AE60;
}
QStatusBar {
    background-color: #1A252F;
    color: #BDC3C7;
    border-top: 1px solid #34495E;
}
QDockWidget::title {
    background-color: #1A252F;
    padding: 8px;
    font-weight: bold;
}
QProgressBar {
    border: 1px solid #5D6D7E;
    border-radius: 4px;
    text-align: center;
    background-color: #34495E;
}
QProgressBar::chunk {
    background-color: #27AE60;
    border-radius: 3px;
}
QLineEdit {
    background-color: #34495E;
    border: 1px solid #5D6D7E;
    border-radius: 4px;
    padding: 6px;
    color: #ECF0F1;
}
QLabel {
    background-color: transparent;
}
QScrollArea {
    border: none;
}
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  TEMAS DE CORES DO MAPA BASE (Cartopy)
# ═══════════════════════════════════════════════════════════════════════════════

# Além das cores do mapa base, cada tema traz o par do modo "Destacar
# contornos": `emphasis_line` (cor forte das linhas realçadas) e
# `emphasis_halo` (contorno de contraste desenhado POR BAIXO delas — técnica
# de desenho duplo, ver MapCanvas.set_context_emphasis). O halo garante
# leitura sobre satélite e campos preenchidos em qualquer tonalidade.
MAP_THEMES = {
    "Clássico": {
        "land": "#F5F5DC",
        "ocean": "#D6EAF8",
        "lakes": "#D6EAF8",
        "coastline": "#2C3E50",
        "borders": "#7F8C8D",
        "states": "#BDC3C7",
        "emphasis_line": "#1A1A1A",
        "emphasis_halo": "#FFFFFF",
    },
    "Branco": {
        "land": "#FFFFFF",
        "ocean": "#FFFFFF",
        "lakes": "#E8F0FE",
        "coastline": "#333333",
        "borders": "#666666",
        "states": "#AAAAAA",
        "emphasis_line": "#1A1A1A",
        "emphasis_halo": "#FFFFFF",
    },
    "Pastel": {
        "land": "#F0E6D3",
        "ocean": "#E3F2FD",
        "lakes": "#E3F2FD",
        "coastline": "#5D4037",
        "borders": "#8D6E63",
        "states": "#BCAAA4",
        "emphasis_line": "#3E2723",
        "emphasis_halo": "#FFFFFF",
    },
    "Tons de cinza": {
        "land": "#E0E0E0",
        "ocean": "#F5F5F5",
        "lakes": "#F5F5F5",
        "coastline": "#424242",
        "borders": "#757575",
        "states": "#BDBDBD",
        "emphasis_line": "#212121",
        "emphasis_halo": "#FFFFFF",
    },
    "Terra": {
        "land": "#E8DCC8",
        "ocean": "#C5D8E8",
        "lakes": "#C5D8E8",
        "coastline": "#3E2723",
        "borders": "#5D4037",
        "states": "#A1887F",
        "emphasis_line": "#2B1B14",
        "emphasis_halo": "#FFFFFF",
    },
    "Escuro": {
        "land": "#2C3E50",
        "ocean": "#1A252F",
        "lakes": "#1A252F",
        "coastline": "#ECF0F1",
        "borders": "#7F8C8D",
        "states": "#5D6D7E",
        "emphasis_line": "#F5F6FA",
        "emphasis_halo": "#000000",
    },
}
