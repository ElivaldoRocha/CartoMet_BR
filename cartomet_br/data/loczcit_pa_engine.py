"""
Motor do Índice LOCZCIT-PA (Potencial Acoplado) — CartoMet BR v3.0.

Funde três forçantes geofísicas alinhadas espacialmente para localizar a Zona de
Convergência Intertropical (ZCIT):

    1. ∇TSM       — magnitude do gradiente da Temperatura da Superfície do Mar
                    (skin temperature do IFS sobre o oceano)
    2. C          — convergência do vento de baixos níveis (10 m)
    3. F_OLR      — fluxo de Radiação de Onda Longa Emergente, DESACUMULADO
                    (Técnica B: rodada anterior madura, mitigação de spin-up)

O produto final é um RASTER CATEGÓRICO (3=Forte / 2=Moderada / 1=Fraca / NaN) que
orienta o traçado manual da ZCIT pela simbologia OMM (human-in-the-loop). O motor
NÃO traça a carta — apenas quantifica e espacializa o potencial de acoplamento físico.

Referências científicas: Rocha (2022, UFPA — LOCZCIT-IQR); Ferreira et al. (2005);
Gadgil & Guruprasad (1990); Lindzen & Nigam (1987). Forçantes do ECMWF IFS Cycle 50r1.

Este módulo é isolado e testável sem GUI. Ver docs/Metodologia_LOCZCIT-PA.md,
docs/Blindagem_Arquitetural_LOCZCIT-PA_CartoMet_v3.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xarray as xr
from scipy.ndimage import gaussian_filter

from cartomet_br.data.olr_timing import OLR_WINDOW_SHORT_H, resolve_olr_window

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTES DE DOMÍNIO (as 7 blindagens — NÃO alterar sem rever os documentos)
# ═══════════════════════════════════════════════════════════════════════════════

# Blindagem #1 — máscara oceânica por LIMIAR (não `== 0`). Calibrável; valida sobre
# Marajó: baixo demais reabre o buraco costeiro, alto demais contamina o ∇TSM com
# temperatura de solo. A borda mar-terra é onde a forçante térmica nasce.
OCEAN_MASK_THRESHOLD: float = 0.2  # mantém pixels com lsm <= 0.2 (oceano)

# Mitigação da Camada Quente Diurna (DWL) da skt (revisões científicas N1/N2): a
# skin temperature carrega ruído diurno espacialmente variável que polui o ∇TSM.
# Suavização Gaussiana CIENTE-DE-NaN (só sobre o oceano, sem contaminar a costa —
# preserva a Blindagem #1) ANTES da derivação. σ calibrável; 0 desliga.
SKT_SMOOTH_SIGMA: float = 1.0

# Blindagem #2 — épsilon no denominador da normalização meridional.
EPS: float = 1e-6

# Blindagem #5 — "derivar largo, normalizar e filtrar estrito".
# Domínio FIXO do índice (a régua calibrada do IQR), formato [lon_min, lat_min, lon_max, lat_max].
STRICT_EXTENT: list[float] = [-55.0, -15.0, 15.0, 15.0]  # 15°S–15°N, 55°W–15°E
BUFFER_DEG: float = 2.0  # folga p/ diferenças finitas
BUFFER_EXTENT: list[float] = [-57.0, -17.0, 17.0, 17.0]  # 17°S–17°N (derivar largo)

# Blindagem #3 — desacumulação DINÂMICA da OLR (Técnica B; sem steps hardcoded).
# A regra de tempo e suas constantes (rodada-base 12 h antes, janelas curta/longa,
# snapping anti-404) são a FONTE ÚNICA em `olr_timing.py`; o motor delega via
# `resolve_olr_window`. Aqui só derivamos o Δt da janela curta p/ compat. de teste.
# Compat.: para a análise (step=0) → target=12, previous=9, Δt=10800 s.
OLR_WINDOW_SECONDS: float = OLR_WINDOW_SHORT_H * 3600.0  # 10800 s (janela 3 h; step≤144)

# Seção 5 — filtro espacial IQR de Tukey.
IQR_CONSTANT: float = 1.5

# Seção 6 — limiares físicos de OLR (W/m²) e categorias.
#   3 ZCIT Forte     : F_OLR <= 180
#   2 ZCIT Moderada  : 180 < F_OLR <= 210
#   1 ZCIT Fraca     : 210 < F_OLR <= 240
#   0 ZCIT Cinemática: F_OLR > 240 mas banda ATIVA por convergência (resgate do ramo sul)
#   NaN              : fora da máscara ativa / do envelope / do filtro
OLR_THRESHOLD_STRONG: float = 180.0
OLR_THRESHOLD_MODERATE: float = 210.0
OLR_THRESHOLD_WEAK: float = 240.0

# Máscara ATIVA acoplada (resgate cinemático): oceano ∧ (OLR<240 ∨ C>C_THR).
# C_THR calibrado (calibrate_cthr.py, pesquisa ZCIT_AXIS) — convergência ORGANIZADA,
# não o fundo fraco dos alísios. Faixa segura 2e-5..5e-5. MIN_CLUSTER remove núcleos
# isolados (coerência morfológica), em pixels de grade.
C_THR: float = 3.0e-5  # s⁻¹
MIN_CLUSTER_PIXELS: int = 80  # área mínima de aglomerado coerente

# Envelope climatológico de latitude da ZCIT atlântica (migração sazonal sinusoidal):
#   φ_c(doy) = ITCZ_PHI_MEAN + ITCZ_AMP·cos(2π(doy − ITCZ_DOY_PEAK)/365.25)
# Trava física que rejeita convecção subtropical transiente (VCAN/DOL) fora da faixa
# φ_c ± ITCZ_LAT_HALFWIDTH. Refs.: Waliser & Gautier (1993); Nobre & Shukla (1996).
ITCZ_PHI_MEAN: float = 5.0  # °N — latitude média anual do eixo
ITCZ_AMP: float = 4.5  # ° — amplitude da migração sazonal
ITCZ_DOY_PEAK: int = 245  # dia do ano da posição mais ao norte (~2 set)
ITCZ_LAT_HALFWIDTH: float = 7.5  # ° — meia-largura do envelope

# Códigos de categoria do raster (Seção 6).
CAT_CINEMATICA: int = 0  # banda sustentada só por convergência (OLR>240); magenta
CAT_FRACA: int = 1
CAT_MODERADA: int = 2
CAT_FORTE: int = 3

# Blindagem #7 — render categórico (0 Magenta, 1 Verde, 2 Amarelo, 3 Vermelho escuro).
CATEGORY_COLORS: list[str] = ["#FF00FF", "#2E8B57", "#FFD700", "#8B0000"]
CATEGORY_LABELS: dict[int, str] = {
    0: "Cinemática",
    1: "Fraca",
    2: "Moderada",
    3: "Forte",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  RESULTADO
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class LoczcitResult:
    """Saída do motor LOCZCIT-PA — raster categórico + índice contínuo."""

    raster: np.ndarray  # 2D: categorias {0, 1, 2, 3} ou NaN
    lons: np.ndarray  # 1D (domínio estrito)
    lats: np.ndarray  # 1D (domínio estrito)
    index: np.ndarray | None = None  # I_ZCIT contínuo [0,1] (potencial acoplado)
    valid_time: str = ""  # instante alvo (análise)
    base_time: str = ""  # rodada-base da OLR desacumulada
    axis: object | None = None  # ZcitAxisDual (overlay opcional do eixo) — None se indisponível
    meta: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAPEAMENTO DA DESACUMULAÇÃO (Técnica B) — fonte única em olr_timing.py
# ═══════════════════════════════════════════════════════════════════════════════
# A triangulação temporal (rodada-base + par de steps + Δt) foi unificada em
# `cartomet_br/data/olr_timing.py` (DRY): o motor e `ecmwf._resolve_accum_window`
# usam a MESMA função pura `resolve_olr_window`, com fallback de data (C2) e
# snapping anti-404 (P1). `plan_olr_deaccumulation` é mantido como ALIAS histórico
# (importado por tests e código legado).

plan_olr_deaccumulation = resolve_olr_window


# ═══════════════════════════════════════════════════════════════════════════════
#  F1 — AQUISIÇÃO DAS FORÇANTES (leitura no domínio BUFFER; cache-antes-da-rede)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class InputBundle:
    """Tensores brutos lidos no domínio buffer (17°S–17°N), prontos p/ derivar."""

    skt: np.ndarray  # Temperatura de pele (°C) — bruta (mascarar no ∇TSM)
    lsm: np.ndarray  # Máscara terra-mar (fração 0–1)
    u10: np.ndarray  # Vento zonal 10 m (m/s)
    v10: np.ndarray  # Vento meridional 10 m (m/s)
    ttr_hi: np.ndarray  # ttr acumulado no step alto (J/m²)
    ttr_lo: np.ndarray  # ttr acumulado no step baixo (J/m²)
    lons: np.ndarray  # 1D (buffer)
    lats: np.ndarray  # 1D (buffer)
    window_seconds: float = OLR_WINDOW_SECONDS  # Δt da desacumulação (dinâmico)
    valid_time: str = ""
    base_time: str = ""


def _read_grib_var(path: Path, varname: str, extent: list[float]) -> tuple:
    """Lê uma variável de um GRIB de campo único (do retrieve), ajusta longitude e corta.

    `retrieve()` entrega um campo por arquivo, então não é preciso filtrar por
    shortName — que aliás não casaria p/ o vento 10 m (var cfgrib `u10`/`v10`,
    mas shortName GRIB `10u`/`10v`).
    """
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"errors": "ignore"})
    ds = ds.assign_coords(longitude=(ds.longitude + 180) % 360 - 180).sortby("longitude")
    da = ds[varname].sel(
        longitude=slice(extent[0], extent[2]), latitude=slice(extent[3], extent[1])
    )
    return da, ds


class LoczcitCancelled(Exception):
    """Sinaliza cancelamento cooperativo do cálculo LOCZCIT-PA pelo usuário."""


class LoczcitDataError(Exception):
    """Dados indisponíveis no ECMWF Open Data (rolling archive / step fora de alcance)."""


def _fetch_inputs(
    cycle: int,
    cycle_date: str,
    data_dir: Path,
    step: int = 0,
    source: str = "ecmwf",
    force_download: bool = False,
    progress_callback=None,
    cancel_check=None,
) -> InputBundle:
    """Baixa e lê skt/lsm/10u/10v (no step selecionado) + ttr (Técnica B dinâmica).

    `step` é o horizonte de previsão escolhido — as variáveis de ESTADO (skt,
    vento) são lidas nesse step (valid_time = rodada + step); a OLR vem da
    rodada-base madura via `resolve_olr_window` (steps dinâmicos, **nunca**
    step 0). Blindagem #4: `download_ecmwf` checa cache antes da rede e serializa.

    `cancel_check`: callable → bool; checado ANTES de cada download para abortar
    cooperativamente (raise LoczcitCancelled). 404 → LoczcitDataError amigável.
    """
    from cartomet_br.data.ecmwf import download_ecmwf

    data_dir = Path(data_dir)
    ext = BUFFER_EXTENT

    def _emit(msg: str):
        if progress_callback:
            progress_callback(msg)

    def _guard():
        if cancel_check is not None and cancel_check():
            raise LoczcitCancelled()

    # ── Variáveis de ESTADO no step selecionado (downloads separados por param) ──
    # Requisições multi-parâmetro caem no pacote inteiro de superfície (fallback do
    # Open Data); baixar um param por vez é pequeno, confiável e cacheado.
    def _dl(param: str, shortname: str, s: int) -> tuple:
        _guard()
        try:
            f = download_ecmwf(
                variables=[param],
                levels=None,
                step=s,
                cycle=cycle,
                levtype="sfc",
                date=cycle_date,
                output_path=data_dir
                / f"loczcit_{shortname}_{cycle_date}_{cycle:02d}Z_f{s:03d}.grib2",
                data_dir=data_dir,
                source=source,
                force_download=force_download,
            )
        except FileNotFoundError as e:
            raise LoczcitDataError(
                f"O campo '{param}' não está disponível para a rodada {cycle:02d}Z de "
                f"{cycle_date} no step +{s}h.\n\nO ECMWF Open Data mantém apenas as "
                f"rodadas recentes (~3 dias) e steps dentro do alcance. Selecione uma "
                f"rodada mais recente ou um step menor."
            ) from e
        return _read_grib_var(f, shortname, ext)

    _emit("LOCZCIT-PA: baixando TSM (skin)...")
    skt_da, ds_skt = _dl("skt", "skt", step)
    _emit("LOCZCIT-PA: baixando máscara terra-mar...")
    lsm_da, ds_lsm = _dl("lsm", "lsm", step)
    _emit("LOCZCIT-PA: baixando vento 10 m...")
    u_da, ds_u = _dl("10u", "u10", step)
    v_da, ds_v = _dl("10v", "v10", step)

    skt_c = np.asarray(skt_da.values) - 273.15  # K → °C
    lsm = np.asarray(lsm_da.values)
    u10 = np.asarray(u_da.values)
    v10 = np.asarray(v_da.values)
    lons = np.asarray(skt_da.longitude.values)
    lats = np.asarray(skt_da.latitude.values)

    valid_time = ""
    try:
        if "valid_time" in ds_skt.coords:
            valid_time = np.datetime_as_string(ds_skt.valid_time.values, unit="m")
    except (KeyError, ValueError, TypeError):
        pass
    for d in (ds_skt, ds_lsm, ds_u, ds_v):
        d.close()

    # ── OLR desacumulada (Técnica B DINÂMICA): ttr da rodada-base madura ──
    plan = resolve_olr_window(cycle, cycle_date, step)
    _emit(
        f"LOCZCIT-PA: baixando OLR madura (rodada {plan.base_cycle:02d}Z, steps "
        f"{plan.step_hi}−{plan.step_lo})..."
    )

    def _ttr(s: int) -> np.ndarray:
        _guard()
        try:
            f = download_ecmwf(
                variables=["ttr"],
                levels=None,
                step=s,
                cycle=plan.base_cycle,
                levtype="sfc",
                date=plan.base_date,
                output_path=data_dir
                / f"loczcit_ttr_{plan.base_date}_{plan.base_cycle:02d}Z_f{s:03d}.grib2",
                data_dir=data_dir,
                source=source,
                force_download=force_download,
            )
        except FileNotFoundError as e:
            raise LoczcitDataError(
                f"A OLR madura (rodada-base {plan.base_cycle:02d}Z de {plan.base_date}, "
                f"step +{s}h) não está disponível.\n\nA rodada-base pode ter expirado do "
                f"arquivo do ECMWF (~3 dias) ou o horizonte alvo (+{step}h) excede o "
                f"alcance dela. Use uma rodada mais recente ou um step menor."
            ) from e
        da, ds = _read_grib_var(f, "ttr", ext)
        vals = np.asarray(da.values)
        ds.close()
        return vals

    ttr_hi = _ttr(plan.step_hi)
    ttr_lo = _ttr(plan.step_lo)

    base_time = (
        f"{plan.base_cycle:02d}Z {plan.base_date[6:8]}/{plan.base_date[4:6]}/{plan.base_date[0:4]}"
    )

    return InputBundle(
        skt=skt_c,
        lsm=lsm,
        u10=u10,
        v10=v10,
        ttr_hi=ttr_hi,
        ttr_lo=ttr_lo,
        lons=lons,
        lats=lats,
        window_seconds=plan.window_seconds,
        valid_time=valid_time,
        base_time=base_time,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  F2 — FORÇANTES NO BUFFER (derivar largo; blindagem #5)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Forcings:
    """Forçantes derivadas no domínio buffer (ainda não normalizadas)."""

    grad_tsm: np.ndarray  # |∇TSM| (°C/100km), oceano
    convergence: np.ndarray  # C = -(∂u/∂x+∂v/∂y) do vento 10 m (s⁻¹)
    f_olr: np.ndarray  # OLR instantânea desacumulada (W/m²)
    lons: np.ndarray
    lats: np.ndarray
    lsm: np.ndarray | None = None  # máscara terra-mar (p/ máscara ativa acoplada)


def _convergence_metpy(
    u: np.ndarray, v: np.ndarray, lons: np.ndarray, lats: np.ndarray
) -> np.ndarray:
    """C = -(∂u/∂x + ∂v/∂y) via MetPy (fallback p/ diferenças finitas)."""
    try:
        import metpy.calc as mpcalc
        from metpy.units import units

        dx, dy = mpcalc.lat_lon_grid_deltas(lons, lats)
        div = mpcalc.divergence(u * units("m/s"), v * units("m/s"), dx=dx, dy=dy)
        return -np.asarray(div.magnitude)  # convergência = -divergência
    except Exception as exc:  # pragma: no cover — fallback robusto
        logger.warning("MetPy falhou na convergência (%s); diferenças finitas.", exc)
        lat_rad = np.deg2rad(lats)
        R = 6.371e6
        dy = np.deg2rad(np.diff(lats).mean()) * R
        dx2d = (
            np.deg2rad(np.diff(lons).mean())
            * R
            * np.cos(lat_rad)[:, np.newaxis]
            * np.ones((1, len(lons)))
        )
        dudx = np.gradient(u, axis=1) / dx2d
        dvdy = np.gradient(v, dy, axis=0)
        return np.asarray(-(dudx + dvdy))


def _nanaware_gaussian(field: np.ndarray, sigma: float) -> np.ndarray:
    """Suavização Gaussiana CIENTE-DE-NaN (convolução normalizada).

    Suaviza apenas onde há dado válido e NÃO deixa os NaN (continente) sangrarem
    para os pixels oceânicos vizinhos — preservando a interface costeira da
    Blindagem #1 (Marajó/Amapá). Mitiga a Camada Quente Diurna (DWL) da skt antes
    da derivação do ∇TSM (revisões científicas N1/N2; Donlon et al. 2002).
    """
    valid = np.isfinite(field).astype(float)
    filled = np.where(valid > 0, field, 0.0)
    with np.errstate(invalid="ignore"):
        num = gaussian_filter(filled, sigma=sigma, mode="nearest")
        den = gaussian_filter(valid, sigma=sigma, mode="nearest")
        out = np.where(den > 0, num / den, np.nan)
    out[valid == 0] = np.nan  # mantém o continente (NaN de entrada) como NaN
    return out


def compute_forcings(bundle: InputBundle) -> Forcings:
    """Deriva as três forçantes no buffer (∇TSM oceânico, convergência, F_OLR).

    Blindagem #1: ∇TSM só sobre o oceano (lsm <= OCEAN_MASK_THRESHOLD).
    Mitigação DWL: suavização Gaussiana ciente-de-NaN da skt oceânica antes de
    derivar (SKT_SMOOTH_SIGMA; revisões N1/N2).
    Blindagem #3: F_OLR = -(ttr_hi - ttr_lo)/Δt — fluxo instantâneo maduro.
    """
    from cartomet_br.data.ecmwf import _gradient_magnitude_metpy

    # ∇TSM sobre o oceano (continente → NaN antes de derivar)
    ocean = bundle.lsm <= OCEAN_MASK_THRESHOLD
    skt_ocean = np.where(ocean, bundle.skt, np.nan)
    if SKT_SMOOTH_SIGMA > 0:
        # Denoise da DWL só sobre o mar (mascarar ANTES evita contaminar a costa).
        skt_ocean = _nanaware_gaussian(skt_ocean, SKT_SMOOTH_SIGMA)
    grad_tsm = _gradient_magnitude_metpy(skt_ocean, bundle.lons, bundle.lats)

    # Convergência do vento 10 m
    conv = _convergence_metpy(bundle.u10, bundle.v10, bundle.lons, bundle.lats)

    # OLR instantânea desacumulada (W/m²); ttr é acumulado e negativo (energia saindo).
    # Δt vem do plano dinâmico (10800 s p/ janela 3 h; 21600 s p/ 6 h).
    f_olr = -(bundle.ttr_hi - bundle.ttr_lo) / bundle.window_seconds

    return Forcings(
        grad_tsm=grad_tsm,
        convergence=conv,
        f_olr=f_olr,
        lons=bundle.lons,
        lats=bundle.lats,
        lsm=bundle.lsm,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  F3 — CORTE ESTRITO + NORMALIZAÇÃO MERIDIONAL (blindagens #2, #5)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class NormalizedForcings:
    """Forçantes no domínio ESTRITO: três normalizadas [0,1] + OLR absoluta."""

    tsm_n: np.ndarray  # ∇TSM normalizada
    conv_n: np.ndarray  # convergência normalizada
    olr_n_inv: np.ndarray  # OLR normalizada e INVERTIDA (1 − x̂)
    olr_abs: np.ndarray  # OLR absoluta (W/m²) — "Verdade Terrestre" p/ classificar
    lons: np.ndarray
    lats: np.ndarray
    conv_abs: np.ndarray | None = None  # convergência ABSOLUTA (s⁻¹) p/ a máscara ativa
    lsm: np.ndarray | None = None  # máscara terra-mar (estrito) p/ a máscara ativa


def _cut_strict(field: np.ndarray, lons: np.ndarray, lats: np.ndarray) -> tuple:
    """Recorta o buffer para o domínio estrito 15°S–15°N, 55°W–15°E."""
    lon_m = (lons >= STRICT_EXTENT[0]) & (lons <= STRICT_EXTENT[2])
    lat_m = (lats >= STRICT_EXTENT[1]) & (lats <= STRICT_EXTENT[3])
    return field[np.ix_(lat_m, lon_m)], lons[lon_m], lats[lat_m]


def normalize_meridional(field: np.ndarray) -> np.ndarray:
    """Min-Max meridional (coluna a coluna, por longitude) → [0,1].

    Blindagem #2: `skipna` (nanmin/nanmax) + épsilon no denominador + máscara
    explícita p/ colunas degeneradas (max−min < eps → NaN), evitando faixa
    artificial de zeros no IQR. Campo é [lat, lon]; cada coluna = um meridiano.
    """
    import warnings as _w

    with np.errstate(invalid="ignore"), _w.catch_warnings():
        # Colunas 100% NaN (terra) disparam "All-NaN slice" — esperado e tratado.
        _w.simplefilter("ignore", category=RuntimeWarning)
        cmin = np.nanmin(field, axis=0, keepdims=True)
        cmax = np.nanmax(field, axis=0, keepdims=True)
    rng = cmax - cmin
    norm = (field - cmin) / (rng + EPS)
    # Colunas degeneradas (incl. 100% NaN) → NaN explícito
    degenerate = ~(rng >= EPS)  # True onde rng<eps ou NaN
    norm = np.where(np.broadcast_to(degenerate, field.shape), np.nan, norm)
    return norm


def cut_and_normalize(forc: Forcings) -> NormalizedForcings:
    """Corta para o estrito e normaliza meridionalmente (normalizar SÓ após o corte)."""
    tsm_s, lons_s, lats_s = _cut_strict(forc.grad_tsm, forc.lons, forc.lats)
    conv_s, _, _ = _cut_strict(forc.convergence, forc.lons, forc.lats)
    olr_s, _, _ = _cut_strict(forc.f_olr, forc.lons, forc.lats)
    lsm_s = None
    if forc.lsm is not None:
        lsm_s, _, _ = _cut_strict(forc.lsm, forc.lons, forc.lats)

    tsm_n = normalize_meridional(tsm_s)
    conv_n = normalize_meridional(conv_s)
    # OLR: topos frios emitem MENOS radiação → inverter o sinal normalizado
    olr_n_inv = 1.0 - normalize_meridional(olr_s)

    return NormalizedForcings(
        tsm_n=tsm_n,
        conv_n=conv_n,
        olr_n_inv=olr_n_inv,
        olr_abs=olr_s,
        lons=lons_s,
        lats=lats_s,
        conv_abs=conv_s,  # convergência absoluta (pré-norm) p/ a máscara ativa
        lsm=lsm_s,  # lsm estrito p/ o portão oceânico da máscara ativa
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  F4 — ACOPLAMENTO (Ockham) + IQR (Tukey) + CLASSIFICAÇÃO por OLR
# ═══════════════════════════════════════════════════════════════════════════════


def couple_ockham(norm: NormalizedForcings) -> np.ndarray:
    """I_ZCIT = média aritmética das 3 forçantes normalizadas (Navalha de Ockham).

    Peso idêntico p/ ∇TSM, convergência e OLR (invertida) — sem "números mágicos".
    NaN propaga: pixel só é válido onde as três forçantes coexistem.
    """
    return np.asarray((norm.tsm_n + norm.conv_n + norm.olr_n_inv) / 3.0)


def iqr_latitude_band(izcit: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Banda de latitude do eixo central via IQR de Tukey (blinda contra DOL/VCAN).

    Para cada meridiano, pega a latitude de máxima energia (I_ZCIT); sobre essa
    série 1D aplica as cercas de Tukey [Q1−1.5·IQR, Q3+1.5·IQR]. Latitudes fora
    da banda são outliers espaciais. Retorna máscara booleana [lat, lon].
    """
    nlat, nlon = izcit.shape
    max_lats_list: list[float] = []
    with np.errstate(invalid="ignore"):
        for j in range(nlon):
            col = izcit[:, j]
            if np.all(np.isnan(col)):
                continue
            max_lats_list.append(lats[int(np.nanargmax(col))])
    max_lats = np.asarray(max_lats_list, dtype=float)
    if max_lats.size < 4:  # amostra insuficiente p/ IQR
        return np.ones_like(izcit, dtype=bool)

    q1, q3 = np.percentile(max_lats, [25, 75])
    iqr = q3 - q1
    lo = q1 - IQR_CONSTANT * iqr
    hi = q3 + IQR_CONSTANT * iqr
    band = (lats >= lo) & (lats <= hi)  # [nlat]
    return np.broadcast_to(band[:, None], izcit.shape)


