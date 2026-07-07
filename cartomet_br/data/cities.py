"""Camada "Cidades" — sedes municipais para orientação em mapas regionais.

Base de dados empacotada em ``cartomet_br/assets/cidades_br.csv`` (gerada por
``tools/gera_cidades_ibge.py``; fontes IBGE). Este módulo é lógica pura, sem
Qt/Matplotlib — o plot fica no ``MapCanvas``.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from cartomet_br.gui._constants import get_assets_path


@dataclass(frozen=True)
class City:
    """Sede municipal com os atributos usados no rótulo e no thinning."""

    name: str
    uf: str
    lat: float
    lon: float
    population: int
    is_capital: bool


@lru_cache(maxsize=1)
def load_cities() -> tuple[City, ...]:
    """Carrega o asset empacotado (linhas iniciadas em ``#`` são proveniência)."""
    path = get_assets_path() / "cidades_br.csv"
    cities: list[City] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(line for line in f if not line.startswith("#")):
            cities.append(
                City(
                    name=row["nome"],
                    uf=row["uf"],
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                    population=int(row["populacao"]),
                    is_capital=row["capital"] == "1",
                )
            )
    return tuple(cities)


def select_cities(
    cities: Sequence[City],
    extent: Sequence[float],
    max_labels: int = 14,
) -> list[City]:
    """Escolhe as cidades a rotular no extent ``[lon_min, lat_min, lon_max, lat_max]``.

    Greedy determinístico: filtra pelo extent (com margem interna de ~3% para
    o rótulo não colar na moldura), prioriza capital > população > nome e impõe
    separação mínima proporcional à largura do domínio — mesma ideia do
    ``thinning_radius`` das observações de superfície. No Brasil inteiro sobram
    as capitais; num estado, as cidades médias entram sozinhas.
    """
    lon_min, lat_min, lon_max, lat_max = (float(v) for v in extent)
    width = lon_max - lon_min
    height = lat_max - lat_min
    margin_x = width * 0.03
    margin_y = height * 0.03
    candidates = [
        c
        for c in cities
        if lon_min + margin_x <= c.lon <= lon_max - margin_x
        and lat_min + margin_y <= c.lat <= lat_max - margin_y
    ]
    candidates.sort(key=lambda c: (not c.is_capital, -c.population, c.name))

    min_sep = max(width / 10.0, 0.25)
    chosen: list[City] = []
    for cand in candidates:
        if len(chosen) >= max_labels:
            break
        if all(max(abs(cand.lon - c.lon), abs(cand.lat - c.lat)) >= min_sep for c in chosen):
            chosen.append(cand)
    return chosen
