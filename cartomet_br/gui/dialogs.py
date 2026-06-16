"""
Diálogos de inicialização do CartoMet BR.

Contém WelcomeDialog (boas-vindas) e FirstRunDialog (configuração inicial).
"""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from cartomet_br.gui._constants import (
    APP_DESCRIPTION,
    APP_NAME,
    APP_VERSION,
    get_institutional_logos_path,
    get_logo_path,
)
from cartomet_br.gui.themes import DARK_STYLE

# ═══════════════════════════════════════════════════════════════════════════════
#  JANELA DE BOAS-VINDAS
# ═══════════════════════════════════════════════════════════════════════════════

class WelcomeDialog(QDialog):
    """Janela de boas-vindas exibida ao iniciar o programa."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Bem-vindo ao {APP_NAME}")
        self.setFixedSize(620, 780)
        self.setModal(True)
        self.setStyleSheet(DARK_STYLE)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(20, 16, 20, 16)

        # ─── Logo do CartoMet BR ───
        logo_path = get_logo_path()
        if logo_path and logo_path.exists():
            logo_label = QLabel()
            pixmap = QPixmap(str(logo_path))
            logo_label.setPixmap(pixmap.scaled(
                100, 100,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
            logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(logo_label)

        # Título
        title = QLabel(f"<h1 style='color: #3498DB; margin: 0;'>{APP_NAME}</h1>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(
            f"<p style='color: #BDC3C7; font-size: 13px;'>"
            f"{APP_DESCRIPTION}</p>"
            f"<p style='color: #F39C12; font-size: 12px; font-weight: bold;'>"
            f"Versão {APP_VERSION}</p>"
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        # Separador
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet("background-color: #5D6D7E;")
        layout.addWidget(sep1)

        # ─── Idealizadores e Desenvolvedor ───
        credits_html = """
        <div style='text-align: center; color: #BDC3C7; line-height: 1.4;'>

            <p style='color: #1ABC9C; font-weight: bold; font-size: 13px;
               margin-bottom: 4px;'>Desenvolvedor &amp; Idealizador</p>

            <p style='font-size: 12px; font-weight: bold; margin: 2px 0;'>
                Elivaldo C. Rocha</p>
            <p style='font-size: 9px; color: #AAA; margin: 0; line-height: 1.5;'>
                Bacharel em Meteorologia — FAMET/UFPA<br/>
                Mestre em Gestão de Riscos e Desastres na Amazônia — PPGGRD/UFPA<br/>
                MBA em Geotecnologias e Análise de Dados Espaciais<br/>
                Esp. Georreferenciamento, Geoprocessamento e Sensoriamento Remoto<br/>
                Esp. Ciência de Dados Geográficos<br/>
                Esp. Agrometeorologia e Climatologia<br/>
                Analista e Desenvolvedor de Sistemas
            </p>

            <br/>
            <p style='color: #1ABC9C; font-weight: bold; font-size: 13px;
               margin-bottom: 4px;'>Idealizador</p>

            <p style='font-size: 12px; font-weight: bold; margin: 2px 0;'>
                Prof. Dr. Everaldo Barreiros de Souza</p>
            <p style='font-size: 9px; color: #AAA; margin: 0; line-height: 1.5;'>
                Professor Titular — Instituto de Geociências (IG/UFPA)<br/>
                Doutor em Meteorologia — USP/IAG<br/>
                Mestre em Meteorologia — INPE/CPTEC<br/>
                Docente Permanente do PPGCA (Mestrado e Doutorado)<br/>
                Bolsista PQ-2 Produtividade em Pesquisa — CNPq<br/>
                Líder do Grupo Modelagem Climática Aplicada<br/>
                às Ciências Ambientais da Amazônia<br/>
                +135 artigos científicos publicados
            </p>

        </div>
        """
        credits_label = QLabel(credits_html)
        credits_label.setWordWrap(True)
        layout.addWidget(credits_label)

        # Separador
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("background-color: #5D6D7E;")
        layout.addWidget(sep2)

        # ─── Logos institucionais ───
        inst_path = get_institutional_logos_path()
        if inst_path and inst_path.exists():
            inst_label = QLabel()
            pixmap_inst = QPixmap(str(inst_path))
            inst_label.setPixmap(pixmap_inst.scaled(
                500, 180,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
            inst_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(inst_label)
        else:
            inst_text = QLabel(
                "<div style='text-align: center; color: #BDC3C7; font-size: 11px;'>"
                "<b>UFPA</b> — Universidade Federal do Pará<br/>"
                "<b>IG</b> — Instituto de Geociências<br/>"
                "<b>FAMET</b> — Faculdade de Meteorologia<br/>"
                "<b>PPGGRD</b> — Programa de Pós-Graduação em "
                "Gestão de Riscos e Desastres na Amazônia"
                "</div>"
            )
            inst_text.setWordWrap(True)
            layout.addWidget(inst_text)

        # ─── Dados e licença ───
        footer = QLabel(
            "<div style='text-align: center; color: #7F8C8D; font-size: 9px;'>"
            "<p>Dados meteorológicos: ECMWF Open Data (CC BY 4.0)<br/>"
            "Imagem de satélite: NOAA GOES-East (Domínio Público)<br/>"
            "Licença do software: MIT</p>"
            "</div>"
        )
        footer.setWordWrap(True)
        layout.addWidget(footer)

        # ─── Botão Iniciar ───
        start_btn = QPushButton("🚀 Iniciar CartoMet BR")
        start_btn.setStyleSheet("""
            QPushButton {
                background-color: #27AE60; padding: 12px;
                font-size: 14px; font-weight: bold; border-radius: 8px;
                min-height: 20px;
            }
            QPushButton:hover { background-color: #2ECC71; }
        """)
        start_btn.clicked.connect(self.accept)
        layout.addWidget(start_btn)


# ═══════════════════════════════════════════════════════════════════════════════
#  DIÁLOGO DE CONFIGURAÇÃO INICIAL
# ═══════════════════════════════════════════════════════════════════════════════

class FirstRunDialog(QDialog):
    """Diálogo para configurar diretório de dados na primeira execução."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Configuração Inicial")
        self.setMinimumWidth(550)
        self.setModal(True)
        self.data_dir = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # Header com logo
        header = QHBoxLayout()

        logo_path = get_logo_path()
        if logo_path and logo_path.exists():
            logo_label = QLabel()
            pixmap = QPixmap(str(logo_path))
            logo_label.setPixmap(pixmap.scaled(
                100, 100,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
            header.addWidget(logo_label)

        title_layout = QVBoxLayout()
        title = QLabel(f"<h2 style='color: #3498DB; margin: 0;'>Bem-vindo ao {APP_NAME}!</h2>")
        title_layout.addWidget(title)
        subtitle = QLabel(f"<p style='color: #BDC3C7;'>{APP_DESCRIPTION}</p>")
        title_layout.addWidget(subtitle)
        header.addLayout(title_layout)
        header.addStretch()

        layout.addLayout(header)

        # Separador
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #5D6D7E;")
        layout.addWidget(line)

        # Explicação
        explanation = QLabel(
            "<p style='font-size: 12px;'>"
            "Para funcionar corretamente, o <b>CartoMet BR</b> precisa de um "
            "diretório para salvar os dados meteorológicos baixados do ECMWF.</p>"
            "<p style='font-size: 12px; color: #F39C12;'>"
            "<b>⚠ Importante:</b> Escolha um local onde você tenha permissão de escrita, "
            "como a pasta <b>Documentos</b>.</p>"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        # Seleção de diretório
        dir_group = QGroupBox("Diretório de Dados")
        dir_layout = QHBoxLayout(dir_group)

        self.dir_edit = QLineEdit()
        self.dir_edit.setReadOnly(True)
        self.dir_edit.setMinimumHeight(32)

        default_dir = Path.home() / "Documents" / "CartoMet_BR_Data"
        self.dir_edit.setText(str(default_dir))
        self.data_dir = default_dir

        dir_layout.addWidget(self.dir_edit)

        browse_btn = QPushButton("📁 Procurar...")
        browse_btn.setMinimumWidth(120)
        browse_btn.clicked.connect(self._browse_directory)
        dir_layout.addWidget(browse_btn)

        layout.addWidget(dir_group)

        # Info
        info = QLabel(
            "<p style='font-size: 10px; color: #7F8C8D;'>"
            "Uma subpasta 'CartoMet_BR_Data' será criada no local selecionado.</p>"
        )
        layout.addWidget(info)

        layout.addStretch()

        # Botões
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setStyleSheet("background-color: #7F8C8D;")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("✓ Confirmar e Iniciar")
        ok_btn.setStyleSheet("background-color: #27AE60; min-width: 150px;")
        ok_btn.clicked.connect(self._accept)
        btn_layout.addWidget(ok_btn)

        layout.addLayout(btn_layout)

    def _browse_directory(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Selecione o Diretório",
            str(Path.home() / "Documents"),
        )
        if dir_path:
            self.data_dir = Path(dir_path) / "CartoMet_BR_Data"
            self.dir_edit.setText(str(self.data_dir))

    def _accept(self):
        if not self.data_dir:
            QMessageBox.warning(self, "Aviso", "Selecione um diretório.")
            return

        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            (self.data_dir / "output").mkdir(exist_ok=True)

            # Testa escrita
            test_file = self.data_dir / ".write_test"
            test_file.write_text("test")
            test_file.unlink()

            self.accept()
        except PermissionError:
            QMessageBox.critical(
                self, "Erro de Permissão",
                f"Não foi possível criar/escrever no diretório:\n{self.data_dir}\n\n"
                "Escolha outro local (ex: sua pasta Documentos)."
            )
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao criar diretório:\n{e}")
