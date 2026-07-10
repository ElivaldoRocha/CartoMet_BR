"""Download e processamento de campos ERA5 (reanálise Copernicus, via CDS API).

Diferente do IFS (``ecmwf.py``, previsão operacional), o ERA5 é **reanálise**:
indexada por data/hora **absolutas** (não por rodada+step), publicada com ~5 dias
de atraso (ERA5T) e recortável **no servidor** (``area=[N,W,S,E]``).

A credencial do CDS vem de ``cds_credentials.make_cds_client`` (nunca escreve
``~/.cdsapirc``). O produto final é um :class:`~cartomet_br.data.ecmwf.PLFieldData`
— o **mesmo** payload do IFS —, de modo que a renderização (``add_pl_layer``) não
precisa de nenhuma mudança.

Fase 1: Single Levels (t2m, vento 10 m, PNMM, precipitação, água precipitável),
modo **instantâneo** (uma hora) ou **agregado do período** (média/máx/mín/soma),
calculado no cliente a partir das horas baixadas (Blindagem: um só caminho de
código, funciona offline a partir do cache).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr
from scipy.ndimage import gaussian_filter

from cartomet_br.data.cds_credentials import make_cds_client
from cartomet_br.data.ecmwf import (
    VARIABLE_REGISTRY,
    CacheMissError,
    PLFieldData,
    _cache_only_active,
)

logger = logging.getLogger(__name__)

# Datasets do CDS (reanálise horária).
ERA5_SINGLE_LEVELS = "reanalysis-era5-single-levels"
ERA5_PRESSURE_LEVELS = "reanalysis-era5-pressure-levels"

# Modos de agregação temporal (reduzem o período a um campo 2-D). Quais modos
# uma variável OFERECE é decidido pelo seu perfil (AGG_PROFILES); esta tupla é o
# universo aceito pelo engine — que permanece permissivo (round-trip de projetos
# antigos, ver load_era5_field).
#   hora            instantâneo — 1 dia, 1 hora
#   media           média de TODAS as horas de todos os dias
#   media_hora      média SÓ da hora fixa ao longo dos dias (ex.: média das 12 UTC)
#   maxima          máxima ABSOLUTA do período (todas as horas)
#   minima          mínima absoluta do período
#   max_diaria      duas etapas: máxima diária → média das máximas diárias
#   min_diaria      duas etapas: mínima diária → média das mínimas diárias
#   soma            total do período (precipitação — acumulação)
#   soma_diaria     total de cada dia → média entre os dias (precip: mm/dia médio)
#   max_soma_diaria total de cada dia → maior (precip: dia mais chuvoso, mm/dia)
# Índices de EVENTO (reduções do período; ver AGG_INDEX_MODES / _aggregate):
#   idx_cdd/idx_cwd dias secos/úmidos consecutivos (spell), idx_wetdays contagem,
#   idx_rx5day soma móvel de 5 dias, idx_hotdays/idx_warmspell/idx_tropicalnights
#   sobre a série diária de Tmax/Tmin.
AGG_MODES: tuple[str, ...] = (
    "hora",
    "media",
    "media_hora",
    "maxima",
    "minima",
    "max_diaria",
    "min_diaria",
    "soma",
    "soma_diaria",
    "max_soma_diaria",
    "idx_cdd",
    "idx_cwd",
    "idx_wetdays",
    "idx_rx5day",
    "idx_hotdays",
    "idx_warmspell",
    "idx_tropicalnights",
)

# Índices de evento (subconjunto de AGG_MODES). Cada um vira uma chave de render
# própria (INDEX_RENDER_KEY) — o valor não é mais precip/temperatura, e sim
# "dias" ou "mm", então NÃO herda o cmap/unidade da variável-fonte.
AGG_INDEX_MODES: frozenset[str] = frozenset(
    {
        "idx_cdd",
        "idx_cwd",
        "idx_wetdays",
        "idx_rx5day",
        "idx_hotdays",
        "idx_warmspell",
        "idx_tropicalnights",
    }
)

# Índices cujo limiar é DEFINIDO PELO USUÁRIO (regional): dias quentes/onda de calor.
AGG_USER_THRESH_MODES: frozenset[str] = frozenset({"idx_hotdays", "idx_warmspell"})

# Chave do VARIABLE_REGISTRY que RENDERIZA cada índice (cmap/unit próprios).
INDEX_RENDER_KEY: dict[str, str] = {
    "idx_cdd": "era5_idx_cdd",
    "idx_cwd": "era5_idx_cwd",
    "idx_wetdays": "era5_idx_wetdays",
    "idx_rx5day": "era5_idx_rx5day",
    "idx_hotdays": "era5_idx_hotdays",
    "idx_warmspell": "era5_idx_warmspell",
    "idx_tropicalnights": "era5_idx_tropicalnights",
}

# Limiares ETCCDI/OMM (não inventados). O de dias quentes/onda de calor é regional
# → definido pelo usuário (spinbox); este é só o valor inicial do controle.
WET_DAY_MM: float = 1.0  # dia úmido (ETCCDI): precip diária ≥ 1 mm
TROPICAL_NIGHT_C: float = 20.0  # noite quente (ETCCDI TR): Tmin > 20 °C
HOT_DAY_DEFAULT_C: float = 30.0  # valor inicial do spinbox de dias quentes (°C)

# Modos que pedem UMA hora ao CDS (dia único ou hora fixa ao longo do período).
_SINGLE_HOUR_AGGS: frozenset[str] = frozenset({"hora", "media_hora"})

# ── Perfis de agregação por variável ────────────────────────────────────────
# A agregação temporal NÃO é neutra em relação à física do campo: somar
# temperatura não tem sentido, a unidade da chuva muda com o modo, o pico é o que
# importa numa rajada. Cada variável declara um perfil; a UI oferece SÓ os modos
# do perfil, na ordem dada (o 1º é o default). O engine, porém, aceita qualquer
# modo de AGG_MODES — os perfis filtram só a criação na interface.
AGG_LABELS: dict[str, str] = {
    "hora": "Instantâneo (1 hora)",
    "media": "Média do período (todas as horas)",
    "media_hora": "Média do período à hora fixa",
    "maxima": "Máxima do período (absoluta)",
    "minima": "Mínima do período (absoluta)",
    "max_diaria": "Média das máximas diárias",
    "min_diaria": "Média das mínimas diárias",
    "soma": "Total do período",
    "soma_diaria": "Total diário médio",
    "max_soma_diaria": "Dia mais chuvoso (total)",
    "idx_cdd": "Índice — Dias Secos Consec. (CDD)",
    "idx_cwd": "Índice — Dias Úmidos Consec. (CWD)",
    "idx_wetdays": "Índice — Dias Úmidos (≥1 mm)",
    "idx_rx5day": "Índice — Chuva Máx. 5 dias (Rx5day)",
    "idx_hotdays": "Índice — Dias Quentes (Tmax>limiar)",
    "idx_warmspell": "Índice — Onda de Calor (dias consec.)",
    "idx_tropicalnights": "Índice — Noites Quentes (Tmin>20 °C)",
}

AGG_PROFILES: dict[str, tuple[str, ...]] = {
    # estado instantâneo, ciclo diurno fraco: média é o padrão climatológico
    "state": ("media", "hora", "maxima", "minima"),
    # estado com ciclo diurno relevante: habilita "média à hora fixa"
    "state_diurnal": ("media", "hora", "media_hora", "maxima", "minima"),
    # fluxos médios (radiação, já em W/m²): mesma lógica do estado diurno
    "flux": ("media", "hora", "media_hora", "maxima", "minima"),
    # Temp. Máxima (mx2t): a média das máximas diárias é a métrica climatológica;
    # índices de calor entram DEPOIS (não mexem no default)
    "temp_max": ("max_diaria", "maxima", "hora", "idx_hotdays", "idx_warmspell"),
    # Temp. Mínima (mn2t) + noites quentes
    "temp_min": ("min_diaria", "minima", "hora", "idx_tropicalnights"),
    # rajada: o pico do período é o que o previsor quer por padrão
    "gust": ("maxima", "media", "max_diaria", "hora"),
    # precipitação (acumulação): total do período é o padrão; índices de evento no fim
    "accumulation": (
        "soma",
        "soma_diaria",
        "max_soma_diaria",
        "hora",
        "idx_cdd",
        "idx_cwd",
        "idx_wetdays",
        "idx_rx5day",
    ),
}


def profile_modes(profile: str) -> tuple[str, ...]:
    """Modos ofertados por um perfil (1º = default). Vazio se perfil desconhecido."""
    return AGG_PROFILES.get(profile, ())


def default_agg(profile: str) -> str:
    """Modo default de um perfil (o primeiro da lista); 'media' como fallback seguro."""
    modes = AGG_PROFILES.get(profile)
    return modes[0] if modes else "media"


# Acima deste nº de dias, um pedido horário (campo agregado ou série) baixa muitas
# horas do CDS — a GUI pede confirmação antes (aviso, não bloqueio).
ERA5_LONG_PERIOD_DAYS = 31


def era5_period_days(date_start: str, date_end: str) -> int:
    """Número de dias no intervalo fechado [start, end] (ISO), mínimo 1."""
    d0 = _date.fromisoformat(date_start)
    d1 = _date.fromisoformat(date_end)
    return abs((d1 - d0).days) + 1


# Frase curta da métrica, embutida no título do mapa (ver _valid_time_str).
_AGG_LABEL: dict[str, str] = {
    "media": "média do período",
    "maxima": "máxima do período",
    "minima": "mínima do período",
    "soma": "total do período",
    "soma_diaria": "total diário médio",
    "max_soma_diaria": "dia mais chuvoso",
    "max_diaria": "média das máximas diárias",
    "min_diaria": "média das mínimas diárias",
    "idx_cdd": "dias secos consecutivos (CDD)",
    "idx_cwd": "dias úmidos consecutivos (CWD)",
    "idx_wetdays": "dias úmidos (≥1 mm)",
    "idx_rx5day": "chuva máx. em 5 dias (Rx5day)",
    "idx_hotdays": "dias quentes",
    "idx_warmspell": "onda de calor (dias consec.)",
    "idx_tropicalnights": "noites quentes (Tmin>20 °C)",
}


@dataclass(frozen=True)
class ERA5Variable:
    """Descreve uma variável ERA5: como pedir ao CDS e como ler o NetCDF.

    ``registry_key`` aponta para :data:`VARIABLE_REGISTRY` (cmap/nome/unidade/
    conversão) — fonte única de verdade de render, compartilhada com o IFS.
    """

    registry_key: str
    cds_names: list[str]  # nomes longos do CDS (ex.: "2m_temperature")
    nc_names: list[str]  # nomes curtos no NetCDF (ex.: "t2m")
    is_wind: bool = False
    pressure_level: bool = False  # True → dataset pressure-levels (exige nível)
    agg_profile: str = "state"  # perfil de agregação (define os modos ofertados na UI)

    @property
    def dataset(self) -> str:
        return ERA5_PRESSURE_LEVELS if self.pressure_level else ERA5_SINGLE_LEVELS


# Catálogo ERA5 (Fase 1: Single Levels; Fase 2: Pressure Levels). A chave é a
# mesma usada no VARIABLE_REGISTRY; o layer_id inclui o nível nos campos PL.
ERA5_VARIABLES: dict[str, ERA5Variable] = {
    # ── Single Levels (superfície) ──
    "era5_t2m": ERA5Variable("era5_t2m", ["2m_temperature"], ["t2m"], agg_profile="state_diurnal"),
    "era5_wind10m": ERA5Variable(
        "era5_wind10m",
        ["10m_u_component_of_wind", "10m_v_component_of_wind"],
        ["u10", "v10"],
        is_wind=True,
        agg_profile="state",
    ),
    "era5_mslp": ERA5Variable(
        "era5_mslp", ["mean_sea_level_pressure"], ["msl"], agg_profile="state"
    ),
    "era5_precip": ERA5Variable(
        "era5_precip", ["total_precipitation"], ["tp"], agg_profile="accumulation"
    ),
    "era5_tcwv": ERA5Variable(
        "era5_tcwv", ["total_column_water_vapour"], ["tcwv"], agg_profile="state"
    ),
    # ── Temp. Máx/Mín 2 m (extremo pós-processado; média das diárias = clim.) ──
    "era5_tmax": ERA5Variable(
        "era5_tmax",
        ["maximum_2m_temperature_since_previous_post_processing"],
        ["mx2t"],
        agg_profile="temp_max",
    ),
    "era5_tmin": ERA5Variable(
        "era5_tmin",
        ["minimum_2m_temperature_since_previous_post_processing"],
        ["mn2t"],
        agg_profile="temp_min",
    ),
    # ── Radiação (fluxos médios, já em W/m² — sem desacumular; CDS usa 'avg_*') ──
    "era5_olr": ERA5Variable(
        "era5_olr", ["mean_top_net_long_wave_radiation_flux"], ["avg_tnlwrf"], agg_profile="flux"
    ),
    "era5_toa_sw": ERA5Variable(
        "era5_toa_sw",
        ["mean_top_net_short_wave_radiation_flux"],
        ["avg_tnswrf"],
        agg_profile="flux",
    ),
    "era5_ssrd": ERA5Variable(
        "era5_ssrd",
        ["mean_surface_downward_short_wave_radiation_flux"],
        ["avg_sdswrf"],
        agg_profile="flux",
    ),
    "era5_strd": ERA5Variable(
        "era5_strd",
        ["mean_surface_downward_long_wave_radiation_flux"],
        ["avg_sdlwrf"],
        agg_profile="flux",
    ),
    # ── Oceano + umidade de superfície ──
    "era5_sst": ERA5Variable("era5_sst", ["sea_surface_temperature"], ["sst"], agg_profile="state"),
    "era5_d2m": ERA5Variable(
        "era5_d2m", ["2m_dewpoint_temperature"], ["d2m"], agg_profile="state_diurnal"
    ),
    "era5_tcc": ERA5Variable("era5_tcc", ["total_cloud_cover"], ["tcc"], agg_profile="state"),
    # ── Convecção / vento ──
    "era5_cape": ERA5Variable(
        "era5_cape",
        ["convective_available_potential_energy"],
        ["cape"],
        agg_profile="state_diurnal",
    ),
    "era5_kindex": ERA5Variable("era5_kindex", ["k_index"], ["kx"], agg_profile="state_diurnal"),
    "era5_totalx": ERA5Variable(
        "era5_totalx", ["total_totals_index"], ["totalx"], agg_profile="state_diurnal"
    ),
    "era5_gust": ERA5Variable(
        "era5_gust",
        ["10m_wind_gust_since_previous_post_processing"],
        ["fg10"],
        agg_profile="gust",
    ),
    # ── Pressure Levels (níveis isobáricos) ──
    "era5pl_gh": ERA5Variable(
        "era5pl_gh", ["geopotential"], ["z"], pressure_level=True, agg_profile="state"
    ),
    "era5pl_t": ERA5Variable(
        "era5pl_t", ["temperature"], ["t"], pressure_level=True, agg_profile="state"
    ),
    "era5pl_wind": ERA5Variable(
        "era5pl_wind",
        ["u_component_of_wind", "v_component_of_wind"],
        ["u", "v"],
        is_wind=True,
        pressure_level=True,
        agg_profile="state",
    ),
    "era5pl_r": ERA5Variable(
        "era5pl_r", ["relative_humidity"], ["r"], pressure_level=True, agg_profile="state"
    ),
    "era5pl_w": ERA5Variable(
        "era5pl_w", ["vertical_velocity"], ["w"], pressure_level=True, agg_profile="state"
    ),
    "era5pl_q": ERA5Variable(
        "era5pl_q", ["specific_humidity"], ["q"], pressure_level=True, agg_profile="state"
    ),
    "era5pl_vo": ERA5Variable(
        "era5pl_vo", ["vorticity"], ["vo"], pressure_level=True, agg_profile="state"
    ),
    "era5pl_d": ERA5Variable(
        "era5pl_d", ["divergence"], ["d"], pressure_level=True, agg_profile="state"
    ),
}


def available_variables() -> dict[str, ERA5Variable]:
    """Cópia do catálogo Fase 1 (para a GUI popular seletores)."""
    return dict(ERA5_VARIABLES)


# ── Construção da requisição ────────────────────────────────────────────────


def _date_span(date_start: str, date_end: str) -> tuple[list[str], list[str], list[str]]:
    """Listas (anos, meses, dias) que COBREM o intervalo [start, end] (ISO).

    Usa listas year/month/day (sempre aceitas pelo adaptador do CDS). Em
    intervalos que cruzam mês, o produto cartesiano super-pede alguns dias —
    o recorte exato é feito depois, em xarray (``.sel(time=slice(...))``).
    """
    d0 = _date.fromisoformat(date_start)
    d1 = _date.fromisoformat(date_end)
    if d1 < d0:
        d0, d1 = d1, d0
    years: set[int] = set()
    months: set[int] = set()
    days: set[int] = set()
    cur = d0
    # itera dia a dia (intervalos de reanálise aqui são curtos — dias, não anos)
    while cur <= d1:
        years.add(cur.year)
        months.add(cur.month)
        days.add(cur.day)
        cur = _date.fromordinal(cur.toordinal() + 1)
    return (
        [f"{y:04d}" for y in sorted(years)],
        [f"{m:02d}" for m in sorted(months)],
        [f"{d:02d}" for d in sorted(days)],
    )


def build_era5_request(
    var: ERA5Variable,
    date_start: str,
    date_end: str,
    hour: int,
    agg: str,
    extent: list[float],
    level: int = 0,
) -> dict[str, Any]:
    """Monta o dict do ``client.retrieve`` (Single- ou Pressure-Levels).

    ``extent`` é [lon_min, lat_min, lon_max, lat_max]; o CDS pede ``area`` como
    [Norte, Oeste, Sul, Leste]. Modo "hora" pede só a hora escolhida; os modos
    agregados pedem as 24 horas (agregação client-side). Para variáveis de nível
    de pressão, ``level`` (hPa) vira ``pressure_level``.
    """
    if agg not in AGG_MODES:
        raise ValueError(f"Modo de agregação inválido: {agg!r} (use {AGG_MODES})")
    if var.pressure_level and level <= 0:
        raise ValueError(f"{var.registry_key} exige um nível de pressão (hPa) > 0")

    lon_min, lat_min, lon_max, lat_max = extent
    area = [lat_max, lon_min, lat_min, lon_max]  # N, W, S, E

    single_hour = agg in _SINGLE_HOUR_AGGS
    times = [f"{hour:02d}:00"] if single_hour else [f"{h:02d}:00" for h in range(24)]

    years, months, days = _date_span(date_start, date_end)

    request: dict[str, Any] = {
        "product_type": ["reanalysis"],
        "variable": list(var.cds_names),
        "year": years,
        "month": months,
        "day": days,
        "time": times,
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": [round(float(v), 3) for v in area],
    }
    if var.pressure_level:
        request["pressure_level"] = [str(int(level))]
    return request


def _cache_path(
    var: ERA5Variable,
    date_start: str,
    date_end: str,
    hour: int,
    agg: str,
    extent: list[float],
    data_dir: Path,
    level: int = 0,
) -> Path:
    """Nome de cache determinístico (região e nível incluídos)."""
    lon_min, lat_min, lon_max, lat_max = (round(float(v)) for v in extent)
    span = (
        date_start.replace("-", "")
        if agg == "hora"
        else (f"{date_start.replace('-', '')}_{date_end.replace('-', '')}")
    )
    # Modos de hora fixa embutem a hora no nome; os de 24 h/dia usam "24h".
    htag = f"{hour:02d}z" if agg in _SINGLE_HOUR_AGGS else "24h"
    region = f"{lon_min}_{lon_max}_{lat_min}_{lat_max}"
    ltag = f"_{int(level)}hPa" if var.pressure_level else ""
    return data_dir / f"{var.registry_key}{ltag}_{span}_{htag}_{agg}_{region}.nc"


# ── Leitura / agregação ─────────────────────────────────────────────────────


def _time_dim(ds: xr.Dataset) -> str | None:
    """Nome da dimensão temporal (CDS-Beta usa 'valid_time'; legado usa 'time')."""
    for name in ("valid_time", "time", "forecast_reference_time"):
        if name in ds.dims:
            return name
    return None


def _normalize(ds: xr.Dataset) -> xr.Dataset:
    """Aplaina dims espúrias do NetCDF do CDS (expver/number) e ordena longitude."""
    # ERA5T pode trazer a dim 'expver' (1=ERA5, 5=ERA5T), mutuamente exclusivas.
    if "expver" in ds.dims:
        ds = ds.mean(dim="expver", skipna=True)
    if "number" in ds.dims:
        ds = ds.isel(number=0, drop=True)
    if "longitude" in ds.coords:
        ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180).sortby("longitude")
    return ds


def _max_consecutive(mask: np.ndarray, axis: int) -> np.ndarray:
    """Maior sequência de ``True`` ao longo de ``axis`` (retorna o eixo colapsado).

    Usado nos índices de *spell* (CDD/CWD/onda de calor): p/ cada ponto de grade,
    o comprimento da maior sequência contígua de dias que satisfazem a condição.
    """
    mask = np.moveaxis(np.asarray(mask, dtype=bool), axis, 0)
    best = np.zeros(mask.shape[1:], dtype=int)
    cur = np.zeros(mask.shape[1:], dtype=int)
    for t in range(mask.shape[0]):
        cur = np.where(mask[t], cur + 1, 0)
        best = np.maximum(best, cur)
    return best


def _aggregate_index(
    da: xr.DataArray, agg: str, tdim: str, thresh: float, conversion: Any
) -> np.ndarray:
    """Índices de EVENTO sobre a série DIÁRIA (em unidades físicas).

    ``conversion`` (do VARIABLE_REGISTRY da variável-fonte) leva o dado bruto às
    unidades físicas ANTES de limiarizar (chuva → mm; Tmax/Tmin → °C), de modo que
    ``WET_DAY_MM``, ``TROPICAL_NIGHT_C`` e o ``thresh`` do usuário estejam na mesma
    escala do campo. O resultado é uma contagem de "dias" (ou mm, no Rx5day).
    """
    phys = conversion(da) if conversion is not None else da
    if agg in ("idx_cdd", "idx_cwd", "idx_wetdays"):
        daily = phys.resample({tdim: "1D"}).sum()  # total diário (mm/dia)
        wet = daily >= WET_DAY_MM
        if agg == "idx_wetdays":
            return np.asarray(wet.sum(dim=tdim).values, dtype=float)
        run = (~wet) if agg == "idx_cdd" else wet  # CDD = dias secos; CWD = úmidos
        return _max_consecutive(run.values, run.get_axis_num(tdim)).astype(float)
    if agg == "idx_rx5day":
        daily = phys.resample({tdim: "1D"}).sum()
        roll = daily.rolling({tdim: 5}, min_periods=1).sum()  # soma móvel de 5 dias
        return np.asarray(roll.max(dim=tdim, skipna=True).values)
    if agg == "idx_hotdays":
        daily = phys.resample({tdim: "1D"}).max()  # Tmax diária (°C)
        return np.asarray((daily > thresh).sum(dim=tdim).values, dtype=float)
    if agg == "idx_warmspell":
        daily = phys.resample({tdim: "1D"}).max()
        hot = daily > thresh
        return _max_consecutive(hot.values, hot.get_axis_num(tdim)).astype(float)
    if agg == "idx_tropicalnights":
        daily = phys.resample({tdim: "1D"}).min()  # Tmin diária (°C)
        return np.asarray((daily > TROPICAL_NIGHT_C).sum(dim=tdim).values, dtype=float)
    raise ValueError(f"Índice inválido: {agg!r}")


def _aggregate(
    da: xr.DataArray, agg: str, tdim: str, thresh: float = 0.0, conversion: Any = None
) -> np.ndarray:
    """Reduz a dimensão temporal a um campo 2-D conforme o modo.

    ``max_diaria``/``min_diaria`` são de DUAS etapas: extremo diário (``resample``
    por dia) e depois média entre os dias — a métrica climatológica clássica de
    Tmáx/Tmín média, distinta da máxima/mínima absoluta do período. ``media_hora``
    já recebe só a hora fixa (uma por dia), então é uma média simples no tempo.
    Os modos ``idx_*`` são índices de evento (ver :func:`_aggregate_index`).
    """
    if tdim not in da.dims:
        return np.asarray(da.values)
    if agg == "hora":
        return np.asarray(da.isel({tdim: 0}).values)
    if agg in ("media", "media_hora"):
        return np.asarray(da.mean(dim=tdim, skipna=True).values)
    if agg == "maxima":
        return np.asarray(da.max(dim=tdim, skipna=True).values)
    if agg == "minima":
        return np.asarray(da.min(dim=tdim, skipna=True).values)
    if agg == "soma":
        return np.asarray(da.sum(dim=tdim, skipna=True).values)
    if agg == "max_diaria":
        daily = da.resample({tdim: "1D"}).max()
        return np.asarray(daily.mean(dim=tdim, skipna=True).values)
    if agg == "min_diaria":
        daily = da.resample({tdim: "1D"}).min()
        return np.asarray(daily.mean(dim=tdim, skipna=True).values)
    if agg == "soma_diaria":
        # acumulação: total de cada dia → média entre os dias (mm/dia médio)
        daily = da.resample({tdim: "1D"}).sum()
        return np.asarray(daily.mean(dim=tdim, skipna=True).values)
    if agg == "max_soma_diaria":
        # acumulação: total de cada dia → o maior (dia mais chuvoso, mm/dia)
        daily = da.resample({tdim: "1D"}).sum()
        return np.asarray(daily.max(dim=tdim, skipna=True).values)
    if agg in AGG_INDEX_MODES:
        return _aggregate_index(da, agg, tdim, thresh, conversion)
    raise ValueError(f"Modo de agregação inválido: {agg!r}")


def _valid_time_str(
    date_start: str, date_end: str, hour: int, agg: str, thresh: float = 0.0
) -> str:
    if agg == "hora":
        return f"{date_start} {hour:02d}:00"
    period = date_start if date_start == date_end else f"{date_start} a {date_end}"
    if agg == "media_hora":
        return f"{period} (média às {hour:02d} UTC)"
    label = _AGG_LABEL.get(agg, agg)
    if agg in AGG_USER_THRESH_MODES:  # dias quentes / onda de calor: mostra o limiar
        label = f"{label} > {thresh:g} °C"
    return f"{period} ({label})"


# Unidade da precipitação (acumulação) por modo — o número muda de natureza:
# total → mm; total diário → mm/dia; hora única → mm/h.
_PRECIP_UNIT_BY_AGG: dict[str, str] = {
    "soma": "mm",
    "soma_diaria": "mm/dia",
    "max_soma_diaria": "mm/dia",
    "hora": "mm/h",
}


def _era5_unit(var: ERA5Variable, var_info: dict[str, Any], agg: str) -> str:
    """Unidade a exibir. Para acumulação (chuva) a unidade acompanha o modo;
    demais variáveis seguem o ``unit_display`` do VARIABLE_REGISTRY."""
    if var.agg_profile == "accumulation":
        return _PRECIP_UNIT_BY_AGG.get(agg, str(var_info.get("unit_display", "mm")))
    return str(var_info.get("unit_display", ""))


def _select_level(ds: xr.Dataset, level: int) -> xr.Dataset:
    """Isola um nível isobárico (dim 'pressure_level' no CDS-Beta; 'level' no legado)."""
    for name in ("pressure_level", "level", "isobaricInhPa"):
        if name in ds.dims or name in ds.coords:
            return ds.sel({name: level}, method="nearest")
    return ds  # single-level: nada a selecionar


def load_era5_field(
    variable_key: str,
    date_start: str,
    date_end: str,
    hour: int,
    agg: str,
    extent: list[float],
    level: int = 0,
    data_dir: Path = Path("data"),
    smoothing_sigma: float = 1.0,
    force_download: bool = False,
    thresh: float = 0.0,
) -> PLFieldData:
    """Baixa (cache-first) e processa um campo ERA5, retornando ``PLFieldData``.

    ``level`` (hPa) é usado apenas para variáveis de nível de pressão. ``thresh``
    (°C) só é usado pelos índices de dias quentes/onda de calor. Respeita
    ``cache_only_mode()`` de ``ecmwf.py``: ao abrir um projeto salvo, um cache
    miss vira ``CacheMissError`` em vez de ir à rede (human-in-the-loop).
    """
    var = ERA5_VARIABLES.get(variable_key)
    if var is None:
        raise ValueError(f"Variável ERA5 desconhecida: {variable_key!r}")
    if agg not in AGG_MODES:
        raise ValueError(f"Modo de agregação inválido: {agg!r} (use {AGG_MODES})")
    out_level = int(level) if var.pressure_level else 0

    data_dir = Path(data_dir)
    target = _cache_path(var, date_start, date_end, hour, agg, extent, data_dir, out_level)

    if not target.exists() or force_download:
        if _cache_only_active():
            raise CacheMissError(f"ERA5 fora do cache (modo somente-cache): {target.name}")
        logger.info("Baixando ERA5 %s → %s", variable_key, target.name)
        request = build_era5_request(var, date_start, date_end, hour, agg, extent, out_level)
        client = make_cds_client()
        # download atômico: escreve .part e renomeia (evita cache corrompido em falha)
        tmp = target.with_suffix(".part")
        client.retrieve(var.dataset, request, str(tmp))
        tmp.replace(target)
    else:
        logger.info("ERA5 em cache, reutilizando: %s", target.name)

    ds = _normalize(xr.open_dataset(target, engine="netcdf4"))
    try:
        # recorte estrito (o server já recortou por 'area'; robustez p/ bordas)
        ds = ds.sel(
            longitude=slice(extent[0], extent[2]),
            latitude=slice(extent[3], extent[1]),
        )
        if var.pressure_level:
            ds = _select_level(ds, out_level)
        tdim = _time_dim(ds)
        if tdim is not None:
            ds = ds.sortby(tdim).sel({tdim: slice(f"{date_start}T00:00", f"{date_end}T23:59")})
            tdim = _time_dim(ds)  # pode ter virado escalar após o slice

        lons_arr = np.asarray(ds["longitude"].values)
        lats_arr = np.asarray(ds["latitude"].values)
        var_info = VARIABLE_REGISTRY[var.registry_key]
        conversion = var_info.get("conversion")

        # Índice de evento → a saída (dias/mm) NÃO é mais precip/temperatura: usa
        # uma chave de render própria (cmap/unidade). A conversão da fonte é aplicada
        # DENTRO do índice (para limiarizar em unidades físicas), não no resultado.
        is_index = agg in AGG_INDEX_MODES
        render_key = INDEX_RENDER_KEY[agg] if is_index else var.registry_key
        render_info = VARIABLE_REGISTRY[render_key]

        # Parâmetros para reconstruir a camada ao reabrir o projeto (cache-first).
        extra = {
            "variable": var.registry_key,
            "date_start": date_start,
            "date_end": date_end,
            "hour": int(hour),
            "agg": agg,
            "level": out_level,
            "thresh": float(thresh),
        }

        if var.is_wind:
            u = _aggregate(ds[var.nc_names[0]], agg, tdim or "")
            v = _aggregate(ds[var.nc_names[1]], agg, tdim or "")
            if smoothing_sigma > 0:
                u = gaussian_filter(u, sigma=smoothing_sigma)
                v = gaussian_filter(v, sigma=smoothing_sigma)
            speed = np.sqrt(u**2 + v**2)
            return PLFieldData(
                values=speed,
                lons=lons_arr,
                lats=lats_arr,
                u_values=u,
                v_values=v,
                wind_speed=speed,
                variable=var.registry_key,
                level=out_level,
                unit=_era5_unit(var, var_info, agg),
                valid_time=_valid_time_str(date_start, date_end, hour, agg),
                base_time="ERA5 (reanálise, atraso ~5 dias)",
                step=0,
                source="era5",
                extra=extra,
            )

        values = _aggregate(
            ds[var.nc_names[0]], agg, tdim or "", thresh=thresh, conversion=conversion
        )
        if conversion is not None and not is_index:
            values = conversion(values)
        # Índices são contagens/spells (mapa "de blocos"): suavizar borraria a
        # informação inteira — só suaviza campos contínuos.
        if smoothing_sigma > 0 and not is_index:
            values = gaussian_filter(values, sigma=smoothing_sigma)

        return PLFieldData(
            values=values,
            lons=lons_arr,
            lats=lats_arr,
            variable=render_key,
            level=out_level,
            unit=_era5_unit(var, render_info, agg),
            valid_time=_valid_time_str(date_start, date_end, hour, agg, thresh),
            base_time="ERA5 (reanálise, atraso ~5 dias)",
            step=0,
            source="era5",
            extra=extra,
        )
    finally:
        ds.close()


# ── Série temporal num ponto (Fase 3) ────────────────────────────────────────


@dataclass
class ERA5Series:
    """Série temporal horária de uma variável ERA5 num ponto de grade."""

    times: np.ndarray  # datetime64[ns]
    values: np.ndarray  # 1-D alinhado a ``times``
    variable: str  # chave do VARIABLE_REGISTRY
    level: int  # hPa (0 = superfície)
    grid_lon: float  # ponto de grade efetivamente amostrado
    grid_lat: float
    unit: str
    date_start: str
    date_end: str


def _point_area(lon: float, lat: float, pad: float = 0.5) -> list[float]:
    """Caixa pequena [lon_min, lat_min, lon_max, lat_max] ao redor do ponto."""
    lon = max(-179.9, min(179.9, float(lon)))
    lat = max(-89.9, min(89.9, float(lat)))
    return [
        max(-180.0, lon - pad),
        max(-90.0, lat - pad),
        min(180.0, lon + pad),
        min(90.0, lat + pad),
    ]


def _series_cache_path(
    var: ERA5Variable,
    date_start: str,
    date_end: str,
    lon: float,
    lat: float,
    level: int,
    data_dir: Path,
) -> Path:
    """Nome de cache determinístico da série (ponto + período + nível)."""
    span = f"{date_start.replace('-', '')}_{date_end.replace('-', '')}"
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    pt = f"{abs(lat):.2f}{ns}_{abs(lon):.2f}{ew}"
    ltag = f"_{int(level)}hPa" if var.pressure_level else ""
    return data_dir / f"era5series_{var.registry_key}{ltag}_{span}_{pt}.nc"


def load_era5_timeseries(
    variable_key: str,
    date_start: str,
    date_end: str,
    lon: float,
    lat: float,
    level: int = 0,
    data_dir: Path = Path("data"),
    force_download: bool = False,
) -> ERA5Series:
    """Baixa (cache-first) a série horária de uma variável ERA5 num ponto.

    Pede TODAS as horas do período sobre uma caixa pequena ao redor do ponto e
    amostra o nó de grade mais próximo. Respeita ``cache_only_mode()``.
    """
    var = ERA5_VARIABLES.get(variable_key)
    if var is None:
        raise ValueError(f"Variável ERA5 desconhecida: {variable_key!r}")
    out_level = int(level) if var.pressure_level else 0

    data_dir = Path(data_dir)
    area = _point_area(lon, lat)
    target = _series_cache_path(var, date_start, date_end, lon, lat, out_level, data_dir)

    if not target.exists() or force_download:
        if _cache_only_active():
            raise CacheMissError(f"Série ERA5 fora do cache (somente-cache): {target.name}")
        logger.info("Baixando série ERA5 %s → %s", variable_key, target.name)
        # agg="media" só serve para pedir as 24 horas; a série NÃO agrega no tempo.
        request = build_era5_request(var, date_start, date_end, 0, "media", area, out_level)
        client = make_cds_client()
        tmp = target.with_suffix(".part")
        client.retrieve(var.dataset, request, str(tmp))
        tmp.replace(target)
    else:
        logger.info("Série ERA5 em cache, reutilizando: %s", target.name)

    ds = _normalize(xr.open_dataset(target, engine="netcdf4"))
    try:
        if var.pressure_level:
            ds = _select_level(ds, out_level)
        # nó de grade mais próximo do ponto clicado
        ds = ds.sel(longitude=float(lon), latitude=float(lat), method="nearest")
        grid_lon = float(np.asarray(ds["longitude"].values))
        grid_lat = float(np.asarray(ds["latitude"].values))

        tdim = _time_dim(ds)
        if tdim is None:
            raise ValueError("Série ERA5 sem dimensão temporal — dado inesperado do CDS.")
        ds = ds.sortby(tdim).sel({tdim: slice(f"{date_start}T00:00", f"{date_end}T23:59")})
        times = np.asarray(ds[tdim].values)

        var_info = VARIABLE_REGISTRY[var.registry_key]
        conversion = var_info.get("conversion")
        if var.is_wind:
            u = np.asarray(ds[var.nc_names[0]].values)
            v = np.asarray(ds[var.nc_names[1]].values)
            values = np.sqrt(u**2 + v**2)
        else:
            values = np.asarray(ds[var.nc_names[0]].values)
            if conversion is not None:
                values = conversion(values)

        return ERA5Series(
            times=times,
            values=values,
            variable=var.registry_key,
            level=out_level,
            grid_lon=grid_lon,
            grid_lat=grid_lat,
            unit=str(var_info.get("unit_display", "")),
            date_start=date_start,
            date_end=date_end,
        )
    finally:
        ds.close()
