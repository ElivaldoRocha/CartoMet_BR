"""Estilo de campos de vento — controles de cor e densidade (UI).

- **Cor** aplica-se às três representações (barbelas, vetores, correntes).
- **Densidade** aplica-se apenas a barbelas e vetores; as **correntes**
  (streamplot) ficam intocadas — o controle de densidade é escondido nesse modo.

Os valores de densidade são chaves nomeadas (``baixa``/``media``/``alta``); o
mapeamento para o *stride* de amostragem vive no motor de render
(``WIND_DENSITY_SKIP`` em ``map_canvas.py``), mantendo a UI desacoplada do render.
"""

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cartomet_br.gui.color_row import make_color_row
from cartomet_br.gui.themes import DARK_STYLE

# Opções de densidade expostas ao usuário (chave interna, rótulo visível).
WIND_DENSITY_OPTIONS = [
    ("baixa", "Baixa"),
    ("media", "Média"),
    ("alta", "Alta"),
]

# Defaults que reproduzem o comportamento histórico (cor cinza, stride 8 == "media").
DEFAULT_WIND_COLOR = "gray"
DEFAULT_WIND_DENSITY = "media"


class WindStyleControls(QWidget):
    """Cor (3 representações) + densidade (só barbelas/vetores) de um campo de vento.

    Reutilizado tanto no painel de adição (``FieldLayerPanel``) quanto no diálogo
    de edição de um campo já plotado (``WindStyleDialog``).
    """

    def __init__(
        self,
        color: str = DEFAULT_WIND_COLOR,
        density: str = DEFAULT_WIND_DENSITY,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._color = color

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        # ─── Cor (todas as representações) ───
        cor_row = QHBoxLayout()
        cor_row.setSpacing(4)
        cor_row.addWidget(QLabel("Cor:"))
        cor_row.addLayout(make_color_row(self, color, self._on_color))
        layout.addLayout(cor_row)

        # ─── Densidade (só barbelas/vetores) ───
        self.density_row = QWidget()
        d_layout = QHBoxLayout(self.density_row)
        d_layout.setContentsMargins(0, 0, 0, 0)
        d_layout.setSpacing(4)
        d_layout.addWidget(QLabel("Densidade:"))
        self.density_combo = QComboBox()
        for key, label in WIND_DENSITY_OPTIONS:
            self.density_combo.addItem(label, key)
        self._select_density(density)
        d_layout.addWidget(self.density_combo)
        d_layout.addStretch()
        layout.addWidget(self.density_row)

    def _on_color(self, col: str) -> None:
        self._color = col

    def _select_density(self, density: str) -> None:
        idx = self.density_combo.findData(density)
        if idx >= 0:
            self.density_combo.setCurrentIndex(idx)

    def set_wind_type(self, wind_type: str) -> None:
        """Correntes (stream) = só cor; esconde o controle de densidade."""
        self.density_row.setVisible(wind_type != "stream")

    def get_style(self) -> tuple[str, str]:
        """Retorna (cor, densidade) selecionadas."""
        density = self.density_combo.currentData() or DEFAULT_WIND_DENSITY
        return self._color, density

    def set_style(self, color: str, density: str) -> None:
        self._color = color
        self._select_density(density)


class WindStyleDialog(QDialog):
    """Edita cor/densidade de um campo de vento JÁ plotado (aplica sem re-baixar).

    Reutiliza ``WindStyleControls``, pré-preenchido com o estilo atual do campo.
    ``get_style()`` devolve (cor, densidade) após ``exec()`` retornar ``Accepted``.
    """

    def __init__(
        self,
        wind_type: str,
        color: str = DEFAULT_WIND_COLOR,
        density: str = DEFAULT_WIND_DENSITY,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Estilo do campo de vento")
        self.setModal(True)
        self.setStyleSheet(DARK_STYLE)
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.addWidget(
            QLabel("<h3 style='color:#9B59B6; margin:0;'>Estilo do campo de vento</h3>")
        )

        self.controls = WindStyleControls(color=color, density=density)
        self.controls.set_wind_type(wind_type)  # Correntes = só cor
        layout.addWidget(self.controls)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setStyleSheet("background-color:#7F8C8D;")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("✓ Aplicar")
        ok_btn.setStyleSheet("background-color:#27AE60; min-width:110px;")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def get_style(self) -> tuple[str, str]:
        return self.controls.get_style()
