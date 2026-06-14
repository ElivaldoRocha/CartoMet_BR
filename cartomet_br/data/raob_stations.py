"""
Estações de radiossondagem (RAOB) operacionais — CartoMet BR v3.0.

Lista curada das estações que **efetivamente lançam radiossonda** e respondem no
servidor da Universidade de Wyoming (siphon.simplewebservice.wyoming). Diferente da
tabela WMO completa (`stations._load_wmo_coords`), que inclui ~11k estações de
superfície, esta lista é a "régua calibrada" do snap da Sonda Vertical: ancorar o
clique do usuário apenas em estações de altitude evita "cliques mortos" — pontos onde
o Wyoming retornaria vazio por não haver perfil vertical.

Códigos WMO de 5 dígitos (BBSSS). Foco em Brasil / América do Sul / Atlântico tropical,
a região operacional do CartoMet BR.
"""

from __future__ import annotations

import math

# Cada estação: código WMO (string de 5 dígitos), nome amigável, latitude, longitude.
# Conjunto operacional do INMET/rede sul-americana atendido pelo Wyoming UpperAir.
RAOB_STATIONS: list[dict] = [
    # — Norte / Amazônia / Atlântico equatorial —
    {"wmo": "82022", "name": "Boa Vista", "lat": 2.83, "lon": -60.70},
    {"wmo": "82332", "name": "Manaus (Ponta Pelada)", "lat": -3.15, "lon": -59.98},
    {"wmo": "82193", "name": "Belém", "lat": -1.38, "lon": -48.48},
    {"wmo": "82281", "name": "São Luís", "lat": -2.53, "lon": -44.30},
    {"wmo": "82099", "name": "Macapá", "lat": 0.05, "lon": -51.07},
    {"wmo": "82397", "name": "Fortaleza", "lat": -3.72, "lon": -38.55},
    {"wmo": "82599", "name": "Natal", "lat": -5.91, "lon": -35.25},
    {"wmo": "82400", "name": "Fernando de Noronha", "lat": -3.85, "lon": -32.41},
    # — Nordeste / Centro —
    {"wmo": "82900", "name": "Petrolina", "lat": -9.38, "lon": -40.48},
    {"wmo": "83096", "name": "Salvador", "lat": -12.91, "lon": -38.33},
    {"wmo": "83229", "name": "Vilhena", "lat": -12.73, "lon": -60.13},
    {"wmo": "83362", "name": "Cuiabá", "lat": -15.65, "lon": -56.10},
    {"wmo": "83378", "name": "Brasília", "lat": -15.87, "lon": -47.93},
    {"wmo": "83612", "name": "Campo Grande", "lat": -20.47, "lon": -54.67},
    # — Sudeste / Sul —
    {"wmo": "83746", "name": "Rio de Janeiro (Galeão)", "lat": -22.82, "lon": -43.25},
    {"wmo": "83779", "name": "São Paulo (Campo de Marte)", "lat": -23.50, "lon": -46.62},
    {"wmo": "83827", "name": "Curitiba", "lat": -25.52, "lon": -49.17},
    {"wmo": "83840", "name": "Florianópolis", "lat": -27.67, "lon": -48.55},
    {"wmo": "83971", "name": "Porto Alegre", "lat": -30.00, "lon": -51.18},
    {"wmo": "83899", "name": "Santa Maria", "lat": -29.70, "lon": -53.70},
]


def nearest_raob(lon: float, lat: float) -> dict | None:
    """Retorna a estação RAOB mais próxima de (lon, lat), ou None se a lista vazia.

    Usa distância euclidiana simples em graus — suficiente e barato na escala
    regional do CartoMet (sem grandes deformações de projeção em latitudes baixas).
    O dicionário retornado é uma cópia, acrescido de ``distance_deg`` (graus) para o
    chamador decidir se o clique foi "longe demais" de qualquer estação.
    """
    if not RAOB_STATIONS:
        return None

    best: dict | None = None
    best_d2 = math.inf
    for st in RAOB_STATIONS:
        d2 = (st["lon"] - lon) ** 2 + (st["lat"] - lat) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best = st

    if best is None:
        return None
    result = dict(best)
    result["distance_deg"] = math.sqrt(best_d2)
    return result
