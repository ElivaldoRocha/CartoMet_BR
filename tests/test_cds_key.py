"""Credencial CDS/ERA5 (cds_credentials + diálogo).

A cadeia de resolução da chave é lógica pura; a persistência usa QSettings
com organização de TESTE (monkeypatch de _qsettings) para nunca tocar a
chave real do usuário no registro.
"""

from __future__ import annotations

import pytest

from cartomet_br.data import cds_credentials as cds


class _FakeSettings:
    """Dublê de QSettings em memória."""

    def __init__(self, store: dict):
        self._store = store

    def value(self, key, default="", type=str):  # noqa: A002 — assinatura Qt
        return self._store.get(key, default)

    def setValue(self, key, value):  # noqa: N802 — assinatura Qt
        self._store[key] = value

    def remove(self, key):
        self._store.pop(key, None)

    def sync(self):
        pass


@pytest.fixture
def fake_settings(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(cds, "_qsettings", lambda: _FakeSettings(store))
    return store


def test_resolve_explicito_tem_prioridade(fake_settings, monkeypatch):
    monkeypatch.setenv(cds.CDS_KEY_ENV_VAR, "chave-do-env")
    fake_settings[cds._SETTINGS_KEY] = "chave-do-qsettings"
    assert cds.resolve_cds_key("  chave-explicita  ") == "chave-explicita"


def test_resolve_env_antes_do_qsettings(fake_settings, monkeypatch):
    monkeypatch.setenv(cds.CDS_KEY_ENV_VAR, "chave-do-env")
    fake_settings[cds._SETTINGS_KEY] = "chave-do-qsettings"
    assert cds.resolve_cds_key() == "chave-do-env"


def test_resolve_cai_no_qsettings(fake_settings, monkeypatch):
    monkeypatch.delenv(cds.CDS_KEY_ENV_VAR, raising=False)
    fake_settings[cds._SETTINGS_KEY] = "chave-do-qsettings"
    assert cds.resolve_cds_key() == "chave-do-qsettings"


def test_resolve_none_quando_nada(fake_settings, monkeypatch):
    monkeypatch.delenv(cds.CDS_KEY_ENV_VAR, raising=False)
    assert cds.resolve_cds_key() is None
    assert cds.resolve_cds_key("   ") is None


def test_salvar_e_apagar_chave(fake_settings, monkeypatch):
    monkeypatch.delenv(cds.CDS_KEY_ENV_VAR, raising=False)
    cds.save_cds_key("  minha-chave-123  ")
    assert cds.get_stored_key() == "minha-chave-123"
    cds.delete_cds_key()  # requisito de laboratório compartilhado
    assert cds.get_stored_key() is None


def test_mask_key_nunca_exibe_o_valor():
    assert cds.mask_key("c3defdb3-7201-4560-989d-861c32c2110f") == "••••110f"
    assert "1234" not in cds.mask_key("abc")  # curta → só bolinhas


def test_make_cds_client_sem_chave_erro_instrutivo(fake_settings, monkeypatch):
    monkeypatch.delenv(cds.CDS_KEY_ENV_VAR, raising=False)
    with pytest.raises(RuntimeError, match="Chave ERA5"):
        cds.make_cds_client()


def test_dialogo_importavel_e_instanciavel(qt_app, fake_settings, monkeypatch):
    """Smoke: diálogo constrói offscreen e reflete o estado sem chave."""
    monkeypatch.delenv(cds.CDS_KEY_ENV_VAR, raising=False)
    from cartomet_br.gui.cds_key_dialog import CDSKeyDialog

    dlg = CDSKeyDialog()
    assert "nenhuma chave" in dlg.status_label.text().lower()
    assert not dlg.delete_btn.isEnabled()

    fake_settings[cds._SETTINGS_KEY] = "abcd1234"
    dlg._refresh_status()
    assert "1234" in dlg.status_label.text()
    assert "abcd" not in dlg.status_label.text()  # nunca o valor completo
    assert dlg.delete_btn.isEnabled()


@pytest.fixture
def qt_app():
    """QApplication offscreen (conftest já força QT_QPA_PLATFORM=offscreen)."""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
