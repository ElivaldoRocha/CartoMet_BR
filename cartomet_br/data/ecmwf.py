"""
Download e processamento de dados do ECMWF Open Data.

O ECMWF Open Data disponibiliza previsões do modelo IFS:
- Atualização: 4x ao dia (00, 06, 12, 18 UTC)
- Resolução: ~0.25° (HRES) ou 0.5° (ensemble)
- Alcance: até 10 dias
"""

import logging
import os
import tempfile
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import xarray as xr
from ecmwf.opendata import Client
from scipy.ndimage import gaussian_filter

warnings.filterwarnings("ignore", message=".*skipping variable.*")

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  DOWNLOAD DE DADOS
# ═══════════════════════════════════════════════════════════════════════════════

def download_ecmwf(
    variables: list[str],
    levels: list[int] | None = None,
    step: int = 0,
    cycle: int | None = None,
    output_path: Path | None = None,
    data_dir: Path = Path("data"),
    source: str = "ecmwf",
    force_download: bool = False,
) -> Path:
    """
    Baixa dados do ECMWF Open Data (IFS).
    
    Se o arquivo já existir, reutiliza (evita rate limit 429).
    
    Parâmetros
    ----------
    variables : list[str]
        Variáveis a baixar. Exemplos:
        - 'msl': Mean Sea Level Pressure (Pa)
        - 'gh': Geopotential Height (gpm) - requer levels
        - 't': Temperature (K)
        - 'u', 'v': Wind components (m/s)
    levels : list[int], opcional
        Níveis de pressão (hPa) para variáveis em altitude
    step : int
        Passo de previsão em horas (0 = análise)
    cycle : int, opcional
        Rodada específica em horas UTC (0, 6, 12 ou 18).
        Se None, o ECMWF retorna a mais recente disponível.
    output_path : Path, opcional
        Caminho do arquivo de saída
    data_dir : Path
        Diretório para salvar os dados
    source : str
        Fonte dos dados: "ecmwf", "aws", "azure", "google"
    force_download : bool
        Se True, força novo download mesmo que arquivo exista
        
    Retorna
    -------
    Path
        Caminho do arquivo GRIB baixado
    """
    # Garante que data_dir é um Path válido
    if data_dir is None:
        data_dir = Path(tempfile.gettempdir()) / "cartomet_br_data"
        logger.warning("data_dir era None, usando: %s", data_dir)
    
    data_dir = Path(data_dir)
    
    # Cria diretório com tratamento de erro
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise PermissionError(
            f"Sem permissão para criar diretório: {data_dir}\n"
            f"Vá em Arquivo → Configurar Diretório de Dados e escolha outro local."
        ) from e
    except Exception as e:
        raise RuntimeError(f"Erro ao criar diretório {data_dir}: {e}") from e
    
    # Verifica se podemos escrever no diretório
    test_file = data_dir / ".write_test"
    try:
        test_file.write_text("test")
        test_file.unlink()
    except Exception as e:
        raise PermissionError(
            f"Sem permissão de escrita em: {data_dir}\n"
            f"Vá em Arquivo → Configurar Diretório de Dados e escolha outro local.\n"
            f"Erro: {e}"
        )
    
    if output_path is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        var_str = "_".join(variables)
        output_path = data_dir / f"ecmwf_{var_str}_f{step:03d}.grib2"
    else:
        output_path = Path(output_path)
    
    # Verifica se arquivo já existe
    if output_path.exists() and not force_download:
        logger.info("Arquivo já existe, reutilizando: %s", output_path)
        return output_path
    
    # Cria cliente ECMWF
    try:
        client = Client(source=source)
    except Exception as e:
        raise ConnectionError(f"Erro ao conectar ao ECMWF: {e}") from e
    
    request_params = {
        "step": step,
        "type": "fc",
        "param": variables,
        "target": str(output_path),
    }
    
    # Se o usuário escolheu uma rodada específica, passa o parâmetro time
    if cycle is not None:
        request_params["time"] = cycle
    
    if levels is not None:
        request_params["levelist"] = levels

    cycle_str = f"{cycle:02d}Z" if cycle is not None else "auto"
    logger.info("Baixando dados do ECMWF Open Data...")
    logger.info("  Variáveis: %s", variables)
    logger.info("  Níveis: %s", levels)
    logger.info("  Rodada: %s", cycle_str)
    logger.info("  Step: %sh", step)
    logger.info("  Destino: %s", output_path)
    
    try:
        result = client.download(**request_params)
        
        # Verifica se o download foi bem-sucedido
        if not output_path.exists():
            raise FileNotFoundError(
                f"Download não criou o arquivo esperado: {output_path}"
            )
        
        if output_path.stat().st_size == 0:
            output_path.unlink()  # Remove arquivo vazio
            raise RuntimeError("Download criou arquivo vazio")
            
    except Exception as e:
        error_msg = str(e)
        
        # Trata erros específicos
        if "Cannot establish latest" in error_msg:
            raise ValueError(
                f"Dados não disponíveis para step +{step}h.\n"
                f"O ECMWF disponibiliza apenas steps múltiplos de 3 (ex: 0, 3, 6, 9...)."
            ) from e
        elif "404" in error_msg:
            raise FileNotFoundError(
                f"Arquivo não encontrado no servidor ECMWF.\n"
                f"Verifique se o step +{step}h é válido."
            ) from e
        elif "429" in error_msg or "Too Many Requests" in error_msg:
            raise ConnectionError(
                f"Servidor ECMWF sobrecarregado (erro 429).\n\n"
                f"O servidor limita conexões simultâneas.\n"
                f"Aguarde 2-3 minutos e tente novamente.\n\n"
                f"Dica: os dados também estão disponíveis via AWS, Azure e Google Cloud."
            ) from e
        elif "SSL" in error_msg or "certificate" in error_msg.lower():
            raise ConnectionError(
                f"Erro de conexão SSL. Verifique sua internet."
            ) from e
        else:
            raise RuntimeError(f"Erro no download ECMWF: {error_msg}") from e
    
    logger.info("  Arquivo salvo: %s", output_path)
    return output_path


# ═══════════════════════════════════════════════════════════════════════════════
#  PROCESSAMENTO DE DADOS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SynopticData:
    """Container para dados sinóticos processados."""
    
    # Campos 2D
    pnmm: np.ndarray           # Pressão ao nível do mar (hPa)
    thickness: np.ndarray      # Espessura 1000-500 hPa (m)
    
    # Coordenadas
    lons: np.ndarray
    lats: np.ndarray
    lon2d: np.ndarray
    lat2d: np.ndarray
    
    # Metadados
    valid_time: str
    extent: list[float]
    base_time: str = ""        # Hora da rodada base (ex: "06Z 17/03/2026")
    step: int = 0              # Step de previsão em horas


