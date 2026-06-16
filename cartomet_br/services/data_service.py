"""
Camada de serviço para operações de dados do CartoMet BR.

Abstrai as chamadas diretas a ecmwf.py, centralizando validação,
logging e gerenciamento de parâmetros. A GUI depende apenas desta
interface, não das funções internas de download/processamento.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from cartomet_br.core.config import Config
from cartomet_br.data.ecmwf import (
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
    load_tcwv,
)

logger = logging.getLogger(__name__)

# Steps válidos do ECMWF Open Data
VALID_STEPS: list[int] = list(range(0, 145, 3)) + list(range(150, 241, 6))


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
    def validate_step(step: int) -> None:
        """Valida se o step está na grade do ECMWF Open Data."""
        if step not in VALID_STEPS:
            closest = min(VALID_STEPS, key=lambda x: abs(x - step))
            raise ValidationError(
                f"Step +{step}h não está disponível no ECMWF Open Data.\n\n"
                f"O ECMWF disponibiliza dados em intervalos de:\n"
                f"  - 3 em 3 horas (0h até 144h)\n"
                f"  - 6 em 6 horas (150h até 240h)\n\n"
                f"Sugestão: use step +{closest}h"
            )

    @staticmethod
    def validate_cycle(cycle: int | None, step: int) -> None:
        """Valida combinação ciclo × step."""
        if cycle is not None and cycle in (6, 18) and step > 144:
            raise ValidationError(
                f"A rodada {cycle:02d}Z tem alcance máximo de +144h.\n\n"
                f"Step +{step}h excede esse limite.\n"
                f"Use a rodada 00Z ou 12Z (alcance até +240h)."
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
        if variable_key in ("olr", "tcwv", "precip", "sst_model", "sst_grad"):
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
        self.validate_step(step)
        self.validate_cycle(cycle, step)

        logger.info(
            "Solicitando dados sinóticos: step=%d, cycle=%s, date=%s",
            step,
            cycle,
            cycle_date,
        )

        try:
            return load_synoptic_data(
                extent=self._config.extent,
                step=step,
                cycle=cycle,
                cycle_date=cycle_date,
                data_dir=self._config.grib_dir,
                smoothing_sigma=self._config.smoothing_sigma,
            )
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
        self.validate_step(step)
        self.validate_cycle(cycle, step)
        self.validate_variable(variable_key)
        self.validate_level(variable_key, level)

        logger.info(
            "Solicitando campo: %s nível=%s step=%d cycle=%s",
            variable_key,
            level,
            step,
            cycle,
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
                    data_dir=self._config.grib_dir,
                    smoothing_sigma=self._config.smoothing_sigma,
                )

            return layer_id, data

        except (ValidationError, DataServiceError):
            raise
        except Exception as exc:
            raise DownloadError(self._format_download_error(exc, step)) from exc

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
        """Estima ciclos ECMWF disponíveis com base no horário UTC."""
        try:
            return estimate_available_cycles()
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
