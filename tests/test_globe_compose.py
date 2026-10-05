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
    reload_global_synoptic,
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

    def test_borda_superior_inclui_o_nivel_maximo(self):
        # contourf inclui v == levels[-1] na ÚLTIMA banda; digitize cru o
        # jogaria em "acima" (cor de extend) — ex.: prob ENS de exatos 100%.
        data = _global_field(fill=2.0)
        style = derive_scalar_style(
            "t", "°C", data.values, {"cmap": "viridis"}, fixed_levels=[0.0, 1.0, 2.0]
        )
        interior, _under, over = band_colors(style)
        rgba = rasterize_layer(data, style, SHAPE_TESTE)
        assert np.allclose(rgba[45, 90, :3], interior[-1][:3], atol=1 / 255)
        assert not np.allclose(interior[-1][:3], np.asarray(over)[:3], atol=1 / 255), (
            "teste inconclusivo: última banda e extend têm a mesma cor"
        )

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

    def test_identidade_e_guarda_de_rodada(self, tmp_path, monkeypatch):
        # Achados da revisão: (1) o loader cru não preenche .source — um campo
        # AIFS relido globalmente era carimbado "ECMWF IFS"; (2) cycle/
        # cycle_date vêm do PAINEL — se o usuário trocou a rodada depois de
        # carregar a camada, o globo mostraria OUTRA rodada sem aviso.
        import cartomet_br.data.ecmwf as ec

        regional = _field(
            np.zeros((3, 3)),
            [-50, -45, -40],
            [0, -5, -10],
            source="aifs",
            valid_time="2026-10-03 12Z",
            base_time="00Z 03/10/2026",
        )

        mesma_rodada = _global_field()
        mesma_rodada.valid_time = regional.valid_time
        mesma_rodada.base_time = regional.base_time
        monkeypatch.setattr(ec, "load_pl_variable", lambda **kw: mesma_rodada)
        out, ok = reload_global(regional, config=_Cfg(tmp_path), cycle=0, cycle_date="20261003")
        assert ok
        assert out.source == "aifs"  # o carimbo rotula o modelo CERTO

        outra_rodada = _global_field()
        outra_rodada.valid_time = regional.valid_time
        outra_rodada.base_time = "12Z 02/10/2026"
        monkeypatch.setattr(ec, "load_pl_variable", lambda **kw: outra_rodada)
        out2, ok2 = reload_global(regional, config=_Cfg(tmp_path), cycle=12, cycle_date="20261002")
        assert not ok2 and out2 is regional  # rodada divergente → recorte honesto

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

    def test_textura_achatada_sobre_branco_como_a_carta(self, tmp_path, monkeypatch):
        # A carta compõe alpha 0.85 sobre fundo CLARO — a textura final achata
        # a pilha sobre branco (cor idêntica à vista na carta) e deixa a área
        # sem dado transparente (o oceano noturno do globo aparece lá).
        import cartomet_br.services.globe_compose as gc

        monkeypatch.setattr(gc, "reload_global", lambda d, **kw: (d, True))
        data = _global_field()
        data.values[0, 0] = np.nan  # um furo sem dado
        style = derive_scalar_style("t", "°C", data.values, self.REGISTRY["t"])
        raw = gc.rasterize_layer(data, style, SHAPE_TESTE)
        scene = gc.compose_scene(
            {"t_850": data},
            self.REGISTRY,
            config=_Cfg(tmp_path),
            cycle=0,
            cycle_date="20261003",
            shape=SHAPE_TESTE,
        )
        tex = scene.texture
        assert tex[0, 0, 3] == 0  # furo continua transparente
        assert tex[45, 90, 3] == 255  # dado vira opaco (já composto)
        esperado = raw[45, 90, :3] * 0.85 + 0.15  # 0.85·cor + 0.15·branco
        assert np.allclose(tex[45, 90, :3] / 255.0, esperado, atol=2 / 255)

    def test_vento_agora_entra_como_camada_do_globo(self, tmp_path, monkeypatch):
        # v1 pulava o vento com aviso; o redesign o inclui (barbelas/vetores
        # subamostrados — TestVentoNoGlobo cobre os detalhes).
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
        assert scene.texture is None  # vento nao vira textura escalar
        assert len(scene.wind_layers) == 1
        assert not any("fora do globo" in w for w in scene.warnings)

    def test_isolinhas_usam_a_derivacao_de_contour_da_carta(self, tmp_path, monkeypatch):
        # Achado da revisão: as isolinhas do globo usavam a derivação ESCALAR
        # (21 níveis fracionários) — a carta usa passo INTEIRO próprio
        # (_plot_scalar_contour). Agora é a mesma função canônica.
        import cartomet_br.services.globe_compose as gc
        from cartomet_br.services.field_style import derive_contour_levels

        monkeypatch.setattr(gc, "reload_global", lambda d, **kw: (d, True))
        gh = _global_field(variable="gh", unit="dam")
        scene = gc.compose_scene(
            {"gh_500": gh},
            self.REGISTRY,
            config=_Cfg(tmp_path),
            cycle=0,
            cycle_date="20261003",
            shape=SHAPE_TESTE,
        )
        esperado = derive_contour_levels(gh.values)
        niveis = np.asarray(scene.contour_layers[0].style.levels)
        assert np.array_equal(niveis, esperado)
        assert np.allclose(np.diff(niveis), np.round(np.diff(niveis)))  # passo inteiro

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
        assert any("sem cache global" in w and "recorte" in w for w in scene.warnings)
        assert scene.filled_layers and not scene.filled_layers[0].is_global


