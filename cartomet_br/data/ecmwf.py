"""
Download e processamento de dados do ECMWF Open Data.

O ECMWF Open Data disponibiliza previsões do modelo IFS:
- Atualização: 4x ao dia (00, 06, 12, 18 UTC)
- Resolução: ~0.25° (HRES) ou 0.5° (ensemble)
- Alcance: até 10 dias
"""

import contextlib
import logging
import tempfile
import threading
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr
from ecmwf.opendata import Client
from scipy.ndimage import gaussian_filter

from cartomet_br.data.olr_timing import resolve_olr_window

warnings.filterwarnings("ignore", message=".*skipping variable.*")

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  MODO SOMENTE-CACHE (abrir projeto NUNCA vai à rede — human-in-the-loop)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Todo download funila por `download_ecmwf`. Uma trava única aqui garante que,
# dentro de `cache_only_mode()`, NENHUM campo — por mais aninhado que seja o
# loader (derivadas, desacumulação) — dispare rede: um cache miss vira
# `CacheMissError` em vez de baixar. Usa `threading.local` para isolar a thread
# da GUI (que faz o replay ao abrir) das threads de download.

_cache_only_state = threading.local()


class CacheMissError(FileNotFoundError):
    """Campo pedido não está no cache e a rede está desabilitada (modo somente-cache)."""


def _cache_only_active() -> bool:
    return getattr(_cache_only_state, "active", False)


@contextlib.contextmanager
def cache_only_mode():
    """Dentro deste contexto, `download_ecmwf` nunca acessa a rede.

    Se o arquivo já está em disco, é reusado; se não, levanta `CacheMissError`
    (em vez de baixar). Usado ao **abrir um projeto**, preservando o
    *human-in-the-loop*: restauramos só o que já está no cache.
    """
    prev = getattr(_cache_only_state, "active", False)
    _cache_only_state.active = True
    try:
        yield
    finally:
        _cache_only_state.active = prev


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
    levtype: str | None = None,
    date: str | None = None,
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
        ) from e

    if output_path is None:
        var_str = "_".join(variables)
        output_path = data_dir / f"ecmwf_{var_str}_f{step:03d}.grib2"
    else:
        output_path = Path(output_path)

    # Modo somente-cache (abrir projeto): reusa se existe, senão FALHA sem rede.
    if _cache_only_active():
        if output_path.exists():
            logger.info("Somente-cache: reutilizando %s", output_path)
            return output_path
        raise CacheMissError(f"Campo fora do cache (modo somente-cache): {output_path.name}")

    # Verifica se arquivo já existe
    if output_path.exists() and not force_download:
        logger.info("Arquivo já existe, reutilizando: %s", output_path)
        return output_path

    # Cria cliente ECMWF
    try:
        client = Client(source=source)
    except Exception as e:
        raise ConnectionError(f"Erro ao conectar ao ECMWF: {e}") from e

    # IFS Cycle 50r1 (13/05/2026): 06Z/18Z migraram de 'scda' para 'oper'. NÃO
    # forçamos stream="oper" aqui — a ecmwf-opendata >= 0.3.29 já infere o stream
    # correto. Forçá-lo quebrava o filtro de alguns params em nível de pressão
    # (vo/d/w), que baixavam o pacote oper inteiro (~132 MB) e falhavam no cfgrib.
    request_params = {
        "step": step,
        "type": "fc",
        "param": variables,
        "target": str(output_path),
    }

    # Se o usuário escolheu uma rodada específica, passa o parâmetro time
    if cycle is not None:
        request_params["time"] = cycle

    # Data específica da rodada (YYYYMMDD). Sem isto, o cliente pega a mais recente
    # daquela hora — o que quebraria a consistência temporal da Técnica B (OLR).
    if date is not None:
        request_params["date"] = date

    if levels is not None:
        request_params["levelist"] = levels

    # levtype EXPLÍCITO p/ filtrar só o campo pedido: 'pl' quando há níveis de
    # pressão, 'sfc' caso contrário (o chamador pode forçar, ex.: LOCZCIT="sfc").
    # Sem isto, params como vo/d/w caem no pacote oper inteiro (~132 MB) de forma
    # intermitente. O nível único vira isobaricInhPa ESCALAR — a leitura abaixo
    # trata isso usando `in ds.dims` (não `.coords`) antes do `.sel`.
    if levtype is None:
        levtype = "pl" if levels is not None else "sfc"
    request_params["levtype"] = levtype

    cycle_str = f"{cycle:02d}Z" if cycle is not None else "auto"
    logger.info("Baixando dados do ECMWF Open Data...")
    logger.info("  Variáveis: %s", variables)
    logger.info("  Níveis: %s", levels)
    logger.info("  Rodada: %s", cycle_str)
    logger.info("  Step: %sh", step)
    logger.info("  Destino: %s", output_path)

    try:
        # client.retrieve() faz SELEÇÃO DE CAMPO por param/levtype (arquivo pequeno e
        # correto, inclui 10u/10v); client.download() baixa o arquivo inteiro (~130 MB)
        # e nem contém o vento 10 m. Usamos retrieve quando levtype é explícito.
        if levtype is not None:
            client.retrieve(**request_params)
        else:
            client.download(**request_params)

        # Verifica se o download foi bem-sucedido
        if not output_path.exists():
            raise FileNotFoundError(f"Download não criou o arquivo esperado: {output_path}")

        if output_path.stat().st_size == 0:
            output_path.unlink()  # Remove arquivo vazio
            raise RuntimeError("Download criou arquivo vazio")

    except Exception as e:
        error_msg = str(e)

        # Trata erros específicos
        if "Cannot establish latest" in error_msg:
            cycle_hint = f" {cycle:02d}Z" if cycle is not None else ""
            raise ValueError(
                f"A rodada{cycle_hint} ainda não está disponível no ECMWF.\n\n"
                f"Isso costuma acontecer com as rodadas 06Z e 18Z, que são publicadas "
                f"mais tarde (a 18Z só fica pronta por volta de 01:30 UTC).\n\n"
                f"O que fazer agora:\n"
                f"  • Use as rodadas 00Z ou 12Z, que já estão disponíveis.\n"
                f'  • Clique em "Verificar Rodadas" para ver as passadas prontas.\n\n'
                f"Se as rodadas 06Z/18Z continuarem falhando mesmo já publicadas, "
                f"pode ser necessário atualizar o CartoMet BR para uma versão mais "
                f"recente (mudança técnica do ECMWF — IFS Cycle 50r1)."
            ) from e
        elif "404" in error_msg:
            raise FileNotFoundError(
                f"Arquivo não encontrado no servidor ECMWF.\nVerifique se o step +{step}h é válido."
            ) from e
        elif "429" in error_msg or "Too Many Requests" in error_msg:
            raise ConnectionError(
                "Servidor ECMWF sobrecarregado (erro 429).\n\n"
                "O servidor limita conexões simultâneas.\n"
                "Aguarde 2-3 minutos e tente novamente.\n\n"
                "Dica: os dados também estão disponíveis via AWS, Azure e Google Cloud."
            ) from e
        elif "SSL" in error_msg or "certificate" in error_msg.lower():
            raise ConnectionError("Erro de conexão SSL. Verifique sua internet.") from e
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
    pnmm: np.ndarray  # Pressão ao nível do mar (hPa)
    thickness: np.ndarray  # Espessura 1000-500 hPa (m)

    # Coordenadas
    lons: np.ndarray
    lats: np.ndarray
    lon2d: np.ndarray
    lat2d: np.ndarray

    # Metadados
    valid_time: str
    extent: list[float]
    base_time: str = ""  # Hora da rodada base (ex: "06Z 17/03/2026")
    step: int = 0  # Step de previsão em horas

    # Máscara de terreno elevado (True onde a PNMM é extrapolação artefactual —
    # Andes/Altiplano). None quando `sp` não está disponível (cache antigo).
    highland_mask: np.ndarray | None = None


# Limiar da máscara orográfica dos centros H/L: onde a REDUÇÃO ao nível do mar
# (msl − sp) excede este valor, a PNMM é extrapolação sob a montanha (ISA:
# ~155 hPa ≈ 1500 m) e cria máximos/mínimos artefactuais no Altiplano/Andes.
# Parâmetro de calibração (mesmo espírito do OCEAN_MASK_THRESHOLD do LOCZCIT).
PNMM_ARTIFACT_DELTA_HPA: float = 155.0


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
    date_str = cycle_date if cycle_date else datetime.now(UTC).strftime("%Y%m%d")
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"

    msl_file = download_ecmwf(
        variables=["msl"],
        step=step,
        cycle=cycle,
        output_path=data_dir / f"ecmwf_msl_{date_str}_{cycle_tag}_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
        date=cycle_date,
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
        date=cycle_date,
    )

    # Leitura com xarray
    logger.info("Carregando dados com xarray...")

    ds_msl = xr.open_dataset(
        msl_file,
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "meanSea"},
            "errors": "ignore",
        },
    )

    ds_gh = xr.open_dataset(
        gh_file,
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "isobaricInhPa"},
            "errors": "ignore",
        },
    )

    # Ajusta longitude de 0-360 para -180 a 180
    ds_msl = ds_msl.assign_coords(longitude=(ds_msl.longitude + 180) % 360 - 180)
    ds_msl = ds_msl.sortby("longitude")

    ds_gh = ds_gh.assign_coords(longitude=(ds_gh.longitude + 180) % 360 - 180)
    ds_gh = ds_gh.sortby("longitude")

    # Seleciona região
    msl = ds_msl["msl"].sel(
        longitude=slice(extent[0], extent[2]),
        latitude=slice(extent[3], extent[1]),  # lat decrescente no ECMWF
    )

    gh = ds_gh["gh"].sel(
        longitude=slice(extent[0], extent[2]), latitude=slice(extent[3], extent[1])
    )

    # Processa campos
    pnmm = msl.values / 100.0  # Pa → hPa
    hght_500 = gh.sel(isobaricInhPa=500).values
    hght_1000 = gh.sel(isobaricInhPa=1000).values
    thickness = hght_500 - hght_1000

    # Máscara orográfica p/ centros H/L: onde (msl − sp) > PNMM_ARTIFACT_DELTA_HPA,
    # a redução ao nível do mar é extrapolação sob a montanha (Andes/Altiplano) e
    # gera H/L falsos. Falha de rede/cache antigo sem `sp` → mask None (detector
    # segue sem filtro — degradação graciosa, não bloqueia a carta).
    highland_mask = None
    try:
        sp_file = download_ecmwf(
            variables=["sp"],
            step=step,
            cycle=cycle,
            output_path=data_dir / f"ecmwf_sp_{date_str}_{cycle_tag}_f{step:03d}.grib2",
            data_dir=data_dir,
            source=source,
            force_download=force_download,
            date=cycle_date,
        )
        ds_sp = xr.open_dataset(
            sp_file,
            engine="cfgrib",
            backend_kwargs={
                "filter_by_keys": {"typeOfLevel": "surface"},
                "errors": "ignore",
            },
        )
        ds_sp = ds_sp.assign_coords(longitude=(ds_sp.longitude + 180) % 360 - 180)
        ds_sp = ds_sp.sortby("longitude")
        sp = ds_sp["sp"].sel(
            longitude=slice(extent[0], extent[2]),
            latitude=slice(extent[3], extent[1]),
        )
        reduction_hpa = (msl.values - sp.values) / 100.0
        highland_mask = reduction_hpa > PNMM_ARTIFACT_DELTA_HPA
        ds_sp.close()
    except Exception as e:  # noqa: BLE001 — opcional por design
        logger.warning("Máscara orográfica indisponível (sp): %s", e)

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
        highland_mask=highland_mask,
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
    {"cycle": 0, "label": "00Z", "avail_hour": 7.5, "max_step": 240},
    {"cycle": 6, "label": "06Z", "avail_hour": 13.5, "max_step": 144},
    {"cycle": 12, "label": "12Z", "avail_hour": 19.5, "max_step": 240},
    {"cycle": 18, "label": "18Z", "avail_hour": 1.5, "max_step": 144},  # dia seguinte
]