def load_synoptic_data(
    extent: list[float],
    step: int = 0,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    smoothing_sigma: float = 1.5,
    source: str = "ecmwf",
    force_download: bool = False,
) -> SynopticData:
    """
    Carrega e processa dados sinóticos do ECMWF.
    
    Baixa PNMM e altura geopotencial (500/1000 hPa), calcula espessura,
    aplica suavização gaussiana e retorna um objeto SynopticData.
    
    Parâmetros
    ----------
    extent : list[float]
        [lon_min, lat_min, lon_max, lat_max]
    step : int
        Passo de previsão em horas
    cycle : int, opcional
        Rodada específica (0, 6, 12 ou 18). None = mais recente.
    data_dir : Path
        Diretório para dados
    smoothing_sigma : float
        Desvio padrão do filtro gaussiano (0 = sem suavização)
    source : str
        Fonte ECMWF
    force_download : bool
        Forçar novo download
        
    Retorna
    -------
    SynopticData
        Objeto com todos os campos processados
    """
    # Validação de entrada
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    
    data_dir = Path(data_dir)
    
    cycle_str = f"{cycle:02d}Z" if cycle is not None else "auto"
    logger.info("Carregando dados sinóticos")
    logger.info("  Diretório: %s", data_dir)
    logger.info("  Rodada: %s", cycle_str)
    logger.info("  Step: +%sh", step)
    logger.info("  Extent: %s", extent)
    
    # Nome inclui data + rodada para evitar reutilizar dados errados.
    # cycle_date vem do combo (data real da rodada selecionada),
    # ou fallback para hoje se auto.
    date_str = cycle_date if cycle_date else datetime.now(timezone.utc).strftime("%Y%m%d")
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"
    
    msl_file = download_ecmwf(
        variables=["msl"],
        step=step,
        cycle=cycle,
        output_path=data_dir / f"ecmwf_msl_{date_str}_{cycle_tag}_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
    )
    
    gh_file = download_ecmwf(
        variables=["gh"],
        levels=[500, 1000],
        step=step,
        cycle=cycle,
        output_path=data_dir / f"ecmwf_gh_{date_str}_{cycle_tag}_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
    )
    
    # Leitura com xarray
    logger.info("Carregando dados com xarray...")
    
    ds_msl = xr.open_dataset(
        msl_file, 
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "meanSea"},
            "errors": "ignore",
        }
    )
    
    ds_gh = xr.open_dataset(
        gh_file, 
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "isobaricInhPa"},
            "errors": "ignore",
        }
    )
    
    # Ajusta longitude de 0-360 para -180 a 180
    ds_msl = ds_msl.assign_coords(longitude=(ds_msl.longitude + 180) % 360 - 180)
    ds_msl = ds_msl.sortby("longitude")
    
    ds_gh = ds_gh.assign_coords(longitude=(ds_gh.longitude + 180) % 360 - 180)
    ds_gh = ds_gh.sortby("longitude")
    
    # Seleciona região
    msl = ds_msl["msl"].sel(
        longitude=slice(extent[0], extent[2]),
        latitude=slice(extent[3], extent[1])  # lat decrescente no ECMWF
    )
    
    gh = ds_gh["gh"].sel(
        longitude=slice(extent[0], extent[2]),
        latitude=slice(extent[3], extent[1])
    )
    
    # Processa campos
    pnmm = msl.values / 100.0  # Pa → hPa
    hght_500 = gh.sel(isobaricInhPa=500).values
    hght_1000 = gh.sel(isobaricInhPa=1000).values
    thickness = hght_500 - hght_1000
    
    # Suavização gaussiana
    if smoothing_sigma > 0:
        pnmm = gaussian_filter(pnmm, sigma=smoothing_sigma)
        thickness = gaussian_filter(thickness, sigma=smoothing_sigma)
    
    # Coordenadas
    lons = msl.longitude.values
    lats = msl.latitude.values
    lon2d, lat2d = np.meshgrid(lons, lats)
    
    # Tempo
    valid_time = ds_msl.valid_time.values
    valid_time_str = np.datetime_as_string(valid_time, unit="m")
    
    # Rodada base (hora de inicialização do modelo)
    base_time_str = ""
    try:
        if "time" in ds_msl.coords:
            bt = ds_msl.time.values
            bt_dt = np.datetime64(bt, "s").astype("datetime64[s]").astype(datetime)
            base_time_str = f"{bt_dt.strftime('%HZ %d/%m/%Y')}"
        elif hasattr(ds_msl, "attrs") and "GRIB_dataDate" in ds_msl.attrs:
            base_time_str = str(ds_msl.attrs["GRIB_dataDate"])
    except (KeyError, IndexError, ValueError, TypeError) as e:
        logger.warning("Não foi possível extrair base_time do dataset MSL: %s", e)
        base_time_str = "(não identificada)"
    
    # Fecha datasets
    ds_msl.close()
    ds_gh.close()
    
    logger.info("  Dados carregados com sucesso!")
    logger.info("  Rodada base: %s", base_time_str)
    logger.info("  Válido: %s UTC", valid_time_str)
    
    return SynopticData(
        pnmm=pnmm,
        thickness=thickness,
        lons=lons,
        lats=lats,
        lon2d=lon2d,
        lat2d=lat2d,
        valid_time=valid_time_str,
        extent=extent,
        base_time=base_time_str,
        step=step,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  ESTIMATIVA DE RODADA DISPONÍVEL
# ═══════════════════════════════════════════════════════════════════════════════

# Cronograma de publicação do ECMWF Open Data:
# Rodada  | Disponível ~   | Alcance
# 00Z     | 07:30 UTC      | até +240h (10 dias)
# 06Z     | 13:30 UTC      | até +144h (6 dias)
# 12Z     | 19:30 UTC      | até +240h (10 dias)
# 18Z     | 01:30 UTC (+1) | até +144h (6 dias)

CYCLE_SCHEDULE = [
    {"cycle": 0,  "label": "00Z", "avail_hour": 7.5,  "max_step": 240},
    {"cycle": 6,  "label": "06Z", "avail_hour": 13.5, "max_step": 144},
    {"cycle": 12, "label": "12Z", "avail_hour": 19.5, "max_step": 240},
    {"cycle": 18, "label": "18Z", "avail_hour": 1.5,  "max_step": 144},  # dia seguinte
]


def estimate_available_cycles() -> dict:
    """
    Estima quais rodadas ECMWF estão disponíveis agora.
    
    O ECMWF mantém dados no servidor por pelo menos 24h, então precisamos
    verificar tanto as rodadas de hoje quanto as de ontem.
    
    Retorna
    -------
    dict com:
        - "available": lista de rodadas disponíveis (mais recente primeiro)
        - "latest": a rodada mais recente estimada
        - "next": próxima rodada esperada e horário estimado
        - "utc_now": hora UTC atual
    """
    now = datetime.now(timezone.utc)
    hour_decimal = now.hour + now.minute / 60.0
    today = now.date()
    yesterday = today - timedelta(days=1)
    
    available = []
    
    # Tabela de publicação: (cycle_hour, delay_hours_after_cycle)
    # 00Z publica ~07:30 → delay 7.5h
    # 06Z publica ~13:30 → delay 7.5h
    # 12Z publica ~19:30 → delay 7.5h
    # 18Z publica ~01:30+1d → delay 7.5h
    PUBLISH_DELAY = 7.5  # horas após a rodada
    
    # Verifica rodadas dos últimos 2 dias (ontem e hoje)
    for day_offset in [0, -1]:  # hoje, ontem
        check_date = today + timedelta(days=day_offset)
        
        for info in CYCLE_SCHEDULE:
            cycle_h = info["cycle"]
            
            # Datetime de quando essa rodada ficou disponível
            cycle_dt = datetime(check_date.year, check_date.month, check_date.day,
                                cycle_h, 0, tzinfo=timezone.utc)
            publish_dt = cycle_dt + timedelta(hours=PUBLISH_DELAY)
            
            # Está disponível se já passou do horário de publicação
            if now >= publish_dt:
                # Evita duplicatas
                already = any(
                    c["cycle"] == cycle_h and c["date_str"] == check_date.strftime("%d/%m/%Y")
                    for c in available
                )
                if not already:
                    available.append({
                        "cycle": cycle_h,
                        "label": info["label"],
                        "max_step": info["max_step"],
                        "base_datetime": cycle_dt,
                        "date_str": check_date.strftime("%d/%m/%Y"),
                    })
    
    # Ordena por datetime, mais recente primeiro
    available.sort(key=lambda x: x["base_datetime"], reverse=True)
    
    # Limita a 6 rodadas mais recentes (evita lista enorme)
    available = available[:6]
    
    # Próxima rodada (a mais próxima que ainda não está disponível)
    next_cycle = None
    for day_offset in [0, 1]:
        check_date = today + timedelta(days=day_offset)
        for info in CYCLE_SCHEDULE:
            cycle_dt = datetime(check_date.year, check_date.month, check_date.day,
                                info["cycle"], 0, tzinfo=timezone.utc)
            publish_dt = cycle_dt + timedelta(hours=PUBLISH_DELAY)
            
            if now < publish_dt:
                wait_minutes = int((publish_dt - now).total_seconds() / 60)
                next_cycle = {
                    "label": info["label"],
                    "estimated_time": publish_dt.strftime("%H:%M UTC"),
                    "wait_minutes": wait_minutes,
                    "date_str": check_date.strftime("%d/%m"),
                }
                break
        if next_cycle:
            break
    
    return {
        "available": available,
        "latest": available[0] if available else None,
        "next": next_cycle,
        "utc_now": now.strftime("%H:%M UTC"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  REGISTRO DE VARIÁVEIS EM NÍVEIS DE PRESSÃO + OLR
# ═══════════════════════════════════════════════════════════════════════════════

# Níveis de pressão disponíveis no IFS Open Data
PL_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]

VARIABLE_REGISTRY = {
    "gh": {
        "nome": "Altura Geopotencial",
        "param": ["gh"],
        "unit_raw": "m",
        "unit_display": "mgp",
        "conversion": None,
        "plot_type": "contour",
        "cmap": None,
        "symmetric": False,
        "category": "scalar",
    },
    "t": {
        "nome": "Temperatura",
        "param": ["t"],
        "unit_raw": "K",
        "unit_display": "°C",
        "conversion": lambda x: x - 273.15,
        "plot_type": "contourf",
        "cmap": "RdBu_r",
        "symmetric": False,
        "category": "scalar",
    },
    "wind": {
        "nome": "Vento",
        "param": ["u", "v"],
        "unit_raw": "m/s",
        "unit_display": "kt",
        "conversion": lambda x: x * 1.94384,
        "plot_type": "wind",
        "cmap": None,
        "symmetric": False,
        "category": "wind",
    },
    "wind_speed": {
        "nome": "Vel. do Vento (isotacas)",
        "param": ["u", "v"],
        "unit_raw": "m/s",
        "unit_display": "km/h",
        "conversion": lambda x: x * 3.6,
        "plot_type": "contourf",
        "cmap": "YlOrRd",
        "symmetric": False,
        "category": "wind_speed",
    },
    "w": {
        "nome": "Vel. Vertical (ω)",
        "param": ["w"],
        "unit_raw": "Pa/s",
        "unit_display": "hPa/h",
        "conversion": lambda x: x * 36.0,
        "plot_type": "contourf",
        "cmap": "RdBu",
        "symmetric": True,
        "category": "scalar",
    },
    "q": {
        "nome": "Umidade Específica",
        "param": ["q"],
        "unit_raw": "kg/kg",
        "unit_display": "g/kg",
        "conversion": lambda x: x * 1000.0,
        "plot_type": "contourf",
        "cmap": "BrBG",
        "symmetric": False,
        "category": "scalar",
    },
    "r": {
        "nome": "Umidade Relativa",
        "param": ["r"],
        "unit_raw": "%",
        "unit_display": "%",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "BrBG",
        "symmetric": False,
        "category": "scalar",
    },
    "d": {
        "nome": "Divergência",
        "param": ["d"],
        "unit_raw": "s⁻¹",
        "unit_display": "×10⁻⁵ s⁻¹",
        "conversion": lambda x: x * 1e5,
        "plot_type": "contourf",
        "cmap": "RdBu_r",
        "symmetric": True,
        "category": "scalar",
    },
    "vo": {
        "nome": "Vorticidade Relativa",
        "param": ["vo"],
        "unit_raw": "s⁻¹",
        "unit_display": "×10⁻⁵ s⁻¹",
        "conversion": lambda x: x * 1e5,
        "plot_type": "contourf",
        "cmap": "RdBu_r",
        "symmetric": True,
        "category": "scalar",
    },
    "olr": {
        "nome": "OLR",
        "param": ["ttr"],
        "unit_raw": "J/m²",
        "unit_display": "W/m²",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "olr_classic",
        "symmetric": False,
        "category": "radiation",
        "min_step": 3,
    },
    "temp_adv": {
        "nome": "Advecção de Temperatura",
        "param": ["t", "u", "v"],
        "unit_raw": "K/s",
        "unit_display": "°C/h",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "RdBu_r",
        "symmetric": True,
        "category": "derived",
    },
    "temp_grad": {
        "nome": "Gradiente de Temperatura",
        "param": ["t"],
        "unit_raw": "K/m",
        "unit_display": "°C/100km",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "YlOrRd",
        "symmetric": False,
        "category": "derived",
    },
    "frontogenesis": {
        "nome": "Frontogênese (Petterssen)",
        "param": ["t", "u", "v"],
        "unit_raw": "K/m/s",
        "unit_display": "°C/100km/3h",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "RdBu_r",
        "symmetric": True,
        "category": "derived",
    },
    "tcwv": {
        "nome": "Água Precipitável",
        "param": ["tcwv"],
        "unit_raw": "kg/m²",
        "unit_display": "mm",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "YlGnBu",
        "symmetric": False,
        "category": "surface",
    },
    "mfc": {
        "nome": "Convergência de Umidade (MFC)",
        "param": ["q", "u", "v"],
        "unit_raw": "g/kg/s",
        "unit_display": "×10⁻⁵ g/kg/s",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "BrBG",
        "symmetric": True,
        "category": "derived",
    },
}


@dataclass
class PLFieldData:
    """Container para dados de variável em nível de pressão."""
    
    values: np.ndarray
    lons: np.ndarray
    lats: np.ndarray
    
    # Para vento: componentes separados
    u_values: np.ndarray | None = None
    v_values: np.ndarray | None = None
    wind_speed: np.ndarray | None = None
    
    # Metadados
    variable: str = ""
    level: int = 0
    unit: str = ""
    valid_time: str = ""
    base_time: str = ""
    step: int = 0


def load_pl_variable(
    variable_key: str,
    level: int,
    extent: list[float],
    step: int = 0,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    smoothing_sigma: float = 1.0,
    source: str = "ecmwf",
    force_download: bool = False,
) -> PLFieldData:
    """
    Baixa e processa uma variável em nível de pressão.
    
    Parâmetros
    ----------
    variable_key : str
        Chave do VARIABLE_REGISTRY (ex: "t", "wind", "gh")
    level : int
        Nível de pressão em hPa
    extent : list[float]
        [lon_min, lat_min, lon_max, lat_max]
    step : int
        Passo de previsão em horas
    cycle : int, opcional
        Rodada específica (0, 6, 12 ou 18)
    data_dir : Path
        Diretório para salvar os dados
    smoothing_sigma : float
        Desvio padrão do filtro gaussiano
    source : str
        Fonte ECMWF
    force_download : bool
        Forçar novo download
        
    Retorna
    -------
    PLFieldData
        Objeto com os dados processados
    """
    if variable_key not in VARIABLE_REGISTRY:
        raise ValueError(f"Variável '{variable_key}' não encontrada no registro.")
    
    var_info = VARIABLE_REGISTRY[variable_key]
    params = var_info["param"]
    
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)
    
    date_str = cycle_date if cycle_date else datetime.now(timezone.utc).strftime("%Y%m%d")
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"
    param_str = "_".join(params)
    
    logger.info("Carregando %s em %s hPa", var_info['nome'], level)
    
    # Variáveis derivadas fazem seus próprios downloads
    if var_info["category"] == "derived":
        return _compute_derived_variable(
            variable_key=variable_key,
            level=level,
            extent=extent,
            step=step,
            cycle=cycle,
            cycle_date=cycle_date,
            data_dir=data_dir,
            smoothing_sigma=smoothing_sigma,
            source=source,
            valid_time_str="",
            base_time_str="",
        )
    
    # Download
    grib_file = download_ecmwf(
        variables=params,
        levels=[level],
        step=step,
        cycle=cycle,
        output_path=data_dir / f"ecmwf_{param_str}_{date_str}_{cycle_tag}_{level}hPa_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
    )
    
    # Leitura com xarray
    ds = xr.open_dataset(
        grib_file,
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "isobaricInhPa"},
            "errors": "ignore",
        }
    )
    
    # Ajusta longitude de 0-360 para -180 a 180
    ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180)
    ds = ds.sortby("longitude")
    
    # Seleciona região e nível
    sel_kwargs = {
        "longitude": slice(extent[0], extent[2]),
        "latitude": slice(extent[3], extent[1]),
    }
    if "isobaricInhPa" in ds.coords:
        sel_kwargs["isobaricInhPa"] = level
    
    # Extrai metadados de tempo
    valid_time_str = ""
    base_time_str = ""
    try:
        if "valid_time" in ds.coords:
            vt = ds.valid_time.values
            valid_time_str = np.datetime_as_string(vt, unit="m")
        if "time" in ds.coords:
            bt = ds.time.values
            bt_dt = np.datetime64(bt, "s").astype("datetime64[s]").astype(datetime)
            base_time_str = f"{bt_dt.strftime('%HZ %d/%m/%Y')}"
    except (KeyError, IndexError, ValueError, TypeError) as e:
        logger.warning("Não foi possível extrair metadados de tempo do dataset: %s", e)

    # Processa conforme a categoria
    if var_info["category"] == "wind":
        u_data = ds["u"].sel(**sel_kwargs).values
        v_data = ds["v"].sel(**sel_kwargs).values
        
        if smoothing_sigma > 0:
            u_data = gaussian_filter(u_data, sigma=smoothing_sigma)
            v_data = gaussian_filter(v_data, sigma=smoothing_sigma)
        
        ws = np.sqrt(u_data**2 + v_data**2)
        
        lons_arr = ds["u"].sel(**sel_kwargs).longitude.values
        lats_arr = ds["u"].sel(**sel_kwargs).latitude.values
        
        # Conversão para kt (speed display)
        conv = var_info["conversion"]
        ws_display = conv(ws) if conv else ws
        
        ds.close()
        
        return PLFieldData(
            values=ws_display,
            lons=lons_arr,
            lats=lats_arr,
            u_values=u_data,
            v_values=v_data,
            wind_speed=ws_display,
            variable=variable_key,
            level=level,
            unit=var_info["unit_display"],
            valid_time=valid_time_str,
            base_time=base_time_str,
            step=step,
        )
    
    elif var_info["category"] == "wind_speed":
        # Isotacas: baixa u+v, calcula magnitude, retorna como campo escalar
        u_data = ds["u"].sel(**sel_kwargs).values
        v_data = ds["v"].sel(**sel_kwargs).values
        
        if smoothing_sigma > 0:
            u_data = gaussian_filter(u_data, sigma=smoothing_sigma)
            v_data = gaussian_filter(v_data, sigma=smoothing_sigma)
        
        ws = np.sqrt(u_data**2 + v_data**2)
        lons_arr = ds["u"].sel(**sel_kwargs).longitude.values
        lats_arr = ds["u"].sel(**sel_kwargs).latitude.values
        
        conv = var_info["conversion"]
        ws_display = conv(ws) if conv else ws
        
        ds.close()
        
        return PLFieldData(
            values=ws_display,
            lons=lons_arr,
            lats=lats_arr,
            variable=variable_key,
            level=level,
            unit=var_info["unit_display"],
            valid_time=valid_time_str,
            base_time=base_time_str,
            step=step,
        )
    
    else:
        # Escalar (t, gh, w, q, r, d, vo)
        param_name = params[0]
        data_var = ds[param_name].sel(**sel_kwargs).values
        lons_arr = ds[param_name].sel(**sel_kwargs).longitude.values
        lats_arr = ds[param_name].sel(**sel_kwargs).latitude.values
        
        if smoothing_sigma > 0:
            data_var = gaussian_filter(data_var, sigma=smoothing_sigma)
        
        # Conversão de unidade
        conv = var_info["conversion"]
        if conv is not None:
            data_var = conv(data_var)
        
        ds.close()
        
        return PLFieldData(
            values=data_var,
            lons=lons_arr,
            lats=lats_arr,
            variable=variable_key,
            level=level,
            unit=var_info["unit_display"],
            valid_time=valid_time_str,
            base_time=base_time_str,
            step=step,
        )


