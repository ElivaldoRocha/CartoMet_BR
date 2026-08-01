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

import contextlib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# URL base do ERDDAP — dataset MUR SST v4.1 (mantida p/ compatibilidade)
ERDDAP_URL = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41"

# O WAF da NOAA (nó leste) responde 403 ao User-Agent padrão do requests —
# um UA de navegador libera todos os nós.
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CartoMetBR"}

# Conexão curta + leitura longa: um servidor FORA DO AR falha em ~15 s e a
# cadeia cai para o próximo espelho, em vez de prender o worker por minutos.
CONNECT_TIMEOUT_S = 15
READ_TIMEOUT_S = 300

# Cadeia de fontes, na ordem de tentativa. O MUR 1km (nó oeste/PFEG) é o
# produto titular; em 31/07/2026 o PFEG saiu do ar (timeout de conexão nos
# dois hostnames) e o Geo-Polar Blended 5km global do nó leste entrou como
# CONTINGÊNCIA honesta — mesma variável `analysed_sst`, mesmas unidades (°C
# via ERDDAP), mesma sintaxe griddap; só muda a grade (0.05°) e a hora da
# análise diária (12Z em vez de 09Z). O rótulo da carta sempre diz qual
# fonte serviu (SSTData.source).
SST_SOURCES: tuple[dict, ...] = (
    {
        "base": "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41",
        "nome": "MUR SST 1km (NASA/NOAA)",
        "curto": "MUR 1km (PFEG)",
        "grid_deg": 0.01,
        "hora": "09:00:00Z",
        "cache_prefix": "mur_sst",
    },
    {
        "base": "https://upwell.pfeg.noaa.gov/erddap/griddap/jplMURSST41",
        "nome": "MUR SST 1km (NASA/NOAA)",
        "curto": "MUR 1km (espelho upwell)",
        "grid_deg": 0.01,
        "hora": "09:00:00Z",
        "cache_prefix": "mur_sst",
    },
    {
        "base": "https://coastwatch.noaa.gov/erddap/griddap/noaacwBLENDEDsstDNDaily",
        "nome": "Geo-Polar Blended 5km (NOAA — contingência do MUR)",
        "curto": "Blended 5km (nó leste)",
        "grid_deg": 0.05,
        "hora": "12:00:00Z",
        "cache_prefix": "blended_sst",
    },
)


@dataclass
class SSTData:
    """Container para dados de TSM (Temperatura da Superfície do Mar)."""

    sst: np.ndarray  # Temperatura em °C (2D: lat × lon)
    lons: np.ndarray  # Longitudes 1D
    lats: np.ndarray  # Latitudes 1D
    time_str: str  # Data da análise (ex.: "2026-03-23")
    # Qual produto serviu o dado (MUR 1km ou o fallback Blended 5km) —
    # vai para o título da carta (honestidade: a fonte real, sempre).
    source: str = "MUR SST 1km (NASA/NOAA)"


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
        date_label = "mais recente"
        date_tag = "latest"
    else:
        assert target_date is not None  # use_latest == False ⇒ target_date definido
        date_tag = target_date.strftime("%Y-%m-%d")
        date_label = date_tag

    # ── Cache local (apenas para datas específicas) — por PRODUTO ──
    if data_dir is not None and not use_latest and not force_download:
        for src in SST_SOURCES:
            cache_file = data_dir / f"{src['cache_prefix']}_{date_tag}_s{stride}.nc"
            if cache_file.exists():
                _emit("status", f"Cache encontrado: {cache_file.name}")
                _emit("percent", 100)
                logger.info("TSM cache hit: %s", cache_file)
                return _load_sst_from_file(cache_file)

    # ── Cadeia de fontes: tenta cada uma até obter o dado ──
    errors: list[str] = []
    date_missing = 0
    for i, src in enumerate(SST_SOURCES):
        _emit("status", f"Conectando: {src['curto']} — TSM {date_label}...")
        _emit("percent", 5)
        try:
            return _download_from_source(
                src,
                use_latest=use_latest,
                date_tag=date_tag,
                date_label=date_label,
                extent=extent,
                data_dir=data_dir,
                stride=stride,
                emit=_emit,
            )
        except _DateUnavailable as e:
            date_missing += 1
            errors.append(f"• {src['curto']}: {e}")
            logger.warning("TSM: %s sem a data %s", src["curto"], date_label)
        except Exception as e:  # rede/timeout/HTTP/parse — tenta o próximo espelho
            errors.append(f"• {src['curto']}: {e}")
            logger.warning("TSM: falha em %s: %s", src["curto"], e)
        if i < len(SST_SOURCES) - 1:
            _emit("status", f"{src['curto']} indisponível — tentando o próximo espelho...")

    # ── Todas as fontes falharam — erro honesto (não culpar a internet) ──
    detalhes = "\n".join(errors)
    if date_missing == len(SST_SOURCES):
        raise RuntimeError(
            f"A data {date_label} não está disponível em nenhuma das fontes de TSM.\n\n"
            "O MUR tem ~2 dias de latência (e o Blended ~1 dia) em relação ao "
            "dia atual. Tente uma data mais antiga.\n\n"
            f"{detalhes}"
        )
    raise RuntimeError(
        "Não foi possível obter a TSM de nenhuma das fontes da NOAA.\n\n"
        "Isso normalmente é manutenção/queda NOS SERVIDORES (o nó oeste/PFEG "
        "fica fora do ar de tempos em tempos) — não precisa ser a sua "
        "internet. Tente novamente mais tarde.\n\n"
        f"{detalhes}"
    )


