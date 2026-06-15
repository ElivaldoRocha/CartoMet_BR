"""Montagem (pura) dos metadados da carta OMM (F7).

Sem PyQt/Matplotlib — combina o cabeçalho coletado no diálogo com a cronologia
da carta (validade/rodada/step, lida do ``MapCanvas``) e a hora de emissão,
produzindo o dict que ``MapCanvas.render_chart_furniture`` consome. Mantido puro
para ser testável sem ``QApplication``.
"""

from __future__ import annotations

from datetime import UTC, datetime


def format_emission(dt: datetime | None = None) -> str:
    """Hora de emissão em UTC, formato ``dd-mm-AAAA HH:MM`` (default: agora)."""
    dt = dt or datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%d-%m-%Y %H:%M")


def compose_chart_metadata(
    header: dict,
    time_meta: dict,
    emission: datetime | None = None,
) -> dict:
    """Funde cabeçalho do diálogo + cronologia da carta + emissão num só dict.

    ``header`` traz instituição/analista/tipo/logo; ``time_meta`` traz
    validade/rodada/step (de ``MapCanvas.get_chart_time_meta``). Campos ausentes
    viram strings vazias — a renderização decide o que omitir.
    """
    return {
        "institution": (header.get("institution") or "").strip(),
        "analyst": (header.get("analyst") or "").strip(),
        "chart_type": (header.get("chart_type") or "").strip(),
        "logo_path": (header.get("logo_path") or "").strip(),
        "valid_time": time_meta.get("valid_time", "") or "",
        "base_time": time_meta.get("base_time", "") or "",
        "step": time_meta.get("step", 0) or 0,
        "emission": format_emission(emission),
    }
