"""
Motor do Ensemble ENS — ECMWF Open Data, 51 membros (CartoMet BR v3.2, Onda 3).

Pós-50r1 o ``enfo`` serve UM arquivo combinado (~6 GB/step) com apenas
``type=pf`` (membros perturbados 1–50); o CONTROLE migrou para o ``stream
oper`` — que o app já baixa para as cartas normais (cache compartilhado de
graça). Por isso este módulo:

  1. baixa os 50 pf via ``retrieve()`` com byte-ranges (~20–40 MB por campo,
     NUNCA ``download()`` no enfo);
  2. reusa o cache ``ecmwf_*`` do oper como membro de controle (51 = 50 + 1);
  3. recorta ao extent ANTES de empilhar (stack pequeno, numpy puro, sem Dask);
  4. entrega produtos prontos como ``PLFieldData`` sintético (render pelo
     mesmo caminho das camadas PL — padrão herdado da Comparação de Rodadas):
     - P(R24h > limiar): fração dos 51 membros com chuva de 24 h acima do
       limiar (desacumulação tp(step) − tp(step−24) POR MEMBRO);
     - média ± dispersão (σ) de PNMM e Z500: σ sombreado + média em linhas
       (``extra["mean_contour"]``).

Os produtos ``em``/``es`` prontos sumiram do open data — média e σ são
calculadas aqui. Puro (sem Qt): a orquestração vive no DataService/worker.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from cartomet_br.data.ecmwf import PLFieldData, download_ecmwf

logger = logging.getLogger(__name__)

# O enfo publica ~8 h após a rodada (o oper leva ~7,5 h) — steps de 3 h até
# +144 h em todas as rodadas (00/12Z seguem de 6 h até +360, além do alcance
# do app). O delay maior significa que uma rodada já visível nas cartas
# normais pode ainda não ter ENS por ~30 min.
ENS_PUBLISH_DELAY = 8.0
ENS_N_PERTURBED = 50
ENS_MEMBERS_LABEL = "51 membros (50 pert. + controle)"

# Janela da probabilidade de precipitação: 24 h — a pergunta operacional
# honesta ("chove mais de X mm amanhã?"), não a chuva de um step de 3 h.
PROB_WINDOW_HOURS = 24
PROB_MIN_STEP = PROB_WINDOW_HOURS
PROB_THRESHOLDS_MM = (1.0, 5.0, 10.0, 20.0, 30.0, 50.0)

# Campos dos produtos média ± σ. ``scale`` converte a unidade crua do GRIB
# para a de exibição (msl: Pa → hPa).
ENS_SPREAD_FIELDS: dict[str, dict[str, Any]] = {
    "msl": {
        "param": "msl",
        "level": None,
        "unit": "hPa",
        "scale": 0.01,
        "nome": "PNMM",
    },
    "gh500": {
        "param": "gh",
        "level": 500,
        "unit": "gpm",
        "scale": 1.0,
        "nome": "Geopotencial 500 hPa",
    },
}

ProgressCallback = Callable[[str], None] | None


def _progress(cb: ProgressCallback, msg: str) -> None:
    if cb is not None:
        cb(msg)
    logger.info(msg)


# ═══════════════════════════════════════════════════════════════════════════════
#  DOWNLOAD (cache-first) + LEITURA RECORTADA
# ═══════════════════════════════════════════════════════════════════════════════


def _cache_name(
    prefix: str, param: str, level: int | None, date_str: str, cycle: int, step: int
) -> str:
    """Nome de cache no MESMO layout dos loaders existentes.

    ``prefix="ecmwf"`` reproduz byte-a-byte os nomes do oper — o controle do
    ENS e as cartas normais compartilham o arquivo (cache de graça nos dois
    sentidos). ``prefix="ens"`` indexa os 50 membros perturbados.
    """
    cycle_tag = f"{cycle:02d}Z"
    if level:
        return f"{prefix}_{param}_{date_str}_{cycle_tag}_{level}hPa_f{step:03d}.grib2"
    return f"{prefix}_{param}_{date_str}_{cycle_tag}_f{step:03d}.grib2"


def _download_members(
    param: str,
    level: int | None,
    step: int,
    cycle: int,
    date_str: str,
    data_dir: Path,
    source: str,
    force_download: bool,
) -> Path:
    """Baixa os 50 membros perturbados (stream=enfo, type=pf) via byte-ranges."""
    try:
        return download_ecmwf(
            variables=[param],
            levels=[level] if level else None,
            step=step,
            cycle=cycle,
            date=date_str,
            output_path=Path(data_dir) / _cache_name("ens", param, level, date_str, cycle, step),
            data_dir=Path(data_dir),
            source=source,
            force_download=force_download,
            model="ifs",
            stream="enfo",
            type_="pf",
        )
    except (FileNotFoundError, ValueError) as e:
        raise ValueError(
            f"O Ensemble (ENS) da rodada {cycle:02d}Z ainda não está disponível.\n\n"
            "O ENS é publicado ~8 h após a rodada — cerca de 30 min DEPOIS das "
            "cartas normais (oper). Se a carta determinística já carrega mas o "
            "ENS falha, aguarde alguns minutos ou use a rodada anterior.\n\n"
            f"(step solicitado: +{step}h; o enfo publica steps de 3 h até +144 h)"
        ) from e


def _download_control(
    param: str,
    level: int | None,
    step: int,
    cycle: int,
    date_str: str,
    data_dir: Path,
    source: str,
    force_download: bool,
) -> Path:
    """Garante o membro de CONTROLE — o próprio oper, no cache das cartas normais."""
    return download_ecmwf(
        variables=[param],
        levels=[level] if level else None,
        step=step,
        cycle=cycle,
        date=date_str,
        output_path=Path(data_dir) / _cache_name("ecmwf", param, level, date_str, cycle, step),
        data_dir=Path(data_dir),
        source=source,
        force_download=force_download,
        model="ifs",
    )


def _read_stack(
    grib_file: Path, param: str, extent: list[float], level: int | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, str, str]:
    """Lê um GRIB recortado ao extent → (stack [n, lat, lon], lons, lats, vt, bt).

    Arquivo enfo: dimensão ``number`` (50 membros) vira o eixo 0. Arquivo oper
    (controle, sem ``number``): ganha eixo 0 de tamanho 1 — o chamador só
    concatena. O recorte acontece ANTES do ``.values`` (nunca materializa o
    globo dos 50 membros).
    """
    ds = xr.open_dataset(grib_file, engine="cfgrib", backend_kwargs={"errors": "ignore"})
    ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180)
    ds = ds.sortby("longitude")
    da = ds[param]
    if "isobaricInhPa" in da.dims and level is not None:
        da = da.sel(isobaricInhPa=level)
    da = da.sel(
        longitude=slice(extent[0], extent[2]),
        latitude=slice(extent[3], extent[1]),
    )
    if "number" in da.dims:
        da = da.transpose("number", "latitude", "longitude")
        values = np.asarray(da.values, dtype=float)
    else:
        values = np.asarray(da.values, dtype=float)[None, ...]
    lons = da.longitude.values
    lats = da.latitude.values
    vt, bt = "", ""
    try:
        if "valid_time" in ds.coords:
            vt = str(np.datetime_as_string(ds.valid_time.values, unit="m"))
        if "time" in ds.coords:
            bt_dt = np.datetime64(ds.time.values, "s").astype("datetime64[s]").astype(datetime)
            bt = bt_dt.strftime("%HZ %d/%m/%Y")
    except (KeyError, IndexError, ValueError, TypeError):
        pass
    ds.close()
    return values, lons, lats, vt, bt


def _load_51(
    param: str,
    level: int | None,
    step: int,
    cycle: int,
    date_str: str,
    extent: list[float],
    data_dir: Path,
    source: str,
    force_download: bool,
    progress_callback: ProgressCallback,
    what: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, str, str]:
    """Empilha controle + 50 pf num stack (51, lat, lon) no step pedido."""
    _progress(progress_callback, f"ENS: baixando {what} dos 50 membros (+{step}h)...")
    pf_file = _download_members(
        param, level, step, cycle, date_str, data_dir, source, force_download
    )
    _progress(progress_callback, f"ENS: garantindo o controle (oper) de {what} (+{step}h)...")
    ctl_file = _download_control(
        param, level, step, cycle, date_str, data_dir, source, force_download
    )

    pf, lons, lats, vt, bt = _read_stack(pf_file, param, extent, level)
    ctl, lons_c, lats_c, *_ = _read_stack(ctl_file, param, extent, level)
    if ctl.shape[1:] != pf.shape[1:]:
        raise RuntimeError(
            "Grades incompatíveis entre o controle (oper) e os membros (enfo): "
            f"{ctl.shape[1:]} × {pf.shape[1:]}. Cache corrompido? Tente forçar novo download."
        )
    stack = np.concatenate([ctl, pf], axis=0)
    if stack.shape[0] != ENS_N_PERTURBED + 1:
        logger.warning(
            "ENS com %d membros (esperados %d) — o produto segue com os disponíveis.",
            stack.shape[0],
            ENS_N_PERTURBED + 1,
        )
    return stack, lons, lats, vt, bt


# ═══════════════════════════════════════════════════════════════════════════════
#  PRODUTOS (numpy puro)
# ═══════════════════════════════════════════════════════════════════════════════


def member_mean_spread(stack: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Média e dispersão (σ populacional) ao longo do eixo dos membros."""
    return np.nanmean(stack, axis=0), np.nanstd(stack, axis=0)


