"""Cobre o autoteste de empacotamento (cartomet_br._selftest) — sem GUI.

No ambiente de dev (com `uv sync --extra spatial`), TODOS os REQUIRED devem
importar; é o mesmo conjunto que roda no `.exe` via `--selftest`. Os testes
também validam a lógica do código de saída e a formatação do relatório.
"""

from __future__ import annotations

from cartomet_br import _selftest
from cartomet_br._selftest import CheckResult, format_report, run_checks, run_selftest


def test_all_required_modules_importable_in_dev():
    """Os módulos essenciais (REQUIRED) precisam importar/exercitar sem erro."""
    results = run_checks()
    failed = [r for r in results if r.required and not r.ok]
    assert not failed, "REQUIRED falhando no dev:\n" + "\n".join(
        f"  {r.name} ({r.feature}) -> {r.detail}" for r in failed
    )


def test_pint_and_xarray_engines_are_exercised():
    """Os probes de causa-raiz (pint registry + engine cfgrib do xarray) passam."""
    by_name = {r.name: r for r in run_checks()}
    assert by_name["pint.UnitRegistry()"].ok, by_name["pint.UnitRegistry()"].detail
    assert by_name["xarray engines (cfgrib)"].ok, by_name["xarray engines (cfgrib)"].detail


def test_run_selftest_ok_in_dev():
    """Sem GUI, com tudo instalado, o autoteste retorna 0 (sucesso)."""
    assert run_selftest(show_dialog=False) == 0


def test_run_selftest_detects_missing_required(monkeypatch):
    """Um REQUIRED ausente deve reprovar o autoteste (exit 1)."""
    fake = [
        CheckResult(
            "modulo_fantasma", "Feature X", required=True, ok=False, detail="ImportError: boom"
        ),
        CheckResult("ok_modulo", "Feature Y", required=True, ok=True),
    ]
    monkeypatch.setattr(_selftest, "run_checks", lambda: fake)
    assert run_selftest(show_dialog=False) == 1


def test_optional_missing_does_not_fail(monkeypatch):
    """Um OPTIONAL ausente não reprova (exit 0) e aparece como SKIP no relatório."""
    fake = [
        CheckResult("ok_req", "Feature Z", required=True, ok=True),
        CheckResult(
            "esda.moran", "Coerência Espacial (LISA)", required=False, ok=False, detail="ausente"
        ),
    ]
    monkeypatch.setattr(_selftest, "run_checks", lambda: fake)
    assert run_selftest(show_dialog=False) == 0
    assert "SKIP" in format_report(fake)


def test_report_has_expected_sections():
    report = format_report(run_checks())
    assert "CartoMet BR — Autoteste" in report
    assert "REQUIRED:" in report
    assert "OPTIONAL:" in report
