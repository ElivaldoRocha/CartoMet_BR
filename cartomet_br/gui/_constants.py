"""
Constantes e utilitários da aplicação CartoMet BR.

Centraliza metadados, steps válidos e caminhos de assets
para evitar dependências circulares entre módulos GUI.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME: str = "CartoMet BR"
APP_VERSION: str = "3.1.0"
APP_AUTHOR: str = "Elivaldo C. Rocha"
APP_INSTITUTION: str = "PPGGRD-UFPA / FAMET-UFPA"
APP_DESCRIPTION: str = "Cartografia Meteorológica para o Brasil"

# Steps válidos do ECMWF Open Data:
# - Múltiplos de 3 até 144h
# - Múltiplos de 6 após 144h até 240h
VALID_STEPS: list[int] = list(range(0, 145, 3)) + list(range(150, 241, 6))


def get_assets_path() -> Path:
    """Retorna caminho dos assets, funciona em dev e no executável."""
    if getattr(sys, "frozen", False):
        base: Path = Path(sys._MEIPASS)  # type: ignore[attr-defined]  # PyInstaller (runtime frozen)
    else:
        base = Path(__file__).parent.parent
    return base / "assets"


def get_icon_path() -> Path | None:
    """Retorna caminho do ícone 32x32."""
    assets: Path = get_assets_path()
    for name in ["CartoMet_BR_logo_32_32.ico", "icon.ico", "icon.svg"]:
        path: Path = assets / name
        if path.exists():
            return path
    return None


def get_logo_path() -> Path | None:
    """Retorna caminho da logo 400x400."""
    assets: Path = get_assets_path()
    for name in ["CartoMet_BR_logo_400_400.png", "logo.png", "logo.svg"]:
        path: Path = assets / name
        if path.exists():
            return path
    return None


def get_institutional_logos_path() -> Path | None:
    """Retorna caminho das logos institucionais."""
    assets: Path = get_assets_path()
    path: Path = assets / "Logos_UFPA_IG_FAMET_PPGGRD.png"
    if path.exists():
        return path
    return None