def classify_by_olr(olr_abs: np.ndarray) -> np.ndarray:
    """Classifica por OLR absoluta: 3≤180, 2≤210, 1≤240, >240→NaN (céu limpo).

    Mantido para compatibilidade (3 classes). O padrão atual do motor é o raster de
    4 classes via ``categorical_raster`` (inclui a Cinemática). Ver ``build_raster``.
    """
    raster = np.full(olr_abs.shape, np.nan)
    with np.errstate(invalid="ignore"):
        raster[olr_abs <= OLR_THRESHOLD_WEAK] = 1.0
        raster[olr_abs <= OLR_THRESHOLD_MODERATE] = 2.0
        raster[olr_abs <= OLR_THRESHOLD_STRONG] = 3.0
    return raster


# ── Máscara ATIVA acoplada + envelope sazonal + classificação 4 classes ──────────
# Portados de _rascunhos/Projeto_ZCIT_AXIS/zcit_coupled.py (valores idênticos).


def _largest_clusters(binary: np.ndarray, min_size: int) -> np.ndarray:
    """Mantém só aglomerados conexos com área >= min_size (8-conectividade=Queen)."""
    from scipy.ndimage import label

    structure = np.ones((3, 3), dtype=int)
    labels, n = label(binary, structure=structure)
    if n == 0:
        return np.zeros_like(binary, dtype=bool)
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    keep = np.where(sizes >= min_size)[0]
    return np.isin(labels, keep)