class _DateUnavailable(RuntimeError):
    """A fonte respondeu, mas não tem a data pedida (404/400 do griddap)."""


def _download_from_source(
    src: dict,
    *,
    use_latest: bool,
    date_tag: str,
    date_label: str,
    extent: list[float],
    data_dir: Path | None,
    stride: int,
    emit,
) -> SSTData:
    """Uma tentativa completa (URL → download → parse → cache) numa fonte."""
    import pandas as pd
    import requests
    import xarray as xr

    lon_min, lat_min, lon_max, lat_max = extent

    # Stride EFETIVO: `stride` é calibrado para a grade de 0.01° do MUR
    # (5 ≈ 5 km). Numa grade mais grossa (Blended 0.05°), o mesmo passo
    # daria ~27 km — reescala para preservar a resolução efetiva pedida.
    eff_stride = max(1, round(stride * 0.01 / float(src["grid_deg"])))

    time_constraint = "last" if use_latest else f"{date_tag}T{src['hora']}"
    constraint_url = (
        f"{src['base']}.nc?"
        f"analysed_sst[({time_constraint}):1:({time_constraint})]"
        f"[({lat_min}):{eff_stride}:({lat_max})]"
        f"[({lon_min}):{eff_stride}:({lon_max})]"
    )
    logger.info("TSM request (%s): %s", src["curto"], constraint_url)
    res_km = eff_stride * float(src["grid_deg"]) * 111
    emit("status", f"Baixando TSM ({date_label}) — {src['curto']}, ~{res_km:.0f} km...")
    emit("percent", 10)

    try:
        resp = requests.get(
            constraint_url,
            timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S),
            stream=True,
            headers=_UA,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status in (404, 400):
            raise _DateUnavailable(f"data {date_label} não disponível (HTTP {status})") from e
        raise RuntimeError(f"HTTP {status}") from e
    except requests.ConnectionError as e:
        raise RuntimeError("sem resposta (servidor fora do ar?)") from e
    except requests.Timeout as e:
        raise RuntimeError(f"timeout de conexão ({CONNECT_TIMEOUT_S}s)") from e

    total_size = int(resp.headers.get("content-length", 0))
    downloaded = 0

    # Download para arquivo temporário — quedas NO MEIO da transferência
    # também caem na cadeia de espelhos (o try do chamador envolve tudo).
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".nc")
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    pct = 10 + int(downloaded * 75 / total_size)
                    emit("percent", min(pct, 85))
                    # Atualiza status a cada ~256 KB
                    if downloaded % (256 * 1024) < 65536:
                        mb_down = downloaded / (1024 * 1024)
                        mb_total = total_size / (1024 * 1024)
                        emit("status", f"Baixando... {mb_down:.1f} / {mb_total:.1f} MB")
                else:
                    # Sem content-length: mostra apenas bytes baixados
                    if downloaded % (256 * 1024) < 65536:
                        mb_down = downloaded / (1024 * 1024)
                        emit("status", f"Baixando... {mb_down:.1f} MB")

        emit("status", "Processando dados SST...")
        emit("percent", 90)

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

        # ── Salva no cache (prefixo do PRODUTO — MUR e Blended não colidem) ──
        if data_dir is not None:
            final_cache = data_dir / f"{src['cache_prefix']}_{actual_date_str}_s{stride}.nc"
            if not final_cache.exists():
                try:
                    final_cache.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(tmp_path, final_cache)
                    logger.info("TSM salva em cache: %s", final_cache)
                except Exception as e:
                    logger.warning("Falha ao salvar cache SST: %s", e)

    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)

    emit("status", f"TSM carregada — {actual_date_str} ({src['curto']})")
    emit("percent", 100)

    logger.info(
        "TSM carregada de %s: %s, shape=%s, stride_ef=%d",
        src["curto"],
        actual_date_str,
        sst_arr.shape,
        eff_stride,
    )

    return SSTData(
        sst=sst_arr,
        lons=lons,
        lats=lats,
        time_str=actual_date_str,
        source=str(src["nome"]),
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

    # Fonte inferida do prefixo do cache — um Blended restaurado do disco
    # continua rotulado como Blended na carta (honestidade sobrevive ao cache).
    source = "MUR SST 1km (NASA/NOAA)"
    for src in SST_SOURCES:
        if filepath.name.startswith(f"{src['cache_prefix']}_"):
            source = str(src["nome"])
            break

    return SSTData(
        sst=sst_arr,
        lons=lons,
        lats=lats,
        time_str=date_str,
        source=source,
    )
