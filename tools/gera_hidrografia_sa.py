"""Gerador DEV-ONLY do asset ``cartomet_br/assets/hidrografia_sa.npz``.

Roda FORA do aplicativo (não é empacotado; sem dependências novas — pyshp,
shapely e numpy já vêm com o cartopy/stack científica do venv). Produz a base
do nível "Detalhado" da camada "Hidrografia": rios da América do Sul com ordem
de Strahler >= 5 e lagos com área >= 10 km².

Fontes (arquivos locais do autor — baixados de hydrosheds.org):
- Rios:  HydroRIVERS v1.0 (subset já filtrado ORD_STRA > 4; usar ESTE, não o
  shapefile completo de 202 MB).
- Lagos: LakeATLAS v1.0, hemisfério oeste (AmSul inteira tem lon < 0). Lido
  com ``iterShapes(bbox=...)`` — NUNCA toca o .dbf de 1,9 GB.

Licenças/atribuição (replicadas no array ``meta`` do asset e no Sobre do app):
- HydroRIVERS © World Wildlife Fund, Inc. — HydroSHEDS License v1 (uso livre
  científico/educacional/comercial COM atribuição). Citação: Lehner, B. &
  Grill, G. (2013): Global river hydrography and network routing: baseline
  data and new approaches to study the world's large river systems.
  Hydrological Processes 27(15): 2171-2186.
- LakeATLAS — CC-BY 4.0. Citação: Lehner, B., Messager, M.L., Korver, M.C.,
  Linke, S. (2022): Global hydro-environmental lake characteristics at high
  spatial resolution. Scientific Data 9: 351.

Critérios de corte: Strahler >= 5 (herdado do subset), lago >= AREA_MIN_KM2,
simplificação ~400 m (SIMPLIFY_TOL_DEG) — invisível até zoom de estado.
Saída determinística no CONTEÚDO (ordenação estável; sem timestamp no meta);
o hash impresso ao final permite comparar duas execuções (o .npz em si carrega
timestamps de zip, então bytes do arquivo podem diferir).

Schema do .npz:
- rios_coords  float32 (Nv, 2)  lon/lat de todas as polilinhas concatenadas
- rios_offsets int32   (Nr+1,)  polilinha i = rios_coords[offsets[i]:offsets[i+1]]
- rios_ordem   uint8   (Nr,)    ordem de Strahler (5..10) por polilinha
- lagos_coords float32 (Nl_v,2) anéis exteriores concatenados
- lagos_offsets int32  (Nl+1,)
- meta         uint8           JSON utf-8 (fontes, filtros, licenças)

Uso:  uv run python tools/gera_hidrografia_sa.py
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import shapefile  # pyshp (transitiva do cartopy)
from shapely import MultiLineString, Polygon, simplify
from shapely.ops import linemerge

SRC_RIOS = Path(
    r"F:\geoBr_Shapefiles\HydroRIVERS_v10_sa_shp\HydroRIVERS_v10_sa_Order_Straler_maior_4.shp"
)
SRC_LAGOS = Path(r"F:\GloH2O\HydroSHEDS\Lake\LakeATLAS_v10_shp\LakeATLAS_v10_pol_west.shp")
DEST = Path(__file__).resolve().parents[1] / "cartomet_br" / "assets" / "hidrografia_sa.npz"

# América do Sul inteira (lon_min, lat_min, lon_max, lat_max) — toda com lon < 0
BBOX_AMSUL = (-93.0, -56.5, -32.0, 13.5)
AREA_MIN_KM2 = 10.0
SIMPLIFY_TOL_DEG = 0.004  # ~400 m no equador — invisível até zoom de estado
MAX_ASSET_MB = 8.0
KM_POR_GRAU = 111.195  # raio médio da Terra

ORDENS_VALIDAS = range(5, 11)

META = {
    "versao": 1,
    "cobertura": "America do Sul",
    "bbox": BBOX_AMSUL,
    "filtros": {
        "strahler_min": 5,
        "area_min_km2": AREA_MIN_KM2,
        "simplify_tol_deg": SIMPLIFY_TOL_DEG,
    },
    "fontes": [
        {
            "nome": "HydroRIVERS v1.0",
            "licenca": "HydroSHEDS License v1 — (C) World Wildlife Fund, Inc.",
            "citacao": (
                "Lehner, B. & Grill, G. (2013): Global river hydrography and network "
                "routing: baseline data and new approaches to study the world's large "
                "river systems. Hydrological Processes 27(15): 2171-2186."
            ),
        },
        {
            "nome": "LakeATLAS v1.0",
            "licenca": "CC-BY 4.0",
            "citacao": (
                "Lehner, B., Messager, M.L., Korver, M.C., Linke, S. (2022): Global "
                "hydro-environmental lake characteristics at high spatial resolution. "
                "Scientific Data 9: 351."
            ),
        },
    ],
    "gerador": "tools/gera_hidrografia_sa.py",
}


def _shoelace_km2(pontos: np.ndarray) -> float:
    """Área aproximada (km²) de um anel lon/lat pelo shoelace em graus.

    Dispensa o .dbf gigante do LakeATLAS: 1° lon ~ 111,195·cos(lat) km e
    1° lat ~ 111,195 km — aproximação plana suficiente para um corte de 10 km².
    """
    lon = pontos[:, 0]
    lat = pontos[:, 1]
    area_graus2 = 0.5 * abs(float(np.dot(lon, np.roll(lat, -1)) - np.dot(lat, np.roll(lon, -1))))
    lat_media = math.radians(float(lat.mean()))
    return area_graus2 * KM_POR_GRAU * KM_POR_GRAU * math.cos(lat_media)


def _carrega_rios() -> tuple[list[np.ndarray], list[int]]:
    """Lê o subset HydroRIVERS, funde segmentos por ordem e simplifica."""
    print(f"Lendo rios de {SRC_RIOS.name}...")
    reader = shapefile.Reader(str(SRC_RIOS))
    campos = [f[0] for f in reader.fields[1:]]  # [0] é o DeletionFlag
    idx_ordem = campos.index("ORD_STRA")

    por_ordem: dict[int, list[list[tuple[float, float]]]] = {o: [] for o in ORDENS_VALIDAS}
    descartados = 0
    for sr in reader.iterShapeRecords():
        ordem = int(sr.record[idx_ordem])
        if ordem not in por_ordem:
            descartados += 1
            continue
        por_ordem[ordem].append(sr.shape.points)
    reader.close()
    total_seg = sum(len(v) for v in por_ordem.values())
    print(f"  {total_seg} segmentos (ordens 5..10; {descartados} fora da faixa)")

    linhas: list[np.ndarray] = []
    ordens: list[int] = []
    for ordem in ORDENS_VALIDAS:
        segmentos = por_ordem[ordem]
        if not segmentos:
            continue
        fundido = linemerge(MultiLineString(segmentos))
        geoms = getattr(fundido, "geoms", [fundido])  # LineString único ou Multi
        caminhos = [simplify(g, SIMPLIFY_TOL_DEG) for g in geoms]
        # Determinístico: lon do 1º vértice, depois lat (ordem do loop já é crescente)
        caminhos.sort(key=lambda g: (g.coords[0][0], g.coords[0][1]))
        for g in caminhos:
            arr = np.round(np.asarray(g.coords, dtype=np.float64), 4).astype(np.float32)
            if len(arr) < 2:
                continue
            linhas.append(arr)
            ordens.append(ordem)
        print(f"  ordem {ordem}: {len(segmentos)} segmentos -> {len(caminhos)} caminhos")
    return linhas, ordens


def _carrega_lagos() -> list[np.ndarray]:
    """Lê os anéis exteriores do LakeATLAS no bbox, filtra por área e simplifica."""
    print(f"Lendo lagos de {SRC_LAGOS.name} (bbox AmSul, sem .dbf)...")
    reader = shapefile.Reader(str(SRC_LAGOS))
    candidatos: list[tuple[float, np.ndarray]] = []
    varridos = 0
    for shp in reader.iterShapes(bbox=BBOX_AMSUL):
        varridos += 1
        partes = list(shp.parts) + [len(shp.points)]
        exterior = np.asarray(shp.points[partes[0] : partes[1]], dtype=np.float64)
        if len(exterior) < 4:
            continue
        area = _shoelace_km2(exterior)
        if area < AREA_MIN_KM2:
            continue
        anel = simplify(Polygon(exterior), SIMPLIFY_TOL_DEG)
        coords = np.round(np.asarray(anel.exterior.coords, dtype=np.float64), 4)
        if len(coords) < 4:
            continue
        candidatos.append((area, coords.astype(np.float32)))
    reader.close()
    # Determinístico: área decrescente, desempate por lon/lat do 1º vértice
    candidatos.sort(key=lambda c: (-c[0], float(c[1][0, 0]), float(c[1][0, 1])))
    print(f"  {varridos} lagos no bbox -> {len(candidatos)} com area >= {AREA_MIN_KM2} km2")
    return [coords for _, coords in candidatos]


def _concatena(linhas: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    coords = np.concatenate(linhas).astype(np.float32)
    tamanhos = np.array([len(a) for a in linhas], dtype=np.int64)
    offsets = np.concatenate([[0], np.cumsum(tamanhos)]).astype(np.int32)
    return coords, offsets


def _valida(
    rios_coords: np.ndarray,
    rios_offsets: np.ndarray,
    rios_ordem: np.ndarray,
    lagos_coords: np.ndarray,
    lagos_offsets: np.ndarray,
) -> list[str]:
    erros: list[str] = []
    for nome, coords, offsets in (
        ("rios", rios_coords, rios_offsets),
        ("lagos", lagos_coords, lagos_offsets),
    ):
        if np.isnan(coords).any():
            erros.append(f"{nome}: coordenadas NaN")
        if not (np.diff(offsets) > 0).all():
            erros.append(f"{nome}: offsets nao sao estritamente crescentes")
        if offsets[0] != 0 or offsets[-1] != len(coords):
            erros.append(f"{nome}: offsets nao cobrem o array de coordenadas")
        lon_min, lat_min, lon_max, lat_max = BBOX_AMSUL
        if not (
            (coords[:, 0] >= lon_min).all()
            and (coords[:, 0] <= lon_max).all()
            and (coords[:, 1] >= lat_min).all()
            and (coords[:, 1] <= lat_max).all()
        ):
            erros.append(f"{nome}: coordenadas fora do bbox AmSul")

    if not set(np.unique(rios_ordem)).issubset(set(ORDENS_VALIDAS)):
        erros.append(f"ordens de Strahler invalidas: {sorted(np.unique(rios_ordem))}")
    if 10 not in rios_ordem:
        erros.append("nenhum rio de ordem 10 (o Amazonas sumiu?)")
    n_lagos = len(lagos_offsets) - 1
    if not 500 <= n_lagos <= 5000:
        erros.append(f"numero de lagos suspeito: {n_lagos} (esperado 500..5000)")

    # Lagos-sentinela: centroide (média dos vértices) a menos de 0,5° do esperado
    sentinelas = {"Titicaca": (-69.3, -15.8), "Lagoa dos Patos": (-51.2, -31.0)}
    n = len(lagos_offsets) - 1
    centroides = np.array(
        [lagos_coords[lagos_offsets[i] : lagos_offsets[i + 1]].mean(axis=0) for i in range(n)]
    )
    for nome_lago, (lon, lat) in sentinelas.items():
        dist = np.abs(centroides - [lon, lat]).max(axis=1)
        if not (dist < 0.5).any():
            erros.append(f"lago-sentinela ausente: {nome_lago} (~{lon}, {lat})")
    return erros


def main() -> int:
    for src in (SRC_RIOS, SRC_LAGOS):
        if not src.exists():
            print(f"ERRO: fonte nao encontrada: {src}", file=sys.stderr)
            return 1

    linhas, ordens = _carrega_rios()
    aneis = _carrega_lagos()

    rios_coords, rios_offsets = _concatena(linhas)
    rios_ordem = np.array(ordens, dtype=np.uint8)
    lagos_coords, lagos_offsets = _concatena(aneis)

    erros = _valida(rios_coords, rios_offsets, rios_ordem, lagos_coords, lagos_offsets)
    if erros:
        for e in erros:
            print(f"ERRO: {e}", file=sys.stderr)
        return 1

    meta = np.frombuffer(json.dumps(META, ensure_ascii=False).encode("utf-8"), dtype=np.uint8)
    DEST.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        DEST,
        rios_coords=rios_coords,
        rios_offsets=rios_offsets,
        rios_ordem=rios_ordem,
        lagos_coords=lagos_coords,
        lagos_offsets=lagos_offsets,
        meta=meta,
    )

    tamanho_mb = DEST.stat().st_size / 1024 / 1024
    if tamanho_mb >= MAX_ASSET_MB:
        print(f"ERRO: asset com {tamanho_mb:.1f} MB (teto {MAX_ASSET_MB} MB)", file=sys.stderr)
        return 1

    conteudo = hashlib.sha256()
    for arr in (rios_coords, rios_offsets, rios_ordem, lagos_coords, lagos_offsets, meta):
        conteudo.update(arr.tobytes())
    print(
        f"OK: {DEST} ({tamanho_mb:.2f} MB; {len(rios_ordem)} rios, "
        f"{len(lagos_offsets) - 1} lagos; sha256 do conteudo {conteudo.hexdigest()[:16]})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
