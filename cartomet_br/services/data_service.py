"""
Camada de serviço para operações de dados do CartoMet BR.

Abstrai as chamadas diretas a ecmwf.py, centralizando validação,
logging e gerenciamento de parâmetros. A GUI depende apenas desta
interface, não das funções internas de download/processamento.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cartomet_br.core.config import Config
from cartomet_br.data.cds_credentials import ERA5_MIN_DELAY_DAYS
from cartomet_br.data.ecmwf import (
    AIFS_VALID_STEPS,
    PL_LEVELS,
    VARIABLE_REGISTRY,
    PLFieldData,
    SatelliteData,
    SynopticData,
    download_goes16_ir,
    estimate_available_cycles,
    load_model_sst,
    load_olr,
    load_pl_variable,
    load_precip,
    load_sst_gradient,
    load_synoptic_data,
    load_t2_extreme,
    load_tcwv,
    variable_available,
)
from cartomet_br.data.era5 import (
    AGG_INDEX_MODES,
    AGG_MODES,
    ERA5_VARIABLES,
    INDEX_RENDER_KEY,
    ERA5Series,
    load_era5_field,
    load_era5_timeseries,
    profile_modes,
)

logger = logging.getLogger(__name__)

# Steps válidos do ECMWF Open Data
VALID_STEPS: list[int] = list(range(0, 145, 3)) + list(range(150, 241, 6))


def valid_steps_for(model: str = "ifs") -> list[int]:
    """Grade de steps do modelo: IFS 3/3 h até 144 + 6/6 até 240; AIFS 6/6 até 360."""
    return AIFS_VALID_STEPS if model == "aifs" else VALID_STEPS


# Variáveis elegíveis para a comparação de rodadas (Δ novo − antigo). Escalares
# com significado direto na diferença; vetores (vento) e acumulados desde o
# step 0 (precip/OLR — janelas de comprimentos diferentes entre rodadas no
# mesmo valid_time) ficam de fora por honestidade física.
RUN_DIFF_VARIABLES: tuple[str, ...] = ("gh", "t", "r", "q", "wind_speed", "tcwv")


class DataServiceError(Exception):
    """Erro genérico da camada de serviço."""


class ValidationError(DataServiceError):
    """Parâmetros inválidos."""


class DownloadError(DataServiceError):
    """Erro de rede ou servidor."""


class DataService:
    """Interface unificada para todas as operações de dados.

    A GUI (e as threads de download) deve usar esta classe ao invés
    de chamar ``load_synoptic_data`` / ``load_pl_variable`` etc.
    diretamente.

    Parameters
    ----------
    config : Config
        Configuração ativa (extent, data_dir, smoothing, …).
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    # ─── propriedades ────────────────────────────────────────────────────

    @property
    def config(self) -> Config:
        return self._config

    @property
    def data_dir(self) -> Path:
        return self._config.data_dir

    @property
    def extent(self) -> list[float]:
        return self._config.extent

    # ─── validação ───────────────────────────────────────────────────────

    @staticmethod
    def validate_step(step: int, model: str = "ifs") -> None:
        """Valida se o step está na grade do modelo (IFS ou AIFS)."""
        steps = valid_steps_for(model)
        if step not in steps:
            closest = min(steps, key=lambda x: abs(x - step))
            grade = (
                "  - 6 em 6 horas (0h até 360h)"
                if model == "aifs"
                else "  - 3 em 3 horas (0h até 144h)\n  - 6 em 6 horas (150h até 240h)"
            )
            nome = "AIFS" if model == "aifs" else "IFS"
            raise ValidationError(
                f"Step +{step}h não está disponível no ECMWF {nome} Open Data.\n\n"
                f"O {nome} disponibiliza dados em intervalos de:\n"
                f"{grade}\n\n"
                f"Sugestão: use step +{closest}h"
            )

    @staticmethod
    def validate_cycle(cycle: int | None, step: int, model: str = "ifs") -> None:
        """Valida combinação ciclo × step (no AIFS todas as rodadas vão a +360h)."""
        if model == "aifs":
            return
        if cycle is not None and cycle in (6, 18) and step > 144:
            raise ValidationError(
                f"A rodada {cycle:02d}Z tem alcance máximo de +144h.\n\n"
                f"Step +{step}h excede esse limite.\n"
                f"Use a rodada 00Z ou 12Z (alcance até +240h)."
            )

    @staticmethod
    def validate_variable_model(variable_key: str, model: str = "ifs") -> None:
        """Bloqueia variáveis sem equivalente no modelo selecionado."""
        if not variable_available(variable_key, model):
            nome = VARIABLE_REGISTRY.get(variable_key, {}).get("nome", variable_key)
            raise ValidationError(
                f"{nome} não está disponível no AIFS.\n\n"
                "O aifs-single não publica r, vo, d (níveis de pressão) nem "
                "OLR (ttr), água precipitável (tcwv), extremos de T 2 m, "
                "precipitação (janela de 6 h ainda não suportada) e TSM do "
                "modelo.\n\nTroque o Modelo para IFS (físico) para usar esta "
                "variável."
            )

    @staticmethod
    def validate_variable(variable_key: str) -> dict:
        """Valida e retorna metadados de uma variável."""
        if variable_key not in VARIABLE_REGISTRY:
            available = ", ".join(sorted(VARIABLE_REGISTRY.keys()))
            raise ValidationError(
                f"Variável '{variable_key}' não encontrada.\nDisponíveis: {available}"
            )
        return VARIABLE_REGISTRY[variable_key]

    @staticmethod
    def validate_level(variable_key: str, level: int | None) -> None:
        """Valida se o nível de pressão é válido para a variável."""
        if variable_key in ("olr", "tcwv", "precip", "sst_model", "sst_grad", "tmax2m", "tmin2m"):
            return  # Variáveis de superfície, sem nível
        if level is None:
            raise ValidationError(f"A variável '{variable_key}' requer um nível de pressão.")
        if level not in PL_LEVELS:
            raise ValidationError(f"Nível {level} hPa não disponível.\nNíveis válidos: {PL_LEVELS}")

    # ─── operações de dados ──────────────────────────────────────────────

    def load_synoptic(
        self,
        step: int,
        cycle: int | None = None,
        cycle_date: str | None = None,
    ) -> SynopticData:
        """Baixa e processa campos sinóticos (PNMM + Espessura).

        Raises
        ------
        ValidationError
            Se step ou cycle forem inválidos.
        DownloadError
            Se o download falhar.
        """
        model = getattr(self._config, "model", "ifs")
        self.validate_step(step, model)
        self.validate_cycle(cycle, step, model)

        logger.info(
            "Solicitando dados sinóticos: step=%d, cycle=%s, date=%s, model=%s",
            step,
            cycle,
            cycle_date,
            model,
        )

        try:
            data = load_synoptic_data(
                extent=self._config.extent,
                step=step,
                cycle=cycle,
                cycle_date=cycle_date,
                data_dir=self._config.grib_dir,
                smoothing_sigma=self._config.smoothing_sigma,
                model=model,
            )
            data.source = "aifs" if model == "aifs" else "ifs"
            return data
        except (ValidationError, DataServiceError):
            raise
        except Exception as exc:
            raise DownloadError(self._format_download_error(exc, step)) from exc

    def load_field(
        self,
        variable_key: str,
        level: int | None,
        step: int,
        cycle: int | None = None,
        cycle_date: str | None = None,
        wind_type: str = "barbs",
        technique: str = "direct",
    ) -> tuple[str, PLFieldData]:
        """Baixa um campo em nível de pressão / OLR / TCWV.

        Returns
        -------
        tuple[str, PLFieldData]
            (layer_id, dados processados)
        """
        model = getattr(self._config, "model", "ifs")
        self.validate_step(step, model)
        self.validate_cycle(cycle, step, model)
        self.validate_variable(variable_key)
        self.validate_variable_model(variable_key, model)
        self.validate_level(variable_key, level)

        logger.info(
            "Solicitando campo: %s nível=%s step=%d cycle=%s model=%s",
            variable_key,
            level,
            step,
            cycle,
            model,
        )

        try:
            if variable_key == "olr":
                layer_id = "olr"
                data = load_olr(
                    extent=self._config.extent,
                    step=step,
                    cycle=cycle,
                    cycle_date=cycle_date,
                    data_dir=self._config.grib_dir,
                    smoothing_sigma=self._config.smoothing_sigma,
                    technique=technique,
                )
            elif variable_key == "precip":
                layer_id = "precip"
                data = load_precip(
                    extent=self._config.extent,
                    step=step,
                    cycle=cycle,
                    cycle_date=cycle_date,
                    data_dir=self._config.grib_dir,
                    smoothing_sigma=self._config.smoothing_sigma,
                    technique=technique,
                )
            elif variable_key == "tcwv":
                layer_id = "tcwv"
                data = load_tcwv(
                    extent=self._config.extent,
                    step=step,
                    cycle=cycle,
                    cycle_date=cycle_date,
                    data_dir=self._config.grib_dir,
                    smoothing_sigma=self._config.smoothing_sigma,
                )
            elif variable_key in ("sst_model", "sst_grad"):
                layer_id = variable_key
                loader = load_model_sst if variable_key == "sst_model" else load_sst_gradient
                data = loader(
                    extent=self._config.extent,
                    step=step,
                    cycle=cycle,
                    cycle_date=cycle_date,
                    data_dir=self._config.grib_dir,
                    smoothing_sigma=self._config.smoothing_sigma,
                )
            elif variable_key in ("tmax2m", "tmin2m"):
                layer_id = variable_key
                data = load_t2_extreme(
                    variable_key,
                    extent=self._config.extent,
                    step=step,
                    cycle=cycle,
                    cycle_date=cycle_date,
                    data_dir=self._config.grib_dir,
                    smoothing_sigma=self._config.smoothing_sigma,
                )
            else:
                if variable_key == "wind":
                    layer_id = f"wind_{level}_{wind_type}"
                else:
                    layer_id = f"{variable_key}_{level}"
                # Ramo de variáveis em nível de pressão: olr/tcwv/sst saem nos
                # ramos acima, e validate_level garante o nível para variáveis PL.
                assert level is not None
                data = load_pl_variable(
                    variable_key=variable_key,
                    level=level,
                    extent=self._config.extent,
                    step=step,
                    cycle=cycle,
                    cycle_date=cycle_date,
                    model=model,
                    data_dir=self._config.grib_dir,
                    smoothing_sigma=self._config.smoothing_sigma,
                )

            data.source = "aifs" if model == "aifs" else "ifs"
            return layer_id, data

        except (ValidationError, DataServiceError):
            raise
        except Exception as exc:
            raise DownloadError(self._format_download_error(exc, step)) from exc

    # ─── Comparação de rodadas (Δ novo − antigo, mesmo valid_time) ──────────

    @staticmethod
    def _resolve_run(cycle: int | None, cycle_date: str | None, model: str = "ifs") -> datetime:
        """Instante-base (aware UTC) da rodada pedida; None = mais recente."""
        if cycle is None or not cycle_date:
            latest = estimate_available_cycles(model)["latest"]
            if latest is None:  # defensivo — a janela deslizante sempre acha
                raise ValidationError("Não foi possível estimar a rodada mais recente.")
            base_latest = latest["base_datetime"]
            assert isinstance(base_latest, datetime)
            return base_latest
        base = datetime.strptime(cycle_date, "%Y%m%d").replace(tzinfo=UTC)
        return base.replace(hour=int(cycle))

    @staticmethod
    def _cycle_label(base: datetime) -> str:
        """Rótulo humano da rodada: "00Z 31/07"."""
        return f"{base:%H}Z {base:%d/%m}"

    def load_run_comparison(
        self,
        variable_key: str,
        level: int | None,
        step: int,
        cycle: int | None = None,
        cycle_date: str | None = None,
        delta_hours: int = 6,
    ) -> tuple[str, PLFieldData]:
        """Campo diferença entre a rodada vigente e uma anterior, no MESMO valid_time.

        Baixa (cache-first) o campo na rodada A (a selecionada/mais recente,
        step ``step``) e na rodada B (``delta_hours`` antes, step
        ``step + delta_hours``) e devolve A − B como ``PLFieldData`` sintético
        (``variable="run_diff"``): Δ > 0 = a rodada nova intensificou o campo.
        Hábito operacional de consistência entre rodadas.
        """
        model = getattr(self._config, "model", "ifs")
        if variable_key not in RUN_DIFF_VARIABLES:
            raise ValidationError(
                f"A comparação de rodadas não está disponível para '{variable_key}'.\n"
                f"Variáveis elegíveis: {', '.join(RUN_DIFF_VARIABLES)}."
            )
        if delta_hours <= 0 or delta_hours % 6 != 0:
            raise ValidationError("A defasagem entre rodadas deve ser múltiplo de 6 h.")
        self.validate_step(step, model)
        self.validate_variable(variable_key)
        self.validate_variable_model(variable_key, model)
        self.validate_level(variable_key, level)

        base_a = self._resolve_run(cycle, cycle_date, model)
        base_b = base_a - timedelta(hours=delta_hours)
        step_b = step + delta_hours

        if step_b not in valid_steps_for(model):
            grade = "6/6 h até 360 h" if model == "aifs" else "3/3 h até 144 h; 6/6 h até 240 h"
            raise ValidationError(
                f"Comparar com a rodada de {delta_hours} h atrás exigiria o step "
                f"+{step_b}h na rodada antiga, que não existe na grade do modelo "
                f"({grade}).\n\n"
                f"Use um step compatível ou outra defasagem."
            )
        self.validate_cycle(base_b.hour, step_b, model)

        cycle_date_a = base_a.strftime("%Y%m%d")
        _lid_a, data_a = self.load_field(variable_key, level, step, base_a.hour, cycle_date_a)
        _lid_b, data_b = self.load_field(
            variable_key, level, step_b, base_b.hour, base_b.strftime("%Y%m%d")
        )

        if data_a.values.shape != data_b.values.shape:
            raise DataServiceError(
                "As grades das duas rodadas não coincidem — limpe o cache de "
                "dados (Arquivo → Limpar dados baixados) e tente novamente."
            )

        var_info = VARIABLE_REGISTRY[variable_key]
        nome = var_info.get("nome", variable_key)
        lbl_a, lbl_b = self._cycle_label(base_a), self._cycle_label(base_b)
        nivel_txt = f" {level} hPa" if level else ""

        diff = PLFieldData(
            values=data_a.values - data_b.values,
            lons=data_a.lons,
            lats=data_a.lats,
            variable="run_diff",
            level=data_a.level,
            unit=data_a.unit,
            valid_time=data_a.valid_time,
            base_time=data_a.base_time,
            step=data_a.step,
            source="ifs",
            extra={
                "run_diff": True,
                "base_var": variable_key,
                "delta_hours": int(delta_hours),
                "cycle_a": lbl_a,
                "cycle_b": lbl_b,
                "title_desc": (f"Δ {nome}{nivel_txt} ({data_a.unit}) — rodada {lbl_a} − {lbl_b}"),
                "entry_label": f"Δ {nome}{nivel_txt}",
                "entry_detail": f"{lbl_a} − {lbl_b}",
            },
        )
        layer_id = f"run_diff_{variable_key}_{level or 0}"
        logger.info(
            "Comparação de rodadas: %s%s | %s(step %d) − %s(step %d)",
            variable_key,
            nivel_txt,
            lbl_a,
            step,
            lbl_b,
            step_b,
        )
        return layer_id, diff

    # ─── Ensemble ENS (51 membros) ───────────────────────────────────────────

    def load_ensemble(
        self,
        product: str,
        threshold_mm: float = 10.0,
        step: int = 24,
        cycle: int | None = None,
        cycle_date: str | None = None,
        progress_callback=None,
        force_download: bool = False,
    ) -> tuple[str, PLFieldData]:
        """Produto do Ensemble ENS: 50 pf (enfo, byte-ranges) + controle (oper).

        ``product``: "prob" (P(R24h > ``threshold_mm``)) ou um campo de
        ``ENS_SPREAD_FIELDS`` ("msl" | "gh500", média ± σ). O controle reusa o
        cache das cartas normais; validação de step/rodada ANTES da rede.
        """
        from cartomet_br.data import ensemble

        model = getattr(self._config, "model", "ifs")
        if model == "aifs":
            raise ValidationError(
                "O Ensemble ENS é um produto do IFS (físico) — o aifs-ens ainda "
                "não é suportado.\n\nTroque o Modelo para IFS (físico)."
            )
        self.validate_step(step, "ifs")
        base = self._resolve_run(cycle, cycle_date, "ifs")
        self.validate_cycle(base.hour, step, "ifs")

        common: dict = {
            "extent": self._config.extent,
            "step": step,
            "cycle": base.hour,
            "cycle_date": base.strftime("%Y%m%d"),
            "data_dir": self._config.grib_dir,
            "source": self._config.ecmwf_source,
            "force_download": force_download,
            "progress_callback": progress_callback,
        }
        if product == "prob":
            data = ensemble.load_ens_prob_precip(threshold_mm, **common)
            layer_id = f"ens_prob_{threshold_mm:g}mm"
        elif product in ensemble.ENS_SPREAD_FIELDS:
            data = ensemble.load_ens_spread(product, **common)
            layer_id = f"ens_spread_{product}"
        else:
            raise ValidationError(
                f"Produto ENS desconhecido: {product!r} "
                f"(use 'prob' ou {', '.join(ensemble.ENS_SPREAD_FIELDS)})."
            )
        logger.info("ENS: produto %s pronto (%s)", product, layer_id)
        return layer_id, data

    # ─── ERA5 (reanálise Copernicus/CDS) ─────────────────────────────────────

    @staticmethod
    def validate_era5_request(
        variable_key: str,
        date_start: str,
        date_end: str,
        hour: int,
        agg: str,
        level: int = 0,
    ) -> None:
        """Valida um pedido ERA5 antes de tocar a rede.

        Raises
        ------
        ValidationError
            Variável/agg desconhecidos, datas malformadas, intervalo invertido,
            hora fora de 0–23, nível ausente/ inválido para variáveis de nível de
            pressão, ou data recente demais (< hoje − atraso do ERA5T).
        """
        var = ERA5_VARIABLES.get(variable_key)
        if var is None:
            raise ValidationError(f"Variável ERA5 desconhecida: {variable_key!r}")
        if agg not in AGG_MODES:
            raise ValidationError(f"Modo de agregação inválido: {agg!r}")
        # Modos de acumulação (somar/total diário) só fazem sentido para chuva.
        if agg in ("soma", "soma_diaria", "max_soma_diaria") and var.agg_profile != "accumulation":
            raise ValidationError("Modos de total (soma) só fazem sentido para precipitação.")
        # Índices de evento só valem para a variável cujo perfil os oferece
        # (defesa em profundidade — a UI já filtra pelo perfil).
        if agg in AGG_INDEX_MODES and agg not in profile_modes(var.agg_profile):
            raise ValidationError(f"O índice {agg!r} não se aplica a {variable_key}.")
        if not 0 <= int(hour) <= 23:
            raise ValidationError(f"Hora fora do intervalo 0–23: {hour}")
        if var.pressure_level and int(level) not in PL_LEVELS:
            raise ValidationError(
                f"{variable_key} exige um nível de pressão válido (hPa). Escolha um de {PL_LEVELS}."
            )
        try:
            d0 = date.fromisoformat(date_start)
            d1 = date.fromisoformat(date_end)
        except ValueError as exc:
            raise ValidationError(f"Data inválida (use AAAA-MM-DD): {exc}") from exc
        if d1 < d0:
            raise ValidationError("A data final é anterior à inicial.")

        latest_ok = datetime.now(UTC).date() - timedelta(days=ERA5_MIN_DELAY_DAYS)
        if d1 > latest_ok:
            raise ValidationError(
                "O ERA5 é reanálise, publicada com ~5 dias de atraso (ERA5T).\n\n"
                f"A data mais recente disponível é {latest_ok.isoformat()}.\n"
                "Escolha um período que termine nessa data ou antes."
            )

    def load_era5_field(
        self,
        variable_key: str,
        date_start: str,
        date_end: str,
        hour: int,
        agg: str,
        level: int = 0,
        thresh: float = 0.0,
    ) -> tuple[str, PLFieldData]:
        """Baixa (cache-first) um campo ERA5 e devolve ``(layer_id, PLFieldData)``.

        O ``layer_id`` é a chave da variável (``era5_t2m``) para campos de
        superfície, ``{chave}_{nível}`` (``era5pl_t_500``) para níveis de pressão,
        e a chave de render do índice (``era5_idx_cdd``) para índices de evento —
        assim índices distintos da MESMA variável-fonte coexistem como camadas.
        ``thresh`` (°C) só é usado pelos índices de dias quentes/onda de calor.
        """
        self.validate_era5_request(variable_key, date_start, date_end, hour, agg, level)
        var = ERA5_VARIABLES[variable_key]
        out_level = int(level) if var.pressure_level else 0
        logger.info(
            "Solicitando ERA5: %s %s→%s %02dZ agg=%s nível=%s",
            variable_key,
            date_start,
            date_end,
            hour,
            agg,
            out_level,
        )
        try:
            data = load_era5_field(
                variable_key=variable_key,
                date_start=date_start,
                date_end=date_end,
                hour=hour,
                agg=agg,
                extent=self._config.extent,
                level=out_level,
                data_dir=self._config.era5_dir,
                smoothing_sigma=self._config.smoothing_sigma,
                thresh=thresh,
            )
        except (ValidationError, DataServiceError):
            raise
        except Exception as exc:
            raise DownloadError(self._format_era5_error(exc)) from exc
        if agg in AGG_INDEX_MODES:
            layer_id = INDEX_RENDER_KEY[agg]
        elif var.pressure_level:
            layer_id = f"{variable_key}_{out_level}"
        else:
            layer_id = variable_key
        return layer_id, data

    def load_era5_series(
        self,
        variable_key: str,
        date_start: str,
        date_end: str,
        lon: float,
        lat: float,
        level: int = 0,
    ) -> ERA5Series:
        """Baixa (cache-first) a série temporal horária de um campo ERA5 num ponto."""
        # Reusa a validação de campo (guarda do ERA5T, nível); a série não agrega,
        # então usa agg="hora" só para passar pela checagem de datas/nível.
        self.validate_era5_request(variable_key, date_start, date_end, 0, "hora", level)
        var = ERA5_VARIABLES[variable_key]
        out_level = int(level) if var.pressure_level else 0
        logger.info(
            "Série ERA5: %s %s→%s @(%.2f, %.2f) nível=%s",
            variable_key,
            date_start,
            date_end,
            lon,
            lat,
            out_level,
        )
        try:
            return load_era5_timeseries(
                variable_key=variable_key,
                date_start=date_start,
                date_end=date_end,
                lon=lon,
                lat=lat,
                level=out_level,
                data_dir=self._config.era5_dir,
            )
        except (ValidationError, DataServiceError):
            raise
        except Exception as exc:
            raise DownloadError(self._format_era5_error(exc)) from exc

    @staticmethod
    def _format_era5_error(exc: Exception) -> str:
        """Traduz erros do CDS/cdsapi para mensagens acionáveis ao usuário."""
        msg = str(exc)
        low = msg.lower()
        if "não configurada" in low or "ausente" in low or "somente-cache" in low:
            return msg  # mensagens de make_cds_client / cache já são instrutivas
        if "licence" in low or "license" in low or "not been accepted" in low:
            return (
                "É preciso aceitar os termos do dataset ERA5 no site do CDS.\n\n"
                "Acesse a página do 'ERA5 hourly data on single levels' em\n"
                "https://cds.climate.copernicus.eu e clique em 'Accept terms'."
            )
        if "401" in msg or "403" in msg or "authoriz" in low or "authentic" in low:
            return (
                "Chave do CDS inválida ou sem permissão (HTTP 401/403).\n\n"
                "Reconfigure em Arquivo → 'Chave ERA5 (CDS)...' e teste a conexão."
            )
        if "connection" in low or "timeout" in low or "ssl" in low:
            return "Erro de conexão com o CDS.\n\nVerifique sua internet e tente novamente."
        return f"Falha ao baixar ERA5:\n{msg}"

    def load_satellite(
        self,
        target_time: datetime | None = None,
        progress_callback=None,
    ) -> SatelliteData:
        """Baixa imagem GOES-East (Banda 13 IR).

        Raises
        ------
        DownloadError
            Se o download falhar.
        """
        logger.info("Solicitando imagem de satélite GOES-East")

        try:
            return download_goes16_ir(
                data_dir=self._config.satellite_dir,
                target_time=target_time,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            raise DownloadError(f"Erro ao baixar imagem de satélite: {exc}") from exc

    def get_available_cycles(self) -> dict:
        """Estima ciclos disponíveis do modelo vigente com base no horário UTC."""
        try:
            return estimate_available_cycles(getattr(self._config, "model", "ifs"))
        except Exception as exc:
            logger.warning("Falha ao estimar ciclos: %s", exc)
            return {"latest": None, "available": []}

    def get_variable_info(self, variable_key: str) -> dict:
        """Retorna metadados de uma variável do registro."""
        return self.validate_variable(variable_key)

    @staticmethod
    def get_available_variables() -> dict[str, dict]:
        """Retorna o registro completo de variáveis."""
        return dict(VARIABLE_REGISTRY)

    @staticmethod
    def get_available_levels() -> list[int]:
        """Retorna níveis de pressão disponíveis."""
        return list(PL_LEVELS)

    @staticmethod
    def generate_layer_id(
        variable_key: str,
        level: int | None = None,
        wind_type: str = "barbs",
    ) -> str:
        """Gera identificador consistente para uma camada."""
        if variable_key in ("olr", "tcwv", "precip", "sst_model", "sst_grad"):
            return variable_key
        if variable_key == "wind":
            return f"wind_{level}_{wind_type}"
        return f"{variable_key}_{level}"

    # ─── utilidades ──────────────────────────────────────────────────────

    @staticmethod
    def _format_download_error(exc: Exception, step: int) -> str:
        """Formata mensagens de erro de download para o usuário."""
        msg = str(exc)

        if "429" in msg or "Too Many Requests" in msg:
            return (
                "Limite de requisições excedido (HTTP 429).\n\n"
                "O servidor ECMWF limita o número de requisições\n"
                "simultâneas por endereço IP.\n\n"
                "O que fazer:\n"
                "  1. Aguarde 2–3 minutos antes de tentar novamente\n"
                "  2. Evite baixar muitas camadas em sequência rápida\n"
                "  3. Se o problema persistir, tente usar outra fonte\n"
                "     (Arquivo → Configurar Diretório de Dados)\n\n"
                "Dica: dados já baixados ficam em cache e não\n"
                "precisam de novo download."
            )
        if "Cannot establish latest" in msg:
            return (
                f"Dados não encontrados para step +{step}h.\n\n"
                f"Possíveis causas:\n"
                f"  - Step inválido (use múltiplos de 3)\n"
                f"  - Dados ainda não publicados pelo ECMWF\n"
                f"  - Problema temporário no servidor\n\n"
                f"Tente novamente com step 0, 3, 6, 9, 12..."
            )
        if "404" in msg or "not found" in msg.lower():
            return (
                f"Arquivo não encontrado no servidor ECMWF (HTTP 404).\n\n"
                f"Verifique se o step +{step}h é válido."
            )
        if "SSL" in msg or "certificate" in msg.lower():
            return (
                "Erro de conexão segura (SSL/TLS).\n\n"
                "Verifique sua conexão com a internet.\n"
                "Se estiver usando proxy/VPN, tente desativar."
            )
        if "connection" in msg.lower() or "timeout" in msg.lower():
            return (
                "Erro de conexão com o servidor ECMWF.\n\nVerifique sua internet e tente novamente."
            )
        if "permission" in msg.lower() or "access" in msg.lower():
            return (
                "Erro de permissão ao salvar arquivos.\n\n"
                "O diretório de dados pode estar protegido.\n"
                "Vá em Arquivo → Configurar Diretório de Dados\n"
                "e escolha uma pasta como Documentos."
            )
        return msg
