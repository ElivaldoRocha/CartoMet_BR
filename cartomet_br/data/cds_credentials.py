"""Credencial da API do CDS (Copernicus/ERA5) — lógica pura, sem GUI.

O CartoMet BR NUNCA escreve o arquivo ``~/.cdsapirc`` do usuário: a chave é
passada por kwargs ao ``cdsapi.Client`` e a persistência opcional usa
``QSettings`` (mesmo mecanismo do resto do app), com remoção explícita —
requisito de laboratórios de ensino com computadores compartilhados.

Cadeia de resolução da chave (``resolve_cds_key``):
    1. argumento explícito;
    2. variável de ambiente ``CDSAPI_KEY``;
    3. QSettings (gravada pelo diálogo Arquivo → "Chave ERA5 (CDS)...");
    4. ``None`` (o chamador decide a mensagem de erro).

A reanálise ERA5 é publicada com ~5 dias de atraso (ERA5T) — validação de
datas é responsabilidade do chamador (ver ``ERA5_MIN_DELAY_DAYS``).
"""

from __future__ import annotations

import os
from typing import Any

CDS_URL = "https://cds.climate.copernicus.eu/api"
CDS_KEY_ENV_VAR = "CDSAPI_KEY"
CDS_PROFILE_URL = "https://cds.climate.copernicus.eu/profile"
ERA5_MIN_DELAY_DAYS = 6  # ERA5T atrasa ~5 dias; 6 dá folga operacional

_SETTINGS_ORG = "PPGGRD-UFPA"
_SETTINGS_KEY = "cds_api_key"


def resolve_cds_key(explicit: str | None = None) -> str | None:
    """Resolve a chave pela cadeia documentada no módulo; None se ausente."""
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get(CDS_KEY_ENV_VAR, "").strip()
    if env:
        return env
    return get_stored_key()


def get_stored_key() -> str | None:
    """Chave persistida no QSettings, ou None (também se PyQt6 indisponível)."""
    settings = _qsettings()
    if settings is None:
        return None
    value = str(settings.value(_SETTINGS_KEY, "", type=str)).strip()
    return value or None


def save_cds_key(key: str) -> None:
    """Persiste a chave no QSettings (sobrescreve a anterior)."""
    settings = _qsettings()
    if settings is None:
        raise RuntimeError("PyQt6 indisponível — não há onde persistir a chave.")
    settings.setValue(_SETTINGS_KEY, key.strip())
    settings.sync()


def delete_cds_key() -> None:
    """Remove a chave do QSettings (computadores compartilhados)."""
    settings = _qsettings()
    if settings is None:
        return
    settings.remove(_SETTINGS_KEY)
    settings.sync()


def mask_key(key: str) -> str:
    """Exibição segura: só os 4 últimos caracteres (••••d10f)."""
    key = key.strip()
    if len(key) <= 4:
        return "••••"
    return f"••••{key[-4:]}"


def make_cds_client(key: str | None = None, *, quiet: bool = True) -> Any:
    """Instancia ``cdsapi.Client`` com a chave resolvida (sem .cdsapirc).

    Levanta ``RuntimeError`` instrutivo se não houver chave, e
    ``ImportError`` se o extra ``reanalysis`` não estiver instalado.
    """
    resolved = resolve_cds_key(key)
    if not resolved:
        raise RuntimeError(
            "Chave da API do CDS (Copernicus) não configurada.\n"
            "No CartoMet BR: menu Arquivo → 'Chave ERA5 (CDS)...'.\n"
            f"Obtenha a sua em {CDS_PROFILE_URL} (gratuita)."
        )
    try:
        import cdsapi
    except ImportError as exc:
        raise ImportError(
            "Pacote 'cdsapi' ausente. Instale o extra de reanálise:\n"
            "    uv sync --all-extras   (ou: pip install cartomet-br[reanalysis])"
        ) from exc
    return cdsapi.Client(url=CDS_URL, key=resolved, quiet=quiet)


def _qsettings() -> Any | None:
    """QSettings do app (lazy) — None quando PyQt6 não está disponível."""
    try:
        from PyQt6.QtCore import QSettings

        from cartomet_br.gui._constants import APP_NAME
    except ImportError:
        return None
    return QSettings(_SETTINGS_ORG, APP_NAME)
