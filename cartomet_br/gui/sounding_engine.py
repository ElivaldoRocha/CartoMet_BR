"""
Motor da Sonda Vertical (Radiossondagem / Skew-T) — CartoMet BR v3.0.

Isola TODA a rede (Universidade de Wyoming via ``siphon``) e o cálculo
termodinâmico (``metpy``) numa ``QThread`` dedicada — a GUI nunca congela e nunca
quebra por falha de rede ou sensor (regra inegociável §3 da spec de Radiossondagem).

O servidor de Wyoming é notoriamente instável; cada etapa é blindada por try/except.
Cada índice termodinâmico é calculado isoladamente: uma sonda com sensor de vento
defeituoso não pode derrubar o cálculo do CAPE nem o hodógrafo.

Imports pesados (siphon/metpy) ficam DENTRO de ``run()`` para não atrasar o startup
da janela (mesmo padrão lazy de ``map_canvas.plot_loczcit_raster``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


@dataclass
class SoundingResult:
    """Pacote pronto-para-render entregue à GUI pela thread.

    Os campos ``*_q`` são quantidades pint (com unidade) do MetPy; ``indices`` é uma
    lista de pares (rótulo, texto) já formatados para a tabela inferior do painel.
    Campos que falharam no cálculo entram como None — o painel plota o que houver.
    """

    station: dict
    valid_time: datetime
    station_label: str
    time_label: str
    # Perfis (pint Quantity arrays) — None se a coluna inteira falhou
    pressure_q: object = None
    temperature_q: object = None
    dewpoint_q: object = None
    u_wind_q: object = None
    v_wind_q: object = None
    parcel_q: object = None  # perfil da parcela (parcel_profile), p/ sombrear CAPE/CIN
    has_wind: bool = False
    # Índices termodinâmicos já formatados: [(rótulo, valor_str), ...]
    indices: list = field(default_factory=list)


class SoundingWorker(QThread):
    """Baixa e processa uma radiossondagem fora da thread da GUI.

    Sinais espelham o padrão dos threads de ``download_dialog.py``:
        progress(str), finished_ok(SoundingResult), finished_error(str)
    """

    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object)   # SoundingResult
    finished_error = pyqtSignal(str)

    def __init__(self, station: dict, target_time: datetime, parent=None) -> None:
        super().__init__(parent)
        self.station = station
        self.target_time = target_time

    # ── helper de formatação de índice escalar pint ──────────────────────────
    @staticmethod
    def _fmt(value, unit: str, nd: int = 0) -> str:
        try:
            import numpy as np
            mag = float(np.atleast_1d(value.magnitude)[0]) if hasattr(value, "magnitude") \
                else float(np.atleast_1d(value)[0])
            if np.isnan(mag):
                return "—"
            return f"{mag:.{nd}f} {unit}".strip()
        except Exception:
            return "—"

    def run(self) -> None:  # noqa: C901 — sequência linear de etapas blindadas
        st = self.station
        wmo = st["wmo"]
        name = st.get("name", wmo)
        time_label = self.target_time.strftime("%d/%m/%Y %H:%MZ")
        station_label = f"{name} ({wmo})"

        # ── 1. Rede: Wyoming via siphon ──────────────────────────────────────
        self.progress.emit(f"Buscando sondagem de {station_label}…")
        try:
            from siphon.simplewebservice.wyoming import WyomingUpperAir
            df = WyomingUpperAir.request_data(self.target_time, wmo)
        except Exception as e:  # HTTPError, timeout, ValueError "no data", etc.
            logger.warning("Falha ao baixar sondagem %s @ %s: %s", wmo, time_label, e)
            self.finished_error.emit(
                f"Sem dados de radiossondagem para {station_label} às {time_label}.\n"
                f"O servidor da Universidade de Wyoming pode estar instável ou o balão "
                f"desta estação/horário não foi lançado.\n\nDetalhe técnico: {e}"
            )
            return

        # ── 2. Limpeza + unidades MetPy ──────────────────────────────────────
        try:
            import numpy as np  # noqa: F401
            from metpy.units import units

            df = df.dropna(subset=["pressure", "temperature", "dewpoint"])
            if len(df) < 3:
                self.finished_error.emit(
                    f"Sondagem de {station_label} às {time_label} retornou dados "
                    f"insuficientes para o diagrama."
                )
                return

            pressure = df["pressure"].values * units.hPa
            temperature = df["temperature"].values * units.degC
            dewpoint = df["dewpoint"].values * units.degC
        except Exception as e:
            logger.exception("Falha ao preparar perfis da sondagem %s", wmo)
            self.finished_error.emit(f"Erro ao processar a sondagem: {e}")
            return

        # Vento é opcional (sensor pode falhar em altitude) — não derruba o resto
        u_wind = v_wind = None
        has_wind = False
        try:
            if {"u_wind", "v_wind"}.issubset(df.columns):
                from metpy.units import units
                u_wind = df["u_wind"].values * units("m/s")
                v_wind = df["v_wind"].values * units("m/s")
                has_wind = True
        except Exception as e:
            logger.warning("Vento indisponível na sondagem %s: %s", wmo, e)

        # ── 3. Índices termodinâmicos — cada um isolado ──────────────────────
        self.progress.emit("Calculando índices termodinâmicos…")
        import metpy.calc as mpcalc

        indices: list = []
        parcel = None
        try:
            parcel = mpcalc.parcel_profile(pressure, temperature[0], dewpoint[0]).to("degC")
        except Exception as e:
            logger.warning("parcel_profile falhou (%s): %s", wmo, e)

        def _try(label, fn, unit, nd=0):
            try:
                indices.append((label, self._fmt(fn(), unit, nd)))
            except Exception as e:  # noqa: BLE001
                logger.debug("Índice %s indisponível (%s): %s", label, wmo, e)
                indices.append((label, "—"))

        try:
            cape, cin = mpcalc.surface_based_cape_cin(pressure, temperature, dewpoint)
            indices.append(("CAPE (SB)", self._fmt(cape, "J/kg", 0)))
            indices.append(("CIN (SB)", self._fmt(cin, "J/kg", 0)))
        except Exception as e:
            logger.debug("CAPE/CIN indisponível (%s): %s", wmo, e)
            indices.append(("CAPE (SB)", "—"))
            indices.append(("CIN (SB)", "—"))

        _try("LCL", lambda: mpcalc.lcl(pressure[0], temperature[0], dewpoint[0])[0], "hPa", 0)
        _try("LFC", lambda: mpcalc.lfc(pressure, temperature, dewpoint)[0], "hPa", 0)
        _try("EL", lambda: mpcalc.el(pressure, temperature, dewpoint)[0], "hPa", 0)
        _try("PW", lambda: mpcalc.precipitable_water(pressure, dewpoint), "mm", 1)
        _try("Showalter", lambda: mpcalc.showalter_index(pressure, temperature, dewpoint), "°C", 1)

        # ── 4. Empacota e entrega ────────────────────────────────────────────
        result = SoundingResult(
            station=st,
            valid_time=self.target_time,
            station_label=station_label,
            time_label=time_label,
            pressure_q=pressure,
            temperature_q=temperature,
            dewpoint_q=dewpoint,
            u_wind_q=u_wind,
            v_wind_q=v_wind,
            parcel_q=parcel,
            has_wind=has_wind,
            indices=indices,
        )
        self.progress.emit("Sondagem pronta.")
        self.finished_ok.emit(result)