def _compute_derived_variable(
    variable_key: str,
    level: int,
    extent: list[float],
    step: int,
    cycle: int | None,
    cycle_date: str | None,
    data_dir: Path,
    smoothing_sigma: float,
    source: str,
    valid_time_str: str,
    base_time_str: str,
) -> PLFieldData:
    """
    Calcula variáveis derivadas (advecção de T, gradiente de T).
    
    Baixa t e u,v separadamente (reutiliza cache) e combina.
    """
    var_info = VARIABLE_REGISTRY[variable_key]
    date_str = cycle_date if cycle_date else datetime.now(timezone.utc).strftime("%Y%m%d")
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"
    
    # ─── Baixa temperatura ───
    t_file = download_ecmwf(
        variables=["t"],
        levels=[level],
        step=step,
        cycle=cycle,
        output_path=data_dir / f"ecmwf_t_{date_str}_{cycle_tag}_{level}hPa_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
    )
    
    ds_t = xr.open_dataset(
        t_file, engine="cfgrib",
        backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}, "errors": "ignore"}
    )
    ds_t = ds_t.assign_coords(longitude=(ds_t.longitude + 180) % 360 - 180)
    ds_t = ds_t.sortby("longitude")
    
    sel_kw = {
        "longitude": slice(extent[0], extent[2]),
        "latitude": slice(extent[3], extent[1]),
    }
    if "isobaricInhPa" in ds_t.coords:
        sel_kw["isobaricInhPa"] = level
    
    t_data = ds_t["t"].sel(**sel_kw).values  # Kelvin
    lons = ds_t["t"].sel(**sel_kw).longitude.values
    lats = ds_t["t"].sel(**sel_kw).latitude.values
    
    # Extrai metadados de tempo do dataset de temperatura
    try:
        if "valid_time" in ds_t.coords:
            valid_time_str = np.datetime_as_string(ds_t.valid_time.values, unit="m")
        if "time" in ds_t.coords:
            bt = ds_t.time.values
            bt_dt = np.datetime64(bt, "s").astype("datetime64[s]").astype(datetime)
            base_time_str = f"{bt_dt.strftime('%HZ %d/%m/%Y')}"
    except (KeyError, IndexError, ValueError, TypeError) as e:
        logger.warning("Não foi possível extrair metadados de tempo (derivada): %s", e)
    
    ds_t.close()
    
    # Converte lat/lon para metros (aproximação esférica)
    # dy e dx em metros para diferenças finitas
    lat_rad = np.deg2rad(lats)
    R = 6.371e6  # Raio da Terra em metros
    
    dlat = np.deg2rad(np.diff(lats).mean())  # espaçamento em radianos
    dlon = np.deg2rad(np.diff(lons).mean())
    
    dy = dlat * R  # metros por ponto em y
    # dx varia com a latitude
    dx_2d = dlon * R * np.cos(lat_rad)[:, np.newaxis] * np.ones((1, len(lons)))
    
    if smoothing_sigma > 0:
        t_data = gaussian_filter(t_data, sigma=smoothing_sigma)
    
    # ─── Gradiente de temperatura ───
    # dT/dy, dT/dx usando diferenças finitas centrais
    dTdy = np.gradient(t_data, dy, axis=0)       # ∂T/∂y
    dTdx = np.gradient(t_data, axis=1) / dx_2d   # ∂T/∂x
    
    if variable_key == "temp_grad":
        # |∇T| em °C/100km
        grad_mag = np.sqrt(dTdx**2 + dTdy**2)
        grad_display = grad_mag * 1e5  # K/m → °C/100km
        
        return PLFieldData(
            values=grad_display,
            lons=lons,
            lats=lats,
            variable=variable_key,
            level=level,
            unit=var_info["unit_display"],
            valid_time=valid_time_str,
            base_time=base_time_str,
            step=step,
        )
    
    elif variable_key == "temp_adv":
        # ─── Baixa vento ───
        uv_file = download_ecmwf(
            variables=["u", "v"],
            levels=[level],
            step=step,
            cycle=cycle,
            output_path=data_dir / f"ecmwf_u_v_{date_str}_{cycle_tag}_{level}hPa_f{step:03d}.grib2",
            data_dir=data_dir,
            source=source,
        )
        
        ds_uv = xr.open_dataset(
            uv_file, engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}, "errors": "ignore"}
        )
        ds_uv = ds_uv.assign_coords(longitude=(ds_uv.longitude + 180) % 360 - 180)
        ds_uv = ds_uv.sortby("longitude")
        
        sel_kw_uv = {
            "longitude": slice(extent[0], extent[2]),
            "latitude": slice(extent[3], extent[1]),
        }
        if "isobaricInhPa" in ds_uv.coords:
            sel_kw_uv["isobaricInhPa"] = level
        
        u_data = ds_uv["u"].sel(**sel_kw_uv).values
        v_data = ds_uv["v"].sel(**sel_kw_uv).values
        ds_uv.close()
        
        if smoothing_sigma > 0:
            u_data = gaussian_filter(u_data, sigma=smoothing_sigma)
            v_data = gaussian_filter(v_data, sigma=smoothing_sigma)
        
        # Advecção: -V · ∇T = -(u * ∂T/∂x + v * ∂T/∂y)
        # Sinal negativo: advecção positiva = aquecimento
        adv = -(u_data * dTdx + v_data * dTdy)
        
        # Converte K/s → °C/h (× 3600, K=°C para diferenças)
        adv_display = adv * 3600.0
        
        return PLFieldData(
            values=adv_display,
            lons=lons,
            lats=lats,
            variable=variable_key,
            level=level,
            unit=var_info["unit_display"],
            valid_time=valid_time_str,
            base_time=base_time_str,
            step=step,
        )
    
    elif variable_key == "frontogenesis":
        # ─── Frontogênese de Petterssen (2D horizontal) ───
        #
        # F = -1/(2|∇T|) × [ (∂T/∂x)²·(∂u/∂x) + (∂T/∂y)²·(∂v/∂y)
        #                   + (∂T/∂x)·(∂T/∂y)·(∂v/∂x + ∂u/∂y) ]
        #
        # F > 0 → frontogênese (gradiente se intensifica)
        # F < 0 → frontólise (gradiente se enfraquece)
        
        # ─── Baixa vento ───
        uv_file = download_ecmwf(
            variables=["u", "v"],
            levels=[level],
            step=step,
            cycle=cycle,
            output_path=data_dir / f"ecmwf_u_v_{date_str}_{cycle_tag}_{level}hPa_f{step:03d}.grib2",
            data_dir=data_dir,
            source=source,
        )
        
        ds_uv = xr.open_dataset(
            uv_file, engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}, "errors": "ignore"}
        )
        ds_uv = ds_uv.assign_coords(longitude=(ds_uv.longitude + 180) % 360 - 180)
        ds_uv = ds_uv.sortby("longitude")
        
        sel_kw_uv = {
            "longitude": slice(extent[0], extent[2]),
            "latitude": slice(extent[3], extent[1]),
        }
        if "isobaricInhPa" in ds_uv.coords:
            sel_kw_uv["isobaricInhPa"] = level
        
        u_data = ds_uv["u"].sel(**sel_kw_uv).values
        v_data = ds_uv["v"].sel(**sel_kw_uv).values
        ds_uv.close()
        
        if smoothing_sigma > 0:
            u_data = gaussian_filter(u_data, sigma=smoothing_sigma)
            v_data = gaussian_filter(v_data, sigma=smoothing_sigma)
        
        # Derivadas do vento
        dudx = np.gradient(u_data, axis=1) / dx_2d
        dudy = np.gradient(u_data, dy, axis=0)
        dvdx = np.gradient(v_data, axis=1) / dx_2d
        dvdy = np.gradient(v_data, dy, axis=0)
        
        # Magnitude do gradiente de T (evita divisão por zero)
        grad_mag = np.sqrt(dTdx**2 + dTdy**2)
        grad_mag = np.where(grad_mag < 1e-12, 1e-12, grad_mag)
        
        # Fórmula de Petterssen
        F = -(1.0 / (2.0 * grad_mag)) * (
            dTdx**2 * dudx +
            dTdy**2 * dvdy +
            dTdx * dTdy * (dvdx + dudy)
        )
        
        # Converte K/m/s → °C/100km/3h
        # × 1e5 (m→100km) × 10800 (s→3h) = × 1.08e9
        F_display = F * 1.08e9
        
        return PLFieldData(
            values=F_display,
            lons=lons,
            lats=lats,
            variable=variable_key,
            level=level,
            unit=var_info["unit_display"],
            valid_time=valid_time_str,
            base_time=base_time_str,
            step=step,
        )
    
    elif variable_key == "mfc":
        # ─── Convergência do Fluxo de Umidade (MFC) ───
        #
        # MFC = -(V · ∇q + q · ∇ · V)
        #      = -(u·∂q/∂x + v·∂q/∂y + q·(∂u/∂x + ∂v/∂y))
        #
        # Valores positivos → convergência de umidade (favorável a chuva)

        # ─── Baixa umidade específica ───
        q_file = download_ecmwf(
            variables=["q"],
            levels=[level],
            step=step,
            cycle=cycle,
            output_path=data_dir / f"ecmwf_q_{date_str}_{cycle_tag}_{level}hPa_f{step:03d}.grib2",
            data_dir=data_dir,
            source=source,
        )

        ds_q = xr.open_dataset(
            q_file, engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}, "errors": "ignore"}
        )
        ds_q = ds_q.assign_coords(longitude=(ds_q.longitude + 180) % 360 - 180)
        ds_q = ds_q.sortby("longitude")

        sel_kw_q = {
            "longitude": slice(extent[0], extent[2]),
            "latitude": slice(extent[3], extent[1]),
        }
        if "isobaricInhPa" in ds_q.coords:
            sel_kw_q["isobaricInhPa"] = level

        q_data = ds_q["q"].sel(**sel_kw_q).values  # kg/kg
        lons = ds_q["q"].sel(**sel_kw_q).longitude.values
        lats = ds_q["q"].sel(**sel_kw_q).latitude.values

        # Extrai metadados de tempo
        try:
            if "valid_time" in ds_q.coords:
                valid_time_str = np.datetime_as_string(ds_q.valid_time.values, unit="m")
            if "time" in ds_q.coords:
                bt = ds_q.time.values
                bt_dt = np.datetime64(bt, "s").astype("datetime64[s]").astype(datetime)
                base_time_str = f"{bt_dt.strftime('%HZ %d/%m/%Y')}"
        except (KeyError, IndexError, ValueError, TypeError) as e:
            logger.warning("Não foi possível extrair metadados de tempo (MFC): %s", e)

        ds_q.close()

        # ─── Baixa vento ───
        uv_file = download_ecmwf(
            variables=["u", "v"],
            levels=[level],
            step=step,
            cycle=cycle,
            output_path=data_dir / f"ecmwf_u_v_{date_str}_{cycle_tag}_{level}hPa_f{step:03d}.grib2",
            data_dir=data_dir,
            source=source,
        )

        ds_uv = xr.open_dataset(
            uv_file, engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}, "errors": "ignore"}
        )
        ds_uv = ds_uv.assign_coords(longitude=(ds_uv.longitude + 180) % 360 - 180)
        ds_uv = ds_uv.sortby("longitude")

        sel_kw_uv = {
            "longitude": slice(extent[0], extent[2]),
            "latitude": slice(extent[3], extent[1]),
        }
        if "isobaricInhPa" in ds_uv.coords:
            sel_kw_uv["isobaricInhPa"] = level

        u_data = ds_uv["u"].sel(**sel_kw_uv).values
        v_data = ds_uv["v"].sel(**sel_kw_uv).values
        ds_uv.close()

        if smoothing_sigma > 0:
            q_data = gaussian_filter(q_data, sigma=smoothing_sigma)
            u_data = gaussian_filter(u_data, sigma=smoothing_sigma)
            v_data = gaussian_filter(v_data, sigma=smoothing_sigma)

        # Converte q para g/kg antes do cálculo
        q_data = q_data * 1000.0  # kg/kg → g/kg

        # Grade em metros
        lat_rad_q = np.deg2rad(lats)
        R = 6.371e6
        dlat_q = np.deg2rad(np.diff(lats).mean())
        dlon_q = np.deg2rad(np.diff(lons).mean())
        dy_q = dlat_q * R
        dx_2d_q = dlon_q * R * np.cos(lat_rad_q)[:, np.newaxis] * np.ones((1, len(lons)))

        # Derivadas espaciais
        dqdx = np.gradient(q_data, axis=1) / dx_2d_q
        dqdy = np.gradient(q_data, dy_q, axis=0)
        dudx = np.gradient(u_data, axis=1) / dx_2d_q
        dvdy = np.gradient(v_data, dy_q, axis=0)

        # Divergência do vento
        div_v = dudx + dvdy

        # Advecção de umidade
        adv_q = u_data * dqdx + v_data * dqdy

        # MFC = -(advecção + q * divergência)
        mfc = -(adv_q + q_data * div_v)

        # Escala para visualização: × 10⁵
        mfc_display = mfc * 1e5

        return PLFieldData(
            values=mfc_display,
            lons=lons,
            lats=lats,
            variable=variable_key,
            level=level,
            unit=var_info["unit_display"],
            valid_time=valid_time_str,
            base_time=base_time_str,
            step=step,
        )

    raise ValueError(f"Variável derivada '{variable_key}' não implementada.")


