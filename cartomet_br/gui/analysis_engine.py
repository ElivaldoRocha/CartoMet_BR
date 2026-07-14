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


class WindRoseWorker(QThread):
    """Monta a rosa dos ventos num ponto, fora da thread da GUI.

    ``level_hpa=None`` (default) → vento de 10 m via ``load_point_timeseries``
    (mesmo download cache-first/anti-429 do meteograma); um nível em hPa →
    ``load_point_level_wind_timeseries`` (reusa o GRIB de perfil do Skew-T).
    Bina a série em ``compute_wind_rose`` com as faixas/calmaria configuradas
    e entrega um ``WindRoseResult``. Honestidade: é a distribuição do vento
    PREVISTO ao longo dos steps da rodada — não uma climatologia (o painel
    carrega o badge).
    """

    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object)  # WindRoseResult
    finished_error = pyqtSignal(str)

    def __init__(
        self,
        lon: float,
        lat: float,
        cycle: int | None,
        cycle_date: str | None,
        data_dir,
        steps=None,
        n_sectors: int = 16,
        level_hpa: float | None = None,
        speed_bin_edges=None,
        calm_threshold: float | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.lon = float(lon)
        self.lat = float(lat)
        self.cycle = cycle
        self.cycle_date = cycle_date
        self.data_dir = data_dir
        self.steps = steps
        self.n_sectors = int(n_sectors)
        self.level_hpa = None if level_hpa is None else float(level_hpa)
        self.speed_bin_edges = speed_bin_edges  # None → DEFAULT_SPEED_BINS
        self.calm_threshold = calm_threshold  # None → CALM_THRESHOLD

    def run(self) -> None:
        try:
            from cartomet_br.data.ecmwf import (
                PointLevelWindSeries,
                PointTimeseries,
                load_point_level_wind_timeseries,
                load_point_timeseries,
            )
            from cartomet_br.data.wind_rose import (
                CALM_THRESHOLD,
                DEFAULT_SPEED_BINS,
                WindRoseResult,
                compute_wind_rose,
                level_label,
            )

            ts: PointTimeseries | PointLevelWindSeries
            if self.level_hpa is None:
                ts = load_point_timeseries(
                    self.lon,
                    self.lat,
                    steps=self.steps,
                    cycle=self.cycle,
                    cycle_date=self.cycle_date,
                    data_dir=self.data_dir,
                    progress_cb=self.progress.emit,
                )
            else:
                ts = load_point_level_wind_timeseries(
                    self.lon,
                    self.lat,
                    level_hpa=self.level_hpa,
                    steps=self.steps,
                    cycle=self.cycle,
                    cycle_date=self.cycle_date,
                    data_dir=self.data_dir,
                    progress_cb=self.progress.emit,
                )
            edges = self.speed_bin_edges if self.speed_bin_edges is not None else DEFAULT_SPEED_BINS
            calm = self.calm_threshold if self.calm_threshold is not None else CALM_THRESHOLD
            rose = compute_wind_rose(
                ts.wind_speed,
                ts.wind_dir,
                n_sectors=self.n_sectors,
                speed_bin_edges=edges,
                calm_threshold=calm,
            )
            result = WindRoseResult(
                rose=rose,
                speed=tuple(float(v) for v in ts.wind_speed),
                direction=tuple(float(v) for v in ts.wind_dir),
                lon=self.lon,
                lat=self.lat,
                grid_lon=float(ts.grid_lon),
                grid_lat=float(ts.grid_lat),
                level=level_label(self.level_hpa),
                base_time=ts.base_time,
                steps=tuple(int(s) for s in ts.steps),
            )
        except Exception as e:  # rede, GRIB ausente, etc.
            logger.warning("Falha ao montar rosa dos ventos (%.2f,%.2f): %s", self.lon, self.lat, e)
            self.finished_error.emit(
                "Não foi possível montar a rosa dos ventos neste ponto.\n"
                "Verifique a rodada/step (rede ou cache).\n\n"
                f"Detalhe técnico: {e}"
            )
            return
        self.progress.emit("Rosa dos ventos pronta.")
        self.finished_ok.emit(result)


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


class ConvectiveCellsWorker(QThread):
    """Detecta células convectivas na (sub)grade IR recortada, fora da GUI.

    Recebe a grade JÁ recortada ao extent visível (pelo canvas) e roda a
    detecção vetorizada. CPU isolada: a GUI não trava e o usuário pode cancelar
    (o resultado é abandonado se cancelado). Sem rede.
    """

    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object)  # ConvectiveCellsResult
    finished_error = pyqtSignal(str)

    def __init__(self, data, x, y, *, threshold_c: float, min_area_km2=None, parent=None) -> None:
        super().__init__(parent)
        self.data = data
        self.x = x
        self.y = y
        self.threshold_c = float(threshold_c)
        self.min_area_km2 = min_area_km2

    def run(self) -> None:
        try:
            from cartomet_br.data.convective_cells import (
                DEFAULT_MIN_AREA_KM2,
                detect_convective_cells,
            )

            self.progress.emit("Analisando a área visível…")
            min_area = DEFAULT_MIN_AREA_KM2 if self.min_area_km2 is None else self.min_area_km2
            result = detect_convective_cells(
                self.data,
                self.x,
                self.y,
                threshold_c=self.threshold_c,
                min_area_km2=min_area,
            )
        except Exception as e:  # detecção nunca deve derrubar a GUI
            logger.warning("Falha ao detectar células convectivas: %s", e)
            self.finished_error.emit(
                f"Não foi possível detectar as células convectivas.\n\nDetalhe técnico: {e}"
            )
            return
        self.progress.emit(f"{result.n_kept} célula(s) detectada(s).")
        self.finished_ok.emit(result)
