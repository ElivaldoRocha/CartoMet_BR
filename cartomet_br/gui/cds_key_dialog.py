"""Diálogo da chave ERA5/CDS (Arquivo → "Chave ERA5 (CDS)...").

O usuário do CartoMet não usa terminal: a chave da API do Copernicus (CDS)
é digitada aqui e persistida via ``QSettings`` — nunca em ``~/.cdsapirc``.
O botão "Apagar chave" atende laboratórios de ensino com computadores
compartilhados. O teste de conexão roda em ``QThread`` (nunca trava a GUI)
e degrada com mensagem clara se o extra ``reanalysis`` não está instalado.
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
)

from cartomet_br.data.cds_credentials import (
    CDS_PROFILE_URL,
    delete_cds_key,
    get_stored_key,
    make_cds_client,
    mask_key,
    save_cds_key,
)


class _TestConnectionThread(QThread):
    """Valida a chave contra o CDS fora da thread da GUI."""

    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self._key = key

    def run(self) -> None:
        try:
            client = make_cds_client(self._key)
            # Chamada leve: o construtor valida formato; um status check
            # confirma credencial/endpoint sem enfileirar download.
            status = getattr(client, "status", None)
            if callable(status):
                status()
            self.finished_ok.emit("Conexão com o CDS OK — chave aceita.")
        except ImportError as exc:
            self.finished_error.emit(str(exc))
        except Exception as exc:  # credencial inválida, rede, etc.
            self.finished_error.emit(f"Falha ao validar a chave:\n{exc}")


class CDSKeyDialog(QDialog):
    """Gerencia a chave pessoal da API do CDS (salvar/apagar/testar)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chave ERA5 (CDS) — Copernicus")
        self.setMinimumWidth(520)
        self._test_thread: _TestConnectionThread | None = None

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Para baixar a reanálise <b>ERA5</b> é preciso uma chave pessoal "
            f"(gratuita) da API do CDS/Copernicus: <a href='{CDS_PROFILE_URL}'>"
            "cds.climate.copernicus.eu/profile</a>.<br>"
            "A chave fica salva apenas neste computador e pode ser apagada a "
            "qualquer momento (recomendado em computadores compartilhados).<br>"
            "<i>Obs.: o ERA5 é publicado com ~5 dias de atraso em relação a "
            "hoje.</i>"
        )
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        layout.addWidget(intro)

        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Cole aqui a sua API key do CDS")
        self.show_btn = QToolButton()
        self.show_btn.setText("👁")
        self.show_btn.setCheckable(True)
        self.show_btn.setToolTip("Exibir/ocultar a chave")
        self.show_btn.toggled.connect(self._toggle_echo)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.show_btn)
        layout.addLayout(key_row)

        buttons = QHBoxLayout()
        self.save_btn = QPushButton("Salvar chave")
        self.save_btn.clicked.connect(self._save)
        self.delete_btn = QPushButton("Apagar chave…")
        self.delete_btn.clicked.connect(self._delete)
        self.test_btn = QPushButton("Testar conexão")
        self.test_btn.clicked.connect(self._test)
        close_btn = QPushButton("Fechar")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.delete_btn)
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self._refresh_status()

    # ── Slots ────────────────────────────────────────────────────────────

    def _toggle_echo(self, checked: bool) -> None:
        self.key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _save(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            QMessageBox.warning(self, "Chave vazia", "Cole a chave antes de salvar.")
            return
        save_cds_key(key)
        self.key_edit.clear()
        self._refresh_status()
        QMessageBox.information(self, "Chave salva", "Chave do CDS salva neste computador.")

    def _delete(self) -> None:
        if get_stored_key() is None:
            QMessageBox.information(self, "Sem chave", "Não há chave salva.")
            return
        answer = QMessageBox.question(
            self,
            "Apagar chave",
            "Remover a chave do CDS deste computador?\n"
            "(Recomendado ao encerrar o uso em máquinas compartilhadas.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            delete_cds_key()
            self._refresh_status()

    def _test(self) -> None:
        key = self.key_edit.text().strip() or (get_stored_key() or "")
        if not key:
            QMessageBox.warning(
                self,
                "Sem chave",
                "Cole uma chave (ou salve uma) antes de testar a conexão.",
            )
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Testando…")
        self._test_thread = _TestConnectionThread(key, self)
        self._test_thread.finished_ok.connect(self._on_test_ok)
        self._test_thread.finished_error.connect(self._on_test_error)
        self._test_thread.finished.connect(self._on_test_done)
        self._test_thread.start()

    def _on_test_ok(self, message: str) -> None:
        QMessageBox.information(self, "Teste de conexão", message)

    def _on_test_error(self, message: str) -> None:
        QMessageBox.warning(self, "Teste de conexão", message)

    def _on_test_done(self) -> None:
        self.test_btn.setEnabled(True)
        self.test_btn.setText("Testar conexão")
        self._test_thread = None

    # ── Helpers ──────────────────────────────────────────────────────────

    def _refresh_status(self) -> None:
        stored = get_stored_key()
        if stored:
            self.status_label.setText(f"Status: <b>chave configurada</b> ({mask_key(stored)}) ✔")
        else:
            self.status_label.setText("Status: <b>nenhuma chave salva</b>.")
        self.delete_btn.setEnabled(stored is not None)

    def closeEvent(self, event) -> None:  # noqa: N802 — API Qt
        # Não deixa QThread órfã se o usuário fechar durante o teste.
        if self._test_thread is not None and self._test_thread.isRunning():
            self._test_thread.wait(3000)
        super().closeEvent(event)
