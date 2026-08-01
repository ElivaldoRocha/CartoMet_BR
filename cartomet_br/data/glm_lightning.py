"""
Raios GLM ao vivo — Geostationary Lightning Mapper do GOES-East (CartoMet BR v3.2).

Consome o produto ``GLM-L2-LCFA`` (Lightning Cluster-Filter Algorithm) dos
MESMOS buckets S3 públicos da NOAA que a imagem de satélite já usa
(``noaa-goes19`` com fallback ``noaa-goes16``, sem autenticação). O GLM grava
um netCDF a cada ~20 s; a janela padrão de 15 min são ~45 arquivos pequenos
(dezenas de KB), baixados SERIALIZADOS com cache por nome.

De cada arquivo saem os *flashes* (``flash_lat``/``flash_lon``) — o nível
mais agregado do produto (evento → grupo → flash), o certo para sinóptica.
A idade de cada flash (p/ colorir por recência) vem do timestamp de início
do próprio arquivo (granularidade de 20 s — mais que suficiente para as
faixas de 5 min).

Camada de dados PURA (sem Qt): o worker vive em ``gui/analysis_engine.py``
e o overlay em ``map_canvas.py`` (padrão dos Avisos INMET).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

GLM_PRODUCT = "GLM-L2-LCFA"
GLM_WINDOW_MINUTES = 15
# Faixas de idade (min) do overlay: 0–5 / 5–10 / 10–15.
GLM_AGE_BINS_MIN = (5.0, 10.0, 15.0)

# Mesma ordem de preferência do satélite: GOES-19 (operacional) → GOES-16.
_SATELLITES = (
    ("noaa-goes19", "G19", "GOES-19"),
    ("noaa-goes16", "G16", "GOES-16"),
)
# GOES-19 assumiu como GOES-East operacional em 04/04/2025. Antes disso o
# bucket noaa-goes19 já continha dados PRELIMINARES do período de checkout
# (set/2024+) — para janelas históricas anteriores, o GOES-East da época era
# o G16 e a preferência se inverte (senão o preliminar passaria por oficial).
G19_OPERATIONAL_SINCE = datetime(2025, 4, 4, tzinfo=UTC)

_KEY_RE = re.compile(r"<Key>([^<]+)</Key>")
# Campo _s do nome: OR_GLM-L2-LCFA_G19_s20262121858200_e..._c....nc
_START_RE = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})\d")

ProgressCallback = Callable[[str], None] | None
CancelCheck = Callable[[], bool] | None


class GlmError(Exception):
    """Falha tratada da camada GLM (rede/listagem/leitura) com msg amigável."""


class GlmCancelled(Exception):
    """Busca cancelada cooperativamente pelo usuário."""


@dataclass
class GLMLightningData:
    """Flashes da janela, prontos para o overlay (arrays alinhados)."""

    lons: np.ndarray
    lats: np.ndarray
    ages_min: np.ndarray  # idade de cada flash (min) em relação ao FIM da janela
    window_start: datetime
    window_end: datetime
    satellite: str  # "GOES-19" | "GOES-16"
    n_files: int
    extent: list[float] | None = field(default=None)
    # True = janela "agora" (re-buscar depois traz dados novos); False =
    # janela histórica fixa (re-buscar repete o MESMO resultado).
    live: bool = True

    @property
    def n_flashes(self) -> int:
        return int(self.lons.size)


def window_label(window_start: datetime, window_end: datetime) -> str:
    """Rótulo honesto da janela: SEMPRE com ano; data do fim quando cruza o dia.

    Fonte única dos carimbos (legenda, status, detalhe da camada, erros) —
    uma janela 31/12/2025 23:50 → 01/01/2026 00:05 nunca pode aparecer como
    "31/12 23:50–00:05". Segundos só quando o usuário os usou.
    """
    fmt_t = "%H:%M:%S" if (window_start.second or window_end.second) else "%H:%M"
    ini = f"{window_start:%d/%m/%Y} {window_start:{fmt_t}}"
    if window_start.date() == window_end.date():
        return f"{ini}–{window_end:{fmt_t}} UTC"
    return f"{ini}–{window_end:%d/%m/%Y} {window_end:{fmt_t}} UTC"


def parse_start_time(filename: str) -> datetime | None:
    """Timestamp de início (aware UTC) do campo ``_s`` do nome do arquivo GOES."""
    m = _START_RE.search(filename)
    if m is None:
        return None
    year, doy, hh, mm, ss = (int(g) for g in m.groups())
    try:
        return datetime(year, 1, 1, hh, mm, ss, tzinfo=UTC) + timedelta(days=doy - 1)
    except ValueError:
        return None


def _hour_prefixes(window_start: datetime, window_end: datetime) -> list[str]:
    """Prefixos S3 (ano/dia-juliano/hora) que a janela toca — 1 ou 2 horas."""
    prefixes = []
    t = window_start.replace(minute=0, second=0, microsecond=0)
    while t <= window_end:
        prefixes.append(f"{GLM_PRODUCT}/{t:%Y}/{t:%j}/{t:%H}/")
        t += timedelta(hours=1)
    return prefixes


def list_glm_keys(
    window_start: datetime, window_end: datetime, session=None
) -> tuple[str, str, list[str]]:
    """Lista as chaves GLM da janela → (bucket_url, satélite, chaves ordenadas).

    Mesma listagem HTTP anônima do satélite (``?list-type=2&prefix=...`` +
    regex ``<Key>``). GOES-19 primeiro; se a listagem vier vazia (satélite em
    manutenção), cai para o GOES-16. ``session`` (requests.Session) reusa a
    conexão TLS — com ~45 requisições pequenas, o handshake dominaria o tempo.
    """
    import requests

    http = session if session is not None else requests

    satellites = (
        _SATELLITES
        if window_end >= G19_OPERATIONAL_SINCE
        else tuple(reversed(_SATELLITES))  # caso histórico: o GOES-East era o G16
    )
    for bucket_name, sat_id, sat_label in satellites:
        bucket_url = f"https://{bucket_name}.s3.amazonaws.com"
        keys: list[str] = []
        for prefix in _hour_prefixes(window_start, window_end):
            list_url = f"{bucket_url}?list-type=2&prefix={prefix}&max-keys=1000"
            try:
                resp = http.get(list_url, timeout=30)
                resp.raise_for_status()
            except (requests.RequestException, OSError) as e:
                logger.warning("GLM: erro ao listar %s: %s", prefix, e)
                continue
            for key in _KEY_RE.findall(resp.text):
                if sat_id not in key or not key.endswith(".nc"):
                    continue
                # Fim EXCLUSIVO: um granulo que COMEÇA no fim da janela cobre
                # os 20 s seguintes — seus flashes são posteriores ao instante
                # pedido e entrariam como "mais recentes" (idade 0).
                start = parse_start_time(key.rsplit("/", 1)[-1])
                if start is not None and window_start <= start < window_end:
                    keys.append(key)
        if keys:
            return bucket_url, sat_label, sorted(set(keys))  # dedup defensivo
    raise GlmError(
        "Nenhum arquivo GLM encontrado na janela "
        f"{window_label(window_start, window_end)} (GOES-19 e GOES-16).\n\n"
        "Janela recente: pode ser indisponibilidade momentânea do S3 da NOAA "
        "ou falha de conexão — tente de novo em instantes.\n"
        "Janela histórica: confira a data (o GLM opera desde 2017, no ar do "
        "GOES-16 em diante)."
    )


def _ensure_file(bucket_url: str, key: str, data_dir: Path, session=None) -> Path:
    """Baixa um arquivo GLM se não estiver no cache (nomes são únicos)."""
    import requests

    http = session if session is not None else requests
    local = data_dir / key.rsplit("/", 1)[-1]
    if local.exists() and local.stat().st_size > 0:
        return local
    resp = http.get(f"{bucket_url}/{key}", timeout=60)
    resp.raise_for_status()
    local.write_bytes(resp.content)
    return local


def _read_flashes(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Lê ``flash_lat``/``flash_lon`` de um netCDF GLM (arquivo plano, sem grupos).

    ``netCDF4`` direto (não ``xarray.open_dataset``): o GLM tem ~50 variáveis
    e o decode completo do xarray custava ~1 s/arquivo — ×45 arquivos por
    janela. Ler só as duas variáveis derruba para milissegundos.
    """
    import netCDF4

    ds = netCDF4.Dataset(path, "r")
    try:
        lats = np.asarray(ds.variables["flash_lat"][:], dtype=float)
        lons = np.asarray(ds.variables["flash_lon"][:], dtype=float)
    finally:
        ds.close()
    return lons, lats


