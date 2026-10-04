"""Motor de composição do globo (services/globe_compose.py) — puro, offline.

O contrato central: a textura do globo usa EXATAMENTE as cores de banda que o
``contourf`` da carta atribuiria (teste-verdade contra um ContourSet real), e
a releitura global nunca toca a rede (cache-only; fallback regional honesto).
"""

import numpy as np
import pytest

from cartomet_br.data.ecmwf import PLFieldData
from cartomet_br.services.field_style import derive_scalar_style
from cartomet_br.services.globe_compose import (
    GLOBE_EXTENT,
    band_colors,
    composite_over,
    rasterize_layer,
    reload_global,
)

SHAPE_TESTE = (91, 180)  # 2° — rápido; a produção usa (721, 1440)


def _field(values, lons, lats, variable="t", level=850, unit="°C", source="ifs", **kw):
    return PLFieldData(
        values=np.asarray(values, dtype=float),
        lons=np.asarray(lons, dtype=float),
        lats=np.asarray(lats, dtype=float),
        variable=variable,
        level=level,
        unit=unit,
        source=source,
        **kw,
    )


def _global_field(variable="t", unit="°C", fill=None):
    lats = np.linspace(90, -90, 91)
    lons = np.linspace(-180, 178, 180)
    lo, la = np.meshgrid(lons, lats)
    vals = 10 + 10 * np.cos(np.radians(la)) if fill is None else np.full(lo.shape, fill)
    return _field(vals, lons, lats, variable=variable, unit=unit)


class TestBandColorsVerdade:
    @pytest.mark.parametrize("extend", ["both", "max"])
    def test_identicas_ao_contourf_real(self, extend):
        # Verdade: um ContourSet de verdade com os mesmos níveis/cmap/extend.
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        levels = [0.0, 1.0, 2.0, 5.0, 10.0]  # irregulares de propósito
        style = derive_scalar_style("t", "°C", np.zeros((3, 3)), {"cmap": "viridis"})
        style = type(style)(levels=levels, cmap="viridis", extend=extend)

        fig, ax = plt.subplots()
        grid = np.array([[-1.0, 0.5, 1.5], [3.0, 7.0, 12.0], [0.2, 4.0, 9.0]])
        cs = ax.contourf(grid, levels=levels, cmap="viridis", extend=extend)
        truth = cs.to_rgba(cs.cvalues)
        plt.close(fig)

        interior, under, over = band_colors(style)
        mine = []
        if under is not None:
            mine.append(under)
        mine.extend(interior)
        if over is not None:
            mine.append(over)
        assert np.allclose(np.asarray(mine), np.asarray(truth), atol=1 / 255), (
            f"cores de banda divergem do contourf (extend={extend})"
        )


class TestRasterize:
    def test_grade_global_identidade_e_nan_transparente(self):
        data = _global_field()
        data.values[0, 0] = np.nan
        style = derive_scalar_style("t", "°C", data.values, {"cmap": "viridis"})
        rgba = rasterize_layer(data, style, SHAPE_TESTE)
        assert rgba.shape == (*SHAPE_TESTE, 4)
        assert rgba[0, 0, 3] == 0.0  # NaN → transparente
        assert rgba[45, 90, 3] == pytest.approx(0.85)  # alpha da carta

    def test_patch_regional_fora_do_dominio_transparente(self):
        lats = np.linspace(5, -35, 41)
        lons = np.linspace(-75, -35, 41)
        lo, la = np.meshgrid(lons, lats)
        data = _field(20 + la * 0.1, lons, lats)
        style = derive_scalar_style("t", "°C", data.values, {"cmap": "viridis"})
        rgba = rasterize_layer(data, style, SHAPE_TESTE)
        # Centro do recorte (lat -15, lon -55) pintado; antípoda transparente.
        iy = int(round((90 - (-15)) / 2))
        ix = int(round((-55 + 180) / 2))
        assert rgba[iy, ix, 3] > 0
        assert rgba[iy, (ix + 90) % 180, 3] == 0.0

    def test_extend_max_deixa_abaixo_do_piso_transparente(self):
        data = _global_field(variable="precip", unit="mm/3h", fill=0.01)  # tudo sob o piso
        style = derive_scalar_style("precip", "mm/3h", data.values, {"cmap": "precip_classic"})
        rgba = rasterize_layer(data, style, SHAPE_TESTE)
        assert rgba[..., 3].max() == 0.0  # seca total → globo transparente

    def test_continuidade_em_180(self):
        data = _global_field()  # campo zonal contínuo
        style = derive_scalar_style("t", "°C", data.values, {"cmap": "viridis"})
        rgba = rasterize_layer(data, style, SHAPE_TESTE)
        assert np.allclose(rgba[:, 0], rgba[:, -1], atol=1 / 255)  # sem costura


