"""Testes da biblioteca de estudos de caso ERA5 (Onda 5 da v3.2) — offline.

O catálogo é um CONTRATO com o fluxo ERA5 existente: cada receita precisa ser
aceita pela validação do DataService sem tocar a rede, rodar na fila sem
disparar o diálogo de período longo e usar apenas agregações que a própria UI
ofereceria (mesma régua do ``ERA5Panel``).
"""

import os
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from cartomet_br.data.case_studies import (
    CASE_STUDIES,
    CaseStudy,
    get_case,
)
from cartomet_br.data.ecmwf import PL_LEVELS, VARIABLE_REGISTRY
from cartomet_br.data.era5 import (
    AGG_INDEX_MODES,
    ERA5_LONG_PERIOD_DAYS,
    ERA5_VARIABLES,
    era5_period_days,
    profile_modes,
)
from cartomet_br.services.data_service import DataService

ALL_REQUESTS = [(case, req) for case in CASE_STUDIES for req in case.layer_requests()]


def _layer_id(req) -> str:
    """Reproduz a regra de layer_id do DataService.load_era5_field."""
    var = ERA5_VARIABLES[req.var_key]
    return f"{req.var_key}_{req.level}" if var.pressure_level else req.var_key


class TestCatalogo:
    def test_cinco_casos_aprovados_com_chaves_unicas(self):
        assert len(CASE_STUDIES) == 5
        keys = [c.key for c in CASE_STUDIES]
        assert len(set(keys)) == 5
        # A lista aprovada pelo usuário (decisão de conteúdo) — ordem estável.
        assert keys == [
            "catarina_2004",
            "bomba_2020",
            "zcas_petropolis_2022",
            "friagem_2021",
            "cheia_amazonas_2021",
        ]

    def test_get_case(self):
        assert isinstance(get_case("catarina_2004"), CaseStudy)
        assert get_case("nao_existe") is None

    def test_material_didatico_completo(self):
        for case in CASE_STUDIES:
            assert case.resumo and case.porque and case.referencia and case.quando
            for spec in case.layers:
                assert spec.nota, f"{case.key}: camada {spec.var_key} sem nota didática"

    def test_extent_na_ordem_do_config(self):
        # [lon_min, lat_min, lon_max, lat_max] — inteiros (spinboxes) e válidos.
        for case in CASE_STUDIES:
            lon_min, lat_min, lon_max, lat_max = case.extent
            assert all(isinstance(v, int) for v in case.extent)
            assert -180 <= lon_min < lon_max <= 180, case.key
            assert -90 <= lat_min < lat_max <= 90, case.key

    def test_primeira_camada_visivel_e_ha_pelo_menos_uma(self):
        # A primeira é a base da pilha (ligada); apoios entram desligados.
        for case in CASE_STUDIES:
            assert case.layers, case.key
            assert case.layers[0].visible, case.key
            assert any(spec.visible for spec in case.layers), case.key


class TestContratoComFluxoERA5:
    def test_toda_receita_passa_na_validacao_do_service(self):
        # A mesma validação que rodaria antes da rede — sem instância/rede.
        for _case, req in ALL_REQUESTS:
            DataService.validate_era5_request(
                req.var_key, req.date_start, req.date_end, req.hour, req.agg, req.level
            )

    def test_aggs_pertencem_ao_perfil_da_variavel(self):
        # Régua da UI: o ERA5Panel só oferece os modos do perfil físico.
        for case, req in ALL_REQUESTS:
            var = ERA5_VARIABLES[req.var_key]
            assert req.agg in profile_modes(var.agg_profile), (
                f"{case.key}: agg {req.agg!r} fora do perfil de {req.var_key}"
            )

    def test_sem_indices_de_evento_e_thresh_zero(self):
        # O catálogo não usa índices (thresh sempre 0.0, como o sinal espera).
        for _case, req in ALL_REQUESTS:
            assert req.agg not in AGG_INDEX_MODES
            assert req.thresh == 0.0

    def test_periodos_nao_disparam_dialogo_de_periodo_longo(self):
        # A fila roda sem confirmação: todo período ≤ ERA5_LONG_PERIOD_DAYS.
        for case, req in ALL_REQUESTS:
            days = era5_period_days(req.date_start, req.date_end)
            assert days <= ERA5_LONG_PERIOD_DAYS, (
                f"{case.key}: {req.var_key} pede {days} dias — travaria a fila"
            )

    def test_layer_ids_nao_colidem_dentro_do_caso(self):
        # Colisão substituiria uma camada da própria receita (mesmo layer_id).
        for case in CASE_STUDIES:
            ids = [_layer_id(req) for req in case.layer_requests()]
            assert len(ids) == len(set(ids)), f"{case.key}: {ids}"

    def test_variaveis_existem_no_registry_de_render(self):
        for _case, req in ALL_REQUESTS:
            assert req.var_key in VARIABLE_REGISTRY, req.var_key

    def test_niveis_de_pressao_validos(self):
        for case, req in ALL_REQUESTS:
            var = ERA5_VARIABLES[req.var_key]
            if var.pressure_level:
                assert req.level in PL_LEVELS, f"{case.key}: {req.var_key} @ {req.level}"
            else:
                assert req.level == 0, f"{case.key}: superfície com nível {req.level}"


