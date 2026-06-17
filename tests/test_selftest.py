"""Cobre o autoteste de empacotamento (cartomet_br._selftest) — sem GUI.

Cobertura REAL: no dev (Windows: DLL embarcada no wheel do eccodes) e no CI
(Linux/macOS: binário via `eccodeslib`, descoberto pelo `findlibs`), TODOS os
REQUIRED — inclusive cfgrib/eccodes e o engine cfgrib do xarray — devem passar.
É o mesmo conjunto que roda no `.exe` via `--selftest`. Se este teste ficar
verde no CI Linux, está provado que a leitura de GRIB funciona num clone limpo.
"""

from __future__ import annotations

from cartomet_br import _selftest
from cartomet_br._selftest import CheckResult, format_report, run_checks, run_selftest


def test_all_required_modules_importable():
    """Todos os REQUIRED importam/exercitam sem erro (cobre o clone limpo + o .exe)."""
    failed = [r for r in run_checks() if r.required and not r.ok]
    assert not failed, "REQUIRED falhando:\n" + "\n".join(
        f"  {r.name} ({r.feature}) -> {r.detail}" for r in failed
    )


def test_pint_and_grib_stack_are_exercised():
    """Probes de causa-raiz passam: pint (default_en.txt) e o engine cfgrib do xarray."""
    by_name = {r.name: r for r in run_checks()}
    assert by_name["pint.UnitRegistry()"].ok, by_name["pint.UnitRegistry()"].detail
    assert by_name["metpy.units('degC')"].ok, by_name["metpy.units('degC')"].detail
    assert by_name["xarray engines (cfgrib)"].ok, by_name["xarray engines (cfgrib)"].detail


def test_run_selftest_ok_with_full_stack():
    """Sem GUI, com a stack completa, o autoteste retorna 0 (sucesso)."""
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