def active_mask(
    olr_abs: np.ndarray,
    conv_abs: np.ndarray | None,
    lsm: np.ndarray | None,
    *,
    t_env: float = OLR_THRESHOLD_WEAK,
    c_thr: float = C_THR,
    ocean_thr: float = OCEAN_MASK_THRESHOLD,
    min_cluster: int = MIN_CLUSTER_PIXELS,
) -> np.ndarray:
    """Portão físico ACOPLADO: oceano ∧ (OLR<t_env ∨ C>c_thr), depois coerência morfológica.

    A união OLR-baixa OU convergência-forte permite que o ramo sul (invisível em OLR)
    seja "ativado" pela convergência. `_largest_clusters` remove núcleos isolados.
    Se `conv_abs`/`lsm` forem None, o gancho correspondente fica neutro (compat.).
    """
    olr_abs = np.asarray(olr_abs, dtype=float)
    ocean = (np.asarray(lsm) <= ocean_thr) if lsm is not None else np.ones_like(olr_abs, dtype=bool)
    radiative = np.isfinite(olr_abs) & (olr_abs < t_env)
    if conv_abs is not None:
        conv_abs = np.asarray(conv_abs, dtype=float)
        kinematic = np.isfinite(conv_abs) & (conv_abs > c_thr)
    else:
        kinematic = np.zeros_like(olr_abs, dtype=bool)
    raw = ocean & (radiative | kinematic)
    return _largest_clusters(raw, min_cluster)


