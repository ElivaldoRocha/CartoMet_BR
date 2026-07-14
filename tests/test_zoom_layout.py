"""Regressão do layout da mesa ao dar zoom — offscreen.

Bug: conteúdo ancorado em coordenadas de dados (emoji, desenhos, símbolos,
overlays) sai da vista ao dar zoom; como ``get_tightbbox`` ignora o clip, sua
caixa é projetada muito longe e ``_fit_layout_to_figure`` encolhia toda a carta
(largura caía de ~0,68 para ~0,12). O motor de layout deve medir só o quadro do
mapa + gridliner + título, ignorando o conteúdo.

Roda sob ``QT_QPA_PLATFORM=offscreen``; se o Qt não puder iniciar, é pulado.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("PyQt6")

import cartopy.crs as ccrs

from cartomet_br.core.config import EXTENT_AMSUL, Config


@pytest.fixture
def canvas(qapp, tmp_path):
    from cartomet_br.gui.map_canvas import MapCanvas

    data_dir = tmp_path / "data"
    out_dir = tmp_path / "out"
    data_dir.mkdir()
    out_dir.mkdir()
    cfg = Config(extent=EXTENT_AMSUL.copy(), data_dir=data_dir, output_dir=out_dir)
    try:
        return MapCanvas(config=cfg)
    except Exception as exc:  # noqa: BLE001 — ambiente sem render
        pytest.skip(f"MapCanvas não pôde ser criado offscreen: {exc}")


def _zoom_ratio(canvas):
    """Largura da carta após zoom fechado / largura base (deve ficar perto de 1)."""
    canvas._reflow_layout()
    base = canvas.ax.get_position().width
    # zoom bem fechado numa área LONGE do conteúdo colocado nos testes.
    canvas.ax.set_extent([-40, -37, -8, -5], crs=ccrs.PlateCarree())
    canvas._reflow_layout()
    zoomed = canvas.ax.get_position().width
    return zoomed / base, base, zoomed


def test_zoom_with_offscreen_emoji_does_not_shrink(canvas):
    # Emoji sobre a Bolívia; o zoom vai para o Nordeste (fora da vista).
    canvas.add_emoji(-60.0, -19.0, "CB", 28)
    canvas.add_emoji(-58.0, -21.0, "CB", 28)
    ratio, base, zoomed = _zoom_ratio(canvas)
    assert ratio >= 0.7, f"carta encolheu ao dar zoom (base={base:.3f} zoom={zoomed:.3f})"


def test_emoji_in_layout_restored_after_reflow(canvas):
    canvas.add_emoji(-60.0, -19.0, "CB", 28)
    canvas._reflow_layout()
    # O toggle de medição é temporário — o estado deve voltar a True.
    assert all(cmd.artist.get_in_layout() for cmd in canvas._emoji_records)


def test_clean_chart_zoom_stable(canvas):
    # Sem conteúdo, o zoom já era estável; garante que o fix não regride isso.
    ratio, base, zoomed = _zoom_ratio(canvas)
    assert ratio >= 0.7


def test_gridliner_kept_in_layout_measurement(canvas):
    """Regressão: ``ax._gridliners`` não existe no cartopy 0.25 — o Gridliner
    precisa ser achado por ``isinstance``, senão os rótulos de lat/lon saem da
    medição da mesa e podem cortar na borda sem correção."""
    from cartopy.mpl.gridliner import Gridliner

    keep = canvas._layout_keep_artists()
    assert any(isinstance(a, Gridliner) for a in keep), (
        "nenhum Gridliner na medição da mesa — mudança de API do cartopy?"
    )
    assert canvas.ax.title in keep


def _aviso(rings):
    from cartomet_br.data.inmet_avisos import AvisoINMET

    return AvisoINMET(
        severidade="Perigo",
        descricao="Tempestade",
        cor="#F96602",
        riscos=[],
        instrucoes=[],
        estados="PR",
        inicio="",
        fim="",
        quando="hoje",
        rings=rings,
    )


def test_inmet_overlay_out_of_layout(canvas):
    """Polígonos/rótulos de aviso fora da vista NÃO podem inflar o bbox 'tight'
    do export (get_tightbbox ignora o clip) — todos com in_layout=False."""
    ring = [(-53.0, -26.0), (-51.0, -26.0), (-51.0, -24.0), (-53.0, -24.0), (-53.0, -26.0)]
    canvas.render_inmet_avisos([_aviso([ring])])
    arts = canvas._inmet_avisos_artists
    assert arts
    assert all(a.get_in_layout() is False for a in arts)


def test_cells_overlay_out_of_layout(canvas):
    """Contornos/rótulos de células convectivas idem — fora da medição/export."""
    from cartomet_br.data.convective_cells import detect_convective_cells
    from cartomet_br.data.ecmwf import SatelliteData

    n = 60
    x = np.linspace(-2.0e6, 2.0e6, n)
    y = np.linspace(2.0e6, -2.0e6, n)  # descendente, como no GOES
    data = np.full((n, n), 15.0)
    data[20:40, 20:40] = -60.0
    canvas._sat_data = SatelliteData(
        data=data,
        x=x,
        y=y,
        sat_lon=-75.0,
        sat_h=35786023.0,
        sat_sweep="x",
        time_str="teste",
        filename="teste.nc",
    )
    result = detect_convective_cells(data, x, y, threshold_c=-50.0, min_area_km2=10.0)
    assert result.n_kept >= 1
    canvas.render_convective_cells(result, x, y)
    arts = canvas._convective_cells_artists
    assert arts
    assert all(a.get_in_layout() is False for a in arts)


def test_crop_satellite_returns_views(canvas):
    """O recorte roda no slot do clique (thread da GUI): tem que ser fatia
    contígua (view, sem cópia) — a imagem full-disk tem ~29 Mpx."""
    from cartomet_br.data.ecmwf import SatelliteData

    n = 200
    x = np.linspace(-5.0e6, 5.0e6, n)
    y = np.linspace(5.0e6, -5.0e6, n)
    data = np.full((n, n), 15.0)
    canvas._sat_data = SatelliteData(
        data=data,
        x=x,
        y=y,
        sat_lon=-75.0,
        sat_h=35786023.0,
        sat_sweep="x",
        time_str="teste",
        filename="teste.nc",
    )
    crop = canvas.crop_satellite_to_extent()
    assert crop is not None
    data_crop, x_crop, y_crop = crop
    assert data_crop.size > 0
    assert data_crop.base is not None, "recorte copiou a grade — deveria ser view"
