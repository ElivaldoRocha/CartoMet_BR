"""Avisos Meteorológicos do INMET — camada de contexto (overlay).

Busca os avisos **ativos** da API pública do INMET e os converte em polígonos
plotáveis. Módulo **PURO** (sem Qt/matplotlib): a rede vive em ``fetch_avisos`` e
o parsing testável em ``parse_avisos`` (recebe o JSON já carregado).

É o análogo brasileiro do *SPC Convective Outlook* do MetPy (`PlotGeometry`): um
produto **vetorial pronto** — polígono + cor + severidade — que orienta o traçado
manual da simbologia OMM (*human-in-the-loop*), sem automatizar a decisão.

Fonte: **INMET** (Instituto Nacional de Meteorologia). A API ``apiprevmet3`` é
semi-oficial e não-documentada; o schema pode mudar, então o parsing é defensivo
(``.get`` com defaults, validação de coordenadas) e nunca deixa exceção crua subir
para a GUI. O endpoint devolve os avisos **publicados** no momento, em dois
buckets: ``"hoje"`` (validade em vigor) e ``"futuro"`` (já emitidos, com início de
validade próximo) — ambos entram na lista, com a origem preservada no campo
``quando`` de cada aviso. Não há histórico arbitrário. CRS assumido: WGS84
(lon/lat em graus decimais).
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

INMET_AVISOS_URL = "https://apiprevmet3.inmet.gov.br/avisos/ativos"

_USER_AGENT = "CartoMet-BR (avisos INMET; overlay de contexto)"
_CONNECT_TIMEOUT_S = 10
_READ_TIMEOUT_S = 30

# Cor de preenchimento default quando ``aviso_cor`` faltar (cinza neutro).
_DEFAULT_COLOR = "#9E9E9E"

# ``aviso_cor`` deve ser hex CSS (#RGB/#RRGGBB/#RRGGBBAA). Qualquer outro formato
# (hex sem '#', 'rgb(...)', lixo) cai no default — string crua da API iria direto
# ao Matplotlib e um formato inválido estouraria ValueError dentro do render.
_HEX_COLOR_RE = re.compile(r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})\Z")

# Caixa generosa do Brasil (lon, lat) — descarta geometria absurda/fora de contexto.
_BRASIL_LON = (-90.0, -25.0)
_BRASIL_LAT = (-40.0, 10.0)


class InmetAvisosError(Exception):
    """Falha ao buscar/decodificar os avisos do INMET (mensagem amigável)."""


@dataclass(frozen=True)
class AvisoINMET:
    """Um aviso ativo do INMET, já pronto para plotagem.

    ``rings`` são os anéis **exteriores** (lon, lat) das áreas afetadas — um
    ``Polygon`` vira um anel; um ``MultiPolygon`` vira vários.
    """

    severidade: str
    descricao: str
    cor: str
    riscos: list[str]
    instrucoes: list[str]
    estados: str
    inicio: str
    fim: str
    quando: str  # "hoje" | "futuro"
    rings: list[list[tuple[float, float]]]

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        """Caixa ``(lon_min, lat_min, lon_max, lat_max)`` de todos os anéis."""
        pts = [p for ring in self.rings for p in ring]
        if not pts:
            return None
        lons = [x for x, _ in pts]
        lats = [y for _, y in pts]
        return (min(lons), min(lats), max(lons), max(lats))

    @property
    def label_point(self) -> tuple[float, float] | None:
        """Centroide do anel de maior ÁREA — âncora do rótulo no mapa.

        Área (shoelace), não nº de vértices: num MultiPolygon, um anel pequeno
        porém detalhado (costa recortada) não pode roubar o rótulo do anel
        grande e simples que cobre a área realmente afetada.
        """
        if not self.rings:
            return None
        biggest = max(self.rings, key=_ring_area)
        n = len(biggest)
        if n == 0:
            return None
        return (sum(x for x, _ in biggest) / n, sum(y for _, y in biggest) / n)


def _ring_area(ring: list[tuple[float, float]]) -> float:
    """Área do anel pelo shoelace (graus² — serve só para COMPARAR tamanhos)."""
    n = len(ring)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0


def _clean_ring(raw: Any) -> list[tuple[float, float]] | None:
    """Valida um anel: ≥3 pares (lon,lat) finitos e centroide dentro do Brasil."""
    if not isinstance(raw, list):
        return None
    pts: list[tuple[float, float]] = []
    for p in raw:
        if not (isinstance(p, (list, tuple)) and len(p) >= 2):
            continue
        try:
            lon = float(p[0])
            lat = float(p[1])
        except (TypeError, ValueError):
            continue
        if math.isfinite(lon) and math.isfinite(lat):
            pts.append((lon, lat))
    if len(pts) < 3:
        return None
    clon = sum(x for x, _ in pts) / len(pts)
    clat = sum(y for _, y in pts) / len(pts)
    if not (_BRASIL_LON[0] <= clon <= _BRASIL_LON[1] and _BRASIL_LAT[0] <= clat <= _BRASIL_LAT[1]):
        return None
    return pts


def _extract_rings(poligono: Any) -> list[list[tuple[float, float]]]:
    """Decodifica ``poligono`` (str GeoJSON ou dict) → anéis exteriores limpos."""
    if not poligono:
        return []
    geom: Any = poligono
    if isinstance(geom, str):
        try:
            geom = json.loads(geom)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(geom, dict):
        return []
    gtype = str(geom.get("type", "")).lower()
    coords = geom.get("coordinates")
    if not isinstance(coords, list):
        return []

    raw_rings: list[Any] = []
    if gtype == "polygon":
        # coordinates = [exterior, buraco1, ...]; usamos o anel exterior.
        if coords and isinstance(coords[0], list):
            raw_rings.append(coords[0])
    elif gtype == "multipolygon":
        for poly in coords:
            if isinstance(poly, list) and poly and isinstance(poly[0], list):
                raw_rings.append(poly[0])
    else:
        return []

    cleaned = [_clean_ring(r) for r in raw_rings]
    return [r for r in cleaned if r]


def _coerce_str_list(value: Any) -> list[str]:
    """``riscos``/``instrucoes`` vêm como lista; tolera string única ou vazio."""
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _parse_one(record: Any, quando: str) -> AvisoINMET | None:
    """Converte um registro cru num ``AvisoINMET`` (ou ``None`` se sem geometria)."""
    if not isinstance(record, dict):
        return None
    rings = _extract_rings(record.get("poligono"))
    if not rings:
        return None
    cor_raw = str(record.get("aviso_cor") or "").strip()
    cor = cor_raw if _HEX_COLOR_RE.fullmatch(cor_raw) else _DEFAULT_COLOR
    return AvisoINMET(
        severidade=str(record.get("severidade") or "").strip(),
        descricao=str(record.get("descricao") or "").strip(),
        cor=cor,
        riscos=_coerce_str_list(record.get("riscos")),
        instrucoes=_coerce_str_list(record.get("instrucoes")),
        estados=str(record.get("estados") or "").strip(),
        inicio=str(record.get("inicio") or "").strip(),
        fim=str(record.get("fim") or "").strip(),
        quando=quando,
        rings=rings,
    )


def parse_avisos(payload: Any) -> list[AvisoINMET]:
    """Converte o payload da API (``{"hoje":[...], "futuro":[...]}``) em avisos.

    Função **pura** (sem rede) — o coração testável. Registros sem geometria
    válida são silenciosamente descartados.
    """
    if not isinstance(payload, dict):
        raise InmetAvisosError("Resposta do INMET em formato inesperado (esperava um objeto JSON).")
    out: list[AvisoINMET] = []
    for quando in ("hoje", "futuro"):
        bucket = payload.get(quando)
        if not isinstance(bucket, list):
            continue
        for record in bucket:
            aviso = _parse_one(record, quando)
            if aviso is not None:
                out.append(aviso)
    return out


def avisos_bbox(avisos: list[AvisoINMET]) -> tuple[float, float, float, float] | None:
    """Caixa envolvente ``(lon_min, lat_min, lon_max, lat_max)`` de todos os avisos."""
    boxes = [b for b in (a.bbox for a in avisos) if b is not None]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def fetch_avisos(
    *,
    url: str = INMET_AVISOS_URL,
    timeout: tuple[float, float] = (_CONNECT_TIMEOUT_S, _READ_TIMEOUT_S),
    session: Any = None,
) -> list[AvisoINMET]:
    """Busca os avisos ativos do INMET e devolve a lista pronta.

    Encapsula qualquer falha de rede/decodificação em ``InmetAvisosError`` com
    mensagem amigável — a GUI trata só esse tipo e nunca vê exceção crua.
    """
    import requests

    own_session = session is None
    sess = requests.Session() if own_session else session
    try:
        resp = sess.get(url, timeout=timeout, headers={"User-Agent": _USER_AGENT})
        resp.raise_for_status()
        payload = resp.json()
    except requests.exceptions.Timeout as exc:
        raise InmetAvisosError(
            "Tempo esgotado ao contatar o INMET. Verifique a conexão e tente novamente."
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise InmetAvisosError(f"Não foi possível obter os avisos do INMET: {exc}") from exc
    except ValueError as exc:  # json() inválido
        raise InmetAvisosError("O INMET respondeu num formato inesperado (JSON inválido).") from exc
    finally:
        if own_session:
            sess.close()
    return parse_avisos(payload)