PUBLISH_DELAY = 7.5  # horas após a rodada até a publicação no Open Data
N_RECENT_CYCLES = 12  # rodadas listadas = arquivo rotativo do ECMWF (~3 dias)


def estimate_available_cycles() -> dict:
    """
    Estima as rodadas ECMWF mais recentes via JANELA DESLIZANTE (rolling window).

    Caminha para trás em passos de 6 h a partir do horário sinótico atual,
    aplicando o atraso de publicação (~7,5 h), até coletar exatamente as
    ``N_RECENT_CYCLES`` rodadas já publicadas — cruzando a meia-noite (e dias
    anteriores) naturalmente, sem travar no dia corrente.

    Retorna
    -------
    dict com:
        - "available": lista das rodadas disponíveis (mais recente primeiro)
        - "latest": a rodada mais recente estimada
        - "next": próxima rodada esperada e horário estimado
        - "utc_now": hora UTC atual
    """
    now = datetime.now(UTC)
    by_cycle = {c["cycle"]: c for c in CYCLE_SCHEDULE}

    # Ancora no horário sinótico atual (piso para 0/6/12/18)
    anchor = now.replace(minute=0, second=0, microsecond=0)
    anchor -= timedelta(hours=anchor.hour % 6)

    # Recolhe para trás (6/6h) até ter N rodadas publicadas
    available: list[dict[str, Any]] = []
    cand = anchor
    guard = 80  # segurança (~20 dias de ciclos)
    while len(available) < N_RECENT_CYCLES and guard > 0:
        if now >= cand + timedelta(hours=PUBLISH_DELAY):
            info = by_cycle[cand.hour]
            available.append(
                {
                    "cycle": cand.hour,
                    "label": info["label"],
                    "max_step": info["max_step"],
                    "base_datetime": cand,
                    "date_str": cand.strftime("%d/%m/%Y"),
                }
            )
        cand -= timedelta(hours=6)
        guard -= 1

    # Próxima rodada (a primeira ainda NÃO publicada, andando p/ frente)
    next_cycle = None
    fwd = anchor
    for _ in range(8):
        publish_dt = fwd + timedelta(hours=PUBLISH_DELAY)
        if now < publish_dt:
            wait_minutes = int((publish_dt - now).total_seconds() / 60)
            next_cycle = {
                "label": by_cycle[fwd.hour]["label"],
                "estimated_time": publish_dt.strftime("%H:%M UTC"),
                "wait_minutes": wait_minutes,
                "date_str": fwd.strftime("%d/%m"),
            }
            break
        fwd += timedelta(hours=6)

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

# ── Preset "Diagnóstico Baroclínico" (apoio ao traçado MANUAL de frentes) ────
# θe num nível de pressão é subterrâneo/fictício onde a pressão de superfície é
# menor que o nível (+ tolerância): sobre os Andes/Altiplano o campo é lixo e o
# eixo TFP (isolinha TFP=0) desenharia "frentes-fantasma" coladas à cordilheira.
# Máscara → NaN; o np.gradient propaga o NaN e apaga o eixo espúrio na borda.
THETA_E_HIGHLAND_TOL_HPA: float = 25.0  # sp <= (nível + isto) hPa → θe = NaN
# O eixo TFP só é traçado onde |∇θe| tem magnitude sinótica (zona baroclínica).
TFP_GRAD_MIN_K_100KM: float = 3.0

# Horizonte/cadência padrão do meteograma (F6): +0…+72h de 3 em 3 horas. Bounded
# de propósito — a série é baixada step a step (serializado, anti-429) na thread.
METEOGRAM_STEPS = list(range(0, 73, 3))

VARIABLE_REGISTRY: dict[str, dict[str, Any]] = {
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
        "nome": "OLR (desacumulada)",
        "param": ["ttr"],
        "unit_raw": "J/m²",
        "unit_display": "W/m²",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "olr_classic",
        "symmetric": False,
        "category": "radiation",
        "min_step": 3,
        "tem_tecnica": True,  # suporta seletor Direta/Estabilizada (desacumulação)
    },
    "precip": {
        "nome": "Precipitação (3h)",
        "param": ["tp"],
        "unit_raw": "m",
        "unit_display": "mm/3h",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "precip_classic",
        "symmetric": False,
        "category": "accumulated",
        "min_step": 3,
        "tem_tecnica": True,
    },
    "sst_model": {
        "nome": "TSM do modelo (IFS skin)",
        "param": ["skt"],
        "unit_raw": "K",
        "unit_display": "°C",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "sst_classic",
        "symmetric": False,
        "category": "ocean",
    },
    "sst_grad": {
        "nome": "Gradiente de TSM |∇TSM|",
        "param": ["skt"],
        "unit_raw": "K/m",
        "unit_display": "°C/100km",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "YlOrRd",
        "symmetric": False,
        "category": "ocean",
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
    # ── Diagnóstico Baroclínico (θe/∇θe/advecção/eixo TFP) ───────────────────
    # θe = Temperatura Potencial Equivalente (identidade da massa de ar). Todos
    # mascaram o terreno alto (Andes) — ver THETA_E_HIGHLAND_TOL_HPA.
    "theta_e": {
        "nome": "θe (Temp. Pot. Equivalente)",
        "param": ["t", "q"],
        "unit_raw": "K",
        "unit_display": "K",
        "conversion": None,
        "plot_type": "contour",
        "cmap": None,
        "symmetric": False,
        "category": "derived",
    },
    "theta_e_grad": {
        "nome": "Gradiente de θe",
        "param": ["t", "q"],
        "unit_raw": "K/m",
        "unit_display": "°C/100km",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "YlOrRd",
        "symmetric": False,
        "category": "derived",
    },
    "theta_e_adv": {
        "nome": "Advecção de θe",
        "param": ["t", "q", "u", "v"],
        "unit_raw": "K/s",
        "unit_display": "°C/h",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "RdBu_r",
        "symmetric": True,
        "category": "derived",
    },
    # Eixo da Frente (TFP=0 mascarado por |∇θe|): LINHA neutra (guia), não frente
    # classificada. plot_type "axis_line" → render dedicado em map_canvas, s/ colorbar.
    "tfp_axis": {
        "nome": "Eixo da Frente (TFP)",
        "param": ["t", "q"],
        "unit_raw": "K/m²",
        "unit_display": "",
        "conversion": None,
        "plot_type": "axis_line",
        "cmap": None,
        "symmetric": False,
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
    # Extremos de T 2 m: o Open Data (stream oper) publica o extremo da JANELA
    # que antecede o step — 3 h até +144 h (mx2t3/mn2t3) e 6 h além (mx2t6/
    # mn2t6). O nome do parâmetro é resolvido por `t2_extreme_param(step)`;
    # step 0 é inválido (janela degenerada) — daí min_step 3.
    "tmax2m": {
        "nome": "Temp. Máxima 2 m",
        "param": ["mx2t3"],
        "unit_raw": "K",
        "unit_display": "°C",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "YlOrRd",
        "symmetric": False,
        "category": "surface",
        "min_step": 3,
    },
    "tmin2m": {
        "nome": "Temp. Mínima 2 m",
        "param": ["mn2t3"],
        "unit_raw": "K",
        "unit_display": "°C",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "YlGnBu_r",
        "symmetric": False,
        "category": "surface",
        "min_step": 3,
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
    # ── Instabilidade (F9) — campos DERIVADOS no worker (não passam por
    #    load_pl_variable). Aqui só metadados de RENDER: nome/unidade/cmap.
    #    Render contínuo (níveis por percentil) — sem classes/limiares inventados.
    "kindex": {
        "nome": "Índice K (modelo IFS, 13 níveis — aprox.)",
        "param": ["t", "q"],
        "unit_raw": "°C",
        "unit_display": "°C",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "YlOrRd",
        "symmetric": False,
        "category": "instability",
    },
    "li": {
        "nome": "Lifted Index (modelo IFS, 13 níveis — aprox.)",
        "param": ["t", "q", "gh"],
        "unit_raw": "°C",
        "unit_display": "°C",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "RdBu_r",
        "symmetric": True,
        "category": "instability",
    },
    "cape": {
        "nome": "CAPE-SB (modelo IFS, 13 níveis — aprox.)",
        "param": ["t", "q", "gh"],
        "unit_raw": "J/kg",
        "unit_display": "J/kg",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "YlOrRd",
        "symmetric": False,
        "category": "instability",
    },
    "cin": {
        "nome": "CIN-SB (modelo IFS, 13 níveis — aprox.)",
        "param": ["t", "q", "gh"],
        "unit_raw": "J/kg",
        "unit_display": "J/kg",
        "conversion": None,
        "plot_type": "contourf",
        "cmap": "Blues_r",
        "symmetric": False,
        "category": "instability",
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


@dataclass
class ModelProfile:
    """Coluna vertical do modelo (IFS) num ponto — base da pseudo-sondagem (F1).

    Resolução vertical GROSSEIRA (13 níveis de pressão): índices de parcela
    (CAPE/CIN/LFC) saem aproximados — **não é observação**. Arrays alinhados por
    nível, pressão DESCENDENTE (superfície primeiro); níveis abaixo do solo
    (NaN na temperatura) já removidos.
    """

    lon: float
    lat: float
    grid_lon: float
    grid_lat: float
    pressures: np.ndarray  # hPa (descendente)
    t: np.ndarray  # K
    q: np.ndarray  # kg/kg (umidade específica)
    u: np.ndarray  # m/s
    v: np.ndarray  # m/s
    gh: np.ndarray  # m (altura geopotencial)
    valid_time: str = ""
    base_time: str = ""
    cycle: int | None = None
    step: int = 0


def _profile_from_dataset(
    ds: xr.Dataset,
    lon: float,
    lat: float,
    cycle: int | None,
    step: int,
) -> ModelProfile:
    """Extrai a coluna vertical (t,q,u,v,gh) de um Dataset isobárico num ponto.

    PURO (recebe o Dataset já aberto) — testável sem rede. Normaliza longitude,
    seleciona a coluna mais próxima, ordena por pressão descendente e descarta
    níveis abaixo do solo (NaN). ``ValueError`` se sobrarem < 3 níveis válidos.
    """
    ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180)
    ds = ds.sortby("longitude")

    valid_time_str = ""
    base_time_str = ""
    try:
        if "valid_time" in ds.coords:
            valid_time_str = np.datetime_as_string(ds.valid_time.values, unit="m")
        if "time" in ds.coords:
            bt = np.datetime64(ds.time.values, "s").astype("datetime64[s]").astype(datetime)
            base_time_str = bt.strftime("%HZ %d/%m/%Y")
    except (KeyError, IndexError, ValueError, TypeError) as e:
        logger.warning("Metadados de tempo indisponíveis no perfil: %s", e)

    col = ds.sel(latitude=float(lat), longitude=float(lon), method="nearest")
    pressures = np.asarray(col["isobaricInhPa"].values, dtype=float)
    t = np.asarray(col["t"].values, dtype=float)
    q = np.asarray(col["q"].values, dtype=float)
    u = np.asarray(col["u"].values, dtype=float)
    v = np.asarray(col["v"].values, dtype=float)
    gh = np.asarray(col["gh"].values, dtype=float)
    grid_lat = float(np.asarray(col["latitude"].values))
    grid_lon = float(np.asarray(col["longitude"].values))

    # Pressão descendente (superfície primeiro) + remoção de níveis sob o solo.
    order = np.argsort(pressures)[::-1]
    pressures, t, q, u, v, gh = (a[order] for a in (pressures, t, q, u, v, gh))
    keep = np.isfinite(t) & np.isfinite(pressures)
    pressures, t, q, u, v, gh = (a[keep] for a in (pressures, t, q, u, v, gh))

    if pressures.size < 3:
        raise ValueError(
            "Perfil do modelo com níveis válidos insuficientes neste ponto "
            "(coluna possivelmente sobre relevo alto)."
        )

    return ModelProfile(
        lon=float(lon),
        lat=float(lat),
        grid_lon=grid_lon,
        grid_lat=grid_lat,
        pressures=pressures,
        t=t,
        q=q,
        u=u,
        v=v,
        gh=gh,
        valid_time=valid_time_str,
        base_time=base_time_str,
        cycle=cycle,
        step=step,
    )


def load_model_profile(
    lon: float,
    lat: float,
    step: int = 0,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    source: str = "ecmwf",
    force_download: bool = False,
) -> ModelProfile:
    """Baixa a coluna vertical do IFS (t,q,u,v,gh nos 13 níveis) e extrai um ponto.

    Um ÚNICO GRIB (5 params × 13 níveis, cache-first via ``download_ecmwf``).
    Base da pseudo-sondagem do modelo — Skew-T em qualquer ponto, inclusive
    **oceano** e **steps de previsão** (onde não há radiossonda). Resolução
    vertical grosseira — ver :class:`ModelProfile`.
    """
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)

    date_str = cycle_date if cycle_date else datetime.now(UTC).strftime("%Y%m%d")
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"

    grib_file = download_ecmwf(
        variables=["t", "q", "u", "v", "gh"],
        levels=PL_LEVELS,
        step=step,
        cycle=cycle,
        output_path=data_dir / f"ecmwf_profile_{date_str}_{cycle_tag}_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
        levtype="pl",
        date=cycle_date,
    )

    ds = xr.open_dataset(
        grib_file,
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "isobaricInhPa"},
            "errors": "ignore",
        },
    )
    try:
        return _profile_from_dataset(ds, lon, lat, cycle, step)
    finally:
        ds.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  SÉRIE TEMPORAL NUM PONTO (Meteograma — F6)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Previsão PONTUAL do IFS ao longo dos steps numa cidade clicada. T é o ar a
