"""Testes da cadeia de fontes da TSM (MUR 1km → espelho → Blended 5km) — offline.

Contexto: em 31/07/2026 o nó oeste do ERDDAP (PFEG, os dois hostnames) saiu
do ar com timeout de conexão e o app culpava a internet do usuário. A cadeia
de contingência cai para o Geo-Polar Blended 5km do nó leste com rótulo
honesto da fonte real.
"""

import os
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from cartomet_br.data.sst import (
    SST_SOURCES,
    SSTData,
    _load_sst_from_file,
    download_mur_sst,
)

EXTENT = [-75.0, -35.0, -30.0, 6.0]


def _tiny_nc_bytes(date_iso: str = "2026-07-30T12:00:00") -> bytes:
    """netCDF real e minúsculo no formato que o ERDDAP entrega (°C)."""
    import pandas as pd
    import xarray as xr

    ds = xr.Dataset(
        {"analysed_sst": (("time", "latitude", "longitude"), np.full((1, 3, 4), 27.5))},
        coords={
            "time": pd.to_datetime([date_iso]),
            "latitude": [-22.0, -21.0, -20.0],
            "longitude": [-42.0, -41.0, -40.0, -39.0],
        },
    )
    fd, name = tempfile.mkstemp(suffix=".nc")
    os.close(fd)  # Windows: fd aberto do mkstemp travaria o unlink
    tmp = Path(name)
    try:
        ds.to_netcdf(tmp, engine="netcdf4")
        return tmp.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)


class _NcResp:
    """Resposta fake com o corpo netCDF (streaming de 1 chunk)."""

    status_code = 200

    def __init__(self, content: bytes):
        self._content = content
        self.headers = {"content-length": str(len(content))}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=65536):
        yield self._content


class _Http404Resp:
    status_code = 200  # o erro sai no raise_for_status, como no requests real

    def __init__(self):
        self.headers = {}

    def raise_for_status(self):
        import requests

        raise requests.HTTPError(response=SimpleNamespace(status_code=404))


def _wire(monkeypatch, behavior_by_host: dict):
    """Liga um requests.get fake roteado por hostname; captura URLs/headers."""
    import requests

    calls: list[dict] = []

    def fake_get(url, timeout=None, stream=False, headers=None, **kw):
        calls.append({"url": url, "timeout": timeout, "headers": headers or {}})
        for host, action in behavior_by_host.items():
            if host in url:
                if isinstance(action, Exception):
                    raise action
                return action() if callable(action) else action
        raise AssertionError(f"host inesperado: {url}")

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


