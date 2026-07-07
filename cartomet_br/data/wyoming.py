"""Cliente do arquivo de radiossondagens da Universidade de Wyoming.

Em meados de 2026 a UWyo aposentou o CGI legado (``/cgi-bin/sounding`` →
HTTP 404 permanente), o que quebrou o ``siphon.WyomingUpperAir`` — até a
versão 0.10.x ele ainda constrói a URL antiga. Este módulo consome a
interface WSGI atual, que serve CSV limpo em unidades SI (vento em **m/s**;
o CGI antigo servia nós):

    https://weather.uwyo.edu/wsgi/sounding
        ?datetime=YYYY-MM-DD HH:MM:SS&id=<WMO>&src=<FM35|BUFR>&type=TEXT:CSV

Fontes tentadas em ordem: **FM35** (TEMP alfanumérico — níveis obrigatórios/
significativos, paridade visual com o produto legado) e **BUFR** (nativo de
alta resolução, o default do servidor; único disponível em estações que
abandonaram o TAC).

"Sem dados para esta estação/horário" é sinalizado pelo CORPO
``Unable to retrieve the data for <id> at <dt>.`` — com status HTTP
inconsistente entre fontes (FM35 → 404, BUFR → 400; medido em 06/2026).
Um 400 com corpo diferente (ex.: ``'datetime' was incorrectly specified``)
é erro de request e propaga como ``HTTPError``.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

WYOMING_WSGI_URL = "https://weather.uwyo.edu/wsgi/sounding"

# Ordem de tentativa das fontes (ver docstring do módulo)
WYOMING_SOURCES = ("FM35", "BUFR")

_TIMEOUT_S = 40.0

# Cabeçalhos do CSV da UWyo → contrato do sounding_engine (colunas do siphon).
# O CSV BUFR traz ainda time/longitude/latitude (deriva do balão) — ignoradas.
_COLUMN_MAP = {
    "pressure_hPa": "pressure",
    "geopotential height_m": "height",
    "temperature_C": "temperature",
    "dew point temperature_C": "dewpoint",
    "wind direction_degree": "direction",
    "wind speed_m/s": "speed",
}
_OUTPUT_ORDER = ("pressure", "height", "temperature", "dewpoint", "direction", "speed")


class WyomingNoDataError(RuntimeError):
    """Sem sondagem para esta estação/horário (balão não lançado/não recebido)."""


def parse_wyoming_csv(text: str) -> pd.DataFrame:
    """Converte o CSV da interface WSGI no DataFrame do contrato do engine.

    Colunas de saída: ``pressure`` (hPa), ``height`` (m), ``temperature`` /
    ``dewpoint`` (°C), ``direction`` (°), ``speed`` (m/s) e ``u_wind`` /
    ``v_wind`` (m/s, convenção meteorológica — direção de onde o vento vem).
    Campos ausentes vêm como espaços em branco → ``NaN``.
    """
    df = pd.read_csv(io.StringIO(text), skipinitialspace=True)
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in ("pressure_hPa", "temperature_C") if c not in df.columns]
    if missing:
        raise ValueError(f"CSV inesperado da UWyo — colunas ausentes: {missing}")

    df = df.rename(columns=_COLUMN_MAP)
    keep = [c for c in _OUTPUT_ORDER if c in df.columns]
    df = df[keep].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["pressure"]).reset_index(drop=True)
    if df.empty:
        raise WyomingNoDataError("Sondagem sem níveis válidos.")

    if {"direction", "speed"}.issubset(df.columns):
        ang = np.radians(df["direction"].to_numpy(dtype=float))
        spd = df["speed"].to_numpy(dtype=float)
        df["u_wind"] = -spd * np.sin(ang)
        df["v_wind"] = -spd * np.cos(ang)
    return df


def fetch_wyoming_sounding(
    time: datetime, wmo: str | int, timeout: float = _TIMEOUT_S
) -> pd.DataFrame:
    """Baixa a sondagem da estação ``wmo`` válida em ``time`` (UTC).

    Tenta as fontes de :data:`WYOMING_SOURCES` em ordem. "Sem dados" (404 ou
    CSV vazio) só é conclusivo depois de TODAS as fontes falharem →
    :class:`WyomingNoDataError`. Erros de rede/servidor (timeout, 5xx)
    propagam imediatamente — o chamador decide o fallback.
    """
    import requests

    params_base = {
        "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
        "id": str(wmo),
        "type": "TEXT:CSV",
    }
    last_no_data: WyomingNoDataError | None = None
    for src in WYOMING_SOURCES:
        resp = requests.get(WYOMING_WSGI_URL, params={**params_base, "src": src}, timeout=timeout)
        # "Sem dados" vem pelo CORPO; o status varia por fonte (FM35 404, BUFR 400)
        if resp.status_code in (400, 404) and resp.text.lstrip().startswith(
            "Unable to retrieve the data"
        ):
            last_no_data = WyomingNoDataError(resp.text.strip() or f"Sem dados ({src}) para {wmo}.")
            logger.debug("UWyo %s sem dados p/ %s @ %s", src, wmo, params_base["datetime"])
            continue
        resp.raise_for_status()
        try:
            df = parse_wyoming_csv(resp.text)
        except WyomingNoDataError as e:
            last_no_data = e
            continue
        logger.info(
            "Sondagem %s @ %s via WSGI/%s (%d níveis)",
            wmo,
            params_base["datetime"],
            src,
            len(df),
        )
        return df

    raise last_no_data or WyomingNoDataError(f"Sem dados de {wmo} em {params_base['datetime']}.")
