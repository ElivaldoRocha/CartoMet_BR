"""
Observações de superfície — SYNOP (6/6h) e METAR (horário).

Sobrepõe observações reais à análise do modelo (ECMWF) para identificar
sistemas sinóticos (frentes, ciclones, cavados). Pedido do meteorologista
Gustavo C. J. Escobar.

Fontes
------
METAR : NOAA Aviation Weather Center (AWC)
        https://aviationweather.gov/api/data/metar  — JSON com coordenadas,
        T/Td, vento, PNMM, nuvens e tempo presente já decodificados.
SYNOP : OGIMET getsynop — relatórios FM-12 crus, decodificados com
        `pymetdecoder`. As coordenadas das estações vêm da tabela WMO
        nsd_bbsss (NOAA), baixada e cacheada localmente.

Princípio: reaproveitar a stack atual (pandas/MetPy). A plotagem usa
`metpy.plots.StationPlot` + `metpy.calc.reduce_point_density` (ver MapCanvas).
Todas as funções de rede degradam graciosamente: em caso de erro ou resposta
vazia retornam um DataFrame vazio com as colunas canônicas — nunca quebram a UI.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Colunas canônicas (compatíveis com metpy.plots.StationPlot) ──
STATION_COLUMNS: list[str] = [
    "station_id",
    "latitude",
    "longitude",
    "air_temperature",  # °C
    "dew_point_temperature",  # °C
    "air_pressure_at_sea_level",  # hPa
    "eastward_wind",  # m/s (componente u)
    "northward_wind",  # m/s (componente v)
    "cloud_coverage",  # oktas (0–8)
    "current_wx1_symbol",  # código WMO de tempo presente (int)
]

# Atribuição para a legenda da carta
METAR_ATTRIBUTION = "METAR: NOAA Aviation Weather Center"
SYNOP_ATTRIBUTION = "SYNOP: OGIMET"

# Endpoints
_AWC_METAR_URL = "https://aviationweather.gov/api/data/metar"
_OGIMET_SYNOP_URL = "https://www.ogimet.com/cgi-bin/getsynop"
# Tabela WMO bloco-estação → coordenadas (NOAA tgftp). A URL antiga em
# www.weather.gov/source/tg foi descontinuada (404).
_WMO_STATION_LIST_URLS = [
    "https://tgftp.nws.noaa.gov/data/nsd_bbsss.txt",
    "https://www.weather.gov/source/tg/nsd_bbsss.txt",  # fallback histórico
]

# Conversão de cobertura de nuvens METAR → oktas
_CLOUD_OKTAS = {
    "SKC": 0,
    "NSC": 0,
    "NCD": 0,
    "CLR": 0,
    "CAVOK": 0,
    "FEW": 2,
    "SCT": 4,
    "BKN": 6,
    "OVC": 8,
    "VV": 8,
}

_KT_TO_MS = 0.514444


# ═══════════════════════════════════════════════════════════════════════════════
#  NORMALIZAÇÃO (pura, testável — sem rede)
# ═══════════════════════════════════════════════════════════════════════════════


def empty_stations_df() -> pd.DataFrame:
    """DataFrame vazio com as colunas canônicas."""
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in STATION_COLUMNS})


def wind_components(speed_ms: float | None, direction_deg: float | None) -> tuple[float, float]:
    """Converte velocidade/direção (convenção meteorológica) em componentes u, v (m/s).

    u (eastward) = -speed·sin(dir);  v (northward) = -speed·cos(dir).
    Retorna (nan, nan) se algum valor for ausente/inválido (ex.: vento variável).
    """
    if speed_ms is None or direction_deg is None:
        return (math.nan, math.nan)
    try:
        s = float(speed_ms)
        d = float(direction_deg)
    except (TypeError, ValueError):
        return (math.nan, math.nan)
    if not math.isfinite(s) or not math.isfinite(d):
        return (math.nan, math.nan)
    rad = math.radians(d)
    return (-s * math.sin(rad), -s * math.cos(rad))


def normalize_station_records(records: list[dict]) -> pd.DataFrame:
    """Converte registros brutos (lista de dicts) no DataFrame canônico.

    Cada registro pode conter qualquer subconjunto das colunas canônicas; chaves
    ausentes viram NaN. Linhas sem latitude/longitude válidas são descartadas.
    O resultado tem exatamente as colunas de STATION_COLUMNS, na ordem.
    """
    if not records:
        return empty_stations_df()

    df = pd.DataFrame(records)

    # Garante todas as colunas canônicas
    for col in STATION_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan

    df = df[STATION_COLUMNS].copy()

    # Tipos numéricos (station_id permanece como rótulo)
    numeric = [c for c in STATION_COLUMNS if c != "station_id"]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Descarta estações sem coordenadas
    df = df.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)

    df["station_id"] = df["station_id"].astype("string").fillna("")
    return df


def _filter_extent(df: pd.DataFrame, extent: list[float]) -> pd.DataFrame:
    """Mantém apenas estações dentro de [lon_min, lat_min, lon_max, lat_max]."""
    if df.empty:
        return df
    lon_min, lat_min, lon_max, lat_max = extent
    mask = (
        (df["longitude"] >= lon_min)
        & (df["longitude"] <= lon_max)
        & (df["latitude"] >= lat_min)
        & (df["latitude"] <= lat_max)
    )
    return df[mask].reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  METAR — NOAA Aviation Weather Center
# ═══════════════════════════════════════════════════════════════════════════════


def _metar_record_from_json(obj: dict) -> dict:
    """Mapeia um objeto JSON do AWC para um registro canônico."""
    # PNMM: prefere mslp; cai para altímetro (altim já em hPa no AWC)
    mslp = obj.get("mslp")
    if mslp is None:
        mslp = obj.get("altim")

    # Vento
    wdir = obj.get("wdir")
    wspd_kt = obj.get("wspd")
    # Vento variável ("VRB") ou calmo não tem direção numérica
    if isinstance(wdir, str):
        wdir = None
    speed_ms = wspd_kt * _KT_TO_MS if isinstance(wspd_kt, (int, float)) else None
    u, v = wind_components(speed_ms, wdir)

    # Cobertura de nuvens → máxima oktas entre as camadas
    okta = np.nan
    clouds = obj.get("clouds")
    if isinstance(clouds, list):
        oktas = [_CLOUD_OKTAS.get(str(c.get("cover", "")).upper()) for c in clouds]
        oktas_validos = [o for o in oktas if o is not None]
        if oktas_validos:
            okta = float(max(oktas_validos))

    # Tempo presente → código WMO numérico (via MetPy, defensivo)
    wx_symbol = np.nan
    wx_str = obj.get("wxString")
    if wx_str:
        try:
            from metpy.plots.wx_symbols import wx_code_to_numeric

            codes = wx_code_to_numeric([wx_str])
            if len(codes):
                wx_symbol = float(codes[0])
        except Exception:
            wx_symbol = np.nan

    return {
        "station_id": obj.get("icaoId") or obj.get("station_id") or "",
        "latitude": obj.get("lat"),
        "longitude": obj.get("lon"),
        "air_temperature": obj.get("temp"),
        "dew_point_temperature": obj.get("dewp"),
        "air_pressure_at_sea_level": mslp,
        "eastward_wind": u,
        "northward_wind": v,
        "cloud_coverage": okta,
        "current_wx1_symbol": wx_symbol,
    }


def fetch_metar(
    extent: list[float],
    when: datetime | None = None,
    data_dir: Path | None = None,
    force_download: bool = False,
    timeout: int = 60,
) -> pd.DataFrame:
    """Baixa METARs do AWC dentro do `extent` e retorna o DataFrame canônico.

    Parameters
    ----------
    extent : [lon_min, lat_min, lon_max, lat_max]
    when : datetime, opcional
        Hora alvo (UTC). None = observação mais recente. O AWC entrega o METAR
        mais recente de cada estação; `when` é usado apenas para o nome do cache.
    data_dir : Path, opcional
        Diretório de cache.
    force_download : bool
        Ignora o cache local.
    """
    import requests

    lon_min, lat_min, lon_max, lat_max = extent
    cache = _cache_path(data_dir, "metar", extent, when)

    # ── Cache: o arquivo guarda o payload BRUTO do AWC, então é mapeado
    #    pelas mesmas funções do caminho de rede (chaves icaoId/lat/lon → canônicas).
    if cache is not None and cache.exists() and not force_download:
        try:
            payload = json.loads(cache.read_text("utf-8"))
            df = _metar_payload_to_df(payload, extent)
            if not df.empty:
                logger.info("METAR (cache): %d estações", len(df))
                return df
        except Exception as e:  # cache corrompido → segue para rede
            logger.warning("Cache METAR inválido (%s): %s", cache.name, e)

    # AWC bbox: "latMin,lonMin,latMax,lonMax"
    bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    params = {"bbox": bbox, "format": "json"}

    try:
        resp = requests.get(_AWC_METAR_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.warning("Falha ao baixar METAR do AWC: %s", e)
        return empty_stations_df()

    if not isinstance(payload, list) or not payload:
        logger.info("AWC retornou 0 METARs para bbox %s", bbox)
        return empty_stations_df()

    df = _metar_payload_to_df(payload, extent)

    if cache is not None and not df.empty:
        _save_cache(cache, json.dumps(payload))

    logger.info("METAR: %d estações em %s", len(df), bbox)
    return df


def _metar_payload_to_df(payload: object, extent: list[float]) -> pd.DataFrame:
    """Converte o payload bruto do AWC (lista de objetos JSON) no DataFrame canônico."""
    if not isinstance(payload, list) or not payload:
        return empty_stations_df()
    records = [_metar_record_from_json(o) for o in payload if isinstance(o, dict)]
    return _filter_extent(normalize_station_records(records), extent)


# ═══════════════════════════════════════════════════════════════════════════════
#  SYNOP — OGIMET + tabela de coordenadas WMO (nsd_bbsss)
# ═══════════════════════════════════════════════════════════════════════════════


def _parse_dms_coord(token: str) -> float | None:
    """Converte 'DD-MM-SSH' ou 'DD-MMH' (nsd_bbsss) em grau decimal sinalizado."""
    token = token.strip()
    if not token:
        return None
    hemi = token[-1]
    body = token[:-1] if hemi in "NSEW" else token
    parts = body.split("-")
    try:
        deg = float(parts[0])
        minutes = float(parts[1]) if len(parts) > 1 else 0.0
        seconds = float(parts[2]) if len(parts) > 2 else 0.0
    except (ValueError, IndexError):
        return None
    val = deg + minutes / 60.0 + seconds / 3600.0
    if hemi in ("S", "W"):
        val = -val
    return val


def _load_wmo_coords(data_dir: Path | None, timeout: int = 60) -> dict[str, tuple[float, float]]:
    """Carrega o mapa WMO BBSSS → (lat, lon) a partir da tabela nsd_bbsss (cacheada)."""
    text = None
    cache = data_dir / "nsd_bbsss.txt" if data_dir is not None else None
    if cache is not None and cache.exists():
        try:
            text = cache.read_text("latin-1")
        except Exception:
            text = None

    if text is None:
        import requests

        last_err: Exception | None = None
        for url in _WMO_STATION_LIST_URLS:
            try:
                resp = requests.get(url, timeout=timeout)
                resp.raise_for_status()
                text = resp.text
                if cache is not None:
                    _save_cache(cache, text)
                break
            except Exception as e:
                last_err = e
                continue
        if text is None:
            logger.warning("Falha ao baixar tabela de estações WMO: %s", last_err)
            return {}

    coords: dict[str, tuple[float, float]] = {}
    for line in text.splitlines():
        # Campos separados por ';' — bloco;estação;...;lat;lon;...
        fields = line.split(";")
        if len(fields) < 9:
            continue
        block, station = fields[0].strip(), fields[1].strip()
        lat = _parse_dms_coord(fields[7])
        lon = _parse_dms_coord(fields[8])
        if lat is None or lon is None:
            continue
        coords[f"{block}{station}"] = (lat, lon)
    logger.info("Tabela WMO carregada: %d estações", len(coords))
    return coords


def _synop_record_from_decoded(
    decoded: dict, station_id: str, coords: dict[str, tuple[float, float]]
) -> dict | None:
    """Constrói um registro canônico a partir de um SYNOP decodificado (pymetdecoder)."""
    latlon = coords.get(station_id)
    if latlon is None:
        return None
    lat, lon = latlon

    def _g(*keys):
        node = decoded
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return None
            node = node[k]
        return node

    air_t = _g("air_temperature", "value")
    dew_t = _g("dewpoint_temperature", "value")
    mslp = _g("sea_level_pressure", "value")  # hPa

    # Vento: a velocidade pode vir em KT ou MPS (depende do wind_indicator)
    wspd = _g("surface_wind", "speed", "value")
    wunit = _g("surface_wind", "speed", "unit")
    if isinstance(wspd, (int, float)) and str(wunit).upper() in ("KT", "KMH"):
        wspd = wspd * _KT_TO_MS if str(wunit).upper() == "KT" else wspd / 3.6
    wdir = _g("surface_wind", "direction", "value")
    u, v = wind_components(wspd, wdir)

    okta = _g("cloud_cover", "oktas")
    if okta is None:
        okta = _g("cloud_cover", "value")

    wx = _g("present_weather", "value")

    return {
        "station_id": station_id,
        "latitude": lat,
        "longitude": lon,
        "air_temperature": air_t,
        "dew_point_temperature": dew_t,
        "air_pressure_at_sea_level": mslp,
        "eastward_wind": u,
        "northward_wind": v,
        "cloud_coverage": okta,
        "current_wx1_symbol": wx,
    }


def fetch_synop(
    extent: list[float],
    when: datetime | None = None,
    data_dir: Path | None = None,
    force_download: bool = False,
    timeout: int = 90,
    progress_callback=None,
) -> pd.DataFrame:
    """Baixa SYNOPs do OGIMET para a hora alvo e retorna o DataFrame canônico.

    SYNOP é reportado de 6/6h (00/06/12/18 UTC); a hora alvo é arredondada para
    o sinótico mais próximo. Requer `pymetdecoder` — se ausente, retorna vazio.

    O OGIMET responde com a cobertura GLOBAL; para acelerar (e evitar decodificar
    milhares de relatórios), as estações são pré-filtradas pelo `extent` ANTES da
    decodificação, usando a tabela de coordenadas WMO.
    """
    import logging as _logging

    import requests

    def _emit(msg: str):
        if progress_callback:
            progress_callback(msg)

    try:
        from pymetdecoder import synop as _synop
    except Exception:
        logger.warning("pymetdecoder não instalado — SYNOP indisponível.")
        return empty_stations_df()

    target = when or datetime.now(UTC)
    # Arredonda para o sinótico de 6h mais próximo (para baixo)
    synop_hour = (target.hour // 6) * 6
    target = target.replace(hour=synop_hour, minute=0, second=0, microsecond=0)
    stamp = target.strftime("%Y%m%d%H%M")

    _emit("SYNOP: carregando tabela de estações...")
    coords = _load_wmo_coords(data_dir, timeout=timeout)
    if not coords:
        return empty_stations_df()

    # Conjunto de estações dentro do extent (pré-filtro antes de decodificar)
    lon_min, lat_min, lon_max, lat_max = extent
    in_extent = {
        sid
        for sid, (la, lo) in coords.items()
        if lon_min <= lo <= lon_max and lat_min <= la <= lat_max
    }

    # Cache da resposta BRUTA do OGIMET (global, por hora sinótica) — reativar
    # o overlay fica instantâneo sem re-baixar o arquivo global.
    raw_cache = Path(data_dir) / f"synop_raw_{stamp}.txt" if data_dir is not None else None
    text = None
    if raw_cache is not None and raw_cache.exists() and not force_download:
        try:
            text = raw_cache.read_text("utf-8")
            _emit(f"SYNOP: usando cache de {synop_hour:02d}Z...")
        except Exception:
            text = None

    if text is None:
        _emit(f"SYNOP: baixando observações de {synop_hour:02d}Z...")
        params = {"begin": stamp, "end": stamp, "lang": "eng", "header": "yes"}
        try:
            resp = requests.get(_OGIMET_SYNOP_URL, params=params, timeout=timeout)
            resp.raise_for_status()
            text = resp.text
            if raw_cache is not None and text.strip():
                _save_cache(raw_cache, text)
        except Exception as e:
            logger.warning("Falha ao baixar SYNOP do OGIMET: %s", e)
            return empty_stations_df()

    _emit("SYNOP: decodificando observações da região...")
    records: list[dict] = []
    # Silencia o ruído de WARNING do pymetdecoder (grupos não-padrão dos relatórios)
    _logging.disable(_logging.WARNING)
    try:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Formato OGIMET: bloco,ano,mes,dia,hora,min,AAXX ... report
            parts = line.split(",")
            if len(parts) < 7:
                continue
            station_id = parts[0].strip()
            # Pré-filtro: só decodifica estações dentro do extent
            if station_id not in in_extent:
                continue
            report = ",".join(parts[6:]).strip()
            if not report.startswith("AAXX"):
                continue
            try:
                decoded = _synop.SYNOP().decode(report)
            except Exception:
                continue
            rec = _synop_record_from_decoded(decoded, station_id, coords)
            if rec is not None:
                records.append(rec)
    finally:
        _logging.disable(_logging.NOTSET)

    df = _filter_extent(normalize_station_records(records), extent)
    logger.info("SYNOP: %d estações para %s", len(df), stamp)
    return df


# ═══════════════════════════════════════════════════════════════════════════════
#  CACHE
# ═══════════════════════════════════════════════════════════════════════════════


def _cache_path(
    data_dir: Path | None, source: str, extent: list[float], when: datetime | None
) -> Path | None:
    if data_dir is None:
        return None
    tag = when.strftime("%Y%m%d%H") if when else "latest"
    ext_tag = "_".join(f"{v:.0f}" for v in extent)
    return Path(data_dir) / f"obs_{source}_{ext_tag}_{tag}.json"


def _save_cache(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.warning("Falha ao salvar cache %s: %s", path.name, e)
