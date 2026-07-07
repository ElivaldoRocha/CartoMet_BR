"""Gerador DEV-ONLY do asset ``cartomet_br/assets/cidades_br.csv``.

Roda FORA do aplicativo (não é empacotado; sem dependências novas — só stdlib).
Produz a base da camada "Cidades" do mapa: sedes municipais com nome, UF,
coordenadas, população e flag de capital.

Fontes:
- Coordenadas das sedes + flag capital: dataset compilado do IBGE
  (github.com/kelvins/municipios-brasileiros, dados públicos IBGE).
- População: API de agregados do IBGE (agregado 6579 — população residente
  estimada, período mais recente).

Critério de corte: capitais SEMPRE entram; demais municípios entram com
população >= POP_MIN. O limiar 12 mil cobre as cidades dos mapas operacionais
regionais (ex.: Costa Marques e Cerejeiras/RO, do mapa SIPAM que motivou a
feature) mantendo o asset pequeno (< ~150 KB).

Uso:  uv run python tools/gera_cidades_ibge.py
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import sys
import urllib.request
from pathlib import Path

POP_MIN = 12_000

URL_MUNICIPIOS = (
    "https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/municipios.csv"
)
URL_ESTADOS = (
    "https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/estados.csv"
)
# Agregado 6579 = população residente estimada; -1 = período mais recente
URL_POPULACAO = (
    "https://servicodados.ibge.gov.br/api/v3/agregados/6579/"
    "periodos/-1/variaveis/9324?localidades=N6[all]"
)

DEST = Path(__file__).resolve().parents[1] / "cartomet_br" / "assets" / "cidades_br.csv"


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "CartoMetBR-dev-tool"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw: bytes = resp.read()
    # A API do IBGE às vezes entrega gzip mesmo sem Accept-Encoding (magic 1f 8b)
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw


def main() -> int:
    print("Baixando estados (codigo_uf -> sigla)...")
    estados = csv.DictReader(io.StringIO(_fetch(URL_ESTADOS).decode("utf-8-sig")))
    uf_por_codigo = {row["codigo_uf"]: row["uf"] for row in estados}
    if len(uf_por_codigo) != 27:
        print(f"ERRO: esperava 27 UFs, veio {len(uf_por_codigo)}", file=sys.stderr)
        return 1

    print("Baixando municípios (sedes + capital)...")
    municipios = list(csv.DictReader(io.StringIO(_fetch(URL_MUNICIPIOS).decode("utf-8-sig"))))
    print(f"  {len(municipios)} municípios")

    print("Baixando população estimada (IBGE agregados 6579)...")
    agregados = json.loads(_fetch(URL_POPULACAO))
    pop_por_codigo: dict[str, int] = {}
    for serie in agregados[0]["resultados"][0]["series"]:
        valores = serie["serie"]
        # período mais recente do dict {ano: valor}
        valor = valores[max(valores)]
        with_id = serie["localidade"]["id"]
        try:
            pop_por_codigo[with_id] = int(valor)
        except (TypeError, ValueError):
            continue  # "..." = sem dado
    print(f"  população para {len(pop_por_codigo)} municípios")

    linhas: list[tuple[str, str, float, float, int, int]] = []
    sem_pop = 0
    for m in municipios:
        codigo = m["codigo_ibge"]
        capital = int(m["capital"])
        pop = pop_por_codigo.get(codigo)
        if pop is None:
            sem_pop += 1
            pop = 0
        if not capital and pop < POP_MIN:
            continue
        linhas.append(
            (
                m["nome"],
                uf_por_codigo[m["codigo_uf"]],
                round(float(m["latitude"]), 4),
                round(float(m["longitude"]), 4),
                pop,
                capital,
            )
        )
    # Determinístico: UF, depois população decrescente, depois nome
    linhas.sort(key=lambda r: (r[1], -r[4], r[0]))

    capitais = sum(1 for r in linhas if r[5])
    print(f"  selecionadas {len(linhas)} cidades ({capitais} capitais; {sem_pop} sem população)")
    if capitais != 27:
        print(f"ERRO: esperava 27 capitais, veio {capitais}", file=sys.stderr)
        return 1

    DEST.parent.mkdir(parents=True, exist_ok=True)
    with DEST.open("w", encoding="utf-8", newline="") as f:
        f.write("# Sedes municipais p/ camada 'Cidades' — gerado por tools/gera_cidades_ibge.py\n")
        f.write(f"# Fontes: IBGE (coordenadas/capital + agregado 6579). Corte: pop>={POP_MIN}\n")
        w = csv.writer(f)
        w.writerow(["nome", "uf", "lat", "lon", "populacao", "capital"])
        w.writerows(linhas)
    print(f"OK: {DEST} ({DEST.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
