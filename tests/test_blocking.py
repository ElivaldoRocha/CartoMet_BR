"""Testes do motor de Bloqueio Atmosférico (anomalia de Z500) — SEM rede.

Fixture real: a cópia local da climatologia publicada (`climatology/z500/` na raiz
do repositório). As chamadas HTTP são substituídas por monkeypatch do módulo
`requests` dentro do blocking_engine.
"""

from __future__ import annotations

import shutil
import types
from pathlib import Path

import numpy as np
import pytest
import requests as real_requests

from cartomet_br.data import blocking_engine as be

CLIM_DIR = Path(__file__).resolve().parents[1] / "climatology" / "z500"
SAMPLE_MMDD = "0611"
SAMPLE_NAME = f"z500_clim_{SAMPLE_MMDD}.nc"

pytestmark = pytest.mark.skipif(
    not (CLIM_DIR / SAMPLE_NAME).exists(),
    reason="cópia local da climatologia (climatology/z500/) ausente",
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers de rede falsa
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def _patch_requests(monkeypatch, get_func):
    """Substitui o módulo requests DENTRO do engine (sem tocar o global)."""
    ns = types.SimpleNamespace(
        get=get_func,
        RequestException=real_requests.RequestException,
        ConnectionError=real_requests.ConnectionError,
    )
    monkeypatch.setattr(be, "requests", ns)
    monkeypatch.setattr(be, "_sleep", lambda s: None)   # backoff instantâneo


def _offline(monkeypatch):
    def _get(url, timeout=None):
        raise real_requests.ConnectionError("offline (teste)")
    _patch_requests(monkeypatch, _get)


def _online_manifest_only(monkeypatch):
    """Serve o manifest real; qualquer .nc pedido é violação de cache-first."""
    manifest_bytes = (CLIM_DIR / "manifest.json").read_bytes()

    def _get(url, timeout=None):
        if url.endswith("manifest.json"):
            return _FakeResponse(manifest_bytes)
        raise AssertionError(f"cache-first violado: tentou baixar {url}")
    _patch_requests(monkeypatch, _get)


def _online_full(monkeypatch):
    """Serve manifest e NetCDFs reais a partir da cópia local."""
    def _get(url, timeout=None):
        name = url.rsplit("/", 1)[-1]
        return _FakeResponse((CLIM_DIR / name).read_bytes())
    _patch_requests(monkeypatch, _get)


# ═══════════════════════════════════════════════════════════════════════════════
#  Constantes e slot climatológico
# ═══════════════════════════════════════════════════════════════════════════════

class TestConstants:
    def test_clim_extent_ordem_config(self):
        lon_min, lat_min, lon_max, lat_max = be.CLIM_EXTENT
        assert lon_min < lon_max and lat_min < lat_max
        assert be.CLIM_EXTENT == [-150.0, -75.0, 30.0, 15.0]

    def test_anom_levels_simetricos_com_zero(self):
        assert np.allclose(be.ANOM_LEVELS, -be.ANOM_LEVELS[::-1])
        assert 0.0 in be.ANOM_LEVELS


class TestResolveClimSlot:
    @pytest.mark.parametrize(
        ("valid_time", "mmdd", "hour", "approx"),
        [
            ("2026-06-11T00:00", "0611", 0, False),
            ("2026-06-11T06:00", "0611", 12, True),
            ("2026-06-11T09:00", "0611", 12, True),
            ("2026-06-11T12:00", "0611", 12, False),
            ("2026-06-11T15:00", "0611", 12, True),
            ("2026-06-11T18:00", "0611", 0, True),    # MMDD do PRÓPRIO dia
            ("2026-06-11T21:00", "0611", 0, True),
            ("2024-02-29T12:00", "0229", 12, False),  # bissexto coberto
        ],
    )
    def test_mapeamento(self, valid_time, mmdd, hour, approx):
        slot = be.resolve_clim_slot(valid_time)
        assert (slot.mmdd, slot.hour, slot.is_approx) == (mmdd, hour, approx)

    def test_formato_curto_sem_minutos(self):
        slot = be.resolve_clim_slot("2026-06-11T18")
        assert (slot.mmdd, slot.hour) == ("0611", 0)

    def test_malformado_erro_claro(self):
        with pytest.raises(be.BlockingDataError, match="valid_time"):
            be.resolve_clim_slot("11/06/2026 12:00")


# ═══════════════════════════════════════════════════════════════════════════════
#  ensure_climatology_file — cache-first + sha256 + offline
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnsureClimatology:
    def test_cache_valido_zero_download(self, tmp_path, monkeypatch):
        shutil.copy(CLIM_DIR / SAMPLE_NAME, tmp_path / SAMPLE_NAME)
        _online_manifest_only(monkeypatch)   # AssertionError se tentar baixar o .nc
        path, info = be.ensure_climatology_file(SAMPLE_MMDD, tmp_path)
        assert path == tmp_path / SAMPLE_NAME
        assert info == {"sha_verified": True, "from_cache": True}
        assert (tmp_path / "manifest.json").exists()   # manifest cacheado

    def test_corrompido_rebaixa_e_verifica(self, tmp_path, monkeypatch):
        good = (CLIM_DIR / SAMPLE_NAME).read_bytes()
        (tmp_path / SAMPLE_NAME).write_bytes(good[: len(good) // 2])   # truncado
        _online_full(monkeypatch)
        path, info = be.ensure_climatology_file(SAMPLE_MMDD, tmp_path)
        assert info == {"sha_verified": True, "from_cache": False}
        assert path.read_bytes() == good
        assert not list(tmp_path.glob("*.part"))   # sem órfãos

    def test_offline_com_cache_sem_manifest(self, tmp_path, monkeypatch):
        shutil.copy(CLIM_DIR / SAMPLE_NAME, tmp_path / SAMPLE_NAME)
        _offline(monkeypatch)
        avisos: list[str] = []
        path, info = be.ensure_climatology_file(
            SAMPLE_MMDD, tmp_path, progress_callback=avisos.append,
        )
        assert path == tmp_path / SAMPLE_NAME
        assert info == {"sha_verified": False, "from_cache": True}
        assert any("Sem conexão" in m for m in avisos)

    def test_offline_com_manifest_local_valida_sha(self, tmp_path, monkeypatch):
        shutil.copy(CLIM_DIR / SAMPLE_NAME, tmp_path / SAMPLE_NAME)
        shutil.copy(CLIM_DIR / "manifest.json", tmp_path / "manifest.json")
        _offline(monkeypatch)
        path, info = be.ensure_climatology_file(SAMPLE_MMDD, tmp_path)
        assert info == {"sha_verified": True, "from_cache": True}

    def test_offline_sem_nada_erro_amigavel(self, tmp_path, monkeypatch):
        _offline(monkeypatch)
        with pytest.raises(be.BlockingDataError, match="sem conexão|Sem conexão"):
            be.ensure_climatology_file(SAMPLE_MMDD, tmp_path)

    def test_cancelamento_cooperativo(self, tmp_path, monkeypatch):
        _online_full(monkeypatch)
        with pytest.raises(be.BlockingCancelled):
            be.ensure_climatology_file(
                SAMPLE_MMDD, tmp_path, cancel_check=lambda: True,
            )


# ═══════════════════════════════════════════════════════════════════════════════
#  load_climatology — estrutura real do NetCDF publicado
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadClimatology:
    def test_estrutura_real(self):
        values, lats, lons = be.load_climatology(CLIM_DIR / SAMPLE_NAME, 0)
        assert values.shape == (361, 721)
        assert lats[0] == pytest.approx(15.0) and lats[-1] == pytest.approx(-75.0)
        assert np.all(np.diff(lats) < 0)   # decrescente (ordem ECMWF)
        assert lons[0] == pytest.approx(-150.0) and lons[-1] == pytest.approx(30.0)
        assert np.nanmin(values) > 4500.0 and np.nanmax(values) < 6100.0   # gpm

    def test_horas_00_e_12_diferem(self):
        v00, _, _ = be.load_climatology(CLIM_DIR / SAMPLE_NAME, 0)
        v12, _, _ = be.load_climatology(CLIM_DIR / SAMPLE_NAME, 12)
        assert not np.allclose(v00, v12)

    def test_hora_inexistente_erro_claro(self):
        with pytest.raises(be.BlockingDataError, match="06Z"):
            be.load_climatology(CLIM_DIR / SAMPLE_NAME, 6)


# ═══════════════════════════════════════════════════════════════════════════════
#  compute_anomaly — subtração + travas de alinhamento
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeAnomaly:
    @pytest.fixture(scope="class")
    def clim(self):
        return be.load_climatology(CLIM_DIR / SAMPLE_NAME, 12)

    def test_anomalia_recupera_bump_sintetico(self, clim):
        values, lats, lons = clim
        lon2d, lat2d = np.meshgrid(lons, lats)
        # "bloqueio" sintético: +150 gpm centrado em 60°W / 45°S
        bump = 150.0 * np.exp(-(((lon2d + 60.0) / 10.0) ** 2 + ((lat2d + 45.0) / 8.0) ** 2))
        gh = values.astype(np.float64) + bump
        anom = be.compute_anomaly(gh, lats, lons, values, lats, lons)
        assert anom.dtype == np.float64
        assert np.allclose(anom, bump, atol=0.01)

    def test_latitude_invertida_aborta(self, clim):
        values, lats, lons = clim
        with pytest.raises(be.BlockingDataError, match="não alinham"):
            be.compute_anomaly(values, lats[::-1], lons, values, lats, lons)

    def test_shape_diferente_aborta(self, clim):
        values, lats, lons = clim
        with pytest.raises(be.BlockingDataError, match="incompatíveis"):
            be.compute_anomaly(values[:, :-1], lats, lons[:-1], values, lats, lons)


# ═══════════════════════════════════════════════════════════════════════════════
#  compute_blocking — guard de rodada (sem rede: falha ANTES de qualquer download)
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeBlockingGuards:
    def test_rodada_indeterminada(self, tmp_path):
        with pytest.raises(be.BlockingDataError, match="Verificar Rodadas"):
            be.compute_blocking(cycle=None, cycle_date=None, data_dir=tmp_path,
                                clim_dir=tmp_path)

    def test_data_vazia(self, tmp_path):
        with pytest.raises(be.BlockingDataError, match="Verificar Rodadas"):
            be.compute_blocking(cycle=0, cycle_date="", data_dir=tmp_path,
                                clim_dir=tmp_path)
