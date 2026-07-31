"""Cobre o autoteste de empacotamento (cartomet_br._selftest) — sem GUI.

Cobertura REAL: no dev (Windows: DLL embarcada no wheel do eccodes) e no CI
(Linux/macOS: binário via `eccodeslib`, descoberto pelo `findlibs`), TODOS os
REQUIRED — inclusive cfgrib/eccodes e o engine cfgrib do xarray — devem passar.
É o mesmo conjunto que roda no `.exe` via `--selftest`. Se este teste ficar
verde no CI Linux, está provado que a leitura de GRIB funciona num clone limpo.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

from cartomet_br import _selftest
from cartomet_br._selftest import CheckResult, format_report, run_checks, run_selftest

_HAS_CDSAPI = importlib.util.find_spec("cdsapi") is not None


def test_all_required_modules_importable():
    """Todos os REQUIRED importam/exercitam sem erro (cobre o clone limpo + o .exe)."""
    failed = [r for r in run_checks() if r.required and not r.ok]
    assert not failed, "REQUIRED falhando:\n" + "\n".join(
        f"  {r.name} ({r.feature}) -> {r.detail}" for r in failed
    )


def test_pint_and_grib_stack_are_exercised():
    """Probes de causa-raiz passam: pint (default_en.txt) e os engines do xarray.

    O probe de engines cobre `cfgrib` (GRIB do IFS) E `netcdf4` (imagem GOES das
    células convectivas + arquivos da reanálise ERA5) — ambos descobertos por
    entry-point, o que exige os `.dist-info` no bundle do exe.
    """
    by_name = {r.name: r for r in run_checks()}
    engines = "xarray engines (cfgrib+netcdf4)"
    assert by_name["pint.UnitRegistry()"].ok, by_name["pint.UnitRegistry()"].detail
    assert by_name["metpy.units('degC')"].ok, by_name["metpy.units('degC')"].detail
    assert by_name[engines].ok, by_name[engines].detail


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


@pytest.mark.skipif(not _HAS_CDSAPI, reason="extra 'reanalysis' não instalado (ex.: CI)")
def test_cdsapi_chain_probe_ok_when_extra_installed():
    """Com o cdsapi instalado, a cadeia lazy do Client() importa de verdade.

    Guarda contra typo/rename nos módulos do probe (`ecmwf.datastores.legacy_client`):
    no dev box com `--all-extras` este teste reprova se o probe apontar para um
    módulo inexistente — antes, um erro assim virava SKIP permanente e silencioso.
    """
    by_name = {r.name: r for r in run_checks()}
    row = by_name["cdsapi (+cadeia do Client)"]
    assert row.ok, row.detail


def test_frozen_exe_fails_on_missing_optional(monkeypatch):
    """Num exe congelado, OPTIONAL ausente é defeito de empacotamento (FALHA, exit 1).

    O build de distribuição instala `--all-extras`; se um extra sumiu do bundle,
    o gate de release NÃO pode mostrar exit 0 + "saudável para distribuição".
    """
    fake = [
        CheckResult("ok_req", "Feature Z", required=True, ok=True),
        CheckResult(
            "cdsapi (+cadeia do Client)",
            "Reanálise ERA5 (CDS)",
            required=False,
            ok=False,
            detail="ModuleNotFoundError: No module named 'ecmwf.datastores'",
        ),
    ]
    monkeypatch.setattr(_selftest, "run_checks", lambda: fake)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert run_selftest(show_dialog=False) == 1
    report = format_report(fake)
    assert "FALHA" in report
    assert "SKIP" not in report
