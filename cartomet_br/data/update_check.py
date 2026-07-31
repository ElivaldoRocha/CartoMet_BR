"""
Verificação de atualização — GitHub Releases do CartoMet BR.

Consulta a release mais recente do repositório oficial e compara com a versão
instalada. Módulo puro (sem PyQt/matplotlib): a GUI consome via worker em
thread (padrão dos avisos INMET). Nenhuma chamada acontece no startup — a
checagem é sempre iniciada pelo usuário (doutrina do app: toda rede é opt-in).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/ElivaldoRocha/CartoMet_BR/releases/latest"
GITHUB_RELEASES_PAGE = "https://github.com/ElivaldoRocha/CartoMet_BR/releases"
_USER_AGENT = "CartoMet-BR-update-check"
_TIMEOUT = (10, 30)  # (conexão, leitura) em segundos


class UpdateCheckError(RuntimeError):
    """Falha amigável na consulta de versão (rede, quota, resposta inválida)."""


@dataclass(frozen=True)
class ReleaseInfo:
    """Release mais recente publicada no GitHub."""

    version: str  # "3.2.0" (tag sem o prefixo v)
    tag: str  # "v3.2.0" como publicado
    url: str  # página da release
    published_at: str  # ISO da API ("" se ausente)


def parse_version(text: str) -> tuple[int, ...]:
    """Tupla numérica de "v3.2.0"/"3.2.0" (ignora sufixos não numéricos)."""
    nums = re.findall(r"\d+", text or "")
    if not nums:
        raise ValueError(f"versão irreconhecível: {text!r}")
    return tuple(int(n) for n in nums[:4])


def is_newer(remote: str, local: str) -> bool:
    """True se `remote` for estritamente mais nova que `local`.

    Tags exóticas (sem dígitos) retornam False — não assustamos o usuário com
    um falso "atualize agora" por causa de uma tag de teste no repositório.
    """
    try:
        a, b = parse_version(remote), parse_version(local)
    except ValueError:
        return False
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)) > b + (0,) * (size - len(b))


def fetch_latest_release(
    url: str = GITHUB_LATEST_RELEASE_URL,
    timeout: tuple[int, int] = _TIMEOUT,
) -> ReleaseInfo:
    """Busca a release mais recente. Erros viram `UpdateCheckError` amigável."""
    import requests

    headers = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.Timeout as exc:
        raise UpdateCheckError(
            "O GitHub demorou demais para responder — tente novamente em instantes."
        ) from exc
    except requests.RequestException as exc:
        raise UpdateCheckError(
            "Sem conexão com o GitHub — verifique a internet e tente novamente."
        ) from exc

    if resp.status_code == 403:
        raise UpdateCheckError(
            "O GitHub limitou as consultas desta rede (HTTP 403). Tente mais tarde."
        )
    if resp.status_code == 404:
        raise UpdateCheckError("Nenhuma release publicada foi encontrada no repositório.")
    try:
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise UpdateCheckError(f"Resposta inesperada do GitHub (HTTP {resp.status_code}).") from exc

    tag = str(data.get("tag_name") or "") if isinstance(data, dict) else ""
    if not tag:
        raise UpdateCheckError("Release sem tag de versão — resposta inesperada do GitHub.")
    return ReleaseInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        url=str(data.get("html_url") or GITHUB_RELEASES_PAGE),
        published_at=str(data.get("published_at") or ""),
    )