def load_olr(
    extent: list[float],
    step: int = 3,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    smoothing_sigma: float = 1.0,
    source: str = "ecmwf",
    force_download: bool = False,
) -> PLFieldData:
    """
    Baixa e processa OLR (Outgoing Longwave Radiation).
    
    OLR = -ttr / (step_seconds)
    
    Onde ttr é "Top net thermal radiation" acumulado em J/m².
    A conversão divide pelo tempo de acumulação para obter W/m².
    O sinal negativo é porque ttr é negativo para radiação saindo.
    
    IMPORTANTE: step deve ser >= 3h (no step 0, ttr = 0).
    """
    if step < 3:
        raise ValueError(
            "OLR requer step >= 3h.\n\n"
            "No step 0 (análise), o campo ttr é zero porque não houve "
            "acumulação temporal.\n\n"
            "Sugestão: use step +3h ou +6h."
        )
    
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)
    
    date_str = cycle_date if cycle_date else datetime.now(timezone.utc).strftime("%Y%m%d")
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"
    
    logger.info("Carregando OLR (ttr → W/m²)")
    
    # ttr é variável de superfície (sfc)
    grib_file = download_ecmwf(
        variables=["ttr"],
        levels=None,
        step=step,
        cycle=cycle,
        output_path=data_dir / f"ecmwf_ttr_{date_str}_{cycle_tag}_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
    )
    
    ds = xr.open_dataset(
        grib_file,
        engine="cfgrib",
        backend_kwargs={"errors": "ignore"}
    )
    
    ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180)
    ds = ds.sortby("longitude")
    
    ttr = ds["ttr"].sel(
        longitude=slice(extent[0], extent[2]),
        latitude=slice(extent[3], extent[1]),
    )
    
    ttr_values = ttr.values
    lons_arr = ttr.longitude.values
    lats_arr = ttr.latitude.values
    
    if smoothing_sigma > 0:
        ttr_values = gaussian_filter(ttr_values, sigma=smoothing_sigma)
    
    # Conversão: -ttr / (step * 3600) = W/m²
    step_seconds = step * 3600.0
    olr = -ttr_values / step_seconds
    
    # Metadados
    valid_time_str = ""
    base_time_str = ""
    try:
        if "valid_time" in ds.coords:
            valid_time_str = np.datetime_as_string(ds.valid_time.values, unit="m")
        if "time" in ds.coords:
            bt = ds.time.values
            bt_dt = np.datetime64(bt, "s").astype("datetime64[s]").astype(datetime)
            base_time_str = f"{bt_dt.strftime('%HZ %d/%m/%Y')}"
    except (KeyError, IndexError, ValueError, TypeError) as e:
        logger.warning("Não foi possível extrair metadados de tempo (OLR): %s", e)

    ds.close()

    logger.info("  OLR range: %.1f – %.1f W/m²", np.nanmin(olr), np.nanmax(olr))
    
    return PLFieldData(
        values=olr,
        lons=lons_arr,
        lats=lats_arr,
        variable="olr",
        level=0,
        unit="W/m²",
        valid_time=valid_time_str,
        base_time=base_time_str,
        step=step,
    )