def itcz_central_latitude(
    doy: int, phi_mean: float = ITCZ_PHI_MEAN, amp: float = ITCZ_AMP, doy_peak: int = ITCZ_DOY_PEAK
) -> float:
    """Latitude central climatológica do eixo da ZCIT atlântica para o dia do ano `doy`."""
    return float(phi_mean + amp * np.cos(2.0 * np.pi * (doy - doy_peak) / 365.25))


def itcz_lat_band(lats: np.ndarray, doy: int, half_width: float = ITCZ_LAT_HALFWIDTH, **kw):
    """Máscara 1D [nlat] da faixa física plausível da ZCIT em `doy` (±half_width de φ_c).

    Devolve (inside[nlat] bool, (lo, phi_c, hi)). Zera o peso fora da faixa, rejeitando
    excursões subtropicais (ex.: 12°N em abril) sem clipar o ramo sul.
    """
    phi_c = itcz_central_latitude(doy, **kw)
    lo, hi = phi_c - half_width, phi_c + half_width
    lat = np.asarray(lats, dtype=float)
    inside = (lat >= lo) & (lat <= hi)
    return inside, (lo, phi_c, hi)


def categorical_raster(olr_abs: np.ndarray, active: np.ndarray) -> np.ndarray:
    """Raster categórico por PIXEL (Seção 6) sobre a máscara ATIVA.

    Cada pixel ativo recebe a categoria pela OLR ABSOLUTA: Forte (≤180), Moderada
    (180–210), Fraca (210–240) ou Cinemática (>240: banda só por convergência, sem
    assinatura radiativa). Fora da máscara ativa → NaN. Float [nlat,nlon] com códigos CAT_*.
    """
    olr_abs = np.asarray(olr_abs, dtype=float)
    raster = np.full(olr_abs.shape, np.nan)
    a = np.asarray(active, dtype=bool) & np.isfinite(olr_abs)
    with np.errstate(invalid="ignore"):
        raster[a & (olr_abs <= OLR_THRESHOLD_STRONG)] = float(CAT_FORTE)
        raster[a & (olr_abs > OLR_THRESHOLD_STRONG) & (olr_abs <= OLR_THRESHOLD_MODERATE)] = float(
            CAT_MODERADA
        )
        raster[a & (olr_abs > OLR_THRESHOLD_MODERATE) & (olr_abs <= OLR_THRESHOLD_WEAK)] = float(
            CAT_FRACA
        )
        raster[a & (olr_abs > OLR_THRESHOLD_WEAK)] = float(CAT_CINEMATICA)
    return raster


