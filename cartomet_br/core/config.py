"""
Configurações globais do CartoMet_BR.
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

logger: logging.Logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  EXTENSÕES GEOGRÁFICAS PREDEFINIDAS [lon_min, lat_min, lon_max, lat_max]
# ═══════════════════════════════════════════════════════════════════════════════

EXTENT_BRASIL: list[float] = [-75.0, -35.0, -30.0, 6.0]
EXTENT_AMSUL: list[float] = [-100.0, -75.0, 0.0, 15.0]
EXTENT_NORDESTE: list[float] = [-50.0, -20.0, -32.0, 0.0]
EXTENT_SUDESTE: list[float] = [-55.0, -28.0, -38.0, -14.0]
EXTENT_SUL: list[float] = [-60.0, -35.0, -45.0, -20.0]

# Recortes por estado (27 UFs). Caixas derivadas dos limites IBGE, arredondadas
# PARA FORA em graus inteiros com ~1° de margem (os spinboxes de extent da GUI
# são inteiros — valores fracionários dessincronizariam o combo Estado deles).
# Ilhas oceânicas distantes (Fernando de Noronha/PE, Trindade/ES) ficam de fora
# para não esticar o recorte continental.
EXTENT_UFS: dict[str, list[float]] = {
    "AC": [-75.0, -12.0, -66.0, -6.0],
    "AL": [-39.0, -12.0, -34.0, -8.0],
    "AM": [-75.0, -11.0, -55.0, 3.0],
    "AP": [-56.0, -2.0, -49.0, 5.0],
    "BA": [-48.0, -19.0, -36.0, -8.0],
    "CE": [-42.0, -9.0, -36.0, -2.0],
    "DF": [-49.0, -17.0, -46.0, -15.0],
    "ES": [-43.0, -22.0, -38.0, -17.0],
    "GO": [-54.0, -20.0, -45.0, -12.0],
    "MA": [-50.0, -11.0, -41.0, 0.0],
    "MG": [-52.0, -24.0, -39.0, -13.0],
    "MS": [-59.0, -25.0, -50.0, -16.0],
    "MT": [-63.0, -19.0, -49.0, -6.0],
    "PA": [-60.0, -11.0, -45.0, 3.0],
    "PB": [-40.0, -9.0, -34.0, -5.0],
    "PE": [-42.0, -10.0, -34.0, -6.0],
    "PI": [-47.0, -12.0, -39.0, -2.0],
    "PR": [-56.0, -28.0, -47.0, -22.0],
    "RJ": [-46.0, -24.0, -40.0, -20.0],
    "RN": [-40.0, -8.0, -34.0, -4.0],
    "RO": [-68.0, -15.0, -59.0, -7.0],
    "RR": [-66.0, -3.0, -58.0, 6.0],
    "RS": [-59.0, -35.0, -49.0, -26.0],
    "SC": [-55.0, -30.0, -47.0, -25.0],
    "SE": [-39.0, -13.0, -35.0, -9.0],
    "SP": [-54.0, -26.0, -43.0, -19.0],
    "TO": [-52.0, -14.0, -44.0, -4.0],
}

UF_NOMES: dict[str, str] = {
    "AC": "Acre",
    "AL": "Alagoas",
    "AM": "Amazonas",
    "AP": "Amapá",
    "BA": "Bahia",
    "CE": "Ceará",
    "DF": "Distrito Federal",
    "ES": "Espírito Santo",
    "GO": "Goiás",
    "MA": "Maranhão",
    "MG": "Minas Gerais",
    "MS": "Mato Grosso do Sul",
    "MT": "Mato Grosso",
    "PA": "Pará",
    "PB": "Paraíba",
    "PE": "Pernambuco",
    "PI": "Piauí",
    "PR": "Paraná",
    "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte",
    "RO": "Rondônia",
    "RR": "Roraima",
    "RS": "Rio Grande do Sul",
    "SC": "Santa Catarina",
    "SE": "Sergipe",
    "SP": "São Paulo",
    "TO": "Tocantins",
}


def _get_default_data_dir() -> Path:
    """Retorna diretório de dados (usa variável de ambiente se disponível)."""
    env_dir = os.environ.get("CARTOMET_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    return Path("data")


def _get_default_output_dir() -> Path:
    """Retorna diretório de saída (usa variável de ambiente se disponível)."""
    env_dir = os.environ.get("CARTOMET_OUTPUT_DIR")
    if env_dir:
        return Path(env_dir)
    return Path("output")


# ═══════════════════════════════════════════════════════════════════════════════
#  VALIDAÇÃO
# ═══════════════════════════════════════════════════════════════════════════════


def validate_extent(extent: list[float]) -> None:
    """
    Valida extensão geográfica [lon_min, lat_min, lon_max, lat_max].

    Raises
    ------
    ValueError
        Se os valores estiverem fora dos limites ou forem inconsistentes.
    """
    if not isinstance(extent, (list, tuple)) or len(extent) != 4:
        raise ValueError(
            f"extent deve ter 4 elementos [lon_min, lat_min, lon_max, lat_max], recebeu: {extent}"
        )

    lon_min, lat_min, lon_max, lat_max = extent

    if not (-180 <= lon_min <= 180 and -180 <= lon_max <= 180):
        raise ValueError(f"Longitudes devem estar entre -180 e 180: [{lon_min}, {lon_max}]")

    if not (-90 <= lat_min <= 90 and -90 <= lat_max <= 90):
        raise ValueError(f"Latitudes devem estar entre -90 e 90: [{lat_min}, {lat_max}]")

    if lon_min >= lon_max:
        raise ValueError(f"lon_min ({lon_min}) deve ser menor que lon_max ({lon_max})")

    if lat_min >= lat_max:
        raise ValueError(f"lat_min ({lat_min}) deve ser menor que lat_max ({lat_max})")


# ═══════════════════════════════════════════════════════════════════════════════
#  CLASSE DE CONFIGURAÇÃO
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Config:
    """
    Configuração centralizada do CartoMet_BR.

    Exemplo de uso:
    >>> config = Config(extent=EXTENT_BRASIL, dpi=200)
    >>> config.output_dir.mkdir(exist_ok=True)
    """

    # Extensão geográfica
    extent: list[float] = field(default_factory=lambda: EXTENT_AMSUL.copy())

    # Diretórios
    data_dir: Path = field(default_factory=_get_default_data_dir)
    output_dir: Path = field(default_factory=_get_default_output_dir)

    # Parâmetros de plotagem
    dpi: int = 200
    figsize: tuple[int, int] = (16, 12)

    # Parâmetros de processamento
    smoothing_sigma: float = 1.5

    # ECMWF
    ecmwf_source: str = "ecmwf"  # ou "aws", "azure", "google"

    def __post_init__(self) -> None:
        """Valida parâmetros e garante que diretórios existam."""
        validate_extent(self.extent)

        self.data_dir = Path(self.data_dir) if self.data_dir else Path("data")
        self.output_dir = Path(self.output_dir) if self.output_dir else Path("output")

        # Tenta criar diretórios, mas não falha se não conseguir
        # (o erro será tratado no momento do download)
        # Será tratado no download
        with contextlib.suppress(PermissionError, OSError):
            self.data_dir.mkdir(parents=True, exist_ok=True)

        # Será tratado na exportação
        with contextlib.suppress(PermissionError, OSError):
            self.output_dir.mkdir(parents=True, exist_ok=True)

    # ─── Subdiretórios organizados (criados sob demanda) ──────────────────
    #
    # Cada tipo de arquivo vai para sua própria subpasta dentro de data_dir,
    # mantendo o diretório de dados organizado:
    #   grib/         GRIB2 do ECMWF (+ .idx)
    #   satelite/     imagens GOES-East
    #   tsm/          MUR SST (.nc)
    #   observacoes/  cache METAR/SYNOP + tabela de estações
    #   cartas/       cartas sinóticas salvas (PNG/PDF)

    def _subdir(self, name: str) -> Path:
        """Retorna (criando, se possível) um subdiretório dentro de data_dir."""
        path = self.data_dir / name
        # tratado no momento da escrita
        with contextlib.suppress(PermissionError, OSError):
            path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def grib_dir(self) -> Path:
        """Subpasta para arquivos GRIB2 do ECMWF."""
        return self._subdir("grib")

    @property
    def satellite_dir(self) -> Path:
        """Subpasta para imagens de satélite GOES-East."""
        return self._subdir("satelite")

    @property
    def sst_dir(self) -> Path:
        """Subpasta para dados de TSM (MUR SST)."""
        return self._subdir("tsm")

    @property
    def observations_dir(self) -> Path:
        """Subpasta para cache de observações (METAR/SYNOP) e tabela de estações."""
        return self._subdir("observacoes")

    @property
    def charts_dir(self) -> Path:
        """Subpasta para cartas sinóticas salvas (PNG/PDF)."""
        return self._subdir("cartas")

    @property
    def projects_dir(self) -> Path:
        """Subpasta para projetos de análise salvos (.cmbr) — trabalho do usuário."""
        return self._subdir("projetos")

    @property
    def loczcit_dir(self) -> Path:
        """Subpasta para os produtos do índice LOCZCIT-PA (NetCDF: raster + I_ZCIT)."""
        return self._subdir("loczcit_pa")

    @property
    def climatology_dir(self) -> Path:
        """Subpasta para a climatologia diária de Z500 (ERA5; cache re-baixável)."""
        return self._subdir("climatologia/z500")

    # Subpastas que contêm dados baixados/cache (limpáveis com segurança).
    # NÃO inclui 'cartas/', que guarda o trabalho do usuário.
    CACHE_SUBDIRS: ClassVar[tuple[str, ...]] = (
        "grib",
        "satelite",
        "tsm",
        "observacoes",
        "climatologia",
    )

    @classmethod
    def for_brazil(cls) -> Config:
        """Configuração otimizada para o Brasil."""
        return cls(extent=EXTENT_BRASIL)

    @classmethod
    def for_south_america(cls) -> Config:
        """Configuração para América do Sul completa."""
        return cls(extent=EXTENT_AMSUL)


# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTES DE ESTILO
# ═══════════════════════════════════════════════════════════════════════════════

# Cores padrão para campos meteorológicos
COLORS: dict[str, str] = {
    # PNMM
    "pnmm_contour": "#1A1A1A",
    "high_pressure": "#0066CC",
    "low_pressure": "#CC0000",
    # Espessura
    "thickness_warm": "#B2182B",
    "thickness_cold": "#2166AC",
    "thickness_5400": "#0571B0",
    # Frentes
    "cold_front": "#1a6faf",
    "warm_front": "#c0392b",
    "stationary_front": "#6a0dad",
    "occluded_front": "#8e44ad",
    # Zonas de convergência
    "zcas": "#008000",
    "zcit": "darkorange",
    # Outros
    "trough": "saddlebrown",
    "ridge": "#005500",
    "instability_line": "#8B0000",
    "dryline": "#b5651d",
    # Mapa base
    "ocean": "#E6F3FF",
    "land": "#F5F5F5",
    "coastline": "#333333",
    "borders": "#666666",
    "states": "#999999",
}

# Níveis padrão para contornos
LEVELS: dict[str, dict[str, int]] = {
    "pnmm": {"min": 960, "max": 1050, "step": 4},
    "thickness": {"min": 4900, "max": 5900, "step": 40},
}
