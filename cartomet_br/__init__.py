"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  CartoMet_BR — Cartografia Meteorológica para o Brasil                       ║
║  Autor: Elivaldo C. Rocha | PPGGRD-UFPA / FAMET-UFPA                         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Módulos:                                                                    ║
║    • core     — Configurações e utilidades base                              ║
║    • data     — Download e processamento de dados (ECMWF, GFS, etc.)         ║
║    • symbols  — Simbologias meteorológicas (frentes, ZCAS, cavados, etc.)    ║
║    • charts   — Geração de cartas (sinótica, interativa, exportação)         ║
║    • gui      — Interface gráfica PyQt6 (opcional)                           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

try:
    from importlib.metadata import version as _get_version
    __version__ = _get_version("cartomet-br")
except Exception:
    __version__ = "2.2.0"
__author__ = "Elivaldo C. Rocha"
__email__ = "carvalhovaldo09@gmail.com"

# Imports convenientes no nível do pacote
from cartomet_br.core.config import Config, EXTENT_BRASIL, EXTENT_AMSUL, validate_extent
from cartomet_br.symbols import (
    FrenteFria,
    FrenteQuente,
    FrenteEstacionaria,
    FrenteOclusa,
    ZCASEffect,
    ZCITEffect,
    CavadoEffect,
    Crista,
    LinhaInstabilidade,
    LinhaSeca,
    MODOS,
)
from cartomet_br.charts.synoptic import create_synoptic_chart
from cartomet_br.charts.interactive import run_interactive

__all__ = [
    # Config
    "Config",
    "EXTENT_BRASIL",
    "EXTENT_AMSUL",
    "validate_extent",
    # Symbols
    "FrenteFria",
    "FrenteQuente",
    "FrenteEstacionaria",
    "FrenteOclusa",
    "ZCASEffect",
    "ZCITEffect",
    "CavadoEffect",
    "Crista",
    "LinhaInstabilidade",
    "LinhaSeca",
    "MODOS",
    # Charts
    "create_synoptic_chart",
    "run_interactive",
]