def load_tcwv(
    extent: list[float],
    step: int = 0,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    smoothing_sigma: float = 1.0,
    source: str = "ecmwf",
    force_download: bool = False,
) -> PLFieldData:
    """
    Baixa e processa Água Precipitável (Total Column Water Vapour).
    
    tcwv é fornecida em kg/m², que é numericamente igual a mm.
    """
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)
    
    date_str = cycle_date if cycle_date else datetime.now(timezone.utc).strftime("%Y%m%d")
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"
    
    logger.info("Carregando Água Precipitável (tcwv)")
    
    grib_file = download_ecmwf(
        variables=["tcwv"],
        levels=None,
        step=step,
        cycle=cycle,
        output_path=data_dir / f"ecmwf_tcwv_{date_str}_{cycle_tag}_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
    )
    
    ds = xr.open_dataset(
        grib_file,
        engine="cfgrib",
        backend_kwargs={"errors": "ignore"}
    )
    
    ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180)
    ds = ds.sortby("longitude")
    
    tcwv = ds["tcwv"].sel(
        longitude=slice(extent[0], extent[2]),
        latitude=slice(extent[3], extent[1]),
    )
    
    values = tcwv.values  # kg/m² = mm
    lons_arr = tcwv.longitude.values
    lats_arr = tcwv.latitude.values
    
    if smoothing_sigma > 0:
        values = gaussian_filter(values, sigma=smoothing_sigma)
    
    valid_time_str = ""
    base_time_str = ""
    try:
        if "valid_time" in ds.coords:
            valid_time_str = np.datetime_as_string(ds.valid_time.values, unit="m")
        if "time" in ds.coords:
            bt = ds.time.values
            bt_dt = np.datetime64(bt, "s").astype("datetime64[s]").astype(datetime)
            base_time_str = f"{bt_dt.strftime('%HZ %d/%m/%Y')}"
    except (KeyError, IndexError, ValueError, TypeError) as e:
        logger.warning("Não foi possível extrair metadados de tempo (TCWV): %s", e)

    ds.close()

    logger.info("  TCWV range: %.1f – %.1f mm", np.nanmin(values), np.nanmax(values))
    
    return PLFieldData(
        values=values,
        lons=lons_arr,
        lats=lats_arr,
        variable="tcwv",
        level=0,
        unit="mm",
        valid_time=valid_time_str,
        base_time=base_time_str,
        step=step,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  GOES-16 — IMAGEM DE SATÉLITE (BANDA 13 IR)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SatelliteData:
    """Container para dados de imagem de satélite."""
    data: np.ndarray       # Temperatura de brilho em °C
    x: np.ndarray          # Coordenadas x em metros (projeção geoestacionária)
    y: np.ndarray          # Coordenadas y em metros (projeção geoestacionária)
    sat_lon: float         # Longitude do satélite
    sat_h: float           # Altura do satélite (m)
    sat_sweep: str         # Eixo de varredura
    time_str: str          # Data/hora da imagem
    filename: str          # Nome do arquivo original


# Paleta IR4AVHRR6 clássica — 256 cores (branco→roxo→cinza→vermelho→amarelo→verde→azul→ciano→cinza→preto)
# Mapeada para temperaturas de brilho: -103°C (índice 0) a +84°C (índice 255)
_IR_AVHRR_COLORS = [
    (255,255,255),(255,255,255),(255,255,255),(255,255,255),(255,255,255),(255,255,255),
    (255,255,255),(255,255,255),(255,255,255),(255,255,255),(255,255,255),(255,255,255),
    (255,255,255),(255,255,255),(255,255,255),(255,255,255),(255,255,255),(255,255,255),
    (255,255,255),(127,0,127),(140,13,135),(153,25,142),(165,38,150),(178,51,157),
    (191,64,165),(204,76,173),(217,89,180),(229,102,188),(242,114,195),(255,127,203),
    (230,230,230),(205,205,205),(179,179,179),(154,154,154),(128,128,128),(103,103,103),
    (77,77,77),(52,52,52),(26,26,26),(0,0,0),(26,0,0),(51,0,0),(77,0,0),(102,0,0),
    (128,0,0),(153,0,0),(179,0,0),(204,0,0),(230,0,0),(255,0,0),(255,26,0),(255,51,0),
    (255,77,0),(255,102,0),(255,128,0),(255,153,0),(255,179,0),(255,204,0),(255,230,0),
    (255,255,0),(230,255,0),(204,255,0),(179,255,0),(153,255,0),(128,255,0),(102,255,0),
    (77,255,0),(51,255,0),(26,255,0),(0,255,0),(0,234,10),(0,213,19),(0,191,29),
    (0,170,38),(0,149,48),(0,128,58),(0,106,67),(0,85,77),(0,64,86),(0,43,96),
    (0,21,105),(0,0,115),(0,0,115),(0,13,122),(0,26,129),(0,38,136),(0,51,143),
    (0,64,150),(0,77,157),(0,89,164),(0,102,171),(0,115,178),(0,128,185),(0,140,192),
    (0,153,199),(0,166,206),(0,179,213),(0,191,220),(0,204,227),(0,217,234),(0,230,241),
    (0,242,248),(0,255,255),(255,255,255),(255,255,255),(255,255,255),(255,255,255),
    (255,255,255),(255,255,255),(255,255,255),(254,254,254),(252,252,252),(249,249,249),
    (247,247,247),(244,244,244),(242,242,242),(239,239,239),(237,237,237),(234,234,234),
    (232,232,232),(229,229,229),(226,226,226),(224,224,224),(221,221,221),(219,219,219),
    (216,216,216),(214,214,214),(211,211,211),(209,209,209),(206,206,206),(203,203,203),
    (201,201,201),(198,198,198),(196,196,196),(193,193,193),(191,191,191),(188,188,188),
    (186,186,186),(183,183,183),(181,181,181),(178,178,178),(175,175,175),(173,173,173),
    (170,170,170),(168,168,168),(165,165,165),(163,163,163),(160,160,160),(158,158,158),
    (155,155,155),(152,152,152),(150,150,150),(147,147,147),(145,145,145),(142,142,142),
    (140,140,140),(137,137,137),(135,135,135),(132,132,132),(130,130,130),(127,127,127),
    (124,124,124),(122,122,122),(119,119,119),(117,117,117),(114,114,114),(112,112,112),
    (109,109,109),(107,107,107),(104,104,104),(101,101,101),(99,99,99),(96,96,96),
    (94,94,94),(91,91,91),(89,89,89),(86,86,86),(84,84,84),(81,81,81),(79,79,79),
    (76,76,76),(73,73,73),(71,71,71),(68,68,68),(66,66,66),(63,63,63),(61,61,61),
    (58,58,58),(56,56,56),(53,53,53),(50,50,50),(48,48,48),(45,45,45),(43,43,43),
    (40,40,40),(38,38,38),(35,35,35),(33,33,33),(30,30,30),(28,28,28),(25,25,25),
    (22,22,22),(20,20,20),(17,17,17),(15,15,15),(12,12,12),(10,10,10),(7,7,7),
    (5,5,5),(2,2,2),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),
    (0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),
    (0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),
    (0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),
    (0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),(0,0,0),
    (0,0,0),(0,0,0),(0,0,0),(0,0,0),
]


