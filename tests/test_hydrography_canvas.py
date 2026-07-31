"""Camada Hidrografia no MapCanvas — toggles, nível, tema e extent (offscreen).

Usa o nível "Detalhado" (asset embarcado) para não depender do cache Natural
Earth da máquina; o nível "Principais" tem um teste próprio com skip quando o
cache local não tem os shapefiles de rios.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6")


def _liga_detalhado(canvas):
    canvas.set_hydrography_detail("Detalhado")
    canvas.set_hydrography_visible(True)
    assert canvas._hydro_artists, "camada ligada deveria criar artistas"
    return list(canvas._hydro_artists)


def test_toggle_cria_e_remove_artistas(canvas):
    artistas = _liga_detalhado(canvas)
    assert all(a.axes is canvas.ax for a in artistas)
    canvas.set_hydrography_visible(False)
    assert not canvas._hydro_artists
    assert all(a.axes is None for a in artistas)  # removidos do eixo, não só ocultos


def test_troca_de_nivel_recria_artistas(canvas):
    antes = _liga_detalhado(canvas)
    canvas.set_hydrography_detail("Detalhado")  # nível igual: no-op
    assert canvas._hydro_artists == antes


def test_nivel_invalido_e_ignorado(canvas):
    canvas.set_hydrography_detail("Ultra HD")
    assert canvas._hydro_detail == "Principais"  # default intacto


def test_troca_de_tema_preserva_camada(canvas):
    antes = _liga_detalhado(canvas)
    canvas.set_theme("Escuro")  # reconstrói o mapa base (ax.clear)
    assert canvas._hydro_enabled
    assert canvas._hydro_artists, "camada deveria renascer com o novo tema"
    assert canvas._hydro_artists[0] is not antes[0]  # artistas novos, flag antiga


def test_apply_extent_nao_destroi_artistas(canvas):
    antes = _liga_detalhado(canvas)
    canvas.apply_extent([-60.0, -20.0, -40.0, 0.0])
    assert canvas._hydro_artists == antes  # persistentes: recorte é do matplotlib


def test_clear_map_preserva_camada(canvas):
    antes = _liga_detalhado(canvas)
    canvas.clear_map()
    assert canvas._hydro_enabled
    assert canvas._hydro_artists == antes  # contexto de base não é "camada de dado"


def _cache_ne_tem_rios() -> bool:
    base = Path.home() / ".local" / "share" / "cartopy" / "shapefiles" / "natural_earth"
    return any(base.rglob("ne_50m_rivers_lake_centerlines.shp"))


@pytest.mark.skipif(not _cache_ne_tem_rios(), reason="cache Natural Earth local sem rios 50m")
def test_nivel_principais_com_cache_local(canvas):
    canvas.set_hydrography_visible(True)  # default "Principais"
    assert len(canvas._hydro_artists) == 3  # lagos fill + contorno + rios
    canvas.set_hydrography_visible(False)
    assert not canvas._hydro_artists