class TestComposite:
    def test_topo_cobre_base_na_ordem_da_carta(self):
        base = np.zeros((2, 2, 4), dtype=np.float32)
        base[..., 0] = 1.0  # vermelho opaco
        base[..., 3] = 1.0
        top = np.zeros((2, 2, 4), dtype=np.float32)
        top[0, 0] = [0.0, 1.0, 0.0, 1.0]  # verde só num canto
        out = composite_over(base, top)
        assert np.allclose(out[0, 0, :3], [0.0, 1.0, 0.0])  # topo venceu
        assert np.allclose(out[1, 1, :3], [1.0, 0.0, 0.0])  # base preservada


class _Cfg:
    def __init__(self, grib_dir):
        self.grib_dir = grib_dir


class TestReloadGlobal:
    def test_variavel_pl_vai_ao_loader_com_extent_global(self, tmp_path, monkeypatch):
        import cartomet_br.services.globe_compose as gc

        chamadas = {}

        def fake_load(**kw):
            chamadas.update(kw)
            return _global_field()

        import cartomet_br.data.ecmwf as ec

        monkeypatch.setattr(ec, "load_pl_variable", lambda **kw: fake_load(**kw))
        data = _field(np.zeros((3, 3)), [-50, -45, -40], [0, -5, -10], source="aifs")
        out, ok = gc.reload_global(data, config=_Cfg(tmp_path), cycle=0, cycle_date="20261003")
        assert ok
        assert chamadas["extent"] == GLOBE_EXTENT
        assert chamadas["smoothing_sigma"] == 0.0
        assert chamadas["model"] == "aifs"  # modelo da CAMADA, não do painel

    def test_cache_miss_cai_no_recorte_regional(self, tmp_path, monkeypatch):
        import cartomet_br.data.ecmwf as ec

        def explode(**kw):
            raise ec.CacheMissError("sem GRIB global")

        monkeypatch.setattr(ec, "load_pl_variable", explode)
        data = _field(np.zeros((3, 3)), [-50, -45, -40], [0, -5, -10])
        out, ok = reload_global(data, config=_Cfg(tmp_path), cycle=0, cycle_date="20261003")
        assert not ok and out is data

    def test_fontes_regionais_nao_tentam(self, tmp_path, monkeypatch):
        import cartomet_br.data.ecmwf as ec

        def nunca(**kw):
            raise AssertionError("fonte regional não deve ir ao loader")

        monkeypatch.setattr(ec, "load_pl_variable", nunca)
        for src, var in (("ens", "ens_prob"), ("era5", "era5_t"), ("ifs", "t_diff")):
            data = _field(np.zeros((3, 3)), [-50, -45, -40], [0, -5, -10], variable=var, source=src)
            out, ok = reload_global(data, config=_Cfg(tmp_path), cycle=0, cycle_date="20261003")
            assert not ok and out is data


class TestComposeScene:
    REGISTRY = {
        "t": {"nome": "Temperatura", "cmap": "RdYlBu_r", "plot_type": "contourf"},
        "gh": {"nome": "Geopotencial", "cmap": "viridis", "plot_type": "contour"},
        "wind": {"nome": "Vento", "category": "wind"},
    }

    def test_compoe_escalares_e_separa_contornos(self, tmp_path, monkeypatch):
        import cartomet_br.services.globe_compose as gc

        monkeypatch.setattr(gc, "reload_global", lambda d, **kw: (d, True))
        pl_data = {
            "t_850": _global_field(),
            "gh_500": _global_field(variable="gh", unit="dam"),
        }
        scene = gc.compose_scene(
            pl_data,
            self.REGISTRY,
            config=_Cfg(tmp_path),
            cycle=0,
            cycle_date="20261003",
            shape=SHAPE_TESTE,
        )
        assert scene.texture is not None and scene.texture.dtype == np.uint8
        assert scene.texture.shape == (*SHAPE_TESTE, 4)
        assert [ly.layer_id for ly in scene.filled_layers] == ["t_850"]
        assert [ly.layer_id for ly in scene.contour_layers] == ["gh_500"]
        assert scene.warnings == []

    def test_vento_fica_de_fora_com_aviso(self, tmp_path, monkeypatch):
        import cartomet_br.services.globe_compose as gc

        monkeypatch.setattr(gc, "reload_global", lambda d, **kw: (d, True))
        wind = _global_field(variable="wind", unit="kt")
        wind.u_values = wind.values.copy()
        wind.v_values = wind.values.copy()
        scene = gc.compose_scene(
            {"wind_850_barbs": wind},
            self.REGISTRY,
            config=_Cfg(tmp_path),
            cycle=0,
            cycle_date="20261003",
            shape=SHAPE_TESTE,
        )
        assert scene.texture is None
        assert any("vento" in w for w in scene.warnings)

    def test_fallback_regional_gera_aviso_honesto(self, tmp_path, monkeypatch):
        import cartomet_br.services.globe_compose as gc

        monkeypatch.setattr(gc, "reload_global", lambda d, **kw: (d, False))
        scene = gc.compose_scene(
            {"t_850": _global_field()},
            self.REGISTRY,
            config=_Cfg(tmp_path),
            cycle=0,
            cycle_date="20261003",
            shape=SHAPE_TESTE,
        )
        assert any("recorte regional" in w for w in scene.warnings)
        assert scene.filled_layers and not scene.filled_layers[0].is_global
