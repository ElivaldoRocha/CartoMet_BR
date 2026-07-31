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
        assert "Raios GLM" in legend.get_title().get_text()
        assert "GOES-19" in legend.get_title().get_text()
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


# ═══════════════════════════════════════════════════════════════════════════════
#  Painel (offscreen)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGlmPanel:
    def test_botao_emite_o_sinal(self, qapp):
        from PyQt6.QtWidgets import QPushButton

        from cartomet_br.gui.layer_panel import FieldLayerPanel

        panel = FieldLayerPanel()
        btn = next(b for b in panel.findChildren(QPushButton) if "Raios GLM" in b.text())
        got: list[int] = []
        panel.glm_lightning_requested.connect(lambda: got.append(1))
        btn.click()
        assert got == [1]