class TestSinoticoNoGlobo:
    """PNMM/espessura entram no globo; centros H/L ficam fora com aviso."""

    @staticmethod
    def _syn(**kw):
        from cartomet_br.data.ecmwf import SynopticData

        lats = np.linspace(5, -35, 5)
        lons = np.linspace(-75, -35, 5)
        lon2d, lat2d = np.meshgrid(lons, lats)
        base = {
            "pnmm": np.full((5, 5), 1013.0),
            "thickness": np.full((5, 5), 5500.0),
            "lons": lons,
            "lats": lats,
            "lon2d": lon2d,
            "lat2d": lat2d,
            "valid_time": "2026-10-04 00Z",
            "extent": [-75.0, -35.0, -35.0, 5.0],
            "base_time": "00Z 04/10/2026",
            "step": 0,
        }
        base.update(kw)
        return SynopticData(**base)

    def test_compose_inclui_sinotico_e_avisa_centros(self, tmp_path, monkeypatch):
        import cartomet_br.services.globe_compose as gc

        syn_global = self._syn()
        monkeypatch.setattr(gc, "reload_global_synoptic", lambda s, **kw: (syn_global, True))
        scene = gc.compose_scene(
            {},
            {},
            config=_Cfg(tmp_path),
            cycle=0,
            cycle_date="20261004",
            synoptic=self._syn(),
            synoptic_kinds=("pnmm", "thickness", "centers"),
        )
        assert scene.synoptic is syn_global
        assert scene.synoptic_kinds == ("pnmm", "thickness")
        assert any("Centros H/L" in w for w in scene.warnings)
        assert scene.has_fields()  # sinotico conta como campo (pele de campos)

    def test_reload_sinotico_identidade_e_guarda_de_rodada(self, tmp_path, monkeypatch):
        import cartomet_br.data.ecmwf as ec

        regional = self._syn(source="aifs")
        outra = self._syn(base_time="12Z 03/10/2026")
        monkeypatch.setattr(ec, "load_synoptic_data", lambda **kw: outra)
        out, ok = reload_global_synoptic(
            regional, config=_Cfg(tmp_path), cycle=12, cycle_date="20261003"
        )
        assert not ok and out is regional  # rodada divergente -> recorte honesto

        mesma = self._syn()
        monkeypatch.setattr(ec, "load_synoptic_data", lambda **kw: mesma)
        out2, ok2 = reload_global_synoptic(
            regional, config=_Cfg(tmp_path), cycle=0, cycle_date="20261004"
        )
        assert ok2
        assert out2.source == "aifs"  # identidade do modelo preservada


