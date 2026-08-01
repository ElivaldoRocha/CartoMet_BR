"""Testes dos Raios GLM (Onda 4) — tudo offline (S3 mockado, netCDF sintético)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from cartomet_br.data import glm_lightning as glm
from cartomet_br.data.glm_lightning import (
    GlmCancelled,
    GlmError,
    GLMLightningData,
    fetch_glm_flashes,
    list_glm_keys,
    parse_start_time,
    window_label,
)

NOW = datetime(2026, 7, 31, 22, 0, 0, tzinfo=UTC)


def _key(sat_id: str, start: datetime) -> str:
    stamp = f"{start:%Y}{start:%j}{start:%H}{start:%M}{start:%S}0"
    return (
        f"GLM-L2-LCFA/{start:%Y}/{start:%j}/{start:%H}/"
        f"OR_GLM-L2-LCFA_{sat_id}_s{stamp}_e{stamp}_c{stamp}.nc"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Parse + prefixos
# ═══════════════════════════════════════════════════════════════════════════════


class TestParsing:
    def test_parse_start_time(self):
        t = datetime(2026, 7, 31, 21, 58, 20, tzinfo=UTC)
        assert parse_start_time(_key("G19", t).rsplit("/", 1)[-1]) == t

    def test_parse_lixo_vira_none(self):
        assert parse_start_time("nao_e_um_arquivo_goes.nc") is None
        assert parse_start_time("OR_GLM-L2-LCFA_G19_sXXXX.nc") is None

    def test_hour_prefixes_janela_numa_hora(self):
        start = datetime(2026, 7, 31, 21, 10, tzinfo=UTC)
        end = datetime(2026, 7, 31, 21, 25, tzinfo=UTC)
        assert glm._hour_prefixes(start, end) == ["GLM-L2-LCFA/2026/212/21/"]

    def test_hour_prefixes_janela_cruzando_a_hora(self):
        start = datetime(2026, 7, 31, 21, 50, tzinfo=UTC)
        end = datetime(2026, 7, 31, 22, 5, tzinfo=UTC)
        assert glm._hour_prefixes(start, end) == [
            "GLM-L2-LCFA/2026/212/21/",
            "GLM-L2-LCFA/2026/212/22/",
        ]


class TestWindowLabel:
    """Fonte única dos carimbos: sempre com ANO; data do fim quando cruza o dia."""

    def test_mesmo_dia_com_ano(self):
        assert (
            window_label(
                datetime(2026, 7, 31, 22, 15, tzinfo=UTC),
                datetime(2026, 7, 31, 22, 30, tzinfo=UTC),
            )
            == "31/07/2026 22:15–22:30 UTC"
        )

    def test_cruzando_a_meia_noite_datas_dos_dois_lados(self):
        # Achado da revisão: '31/12 23:50–00:05' datava o fim 24h errado.
        assert (
            window_label(
                datetime(2025, 12, 31, 23, 50, tzinfo=UTC),
                datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
            )
            == "31/12/2025 23:50–01/01/2026 00:05 UTC"
        )

    def test_segundos_so_quando_usados(self):
        label = window_label(
            datetime(2026, 7, 30, 23, 44, 40, tzinfo=UTC),
            datetime(2026, 7, 30, 23, 59, 40, tzinfo=UTC),
        )
        assert label == "30/07/2026 23:44:40–23:59:40 UTC"


# ═══════════════════════════════════════════════════════════════════════════════
#  Listagem S3 (requests mockado)
# ═══════════════════════════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(self, keys):
        self.text = "".join(f"<Key>{k}</Key>" for k in keys)

    def raise_for_status(self):
        pass


class TestListKeys:
    def test_filtra_pela_janela_e_prefere_g19(self, monkeypatch):
        import requests

        inside = _key("G19", NOW - timedelta(minutes=5))
        outside = _key("G19", NOW - timedelta(minutes=40))

        def fake_get(url, timeout=30):
            if "noaa-goes19" in url:
                return _FakeResp([inside, outside])
            return _FakeResp([_key("G16", NOW - timedelta(minutes=5))])

        monkeypatch.setattr(requests, "get", fake_get)
        bucket, sat, keys = list_glm_keys(NOW - timedelta(minutes=15), NOW)
        assert sat == "GOES-19"
        assert "noaa-goes19" in bucket
        assert keys == [inside]  # o de 40 min atrás ficou fora da janela

    def test_fallback_para_g16(self, monkeypatch):
        import requests

        g16 = _key("G16", NOW - timedelta(minutes=3))

        def fake_get(url, timeout=30):
            if "noaa-goes19" in url:
                return _FakeResp([])
            return _FakeResp([g16])

        monkeypatch.setattr(requests, "get", fake_get)
        _, sat, keys = list_glm_keys(NOW - timedelta(minutes=15), NOW)
        assert sat == "GOES-16"
        assert keys == [g16]

    def test_nada_em_nenhum_satelite_vira_erro_amigavel(self, monkeypatch):
        import requests

        monkeypatch.setattr(requests, "get", lambda url, timeout=30: _FakeResp([]))
        with pytest.raises(GlmError, match="Nenhum arquivo GLM"):
            list_glm_keys(NOW - timedelta(minutes=15), NOW)

    def test_granulo_que_comeca_no_fim_exato_fica_fora(self, monkeypatch):
        # Achado da revisão: o granulo _s == fim cobre os 20 s SEGUINTES —
        # seus flashes são posteriores ao instante pedido (fim EXCLUSIVO).
        import requests

        inside = _key("G19", NOW - timedelta(seconds=20))
        at_end = _key("G19", NOW)
        monkeypatch.setattr(requests, "get", lambda url, timeout=30: _FakeResp([inside, at_end]))
        _, _, keys = list_glm_keys(NOW - timedelta(minutes=15), NOW)
        assert keys == [inside]

    def test_janela_historica_pre_g19_prefere_g16(self, monkeypatch):
        # Achado da revisão: antes de 04/04/2025 o bucket do G19 tem dados
        # PRELIMINARES do checkout — o GOES-East da época era o G16.
        import requests

        past = datetime(2025, 2, 15, 12, 0, tzinfo=UTC)
        g19 = _key("G19", past - timedelta(minutes=5))
        g16 = _key("G16", past - timedelta(minutes=5))
        g19_now = _key("G19", NOW - timedelta(minutes=5))

        def fake_get(url, timeout=30):
            return _FakeResp([g19, g19_now] if "noaa-goes19" in url else [g16])

        monkeypatch.setattr(requests, "get", fake_get)
        _, sat, keys = list_glm_keys(past - timedelta(minutes=15), past)
        assert sat == "GOES-16"
        assert keys == [g16]
        # Depois da operacionalização, o G19 volta a ter preferência.
        _, sat_now, _ = list_glm_keys(NOW - timedelta(minutes=15), NOW)
        assert sat_now == "GOES-19"


# ═══════════════════════════════════════════════════════════════════════════════
#  Leitor de netCDF (arquivo sintético REAL)
# ═══════════════════════════════════════════════════════════════════════════════


class TestReadFlashes:
    def test_le_flash_lat_lon(self, tmp_path):
        import xarray as xr

        path = tmp_path / "glm.nc"
        xr.Dataset(
            {
                "flash_lat": ("nf", [-3.5, -10.2]),
                "flash_lon": ("nf", [-60.1, -48.7]),
            }
        ).to_netcdf(path, engine="netcdf4")
        lons, lats = glm._read_flashes(path)
        assert lons.tolist() == [-60.1, -48.7]
        assert lats.tolist() == [-3.5, -10.2]


# ═══════════════════════════════════════════════════════════════════════════════
#  fetch_glm_flashes (janela sintética, sem rede)
# ═══════════════════════════════════════════════════════════════════════════════


def _wire_fake_window(monkeypatch, files):
    """files: {key: (lons, lats)} — liga listagem/download/leitura falsos."""
    keys = sorted(files)
    monkeypatch.setattr(
        glm, "list_glm_keys", lambda s, e, **kw: ("https://bucket", "GOES-19", keys)
    )
    monkeypatch.setattr(
        glm, "_ensure_file", lambda bucket, key, d, **kw: d / key.rsplit("/", 1)[-1]
    )
    by_name = {k.rsplit("/", 1)[-1]: v for k, v in files.items()}

    def fake_read(path):
        lons, lats = by_name[path.name]
        return np.asarray(lons, dtype=float), np.asarray(lats, dtype=float)

    monkeypatch.setattr(glm, "_read_flashes", fake_read)


class TestFetch:
    def test_idades_e_recorte(self, tmp_path, monkeypatch):
        recent = _key("G19", NOW - timedelta(minutes=2))
        old = _key("G19", NOW - timedelta(minutes=12))
        _wire_fake_window(
            monkeypatch,
            {
                recent: ([-60.0, -100.0], [-3.0, -3.0]),  # 2º ponto fora do recorte
                old: ([-55.0], [-10.0]),
            },
        )
        data = fetch_glm_flashes(tmp_path, extent=[-75.0, -35.0, -30.0, 6.0], now=NOW)
        assert data.n_flashes == 2  # o ponto em 100°W caiu no filtro
        assert data.satellite == "GOES-19"
        assert data.n_files == 2
        ages = sorted(data.ages_min.tolist())
        assert ages[0] == pytest.approx(2.0)
        assert ages[1] == pytest.approx(12.0)
        assert data.window_end == NOW

    def test_zero_flashes_e_resultado_valido(self, tmp_path, monkeypatch):
        # Céu limpo: arquivos existem mas sem flash no recorte — NÃO é erro.
        k = _key("G19", NOW - timedelta(minutes=5))
        _wire_fake_window(monkeypatch, {k: ([], [])})
        data = fetch_glm_flashes(tmp_path, extent=[-75, -35, -30, 6], now=NOW)
        assert data.n_flashes == 0
        assert data.n_files == 1

    def test_arquivo_podre_nao_derruba_a_janela(self, tmp_path, monkeypatch):
        ok = _key("G19", NOW - timedelta(minutes=3))
        bad = _key("G19", NOW - timedelta(minutes=6))
        _wire_fake_window(monkeypatch, {ok: ([-50.0], [-5.0]), bad: ([-50.0], [-5.0])})

        original = glm._read_flashes

        def flaky(path):
            if path.name == bad.rsplit("/", 1)[-1]:
                raise OSError("netCDF corrompido")
            return original(path)

        monkeypatch.setattr(glm, "_read_flashes", flaky)
        data = fetch_glm_flashes(tmp_path, now=NOW)
        assert data.n_flashes == 1
        assert data.n_files == 1  # só o legível conta

    def test_todos_ilegiveis_vira_erro(self, tmp_path, monkeypatch):
        k = _key("G19", NOW - timedelta(minutes=3))
        _wire_fake_window(monkeypatch, {k: ([-50.0], [-5.0])})
        monkeypatch.setattr(glm, "_read_flashes", lambda p: (_ for _ in ()).throw(OSError("x")))
        with pytest.raises(GlmError, match="nenhum pôde ser lido"):
            fetch_glm_flashes(tmp_path, now=NOW)

    def test_cancelamento_cooperativo(self, tmp_path, monkeypatch):
        k = _key("G19", NOW - timedelta(minutes=3))
        _wire_fake_window(monkeypatch, {k: ([-50.0], [-5.0])})
        with pytest.raises(GlmCancelled):
            fetch_glm_flashes(tmp_path, now=NOW, cancel_check=lambda: True)

    def test_now_naive_e_tratado_como_utc(self, tmp_path, monkeypatch):
        # Achado da revisão: naive quebrava com TypeError no filtro aware.
        k = _key("G19", NOW - timedelta(minutes=5))
        _wire_fake_window(monkeypatch, {k: ([-50.0], [-5.0])})
        data = fetch_glm_flashes(tmp_path, now=NOW.replace(tzinfo=None))
        assert data.window_end == NOW
        assert data.live is False

    def test_now_aware_de_outro_fuso_e_convertido(self, tmp_path, monkeypatch):
        # Achado da revisão: fuso de Belém listaria a pasta de hora errada no S3.
        from datetime import timezone as _tz

        k = _key("G19", NOW - timedelta(minutes=5))
        _wire_fake_window(monkeypatch, {k: ([-50.0], [-5.0])})
        belem = _tz(timedelta(hours=-3))
        data = fetch_glm_flashes(tmp_path, now=NOW.astimezone(belem))
        assert data.window_end == NOW

    def test_fim_no_futuro_recusado_antes_da_rede(self, tmp_path, monkeypatch):
        # Achado da revisão: typo de ano gastava 4 requisições e culpava o S3.
        chamados: list[int] = []
        monkeypatch.setattr(
            glm,
            "list_glm_keys",
            lambda *a, **kw: chamados.append(1) or ("b", "GOES-19", []),
        )
        futuro = datetime.now(UTC) + timedelta(hours=1)
        with pytest.raises(GlmError, match="futuro"):
            fetch_glm_flashes(tmp_path, now=futuro)
        assert chamados == []  # nem tocou a listagem

    def test_modo_agora_marca_live(self, tmp_path, monkeypatch):
        k = _key("G19", NOW - timedelta(minutes=5))
        _wire_fake_window(monkeypatch, {k: ([-50.0], [-5.0])})
        data = fetch_glm_flashes(tmp_path)  # now=None
        assert data.live is True


# ═══════════════════════════════════════════════════════════════════════════════
#  Canvas (offscreen)
# ═══════════════════════════════════════════════════════════════════════════════


def _sample_data(n_recent=3, n_mid=2, n_old=1) -> GLMLightningData:
    ages = [1.0] * n_recent + [7.0] * n_mid + [13.0] * n_old
    n = len(ages)
    return GLMLightningData(
        lons=np.linspace(-60, -40, n),
        lats=np.linspace(-20, -5, n),
        ages_min=np.asarray(ages),
        window_start=NOW - timedelta(minutes=15),
        window_end=NOW,
        satellite="GOES-19",
        n_files=45,
    )


class TestGlmCanvas:
    def test_render_cria_3_faixas_e_legenda(self, canvas):
        data = _sample_data()
        canvas.render_glm_lightning(data)
        assert len(canvas._glm_artists) == 4  # 3 scatters + legenda
        from matplotlib.legend import Legend

        legend = next(a for a in canvas._glm_artists if isinstance(a, Legend))
        title_txt = legend.get_title().get_text()
        assert "Raios GLM" in title_txt
        assert "GOES-19" in title_txt
        assert "31/07/2026" in title_txt  # a janela carimba data COM ANO
        assert "cor = minutos antes do fim" in title_txt  # semântica viaja no PNG
        scatters = [a for a in canvas._glm_artists if not isinstance(a, Legend)]
        counts = sorted(len(s.get_offsets()) for s in scatters)
        assert counts == [1, 2, 3]  # 0–5 / 5–10 / 10–15

    def test_toggle_e_remove(self, canvas):
        canvas.render_glm_lightning(_sample_data())
        canvas.toggle_glm_lightning(False)
        assert all(not a.get_visible() for a in canvas._glm_artists)
        canvas.remove_glm_lightning()
        assert canvas._glm_artists == []

    def test_rerender_substitui(self, canvas):
        canvas.render_glm_lightning(_sample_data())
        canvas.render_glm_lightning(_sample_data(n_recent=1, n_mid=0, n_old=0))
        assert len(canvas._glm_artists) == 4  # substituiu, não acumulou

    def test_legenda_cruzando_meia_noite_data_dos_dois_lados(self, canvas):
        from matplotlib.legend import Legend

        data = _sample_data()
        data.window_start = datetime(2025, 12, 31, 23, 50, tzinfo=UTC)
        data.window_end = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        canvas.render_glm_lightning(data)
        legend = next(a for a in canvas._glm_artists if isinstance(a, Legend))
        title_txt = legend.get_title().get_text()
        assert "31/12/2025" in title_txt
        assert "01/01/2026" in title_txt  # o fim NÃO herda a data do início


# ═══════════════════════════════════════════════════════════════════════════════
#  Painel (offscreen)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGlmPanel:
    def test_botao_no_modo_agora_emite_none(self, qapp):
        from PyQt6.QtWidgets import QPushButton

        from cartomet_br.gui.layer_panel import FieldLayerPanel

        panel = FieldLayerPanel()
        assert panel.glm_now_check.isChecked()  # default honesto: ao vivo
        assert not panel.glm_datetime_edit.isEnabled()
        btn = next(b for b in panel.findChildren(QPushButton) if "Raios GLM" in b.text())
        got: list = []
        panel.glm_lightning_requested.connect(got.append)
        btn.click()
        assert got == [None]

    def test_data_livre_emite_datetime_utc_com_segundos(self, qapp):
        # Diretriz do usuário: como no canal 13, sem prender ao presente —
        # data/hora/minuto/SEGUNDO livres, interpretados como UTC.
        from PyQt6.QtCore import QDate, QDateTime, QTime, QTimeZone

        from cartomet_br.gui.layer_panel import FieldLayerPanel

        panel = FieldLayerPanel()
        panel.glm_now_check.setChecked(False)
        assert panel.glm_datetime_edit.isEnabled()
        panel.glm_datetime_edit.setDateTime(
            QDateTime(QDate(2026, 2, 14), QTime(22, 30, 40), QTimeZone.utc())
        )
        got: list = []
        panel.glm_lightning_requested.connect(got.append)
        panel._on_glm_clicked()
        assert got == [datetime(2026, 2, 14, 22, 30, 40, tzinfo=UTC)]

    def test_religar_agora_desabilita_o_seletor(self, qapp):
        from cartomet_br.gui.layer_panel import FieldLayerPanel

        panel = FieldLayerPanel()
        panel.glm_now_check.setChecked(False)
        panel.glm_now_check.setChecked(True)
        assert not panel.glm_datetime_edit.isEnabled()

    def test_valor_digitado_sobrevive_ao_toggle(self, qapp):
        # Achado da revisão: cada desmarcação resetava o seletor para o agora,
        # apagando o caso histórico digitado (fluxo comparar caso × presente).
        from PyQt6.QtCore import QDate, QDateTime, QTime, QTimeZone

        from cartomet_br.gui.layer_panel import FieldLayerPanel

        panel = FieldLayerPanel()
        panel.glm_now_check.setChecked(False)  # 1ª liberação: posiciona no agora
        alvo = QDateTime(QDate(2026, 2, 14), QTime(22, 30, 40), QTimeZone.utc())
        panel.glm_datetime_edit.setDateTime(alvo)
        panel.glm_now_check.setChecked(True)
        panel.glm_now_check.setChecked(False)  # volta: valor PRESERVADO
        assert panel.glm_datetime_edit.dateTime().toPyDateTime() == alvo.toPyDateTime()

    def test_milissegundos_zerados_no_emit(self, qapp):
        # Achado da revisão: msec herdado do relógio ficava invisível no display
        # e fazia o pedido divergir sub-segundo do que a tela mostra.
        from PyQt6.QtCore import QDate, QDateTime, QTime, QTimeZone

        from cartomet_br.gui.layer_panel import FieldLayerPanel

        panel = FieldLayerPanel()
        panel.glm_now_check.setChecked(False)
        panel.glm_datetime_edit.setDateTime(
            QDateTime(QDate(2026, 2, 14), QTime(22, 30, 40, 567), QTimeZone.utc())
        )
        got: list = []
        panel.glm_lightning_requested.connect(got.append)
        panel._on_glm_clicked()
        [when] = got
        assert when.second == 40
        assert when.microsecond == 0
