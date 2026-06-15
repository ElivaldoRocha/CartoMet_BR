"""Testes para cartomet_br.services.data_service — camada de serviço de dados."""

import pytest

from cartomet_br.services.data_service import (
    DataService, ValidationError, DownloadError, VALID_STEPS,
)
from cartomet_br.core.config import Config
from cartomet_br.data.ecmwf import VARIABLE_REGISTRY, PL_LEVELS


class TestValidateStep:
    """Validação de steps do ECMWF."""

    def test_valid_step_zero(self):
        DataService.validate_step(0)

    def test_valid_step_3(self):
        DataService.validate_step(3)

    def test_valid_step_144(self):
        DataService.validate_step(144)

    def test_valid_step_150(self):
        DataService.validate_step(150)

    def test_valid_step_240(self):
        DataService.validate_step(240)

    def test_invalid_step_raises(self):
        with pytest.raises(ValidationError, match="não está disponível"):
            DataService.validate_step(5)

    def test_invalid_step_suggests_closest(self):
        with pytest.raises(ValidationError, match="Sugestão: use step"):
            DataService.validate_step(145)

    def test_all_valid_steps(self):
        for step in VALID_STEPS:
            DataService.validate_step(step)  # Should not raise


class TestValidateCycle:
    """Validação de combinações ciclo × step."""

    def test_cycle_none_always_ok(self):
        DataService.validate_cycle(None, 240)

    def test_cycle_0_step_240_ok(self):
        DataService.validate_cycle(0, 240)

    def test_cycle_12_step_240_ok(self):
        DataService.validate_cycle(12, 240)

    def test_cycle_6_step_144_ok(self):
        DataService.validate_cycle(6, 144)

    def test_cycle_6_step_over_144_raises(self):
        with pytest.raises(ValidationError, match="alcance máximo"):
            DataService.validate_cycle(6, 150)

    def test_cycle_18_step_over_144_raises(self):
        with pytest.raises(ValidationError, match="alcance máximo"):
            DataService.validate_cycle(18, 240)


class TestValidateVariable:
    """Validação de variáveis."""

    def test_valid_variable(self):
        info = DataService.validate_variable("t")
        assert "nome" in info

    def test_valid_wind(self):
        info = DataService.validate_variable("wind")
        assert info is not None

    def test_invalid_variable_raises(self):
        with pytest.raises(ValidationError, match="não encontrada"):
            DataService.validate_variable("invalid_var")

    def test_all_registry_variables_valid(self):
        for key in VARIABLE_REGISTRY:
            info = DataService.validate_variable(key)
            assert "nome" in info


class TestValidateLevel:
    """Validação de níveis de pressão."""

    def test_olr_no_level_ok(self):
        DataService.validate_level("olr", None)

    def test_tcwv_no_level_ok(self):
        DataService.validate_level("tcwv", None)

    def test_temperature_valid_level(self):
        DataService.validate_level("t", 850)

    def test_temperature_no_level_raises(self):
        with pytest.raises(ValidationError, match="requer um nível"):
            DataService.validate_level("t", None)

    def test_invalid_level_raises(self):
        with pytest.raises(ValidationError, match="não disponível"):
            DataService.validate_level("t", 999)

    def test_all_pl_levels_valid(self):
        for level in PL_LEVELS:
            DataService.validate_level("t", level)


class TestGenerateLayerId:
    """Geração de IDs de camada."""

    def test_olr_id(self):
        assert DataService.generate_layer_id("olr") == "olr"

    def test_tcwv_id(self):
        assert DataService.generate_layer_id("tcwv") == "tcwv"

    def test_wind_id(self):
        assert DataService.generate_layer_id("wind", 850, "barbs") == "wind_850_barbs"

    def test_wind_quiver(self):
        assert DataService.generate_layer_id("wind", 250, "quiver") == "wind_250_quiver"

    def test_scalar_id(self):
        assert DataService.generate_layer_id("t", 500) == "t_500"


class TestDataServiceInit:
    """Inicialização do DataService."""

    def test_creates_with_config(self, tmp_path):
        cfg = Config(data_dir=tmp_path, output_dir=tmp_path / "output")
        svc = DataService(cfg)
        assert svc.config is cfg
        assert svc.data_dir == tmp_path
        assert svc.extent == cfg.extent

    def test_get_available_variables(self):
        variables = DataService.get_available_variables()
        assert isinstance(variables, dict)
        assert len(variables) > 0
        assert "wind" in variables

    def test_get_available_levels(self):
        levels = DataService.get_available_levels()
        assert isinstance(levels, list)
        assert 850 in levels
        assert 500 in levels

    def test_get_available_cycles(self, tmp_path):
        cfg = Config(data_dir=tmp_path, output_dir=tmp_path / "output")
        svc = DataService(cfg)
        result = svc.get_available_cycles()
        assert isinstance(result, dict)
        assert "available" in result


class TestFormatDownloadError:
    """Formatação de erros de download."""

    def test_404_error(self):
        msg = DataService._format_download_error(Exception("HTTP 404"), 12)
        assert "404" in msg

    def test_ssl_error(self):
        msg = DataService._format_download_error(Exception("SSL error"), 0)
        assert "SSL" in msg


class TestCacheOnlyRestore:
    """Abrir projeto restaura só do cache: um miss NUNCA dispara rede."""

    def test_load_synoptic_miss_raises_without_network(self, tmp_path):
        from unittest.mock import patch

        from cartomet_br.data.ecmwf import cache_only_mode

        cfg = Config(data_dir=tmp_path, output_dir=tmp_path / "output")
        svc = DataService(cfg)  # grib_dir vazio → cache miss garantido

        with patch("cartomet_br.data.ecmwf.Client") as mock_client_cls:
            with cache_only_mode():  # noqa: SIM117
                with pytest.raises(DownloadError):
                    svc.load_synoptic(step=0, cycle=12, cycle_date="20260614")
            # Garantia central do human-in-the-loop: nenhuma rede tocada.
            mock_client_cls.assert_not_called()

    def test_load_field_miss_raises_without_network(self, tmp_path):
        from unittest.mock import patch

        from cartomet_br.data.ecmwf import cache_only_mode

        cfg = Config(data_dir=tmp_path, output_dir=tmp_path / "output")
        svc = DataService(cfg)

        with patch("cartomet_br.data.ecmwf.Client") as mock_client_cls:
            with cache_only_mode():  # noqa: SIM117
                with pytest.raises(DownloadError):
                    svc.load_field("wind", 850, step=0, cycle=12, cycle_date="20260614")
            mock_client_cls.assert_not_called()

    def test_connection_error(self):
        msg = DataService._format_download_error(Exception("Connection timeout"), 3)
        assert "conexão" in msg.lower()

    def test_permission_error(self):
        msg = DataService._format_download_error(Exception("Permission denied"), 0)
        assert "permissão" in msg.lower()

    def test_cannot_establish_latest(self):
        msg = DataService._format_download_error(
            Exception("Cannot establish latest"), 6
        )
        assert "Dados não encontrados" in msg

    def test_unknown_error_passthrough(self):
        msg = DataService._format_download_error(Exception("something unusual"), 0)
        assert msg == "something unusual"