# 1000 hPa (proxy de superfície — o Open Data não expõe 2 m); vento de 10 m;
# precip por INTERVALO (desacumulada entre steps consecutivos). Downloads
# bounded (METEOGRAM_STEPS), cache-first e serializados na thread (anti-429).


@dataclass
class PointTimeseries:
    """Série temporal do modelo (IFS) num ponto — base do meteograma (F6).

    Arrays alinhados por step. Previsão pontual e aproximada (não observação):
    ``t`` é o ar a 1000 hPa (sem 2 m no Open Data), ``wind_*`` é de 10 m, e
    ``precip`` é por intervalo (desacumulada de ``tp`` entre steps).
    """

    lon: float
    lat: float
    grid_lon: float
    grid_lat: float
    steps: np.ndarray  # horas de previsão
    valid_times: list  # rótulos UTC por step
    t: np.ndarray  # °C (ar a 1000 hPa)
    wind_speed: np.ndarray  # m/s (10 m)
    wind_dir: np.ndarray  # graus (de onde sopra)
    precip: np.ndarray  # mm no intervalo
    msl: np.ndarray  # hPa (PNMM)
    tcwv: np.ndarray  # mm (água precipitável)
    base_time: str = ""
    cycle: int | None = None


def _scalar(value) -> float:
    """Primeiro elemento como float — robusto a arrays escalares/size-1 do xarray."""
    return float(np.asarray(value).ravel()[0])


def _time_labels(ds: xr.Dataset) -> tuple[str, str]:
    """(valid_time, base_time) formatados a partir das coords do Dataset (ou vazios)."""
    vt = bt = ""
    try:
        if "valid_time" in ds.coords:
            vt = np.datetime_as_string(ds.valid_time.values, unit="m")
        if "time" in ds.coords:
            bt_dt = np.datetime64(ds.time.values, "s").astype("datetime64[s]").astype(datetime)
            bt = bt_dt.strftime("%HZ %d/%m/%Y")
    except (KeyError, IndexError, ValueError, TypeError):
        pass
    return vt, bt


def _sample_nearest(ds: xr.Dataset, lon: float, lat: float):
    """Coluna/ponto mais próximo, com longitude normalizada p/ −180..180."""
    ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180).sortby("longitude")
    col = ds.sel(latitude=float(lat), longitude=float(lon), method="nearest")
    return col, float(np.asarray(col.longitude.values)), float(np.asarray(col.latitude.values))


def _find_var(datasets: list, *names: str):
    """Acha (dataset, nome) da 1ª variável encontrada entre ``names`` na lista.

    ``cfgrib.open_datasets`` devolve VÁRIOS datasets (um por hipercubo/typeOfLevel)
    para um GRIB de superfície heterogêneo; cada variável pode estar num deles.
    """
    for ds in datasets:
        for n in names:
            if n in ds.data_vars:
                return ds, n
    return None, None


def _sample_surface(datasets: list, lon: float, lat: float) -> dict:
    """Amostra msl/10u/10v/tcwv/tp no ponto, varrendo a lista de datasets de sfc."""
    specs = {
        "msl": ("msl",),
        "u10": ("u10", "10u"),
        "v10": ("v10", "10v"),
        "tcwv": ("tcwv",),
        "tp": ("tp",),
    }
    out: dict = {}
    grid_lon = grid_lat = None
    valid_time = base_time = ""
    for key, names in specs.items():
        ds, vname = _find_var(datasets, *names)
        if ds is None:
            raise ValueError(f"Variável de superfície ausente no GRIB: {key}")
        col, glon, glat = _sample_nearest(ds, lon, lat)
        out[key] = _scalar(col[vname].values)
        if grid_lon is None:
            grid_lon, grid_lat = glon, glat
            valid_time, base_time = _time_labels(ds)
    out["grid_lon"], out["grid_lat"] = grid_lon, grid_lat
    out["valid_time"], out["base_time"] = valid_time, base_time
    return out


def _wind_dir_deg(u, v) -> np.ndarray:
    """Direção meteorológica do vento (graus, "de onde sopra"): atan2(-u,-v).

    Convenção OMM: 0° = vento DE norte, 90° = DE leste, sentido horário.
    Função única para toda conversão u/v → direção (meteograma, rosa dos ventos).
    """
    u = np.asarray(u, dtype=float)
    v = np.asarray(v, dtype=float)
    return np.asarray(np.degrees(np.arctan2(-u, -v)) % 360.0)


def _assemble_point_timeseries(
    lon: float,
    lat: float,
    grid_lon: float,
    grid_lat: float,
    steps,
    valid_times,
    t_k,
    u10,
    v10,
    msl_pa,
    tcwv,
    tp_m,
    base_time: str,
    cycle: int | None,
) -> PointTimeseries:
    """Monta o :class:`PointTimeseries` (PURO): unidades, vento e precip por intervalo."""
    steps_arr = np.asarray(steps, dtype=float)
    t_c = np.asarray(t_k, dtype=float) - 273.15
    u = np.asarray(u10, dtype=float)
    v = np.asarray(v10, dtype=float)
    wind_speed = np.hypot(u, v)
    wind_dir = _wind_dir_deg(u, v)
    msl_hpa = np.asarray(msl_pa, dtype=float) / 100.0
    tcwv_mm = np.asarray(tcwv, dtype=float)

    # Precip por intervalo: tp é acumulado desde t=0 da rodada → diferença entre
    # steps consecutivos (monotônica ⇒ ≥ 0; clip é rede de segurança). O 1º
    # intervalo recebe o próprio tp[0] (≈ 0 na análise).
    tp = np.asarray(tp_m, dtype=float)
    precip = np.empty_like(tp)
    if tp.size:
        precip[0] = max(float(tp[0]), 0.0)
        if tp.size > 1:
            precip[1:] = np.clip(np.diff(tp), 0.0, None)
    precip_mm = precip * 1000.0

    return PointTimeseries(
        lon=float(lon),
        lat=float(lat),
        grid_lon=float(grid_lon),
        grid_lat=float(grid_lat),
        steps=steps_arr,
        valid_times=list(valid_times),
        t=t_c,
        wind_speed=wind_speed,
        wind_dir=wind_dir,
        precip=precip_mm,
        msl=msl_hpa,
        tcwv=tcwv_mm,
        base_time=base_time,
        cycle=cycle,
    )