class TestVentoNoGlobo:
    @staticmethod
    def _vento(source="ifs"):
        lats = np.linspace(90, -90, 91)
        lons = np.linspace(-180, 178, 180)
        lo, la = np.meshgrid(lons, lats)
        u = 10.0 + 0.0 * la
        v = 5.0 + 0.0 * la
        return _field(
            np.hypot(u, v),
            lons,
            lats,
            variable="wind",
            level=850,
            unit="kt",
            source=source,
            u_values=u,
            v_values=v,
            wind_speed=np.hypot(u, v),
        )

    def test_subsample_mascara_hemisferio_e_flip_hs(self):
        from cartomet_br.services.globe_compose import subsample_wind

        s = subsample_wind(self._vento(), (0.0, 0.0), "media")
        assert s["lons"].size > 0
        # Antipoda (lon 180) nao entra; todos os pontos no hemisferio visivel
        import numpy as np

        cosd = np.sin(0) * np.sin(np.radians(s["lats"])) + np.cos(0) * np.cos(
            np.radians(s["lats"])
        ) * np.cos(np.radians(s["lons"]))
        assert (cosd > 0.17).all()
        # flip de barbela so no HS (convencao da carta)
        assert (s["flip"] == (s["lats"] < 0)).all()
        # densidade muda a contagem
        from cartomet_br.services.globe_compose import WIND_GLOBE_STRIDE

        s_alta = subsample_wind(self._vento(), (0.0, 0.0), "alta")
        assert s_alta["lons"].size > s["lons"].size
        assert set(WIND_GLOBE_STRIDE) == {"baixa", "media", "alta"}

    def test_subsample_stream_latitudes_ascendentes(self):
        from cartomet_br.services.globe_compose import subsample_wind_stream

        g = subsample_wind_stream(self._vento())
        assert g["lats"][0] < g["lats"][-1]  # exigencia do streamplot
        assert g["u"].shape == (len(g["lats"]), len(g["lons"]))

    def test_compose_cria_camada_de_vento_com_estilo_da_carta(self, tmp_path, monkeypatch):
        import cartomet_br.services.globe_compose as gc

        monkeypatch.setattr(gc, "reload_global", lambda d, **kw: (d, True))
        registry = {"wind": {"nome": "Vento", "category": "wind"}}
        scene = gc.compose_scene(
            {"wind_850_barbs": self._vento()},
            registry,
            config=_Cfg(tmp_path),
            cycle=0,
            cycle_date="20261005",
            shape=SHAPE_TESTE,
            wind_styles={
                "wind_850_barbs": {"wind_type": "barbs", "color": "#123456", "density": "alta"}
            },
        )
        assert len(scene.wind_layers) == 1
        wl = scene.wind_layers[0]
        assert wl.wind_type == "barbs" and wl.color == "#123456" and wl.density == "alta"
        assert not any("fora do globo" in w for w in scene.warnings)  # aviso v1 morreu
        assert scene.has_fields()  # so vento ja ativa a pele de campos

    def test_vento_regional_cai_no_recorte_com_aviso(self, tmp_path, monkeypatch):
        import cartomet_br.services.globe_compose as gc

        monkeypatch.setattr(gc, "reload_global", lambda d, **kw: (d, False))
        registry = {"wind": {"nome": "Vento", "category": "wind"}}
        scene = gc.compose_scene(
            {"wind_850_barbs": self._vento()},
            registry,
            config=_Cfg(tmp_path),
            cycle=0,
            cycle_date="20261005",
            shape=SHAPE_TESTE,
        )
        assert scene.wind_layers and not scene.wind_layers[0].is_global
        assert any("vento do recorte" in w for w in scene.warnings)
