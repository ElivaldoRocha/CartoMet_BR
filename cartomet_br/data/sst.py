"""
Download e processamento de dados MUR SST (1 km) via ERDDAP.

Fonte: NASA/NOAA Multi-scale Ultra-high Resolution (MUR) SST Analysis
       https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41

Resolução nativa: 0.01° (~1 km) — reduzida via stride para performance.
Cobertura: Global, quasi-diária (latência ~2 dias).
Variável: analysed_sst (°C — CF conventions aplicadas pelo ERDDAP).

Estratégia de download
----------------------
Usa a API de subsetting REST do ERDDAP para baixar apenas o recorte
espacial/temporal necessário como arquivo NetCDF.  Isso evita o problema
de latência do OPeNDAP puro, que precisa carregar metadados de toda a
dimensão temporal (~24 anos de dados diários) antes de qualquer operação.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# URL base do ERDDAP — dataset MUR SST v4.1
ERDDAP_URL = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41"


@dataclass
class SSTData:
    """Container para dados de TSM (Temperatura da Superfície do Mar)."""

    sst: np.ndarray  # Temperatura em °C (2D: lat × lon)
    lons: np.ndarray  # Longitudes 1D
    lats: np.ndarray  # Latitudes 1D
    time_str: str  # Data da análise (ex.: "2026-03-23")
    source: str = "MUR SST 1km — NASA/NOAA"


def download_mur_sst(
    target_date: datetime | None = None,
    extent: list[float] | None = None,
    data_dir: Path | None = None,
    force_download: bool = False,
    progress_callback=None,
    stride: int = 5,
) -> SSTData:
    """Baixa dados MUR SST via ERDDAP server-side subsetting.

    Constrói uma URL com constraints que fazem o servidor recortar
    espaço + tempo antes de enviar, eliminando a latência do OPeNDAP.

    Parameters
    ----------
    target_date : datetime, optional
        Data alvo para a análise SST. None = dado mais recente.
    extent : list[float], optional
        [lon_min, lat_min, lon_max, lat_max]. Padrão: América do Sul.
    data_dir : Path, optional
        Diretório para cache de arquivos NetCDF.
    force_download : bool
        Se True, ignora o cache local.
    progress_callback : callable, optional
        Função (kind, value) — kind: "status"|"percent".
    stride : int
        Passo de amostragem espacial (1=1km, 5≈5km, 10≈10km).
        stride=5 é bom equilíbrio entre resolução e velocidade.

    Returns
    -------
    SSTData
        Container com SST, coordenadas e metadados.
    """
    import pandas as pd
    import requests
    import xarray as xr

    def _emit(kind, value):
        if progress_callback:
            progress_callback(kind, value)

    # ── Extent padrão: América do Sul ──
    if extent is None:
        extent = [-100.0, -75.0, 0.0, 15.0]
    lon_min, lat_min, lon_max, lat_max = extent

    # ── Data alvo ──
    use_latest = target_date is None
    if use_latest:
        time_constraint = "last"
        date_label = "mais recente"
        date_tag = "latest"
    else:
        date_tag = target_date.strftime("%Y-%m-%d")
        # MUR SST análise diária: hora fixa 09:00:00Z
        time_constraint = f"{date_tag}T09:00:00Z"
        date_label = date_tag

    # ── Cache local (apenas para datas específicas) ──
    if data_dir is not None and not use_latest:
        cache_file = data_dir / f"mur_sst_{date_tag}_s{stride}.nc"
        if cache_file.exists() and not force_download:
            _emit("status", f"Cache encontrado: {cache_file.name}")
            _emit("percent", 100)
            logger.info("MUR SST cache hit: %s", cache_file)
            return _load_sst_from_file(cache_file)

    # ── Constrói URL com subsetting server-side ──
    # Formato ERDDAP griddap: var[(time)][(lat_min):stride:(lat_max)][(lon_min):stride:(lon_max)]
    _emit("status", f"Conectando ao ERDDAP — MUR SST {date_label}...")
    _emit("percent", 5)

    constraint_url = (
        f"{ERDDAP_URL}.nc?"
        f"analysed_sst[({time_constraint}):1:({time_constraint})]"
        f"[({lat_min}):{stride}:({lat_max})]"
        f"[({lon_min}):{stride}:({lon_max})]"
    )

    logger.info("MUR SST request URL: %s", constraint_url)
    res_km = stride * 0.01 * 111  # resolução efetiva aproximada em km
    _emit("status", f"Baixando TSM ({date_label}) — resolução ~{res_km:.0f} km...")
    _emit("percent", 10)

    # ── Download com progresso em bytes ──
    try:
        resp = requests.get(constraint_url, timeout=300, stream=True)
        resp.raise_for_status()
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status in (404, 400):
            raise RuntimeError(
                f"Data {date_label} não disponível no MUR SST.\n\n"
                f"O MUR SST tem ~2 dias de latência em relação ao dia atual.\n"
                f"Tente uma data mais antiga."
            ) from e
        raise RuntimeError(f"Erro HTTP {status} ao acessar ERDDAP:\n{e}") from e
    except requests.ConnectionError as e:
        raise RuntimeError(
            "Não foi possível conectar ao ERDDAP.\nVerifique sua conexão com a internet."
        ) from e
    except requests.Timeout:
        raise RuntimeError(
            "Timeout ao conectar ao ERDDAP (300s).\nO servidor pode estar lento — tente novamente."
        )

    total_size = int(resp.headers.get("content-length", 0))
    downloaded = 0

    # Download para arquivo temporário
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".nc")
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    pct = 10 + int(downloaded * 75 / total_size)
                    _emit("percent", min(pct, 85))
                    # Atualiza status a cada ~256 KB
                    if downloaded % (256 * 1024) < 65536:
                        mb_down = downloaded / (1024 * 1024)
                        mb_total = total_size / (1024 * 1024)
                        _emit("status", f"Baixando... {mb_down:.1f} / {mb_total:.1f} MB")
                else:
                    # Sem content-length: mostra apenas bytes baixados
                    if downloaded % (256 * 1024) < 65536:
                        mb_down = downloaded / (1024 * 1024)
                        _emit("status", f"Baixando... {mb_down:.1f} MB")

        _emit("status", "Processando dados SST...")
        _emit("percent", 90)

        # ── Abre e extrai dados ──
        ds = xr.open_dataset(tmp_path, engine="netcdf4")

        sst_arr = ds["analysed_sst"].values
        if sst_arr.ndim == 3:
            sst_arr = sst_arr.squeeze(axis=0)

        lons = ds["longitude"].values
        lats = ds["latitude"].values

        # Extrai data real da análise
        actual_date_str = date_tag
        if "time" in ds.coords:
            try:
                t_val = ds["time"].values
                if hasattr(t_val, "__len__"):
                    t_val = t_val[0]
                actual_date_str = pd.to_datetime(t_val).strftime("%Y-%m-%d")
            except Exception:
                pass

        ds.close()

        # ── Salva no cache ──
        if data_dir is not None:
            final_cache = data_dir / f"mur_sst_{actual_date_str}_s{stride}.nc"
            if not final_cache.exists():
                try:
                    final_cache.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(tmp_path, final_cache)
                    logger.info("MUR SST salvo em cache: %s", final_cache)
                except Exception as e:
                    logger.warning("Falha ao salvar cache SST: %s", e)

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    _emit("status", f"TSM carregada — {actual_date_str}")
    _emit("percent", 100)

    logger.info(
        "MUR SST carregado: %s, shape=%s, stride=%d", actual_date_str, sst_arr.shape, stride
    )

    return SSTData(
        sst=sst_arr,
        lons=lons,
        lats=lats,
        time_str=actual_date_str,
    )


def _load_sst_from_file(filepath: Path) -> SSTData:
    """Carrega SSTData a partir de um arquivo NetCDF local (cache)."""
    import pandas as pd
    import xarray as xr

    ds = xr.open_dataset(filepath, engine="netcdf4")

    sst_arr = ds["analysed_sst"].values
    if sst_arr.ndim == 3:
        sst_arr = sst_arr.squeeze(axis=0)

    lons = ds["longitude"].values
    lats = ds["latitude"].values

    date_str = "desconhecida"
    if "time" in ds.coords:
        try:
            t_val = ds["time"].values
            if hasattr(t_val, "__len__"):
                t_val = t_val[0]
            date_str = pd.to_datetime(t_val).strftime("%Y-%m-%d")
        except Exception:
            pass

    ds.close()

    return SSTData(
        sst=sst_arr,
        lons=lons,
        lats=lats,
        time_str=date_str,
    )