def load_point_timeseries(
    lon: float,
    lat: float,
    steps: list[int] | None = None,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    source: str = "ecmwf",
    force_download: bool = False,
    progress_cb=None,
) -> PointTimeseries:
    """Série temporal do IFS num ponto (T/vento/precip/PNMM/tcwv) — meteograma (F6).

    Para cada step (``steps`` ou ``METEOGRAM_STEPS``) baixa DOIS GRIBs cache-first:
    um pacote de superfície (msl, 10u, 10v, tcwv, tp) e ``t``@1000 hPa. O laço roda
    na thread do worker — uma requisição por vez (serializado, anti-429). Steps
    indisponíveis são pulados com aviso (sem matriz zerada). Tudo em ``data_dir``
    (que o chamador aponta para ``config.grib_dir``).
    """
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)
    steps = list(steps) if steps is not None else list(METEOGRAM_STEPS)
    date_str = cycle_date if cycle_date else datetime.now(UTC).strftime("%Y%m%d")
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"

    import cfgrib

    rec_steps, rec_vt = [], []
    rec_t, rec_u, rec_v, rec_msl, rec_tcwv, rec_tp = [], [], [], [], [], []
    grid_lon = grid_lat = None
    base_time = ""

    for s in steps:
        if progress_cb:
            progress_cb(f"Baixando +{s}h…")
        try:
            sfc_path = download_ecmwf(
                variables=["msl", "10u", "10v", "tcwv", "tp"],
                levels=None,
                step=s,
                cycle=cycle,
                date=cycle_date,
                output_path=data_dir / f"ecmwf_meteo_sfc_{date_str}_{cycle_tag}_f{s:03d}.grib2",
                data_dir=data_dir,
                source=source,
                force_download=force_download,
                levtype="sfc",
            )
            datasets = cfgrib.open_datasets(str(sfc_path), backend_kwargs={"errors": "ignore"})
            try:
                sample = _sample_surface(datasets, lon, lat)
            finally:
                for d in datasets:
                    d.close()

            t1000_path = download_ecmwf(
                variables=["t"],
                levels=[1000],
                step=s,
                cycle=cycle,
                date=cycle_date,
                output_path=data_dir / f"ecmwf_meteo_t1000_{date_str}_{cycle_tag}_f{s:03d}.grib2",
                data_dir=data_dir,
                source=source,
                force_download=force_download,
                levtype="pl",
            )
            ds_t = xr.open_dataset(
                t1000_path,
                engine="cfgrib",
                backend_kwargs={
                    "filter_by_keys": {"typeOfLevel": "isobaricInhPa"},
                    "errors": "ignore",
                },
            )
            try:
                col_t, _, _ = _sample_nearest(ds_t, lon, lat)
                t_val = _scalar(col_t["t"].values)
            finally:
                ds_t.close()
        except Exception as e:  # noqa: BLE001 — step ausente não derruba a série
            logger.warning("Step +%sh indisponível para o meteograma: %s", s, e)
            continue

        rec_steps.append(s)
        rec_vt.append(sample["valid_time"])
        rec_t.append(t_val)
        rec_u.append(sample["u10"])
        rec_v.append(sample["v10"])
        rec_msl.append(sample["msl"])
        rec_tcwv.append(sample["tcwv"])
        rec_tp.append(sample["tp"])
        if grid_lon is None:
            grid_lon, grid_lat = sample["grid_lon"], sample["grid_lat"]
            base_time = sample["base_time"]

    if not rec_steps:
        raise ValueError(
            "Nenhum step disponível para o meteograma (rede/cache).\n"
            "Verifique a rodada/conexão e tente novamente."
        )

    # Invariante: rec_steps não-vazio ⇒ o grid foi amostrado (grid_lon/lat setados).
    assert grid_lon is not None and grid_lat is not None

    return _assemble_point_timeseries(
        lon,
        lat,
        grid_lon,
        grid_lat,
        rec_steps,
        rec_vt,
        rec_t,
        rec_u,
        rec_v,
        rec_msl,
        rec_tcwv,
        rec_tp,
        base_time,
        cycle,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  SÉRIE DE VENTO POR NÍVEL DE PRESSÃO NUM PONTO (Rosa dos Ventos — Fase 3)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Vento u/v num nível isobárico ao longo dos steps da rodada, para a rosa dos
# ventos em altitude. Reusa load_model_profile por step (MESMO GRIB/cache da
# pseudo-sondagem Skew-T — clicar rosa a 850 hPa onde já houve sondagem é
# instantâneo). Downloads bounded, cache-first e serializados (anti-429).

# Tolerância p/ casar o nível pedido com a coluna disponível: sobre relevo alto
# o perfil descarta níveis subterrâneos (NaN) e o mais próximo pode ficar longe
# do pedido (ex.: 1000 hPa sobre os Andes). Fora da tolerância → step pulado
# com aviso, nunca um vento de outro nível "fingindo" ser o pedido. Parâmetro
# calibrável, no espírito de THETA_E_HIGHLAND_TOL_HPA.
LEVEL_MATCH_TOLERANCE_HPA: float = 100.0


@dataclass
class PointLevelWindSeries:
    """Série de vento num nível de pressão num ponto — rosa dos ventos em altitude.

    Arrays alinhados por step. Previsão pontual do IFS (não observação); direção
    meteorológica ("de onde sopra"). Só o que a rosa precisa — sem os campos de
    superfície do :class:`PointTimeseries` (msl/tcwv/precip não têm sentido aqui).
    """

    lon: float
    lat: float
    grid_lon: float
    grid_lat: float
    level_hpa: float
    steps: np.ndarray  # horas de previsão
    wind_speed: np.ndarray  # m/s
    wind_dir: np.ndarray  # graus (de onde sopra)
    base_time: str = ""
    cycle: int | None = None


def _nearest_level_wind(
    profile: ModelProfile,
    level_hpa: float,
    tolerance_hpa: float = LEVEL_MATCH_TOLERANCE_HPA,
) -> tuple[float, float] | None:
    """(speed, dir) do nível de ``profile`` mais próximo de ``level_hpa`` (PURO).

    ``None`` se o nível mais próximo estiver além de ``tolerance_hpa`` (coluna
    sobre relevo alto que descartou os níveis baixos) ou se u/v forem NaN ali.
    """
    pressures = np.asarray(profile.pressures, dtype=float)
    if pressures.size == 0:
        return None
    idx = int(np.argmin(np.abs(pressures - float(level_hpa))))
    if abs(float(pressures[idx]) - float(level_hpa)) > tolerance_hpa:
        return None
    u = float(profile.u[idx])
    v = float(profile.v[idx])
    if not (np.isfinite(u) and np.isfinite(v)):
        return None
    speed = float(np.hypot(u, v))
    direction = float(_wind_dir_deg(u, v))
    return speed, direction


def load_point_level_wind_timeseries(
    lon: float,
    lat: float,
    level_hpa: float,
    steps: list[int] | None = None,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    source: str = "ecmwf",
    force_download: bool = False,
    progress_cb=None,
) -> PointLevelWindSeries:
    """Série de vento num nível isobárico num ponto — rosa dos ventos (Fase 3).

    Para cada step (``steps`` ou ``METEOGRAM_STEPS``) reusa ``load_model_profile``
    (um GRIB de perfil por step, cache-first — o MESMO arquivo do Skew-T) e
    extrai u/v do nível mais próximo de ``level_hpa`` via ``_nearest_level_wind``.
    O laço roda na thread do worker, uma requisição por vez (anti-429). Steps
    indisponíveis ou fora da tolerância de nível são pulados com aviso.
    """
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)
    steps = list(steps) if steps is not None else list(METEOGRAM_STEPS)

    rec_steps: list[float] = []
    rec_speed: list[float] = []
    rec_dir: list[float] = []
    grid_lon = grid_lat = None
    base_time = ""

    for s in steps:
        if progress_cb:
            progress_cb(f"Baixando +{s}h…")
        try:
            profile = load_model_profile(
                lon,
                lat,
                step=s,
                cycle=cycle,
                cycle_date=cycle_date,
                data_dir=data_dir,
                source=source,
                force_download=force_download,
            )
        except Exception as e:  # noqa: BLE001 — step ausente não derruba a série
            logger.warning("Step +%sh indisponível para a rosa em nível: %s", s, e)
            continue

        wind = _nearest_level_wind(profile, level_hpa)
        if wind is None:
            logger.warning(
                "Step +%sh sem nível ~%g hPa neste ponto (relevo alto?) — pulado.",
                s,
                level_hpa,
            )
            continue

        rec_steps.append(float(s))
        rec_speed.append(wind[0])
        rec_dir.append(wind[1])
        if grid_lon is None:
            grid_lon, grid_lat = profile.grid_lon, profile.grid_lat
            base_time = profile.base_time

    if not rec_steps:
        raise ValueError(
            f"Nenhum step com vento válido a {level_hpa:g} hPa neste ponto "
            "(rede/cache ou coluna sobre relevo alto).\n"
            "Verifique a rodada/conexão ou tente outro nível."
        )

    # Invariante: rec_steps não-vazio ⇒ o grid foi amostrado (grid_lon/lat setados).
    assert grid_lon is not None and grid_lat is not None

    return PointLevelWindSeries(
        lon=float(lon),
        lat=float(lat),
        grid_lon=float(grid_lon),
        grid_lat=float(grid_lat),
        level_hpa=float(level_hpa),
        steps=np.asarray(rec_steps, dtype=float),
        wind_speed=np.asarray(rec_speed, dtype=float),
        wind_dir=np.asarray(rec_dir, dtype=float),
        base_time=base_time,
        cycle=cycle,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  CORTE VERTICAL (Cross-section A→B — F4)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Seção distância × pressão ao longo de uma reta A→B: t, w (omega), q e vento nos
# 13 níveis. Um ÚNICO GRIB multinível, amostrado ao longo do caminho por
# interpolação linear (xarray.interp). Resolução vertical GROSSEIRA (13 níveis).


@dataclass
class CrossSection:
    """Corte vertical do modelo (IFS) ao longo de uma reta A→B — base do F4.

    Campos 2D ``(n_níveis, n_pontos)`` com pressão DESCENDENTE; ``distances_km``
    é a distância acumulada (haversine) ao longo do caminho. Vertical grosseira
    (13 níveis) — produto do modelo, não observação.
    """

    distances_km: np.ndarray  # (n_pontos,)
    pressures: np.ndarray  # (n_níveis,) descendente
    t: np.ndarray  # °C
    w: np.ndarray  # Pa/s (omega; <0 = ascendência)
    q: np.ndarray  # g/kg
    u: np.ndarray  # m/s
    v: np.ndarray  # m/s
    lons: np.ndarray  # (n_pontos,)
    lats: np.ndarray  # (n_pontos,)
    valid_time: str = ""
    base_time: str = ""
    step: int = 0


def _haversine_cumulative(lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Distância acumulada (km) ao longo do caminho lon/lat (haversine)."""
    radius = 6371.0
    lon_r = np.radians(np.asarray(lons, dtype=float))
    lat_r = np.radians(np.asarray(lats, dtype=float))
    dlon = np.diff(lon_r)
    dlat = np.diff(lat_r)
    a = np.sin(dlat / 2) ** 2 + np.cos(lat_r[:-1]) * np.cos(lat_r[1:]) * np.sin(dlon / 2) ** 2
    seg = 2 * radius * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    return np.concatenate([[0.0], np.cumsum(seg)])


def _cross_section_from_dataset(
    ds: xr.Dataset,
    lon_a: float,
    lat_a: float,
    lon_b: float,
    lat_b: float,
    step: int,
    n_points: int = 80,
) -> CrossSection:
    """Amostra a seção A→B de um Dataset isobárico (PURO — testável sem rede).

    Normaliza longitude, interpola linearmente ``n_points`` ao longo da reta
    (xarray.interp), ordena por pressão descendente e converte unidades de
    exibição (t→°C, q→g/kg). ``w`` (omega) fica em Pa/s.
    """
    # interp exige coordenadas monotônicas crescentes; o IFS entrega latitude
    # decrescente → ordenamos ambas antes de amostrar o caminho.
    ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180)
    ds = ds.sortby("longitude").sortby("latitude")
    lons = np.linspace(float(lon_a), float(lon_b), n_points)
    lats = np.linspace(float(lat_a), float(lat_b), n_points)
    path = ds.interp(
        longitude=xr.DataArray(lons, dims="path"),
        latitude=xr.DataArray(lats, dims="path"),
    )

    pressures = np.asarray(path["isobaricInhPa"].values, dtype=float)
    order = np.argsort(pressures)[::-1]
    pressures = pressures[order]

    def _grab(name: str) -> np.ndarray:
        # transpose explícito (nível, caminho) — não assume a ordem de eixos do
        # cfgrib, mesmo padrão defensivo de _instability_from_dataset.
        arr = np.asarray(path[name].transpose("isobaricInhPa", "path").values, dtype=float)
        return arr[order, :]

    t = _grab("t") - 273.15
    w = _grab("w")
    q = _grab("q") * 1000.0
    u = _grab("u")
    v = _grab("v")
    distances = _haversine_cumulative(lons, lats)
    vt, bt = _time_labels(ds)

    return CrossSection(
        distances_km=distances,
        pressures=pressures,
        t=t,
        w=w,
        q=q,
        u=u,
        v=v,
        lons=lons,
        lats=lats,
        valid_time=vt,
        base_time=bt,
        step=step,
    )


def load_cross_section(
    lon_a: float,
    lat_a: float,
    lon_b: float,
    lat_b: float,
    step: int = 0,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    n_points: int = 80,
    source: str = "ecmwf",
    force_download: bool = False,
) -> CrossSection:
    """Corte vertical A→B do IFS (t,w,q,vento nos 13 níveis) — F4.

    Um ÚNICO GRIB multinível (6 params × 13 níveis, cache-first), amostrado ao
    longo da reta A→B. Vertical grosseira (13 níveis) — ver :class:`CrossSection`.
    Tudo em ``data_dir`` (que o chamador aponta para ``config.grib_dir``).
    """
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)

    date_str = cycle_date if cycle_date else datetime.now(UTC).strftime("%Y%m%d")
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"

    grib_file = download_ecmwf(
        variables=["t", "w", "q", "u", "v", "gh"],
        levels=PL_LEVELS,
        step=step,
        cycle=cycle,
        output_path=data_dir / f"ecmwf_xsec_{date_str}_{cycle_tag}_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
        levtype="pl",
        date=cycle_date,
    )

    ds = xr.open_dataset(
        grib_file,
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "isobaricInhPa"},
            "errors": "ignore",
        },
    )
    try:
        return _cross_section_from_dataset(ds, lon_a, lat_a, lon_b, lat_b, step, n_points)
    finally:
        ds.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  CAMPOS DE INSTABILIDADE (CAPE/CIN/LI/K) — camadas de grade cheia (F9)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Derivados do perfil (t,q,gh nos 13 níveis). K-index é vetorizado na grade
# NATIVA (barato). LI/CAPE/CIN exigem ascensão de parcela por célula (caro) →
# grade ENGROSSADA por stride e interpolada de volta. Render contínuo — SEM
# classes/limiares inventados (não há doc de metodologia p/ estes índices).


def _dewpoint_2d(pressure_hpa: float, q2d: np.ndarray):
    """Td (°C, ndarray) de um nível a partir da umidade específica 2D (q em kg/kg)."""
    import metpy.calc as mpcalc
    from metpy.units import units

    mixing = mpcalc.mixing_ratio_from_specific_humidity(q2d * units("kg/kg"))
    e = mpcalc.vapor_pressure(pressure_hpa * units.hPa, mixing)
    return mpcalc.dewpoint(e).to("degC").magnitude


# ── Campos do preset "Diagnóstico Baroclínico" (helpers PUROS/testáveis) ─────
# θe → ∇θe → advecção → TFP. Operam sobre arrays já em metros (dy escalar, dx_2d
# variando com a latitude) — sem rede, testáveis com campos sintéticos.


def _theta_e_2d(pressure_hpa: float, t2d_k: np.ndarray, q2d: np.ndarray) -> np.ndarray:
    """θe (K, ndarray) de um nível: T (K) + q (kg/kg) via MetPy (Td = _dewpoint_2d)."""
    import metpy.calc as mpcalc
    from metpy.units import units

    td_c = _dewpoint_2d(pressure_hpa, q2d)
    theta_e = mpcalc.equivalent_potential_temperature(
        pressure_hpa * units.hPa, t2d_k * units.K, td_c * units.degC
    )
    return np.asarray(theta_e.to("K").magnitude)


def _theta_e_gradient(theta_e: np.ndarray, dy: float, dx_2d: np.ndarray) -> np.ndarray:
    """|∇θe| (K/m). NaN de entrada (Andes) propaga para a borda via np.gradient."""
    dtedy = np.gradient(theta_e, dy, axis=0)
    dtedx = np.gradient(theta_e, axis=1) / dx_2d
    return np.asarray(np.hypot(dtedx, dtedy))


def _theta_e_advection(
    theta_e: np.ndarray, u: np.ndarray, v: np.ndarray, dy: float, dx_2d: np.ndarray
) -> np.ndarray:
    """Advecção −V·∇θe (K/s): positivo = advecção quente (setor quente da onda)."""
    dtedy = np.gradient(theta_e, dy, axis=0)
    dtedx = np.gradient(theta_e, axis=1) / dx_2d
    return np.asarray(-(u * dtedx + v * dtedy))


def _tfp_field(
    theta_e: np.ndarray, dy: float, dx_2d: np.ndarray, grad_min_k_100km: float
) -> np.ndarray:
    """Thermal Front Parameter (Hewson 1998): TFP = −∇|∇θe|·(∇θe/|∇θe|).

    A isolinha TFP=0 (topo da rampa baroclínica) é o EIXO da frente. Retorna NaN
    onde |∇θe| < ``grad_min`` (K/100km) — o eixo só existe em zona baroclínica
    real, sem "frentes-fantasma" em gradiente fraco.
    """
    dtedy = np.gradient(theta_e, dy, axis=0)
    dtedx = np.gradient(theta_e, axis=1) / dx_2d
    mag = np.hypot(dtedx, dtedy)  # K/m
    dmagdy = np.gradient(mag, dy, axis=0)
    dmagdx = np.gradient(mag, axis=1) / dx_2d
    with np.errstate(invalid="ignore", divide="ignore"):
        tfp = -(dmagdx * dtedx + dmagdy * dtedy) / np.where(mag > 0, mag, np.nan)
    return np.where(mag * 1e5 >= grad_min_k_100km, tfp, np.nan)


def _instability_from_dataset(
    ds: xr.Dataset,
    extent: list[float],
    indices: tuple,
    stride: int,
    step: int,
    progress_cb=None,
) -> dict:
    """Calcula os campos de instabilidade de um Dataset isobárico (PURO/testável).

    K-index na grade nativa (vetorizado); LI/CAPE/CIN na grade engrossada
    (``stride``) com MetPy por coluna, interpolados de volta à grade do extent.
    Devolve ``{nome: PLFieldData}`` só para os ``indices`` pedidos.
    """
    import metpy.calc as mpcalc
    from metpy.units import units

    ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180)
    ds = ds.sortby("longitude").sortby("latitude")
    ds = ds.sel(longitude=slice(extent[0], extent[2]), latitude=slice(extent[1], extent[3]))

    pressures = np.asarray(ds["isobaricInhPa"].values, dtype=float)
    order = np.argsort(pressures)[::-1]
    pressures = pressures[order]
    t3d = np.asarray(
        ds["t"].transpose("isobaricInhPa", "latitude", "longitude").values, dtype=float
    )[order]
    q3d = np.asarray(
        ds["q"].transpose("isobaricInhPa", "latitude", "longitude").values, dtype=float
    )[order]
    lats = np.asarray(ds["latitude"].values, dtype=float)
    lons = np.asarray(ds["longitude"].values, dtype=float)
    vt, bt = _time_labels(ds)

    out: dict = {}

    def _make(name: str, values: np.ndarray, unit: str) -> None:
        # Campo 100% NaN (ex.: ascensão de parcela falhou em toda coluna) é
        # OMITIDO: o render por percentil (np.nanpercentile) viraria NaN e o
        # contourf quebraria. Se todos saírem NaN, o worker avisa (dict vazio).
        if not np.any(np.isfinite(values)):
            logger.warning("Campo de instabilidade '%s' totalmente indefinido — omitido.", name)
            return
        out[name] = PLFieldData(
            values=values,
            lons=lons,
            lats=lats,
            variable=name,
            level=0,
            unit=unit,
            valid_time=vt,
            base_time=bt,
            step=step,
        )

    # ── K-index (nativo, vetorizado) ─────────────────────────────────────────
    if "kindex" in indices:
        if progress_cb:
            progress_cb("Calculando Índice K (nativo)…")
        i850 = int(np.argmin(np.abs(pressures - 850)))
        i700 = int(np.argmin(np.abs(pressures - 700)))
        i500 = int(np.argmin(np.abs(pressures - 500)))
        td850 = _dewpoint_2d(float(pressures[i850]), q3d[i850])
        td700 = _dewpoint_2d(float(pressures[i700]), q3d[i700])
        t850 = t3d[i850] - 273.15
        t700 = t3d[i700] - 273.15
        t500 = t3d[i500] - 273.15
        kindex = (t850 - t500) + td850 - (t700 - td700)
        _make("kindex", kindex, "°C")

    # ── LI/CAPE/CIN (engrossado por stride + interp de volta) ────────────────
    want_parcel = [n for n in ("li", "cape", "cin") if n in indices]
    if want_parcel:
        # Inclui o último índice p/ cobrir a borda (evita extrapolação no interp).
        ci = np.unique(np.append(np.arange(0, lats.size, stride), lats.size - 1))
        cj = np.unique(np.append(np.arange(0, lons.size, stride), lons.size - 1))
        cape_c = np.full((ci.size, cj.size), np.nan)
        cin_c = np.full((ci.size, cj.size), np.nan)
        li_c = np.full((ci.size, cj.size), np.nan)
        ps = pressures * units.hPa
        total = ci.size
        for a, ii in enumerate(ci):
            if progress_cb and total > 1:
                progress_cb(f"Ascensão de parcela (CAPE/LI)… {int(100 * a / total)}%")
            for b, jj in enumerate(cj):
                tcol = t3d[:, ii, jj]
                qcol = q3d[:, ii, jj]
                valid = np.isfinite(tcol) & np.isfinite(qcol)
                if int(valid.sum()) < 3:
                    continue
                p_c = ps[valid]
                temp = (tcol[valid] * units.kelvin).to("degC")
                mixing = mpcalc.mixing_ratio_from_specific_humidity(qcol[valid] * units("kg/kg"))
                dew = mpcalc.dewpoint(mpcalc.vapor_pressure(p_c, mixing))
                try:
                    if "cape" in indices or "cin" in indices:
                        cape, cin = mpcalc.surface_based_cape_cin(p_c, temp, dew)
                        cape_c[a, b] = float(cape.to("J/kg").magnitude)
                        cin_c[a, b] = float(cin.to("J/kg").magnitude)
                    if "li" in indices:
                        parcel = mpcalc.parcel_profile(p_c, temp[0], dew[0]).to("degC")
                        li = mpcalc.lifted_index(p_c, temp, parcel)
                        li_c[a, b] = float(np.atleast_1d(li.magnitude)[0])
                except Exception as e:  # noqa: BLE001 — coluna ruim não derruba o campo
                    logger.debug("Instabilidade falhou numa coluna: %s", e)

        clat = lats[ci]
        clon = lons[cj]

        def _interp(coarse: np.ndarray) -> np.ndarray:
            da = xr.DataArray(
                coarse, dims=("latitude", "longitude"), coords={"latitude": clat, "longitude": clon}
            )
            return da.interp(latitude=lats, longitude=lons).values

        if "cape" in indices:
            _make("cape", _interp(cape_c), "J/kg")
        if "cin" in indices:
            _make("cin", _interp(cin_c), "J/kg")
        if "li" in indices:
            _make("li", _interp(li_c), "°C")

    return out


def compute_instability_fields(
    extent: list[float],
    step: int = 0,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    indices=("kindex", "li", "cape", "cin"),
    coarsen_stride: int = 4,
    source: str = "ecmwf",
    force_download: bool = False,
    progress_cb=None,
) -> dict:
    """Campos de instabilidade do IFS (CAPE/CIN/LI/K) sobre o extent — F9.

    Um ÚNICO GRIB (t,q,gh × 13 níveis, cache-first). Roda no worker (CPU pesada
    no CAPE/LI) com ``progress_cb``. Devolve ``{nome: PLFieldData}`` pronto para
    ``MapCanvas.add_pl_layer``. Tudo em ``data_dir`` (config.grib_dir).
    """
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)
    date_str = cycle_date if cycle_date else datetime.now(UTC).strftime("%Y%m%d")
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"

    if progress_cb:
        progress_cb("Baixando coluna do modelo (t, q, gh)…")
    grib_file = download_ecmwf(
        variables=["t", "q", "gh"],
        levels=PL_LEVELS,
        step=step,
        cycle=cycle,
        output_path=data_dir / f"ecmwf_instab_{date_str}_{cycle_tag}_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
        levtype="pl",
        date=cycle_date,
    )
    ds = xr.open_dataset(
        grib_file,
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "isobaricInhPa"},
            "errors": "ignore",
        },
    )
    try:
        return _instability_from_dataset(
            ds,
            extent,
            tuple(indices),
            int(coarsen_stride),
            step,
            progress_cb,
        )
    finally:
        ds.close()


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

    date_str = cycle_date if cycle_date else datetime.now(UTC).strftime("%Y%m%d")
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"
    param_str = "_".join(params)

    logger.info("Carregando %s em %s hPa", var_info["nome"], level)

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
        output_path=data_dir
        / f"ecmwf_{param_str}_{date_str}_{cycle_tag}_{level}hPa_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
        date=cycle_date,
    )

    # Leitura com xarray
    ds = xr.open_dataset(
        grib_file,
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "isobaricInhPa"},
            "errors": "ignore",
        },
    )

    # Ajusta longitude de 0-360 para -180 a 180
    ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180)
    ds = ds.sortby("longitude")

    # Seleciona região e nível
    sel_kwargs: dict[str, Any] = {
        "longitude": slice(extent[0], extent[2]),
        "latitude": slice(extent[3], extent[1]),
    }
    if "isobaricInhPa" in ds.dims:  # só seleciona se for DIMENSÃO (nível único = escalar)
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
    date_str = cycle_date if cycle_date else datetime.now(UTC).strftime("%Y%m%d")
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
        date=cycle_date,
    )

    ds_t = xr.open_dataset(
        t_file,
        engine="cfgrib",
        backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}, "errors": "ignore"},
    )
    ds_t = ds_t.assign_coords(longitude=(ds_t.longitude + 180) % 360 - 180)
    ds_t = ds_t.sortby("longitude")

    sel_kw: dict[str, Any] = {
        "longitude": slice(extent[0], extent[2]),
        "latitude": slice(extent[3], extent[1]),
    }
    if "isobaricInhPa" in ds_t.dims:
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
    dTdy = np.gradient(t_data, dy, axis=0)  # ∂T/∂y
    dTdx = np.gradient(t_data, axis=1) / dx_2d  # ∂T/∂x

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
            date=cycle_date,
        )

        ds_uv = xr.open_dataset(
            uv_file,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}, "errors": "ignore"},
        )
        ds_uv = ds_uv.assign_coords(longitude=(ds_uv.longitude + 180) % 360 - 180)
        ds_uv = ds_uv.sortby("longitude")

        sel_kw_uv: dict[str, Any] = {
            "longitude": slice(extent[0], extent[2]),
            "latitude": slice(extent[3], extent[1]),
        }
        if "isobaricInhPa" in ds_uv.dims:
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
            date=cycle_date,
        )

        ds_uv = xr.open_dataset(
            uv_file,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}, "errors": "ignore"},
        )
        ds_uv = ds_uv.assign_coords(longitude=(ds_uv.longitude + 180) % 360 - 180)
        ds_uv = ds_uv.sortby("longitude")

        sel_kw_uv = {
            "longitude": slice(extent[0], extent[2]),
            "latitude": slice(extent[3], extent[1]),
        }
        if "isobaricInhPa" in ds_uv.dims:
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
            dTdx**2 * dudx + dTdy**2 * dvdy + dTdx * dTdy * (dvdx + dudy)
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
            date=cycle_date,
        )

        ds_q = xr.open_dataset(
            q_file,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}, "errors": "ignore"},
        )
        ds_q = ds_q.assign_coords(longitude=(ds_q.longitude + 180) % 360 - 180)
        ds_q = ds_q.sortby("longitude")

        sel_kw_q: dict[str, Any] = {
            "longitude": slice(extent[0], extent[2]),
            "latitude": slice(extent[3], extent[1]),
        }
        if "isobaricInhPa" in ds_q.dims:
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
            date=cycle_date,
        )

        ds_uv = xr.open_dataset(
            uv_file,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}, "errors": "ignore"},
        )
        ds_uv = ds_uv.assign_coords(longitude=(ds_uv.longitude + 180) % 360 - 180)
        ds_uv = ds_uv.sortby("longitude")

        sel_kw_uv = {
            "longitude": slice(extent[0], extent[2]),
            "latitude": slice(extent[3], extent[1]),
        }
        if "isobaricInhPa" in ds_uv.dims:
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

    elif variable_key in ("theta_e", "theta_e_grad", "theta_e_adv", "tfp_axis"):
        # ─── Diagnóstico Baroclínico: θe → ∇θe / advecção / eixo TFP ───
        # t_data (já lido/suavizado no preâmbulo) + q; θe mascarado sobre o
        # terreno alto (Andes) e suavizado ciente-de-NaN antes de derivar.
        def _open_pl(names: list[str], tag: str) -> dict[str, np.ndarray]:
            f = download_ecmwf(
                variables=names,
                levels=[level],
                step=step,
                cycle=cycle,
                output_path=data_dir
                / f"ecmwf_{tag}_{date_str}_{cycle_tag}_{level}hPa_f{step:03d}.grib2",
                data_dir=data_dir,
                source=source,
                date=cycle_date,
            )
            ds = xr.open_dataset(
                f,
                engine="cfgrib",
                backend_kwargs={
                    "filter_by_keys": {"typeOfLevel": "isobaricInhPa"},
                    "errors": "ignore",
                },
            )
            ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180).sortby("longitude")
            sel: dict[str, Any] = {
                "longitude": slice(extent[0], extent[2]),
                "latitude": slice(extent[3], extent[1]),
            }
            if "isobaricInhPa" in ds.dims:
                sel["isobaricInhPa"] = level
            out = {v: ds[v].sel(**sel).values for v in names}
            ds.close()
            return out

        q_data = _open_pl(["q"], "q")["q"]

        # θe (K) a partir da t do preâmbulo + q. Suavizar q p/ acompanhar a t.
        if smoothing_sigma > 0:
            q_data = gaussian_filter(q_data, sigma=smoothing_sigma)
        theta_e = _theta_e_2d(level, t_data, q_data)

        # Máscara orográfica: onde sp <= (nível + tol) hPa, o nível é subterrâneo.
        # Falha de rede/sp ausente → sem máscara (degradação graciosa).
        try:
            sp_file = download_ecmwf(
                variables=["sp"],
                step=step,
                cycle=cycle,
                output_path=data_dir / f"ecmwf_sp_{date_str}_{cycle_tag}_f{step:03d}.grib2",
                data_dir=data_dir,
                source=source,
                date=cycle_date,
            )
            ds_sp = xr.open_dataset(
                sp_file,
                engine="cfgrib",
                backend_kwargs={"filter_by_keys": {"typeOfLevel": "surface"}, "errors": "ignore"},
            )
            ds_sp = ds_sp.assign_coords(longitude=(ds_sp.longitude + 180) % 360 - 180).sortby(
                "longitude"
            )
            sp_pa = (
                ds_sp["sp"]
                .sel(
                    longitude=slice(extent[0], extent[2]),
                    latitude=slice(extent[3], extent[1]),
                )
                .values
            )
            ds_sp.close()
            if sp_pa.shape == theta_e.shape:
                threshold_pa = (level + THETA_E_HIGHLAND_TOL_HPA) * 100.0
                theta_e = np.where(sp_pa <= threshold_pa, np.nan, theta_e)
        except Exception as e:  # noqa: BLE001 — máscara é opcional por design
            logger.warning("Máscara orográfica de θe indisponível (sp): %s", e)

        # Suavização ciente-de-NaN (não sangra o buraco dos Andes p/ os vizinhos).
        if smoothing_sigma > 0:
            from cartomet_br.data.loczcit_pa_engine import _nanaware_gaussian

            theta_e = _nanaware_gaussian(theta_e, smoothing_sigma)

        if variable_key == "theta_e":
            values = theta_e
        elif variable_key == "theta_e_grad":
            values = _theta_e_gradient(theta_e, dy, dx_2d) * 1e5  # K/m → °C/100km
        elif variable_key == "theta_e_adv":
            uv = _open_pl(["u", "v"], "u_v")
            u_data, v_data = uv["u"], uv["v"]
            if smoothing_sigma > 0:
                u_data = gaussian_filter(u_data, sigma=smoothing_sigma)
                v_data = gaussian_filter(v_data, sigma=smoothing_sigma)
            values = _theta_e_advection(theta_e, u_data, v_data, dy, dx_2d) * 3600.0  # K/s → °C/h
        else:  # tfp_axis — campo TFP mascarado; a isolinha 0 é o eixo (render dedicado)
            values = _tfp_field(theta_e, dy, dx_2d, TFP_GRAD_MIN_K_100KM)

        return PLFieldData(
            values=values,
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


# ═══════════════════════════════════════════════════════════════════════════════
#  DESACUMULAÇÃO TEMPORAL (OLR / Precipitação) — IFS Cycle 50r1
# ═══════════════════════════════════════════════════════════════════════════════
#
# Variáveis de FLUXO ACUMULADO (ttr → OLR, tp → precipitação) guardam a soma
# desde a inicialização da rodada. Para obter o fluxo representativo de uma janela
# curta usa-se DESACUMULAÇÃO: subtrai-se o passo anterior e divide-se pelo Δt.
#
#   • Direta:       janela [step-3, step] da rodada selecionada.
#   • Estabilizada: "Técnica B" — usa a rodada anterior madura (12h antes do
#                   horário válido, janela steps 9–12), mitigando o spin-up da
#                   microfísica de nuvens. Recomendada p/ convecção/ZCIT.
#
# Ref.: Copernicus CKB (de-accumulation, spin-up mitigation); IFS Cycle 50r1
# preenche o step=0 de variáveis acumuladas com zeros.


def _read_accum_field(
    param: str,
    cycle: int | None,
    date_str: str,
    step: int,
    extent: list[float],
    data_dir: Path,
    source: str,
    force_download: bool,
) -> tuple:
    """Baixa e lê uma variável acumulada (ttr/tp) num run+step, recortada ao extent."""
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"
    grib_file = download_ecmwf(
        variables=[param],
        levels=None,
        step=step,
        cycle=cycle,
        date=date_str,  # P0: ancora a rodada calculada (Técnica B);
        # sem isto o cliente baixa a "latest" e o cache fica rotulado errado.
        output_path=Path(data_dir) / f"ecmwf_{param}_{date_str}_{cycle_tag}_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
    )
    ds = xr.open_dataset(grib_file, engine="cfgrib", backend_kwargs={"errors": "ignore"})
    ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180)
    ds = ds.sortby("longitude")
    da = ds[param].sel(
        longitude=slice(extent[0], extent[2]),
        latitude=slice(extent[3], extent[1]),
    )
    vals = da.values
    lons = da.longitude.values
    lats = da.latitude.values
    vt, bt = "", ""
    try:
        if "valid_time" in ds.coords:
            vt = np.datetime_as_string(ds.valid_time.values, unit="m")
        if "time" in ds.coords:
            bt_dt = np.datetime64(ds.time.values, "s").astype("datetime64[s]").astype(datetime)
            bt = bt_dt.strftime("%HZ %d/%m/%Y")
    except (KeyError, IndexError, ValueError, TypeError):
        pass
    ds.close()
    return vals, lons, lats, vt, bt


def _resolve_accum_window(
    technique: str,
    cycle: int | None,
    cycle_date: str | None,
    step: int,
) -> tuple:
    """Resolve (run_cycle, run_date, step_hi, step_lo, rótulo) para a desacumulação."""
    date_str = cycle_date if cycle_date else datetime.now(UTC).strftime("%Y%m%d")

    if technique == "stabilized":
        # Técnica B DINÂMICA (mitiga spin-up): DELEGA à fonte única
        # `resolve_olr_window` (olr_timing.py) — rodada-base 12h antes, step
        # alvo=step+12, janela 3h(≤144)/6h, snapping anti-404 (P1) e fallback de
        # data (C2). Aqui só formatamos o rótulo da camada.
        plan = resolve_olr_window(cycle if cycle is not None else 0, date_str, step)
        label = (
            f"Estabilizada · rodada {plan.base_cycle:02d}Z "
            f"{plan.base_date[6:8]}/{plan.base_date[4:6]} "
            f"(steps {plan.step_lo}–{plan.step_hi})"
        )
        return plan.base_cycle, plan.base_date, plan.step_hi, plan.step_lo, label

    # Direta: janela [step-w, step] da rodada selecionada (w=3h até 144h, senão 6h)
    window_h = 3 if step <= 144 else 6
    step_lo = max(step - window_h, 0)
    label = f"Direta · rodada atual (steps {step_lo}–{step})"
    return cycle, date_str, step, step_lo, label


def _deaccumulate(
    param: str,
    technique: str,
    cycle: int | None,
    cycle_date: str | None,
    step: int,
    extent: list[float],
    data_dir: Path,
    source: str,
    smoothing_sigma: float,
    force_download: bool,
) -> tuple:
    """Desacumula uma variável de fluxo: retorna (Δ, lons, lats, valid, base, rótulo, Δt_seg)."""
    run_cycle, run_date, step_hi, step_lo, label = _resolve_accum_window(
        technique, cycle, cycle_date, step
    )
    vhi, lons, lats, vt, bt = _read_accum_field(
        param, run_cycle, run_date, step_hi, extent, data_dir, source, force_download
    )
    if step_lo > 0:
        vlo, *_ = _read_accum_field(
            param, run_cycle, run_date, step_lo, extent, data_dir, source, force_download
        )
    else:
        # IFS 50r1: step=0 de variáveis acumuladas é zero — evita um download.
        vlo = np.zeros_like(vhi)

    delta = vhi - vlo
    if smoothing_sigma > 0:
        delta = gaussian_filter(delta, sigma=smoothing_sigma)
    dt_seconds = max(step_hi - step_lo, 1) * 3600.0
    return delta, lons, lats, vt, bt, label, dt_seconds


def load_olr(
    extent: list[float],
    step: int = 3,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    smoothing_sigma: float = 1.0,
    source: str = "ecmwf",
    force_download: bool = False,
    technique: str = "direct",
) -> PLFieldData:
    """
    Baixa e processa a OLR (Outgoing Longwave Radiation) DESACUMULADA.

    A partir de `ttr` (Top net thermal radiation, acumulado em J/m²):

        OLR = -(ttr[step_hi] - ttr[step_lo]) / Δt   [W/m²]

    `technique`:
      - "direct"     : janela [step-3, step] da rodada selecionada.
      - "stabilized" : Técnica B (mitiga spin-up) — rodada anterior madura,
                       janela steps 9–12. Recomendada para convecção/ZCIT.
    """
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)

    if technique == "direct" and step < 3:
        raise ValueError(
            "OLR no modo Direto requer step ≥ 3h (no step 0 não há janela de "
            "acumulação).\n\nUse o modo Estabilizada (mitiga spin-up) para obter "
            "a OLR já no horário da análise."
        )

    logger.info("Carregando OLR desacumulada (técnica=%s, step=%s)", technique, step)
    delta, lons, lats, vt, bt, label, dt_sec = _deaccumulate(
        "ttr",
        technique,
        cycle,
        cycle_date,
        step,
        extent,
        data_dir,
        source,
        smoothing_sigma,
        force_download,
    )
    olr = -delta / dt_sec
    logger.info("  OLR %.0f–%.0f W/m² (%s)", np.nanmin(olr), np.nanmax(olr), label)

    return PLFieldData(
        values=olr,
        lons=lons,
        lats=lats,
        variable="olr",
        level=0,
        unit="W/m²",
        valid_time=vt,
        base_time=bt,
        step=step,
    )


def load_precip(
    extent: list[float],
    step: int = 3,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    smoothing_sigma: float = 1.0,
    source: str = "ecmwf",
    force_download: bool = False,
    technique: str = "direct",
) -> PLFieldData:
    """
    Baixa e processa a precipitação DESACUMULADA (mm na janela) a partir de `tp`.

        precip = (tp[step_hi] - tp[step_lo]) × 1000   [mm]

    O ECMWF entrega `tp` ACUMULADO desde t=0 da rodada; a chuva de uma janela é a
    diferença entre dois steps — o método padrão e único válido de PNT. Como o
    acúmulo é MONOTÔNICO (tp[hi] ≥ tp[lo]), o resultado é SEMPRE ≥ 0 ("chuva
    negativa" é impossível); o `np.clip(..., 0, None)` é só uma rede de segurança
    contra artefatos numéricos. NÃO se divide por Δt (chuva é acúmulo em mm, não
    fluxo — diferente da OLR em W/m²).

    `technique`:
      - "direct" (padrão): janela [step-3, step] da RODADA SELECIONADA.
      - "stabilized" (Técnica B): rodada anterior madura (mitiga spin-up). Ver
        `_resolve_accum_window`.
    """
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)

    if technique == "direct" and step < 3:
        raise ValueError(
            "Precipitação no modo Direto requer step ≥ 3h.\n\n"
            "Use o modo Estabilizada para obter a chuva no horário da análise."
        )

    logger.info("Carregando precipitação desacumulada (técnica=%s, step=%s)", technique, step)
    delta, lons, lats, vt, bt, label, _dt = _deaccumulate(
        "tp",
        technique,
        cycle,
        cycle_date,
        step,
        extent,
        data_dir,
        source,
        smoothing_sigma,
        force_download,
    )
    precip_mm = np.clip(delta * 1000.0, 0.0, None)  # m → mm, sem negativos espúrios
    logger.info("  Precip máx %.1f mm/3h (%s)", np.nanmax(precip_mm), label)

    return PLFieldData(
        values=precip_mm,
        lons=lons,
        lats=lats,
        variable="precip",
        level=0,
        unit="mm/3h",
        valid_time=vt,
        base_time=bt,
        step=step,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  TSM DO MODELO IFS (skin temperature sobre o oceano) — IFS Cycle 50r1
# ═══════════════════════════════════════════════════════════════════════════════
#
# O ECMWF Open Data NÃO expõe o parâmetro `sst` (restrito ao MARS credenciado),
# mas expõe `skt` (Skin Temperature). Sobre pontos de OCEANO, a skin temperature
# do modelo acoplado (NEMO4-SI³, 50r1) É a TSM-pele do IFS — a interface direta
# água↔atmosfera, com o ciclo diurno aprimorado. Mascara-se o continente com a
# máscara terra-mar `lsm`. (Verificado empiricamente: `sst` ausente do índice.)


def _read_skt_ocean(
    extent: list[float],
    step: int,
    cycle: int | None,
    cycle_date: str | None,
    data_dir: Path,
    source: str,
    smoothing_sigma: float,
    force_download: bool,
) -> tuple:
    """Lê skt (°C) mascarada ao oceano via lsm. Retorna (skt_c, lons, lats, vt, bt)."""
    date_str = cycle_date if cycle_date else datetime.now(UTC).strftime("%Y%m%d")
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"

    skt_file = download_ecmwf(
        variables=["skt"],
        levels=None,
        step=step,
        cycle=cycle,
        output_path=Path(data_dir) / f"ecmwf_skt_{date_str}_{cycle_tag}_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
        date=cycle_date,
    )
    lsm_file = download_ecmwf(
        variables=["lsm"],
        levels=None,
        step=step,
        cycle=cycle,
        output_path=Path(data_dir) / f"ecmwf_lsm_{date_str}_{cycle_tag}_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
        date=cycle_date,
    )

    def _open(fn, shortname):
        ds = xr.open_dataset(
            fn,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"shortName": shortname}, "errors": "ignore"},
        )
        ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180).sortby("longitude")
        da = ds[shortname].sel(
            longitude=slice(extent[0], extent[2]), latitude=slice(extent[3], extent[1])
        )
        return ds, da

    ds_skt, skt = _open(skt_file, "skt")
    _ds_lsm, lsm = _open(lsm_file, "lsm")

    skt_c = skt.values - 273.15  # K → °C
    lons = skt.longitude.values
    lats = skt.latitude.values

    if smoothing_sigma > 0:
        skt_c = gaussian_filter(skt_c, sigma=smoothing_sigma)

    # Máscara: lsm >= 0.5 é continente → remove (mantém só oceano)
    land = np.asarray(lsm.values) >= 0.5
    skt_c = np.where(land, np.nan, skt_c)

    vt, bt = "", ""
    try:
        if "valid_time" in ds_skt.coords:
            vt = np.datetime_as_string(ds_skt.valid_time.values, unit="m")
        if "time" in ds_skt.coords:
            bt_dt = np.datetime64(ds_skt.time.values, "s").astype("datetime64[s]").astype(datetime)
            bt = bt_dt.strftime("%HZ %d/%m/%Y")
    except (KeyError, IndexError, ValueError, TypeError):
        pass
    ds_skt.close()
    _ds_lsm.close()
    return skt_c, lons, lats, vt, bt


def load_model_sst(
    extent: list[float],
    step: int = 0,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    smoothing_sigma: float = 1.0,
    source: str = "ecmwf",
    force_download: bool = False,
) -> PLFieldData:
    """TSM do modelo IFS (skin temperature sobre o oceano), em °C.

    Variável de estado instantânea — o step=0 entrega a análise sem spin-up.
    """
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)

    logger.info("Carregando TSM do modelo IFS (skt/oceano)")
    skt_c, lons, lats, vt, bt = _read_skt_ocean(
        extent, step, cycle, cycle_date, data_dir, source, smoothing_sigma, force_download
    )
    logger.info("  TSM modelo: %.1f – %.1f °C", np.nanmin(skt_c), np.nanmax(skt_c))

    return PLFieldData(
        values=skt_c,
        lons=lons,
        lats=lats,
        variable="sst_model",
        level=0,
        unit="°C",
        valid_time=vt,
        base_time=bt,
        step=step,
    )


def load_sst_gradient(
    extent: list[float],
    step: int = 0,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    smoothing_sigma: float = 1.0,
    source: str = "ecmwf",
    force_download: bool = False,
) -> PLFieldData:
    """Magnitude do gradiente térmico da TSM do modelo |∇TSM| (°C/100km), via MetPy.

    Calculado sobre o oceano (continente mascarado). Útil para localizar frentes
    térmicas oceânicas que ancoram a convergência de baixos níveis (ZCIT).
    """
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)

    logger.info("Carregando gradiente de TSM do modelo |∇TSM| (MetPy)")
    skt_c, lons, lats, vt, bt = _read_skt_ocean(
        extent, step, cycle, cycle_date, data_dir, source, smoothing_sigma, force_download
    )

    grad_mag = _gradient_magnitude_metpy(skt_c, lons, lats)
    logger.info("  |∇TSM| máx: %.2f °C/100km", np.nanmax(grad_mag))

    return PLFieldData(
        values=grad_mag,
        lons=lons,
        lats=lats,
        variable="sst_grad",
        level=0,
        unit="°C/100km",
        valid_time=vt,
        base_time=bt,
        step=step,
    )


def _gradient_magnitude_metpy(arr: np.ndarray, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """|∇arr| em unidade/100km usando MetPy (fallback p/ diferenças finitas)."""
    try:
        import metpy.calc as mpcalc
        from metpy.units import units

        dx, dy = mpcalc.lat_lon_grid_deltas(lons, lats)
        # Usa kelvin/°C como diferença numérica (∇K ≡ ∇°C); evita offset do degC no pint
        grad = mpcalc.gradient(arr * units.kelvin, deltas=(dy, dx))
        gy = np.asarray(grad[0].magnitude)
        gx = np.asarray(grad[1].magnitude)
        mag = np.sqrt(gx**2 + gy**2)  # °C/m
        return np.asarray(mag * 1e5)  # °C/m → °C/100km
    except Exception as exc:  # pragma: no cover — fallback robusto
        logger.warning("MetPy falhou no gradiente (%s); usando diferenças finitas.", exc)
        lat_rad = np.deg2rad(lats)
        R = 6.371e6
        dy = np.deg2rad(np.diff(lats).mean()) * R
        dx2d = (
            np.deg2rad(np.diff(lons).mean())
            * R
            * np.cos(lat_rad)[:, np.newaxis]
            * np.ones((1, len(lons)))
        )
        dfdy = np.gradient(arr, dy, axis=0)
        dfdx = np.gradient(arr, axis=1) / dx2d
        return np.asarray(np.sqrt(dfdx**2 + dfdy**2) * 1e5)


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

    date_str = cycle_date if cycle_date else datetime.now(UTC).strftime("%Y%m%d")
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
        date=cycle_date,
    )

    ds = xr.open_dataset(grib_file, engine="cfgrib", backend_kwargs={"errors": "ignore"})

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


def t2_extreme_param(variable_key: str, step: int) -> str:
    """Parâmetro do Open Data para extremos de T 2 m conforme o alcance.

    A stream ``oper`` publica a janela de 3 h (``mx2t3``/``mn2t3``) até
    +144 h e a de 6 h (``mx2t6``/``mn2t6``) de +150 h em diante — espelhando
    a grade temporal do IFS (3/3 h → 6/6 h).
    """
    base = "mx2t" if variable_key == "tmax2m" else "mn2t"
    return f"{base}3" if step <= 144 else f"{base}6"


def load_t2_extreme(
    variable_key: str,
    extent: list[float],
    step: int = 3,
    cycle: int | None = None,
    cycle_date: str | None = None,
    data_dir: Path = Path("data"),
    smoothing_sigma: float = 1.0,
    source: str = "ecmwf",
    force_download: bool = False,
) -> PLFieldData:
    """Baixa e processa Temperatura Máxima/Mínima a 2 m (extremos de janela).

    ``mx2t3``/``mn2t3`` são o extremo das ÚLTIMAS 3 h que antecedem o step
    (janela de 6 h além de +144 h) — por isso o step 0 é inválido (janela
    degenerada). Valores convertidos de K para °C.
    """
    if variable_key not in ("tmax2m", "tmin2m"):
        raise ValueError(f"variable_key inválido para extremos de T 2 m: {variable_key}")
    if step < 3:
        raise ValueError(
            "Temp. máx/mín 2 m é o extremo da janela que ANTECEDE o step — "
            "use step ≥ +3h (no step 0 a janela é degenerada)."
        )
    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)

    param = t2_extreme_param(variable_key, step)
    date_str = cycle_date if cycle_date else datetime.now(UTC).strftime("%Y%m%d")
    cycle_tag = f"{cycle:02d}Z" if cycle is not None else "latest"

    logger.info("Carregando %s (%s)", VARIABLE_REGISTRY[variable_key]["nome"], param)

    grib_file = download_ecmwf(
        variables=[param],
        levels=None,
        step=step,
        cycle=cycle,
        output_path=data_dir / f"ecmwf_{param}_{date_str}_{cycle_tag}_f{step:03d}.grib2",
        data_dir=data_dir,
        source=source,
        force_download=force_download,
        date=cycle_date,
    )

    ds = xr.open_dataset(grib_file, engine="cfgrib", backend_kwargs={"errors": "ignore"})
    ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180)
    ds = ds.sortby("longitude")

    # O GRIB baixado contém um único campo; o cfgrib costuma nomeá-lo pelo
    # shortName ("mx2t3", "mn2t6", …) — se divergir, usa o primeiro data_var.
    var_name = param if param in ds.data_vars else next(iter(ds.data_vars))
    da = ds[var_name].sel(
        longitude=slice(extent[0], extent[2]),
        latitude=slice(extent[3], extent[1]),
    )

    values = da.values - 273.15  # K → °C
    lons_arr = da.longitude.values
    lats_arr = da.latitude.values

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
        logger.warning("Não foi possível extrair metadados de tempo (%s): %s", param, e)

    ds.close()

    logger.info("  %s range: %.1f – %.1f °C", param, np.nanmin(values), np.nanmax(values))

    return PLFieldData(
        values=values,
        lons=lons_arr,
        lats=lats_arr,
        variable=variable_key,
        level=0,
        unit="°C",
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

    data: np.ndarray  # Temperatura de brilho em °C
    x: np.ndarray  # Coordenadas x em metros (projeção geoestacionária)
    y: np.ndarray  # Coordenadas y em metros (projeção geoestacionária)
    sat_lon: float  # Longitude do satélite
    sat_h: float  # Altura do satélite (m)
    sat_sweep: str  # Eixo de varredura
    time_str: str  # Data/hora da imagem
    filename: str  # Nome do arquivo original


# Paleta IR4AVHRR6 clássica — 256 cores (branco→roxo→cinza→vermelho→amarelo→verde→azul→ciano→cinza→preto)
# Mapeada para temperaturas de brilho: -103°C (índice 0) a +84°C (índice 255)
_IR_AVHRR_COLORS = [
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (127, 0, 127),
    (140, 13, 135),
    (153, 25, 142),
    (165, 38, 150),
    (178, 51, 157),
    (191, 64, 165),
    (204, 76, 173),
    (217, 89, 180),
    (229, 102, 188),
    (242, 114, 195),
    (255, 127, 203),
    (230, 230, 230),
    (205, 205, 205),
    (179, 179, 179),
    (154, 154, 154),
    (128, 128, 128),
    (103, 103, 103),
    (77, 77, 77),
    (52, 52, 52),
    (26, 26, 26),
    (0, 0, 0),
    (26, 0, 0),
    (51, 0, 0),
    (77, 0, 0),
    (102, 0, 0),
    (128, 0, 0),
    (153, 0, 0),
    (179, 0, 0),
    (204, 0, 0),
    (230, 0, 0),
    (255, 0, 0),
    (255, 26, 0),
    (255, 51, 0),
    (255, 77, 0),
    (255, 102, 0),
    (255, 128, 0),
    (255, 153, 0),
    (255, 179, 0),
    (255, 204, 0),
    (255, 230, 0),
    (255, 255, 0),
    (230, 255, 0),
    (204, 255, 0),
    (179, 255, 0),
    (153, 255, 0),
    (128, 255, 0),
    (102, 255, 0),
    (77, 255, 0),
    (51, 255, 0),
    (26, 255, 0),
    (0, 255, 0),
    (0, 234, 10),
    (0, 213, 19),
    (0, 191, 29),
    (0, 170, 38),
    (0, 149, 48),
    (0, 128, 58),
    (0, 106, 67),
    (0, 85, 77),
    (0, 64, 86),
    (0, 43, 96),
    (0, 21, 105),
    (0, 0, 115),
    (0, 0, 115),
    (0, 13, 122),
    (0, 26, 129),
    (0, 38, 136),
    (0, 51, 143),
    (0, 64, 150),
    (0, 77, 157),
    (0, 89, 164),
    (0, 102, 171),
    (0, 115, 178),
    (0, 128, 185),
    (0, 140, 192),
    (0, 153, 199),
    (0, 166, 206),
    (0, 179, 213),
    (0, 191, 220),
    (0, 204, 227),
    (0, 217, 234),
    (0, 230, 241),
    (0, 242, 248),
    (0, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (255, 255, 255),
    (254, 254, 254),
    (252, 252, 252),
    (249, 249, 249),
    (247, 247, 247),
    (244, 244, 244),
    (242, 242, 242),
    (239, 239, 239),
    (237, 237, 237),
    (234, 234, 234),
    (232, 232, 232),
    (229, 229, 229),
    (226, 226, 226),
    (224, 224, 224),
    (221, 221, 221),
    (219, 219, 219),
    (216, 216, 216),
    (214, 214, 214),
    (211, 211, 211),
    (209, 209, 209),
    (206, 206, 206),
    (203, 203, 203),
    (201, 201, 201),
    (198, 198, 198),
    (196, 196, 196),
    (193, 193, 193),
    (191, 191, 191),
    (188, 188, 188),
    (186, 186, 186),
    (183, 183, 183),
    (181, 181, 181),
    (178, 178, 178),
    (175, 175, 175),
    (173, 173, 173),
    (170, 170, 170),
    (168, 168, 168),
    (165, 165, 165),
    (163, 163, 163),
    (160, 160, 160),
    (158, 158, 158),
    (155, 155, 155),
    (152, 152, 152),
    (150, 150, 150),
    (147, 147, 147),
    (145, 145, 145),
    (142, 142, 142),
    (140, 140, 140),
    (137, 137, 137),
    (135, 135, 135),
    (132, 132, 132),
    (130, 130, 130),
    (127, 127, 127),
    (124, 124, 124),
    (122, 122, 122),
    (119, 119, 119),
    (117, 117, 117),
    (114, 114, 114),
    (112, 112, 112),
    (109, 109, 109),
    (107, 107, 107),
    (104, 104, 104),
    (101, 101, 101),
    (99, 99, 99),
    (96, 96, 96),
    (94, 94, 94),
    (91, 91, 91),
    (89, 89, 89),
    (86, 86, 86),
    (84, 84, 84),
    (81, 81, 81),
    (79, 79, 79),
    (76, 76, 76),
    (73, 73, 73),
    (71, 71, 71),
    (68, 68, 68),
    (66, 66, 66),
    (63, 63, 63),
    (61, 61, 61),
    (58, 58, 58),
    (56, 56, 56),
    (53, 53, 53),
    (50, 50, 50),
    (48, 48, 48),
    (45, 45, 45),
    (43, 43, 43),
    (40, 40, 40),
    (38, 38, 38),
    (35, 35, 35),
    (33, 33, 33),
    (30, 30, 30),
    (28, 28, 28),
    (25, 25, 25),
    (22, 22, 22),
    (20, 20, 20),
    (17, 17, 17),
    (15, 15, 15),
    (12, 12, 12),
    (10, 10, 10),
    (7, 7, 7),
    (5, 5, 5),
    (2, 2, 2),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
    (0, 0, 0),
]


def get_ir_colormap():
    """Retorna o colormap IR AVHRR clássico para imagens de satélite."""
    from matplotlib.colors import LinearSegmentedColormap

    colors_norm = [(r / 255.0, g / 255.0, b / 255.0) for r, g, b in _IR_AVHRR_COLORS]
    return LinearSegmentedColormap.from_list("ir_avhrr", colors_norm, N=256)


def load_goes_netcdf(
    local_path: Path,
    target_time: datetime | None = None,
) -> SatelliteData:
    """Lê um GOES-East Banda 13 (CMIPF) já em disco e monta o ``SatelliteData``.

    SEM REDE — reusado pelo download (após salvar o .nc) e pela restauração de
    projeto a partir do cache. Converte ``x``/``y`` de **radianos → metros**
    (multiplicando por ``perspective_point_height``), que é o que a projeção
    ``ccrs.Geostationary`` espera no ``imshow``: ler os radianos crus colocaria a
    imagem num quadro submilimétrico no centro da projeção (invisível no mapa).
    """
    local_path = Path(local_path)
    logger.info("  Lendo GOES de %s", local_path.name)
    ds = xr.open_dataset(local_path, engine="netcdf4")
    try:
        cmi = ds["CMI"].values

        proj_info = ds["goes_imager_projection"]
        sat_h = float(proj_info.attrs["perspective_point_height"])
        sat_lon = float(proj_info.attrs["longitude_of_projection_origin"])
        sat_sweep = str(proj_info.attrs["sweep_angle_axis"])

        # Coordenadas em radianos → metros (projeção geoestacionária).
        x = ds["x"].values * sat_h
        y = ds["y"].values * sat_h

        time_str = ""
        try:
            t_val = ds["t"].values
            # t_val é escalar 0-d em runtime; stub do numpy não cobre esse overload.
            time_dt = np.datetime64(t_val, "s").astype("datetime64[s]")  # type: ignore[call-overload]
            time_py = time_dt.astype(datetime)
            time_str = time_py.strftime("%Y-%m-%d %H:%M UTC")
        except (KeyError, IndexError, ValueError, TypeError) as e:
            logger.warning("Não foi possível extrair tempo do satélite: %s", e)
            if target_time is not None:
                time_str = target_time.strftime("%Y-%m-%d %H:%M UTC")
    finally:
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
        filename=local_path.name,
    )


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
    import re

    import requests

    if data_dir is None:
        raise ValueError("data_dir não pode ser None")
    data_dir = Path(data_dir)

    if target_time is None:
        target_time = datetime.now(UTC)

    logger.info("Baixando GOES-East Banda 13 (IR)")
    logger.info("  Horário alvo: %s", target_time.strftime("%Y-%m-%d %H:%M UTC"))

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
    best_diff_sec = float("inf")

    for key in band13_files:
        try:
            fname = key.split("/")[-1]
            s_field = fname.split("_s")[1].split("_")[0]  # ex: 20260791250205
            file_year = int(s_field[0:4])
            file_doy = int(s_field[4:7])
            file_hh = int(s_field[7:9])
            file_mm = int(s_field[9:11])

            # Reconstrói datetime do arquivo
            file_dt = datetime(file_year, 1, 1, file_hh, file_mm, 0, tzinfo=UTC) + timedelta(
                days=file_doy - 1
            )

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

    # Lê o .nc em disco → SatelliteData (mesma rotina usada na restauração).
    logger.info("  Lendo dados...")
    if progress_callback:
        progress_callback("status", "Lendo dados NetCDF...")
    return load_goes_netcdf(local_path, target_time=target_time)