def prob_exceedance(stack: np.ndarray, threshold: float) -> np.ndarray:
    """% dos membros acima do limiar, por pixel (0–100)."""
    return np.asarray(np.asarray(stack > threshold, dtype=float).mean(axis=0) * 100.0)


# ═══════════════════════════════════════════════════════════════════════════════
#  LOADERS DE PRODUTO (chamados pelo DataService)
# ═══════════════════════════════════════════════════════════════════════════════


def load_ens_spread(
    field_key: str,
    extent: list[float],
    step: int,
    cycle: int,
    cycle_date: str,
    data_dir: Path,
    source: str = "ecmwf",
    force_download: bool = False,
    progress_callback: ProgressCallback = None,
) -> PLFieldData:
    """Produto média ± σ de PNMM ou Z500 → PLFieldData sintético.

    ``values`` = σ (sombreado; a incerteza É a informação nova do ensemble);
    ``extra["mean_contour"]`` = média (linhas pretas rotuladas, como isobaras).
    """
    if field_key not in ENS_SPREAD_FIELDS:
        raise ValueError(f"Campo ENS desconhecido: {field_key!r} (use {list(ENS_SPREAD_FIELDS)})")
    spec = ENS_SPREAD_FIELDS[field_key]
    stack, lons, lats, vt, bt = _load_51(
        spec["param"],
        spec["level"],
        step,
        cycle,
        cycle_date,
        extent,
        data_dir,
        source,
        force_download,
        progress_callback,
        str(spec["nome"]),
    )
    stack = stack * float(spec["scale"])
    mean, std = member_mean_spread(stack)
    n = stack.shape[0]
    return PLFieldData(
        values=std,
        lons=lons,
        lats=lats,
        variable="ens_spread",
        level=int(spec["level"] or 0),
        unit=str(spec["unit"]),
        valid_time=vt,
        base_time=bt,
        step=step,
        source="ens",
        extra={
            "mean_contour": mean,
            "ens_product": field_key,
            "n_members": int(n),
            "title_desc": (
                f"{spec['nome']}: média (linhas) ± dispersão σ (cor, {spec['unit']}) "
                f"— {ENS_MEMBERS_LABEL}"
            ),
            "entry_label": f"ENS {spec['nome']} ±σ",
            "entry_detail": str(spec["unit"]),
        },
    )


