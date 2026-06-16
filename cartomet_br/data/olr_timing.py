"""Triangulação temporal ÚNICA da OLR madura (Técnica B) — fonte da verdade.

Módulo PURO (sem rede, sem GUI, sem xarray) que resolve, para um `valid_time`
desejado, a **rodada-base madura** e o **par de steps acumulados** a desacumular,
mitigando o *spin-up* do IFS. É a única implementação da regra de tempo: tanto o
motor LOCZCIT-PA (`loczcit_pa_engine`) quanto o caminho genérico de campos
(`ecmwf._resolve_accum_window`, técnica "stabilized") delegam aqui (DRY).

Regra de negócio (Blindagem #3 + auditoria de desacumulação/spin-up):

* A rodada-base é a inicializada ``OLR_MATURITY_HOURS`` (12 h) ANTES da rodada
  selecionada — sempre real (12 h é múltiplo do ciclo de 6 h) e madura (≥12 h de
  integração, já fora do *spin-up* das primeiras 3–6 h). Ancorar na rodada (e não
  no ``valid_time``) evita uma rodada-base no FUTURO para previsões (step > 0).
* O step que alcança o ``valid_time`` a partir da base é ``target = step + 12``.
* A janela de desacumulação é de 3 h até 144 h e de 6 h além (resolução do IFS
  Open Data).
* **Anti-404 (P1):** ``target`` e ``target − janela`` são arredondados PARA BAIXO
  à grade publicada do IFS (3 h até 144; 6 h de 150 a 360). Sem isto,
  ``step=141`` (selecionável) → ``target=153`` (não publicado) → HTTP 404. O
  arredondamento para o múltiplo inferior garante um step que o servidor já
  publicou; o pequeno desvio de ``valid_time`` (≤ 3–5 h em horizontes > 6 dias) é
  aceitável para a ZCIT — feição de grande escala e evolução lenta.
* **Robustez (C2):** ``cycle_date`` malformado cai em *fallback* para hoje (UTC),
  logado — nunca derruba o motor com ``ValueError`` silencioso.

Casos canônicos (travados em ``tests/test_olr_deaccum_characterization.py``):

    run 12Z, step 0  → base 00Z (mesmo dia),  steps 12−9   (janela 3 h)
    run 12Z, step 3  → base 00Z (mesmo dia),  steps 15−12  (janela 3 h)
    run 00Z, step 0  → base 12Z (dia anterior), steps 12−9 (janela 3 h)
    run 12Z, step 141 → base 00Z, steps 150−144 (snapping anti-404; janela 6 h)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# Constantes da Técnica B (Blindagem #3). NÃO alterar sem rever a metodologia.
OLR_MATURITY_HOURS: int = 12  # offset da rodada-base (mitiga spin-up)
OLR_WINDOW_SHORT_H: int = 3  # janela de desacumulação até 144 h
OLR_WINDOW_LONG_H: int = 6  # janela além de 144 h (resolução 6/6h do IFS)

# Limite da grade de 3 h do IFS Open Data; além dele, só múltiplos de 6 h.
_IFS_GRID_3H_LIMIT: int = 144


@dataclass(frozen=True)
class OLRPlan:
    """Plano de desacumulação DINÂMICA da OLR: rodada-base + par de steps + Δt."""

    base_cycle: int  # 0, 6, 12 ou 18 (UTC)
    base_date: str  # "YYYYMMDD" da rodada-base
    step_hi: int  # step do acumulado final (alcança o valid_time)
    step_lo: int  # step do acumulado inicial (janela antes)
    window_seconds: float  # Δt REAL entre os steps (10800 s ou 21600 s)


def _snap_to_ifs_grid(step: int) -> int:
    """Arredonda um step à grade temporal do IFS Open Data (3 h até 144 h; 6 h além).

    Pós-144 h o servidor só publica múltiplos de 6 (150, 156, …). Sem isto,
    ``target=153`` (de ``step=141 + 12``) gera HTTP 404. Arredonda PARA BAIXO ao
    múltiplo de 6 mais próximo que exista (nunca cria um step inexistente).
    """
    if step <= _IFS_GRID_3H_LIMIT:
        return step  # grade de 3 h: já válido (soma de 3/12)
    return (step // 6) * 6  # grade de 6 h: ancora no múltiplo inferior


def resolve_olr_window(cycle: int, cycle_date: str, step: int = 0) -> OLRPlan:
    """Resolve (rodada-base, par de steps, Δt) da OLR madura — Técnica B (DRY).

    Parameters
    ----------
    cycle : int          Rodada selecionada (0, 6, 12, 18).
    cycle_date : str     Data da rodada selecionada, "YYYYMMDD".
    step : int           Horizonte de previsão escolhido (0 = análise).

    Returns
    -------
    OLRPlan
        Rodada-base madura, steps acumulados a subtrair e Δt real entre eles.
    """
    try:
        run = datetime.strptime(cycle_date, "%Y%m%d").replace(hour=int(cycle), tzinfo=UTC)
    except (ValueError, TypeError):
        run = datetime.now(UTC).replace(hour=int(cycle), minute=0, second=0, microsecond=0)
        logger.warning(
            "resolve_olr_window: cycle_date '%s' malformado; usando hoje (%s).",
            cycle_date,
            run.strftime("%Y%m%d"),
        )

    base = run - timedelta(hours=OLR_MATURITY_HOURS)  # rodada 12 h antes (real e madura)
    target_raw = int(step) + OLR_MATURITY_HOURS  # alcança o valid_time
    window_h = OLR_WINDOW_SHORT_H if target_raw <= _IFS_GRID_3H_LIMIT else OLR_WINDOW_LONG_H

    step_hi = _snap_to_ifs_grid(target_raw)  # anti-404 (P1)
    step_lo = _snap_to_ifs_grid(target_raw - window_h)
    if step_hi == step_lo:  # janela não-degenerada após snapping
        step_lo = max(step_hi - window_h, 0)

    return OLRPlan(
        base_cycle=base.hour,
        base_date=base.strftime("%Y%m%d"),
        step_hi=step_hi,
        step_lo=step_lo,
        window_seconds=max(step_hi - step_lo, 1) * 3600.0,  # Δt REAL dos steps snapados
    )