def build_raster(
    norm: NormalizedForcings, *, filter_method: str = "iqr", doy: int | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Acopla (Ockham), aplica o portão físico (máscara ativa + envelope) e classifica.

    Pipeline (fiel à pesquisa `zcit_coupled.py`):
      1. I_ZCIT = média das forçantes normalizadas (Navalha de Ockham).
      2. Máscara ATIVA acoplada ``oceano ∧ (OLR<240 ∨ C>C_THR)`` — a UNIÃO resgata o ramo
         sul (nítido na convergência, cego na OLR). A coerência espacial já está embutida
         (``_largest_clusters`` remove núcleos isolados). NÃO se aplica o IQR-de-latitude
         sobre o I_ZCIT aqui: ele assume uma banda única e amputaria justamente o resgate
         cinemático. A herança IQR/Tukey permanece na detecção do EIXO (``zcit_axis``).
      3. ENVELOPE sazonal de latitude (se ``doy``) — rejeita transientes subtropicais.
      4. ``filter_method``:
         - ``"iqr"`` (padrão, rápido): usa a coerência morfológica embutida na máscara ativa.
         - ``"coherence"`` (LISA / Moran Local, opcional/robusto): refina o envelope contíguo
           interseccionando a máscara ativa com ``coherence_mask(izcit)``. Cai no padrão se o
           extra ``spatial`` faltar (sem travar a GUI).
      5. Classificação categórica em 4 classes por OLR absoluta (inclui a Cinemática).

    Retorna (raster categórico {0,1,2,3,NaN}, I_ZCIT contínuo [0,1], máscara ativa booleana).
    """
    izcit = couple_ockham(norm)

    # Portão físico acoplado (coerência morfológica embutida) + envelope sazonal
    active = active_mask(norm.olr_abs, norm.conv_abs, norm.lsm)
    if doy is not None:
        inside, _ = itcz_lat_band(norm.lats, doy)
        active = active & inside[:, None]

    # Refinamento geoestatístico opcional (LISA) sobre o índice acoplado
    if filter_method == "coherence":
        from cartomet_br.data.spatial_coherence import (
            SpatialDepsMissing,
            coherence_mask,
        )

        try:
            active = active & np.asarray(coherence_mask(izcit), dtype=bool)
        except SpatialDepsMissing:
            logger.warning(
                "Coerência espacial indisponível (instale o extra 'spatial'); "
                "mantendo a máscara ativa morfológica (modo IQR)."
            )

    # Classifica em 4 classes só onde a banda está ATIVA
    raster = categorical_raster(norm.olr_abs, active)
    return raster, izcit, active


# ═══════════════════════════════════════════════════════════════════════════════
#  ORQUESTRADOR PÚBLICO (F1→F4)
# ═══════════════════════════════════════════════════════════════════════════════


def compute_loczcit_pa(
    cycle: int,
    cycle_date: str,
    data_dir,
    step: int = 0,
    source: str = "ecmwf",
    force_download: bool = False,
    filter_method: str = "iqr",
    progress_callback=None,
    cancel_check=None,
) -> LoczcitResult:
    """Executa o pipeline completo do índice LOCZCIT-PA e retorna o raster categórico.

    `step` é o horizonte de previsão (0 = análise) — define o valid_time e a
    desacumulação dinâmica da OLR. Domínio FIXO (15°S–15°N, 55°W–15°E): derivar
    largo (buffer 17°), normalizar e filtrar estrito. Ver docs/Metodologia_LOCZCIT-PA.md.

    `filter_method`: ``"iqr"`` (padrão, rápido) ou ``"coherence"`` (LISA/Moran
    Local, robusto e opcional — requer o extra ``spatial``; cai no IQR se ausente).

    `cancel_check`: callable → bool para cancelamento cooperativo (raise
    LoczcitCancelled entre downloads).
    """
    bundle = _fetch_inputs(
        cycle=cycle,
        cycle_date=cycle_date,
        data_dir=Path(data_dir),
        step=step,
        source=source,
        force_download=force_download,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )
    if progress_callback:
        method_msg = "Coerência Espacial (LISA)" if filter_method == "coherence" else "IQR"
        progress_callback(f"LOCZCIT-PA: calculando forçantes e acoplamento ({method_msg})...")

    forc = compute_forcings(bundle)
    norm = cut_and_normalize(forc)
    doy = _doy_from_valid_time(bundle.valid_time)
    raster, izcit, active = build_raster(norm, filter_method=filter_method, doy=doy)

    # Overlay opcional do EIXO (banda simples/dupla). Nunca derruba o raster: se a
    # detecção (ou o statsmodels) falhar, segue sem eixo (axis=None).
    axis = _detect_axis_safe(norm, izcit, active, doy)

    return LoczcitResult(
        raster=raster,
        index=izcit,
        lons=norm.lons,
        lats=norm.lats,
        valid_time=bundle.valid_time,
        base_time=bundle.base_time,
        axis=axis,
        meta={
            "n_strong": int(np.nansum(raster == CAT_FORTE)),
            "n_moderate": int(np.nansum(raster == CAT_MODERADA)),
            "n_weak": int(np.nansum(raster == CAT_FRACA)),
            "n_cinematica": int(np.nansum(raster == CAT_CINEMATICA)),
            "ocean_mask_threshold": OCEAN_MASK_THRESHOLD,
            "filter_method": filter_method,
            "doy": doy,
            "is_double": bool(getattr(axis, "is_double", False)),
        },
    )


def _doy_from_valid_time(valid_time: str) -> int | None:
    """Dia do ano (1–366) a partir de '2026-05-31T12:00'; None se malformado."""
    try:
        return datetime.strptime(valid_time[:10], "%Y-%m-%d").timetuple().tm_yday
    except (ValueError, TypeError):
        return None


def _detect_axis_safe(
    norm: NormalizedForcings, izcit: np.ndarray, active: np.ndarray, doy: int | None
) -> object | None:
    """Detecta o eixo (ZcitAxisDual) injetando I_ZCIT + máscara ativa. Falha → None."""
    try:
        from cartomet_br.data.zcit_dual import detect_zcit_axis_dual

        return detect_zcit_axis_dual(
            norm.olr_abs,
            norm.lats,
            norm.lons,
            lsm=norm.lsm,
            intensity=izcit,
            mask_override=active,
        )
    except Exception as exc:  # pragma: no cover — overlay é best-effort
        logger.warning("Detecção de eixo da ZCIT indisponível (%s); seguindo sem overlay.", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  PERSISTÊNCIA — salva o produto (raster + índice) em NetCDF georreferenciado
# ═══════════════════════════════════════════════════════════════════════════════


def _stamp_from_valid(valid_time: str) -> str:
    """'2026-05-31T12:00' → '20260531T1200Z' (ordenável, sem espaços)."""
    try:
        dt = datetime.strptime(valid_time[:16], "%Y-%m-%dT%H:%M")
        return dt.strftime("%Y%m%dT%H%MZ")
    except (ValueError, TypeError):
        return datetime.now(UTC).strftime("%Y%m%dT%H%MZ")


def save_loczcit_netcdf(result: LoczcitResult, out_dir: Path) -> Path:
    """Salva o produto LOCZCIT-PA num NetCDF CF (raster categórico + I_ZCIT).

    Formato portátil (QGIS, Panoply, xarray). Nome ordenável e autoexplicativo:
    ``loczcit_pa_<validtime>.nc`` (ex.: loczcit_pa_20260531T1200Z.nc).
    É um PRODUTO de análise do usuário — preservado pela limpeza de cache.
    """
    import xarray as xr

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"loczcit_pa_{_stamp_from_valid(result.valid_time)}.nc"

    data_vars = {
        "loczcit_raster": (
            ("lat", "lon"),
            result.raster,
            {
                "long_name": "Categoria da ZCIT (LOCZCIT-PA)",
                "flag_values": [0, 1, 2, 3],
                "flag_meanings": "cinematica fraca moderada forte",
                "comment": "0=Cinematica(magenta) 1=Fraca(verde) 2=Moderada(amarelo) "
                "3=Forte(vermelho); NaN=nulo",
            },
        ),
    }
    if result.index is not None:
        data_vars["izcit"] = (
            ("lat", "lon"),
            result.index,
            {
                "long_name": "Indice LOCZCIT-PA (potencial de acoplamento fisico)",
                "units": "1",
                "valid_range": [0.0, 1.0],
            },
        )

    ds = xr.Dataset(
        data_vars,
        coords={
            "lat": (
                "lat",
                np.asarray(result.lats),
                {"units": "degrees_north", "standard_name": "latitude"},
            ),
            "lon": (
                "lon",
                np.asarray(result.lons),
                {"units": "degrees_east", "standard_name": "longitude"},
            ),
        },
        attrs={
            "title": "Indice Integrado LOCZCIT-PA (Potencial Acoplado)",
            "summary": "Raster categorico da ZCIT (3=Forte/2=Moderada/1=Fraca) + "
            "indice continuo de acoplamento (dTSM, convergencia 10m, OLR desacumulada).",
            "valid_time": result.valid_time,
            "base_time_olr": result.base_time,
            "ocean_mask_threshold": OCEAN_MASK_THRESHOLD,
            "olr_thresholds_wm2": "180/210/240",
            "filter_method": result.meta.get("filter_method", "iqr"),
            "method": "LOCZCIT-PA - Rocha (2022, UFPA); Ferreira et al. (2005); "
            "Gadgil & Guruprasad (1990)",
            "source": "ECMWF IFS Cycle 50r1 (Open Data, CC-BY-4.0)",
            "institution": "PPGGRD-UFPA / FAMET-UFPA",
            "software": "CartoMet BR v3.0",
            "Conventions": "CF-1.8",
        },
    )
    ds.to_netcdf(path)
    ds.close()
    logger.info("Produto LOCZCIT-PA salvo: %s", path)
    return path
