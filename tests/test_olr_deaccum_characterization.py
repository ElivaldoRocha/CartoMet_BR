"""Testes de CARACTERIZAÇÃO da triangulação temporal da OLR (Técnica B).

Travam os casos canônicos da desacumulação ANTES da refatoração (Fase 1, que
unifica a lógica em ``cartomet_br/data/olr_timing.py``). Estes valores DEVEM
permanecer idênticos após toda a refatoração — é a âncora anti-regressão exigida
pela L (Limitations) do plano: "zero regressão na desacumulação".

Cobre as DUAS implementações que a Fase 1 unifica numa única função pura:
  • ``plan_olr_deaccumulation`` (motor — ``loczcit_pa_engine``)
  • ``_resolve_accum_window("stabilized", ...)`` (``ecmwf.py``)
e garante que ELAS CONCORDAM (invariante DRY). Ambas são funções puras: sem rede.
"""

import pytest

from cartomet_br.data.ecmwf import _resolve_accum_window
from cartomet_br.data.loczcit_pa_engine import plan_olr_deaccumulation

# ─── Casos documentados — janela madura de 3 h (step ≤ 144) ──────────────────
# Estes NÃO mudam na Fase 1 (o snapping pós-144h não os afeta). São a âncora.
#   cycle, date,        step, base_cycle, base_date,    step_hi, step_lo, Δt
CANONICAL = [
    (12, "20260531", 0, 0, "20260531", 12, 9, 10800.0),  # run 12Z análise → base 00Z, 12−9
    (12, "20260531", 3, 0, "20260531", 15, 12, 10800.0),  # run 12Z +3h     → base 00Z, 15−12
    (0, "20260531", 0, 12, "20260530", 12, 9, 10800.0),  # run 00Z análise → base 12Z ontem
]


class TestEngineCanonical:
    """Motor LOCZCIT-PA: ``plan_olr_deaccumulation``."""

    @pytest.mark.parametrize("cyc,date,step,bc,bd,hi,lo,dt", CANONICAL)
    def test_plan_known_cases(self, cyc, date, step, bc, bd, hi, lo, dt):
        p = plan_olr_deaccumulation(cyc, date, step)
        assert p.base_cycle == bc
        assert p.base_date == bd
        assert (p.step_hi, p.step_lo) == (hi, lo)
        assert p.window_seconds == dt


class TestEcmwfStabilizedCanonical:
    """Caminho genérico ``ecmwf.py`` (Técnica B): ``_resolve_accum_window``."""

    @pytest.mark.parametrize("cyc,date,step,bc,bd,hi,lo,dt", CANONICAL)
    def test_resolve_window_known_cases(self, cyc, date, step, bc, bd, hi, lo, dt):
        rc, rd, shi, slo, label = _resolve_accum_window("stabilized", cyc, date, step)
        assert (rc, rd) == (bc, bd)
        assert (shi, slo) == (hi, lo)
        assert "Estabilizada" in label


class TestDirectUnchanged:
    """O ramo 'direct' (chuva sinótica) NÃO é tocado pela refatoração."""

    def test_direct_window_is_selected_run(self):
        rc, rd, hi, lo, label = _resolve_accum_window("direct", 12, "20260531", 12)
        assert (rc, rd, hi, lo) == (12, "20260531", 12, 9)  # janela [step-3, step] da rodada
        assert "Direta" in label

    def test_direct_step0_nao_fica_negativo(self):
        _, _, hi, lo, _ = _resolve_accum_window("direct", 0, "20260531", 0)
        assert (hi, lo) == (0, 0)  # step_lo travado em 0


class TestImplementationsAgree:
    """Invariante DRY: motor e ecmwf-estabilizado produzem a MESMA janela.

    Vale antes (duas cópias idênticas) e depois (ambos delegam à fonte única) da
    Fase 1 — incl. casos pós-144h, onde ambos passam a snapar de forma idêntica.
    """

    @pytest.mark.parametrize(
        "cyc,date,step",
        [
            (12, "20260531", 0),
            (12, "20260531", 3),
            (0, "20260531", 0),
            (6, "20260531", 6),
            (18, "20260101", 24),  # vira o ano no recuo de 12 h
            (0, "20260301", 48),  # vira o mês (fev→mar)
            (12, "20260531", 138),  # janela de 6 h (target=150)
        ],
    )
    def test_engine_matches_ecmwf(self, cyc, date, step):
        p = plan_olr_deaccumulation(cyc, date, step)
        rc, rd, hi, lo, _ = _resolve_accum_window("stabilized", cyc, date, step)
        assert (p.base_cycle, p.base_date, p.step_hi, p.step_lo) == (rc, rd, hi, lo)


# Grade publicada do IFS Open Data: 3 h até 144 h, depois 6 h até 360 h.
_IFS_GRID = set(range(0, 145, 3)) | set(range(150, 361, 6))


class TestPost144Snapping:
    """[P1] target = step + 12 fora da grade pós-144h é arredondado (anti-404).

    Antes da Fase 1, ``step=135/141`` (selecionáveis) geravam ``target=147/153``,
    que o servidor não publica → HTTP 404. O snapping ancora no múltiplo de 6
    inferior, que existe.
    """

    def test_step_141_snaps_to_150_144(self):
        """Exemplo do plano: step 141 → target 153 → snap 150−144 (janela 6 h)."""
        p = plan_olr_deaccumulation(0, "20260531", 141)
        assert (p.step_hi, p.step_lo) == (150, 144)
        assert p.window_seconds == 21600.0

    def test_step_135_snaps_onto_grid(self):
        """Outro caso da auditoria: step 135 → target 147 → snap p/ a grade."""
        p = plan_olr_deaccumulation(12, "20260531", 135)
        assert p.step_hi <= 150 and p.step_hi in _IFS_GRID
        assert p.step_lo in _IFS_GRID
        assert p.step_hi > p.step_lo

    @pytest.mark.parametrize("step", [132, 135, 138, 141, 144, 150, 156, 240])
    def test_resolved_steps_always_on_grid(self, step):
        """Nenhum step resolvido cai fora da grade publicada (motor e ecmwf)."""
        p = plan_olr_deaccumulation(0, "20260531", step)
        assert p.step_hi in _IFS_GRID, f"step_hi {p.step_hi} fora da grade"
        assert p.step_lo in _IFS_GRID, f"step_lo {p.step_lo} fora da grade"
        rc, rd, hi, lo, _ = _resolve_accum_window("stabilized", 0, "20260531", step)
        assert (hi, lo) == (p.step_hi, p.step_lo)  # delegação consistente
