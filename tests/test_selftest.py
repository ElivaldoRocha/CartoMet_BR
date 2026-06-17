"""Cobre o autoteste de empacotamento (cartomet_br._selftest) — sem GUI.

Nota sobre o ecCodes: ``cfgrib``/``eccodes`` e o engine cfgrib do xarray dependem
da **biblioteca binária** do ecCodes. No ``.exe`` ela é embarcada pelo ``.spec``
(``ECCODES_DLLS``), mas runners de CI Linux enxutos podem não tê-la — é falha
**de ambiente**, não de empacotamento. Estes testes toleram ESSA lacuna
específica; o gate real para cfgrib/eccodes é o ``CartoMet_BR.exe --selftest``
rodado sobre o artefato. O ``_selftest.py`` de produção permanece **estrito**
(no .exe, "Cannot find the ecCodes library" significa empacotamento quebrado).
"""

from __future__ import annotations

from cartomet_br import _selftest
from cartomet_br._selftest import CheckResult, format_report, run_checks, run_selftest

# REQUIRED que dependem da lib binária do ecCodes (pode faltar fora do .exe).
_ECCODES_BINARY_GAP = {"cfgrib", "eccodes", "xarray engines (cfgrib)"}


def _required_failures(results, *, ignore_eccodes: bool) -> list[CheckResult]:
    out = []
    for r in results:
        if not r.required or r.ok:
            continue
        if ignore_eccodes and r.name in _ECCODES_BINARY_GAP:
            continue
        out.append(r)
    return out


def test_pure_python_required_modules_importable():
    """REQUIRED sem dependência de binário externo importam (backends, metpy, pint, siphon…)."""
    failed = _required_failures(run_checks(), ignore_eccodes=True)
    assert not failed, "REQUIRED (nao-eccodes) falhando no ambiente:\n" + "\n".join(
        f"  {r.name} ({r.feature}) -> {r.detail}" for r in failed
    )


def test_pint_registry_is_exercised():
    """Probe de causa-raiz do pint (default_en.txt) — o gap que derrubaria metpy.units no .exe."""
    by_name = {r.name: r for r in run_checks()}
    assert by_name["pint.UnitRegistry()"].ok, by_name["pint.UnitRegistry()"].detail
    assert by_name["metpy.units('degC')"].ok, by_name["metpy.units('degC')"].detail


def test_run_selftest_returns_zero_when_all_ok(monkeypatch):
    """Lógica do código de saída: todos os REQUIRED ok → 0 (independe do ambiente)."""
    fake = [
        CheckResult("mod_a", "Feature A", required=True, ok=True),
        CheckResult("opt_b", "Feature B", required=False, ok=True),
    ]
    monkeypatch.setattr(_selftest, "run_checks", lambda: fake)
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
