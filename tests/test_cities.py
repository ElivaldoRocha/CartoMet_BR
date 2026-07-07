"""Camada "Cidades": asset empacotado + seleção determinística (thinning)."""

from cartomet_br.core.config import EXTENT_BRASIL, EXTENT_UFS
from cartomet_br.data.cities import City, load_cities, select_cities


def _mk(name, lon, lat, pop=10_000, capital=False, uf="XX"):
    return City(name=name, uf=uf, lat=lat, lon=lon, population=pop, is_capital=capital)


# ── Asset empacotado ─────────────────────────────────────────────────────────


def test_asset_parseia_e_tem_volume_esperado():
    cities = load_cities()
    assert len(cities) > 2000


def test_asset_27_capitais_uma_por_uf():
    capitals = [c for c in load_cities() if c.is_capital]
    assert len(capitals) == 27
    assert len({c.uf for c in capitals}) == 27


def test_asset_coordenadas_no_brasil():
    for c in load_cities():
        assert -34.5 <= c.lat <= 5.5, f"{c.name}/{c.uf}: lat {c.lat}"
        assert -74.5 <= c.lon <= -28.0, f"{c.name}/{c.uf}: lon {c.lon}"


def test_asset_sem_duplicatas():
    cities = load_cities()
    assert len({(c.name, c.uf) for c in cities}) == len(cities)


def test_asset_cobre_cidades_do_mapa_sipam_rondonia():
    # Caso que motivou a feature: mapa operacional do SIPAM p/ Rondônia
    nomes_ro = {c.name for c in load_cities() if c.uf == "RO"}
    for nome in [
        "Porto Velho",
        "Ariquemes",
        "Ji-Paraná",
        "Cacoal",
        "Vilhena",
        "Guajará-Mirim",
        "Rolim de Moura",
        "Costa Marques",
        "Cerejeiras",
    ]:
        assert nome in nomes_ro, f"{nome} ausente do asset"


# ── select_cities ────────────────────────────────────────────────────────────


def test_select_respeita_max_labels():
    cities = [_mk(f"c{i}", lon=-60 + i, lat=-10) for i in range(30)]
    out = select_cities(cities, [-75, -35, -30, 6], max_labels=5)
    assert len(out) == 5


def test_select_prioriza_capital_depois_populacao():
    cities = [
        _mk("Grande", -50, -10, pop=1_000_000),
        _mk("Capital", -40, -10, pop=50_000, capital=True),
        _mk("Media", -60, -10, pop=200_000),
    ]
    out = select_cities(cities, [-75, -35, -30, 6], max_labels=3)
    assert [c.name for c in out] == ["Capital", "Grande", "Media"]


def test_select_filtra_extent_com_margem():
    dentro = _mk("Dentro", -50, -10)
    fora = _mk("Fora", -20, -10)
    na_borda = _mk("Borda", -30.05, -10)  # dentro, mas na margem de 3% da moldura
    out = select_cities([dentro, fora, na_borda], [-75, -35, -30, 6])
    assert [c.name for c in out] == ["Dentro"]


def test_select_impoe_separacao_minima():
    # Domínio de 45° de largura → separação mínima 4.5°; vizinhas a 1° colidem
    a = _mk("A", -50.0, -10.0, pop=500_000)
    b = _mk("B", -49.0, -10.0, pop=400_000)
    c = _mk("C", -40.0, -10.0, pop=300_000)
    out = select_cities([a, b, c], [-75, -35, -30, 6])
    assert [x.name for x in out] == ["A", "C"]


def test_select_deterministico():
    cities = load_cities()
    extent = EXTENT_UFS["RO"]
    assert select_cities(cities, extent) == select_cities(cities, extent)


def test_select_brasil_mostra_capitais():
    out = select_cities(load_cities(), EXTENT_BRASIL)
    assert out, "nenhuma cidade selecionada no Brasil"
    assert all(c.is_capital for c in out[: min(5, len(out))]), (
        "no Brasil inteiro as primeiras escolhas devem ser capitais"
    )


def test_select_rondonia_inclui_porto_velho_e_interior():
    out = select_cities(load_cities(), EXTENT_UFS["RO"])
    nomes = [c.name for c in out]
    assert "Porto Velho" in nomes
    assert len(nomes) >= 8, f"esperava um recorte estadual povoado, veio {nomes}"