class TestResolucaoDeCamadas:
    def test_hora_forca_data_final_igual_a_inicial(self):
        # Espelho do ERA5Panel._emit_request: modo "hora" ignora a data final.
        for _case, req in ALL_REQUESTS:
            if req.agg == "hora":
                assert req.date_start == req.date_end

    def test_heranca_e_override_de_datas(self):
        catarina = get_case("catarina_2004")
        reqs = {(_layer_id(r)): r for r in catarina.layer_requests()}
        # Instantâneo herda a data/hora do caso...
        assert reqs["era5_mslp"].date_start == "2004-03-27"
        assert reqs["era5_mslp"].hour == 18
        # ...e a TSM semanal usa o período próprio (override).
        assert reqs["era5_sst"].date_start == "2004-03-20"
        assert reqs["era5_sst"].date_end == "2004-03-27"
        assert reqs["era5_sst"].agg == "media"

        friagem = get_case("friagem_2021")
        freqs = {(_layer_id(r)): r for r in friagem.layer_requests()}
        # A PNMM instantânea foi cravada no auge (29/07), não no início do caso.
        assert freqs["era5_mslp"].date_start == "2021-07-29"
        assert freqs["era5_mslp"].date_end == "2021-07-29"
        # A mínima varre o episódio inteiro.
        assert freqs["era5_tmin"].date_start == "2021-07-28"
        assert freqs["era5_tmin"].date_end == "2021-07-30"

    def test_datas_historicas_sao_iso_e_ordenadas(self):
        for case in CASE_STUDIES:
            d0 = date.fromisoformat(case.date_start)
            d1 = date.fromisoformat(case.date_end)
            assert d0 <= d1, case.key


class TestCaseStudyDialog:
    @pytest.fixture
    def dialog(self, qapp):
        from cartomet_br.gui.dialogs import CaseStudyDialog

        dlg = CaseStudyDialog()
        yield dlg
        dlg.deleteLater()

    def test_lista_todos_os_casos_e_seleciona(self, dialog):
        assert dialog.case_list.count() == len(CASE_STUDIES)
        assert dialog.selected_case() is CASE_STUDIES[0]  # row 0 pré-selecionada
        dialog.case_list.setCurrentRow(2)
        assert dialog.selected_case() is CASE_STUDIES[2]

    def test_detalhes_mostram_material_didatico(self, dialog):
        dialog.case_list.setCurrentRow(0)
        html = dialog.details.toHtml()
        assert CASE_STUDIES[0].nome in html
        assert "Por que este caso ensina" in html

    def test_camadas_de_apoio_marcadas_como_desligadas(self, dialog):
        # Todo caso tem apoios (visible=False) → o rótulo aparece no HTML.
        for row, case in enumerate(CASE_STUDIES):
            dialog.case_list.setCurrentRow(row)
            html = dialog.details.toHtml()
            if any(not spec.visible for spec in case.layers):
                assert "[entra desligada]" in html, case.key

    def test_rich_text_nao_engole_a_frase_do_omega(self, dialog):
        # "ω<0 em..." cru virava TAG para o parser do Qt, que engolia dali até
        # o próximo ">" — a frase didática inteira sumia em silêncio. O
        # html.escape no catálogo preserva o texto (visível no toPlainText).
        row = next(i for i, c in enumerate(CASE_STUDIES) if c.key == "zcas_petropolis_2022")
        dialog.case_list.setCurrentRow(row)
        text = dialog.details.toPlainText()
        assert "ω<0 em 500 hPa" in text
        assert "Sobre a serra" in text