def load_ens_prob_precip(
    threshold_mm: float,
    extent: list[float],
    step: int,
    cycle: int,
    cycle_date: str,
    data_dir: Path,
    source: str = "ecmwf",
    force_download: bool = False,
    progress_callback: ProgressCallback = None,
) -> PLFieldData:
    """P(R24h > limiar): % dos 51 membros com chuva acumulada de 24 h > limiar.

    Desacumulação POR MEMBRO na mesma rodada: tp(step) − tp(step−24), m → mm.
    ``step == 24`` dispensa o download do step 0 (tp acumulado é zero na
    análise — mesma economia do loader de precipitação).
    """
    if step < PROB_MIN_STEP:
        raise ValueError(
            f"A probabilidade de precipitação usa janela de {PROB_WINDOW_HOURS} h — "
            f"requer step ≥ +{PROB_MIN_STEP}h (selecionado: +{step}h).\n\n"
            "Escolha um step de +24h em diante."
        )
    step_lo = step - PROB_WINDOW_HOURS
    hi, lons, lats, vt, bt = _load_51(
        "tp",
        None,
        step,
        cycle,
        cycle_date,
        extent,
        data_dir,
        source,
        force_download,
        progress_callback,
        "precipitação acumulada",
    )
    if step_lo > 0:
        lo, *_ = _load_51(
            "tp",
            None,
            step_lo,
            cycle,
            cycle_date,
            extent,
            data_dir,
            source,
            force_download,
            progress_callback,
            "precipitação acumulada",
        )
        if lo.shape != hi.shape:
            raise RuntimeError(
                f"Stacks de tp com formas diferentes entre steps: {hi.shape} × {lo.shape}."
            )
    else:
        lo = np.zeros_like(hi)  # tp acumulado no step 0 é zero — sem download
    delta_mm = (hi - lo) * 1000.0
    prob = prob_exceedance(delta_mm, float(threshold_mm))
    n = hi.shape[0]
    return PLFieldData(
        values=prob,
        lons=lons,
        lats=lats,
        variable="ens_prob",
        level=0,
        unit="%",
        valid_time=vt,
        base_time=bt,
        step=step,
        source="ens",
        extra={
            "ens_product": "prob",
            "threshold_mm": float(threshold_mm),
            "n_members": int(n),
            "title_desc": (f"P(R24h > {threshold_mm:g} mm) (%) — {ENS_MEMBERS_LABEL}"),
            "entry_label": f"ENS P(R24h>{threshold_mm:g}mm)",
            "entry_detail": "%",
        },
    )
