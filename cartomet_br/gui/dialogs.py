"""
Diálogos de inicialização e utilitários do CartoMet BR.

Contém WelcomeDialog (boas-vindas), FirstRunDialog (configuração inicial) e o
StationReportDialog (relatório da estação METAR/SYNOP clicada no mapa).
"""

import math
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
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
            logo_label.setPixmap(
                pixmap.scaled(
                    100,
                    100,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
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
            inst_label.setPixmap(
                pixmap_inst.scaled(
                    500,
                    180,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
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
            logo_label.setPixmap(
                pixmap.scaled(
                    100,
                    100,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
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
            self,
            "Selecione o Diretório",
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
                self,
                "Erro de Permissão",
                f"Não foi possível criar/escrever no diretório:\n{self.data_dir}\n\n"
                "Escolha outro local (ex: sua pasta Documentos).",
            )
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao criar diretório:\n{e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  DIÁLOGO DO PRESET "DIAGNÓSTICO BAROCLÍNICO"
# ═══════════════════════════════════════════════════════════════════════════════


class BaroclinicLevelDialog(QDialog):
    """Escolha do nível de pressão para o preset Diagnóstico Baroclínico.

    850 hPa é o padrão (nível operacional tradicional de análise de frentes de
    superfície); outros níveis ficam disponíveis. ``selected_level()`` devolve o
    nível escolhido (int) após ``exec()`` retornar ``Accepted``.
    """

    def __init__(self, levels: list[int], default_level: int = 850, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Diagnóstico Baroclínico")
        self.setModal(True)
        self.setStyleSheet(DARK_STYLE)
        self.setMinimumWidth(430)
        self._setup_ui(levels, default_level)

    def _setup_ui(self, levels: list[int], default_level: int):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 16, 18, 16)

        title = QLabel("<h3 style='color:#1ABC9C; margin:0;'>Diagnóstico Baroclínico</h3>")
        layout.addWidget(title)

        desc = QLabel(
            "<p style='font-size:11px; color:#BDC3C7;'>"
            "Empilha campos de apoio ao traçado <b>manual</b> de frentes "
            "(Gradiente de θe + Eixo TFP ligados; Advecção de θe, θe e "
            "Frontogênese disponíveis).</p>"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        row = QHBoxLayout()
        row.addWidget(QLabel("Nível de pressão:"))
        self.level_combo = QComboBox()
        for lv in levels:
            self.level_combo.addItem(f"{lv} hPa", lv)
        idx = levels.index(default_level) if default_level in levels else 0
        self.level_combo.setCurrentIndex(idx)
        self.level_combo.setMinimumWidth(120)
        row.addWidget(self.level_combo)
        row.addStretch()
        layout.addLayout(row)

        note = QLabel(
            "<small style='color:#95A5A6;'>O nível tradicionalmente utilizado na "
            "análise operacional de frentes de superfície é <b>850 hPa</b>.</small>"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setStyleSheet("background-color:#7F8C8D;")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("✓ Confirmar")
        ok_btn.setStyleSheet("background-color:#27AE60; min-width:120px;")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def selected_level(self) -> int:
        """Nível de pressão escolhido (hPa)."""
        return int(self.level_combo.currentData())


class ThermalWindLevelDialog(QDialog):
    """Escolha da camada (base → topo) para a hodógrafa de Vento Térmico.

    Base default 1000 hPa, topo 500 hPa; os níveis-padrão entre eles entram
    automaticamente. ``selected_layer()`` devolve ``(base_p, top_p)`` (base >
    topo em hPa) após ``exec()`` retornar ``Accepted``.
    """

    def __init__(
        self,
        levels: list[int],
        default_base: int = 1000,
        default_top: int = 500,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Vento Térmico — Camada")
        self.setModal(True)
        self.setStyleSheet(DARK_STYLE)
        self.setMinimumWidth(430)
        self._setup_ui(levels, default_base, default_top)

    def _setup_ui(self, levels: list[int], default_base: int, default_top: int):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 16, 18, 16)

        title = QLabel("<h3 style='color:#1ABC9C; margin:0;'>🌀 Vento Térmico</h3>")
        layout.addWidget(title)

        desc = QLabel(
            "<p style='font-size:11px; color:#BDC3C7;'>"
            "Hodógrafa no ponto: o vento de cada nível e o <b>vetor térmico</b> ligando "
            "as pontas, colorido por advecção (🔴 quente / 🔵 fria). Escolha a base e o topo "
            "— os níveis-padrão entre eles entram automaticamente.</p>"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Combos ordenados da base (maior pressão) ao topo (menor).
        levels_desc = sorted(levels, reverse=True)
        row = QHBoxLayout()
        row.addWidget(QLabel("Base:"))
        self.base_combo = QComboBox()
        for lv in levels_desc:
            self.base_combo.addItem(f"{lv} hPa", lv)
        self.base_combo.setCurrentIndex(
            levels_desc.index(default_base) if default_base in levels_desc else 0
        )
        self.base_combo.setMinimumWidth(110)
        row.addWidget(self.base_combo)
        row.addSpacing(12)
        row.addWidget(QLabel("Topo:"))
        self.top_combo = QComboBox()
        for lv in levels_desc:
            self.top_combo.addItem(f"{lv} hPa", lv)
        self.top_combo.setCurrentIndex(
            levels_desc.index(default_top) if default_top in levels_desc else len(levels_desc) - 1
        )
        self.top_combo.setMinimumWidth(110)
        row.addWidget(self.top_combo)
        row.addStretch()
        layout.addLayout(row)

        note = QLabel(
            "<small style='color:#95A5A6;'>A camada clássica de espessura/vento térmico é "
            "<b>1000 → 500 hPa</b>. A base deve ter pressão maior que o topo.</small>"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setStyleSheet("background-color:#7F8C8D;")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("✓ Traçar")
        ok_btn.setStyleSheet("background-color:#27AE60; min-width:120px;")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _on_accept(self):
        base_p = int(self.base_combo.currentData())
        top_p = int(self.top_combo.currentData())
        if base_p <= top_p:
            QMessageBox.warning(
                self,
                "Camada inválida",
                "A base deve ter pressão MAIOR que o topo (ex.: base 1000 hPa, topo 500 hPa).",
            )
            return
        self.accept()

    def selected_layer(self) -> tuple[int, int]:
        """Camada escolhida como ``(base_p, top_p)`` em hPa (base > topo)."""
        return int(self.base_combo.currentData()), int(self.top_combo.currentData())


# ═══════════════════════════════════════════════════════════════════════════════
#  RELATÓRIO DE ESTAÇÃO (popup do clique no METAR/SYNOP plotado)
# ═══════════════════════════════════════════════════════════════════════════════


def _fmt_num(value, unit: str = "", decimals: int = 1) -> str:
    """Número com unidade; NaN/None/inválido vira travessão."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if math.isnan(v):
        return "—"
    return f"{v:.{decimals}f}{unit}"


def station_report_lines(payload: dict) -> list[tuple[str, str]]:
    """Pares (rótulo, valor) do relatório decodificado — puro e testável.

    O vento volta de u/v (canônico) para a convenção meteorológica
    (direção DE ONDE sopra, em graus; velocidade em nós).
    """
    vento = "—"
    try:
        u = float(payload.get("eastward_wind", math.nan))
        v = float(payload.get("northward_wind", math.nan))
        if math.isfinite(u) and math.isfinite(v):
            spd_kt = math.hypot(u, v) * 1.94384
            if spd_kt < 0.5:
                vento = "calmo"
            else:
                # u = -s·sin(d), v = -s·cos(d)  ⇒  d = atan2(-u, -v)
                direc = math.degrees(math.atan2(-u, -v)) % 360.0
                vento = f"{direc:03.0f}° / {spd_kt:.0f} kt"
    except (TypeError, ValueError):
        pass

    okta = payload.get("cloud_coverage", math.nan)
    try:
        okta_txt = f"{int(float(okta))}/8" if not math.isnan(float(okta)) else "—"
    except (TypeError, ValueError):
        okta_txt = "—"

    wx = payload.get("current_wx1_symbol", math.nan)
    try:
        wx_txt = f"código WMO {int(float(wx))}" if not math.isnan(float(wx)) else "—"
    except (TypeError, ValueError):
        wx_txt = "—"

    obs_dt = payload.get("obs_time_utc")
    hora = obs_dt.strftime("%H:%MZ %d/%m/%Y") if obs_dt is not None else "—"

    lat = _fmt_num(payload.get("latitude"), "°", 2)
    lon = _fmt_num(payload.get("longitude"), "°", 2)

    return [
        ("Posição", f"{lat}, {lon}"),
        ("Horário da obs", hora),
        ("Temperatura", _fmt_num(payload.get("air_temperature"), " °C")),
        ("Ponto de orvalho", _fmt_num(payload.get("dew_point_temperature"), " °C")),
        ("PNMM", _fmt_num(payload.get("air_pressure_at_sea_level"), " hPa")),
        ("Vento", vento),
        ("Nebulosidade", okta_txt),
        ("Tempo presente", wx_txt),
    ]


class StationReportDialog(QDialog):
    """Relatório decodificado da estação clicada no mapa (METAR/SYNOP)."""

    def __init__(self, payload: dict, parent=None):
        super().__init__(parent)
        kind = str(payload.get("kind", "")).upper() or "OBS"
        sid = str(payload.get("station_id", "")) or "estação"
        self.setWindowTitle(f"{sid} — {kind}")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h3>{sid} <small style='color:#95A5A6;'>({kind})</small></h3>"))

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        for row, (rotulo, valor) in enumerate(station_report_lines(payload)):
            grid.addWidget(QLabel(f"<b>{rotulo}:</b>"), row, 0)
            grid.addWidget(QLabel(valor), row, 1)
        layout.addLayout(grid)

        raw = str(payload.get("raw_report") or "").strip()
        if raw:
            layout.addWidget(QLabel("<b>Report cru:</b>"))
            raw_box = QPlainTextEdit(raw)
            raw_box.setReadOnly(True)
            raw_box.setMaximumHeight(90)
            raw_box.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
            layout.addWidget(raw_box)

        btn = QPushButton("Fechar")
        btn.setMinimumWidth(100)
        btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn)
        layout.addLayout(btn_row)


# ═══════════════════════════════════════════════════════════════════════════════
#  COMPARAÇÃO DE RODADAS (Δ novo − antigo, mesmo valid_time)
# ═══════════════════════════════════════════════════════════════════════════════


class RunCompareDialog(QDialog):
    """Escolha da comparação de rodadas: variável, nível e defasagem.

    A rodada A é a selecionada no painel (ou a mais recente, no modo auto);
    a rodada B fica ``delta`` horas antes, no MESMO valid_time (step + delta).
    """

    _SFC_VARS = ("tcwv",)  # elegíveis sem nível de pressão

    def __init__(self, parent=None):
        super().__init__(parent)
        from cartomet_br.data.ecmwf import PL_LEVELS, VARIABLE_REGISTRY
        from cartomet_br.services.data_service import RUN_DIFF_VARIABLES

        self.setWindowTitle("Comparar rodadas")
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(
                "<b>Δ = rodada atual − rodada anterior</b>, no mesmo horário de "
                "validade da carta.<br/><small style='color:#95A5A6;'>Vermelho: a "
                "rodada nova intensificou o campo; azul: enfraqueceu. Diferenças "
                "grandes = baixa confiança entre rodadas.</small>"
            )
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)

        grid.addWidget(QLabel("Variável:"), 0, 0)
        self.var_combo = QComboBox()
        for key in RUN_DIFF_VARIABLES:
            nome = VARIABLE_REGISTRY.get(key, {}).get("nome", key)
            self.var_combo.addItem(nome, key)
        grid.addWidget(self.var_combo, 0, 1)

        grid.addWidget(QLabel("Nível:"), 1, 0)
        self.level_combo = QComboBox()
        for lv in PL_LEVELS:
            self.level_combo.addItem(f"{lv} hPa", lv)
        self.level_combo.setCurrentIndex(self.level_combo.findData(500))
        grid.addWidget(self.level_combo, 1, 1)

        grid.addWidget(QLabel("Comparar com:"), 2, 0)
        self.delta_combo = QComboBox()
        for horas in (6, 12, 24):
            self.delta_combo.addItem(f"Rodada de {horas} h atrás", horas)
        self.delta_combo.setToolTip(
            "A rodada anterior é buscada no MESMO valid_time (step + defasagem).\n"
            "Rodadas 06Z/18Z têm alcance de +144h — defasagens grandes podem\n"
            "exigir steps fora da grade; o app avisa se a combinação não existir."
        )
        grid.addWidget(self.delta_combo, 2, 1)
        layout.addLayout(grid)

        self.var_combo.currentIndexChanged.connect(self._on_var_changed)
        self._on_var_changed()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("🔀 Comparar")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _on_var_changed(self, *_args) -> None:
        var = self.var_combo.currentData()
        self.level_combo.setEnabled(var not in self._SFC_VARS)

    def selected(self) -> tuple[str, int | None, int]:
        """(variable_key, level|None p/ superfície, delta_hours)."""
        var = str(self.var_combo.currentData())
        level = None if var in self._SFC_VARS else int(self.level_combo.currentData())
        return var, level, int(self.delta_combo.currentData())
