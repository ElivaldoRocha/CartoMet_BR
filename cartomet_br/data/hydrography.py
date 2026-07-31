"""Camada "Hidrografia" — rios e lagos principais da América do Sul.

Base de dados empacotada em ``cartomet_br/assets/hidrografia_sa.npz`` (gerada
por ``tools/gera_hidrografia_sa.py``; fontes HydroRIVERS © WWF / HydroSHEDS
License v1 e LakeATLAS CC-BY 4.0 — citações no ``meta`` do asset). Este módulo
é lógica pura, sem Qt/Matplotlib — o plot fica no ``MapCanvas``.

O nível "Principais" da camada nem passa por aqui (usa Natural Earth 50m via
cartopy); este módulo serve o nível "Detalhado".
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

# Níveis do seletor "Nível" da camada Hidrografia (espelho de
# CITY_DENSITY_FACTORS da camada Cidades): "Principais" = Natural Earth 50m
# (leve, coerente com o mapa base); "Detalhado" = HydroRIVERS/LakeATLAS
# embarcados, com espessura por ordem de Strahler.
HYDRO_DETAIL_LEVELS: tuple[str, str] = ("Principais", "Detalhado")
DEFAULT_HYDRO_DETAIL = "Principais"

# Espessura do traço por ordem de Strahler (estilo do mapa de referência do
# autor: afluentes finos, tronco do Amazonas destacado).
RIVER_LINEWIDTHS: dict[int, float] = {5: 0.35, 6: 0.55, 7: 0.8, 8: 1.1, 9: 1.5, 10: 2.0}


@dataclass(frozen=True)
class Hydrography:
    """Geometrias do nível "Detalhado", prontas para LineCollection/PolyCollection."""

    river_lines: tuple[np.ndarray, ...]  # cada uma (Ni, 2) float32 lon/lat
    river_orders: np.ndarray  # uint8 (Nr,) — ordem de Strahler por linha
    lake_rings: tuple[np.ndarray, ...]  # cada uma (Ni, 2) float32, anel fechado


def _assets_path() -> Path:
    """Caminho dos assets em dev e no executável PyInstaller.

    Réplica local de ``gui._constants.get_assets_path`` — importá-la daqui
    criaria ciclo (gui/__init__ → layer_panel → data.hydrography → gui). A
    camada de dados não importa da GUI; manter as duas em sincronia (mesma
    doutrina de ``data/cities.py``).
    """
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]  # PyInstaller (runtime frozen)
    else:
        base = Path(__file__).resolve().parents[1]
    return base / "assets"


def _split(coords: np.ndarray, offsets: np.ndarray) -> tuple[np.ndarray, ...]:
    """Fatia o array concatenado em views por polilinha/anel (sem cópia)."""
    return tuple(np.split(coords, offsets[1:-1]))


@lru_cache(maxsize=1)
def load_hydrography() -> Hydrography:
    """Carrega o asset empacotado (o array ``meta`` é só proveniência)."""
    with np.load(_assets_path() / "hidrografia_sa.npz") as data:
        return Hydrography(
            river_lines=_split(data["rios_coords"], data["rios_offsets"]),
            river_orders=data["rios_ordem"].copy(),
            lake_rings=_split(data["lagos_coords"], data["lagos_offsets"]),
        )


def river_linewidths(orders: np.ndarray) -> np.ndarray:
    """Espessura de traço por rio, vetorizada; ordens fora de 5..10 são clampadas."""
    lut = np.zeros(11, dtype=np.float64)
    for ordem, largura in RIVER_LINEWIDTHS.items():
        lut[ordem] = largura
    return lut[np.clip(orders.astype(np.int64), 5, 10)]