def fetch_glm_flashes(
    data_dir: Path,
    extent: list[float] | None = None,
    window_minutes: int = GLM_WINDOW_MINUTES,
    now: datetime | None = None,
    progress_callback: ProgressCallback = None,
    cancel_check: CancelCheck = None,
) -> GLMLightningData:
    """Baixa (cache-first, serializado) e agrega os flashes da janela.

    ``extent`` ([lon_min, lat_min, lon_max, lat_max]) filtra os flashes ao
    recorte do mapa — o disco cheio do GOES teria dezenas de milhares de
    pontos. ZERO flashes na janela é resultado VÁLIDO (céu eletricamente
    calmo), não erro — erro é só rede/listagem (``GlmError``).
    """
    import requests

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    live = now is None
    if now is None:
        window_end = datetime.now(UTC)
    else:
        # Normaliza QUALQUER datetime a UTC: naive = já-é-UTC (contrato da
        # GUI e dos scripts externos); aware de outro fuso é convertido —
        # sem isto, os prefixos de hora do S3 seriam listados no fuso errado.
        window_end = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        if window_end - datetime.now(UTC) > timedelta(minutes=2):
            raise GlmError(
                f"O fim da janela está no futuro ({window_end:%d/%m/%Y %H:%M:%S} UTC).\n\n"
                "O GLM só tem dados do passado — confira o ano e a hora "
                "escolhidos (o horário do seletor é UTC, não hora local)."
            )
    window_start = window_end - timedelta(minutes=window_minutes)

    if progress_callback:
        progress_callback("Listando arquivos GLM no S3 da NOAA…")
    session = requests.Session()  # keep-alive: 1 conexão p/ as ~45 requisições
    try:
        bucket_url, sat_label, keys = list_glm_keys(window_start, window_end, session=session)
        logger.info(
            "GLM: %d arquivos de %s na janela de %d min", len(keys), sat_label, window_minutes
        )

        all_lons: list[np.ndarray] = []
        all_lats: list[np.ndarray] = []
        all_ages: list[np.ndarray] = []
        n_read = 0
        for i, key in enumerate(keys):
            if cancel_check is not None and cancel_check():
                raise GlmCancelled("Busca de raios cancelada.")
            if progress_callback and (i % 5 == 0 or i == len(keys) - 1):
                progress_callback(f"Raios GLM: arquivo {i + 1}/{len(keys)}…")
            fname = key.rsplit("/", 1)[-1]
            start = parse_start_time(fname)
            try:
                path = _ensure_file(bucket_url, key, data_dir, session=session)
                lons, lats = _read_flashes(path)
            except Exception as e:  # 1 arquivo ruim não derruba a janela inteira
                logger.warning("GLM: pulando %s (%s)", fname, e)
                continue
            n_read += 1
            if lons.size == 0:
                continue
            if extent is not None:
                mask = (
                    (lons >= extent[0])
                    & (lons <= extent[2])
                    & (lats >= extent[1])
                    & (lats <= extent[3])
                )
                lons, lats = lons[mask], lats[mask]
                if lons.size == 0:
                    continue
            age_min = (window_end - start).total_seconds() / 60.0 if start else 0.0
            all_lons.append(lons)
            all_lats.append(lats)
            all_ages.append(np.full(lons.shape, age_min))
    finally:
        session.close()

    if n_read == 0:
        raise GlmError(
            "Os arquivos GLM foram listados mas nenhum pôde ser lido.\n\n"
            "Pode ser instabilidade do S3 da NOAA — tente novamente."
        )

    lons_arr = np.concatenate(all_lons) if all_lons else np.empty(0)
    lats_arr = np.concatenate(all_lats) if all_lats else np.empty(0)
    ages_arr = np.concatenate(all_ages) if all_ages else np.empty(0)
    logger.info("GLM: %d flashes no recorte (%d arquivos lidos)", lons_arr.size, n_read)
    return GLMLightningData(
        lons=lons_arr,
        lats=lats_arr,
        ages_min=ages_arr,
        window_start=window_start,
        window_end=window_end,
        satellite=sat_label,
        n_files=n_read,
        extent=list(extent) if extent is not None else None,
        live=live,
    )
