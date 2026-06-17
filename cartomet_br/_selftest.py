"""Autoteste de imports do CartoMet BR — rede de segurança do empacotamento.

Motivação: num `.exe` PyInstaller, a falha clássica é um módulo ou *data-file*
não embarcado (ex.: o backend `matplotlib.backends.backend_pdf`, carregado por
string, ou o `pint/default_en.txt` que o `metpy.units` lê no import). O sintoma
chega ao usuário como "o programa fecha sozinho ao usar a feature X".

Este módulo importa — e quando preciso *exercita* — tudo que as features
dependem, **dentro do binário congelado**, e relata PASS/FALHA. Rode com:

    CartoMet_BR.exe --selftest

Separação proposital: ``run_checks()`` é puro (sem Qt, testável); ``run_selftest()``
acrescenta o relatório em disco e o diálogo, e devolve o código de saída.
"""

from __future__ import annotations

import contextlib
import importlib
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPORT_NAME = "cartomet_selftest_report.txt"


@dataclass
class CheckResult:
    """Resultado de um item do autoteste."""

    name: str
    feature: str
    required: bool
    ok: bool
    detail: str = ""  # mensagem de erro quando ``ok`` é False


def _imp(modname: str) -> Callable[[], None]:
    """Probe que apenas importa ``modname`` (a maioria dos casos)."""

    def probe() -> None:
        importlib.import_module(modname)

    return probe


def _probe_pint_registry() -> None:
    """Instancia o registro do pint — força o load de ``default_en.txt``.

    É exatamente o data-file cuja ausência derrubaria o ``metpy.units`` no .exe.
    """
    import pint

    pint.UnitRegistry()


def _probe_metpy_units() -> None:
    """Exercita o registro de unidades do MetPy (parse de uma unidade real)."""
    from metpy.units import units

    _ = units("degC")


def _probe_xarray_engines() -> None:
    """Confirma que o xarray descobriu o backend de GRIB por entry-point.

    Depende dos metadados (`.dist-info`) do cfgrib estarem no bundle — o gap que
    o ``copy_metadata`` no .spec resolve.
    """
    import xarray as xr

    engines = xr.backends.list_engines()
    if "cfgrib" not in engines:
        raise RuntimeError(f"engine 'cfgrib' nao registrado; disponiveis: {sorted(engines)}")


# (nome exibido, feature, probe). REQUIRED = sem isto o .exe está quebrado.
_REQUIRED: list[tuple[str, str, Callable[[], None]]] = [
    (
        "matplotlib.backends.backend_pdf",
        "Exportar carta em PDF",
        _imp("matplotlib.backends.backend_pdf"),
    ),
    (
        "matplotlib.backends.backend_svg",
        "Exportar carta em SVG",
        _imp("matplotlib.backends.backend_svg"),
    ),
    (
        "matplotlib.backends.backend_agg",
        "Render headless (Agg)",
        _imp("matplotlib.backends.backend_agg"),
    ),
    (
        "matplotlib.backends.backend_qtagg",
        "Canvas Qt da GUI",
        _imp("matplotlib.backends.backend_qtagg"),
    ),
    ("matplotlib.widgets", "Zoom por retângulo", _imp("matplotlib.widgets")),
    ("matplotlib.offsetbox", "Emojis no mapa", _imp("matplotlib.offsetbox")),
    ("matplotlib.colors", "Rasters categóricos (ZCIT)", _imp("matplotlib.colors")),
    ("matplotlib.image", "Camada de satélite", _imp("matplotlib.image")),
    ("matplotlib.patheffects", "Efeitos de símbolos OMM", _imp("matplotlib.patheffects")),
    ("metpy.calc", "Índices termodinâmicos", _imp("metpy.calc")),
    ("metpy.plots", "Skew-T / Hodógrafa", _imp("metpy.plots")),
    ("metpy.plots.wx_symbols", "Símbolos de tempo (SYNOP/METAR)", _imp("metpy.plots.wx_symbols")),
    ("pint.UnitRegistry()", "Unidades do MetPy (default_en.txt)", _probe_pint_registry),
    ("metpy.units('degC')", "Unidades MetPy exercitadas", _probe_metpy_units),
    ("cfgrib", "Leitura de GRIB", _imp("cfgrib")),
    ("eccodes", "Definições GRIB (eccodes)", _imp("eccodes")),
    ("xarray", "Tensores de dados", _imp("xarray")),
    ("xarray engines (cfgrib)", "Backends de leitura do xarray", _probe_xarray_engines),
    ("netCDF4", "Leitura NetCDF (climatologia/SST)", _imp("netCDF4")),
    (
        "siphon.simplewebservice.wyoming",
        "Radiossonda Wyoming",
        _imp("siphon.simplewebservice.wyoming"),
    ),
    ("pymetdecoder.synop", "Decodificação SYNOP", _imp("pymetdecoder.synop")),
    ("markdown", "Render da metodologia (Ajuda)", _imp("markdown")),
    (
        "statsmodels … lowess",
        "LOWESS do eixo da ZCIT",
        _imp("statsmodels.nonparametric.smoothers_lowess"),
    ),
    ("scipy.ndimage", "Rotulagem de manchas (coerência)", _imp("scipy.ndimage")),
    ("scipy.interpolate", "Interpolação de campos", _imp("scipy.interpolate")),
    ("cartopy.crs", "Projeções do mapa", _imp("cartopy.crs")),
    ("cartopy.feature", "Feições do mapa", _imp("cartopy.feature")),
    ("cartopy.io.shapereader", "Shapefiles (costa/fronteiras)", _imp("cartopy.io.shapereader")),
    ("pyproj", "Transformação de coordenadas", _imp("pyproj")),
    ("ecmwf.opendata", "Download ECMWF Open Data", _imp("ecmwf.opendata")),
    ("PyQt6.QtWidgets", "Interface gráfica", _imp("PyQt6.QtWidgets")),
    ("PyQt6.QtGui", "Interface gráfica", _imp("PyQt6.QtGui")),
    ("PyQt6.QtCore", "Interface gráfica", _imp("PyQt6.QtCore")),
    ("requests", "HTTP (downloads)", _imp("requests")),
    ("certifi", "Certificados SSL", _imp("certifi")),
    ("ssl", "TLS/HTTPS", _imp("ssl")),
]