def get_ir_colormap():
    """Retorna o colormap IR AVHRR clássico para imagens de satélite."""
    from matplotlib.colors import LinearSegmentedColormap
    
    colors_norm = [(r / 255.0, g / 255.0, b / 255.0) for r, g, b in _IR_AVHRR_COLORS]
    return LinearSegmentedColormap.from_list("ir_avhrr", colors_norm, N=256)


def download_goes16_ir(
    data_dir: Path,
    target_time: datetime | None = None,
    progress_callback=None,
) -> SatelliteData:
    """
    Baixa imagem GOES-East Banda 13 (IR 10.3μm) do AWS S3.
    
    Tenta GOES-19 (operacional desde 2025) e GOES-16 como fallback.
    Dados disponíveis gratuitamente no S3 da NOAA, sem autenticação.
    
    Parameters
    ----------
    data_dir : Path
        Diretório para salvar os arquivos
    target_time : datetime, optional
        Data/hora alvo (UTC). Se None, usa a hora atual.
    
    Returns
    -------
    SatelliteData
        Dados da imagem para plotagem
    """
    import requests
    import re
    
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)
    
    if target_time is None:
        target_time = datetime.now(timezone.utc)
    
    logger.info("Baixando GOES-East Banda 13 (IR)")
    logger.info("  Horário alvo: %s", target_time.strftime('%Y-%m-%d %H:%M UTC'))
    
    # Tenta GOES-19 (atual) e GOES-16 (legado) como fallback
    satellites = [
        ("noaa-goes19", "G19", "GOES-19"),
        ("noaa-goes16", "G16", "GOES-16"),
    ]
    
    band13_files = []
    used_bucket = ""
    used_sat_name = ""
    
    for bucket_name, sat_id, sat_label in satellites:
        bucket_url = f"https://{bucket_name}.s3.amazonaws.com"
        
        logger.info("  Tentando %s (%s)...", sat_label, bucket_name)
        
        # Busca na hora alvo E na hora anterior (para pegar arquivos como XX:50
        # que são mais próximos da hora cheia seguinte)
        for hour_offset in [0, 1]:
            search_time = target_time - timedelta(hours=hour_offset)
            year = search_time.strftime("%Y")
            doy = search_time.strftime("%j")
            hour = search_time.strftime("%H")
            
            prefix = f"ABI-L2-CMIPF/{year}/{doy}/{hour}/"
            list_url = f"{bucket_url}?list-type=2&prefix={prefix}&max-keys=1000"
            
            logger.debug("  Buscando em %s...", prefix)
            
            try:
                resp = requests.get(list_url, timeout=30)
                resp.raise_for_status()
            except (requests.RequestException, OSError) as e:
                logger.warning("  Erro ao listar: %s", e)
                continue
            
            keys = re.findall(r"<Key>([^<]+)</Key>", resp.text)
            
            for key in keys:
                if "C13" in key and sat_id in key and key.endswith(".nc"):
                    band13_files.append(key)
        
        if band13_files:
            used_bucket = bucket_url
            used_sat_name = sat_label
            logger.info("  Total candidatos: %d arquivos", len(band13_files))
            break
        
        # Se não encontrou na hora alvo e anterior, busca até 4h antes
        for hour_offset in range(2, 5):
            search_time = target_time - timedelta(hours=hour_offset)
            year = search_time.strftime("%Y")
            doy = search_time.strftime("%j")
            hour = search_time.strftime("%H")
            
            prefix = f"ABI-L2-CMIPF/{year}/{doy}/{hour}/"
            list_url = f"{bucket_url}?list-type=2&prefix={prefix}&max-keys=1000"
            
            logger.debug("  Buscando fallback em %s...", prefix)
            
            try:
                resp = requests.get(list_url, timeout=30)
                resp.raise_for_status()
            except (requests.RequestException, OSError):
                continue
            
            keys = re.findall(r"<Key>([^<]+)</Key>", resp.text)
            for key in keys:
                if "C13" in key and sat_id in key and key.endswith(".nc"):
                    band13_files.append(key)
            
            if band13_files:
                used_bucket = bucket_url
                used_sat_name = sat_label
                logger.info("  Total candidatos (fallback): %d arquivos", len(band13_files))
                break
        
        if band13_files:
            break
    
    if not band13_files:
        raise FileNotFoundError(
            f"Nenhuma imagem GOES-East Banda 13 encontrada para "
            f"{target_time.strftime('%d/%m/%Y %HZ')} (±4h).\n"
            "Verifique sua conexão com a internet e a data selecionada."
        )
    
    # Seleciona o arquivo mais PRÓXIMO da hora cheia solicitada
    # Nome: ...G19_s20260791250205_e...  → s = start time: YYYYDDDHHMMSS.s
    best_key = None
    best_diff_sec = 999999999
    
    for key in band13_files:
        try:
            fname = key.split("/")[-1]
            s_field = fname.split("_s")[1].split("_")[0]  # ex: 20260791250205
            file_year = int(s_field[0:4])
            file_doy = int(s_field[4:7])
            file_hh = int(s_field[7:9])
            file_mm = int(s_field[9:11])
            
            # Reconstrói datetime do arquivo
            file_dt = datetime(file_year, 1, 1, file_hh, file_mm, 0,
                               tzinfo=timezone.utc) + timedelta(days=file_doy - 1)
            
            diff = abs((file_dt - target_time).total_seconds())
            if diff < best_diff_sec:
                best_diff_sec = diff
                best_key = key
        except (IndexError, ValueError):
            continue
    
    if best_key is None:
        best_key = sorted(band13_files)[0]
    
    best_diff_min = int(best_diff_sec / 60)
    filename = best_key.split("/")[-1]
    local_path = data_dir / filename
    
    logger.info("  Satélite: %s", used_sat_name)
    logger.info("  Arquivo: %s", filename)
    logger.info("  Diferença do alvo: %d minutos", best_diff_min)
    
    # Download se não existe localmente
    if not local_path.exists():
        download_url = f"{used_bucket}/{best_key}"
        logger.info("  Baixando (~25 MB)...")
        if progress_callback:
            progress_callback("status", "Baixando imagem de satélite...")
        
        resp = requests.get(download_url, timeout=120, stream=True)
        resp.raise_for_status()
        
        total_size = int(resp.headers.get("content-length", 0))
        downloaded = 0
        
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    pct = int(downloaded * 100 / total_size)
                    logger.debug("  Download: %d%%", pct)
                    if progress_callback:
                        progress_callback("percent", pct)
        
        logger.info("  Download completo: %s", local_path.name)
        if progress_callback:
            progress_callback("status", "Download completo! Processando...")
    else:
        logger.info("  Usando cache: %s", filename)
        if progress_callback:
            progress_callback("cache", filename)
    
    # Abre com xarray
    logger.info("  Lendo dados...")
    if progress_callback:
        progress_callback("status", "Lendo dados NetCDF...")
    ds = xr.open_dataset(local_path, engine="netcdf4")
    
    # Dados de temperatura de brilho
    cmi = ds["CMI"].values
    
    # Parâmetros de projeção geoestacionária
    proj_info = ds["goes_imager_projection"]
    sat_h = float(proj_info.attrs["perspective_point_height"])
    sat_lon = float(proj_info.attrs["longitude_of_projection_origin"])
    sat_sweep = str(proj_info.attrs["sweep_angle_axis"])
    
    # Coordenadas em radianos → metros
    x = ds["x"].values * sat_h
    y = ds["y"].values * sat_h
    
    # Extrai metadados de tempo
    time_str = ""
    try:
        t_val = ds["t"].values
        time_dt = np.datetime64(t_val, "s").astype("datetime64[s]")
        time_py = time_dt.astype(datetime)
        time_str = time_py.strftime("%Y-%m-%d %H:%M UTC")
    except (KeyError, IndexError, ValueError, TypeError) as e:
        logger.warning("Não foi possível extrair tempo do satélite: %s", e)
        time_str = target_time.strftime("%Y-%m-%d %H:%M UTC")
    
    ds.close()
    
    # Converte K → °C
    data_celsius = cmi - 273.15
    
    logger.info("  Dimensões: %s", data_celsius.shape)
    logger.info("  Range: %.1f a %.1f °C", np.nanmin(data_celsius), np.nanmax(data_celsius))
    logger.info("  Hora da imagem: %s", time_str)
    
    return SatelliteData(
        data=data_celsius,
        x=x,
        y=y,
        sat_lon=sat_lon,
        sat_h=sat_h,
        sat_sweep=sat_sweep,
        time_str=time_str,
        filename=filename,
    )