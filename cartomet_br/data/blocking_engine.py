"""
Motor da Análise de Bloqueio Atmosférico (anomalia de Z500) — CartoMet BR.

Calcula o campo clássico de diagnóstico de bloqueios atmosféricos:

    Anomalia(λ, φ) = Z500_IFS(rodada, step) − Z̄500_ERA5(dia-do-ano, hora)

A climatologia diária (ERA5 1991–2020, 00Z/12Z, média anual + 4 harmônicos via FFT,
domínio 150°W–30°E / 75°S–15°N, 0.25°, gpm) é distribuída pelo próprio repositório do
CartoMet BR no GitHub — um NetCDF por dia do ano + `manifest.json` com sha256. O motor
baixa SOMENTE o arquivo do dia necessário (cache-first, integridade verificada) e
subtrai do `gh` 500 hPa do IFS Open Data, que está na MESMA grade 0.25° (subtração
direta, sem regrid).

O campo resultante orienta o previsor (human-in-the-loop): anomalias positivas intensas
e persistentes (≳ +100 gpm) em latitudes médias-altas sugerem bloqueio; o padrão ômega
aparece como um dipolo A–B (anomalia positiva ladeada por negativas).

Este módulo é isolado e testável sem GUI e sem rede (ver tests/test_blocking.py).
Referência: docs/Metodologia_Bloqueio_Z500.md. Dados: Hersbach et al. (2020),
doi:10.1002/qj.3803 — contém informação modificada do Copernicus C3S (ERA5).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import requests
import xarray as xr

logger = logging.getLogger(__name__)

# Indireção testável do backoff (testes anulam o sleep sem tocar o time global)
_sleep = time.sleep


# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTES DE DOMÍNIO
# ═══════════════════════════════════════════════════════════════════════════════

# Fonte pública da climatologia (repositório do próprio CartoMet BR; sem autenticação)
DEFAULT_CLIM_BASE_URL: str = (
    "https://raw.githubusercontent.com/ElivaldoRocha/CartoMet_BR/main/climatology/z500"
)

# Domínio FIXO da climatologia, formato do Config [lon_min, lat_min, lon_max, lat_max].
# O gh do IFS é recortado a este setor — as grades ficam idênticas (0.25°, lat 15→−75
# decrescente, lon −150→30), e a anomalia é uma subtração direta.
CLIM_EXTENT: list[float] = [-150.0, -75.0, 30.0, 15.0]

CLIM_HOURS: tuple[int, int] = (0, 12)      # horários sinóticos da climatologia
CLIM_VAR: str = "z500_clim"                # variável dos NetCDFs diários (gpm)

# Render: níveis FIXOS simétricos (cartas comparáveis entre si); extend="both"
# cobre |anomalia| > 320 gpm em latitudes altas.
ANOM_LEVELS: np.ndarray = np.arange(-320.0, 321.0, 40.0)

# Download da climatologia (arquivos ~800 KB; raw.githubusercontent.com)
HTTP_TIMEOUT_S: float = 30.0
HTTP_RETRIES: int = 3
HTTP_BACKOFF_S: float = 1.0                # 1 s, 2 s, 4 s


# ═══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ═══════════════════════════════════════════════════════════════════════════════

class BlockingCancelled(Exception):
    """Sinaliza cancelamento cooperativo da análise de bloqueio pelo usuário."""


class BlockingDataError(Exception):
    """Dados indisponíveis (rolling archive do IFS, rede, climatologia ausente)."""


@dataclass(frozen=True)
class ClimSlot:
    """Slot climatológico resolvido a partir do valid_time do IFS."""

    mmdd: str           # "0611" — sempre do próprio valid_time (29/02 incluído)
    hour: int           # 0 | 12 (horário climatológico mais próximo)
    is_approx: bool     # True se valid_hour ∉ {0, 12} (aproximação documentada)


@dataclass
class BlockingResult:
    """Saída do motor — campo de anomalia pronto para o render divergente."""

    anom: np.ndarray            # 2D float64 (gpm), grade da climatologia
    lons: np.ndarray            # 1D −150 → 30 (721 pts)
    lats: np.ndarray            # 1D 15 → −75 DECRESCENTE (361 pts)
    valid_time: str = ""        # "YYYY-MM-DDTHH:MM" (rodada + step)
    base_time: str = ""         # rodada-base do IFS (formato do load_pl_variable)
    clim_mmdd: str = ""
    clim_hour: int = 0
    meta: dict = field(default_factory=dict)   # is_approx, sha_verified, from_cache,
    #                                            step, anom_min, anom_max


# ═══════════════════════════════════════════════════════════════════════════════
#  SLOT CLIMATOLÓGICO (valid_time → MMDD + hora 00/12)
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_clim_slot(valid_time: str) -> ClimSlot:
    """Mapeia o valid_time do IFS para (MMDD, hora climatológica, aproximação).

    Regra: hora = 12 se 6 ≤ valid_hour < 18, senão 0; o MMDD vem SEMPRE do
    próprio valid_time (inclusive 18–23h — a deriva da climatologia entre dias
    consecutivos é ≲ 5 gpm, desprezível em escala sinótica). O ciclo diurno de
    Z500 é pequeno, então a aproximação para horas ∉ {0, 12} é aceitável e é
    sinalizada via `is_approx` ("≈" no título da carta).
    """
    dt = None
    for fmt, text in (("%Y-%m-%dT%H:%M", valid_time), ("%Y-%m-%dT%H", valid_time[:13])):
        try:
            dt = datetime.strptime(text, fmt)
            break
        except (ValueError, TypeError):
            continue
    if dt is None:
        raise BlockingDataError(
            f"valid_time inválido para a análise de bloqueio: {valid_time!r} "
            "(esperado 'YYYY-MM-DDTHH:MM')."
        )
    hour = 12 if 6 <= dt.hour < 18 else 0
    return ClimSlot(mmdd=dt.strftime("%m%d"), hour=hour, is_approx=dt.hour not in CLIM_HOURS)


# ═══════════════════════════════════════════════════════════════════════════════
#  CLIMATOLOGIA — manifest + arquivo do dia (cache-first, sha256, .part→replace)
# ═══════════════════════════════════════════════════════════════════════════════

def _sha256(path: Path) -> str:
    """sha256 de um arquivo, lido em blocos de 1 MiB."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _fetch_manifest(
    clim_dir: Path,
    base_url: str = DEFAULT_CLIM_BASE_URL,
    progress_callback=None,
    cancel_check=None,
) -> dict | None:
    """Baixa (e cacheia) o manifest.json; offline → manifest local; senão None."""
    local = clim_dir / "manifest.json"
    url = f"{base_url}/manifest.json"
    last_err: Exception | None = None
    for attempt in range(1, HTTP_RETRIES + 1):
        if cancel_check is not None and cancel_check():
            raise BlockingCancelled()
        try:
            resp = requests.get(url, timeout=HTTP_TIMEOUT_S)
            resp.raise_for_status()
            part = clim_dir / "manifest.json.part"
            part.write_bytes(resp.content)
            part.replace(local)
            return json.loads(local.read_text(encoding="utf-8"))
        except requests.RequestException as e:
            last_err = e
            if attempt < HTTP_RETRIES:
                _sleep(HTTP_BACKOFF_S * 2 ** (attempt - 1))
    logger.warning("Manifest da climatologia inacessível (%s)", last_err)
    if local.exists():
        if progress_callback:
            progress_callback("Sem conexão — usando manifest da climatologia em cache.")
        try:
            return json.loads(local.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Manifest local ilegível: %s", e)
    return None


def ensure_climatology_file(
    mmdd: str,
    clim_dir: Path,
    base_url: str = DEFAULT_CLIM_BASE_URL,
    progress_callback=None,
    cancel_check=None,
) -> tuple[Path, dict]:
    """Garante o NetCDF climatológico do dia em disco (cache antes da rede).

    Retorna (caminho, info) com info = {"sha_verified": bool, "from_cache": bool}.
    Cache com sha válido → zero download além do manifest. Cache corrompido →
    re-baixa. Offline com cache mas sem manifest → usa com aviso (sha_verified
    False). Offline sem nada → BlockingDataError amigável.
    """
    clim_dir = Path(clim_dir)
    clim_dir.mkdir(parents=True, exist_ok=True)

    manifest = _fetch_manifest(clim_dir, base_url, progress_callback, cancel_check)
    entry = manifest.get("files", {}).get(mmdd) if manifest else None
    name = entry["name"] if entry else f"z500_clim_{mmdd}.nc"
    path = clim_dir / name

    if path.exists():
        if entry is not None:
            if _sha256(path) == entry["sha256"]:
                logger.info("Climatologia %s em cache (sha256 ok)", name)
                return path, {"sha_verified": True, "from_cache": True}
            logger.warning("Climatologia %s corrompida em cache — re-baixando", name)
            path.unlink()
        else:
            if progress_callback:
                progress_callback(
                    "Sem conexão — usando climatologia em cache (sem verificação de integridade)."
                )
            return path, {"sha_verified": False, "from_cache": True}

    if entry is None:
        raise BlockingDataError(
            "Climatologia de Z500 indisponível: sem conexão com o GitHub e sem "
            f"arquivo em cache para o dia {mmdd[2:]}/{mmdd[:2]}.\n"
            "Conecte-se à internet e tente novamente."
        )

    url = f"{base_url}/{name}"
    part = clim_dir / f"{name}.part"
    last_err: Exception | str | None = None
    for attempt in range(1, HTTP_RETRIES + 1):
        if cancel_check is not None and cancel_check():
            raise BlockingCancelled()
        if progress_callback:
            progress_callback(f"Baixando climatologia ERA5 ({name})...")
        try:
            resp = requests.get(url, timeout=HTTP_TIMEOUT_S)
            resp.raise_for_status()
        except requests.RequestException as e:
            last_err = e
            if attempt < HTTP_RETRIES:
                _sleep(HTTP_BACKOFF_S * 2 ** (attempt - 1))
            continue
        part.write_bytes(resp.content)
        if _sha256(part) != entry["sha256"]:
            part.unlink()
            last_err = "sha256 divergente do manifest (download truncado?)"
            if attempt < HTTP_RETRIES:
                _sleep(HTTP_BACKOFF_S * 2 ** (attempt - 1))
            continue
        part.replace(path)   # Path.replace: atômico e sobrescreve no Windows
        logger.info("Climatologia %s baixada e verificada", name)
        return path, {"sha_verified": True, "from_cache": False}

    raise BlockingDataError(
        f"Falha ao baixar a climatologia ({name}) após {HTTP_RETRIES} tentativas: {last_err}"
    )


def load_climatology(path: Path, hour: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Lê (values, lats, lons) do NetCDF climatológico no horário pedido."""
    try:
        ds = xr.open_dataset(path)
    except (OSError, ValueError) as e:
        raise BlockingDataError(
            f"Não foi possível abrir a climatologia {path.name}: {e}"
        ) from e
    try:
        if CLIM_VAR not in ds:
            raise BlockingDataError(
                f"Variável '{CLIM_VAR}' ausente em {path.name} (arquivo inesperado)."
            )
        try:
            da = ds[CLIM_VAR].sel(hour=hour)
        except KeyError as e:
            raise BlockingDataError(
                f"Horário {hour:02d}Z ausente na climatologia {path.name} "
                f"(disponíveis: {list(ds['hour'].values)})."
            ) from e
        values = np.asarray(da.values)
        lats = np.asarray(ds["latitude"].values)
        lons = np.asarray(ds["longitude"].values)
    finally:
        ds.close()   # fecha ANTES de retornar (lock de arquivo no Windows)
    return values, lats, lons


# ═══════════════════════════════════════════════════════════════════════════════
#  ANOMALIA
# ═══════════════════════════════════════════════════════════════════════════════

def compute_anomaly(
    gh: np.ndarray,
    gh_lats: np.ndarray,
    gh_lons: np.ndarray,
    clim: np.ndarray,
    clim_lats: np.ndarray,
    clim_lons: np.ndarray,
) -> np.ndarray:
    """Subtração direta IFS − climatologia, com trava dura de alinhamento.

    As DUAS grades devem ser idênticas (0.25°, lat decrescente 15→−75): qualquer
    divergência levanta erro claro em vez de produzir uma anomalia espelhada ou
    deslocada silenciosamente (bug catastrófico para o diagnóstico).
    """
    if gh.shape != clim.shape:
        raise BlockingDataError(
            f"Grades incompatíveis entre IFS {gh.shape} e climatologia {clim.shape}. "
            "Esperado o mesmo recorte 0.25° (150°W–30°E, 75°S–15°N)."
        )
    if not (
        np.allclose(gh_lats, clim_lats, atol=1e-6)
        and np.allclose(gh_lons, clim_lons, atol=1e-6)
    ):
        raise BlockingDataError(
            "Coordenadas do IFS e da climatologia não alinham (orientação da "
            "latitude? recorte?). Anomalia abortada para evitar campo espelhado."
        )
    return gh.astype(np.float64) - clim.astype(np.float64)


# ═══════════════════════════════════════════════════════════════════════════════
#  ORQUESTRADOR
# ═══════════════════════════════════════════════════════════════════════════════

def compute_blocking(
    cycle: int | None,
    cycle_date: str | None,
    step: int = 0,
    data_dir: Path = Path("data"),
    clim_dir: Path = Path("data/climatologia/z500"),
    source: str = "ecmwf",
    force_download: bool = False,
    progress_callback=None,
    cancel_check=None,
) -> BlockingResult:
    """Pipeline completo: gh 500 do IFS → climatologia do dia → anomalia.

    O gh é obtido via `load_pl_variable` com `smoothing_sigma=0.0` (anomalia exige
    o campo bruto) e compartilha o cache GRIB com a camada normal de geopotencial
    500 hPa. `cancel_check` permite aborto cooperativo entre as etapas.
    """
    from cartomet_br.data.ecmwf import load_pl_variable

    def _emit(msg: str):
        if progress_callback:
            progress_callback(msg)

    def _guard():
        if cancel_check is not None and cancel_check():
            raise BlockingCancelled()

    if cycle is None or not cycle_date:
        raise BlockingDataError(
            "Rodada do ECMWF indeterminada. Clique em \"Verificar Rodadas\" e "
            "selecione uma rodada antes da análise de bloqueio."
        )

    _guard()
    _emit(f"Bloqueio: obtendo gh 500 hPa (IFS {cycle:02d}Z, passo +{step}h)...")
    try:
        fld = load_pl_variable(
            "gh", 500, extent=CLIM_EXTENT, step=step, cycle=cycle,
            cycle_date=cycle_date, data_dir=Path(data_dir),
            smoothing_sigma=0.0, source=source, force_download=force_download,
        )
    except FileNotFoundError as e:
        raise BlockingDataError(
            f"gh 500 hPa indisponível para a rodada {cycle:02d}Z de {cycle_date} "
            f"no passo +{step}h.\nO ECMWF Open Data mantém ~3 dias de rodadas "
            "(rolling archive) e nem todo step existe em toda rodada.\n"
            "Verifique as rodadas e tente novamente."
        ) from e

    _guard()
    valid = fld.valid_time
    if not valid:   # fallback raro: GRIB sem metadado legível
        base = datetime.strptime(f"{cycle_date}{cycle:02d}", "%Y%m%d%H")
        valid = (base + timedelta(hours=step)).strftime("%Y-%m-%dT%H:%M")
    slot = resolve_clim_slot(valid)

    _emit(
        f"Bloqueio: climatologia ERA5 de {slot.mmdd[2:]}/{slot.mmdd[:2]} "
        f"({slot.hour:02d}Z)..."
    )
    clim_path, info = ensure_climatology_file(
        slot.mmdd, Path(clim_dir),
        progress_callback=progress_callback, cancel_check=cancel_check,
    )
    clim, clim_lats, clim_lons = load_climatology(clim_path, slot.hour)

    _guard()
    _emit("Bloqueio: calculando anomalia (IFS − climatologia 1991–2020)...")
    anom = compute_anomaly(
        np.asarray(fld.values), np.asarray(fld.lats), np.asarray(fld.lons),
        clim, clim_lats, clim_lons,
    )

    return BlockingResult(
        anom=anom,
        lons=np.asarray(fld.lons),
        lats=np.asarray(fld.lats),
        valid_time=valid,
        base_time=fld.base_time,
        clim_mmdd=slot.mmdd,
        clim_hour=slot.hour,
        meta={
            "is_approx": slot.is_approx,
            "step": step,
            "anom_min": float(np.nanmin(anom)),
            "anom_max": float(np.nanmax(anom)),
            **info,
        },
    )
