"""
Threads de download e diálogo de progresso do CartoMet BR.

Contém as threads que executam downloads em background (ECMWF, PL/OLR, GOES)
e o diálogo de progresso com barra e botão de cancelamento.

As threads delegam operações de dados ao DataService, mantendo
a lógica de negócio fora do código de threading/GUI.
"""

import io
import logging
import re
import sys

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread

from cartomet_br.core.config import Config
from cartomet_br.data.ecmwf import VARIABLE_REGISTRY
from cartomet_br.services.data_service import (
    DataService, ValidationError, DownloadError,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  CAPTURA DE PROGRESSO (tqdm do ECMWF client)
# ═══════════════════════════════════════════════════════════════════════════════

class StderrProgressCapture(io.TextIOBase):
    """Captura stderr para extrair progresso do tqdm do ECMWF client."""

    def __init__(self, callback, original_stderr):
        super().__init__()
        self.callback = callback
        self.original = original_stderr
        self._buffer = ""

    def write(self, text):
        if self.original:
            self.original.write(text)

        self._buffer += text

        match = re.search(r'(\d{1,3})%\|', self._buffer)
        if not match:
            match = re.search(r'\s(\d{1,3})%', self._buffer)

        if match:
            pct = int(match.group(1))
            self.callback(pct)
            self._buffer = ""

        if len(self._buffer) > 2000:
            self._buffer = self._buffer[-500:]

        return len(text)

    def flush(self):
        if self.original:
            self.original.flush()


# ═══════════════════════════════════════════════════════════════════════════════
#  INTERCEPTOR DE RETRIES (multiurl → GUI)
# ═══════════════════════════════════════════════════════════════════════════════

class _RetryLogHandler(logging.Handler):
    """Intercepta mensagens de retry do multiurl e repassa para a GUI.

    O multiurl (usado pelo ecmwf-opendata) faz retries automáticos em
    erros HTTP 429, mas essas mensagens ficam apenas no log — invisíveis
    para o usuário.  Este handler captura os LOG.warning() do
    ``multiurl.http`` e emite mensagens amigáveis via callback.
    """

    def __init__(self, message_callback):
        super().__init__(level=logging.WARNING)
        self.message_callback = message_callback

    def emit(self, record):
        try:
            msg = record.getMessage()
            # "Recovering from HTTP error [429 Too Many Requests], attempt 2 of 500"
            if "Recovering from HTTP error" in msg and "429" in msg:
                attempt_match = re.search(r'attempt (\d+) of (\d+)', msg)
                if attempt_match:
                    attempt = attempt_match.group(1)
                    self.message_callback(
                        f"Servidor ECMWF ocupado (HTTP 429) — "
                        f"tentativa {attempt}, aguardando..."
                    )
                return
            # "Recovering from connection error [...], attempt N of M"
            if "Recovering from connection error" in msg:
                attempt_match = re.search(r'attempt (\d+)', msg)
                attempt = attempt_match.group(1) if attempt_match else "?"
                self.message_callback(
                    f"Erro de conexão — tentativa {attempt}, reconectando..."
                )
                return
            # "Retrying in 120 seconds"
            if "Retrying in" in msg:
                wait_match = re.search(r'Retrying in (\d+) seconds', msg)
                if wait_match:
                    secs = int(wait_match.group(1))
                    mins = secs // 60
                    if mins > 0:
                        self.message_callback(
                            f"Aguardando {mins}min{secs % 60:02d}s para nova tentativa..."
                        )
                    else:
                        self.message_callback(
                            f"Aguardando {secs}s para nova tentativa..."
                        )
                return
            # "Retrying using mirror ..."
            if "Retrying using mirror" in msg:
                self.message_callback("Tentando espelho alternativo...")
                return
            # "Retrying now..."
            if "Retrying now" in msg:
                self.message_callback("Reconectando ao ECMWF...")
                return
        except Exception:
            pass  # Nunca deixar o handler quebrar o download


def _attach_retry_handler(message_callback) -> tuple[logging.Handler, logging.Logger]:
    """Anexa o _RetryLogHandler ao logger ``multiurl.http`` e retorna (handler, logger)."""
    handler = _RetryLogHandler(message_callback)
    multiurl_logger = logging.getLogger("multiurl.http")
    multiurl_logger.addHandler(handler)
    return handler, multiurl_logger


# ═══════════════════════════════════════════════════════════════════════════════
#  THREAD DE DOWNLOAD SINÓTICO
# ═══════════════════════════════════════════════════════════════════════════════

class DownloadThread(QThread):
    """Thread para download de dados sinóticos ECMWF via DataService."""

    progress = pyqtSignal(str)
    download_percent = pyqtSignal(int)
    finished_ok = pyqtSignal(object)
    finished_error = pyqtSignal(str)

    def __init__(self, config: Config, step: int, cycle: int | None = None,
                 cycle_date: str | None = None, parent=None):
        super().__init__(parent)
        self.service = DataService(config)
        self.step = step
        self.cycle = cycle
        self.cycle_date = cycle_date

    def _on_percent(self, pct):
        self.download_percent.emit(pct)

    def run(self):
        original_stderr = sys.stderr
        capture = StderrProgressCapture(self._on_percent, original_stderr)
        sys.stderr = capture

        # Intercepta mensagens de retry do multiurl → GUI
        retry_handler, retry_logger = _attach_retry_handler(
            lambda msg: self.progress.emit(msg)
        )

        try:
            # Validação via service (levanta ValidationError)
            DataService.validate_step(self.step)
            DataService.validate_cycle(self.cycle, self.step)

            if self.cycle is not None:
                cycle_label = f"{self.cycle:02d}Z"
                self.progress.emit(f"Conectando ao ECMWF... Rodada: {cycle_label}")
            else:
                cycle_info = self.service.get_available_cycles()
                if cycle_info.get("latest"):
                    latest = cycle_info["latest"]
                    self.progress.emit(
                        f"Conectando ao ECMWF... "
                        f"Rodada estimada: {latest['label']} {latest['date_str']}"
                    )
                else:
                    self.progress.emit("Conectando ao ECMWF Open Data...")

            self.progress.emit(f"Baixando dados (Step +{self.step}h)...")

            data = self.service.load_synoptic(
                step=self.step,
                cycle=self.cycle,
                cycle_date=self.cycle_date,
            )

            rodada_msg = f" | Rodada: {data.base_time}" if data.base_time else ""
            self.progress.emit(f"Download concluído!{rodada_msg}")
            self.finished_ok.emit(data)

        except (ValidationError, DownloadError) as e:
            self.finished_error.emit(str(e))
        except Exception as e:
            self.finished_error.emit(str(e))
        finally:
            sys.stderr = original_stderr
            retry_logger.removeHandler(retry_handler)


# ═══════════════════════════════════════════════════════════════════════════════
#  THREAD DE DOWNLOAD DE CAMPOS PL / OLR
# ═══════════════════════════════════════════════════════════════════════════════

class PLDownloadThread(QThread):
    """Thread para download de variáveis em nível de pressão / OLR via DataService."""

    progress = pyqtSignal(str)
    download_percent = pyqtSignal(int)
    finished_ok = pyqtSignal(str, object)  # (layer_id, PLFieldData)
    finished_error = pyqtSignal(str)

    def __init__(self, variable_key, level, step, cycle, config, wind_type="barbs",
                 cycle_date=None, technique="direct", parent=None):
        super().__init__(parent)
        self.service = DataService(config)
        self.variable_key = variable_key
        self.level = level
        self.step = step
        self.cycle = cycle
        self.wind_type = wind_type
        self.cycle_date = cycle_date
        self.technique = technique

    def _on_percent(self, pct):
        self.download_percent.emit(pct)

    def run(self):
        original_stderr = sys.stderr
        capture = StderrProgressCapture(self._on_percent, original_stderr)
        sys.stderr = capture

        # Intercepta mensagens de retry do multiurl → GUI
        retry_handler, retry_logger = _attach_retry_handler(
            lambda msg: self.progress.emit(msg)
        )

        try:
            var_info = VARIABLE_REGISTRY[self.variable_key]
            self.progress.emit(
                f"Baixando {var_info['nome']}"
                + (f" em {self.level} hPa..." if self.level else "...")
            )

            layer_id, data = self.service.load_field(
                variable_key=self.variable_key,
                level=self.level,
                step=self.step,
                cycle=self.cycle,
                cycle_date=self.cycle_date,
                wind_type=self.wind_type,
                technique=self.technique,
            )

            self.progress.emit(f"Camada {var_info['nome']} carregada!")
            self.finished_ok.emit(layer_id, data)

        except (ValidationError, DownloadError) as e:
            self.finished_error.emit(str(e))
        except Exception as e:
            self.finished_error.emit(str(e))
        finally:
            sys.stderr = original_stderr
            retry_logger.removeHandler(retry_handler)


# ═══════════════════════════════════════════════════════════════════════════════
#  THREAD DE DOWNLOAD GOES-EAST
# ═══════════════════════════════════════════════════════════════════════════════

class SatDownloadThread(QThread):
    """Thread para download de imagem GOES-East via DataService."""

    progress = pyqtSignal(str)
    download_percent = pyqtSignal(int)
    cache_hit = pyqtSignal(str)
    finished_ok = pyqtSignal(object)  # SatelliteData
    finished_error = pyqtSignal(str)

    def __init__(self, config: Config, target_time=None, parent=None):
        super().__init__(parent)
        self.service = DataService(config)
        self.target_time = target_time

    def _progress_callback(self, kind, value):
        """Callback chamado pelo download_goes16_ir."""
        if kind == "percent":
            self.download_percent.emit(int(value))
        elif kind == "status":
            self.progress.emit(str(value))
        elif kind == "cache":
            self.cache_hit.emit(str(value))

    def run(self):
        try:
            self.progress.emit("Buscando imagem GOES-East no AWS...")
            data = self.service.load_satellite(
                target_time=self.target_time,
                progress_callback=self._progress_callback,
            )
            self.progress.emit("Imagem de satélite carregada!")
            self.finished_ok.emit(data)
        except (DownloadError, Exception) as e:
            self.finished_error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════════════
#  THREAD DE DOWNLOAD MUR SST (TSM)
# ═══════════════════════════════════════════════════════════════════════════════

class SSTDownloadThread(QThread):
    """Thread para download de dados MUR SST via ERDDAP OPeNDAP."""

    progress = pyqtSignal(str)
    download_percent = pyqtSignal(int)
    finished_ok = pyqtSignal(object)   # SSTData
    finished_error = pyqtSignal(str)

    def __init__(self, config: Config, target_date=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.target_date = target_date

    def _progress_callback(self, kind, value):
        if kind == "percent":
            self.download_percent.emit(int(value))
        elif kind == "status":
            self.progress.emit(str(value))

    def run(self):
        try:
            from cartomet_br.data.sst import download_mur_sst

            self.progress.emit("Conectando ao ERDDAP — MUR SST 1km...")

            data = download_mur_sst(
                target_date=self.target_date,
                extent=self.config.extent,
                data_dir=self.config.sst_dir,
                progress_callback=self._progress_callback,
            )

            self.progress.emit(f"TSM carregada — {data.time_str}")
            self.finished_ok.emit(data)

        except Exception as e:
            self.finished_error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════════════
#  THREAD DE DOWNLOAD DE OBSERVAÇÕES (SYNOP / METAR)
# ═══════════════════════════════════════════════════════════════════════════════

class StationDownloadThread(QThread):
    """Thread para baixar observações de superfície (SYNOP/METAR).

    Emite `finished_ok(dict)` com {"metar": DataFrame|None, "synop": DataFrame|None}.
    Falhas de rede não quebram a UI — DataFrames vazios são retornados.
    """

    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object)   # dict[str, DataFrame]
    finished_error = pyqtSignal(str)

    def __init__(self, config: Config, want_metar: bool, want_synop: bool,
                 target_time=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.want_metar = want_metar
        self.want_synop = want_synop
        self.target_time = target_time

    def run(self):
        try:
            from cartomet_br.data.stations import fetch_metar, fetch_synop

            result: dict = {"metar": None, "synop": None}
            extent = self.config.extent
            data_dir = self.config.observations_dir

            if self.want_metar:
                self.progress.emit("Consultando METAR (NOAA AWC)...")
                result["metar"] = fetch_metar(
                    extent, when=self.target_time, data_dir=data_dir,
                )

            if self.want_synop:
                self.progress.emit("Consultando SYNOP (OGIMET)...")
                result["synop"] = fetch_synop(
                    extent, when=self.target_time, data_dir=data_dir,
                    progress_callback=lambda msg: self.progress.emit(msg),
                )

            self.progress.emit("Observações carregadas!")
            self.finished_ok.emit(result)

        except Exception as e:
            self.finished_error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════════════
#  THREAD DO ÍNDICE LOCZCIT-PA (ZCIT)
# ═══════════════════════════════════════════════════════════════════════════════

class LoczcitThread(QThread):
    """Thread que roda o motor LOCZCIT-PA fora da UI (blindagem #6).

    Emite `finished_ok(LoczcitResult)`. Falha de rede/cálculo nunca trava a GUI.
    """

    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object)   # LoczcitResult
    finished_error = pyqtSignal(str)
    finished_cancelled = pyqtSignal()

    def __init__(self, config: Config, cycle: int | None, cycle_date: str | None,
                 step: int = 0, parent=None):
        super().__init__(parent)
        self.config = config
        self.cycle = cycle
        self.cycle_date = cycle_date
        self.step = step
        self._cancelled = False

    def cancel(self):
        """Pede cancelamento cooperativo (abortado entre downloads)."""
        self._cancelled = True

    def run(self):
        from cartomet_br.data.loczcit_pa_engine import (
            LoczcitCancelled,
            compute_loczcit_pa,
        )

        # Intercepta retries do multiurl (HTTP 429) → mostra ao usuário
        retry_handler, retry_logger = _attach_retry_handler(
            lambda msg: self.progress.emit(msg)
        )
        try:
            result = compute_loczcit_pa(
                cycle=self.cycle,
                cycle_date=self.cycle_date,
                data_dir=self.config.grib_dir,
                step=self.step,
                source=self.config.ecmwf_source,
                progress_callback=lambda msg: self.progress.emit(msg),
                cancel_check=lambda: self._cancelled,
            )
            self.progress.emit("Índice LOCZCIT-PA concluído!")
            self.finished_ok.emit(result)
        except LoczcitCancelled:
            self.finished_cancelled.emit()
        except Exception as e:
            self.finished_error.emit(str(e))
        finally:
            retry_logger.removeHandler(retry_handler)


# ═══════════════════════════════════════════════════════════════════════════════
#  DIÁLOGO DE PROGRESSO DE DOWNLOAD
# ═══════════════════════════════════════════════════════════════════════════════

class DownloadProgressDialog(QDialog):
    """Janela de progresso que aparece durante downloads ECMWF."""

    cancel_requested = pyqtSignal()

    def __init__(self, title="Baixando dados...", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedSize(420, 180)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint
        )
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        self.status_label = QLabel("Conectando ao ECMWF Open Data...")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 12px; font-weight: bold; color: #3498DB;")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setMinimumHeight(22)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #5D6D7E;
                border-radius: 6px;
                text-align: center;
                background-color: #34495E;
                font-weight: bold;
                color: white;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #27AE60, stop:1 #2ECC71);
                border-radius: 5px;
            }
        """)
        layout.addWidget(self.progress_bar)

        self.detail_label = QLabel("")
        self.detail_label.setStyleSheet("font-size: 10px; color: #95A5A6;")
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.detail_label)

        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #E74C3C; padding: 6px 20px;
                font-size: 11px; min-width: 80px;
            }
            QPushButton:hover { background-color: #FF6666; }
        """)
        self.cancel_btn.clicked.connect(self._on_cancel)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def update_status(self, msg: str):
        self.status_label.setText(msg)
        # Destaca visualmente mensagens de retry/429
        if "429" in msg or "tentativa" in msg or "Aguardando" in msg:
            self.status_label.setStyleSheet(
                "font-size: 12px; font-weight: bold; color: #E67E22;"
            )
        elif "concluído" in msg.lower() or "carregad" in msg.lower():
            self.status_label.setStyleSheet(
                "font-size: 12px; font-weight: bold; color: #27AE60;"
            )
        else:
            self.status_label.setStyleSheet(
                "font-size: 12px; font-weight: bold; color: #3498DB;"
            )

    def update_percent(self, pct: int):
        self.progress_bar.setValue(pct)
        if pct > 0:
            self.progress_bar.setRange(0, 100)
            self.detail_label.setText(f"Baixando arquivo... {pct}%")

    def set_indeterminate(self):
        """Modo indeterminado (pulsante) quando não temos porcentagem."""
        self.progress_bar.setRange(0, 0)
        self.detail_label.setText("Aguarde...")

    def finish_ok(self):
        """Fecha automaticamente ao concluir."""
        self.accept()

    def finish_error(self):
        """Fecha ao encontrar erro (o erro é mostrado em outro diálogo)."""
        self.reject()

    def _on_cancel(self):
        self.cancel_requested.emit()
        self.detail_label.setText("Cancelando...")
        self.cancel_btn.setEnabled(False)

    def closeEvent(self, event):
        self.cancel_requested.emit()
        event.ignore()