class TestFallbackChain:
    def test_pfeg_fora_cai_no_blended_com_rotulo_honesto(self, tmp_path, monkeypatch):
        import requests

        nc = _tiny_nc_bytes()
        calls = _wire(
            monkeypatch,
            {
                "coastwatch.pfeg": requests.ConnectionError("down"),
                "upwell.pfeg": requests.ConnectionError("down"),
                "coastwatch.noaa.gov": lambda: _NcResp(nc),
            },
        )
        data = download_mur_sst(target_date=datetime(2026, 7, 30), extent=EXTENT, data_dir=tmp_path)
        assert "Blended" in data.source  # a carta dirá a fonte REAL
        assert np.allclose(data.sst, 27.5)
        assert data.time_str == "2026-07-30"
        # Cache com o prefixo do PRODUTO (não colide com o MUR)
        assert (tmp_path / "blended_sst_2026-07-30_s5.nc").exists()
        assert len(calls) == 3  # tentou os 3 na ordem

    def test_urls_por_fonte_hora_e_stride_efetivo(self, tmp_path, monkeypatch):
        import requests

        nc = _tiny_nc_bytes()
        calls = _wire(
            monkeypatch,
            {
                "coastwatch.pfeg": requests.ConnectionError("down"),
                "upwell.pfeg": requests.ConnectionError("down"),
                "coastwatch.noaa.gov": lambda: _NcResp(nc),
            },
        )
        download_mur_sst(target_date=datetime(2026, 7, 30), extent=EXTENT, data_dir=tmp_path)
        url_mur = calls[0]["url"]
        url_blended = calls[-1]["url"]
        # MUR: análise 09Z, grade 0.01° → stride pedido (5) direto
        assert "T09:00:00Z" in url_mur and ":5:" in url_mur
        # Blended: análise 12Z, grade 0.05° → stride EFETIVO 1 (preserva ~5 km)
        assert "T12:00:00Z" in url_blended and ":1:" in url_blended
        # WAF da NOAA: todos os pedidos levam User-Agent de navegador
        assert all("Mozilla" in c["headers"].get("User-Agent", "") for c in calls)

    def test_todas_fora_erro_nao_culpa_a_internet(self, tmp_path, monkeypatch):
        import requests

        _wire(
            monkeypatch,
            {
                "coastwatch.pfeg": requests.ConnectionError("down"),
                "upwell.pfeg": requests.ConnectionError("down"),
                "coastwatch.noaa.gov": requests.ConnectionError("down"),
            },
        )
        with pytest.raises(RuntimeError) as exc:
            download_mur_sst(target_date=datetime(2026, 7, 30), extent=EXTENT, data_dir=tmp_path)
        msg = str(exc.value)
        assert "NOAA" in msg
        assert "não precisa ser a sua internet" in msg
        assert "PFEG" in msg  # detalha o que foi tentado

    def test_data_indisponivel_em_todas_vira_msg_de_latencia(self, tmp_path, monkeypatch):
        _wire(
            monkeypatch,
            {
                "coastwatch.pfeg": lambda: _Http404Resp(),
                "upwell.pfeg": lambda: _Http404Resp(),
                "coastwatch.noaa.gov": lambda: _Http404Resp(),
            },
        )
        with pytest.raises(RuntimeError, match="latência"):
            download_mur_sst(target_date=datetime(2026, 7, 31), extent=EXTENT, data_dir=tmp_path)

    def test_mur_404_mas_blended_tem_a_data(self, tmp_path, monkeypatch):
        # Latências diferentes: o MUR (~2 dias) pode não ter a data que o
        # Blended (~1 dia) já publicou — 404 no MUR NÃO encerra a cadeia.
        nc = _tiny_nc_bytes("2026-07-31T12:00:00")
        _wire(
            monkeypatch,
            {
                "coastwatch.pfeg": lambda: _Http404Resp(),
                "upwell.pfeg": lambda: _Http404Resp(),
                "coastwatch.noaa.gov": lambda: _NcResp(nc),
            },
        )
        data = download_mur_sst(target_date=datetime(2026, 7, 31), extent=EXTENT, data_dir=tmp_path)
        assert "Blended" in data.source
        assert data.time_str == "2026-07-31"


class TestCache:
    def test_cache_hit_blended_sem_rede(self, tmp_path, monkeypatch):
        import requests

        cache = tmp_path / "blended_sst_2026-07-30_s5.nc"
        cache.write_bytes(_tiny_nc_bytes())

        def explode(*a, **kw):
            raise AssertionError("cache hit não pode ir à rede")

        monkeypatch.setattr(requests, "get", explode)
        data = download_mur_sst(target_date=datetime(2026, 7, 30), extent=EXTENT, data_dir=tmp_path)
        assert "Blended" in data.source  # fonte inferida do prefixo do arquivo

    def test_load_from_file_infere_mur(self, tmp_path):
        f = tmp_path / "mur_sst_2026-07-28_s5.nc"
        f.write_bytes(_tiny_nc_bytes("2026-07-28T09:00:00"))
        data = _load_sst_from_file(f)
        assert data.source.startswith("MUR SST 1km")

    def test_prefixos_de_cache_sao_unicos(self):
        prefixes = [s["cache_prefix"] for s in SST_SOURCES]
        assert len(set(prefixes)) == 2  # mur_sst (2 espelhos) + blended_sst


class TestCanvasHonesty:
    def test_titulo_e_colorbar_dizem_a_fonte_real(self, canvas):
        data = SSTData(
            sst=np.full((3, 4), 26.0),
            lons=np.array([-42.0, -41.0, -40.0, -39.0]),
            lats=np.array([-22.0, -21.0, -20.0]),
            time_str="2026-07-30",
            source="Geo-Polar Blended 5km (NOAA — contingência do MUR)",
        )
        canvas.plot_sst(data)
        title = canvas.ax.get_title(loc="left")
        assert "Blended" in title
        assert "MUR SST 1km (NASA/NOAA)" not in title
        cbar_label = canvas._sst_colorbar.ax.get_xlabel()
        assert "Blended" in cbar_label