# OPTIONAL = degradam graciosamente (extra `spatial`); ausência não reprova o .exe.
_OPTIONAL: list[tuple[str, str, Callable[[], None]]] = [
    ("esda.moran", "Coerência Espacial (LISA)", _imp("esda.moran")),
    ("libpysal.weights", "Coerência Espacial (LISA)", _imp("libpysal.weights")),
]


def _run_group(
    items: list[tuple[str, str, Callable[[], None]]], required: bool
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for name, feature, probe in items:
        try:
            probe()
            results.append(CheckResult(name, feature, required, ok=True))
        except Exception as exc:  # noqa: BLE001 — qualquer falha de import/probe é um achado
            results.append(
                CheckResult(
                    name, feature, required, ok=False, detail=f"{type(exc).__name__}: {exc}"
                )
            )
    return results


def run_checks() -> list[CheckResult]:
    """Roda todos os itens (REQUIRED + OPTIONAL). Puro — sem Qt, sem I/O. Testável."""
    return _run_group(_REQUIRED, required=True) + _run_group(_OPTIONAL, required=False)


def format_report(results: list[CheckResult]) -> str:
    """Monta o relatório textual do autoteste."""
    req = [r for r in results if r.required]
    opt = [r for r in results if not r.required]
    req_ok = sum(r.ok for r in req)
    opt_ok = sum(r.ok for r in opt)
    frozen = bool(getattr(sys, "frozen", False))

    lines = [
        "CartoMet BR — Autoteste de imports do empacotamento",
        f"Gerado:  {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"Frozen:  {frozen}   Python: {sys.version.split()[0]}",
        "",
        f"REQUIRED: {req_ok}/{len(req)} OK",
        f"OPTIONAL: {opt_ok}/{len(opt)} OK (LISA/Coerência Espacial)",
        "",
    ]
    for r in results:
        tag = "OK   " if r.ok else "FALHA"
        if not r.required and not r.ok:
            tag = "SKIP "  # opcional ausente é esperado, não é falha
        lines.append(f"[{tag}] {r.name:38s} {r.feature}")
        if not r.ok and r.detail:
            lines.append(f"         -> {r.detail}")
    return "\n".join(lines) + "\n"


def _write_report(text: str) -> Path:
    """Grava o relatório em local gravável. Retorna o caminho efetivo.

    %TEMP% é o destino confiável (instalação em Program Files não permite escrita
    ao lado do exe). Também tenta ao lado do exe, como conveniência (best-effort).
    """
    primary = Path(tempfile.gettempdir()) / REPORT_NAME
    with contextlib.suppress(OSError):
        primary.write_text(text, encoding="utf-8")
    with contextlib.suppress(OSError):
        beside = Path(sys.executable).resolve().parent / REPORT_NAME
        if beside != primary:
            beside.write_text(text, encoding="utf-8")
    return primary


def _show_dialog(results: list[CheckResult], report_path: Path) -> None:
    """Mostra o resumo num QMessageBox (o exe é windowed: sem console)."""
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
    except Exception:  # noqa: BLE001 — sem Qt não há como exibir; relatório em disco basta
        return

    app = QApplication.instance() or QApplication(sys.argv)
    failed = [r for r in results if r.required and not r.ok]
    req_total = sum(r.required for r in results)
    req_ok = sum(r.required and r.ok for r in results)

    box = QMessageBox()
    box.setWindowTitle("CartoMet BR — Autoteste")
    if failed:
        box.setIcon(QMessageBox.Icon.Critical)
        box.setText(f"FALHOU: {len(failed)} módulo(s) essencial(is) ausente(s).")
        box.setInformativeText(
            "Estas features quebrariam no programa:\n"
            + "\n".join(f"• {r.name} — {r.feature}" for r in failed)
        )
    else:
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"Autoteste OK — {req_ok}/{req_total} módulos essenciais presentes.")
        box.setInformativeText("O empacotamento está saudável para distribuição.")
    box.setDetailedText(f"Relatório completo:\n{report_path}\n\n" + format_report(results))
    box.exec()
    del app  # evita warning de QApplication órfã ao sair


def run_selftest(show_dialog: bool = True) -> int:
    """Roda o autoteste, grava o relatório e (opcional) mostra o diálogo.

    Retorna 0 se todos os REQUIRED passaram; 1 caso contrário (código de saída).
    """
    results = run_checks()
    report = format_report(results)
    path = _write_report(report)
    if show_dialog:
        _show_dialog(results, path)
    failed_required = any(r.required and not r.ok for r in results)
    return 1 if failed_required else 0
