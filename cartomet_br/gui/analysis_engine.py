"""Workers das análises docadas (CartoMet BR v3.0) — F4/F6/F9.

Cada worker isola rede/CPU pesada numa ``QThread``, emitindo o mesmo trio de
sinais do ``sounding_engine``: ``progress(str)``, ``finished_ok(object)``,
``finished_error(str)``. Imports pesados da camada de dados ficam DENTRO de
``run()`` (lazy) para não atrasar o startup; qualquer falha vira
``finished_error`` — a GUI nunca congela nem quebra por rede/cálculo.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class MeteogramWorker(QThread):
    """Monta a série temporal do IFS num ponto, fora da thread da GUI (F6).

    Baixa step a step (serializado, cache-first, anti-429) e emite um
    ``PointTimeseries`` pronto para o painel.
    """

    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object)  # PointTimeseries
    finished_error = pyqtSignal(str)

    def __init__(
        self,
        lon: float,
        lat: float,
        cycle: int | None,
        cycle_date: str | None,
        data_dir,
        steps=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.lon = float(lon)
        self.lat = float(lat)
        self.cycle = cycle
        self.cycle_date = cycle_date
        self.data_dir = data_dir
        self.steps = steps

    def run(self) -> None:
        try:
            from cartomet_br.data.ecmwf import load_point_timeseries

            ts = load_point_timeseries(
                self.lon,
                self.lat,
                steps=self.steps,
                cycle=self.cycle,
                cycle_date=self.cycle_date,
                data_dir=self.data_dir,
                progress_cb=self.progress.emit,
            )
        except Exception as e:  # rede, GRIB ausente, etc.
            logger.warning("Falha ao montar meteograma (%.2f,%.2f): %s", self.lon, self.lat, e)
            self.finished_error.emit(
                "Não foi possível montar o meteograma neste ponto.\n"
                "Verifique a rodada/step (rede ou cache).\n\n"
                f"Detalhe técnico: {e}"
            )
            return
        self.progress.emit("Meteograma pronto.")
        self.finished_ok.emit(ts)


class CrossSectionWorker(QThread):
    """Monta o corte vertical A→B do IFS, fora da thread da GUI (F4)."""

    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object)  # CrossSection
    finished_error = pyqtSignal(str)

    def __init__(
        self,
        lon_a: float,
        lat_a: float,
        lon_b: float,
        lat_b: float,
        step: int,
        cycle: int | None,
        cycle_date: str | None,
        data_dir,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.lon_a = float(lon_a)
        self.lat_a = float(lat_a)
        self.lon_b = float(lon_b)
        self.lat_b = float(lat_b)
        self.step = step
        self.cycle = cycle
        self.cycle_date = cycle_date
        self.data_dir = data_dir

    def run(self) -> None:
        self.progress.emit("Baixando coluna do modelo e interpolando o corte…")
        try:
            from cartomet_br.data.ecmwf import load_cross_section

            xs = load_cross_section(
                self.lon_a,
                self.lat_a,
                self.lon_b,
                self.lat_b,
                step=self.step,
                cycle=self.cycle,
                cycle_date=self.cycle_date,
                data_dir=self.data_dir,
            )
        except Exception as e:  # rede, GRIB ausente, etc.
            logger.warning("Falha ao montar o corte vertical: %s", e)
            self.finished_error.emit(
                "Não foi possível montar o corte vertical.\n"
                "Verifique a rodada/step (rede ou cache).\n\n"
                f"Detalhe técnico: {e}"
            )
            return
        self.progress.emit("Corte vertical pronto.")
        self.finished_ok.emit(xs)


class InstabilityWorker(QThread):
    """Calcula os campos de instabilidade do IFS (CAPE/CIN/LI/K), fora da GUI (F9).

    CPU pesada (ascensão de parcela no CAPE/LI) → grade engrossada por stride.
    Emite ``{nome: PLFieldData}`` para a MainWindow injetar como camadas.
    """

    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object)  # dict[str, PLFieldData]
    finished_error = pyqtSignal(str)

    def __init__(
        self,
        extent,
        step: int,
        cycle: int | None,
        cycle_date: str | None,
        data_dir,
        indices,
        stride: int = 4,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.extent = list(extent)
        self.step = step
        self.cycle = cycle
        self.cycle_date = cycle_date
        self.data_dir = data_dir
        self.indices = tuple(indices)
        self.stride = int(stride)

    def run(self) -> None:
        try:
            from cartomet_br.data.ecmwf import compute_instability_fields

            fields = compute_instability_fields(
                self.extent,
                step=self.step,
                cycle=self.cycle,
                cycle_date=self.cycle_date,
                data_dir=self.data_dir,
                indices=self.indices,
                coarsen_stride=self.stride,
                progress_cb=self.progress.emit,
            )
        except Exception as e:  # rede, GRIB ausente, etc.
            logger.warning("Falha ao calcular instabilidade: %s", e)
            self.finished_error.emit(
                "Não foi possível calcular os campos de instabilidade.\n"
                "Verifique a rodada/step (rede ou cache).\n\n"
                f"Detalhe técnico: {e}"
            )
            return
        if not fields:
            self.finished_error.emit(
                "Nenhum campo de instabilidade pôde ser calculado neste domínio."
            )
            return
        self.progress.emit("Instabilidade pronta.")
        self.finished_ok.emit(fields)