class TestFilaDeCasoNaJanela:
    """Hardening da fila (revisão adversarial da Onda 5) — MainWindow offscreen."""

    @pytest.fixture
    def window(self, qapp, tmp_path):
        from cartomet_br.gui.main_window import MainWindow

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        try:
            return MainWindow(data_dir=data_dir)
        except Exception as exc:  # noqa: BLE001 — ambiente sem render
            pytest.skip(f"MainWindow não pôde ser criada offscreen: {exc}")

    def test_invalidate_abandona_fila_e_download_em_voo(self, window):
        # Abrir projeto / trocar região / trocar tema passam por
        # _invalidate_map_overlays: a fila do caso NÃO pode sobreviver ao
        # rebuild (as camadas restantes aterrissariam no mapa novo).
        case = CASE_STUDIES[0]
        window._case_name = case.nome
        window._case_queue = case.layer_requests()
        window._case_pending = window._case_queue[0]
        window._case_stage = "Caso: X — camada 1/4"
        window._case_layer_ids = ["era5_mslp"]
        window.era5_download_thread = object()  # download "em voo"
        window._invalidate_map_overlays()
        assert window._case_queue == []
        assert window._case_name == ""
        assert window._case_pending is None
        assert window._case_stage == ""
        assert window._case_layer_ids == []
        assert window.era5_download_thread is None

    def test_ok_tardio_de_thread_abandonada_nao_empilha(self, window):
        # Resultado de uma thread abandonada (sender ≠ thread atual) é
        # descartado — não pode pintar camada recortada na região antiga.
        window.era5_download_thread = object()  # a "atual" é outra
        added = []
        window.canvas.add_pl_layer = lambda *a, **k: added.append(a)
        window._on_era5_download_ok("era5_mslp", None)  # sender() = None
        assert added == []

    def test_novo_caso_limpa_camadas_do_anterior(self, window, monkeypatch):
        # 2004 empilhado sobre 2020 seria indistinguível no painel (layer_ids
        # homônimos do ERA5) — um caso novo remove as camadas do anterior.
        from PyQt6.QtWidgets import QDialog

        from cartomet_br.data import cds_credentials
        from cartomet_br.gui.dialogs import CaseStudyDialog
        from cartomet_br.gui.main_window import MainWindow

        monkeypatch.setattr(cds_credentials, "resolve_cds_key", lambda: "chave")
        monkeypatch.setattr(CaseStudyDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
        monkeypatch.setattr(CaseStudyDialog, "selected_case", lambda self: CASE_STUDIES[1])
        monkeypatch.setattr(MainWindow, "_process_case_queue", lambda self: None)
        window._case_layer_ids = ["era5_mslp", "era5_t_850"]
        removed = []
        monkeypatch.setattr(window.canvas, "remove_pl_layer", lambda lid: removed.append(lid))
        window._on_case_studies_requested()
        assert removed == ["era5_mslp", "era5_t_850"]
        assert window._case_layer_ids == []
        assert window._case_name == CASE_STUDIES[1].nome
        # Extent do caso CRAVADO para a fila inteira (zoom não re-recorta).
        assert window._case_extent == [float(v) for v in CASE_STUDIES[1].extent]
        assert window._case_cancelled is False

    def test_cancelar_fila_esvazia_sem_matar_a_camada_em_voo(self, window):
        window._case_name = "Furacão Catarina"
        window._case_queue = [object(), object()]
        window._case_cancel()
        assert window._case_queue == []
        assert window._case_cancelled is True
        # Sem caso ativo (clique tardio), é no-op.
        window._case_name = ""
        window._case_cancelled = False
        window._case_queue = [object()]
        window._case_cancel()
        assert window._case_queue == [window._case_queue[0]]
        assert window._case_cancelled is False

    def test_fila_cancelada_encerra_com_status_honesto(self, window):
        window._case_name = "Furacão Catarina"
        window._case_queue = []
        window._case_cancelled = True
        window.era5_download_thread = None
        window._process_case_queue()
        assert "parado a pedido" in window.status_label.text()
        assert window._case_name == ""

    def test_conexoes_da_fila_entram_antes_do_start(self, window, monkeypatch):
        # Conexão enfileirada só entrega sinais emitidos DEPOIS do connect:
        # num cache-hit a thread termina em milissegundos — conectar após o
        # start perdia o finished_ok e a fila travava para sempre.
        from cartomet_br.gui import main_window as mw

        eventos = []

        class _FakeSignal:
            def connect(self, *a, **k):
                eventos.append("connect")

        class _FakeThread:
            def __init__(self, **kw):
                self.progress = _FakeSignal()
                self.finished_ok = _FakeSignal()
                self.finished_error = _FakeSignal()

            def start(self):
                eventos.append("start")

            def isRunning(self):
                return False

        monkeypatch.setattr(mw, "ERA5DownloadThread", _FakeThread)
        window._case_stage = "Caso: Teste — camada 1/2"
        window._on_add_era5_layer(
            "era5_mslp",
            "2020-06-30",
            "2020-06-30",
            12,
            0,
            "hora",
            extent_override=[-70.0, -40.0, -30.0, 0.0],
            queue_connect=lambda th: eventos.append("fila"),
        )
        assert eventos.index("fila") < eventos.index("start")
        # Extent do caso aplicado ao motor (não os spinboxes vivos).
        assert window.config.extent == [-70.0, -40.0, -30.0, 0.0]
        # O estágio do caso é visível: título do modal + botão de parar a fila.
        assert "Caso: Teste — camada 1/2" in window._era5_dl_dialog.windowTitle()
        assert window._era5_dl_dialog.cancel_btn.isEnabled()
        window._era5_dl_dialog.finish_ok()

    def test_download_avulso_mantem_botao_cancelar_fora(self, window, monkeypatch):
        from cartomet_br.gui import main_window as mw

        class _FakeSignal:
            def connect(self, *a, **k):
                pass

        class _FakeThread:
            def __init__(self, **kw):
                self.progress = _FakeSignal()
                self.finished_ok = _FakeSignal()
                self.finished_error = _FakeSignal()

            def start(self):
                pass

            def isRunning(self):
                return False

        monkeypatch.setattr(mw, "ERA5DownloadThread", _FakeThread)
        window._case_stage = ""
        window._on_add_era5_layer("era5_mslp", "2020-06-30", "2020-06-30", 12, 0, "hora")
        assert not window._era5_dl_dialog.cancel_btn.isEnabled()
        window._era5_dl_dialog.finish_ok()


class TestEscDoDialogoDeProgresso:
    def test_esc_nao_vaza_do_modal(self, qapp):
        # O reject() default fechava o modal com o download vivo — Esc agora
        # vira o clique no Cancelar (habilitado) ou é engolido (desabilitado).
        from PyQt6.QtCore import QEvent, Qt
        from PyQt6.QtGui import QKeyEvent

        from cartomet_br.gui.download_dialog import DownloadProgressDialog

        esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        pedidos = []

        dlg = DownloadProgressDialog("t")
        dlg.cancel_requested.connect(lambda: pedidos.append(1))
        dlg.keyPressEvent(esc)
        assert pedidos == [1]  # habilitado → cancelamento de verdade
        assert dlg.result() == 0  # e o diálogo NÃO foi rejeitado/fechado

        dlg2 = DownloadProgressDialog("t")
        dlg2.cancel_btn.setEnabled(False)
        dlg2.cancel_requested.connect(lambda: pedidos.append(2))
        dlg2.keyPressEvent(esc)
        assert pedidos == [1]  # desabilitado → Esc engolido, nada emitido
        dlg.deleteLater()
        dlg2.deleteLater()
