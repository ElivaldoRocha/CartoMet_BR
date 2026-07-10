"""Linha reutilizável de seleção de cor (swatches de preset + cor personalizada).

Extraído de ``DrawingPanel`` para reuso entre o painel de desenho e os controles
de estilo dos campos de vento (``WindStyleControls``). É uma função pura de UI: dada
uma cor inicial e um callback ``on_color``, devolve um ``QHBoxLayout`` pronto para
inserir em qualquer layout.
"""

from collections.abc import Callable

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

# Paleta padrão de presets (8 cores exclusivas). Mantida em sincronia com o valor
# histórico usado por DrawingPanel._PRESET_COLORS.
PRESET_COLORS = [
    "#E74C3C",
    "#2980B9",
    "#27AE60",
    "#8E44AD",
    "#F39C12",
    "#16A085",
    "#000000",
    "#FFFFFF",
]


def make_color_row(
    parent: QWidget,
    initial: str,
    on_color: Callable[[str], None],
    presets: list[str] | None = None,
) -> QHBoxLayout:
    """Linha de swatches de cor (presets exclusivos + '⋯' = ``QColorDialog``).

    ``parent`` é o widget dono (usado como parent do ``QButtonGroup`` e do diálogo
    de cor). ``on_color`` recebe a cor escolhida (str hex, ex.: ``"#E74C3C"``) a
    cada seleção.
    """
    colors = presets if presets is not None else PRESET_COLORS
    row = QHBoxLayout()
    row.setSpacing(3)
    group = QButtonGroup(parent)
    group.setExclusive(True)
    for c in colors:
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
        col = QColorDialog.getColor(QColor(initial), parent, "Escolher cor")
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
