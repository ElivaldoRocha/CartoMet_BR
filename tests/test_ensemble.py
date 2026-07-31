"""Testes do Ensemble ENS (Onda 3): produtos puros, contratos de cache/builder,
serviço, painel e render — tudo offline (stacks sintéticos + Client mockado)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from cartomet_br.data import ensemble
from cartomet_br.data.ecmwf import PLFieldData
from cartomet_br.data.ensemble import (
    ENS_MEMBERS_LABEL,
    ENS_SPREAD_FIELDS,
    PROB_THRESHOLDS_MM,
    member_mean_spread,
    prob_exceedance,
)
from cartomet_br.gui._constants import ENS_PROB_THRESHOLDS_MM
from cartomet_br.services.data_service import DataService, ValidationError

# ═══════════════════════════════════════════════════════════════════════════════
#  Contratos (deriva de constantes + nomes de cache)
# ═══════════════════════════════════════════════════════════════════════════════


class TestContracts:
    def test_limiar_gui_nao_deriva_do_motor(self):
        # ENS_PROB_THRESHOLDS_MM é duplicado em gui/_constants.py p/ não puxar
        # xarray na GUI — este teste trava a igualdade.
        assert tuple(ENS_PROB_THRESHOLDS_MM) == tuple(PROB_THRESHOLDS_MM)

    def test_cache_do_controle_e_o_mesmo_do_oper(self):
        # O controle do ENS REUSA o cache das cartas normais: os nomes têm de
        # bater byte-a-byte com os dos loaders existentes (load_synoptic_data,
        # load_pl_variable e _read_accum_field).
        assert (
            ensemble._cache_name("ecmwf", "msl", None, "20260731", 12, 24)
            == "ecmwf_msl_20260731_12Z_f024.grib2"
        )
        assert (
            ensemble._cache_name("ecmwf", "gh", 500, "20260731", 0, 120)
            == "ecmwf_gh_20260731_00Z_500hPa_f120.grib2"
        )
        assert (
            ensemble._cache_name("ecmwf", "tp", None, "20260731", 12, 48)
            == "ecmwf_tp_20260731_12Z_f048.grib2"
        )

    def test_cache_dos_membros_usa_prefixo_ens(self):
        assert (
            ensemble._cache_name("ens", "msl", None, "20260731", 12, 24)
            == "ens_msl_20260731_12Z_f024.grib2"
        )


class TestBuilderEnfo:
    """download_ecmwf com stream/type_ — o choke point único fala enfo."""

    def test_retrieve_recebe_stream_e_type(self, tmp_path, monkeypatch):
        from cartomet_br.data import ecmwf as ecmwf_mod

        captured: dict = {}

        class _FakeClient:
            def __init__(self, **kwargs):
                captured["ctor"] = kwargs

            def retrieve(self, **kwargs):
                captured["retrieve"] = kwargs
                target = kwargs.get("target")
                if target:
                    from pathlib import Path as _P

                    _P(target).write_bytes(b"GRIB-fake")

            def download(self, **kwargs):  # nunca deve ser chamado p/ enfo
                raise AssertionError("download() no enfo baixaria ~6 GB")

        monkeypatch.setattr(ecmwf_mod, "Client", _FakeClient)
        out = ecmwf_mod.download_ecmwf(
            variables=["msl"],
            step=24,
            cycle=12,
            date="20260731",
            output_path=tmp_path / "ens_msl_20260731_12Z_f024.grib2",
            data_dir=tmp_path,
            stream="enfo",
            type_="pf",
            model="ifs",
        )
        assert out.exists()
        assert captured["retrieve"]["stream"] == "enfo"
        assert captured["retrieve"]["type"] == "pf"
        assert "number" not in captured["retrieve"]  # todos os membros
        assert captured["ctor"]["model"] == "ifs"

    def test_sem_stream_o_comportamento_antigo_permanece(self, tmp_path, monkeypatch):
        from cartomet_br.data import ecmwf as ecmwf_mod

        captured: dict = {}

        class _FakeClient:
            def __init__(self, **kwargs):
                pass

            def retrieve(self, **kwargs):
                captured["retrieve"] = kwargs
                from pathlib import Path as _P

                _P(kwargs["target"]).write_bytes(b"GRIB-fake")

        monkeypatch.setattr(ecmwf_mod, "Client", _FakeClient)
        ecmwf_mod.download_ecmwf(
            variables=["msl"],
            step=0,
            cycle=0,
            output_path=tmp_path / "ecmwf_msl_x_f000.grib2",
            data_dir=tmp_path,
        )
        assert "stream" not in captured["retrieve"]  # a lib infere o oper
        assert captured["retrieve"]["type"] == "fc"


# ═══════════════════════════════════════════════════════════════════════════════
#  Produtos puros (numpy)
# ═══════════════════════════════════════════════════════════════════════════════


class TestProducts:
    def test_prob_exceedance_conta_membros(self):
        # 4 membros: no pixel [0,0] chove 0/5/15/20 mm → P(>10) = 50%
        stack = np.zeros((4, 1, 1))
        stack[:, 0, 0] = [0.0, 5.0, 15.0, 20.0]
        prob = prob_exceedance(stack, 10.0)
        assert prob[0, 0] == pytest.approx(50.0)

    def test_prob_limiar_e_estrito(self):
        stack = np.full((10, 1, 1), 10.0)  # todos EXATAMENTE no limiar
        assert prob_exceedance(stack, 10.0)[0, 0] == pytest.approx(0.0)

    def test_mean_spread(self):
        stack = np.zeros((3, 2, 2))
        stack[0], stack[1], stack[2] = 1.0, 2.0, 3.0
        mean, std = member_mean_spread(stack)
        assert np.allclose(mean, 2.0)
        assert np.allclose(std, np.sqrt(2.0 / 3.0))  # σ populacional

    def test_spread_zero_quando_membros_concordam(self):
        stack = np.full((51, 3, 3), 1013.25)
        mean, std = member_mean_spread(stack)
        assert np.allclose(std, 0.0)
        assert np.allclose(mean, 1013.25)


# ═══════════════════════════════════════════════════════════════════════════════
#  Loaders de produto (stack sintético — _load_51 monkeypatchado, SEM rede)
# ═══════════════════════════════════════════════════════════════════════════════


def _fake_load_51(values_by_step):
    """Fabrica um _load_51 falso: step → stack (51, 2, 2)."""

    def _fake(param, level, step, cycle, date_str, extent, data_dir, source, force, cb, what):
        stack = np.full((51, 2, 2), float(values_by_step[step]))
        lons = np.array([-50.0, -49.75])
        lats = np.array([0.0, -0.25])
        return stack, lons, lats, "2026-08-01T12:00", "12Z 31/07/2026"

    return _fake


class TestLoadProbPrecip:
    def test_prob_com_janela_24h(self, tmp_path, monkeypatch):
        # tp acumulado: 5 mm em +24h, 25 mm em +48h → R24h = 20 mm (0.005/0.025 m)
        monkeypatch.setattr(ensemble, "_load_51", _fake_load_51({48: 0.025, 24: 0.005}))
        data = ensemble.load_ens_prob_precip(10.0, [-55, -15, -45, 5], 48, 12, "20260731", tmp_path)
        assert np.allclose(data.values, 100.0)  # todos os membros > 10 mm
        assert data.variable == "ens_prob"
        assert data.source == "ens"
        assert data.unit == "%"
        extra = data.extra or {}
        assert extra["threshold_mm"] == 10.0
        assert extra["n_members"] == 51
        assert "P(R24h > 10 mm)" in extra["title_desc"]
        assert ENS_MEMBERS_LABEL in extra["title_desc"]

    def test_step_24_dispensa_download_do_zero(self, tmp_path, monkeypatch):
        chamados: list[int] = []

        def _spy(param, level, step, *a, **kw):
            chamados.append(step)
            stack = np.full((51, 2, 2), 0.02)  # 20 mm acumulados
            return stack, np.arange(2.0), np.arange(2.0), "", ""

        monkeypatch.setattr(ensemble, "_load_51", _spy)
        data = ensemble.load_ens_prob_precip(10.0, [-55, -15, -45, 5], 24, 0, "20260731", tmp_path)
        assert chamados == [24]  # tp(0) é zero — nenhum segundo download
        assert np.allclose(data.values, 100.0)

    def test_step_menor_que_24_recusado(self, tmp_path):
        with pytest.raises(ValueError, match="step ≥ \\+24h"):
            ensemble.load_ens_prob_precip(10.0, [-55, -15, -45, 5], 12, 0, "20260731", tmp_path)


class TestLoadSpread:
    def test_msl_converte_pa_para_hpa(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ensemble, "_load_51", _fake_load_51({24: 101325.0}))
        data = ensemble.load_ens_spread("msl", [-55, -15, -45, 5], 24, 12, "20260731", tmp_path)
        extra = data.extra or {}
        assert np.allclose(extra["mean_contour"], 1013.25)  # Pa → hPa
        assert np.allclose(data.values, 0.0)  # membros idênticos → σ = 0
        assert data.variable == "ens_spread"
        assert data.unit == "hPa"
        assert data.source == "ens"
        assert extra["ens_product"] == "msl"

    def test_gh500_leva_nivel(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ensemble, "_load_51", _fake_load_51({24: 5870.0}))
        data = ensemble.load_ens_spread("gh500", [-55, -15, -45, 5], 24, 12, "20260731", tmp_path)
        assert data.level == 500
        assert data.unit == "gpm"

    def test_campo_desconhecido(self, tmp_path):
        with pytest.raises(ValueError, match="Campo ENS desconhecido"):
            ensemble.load_ens_spread("t850", [-55, -15, -45, 5], 24, 12, "20260731", tmp_path)

    def test_spread_fields_tem_msl_e_gh500(self):
        assert set(ENS_SPREAD_FIELDS) == {"msl", "gh500"}


# ═══════════════════════════════════════════════════════════════════════════════
#  DataService.load_ensemble (validação ANTES da rede)
# ═══════════════════════════════════════════════════════════════════════════════


class TestServiceLoadEnsemble:
    def test_aifs_recusado(self, config_brasil):
        config_brasil.model = "aifs"
        svc = DataService(config_brasil)
        with pytest.raises(ValidationError, match="IFS"):
            svc.load_ensemble("prob", step=24, cycle=12, cycle_date="20260731")

    def test_step_fora_da_grade_recusado(self, config_brasil):
        svc = DataService(config_brasil)
        with pytest.raises(ValidationError, match="Step"):
            svc.load_ensemble("prob", step=25, cycle=12, cycle_date="20260731")

    def test_rodada_06z_alcance(self, config_brasil):
        svc = DataService(config_brasil)
        with pytest.raises(ValidationError, match="144"):
            svc.load_ensemble("msl", step=150, cycle=6, cycle_date="20260731")

    def test_produto_desconhecido(self, config_brasil):
        svc = DataService(config_brasil)
        with pytest.raises(ValidationError, match="Produto ENS"):
            svc.load_ensemble("nuvens", step=24, cycle=12, cycle_date="20260731")

    def test_layer_ids(self, config_brasil, monkeypatch):
        svc = DataService(config_brasil)
        dummy = PLFieldData(values=np.zeros((2, 2)), lons=np.arange(2.0), lats=np.arange(2.0))
        monkeypatch.setattr(ensemble, "load_ens_prob_precip", lambda *a, **kw: dummy)
        monkeypatch.setattr(ensemble, "load_ens_spread", lambda *a, **kw: dummy)
        lid, _ = svc.load_ensemble(
            "prob", threshold_mm=5.0, step=24, cycle=12, cycle_date="20260731"
        )
        assert lid == "ens_prob_5mm"
        lid, _ = svc.load_ensemble("gh500", step=24, cycle=12, cycle_date="20260731")
        assert lid == "ens_spread_gh500"


# ═══════════════════════════════════════════════════════════════════════════════
#  Painel (offscreen)
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnsPanel:
    def test_limiar_so_no_produto_de_probabilidade(self, qapp):
        from cartomet_br.gui.layer_panel import FieldLayerPanel

        panel = FieldLayerPanel()
        assert panel.ens_thr_combo.isEnabled()  # default = prob
        panel.ens_product_combo.setCurrentIndex(panel.ens_product_combo.findData("msl"))
        assert not panel.ens_thr_combo.isEnabled()
        panel.ens_product_combo.setCurrentIndex(panel.ens_product_combo.findData("prob"))
        assert panel.ens_thr_combo.isEnabled()

    def test_sinal_emite_produto_e_limiar(self, qapp):
        from cartomet_br.gui.layer_panel import FieldLayerPanel

        panel = FieldLayerPanel()
        got: list[tuple[str, float]] = []
        panel.ens_requested.connect(lambda p, t: got.append((p, t)))
        panel.ens_thr_combo.setCurrentIndex(panel.ens_thr_combo.findData(20.0))
        panel._on_ens_add()
        assert got == [("prob", 20.0)]

    def test_gating_aifs_desabilita_o_grupo(self, qapp):
        from cartomet_br.gui.layer_panel import FieldLayerPanel

        panel = FieldLayerPanel()
        panel.set_model_gating("aifs")
        assert not panel.ens_group.isEnabled()
        panel.set_model_gating("ifs")
        assert panel.ens_group.isEnabled()


# ═══════════════════════════════════════════════════════════════════════════════
#  Render + título + manifesto (offscreen)
# ═══════════════════════════════════════════════════════════════════════════════


def _prob_field() -> PLFieldData:
    values = np.tile(np.linspace(0, 100, 6), (5, 1))
    return PLFieldData(
        values=values,
        lons=np.linspace(-60, -40, 6),
        lats=np.linspace(-30, -10, 5),
        variable="ens_prob",
        level=0,
        unit="%",
        valid_time="2026-08-01T12:00",
        base_time="12Z 31/07/2026",
        step=24,
        source="ens",
        extra={
            "ens_product": "prob",
            "threshold_mm": 10.0,
            "n_members": 51,
            "title_desc": f"P(R24h > 10 mm) (%) — {ENS_MEMBERS_LABEL}",
            "entry_label": "ENS P(R24h>10mm)",
            "entry_detail": "%",
        },
    )


def _spread_field() -> PLFieldData:
    rng = np.linspace(0.5, 3.0, 30).reshape(5, 6)
    return PLFieldData(
        values=rng,
        lons=np.linspace(-60, -40, 6),
        lats=np.linspace(-30, -10, 5),
        variable="ens_spread",
        level=0,
        unit="hPa",
        valid_time="2026-08-01T12:00",
        base_time="12Z 31/07/2026",
        step=24,
        source="ens",
        extra={
            "mean_contour": np.linspace(1000, 1024, 30).reshape(5, 6),
            "ens_product": "msl",
            "n_members": 51,
            "title_desc": f"PNMM: média (linhas) ± dispersão σ (cor, hPa) — {ENS_MEMBERS_LABEL}",
            "entry_label": "ENS PNMM ±σ",
            "entry_detail": "hPa",
        },
    )


class TestEnsCanvas:
    def test_titulo_ecmwf_ens(self, canvas):
        canvas.add_pl_layer("ens_prob_10mm", _prob_field(), "barbs")
        title = canvas.ax.get_title(loc="left")
        assert title.startswith("ECMWF ENS")
        assert "51 membros (50 pert. + controle)" in title
        assert "ECMWF IFS" not in title

    def test_spread_desenha_o_contorno_da_media(self, canvas):
        from matplotlib.contour import QuadContourSet

        canvas.add_pl_layer("ens_spread_msl", _spread_field(), "barbs")
        artists = canvas._pl_artists["ens_spread_msl"]
        contour_sets = [a for a in artists if isinstance(a, QuadContourSet)]
        # contourf(σ) + contour fino de σ + contour da MÉDIA = 3 conjuntos
        assert len(contour_sets) >= 3

    def test_manifesto_round_trip(self, canvas):
        canvas._pl_data["ens_prob_10mm"] = _prob_field()
        canvas._pl_data["ens_spread_msl"] = _spread_field()
        manifest = canvas.export_layers_state()
        ens_specs = [m for m in manifest if m["kind"] == "ens"]
        assert len(ens_specs) == 2
        by_id = {m["layer_id"]: m for m in ens_specs}
        assert by_id["ens_prob_10mm"]["product"] == "prob"
        assert by_id["ens_prob_10mm"]["threshold_mm"] == 10.0
        assert by_id["ens_prob_10mm"]["step"] == 24
        assert by_id["ens_spread_msl"]["product"] == "msl"
