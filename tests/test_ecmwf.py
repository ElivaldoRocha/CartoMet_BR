"""Testes para cartomet_br.data.ecmwf — download e processamento de dados."""

import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

from cartomet_br.data.ecmwf import (
    download_ecmwf,
    load_synoptic_data,
    estimate_available_cycles,
    SynopticData,
    PLFieldData,
    SatelliteData,
    VARIABLE_REGISTRY,
    PL_LEVELS,
    CYCLE_SCHEDULE,
    get_ir_colormap,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Dataclasses
# ═══════════════════════════════════════════════════════════════════════════════

class TestSynopticData:
    def test_creation(self):
        data = SynopticData(
            pnmm=np.zeros((10, 10)),
            thickness=np.ones((10, 10)),
            lons=np.linspace(-75, -30, 10),
            lats=np.linspace(-35, 6, 10),
            lon2d=np.zeros((10, 10)),
            lat2d=np.zeros((10, 10)),
            valid_time="2026-03-20T00:00",
            extent=[-75, -35, -30, 6],
        )
        assert data.pnmm.shape == (10, 10)
        assert data.valid_time == "2026-03-20T00:00"
        assert data.base_time == ""
        assert data.step == 0

    def test_with_optional_fields(self):
        data = SynopticData(
            pnmm=np.zeros((5, 5)),
            thickness=np.zeros((5, 5)),
            lons=np.zeros(5),
            lats=np.zeros(5),
            lon2d=np.zeros((5, 5)),
            lat2d=np.zeros((5, 5)),
            valid_time="2026-03-20T12:00",
            extent=[-75, -35, -30, 6],
            base_time="12Z 20/03/2026",
            step=24,
        )
        assert data.base_time == "12Z 20/03/2026"
        assert data.step == 24


class TestPLFieldData:
    def test_creation_scalar(self):
        data = PLFieldData(
            values=np.random.randn(20, 30),
            lons=np.linspace(-75, -30, 30),
            lats=np.linspace(-35, 6, 20),
            variable="t",
            level=850,
            unit="°C",
        )
        assert data.values.shape == (20, 30)
        assert data.u_values is None
        assert data.v_values is None

    def test_creation_wind(self):
        u = np.random.randn(20, 30)
        v = np.random.randn(20, 30)
        ws = np.sqrt(u**2 + v**2)
        data = PLFieldData(
            values=ws,
            lons=np.linspace(-75, -30, 30),
            lats=np.linspace(-35, 6, 20),
            u_values=u,
            v_values=v,
            wind_speed=ws,
            variable="wind",
            level=850,
            unit="kt",
        )
        assert data.u_values is not None
        assert data.v_values is not None
        assert data.wind_speed is not None


class TestSatelliteData:
    def test_creation(self):
        data = SatelliteData(
            data=np.random.randn(100, 100),
            x=np.linspace(-5e6, 5e6, 100),
            y=np.linspace(-5e6, 5e6, 100),
            sat_lon=-75.0,
            sat_h=35786023.0,
            sat_sweep="x",
            time_str="2026-03-20 12:00 UTC",
            filename="goes19_band13.nc",
        )
        assert data.sat_lon == -75.0
        assert data.sat_sweep == "x"


# ═══════════════════════════════════════════════════════════════════════════════
#  VARIABLE_REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

class TestVariableRegistry:
    def test_has_expected_variables(self):
        expected = [
            "gh", "t", "wind", "wind_speed", "w", "q", "r",
            "d", "vo", "olr", "temp_adv", "temp_grad",
            "frontogenesis", "tcwv",
        ]
        for var in expected:
            assert var in VARIABLE_REGISTRY, f"Variável '{var}' ausente"

    def test_each_variable_has_required_fields(self):
        required = ["nome", "param", "unit_raw", "unit_display",
                     "plot_type", "cmap", "symmetric", "category"]
        for key, info in VARIABLE_REGISTRY.items():
            for field in required:
                assert field in info, f"Variável '{key}' sem campo '{field}'"

    def test_param_is_list_of_strings(self):
        for key, info in VARIABLE_REGISTRY.items():
            assert isinstance(info["param"], list)
            assert all(isinstance(p, str) for p in info["param"])

    def test_conversion_callable_or_none(self):
        for key, info in VARIABLE_REGISTRY.items():
            conv = info["conversion"]
            if conv is not None:
                assert callable(conv)

    def test_temperature_conversion(self):
        conv = VARIABLE_REGISTRY["t"]["conversion"]
        assert conv(273.15) == pytest.approx(0.0)
        assert conv(300.0) == pytest.approx(26.85)

    def test_wind_conversion_kt(self):
        conv = VARIABLE_REGISTRY["wind"]["conversion"]
        assert conv(1.0) == pytest.approx(1.94384)

    def test_wind_speed_conversion_kmh(self):
        conv = VARIABLE_REGISTRY["wind_speed"]["conversion"]
        assert conv(1.0) == pytest.approx(3.6)

    def test_omega_conversion(self):
        conv = VARIABLE_REGISTRY["w"]["conversion"]
        assert conv(1.0) == pytest.approx(36.0)

    def test_divergence_conversion(self):
        conv = VARIABLE_REGISTRY["d"]["conversion"]
        assert conv(1.0) == pytest.approx(1e5)


class TestPLLevels:
    def test_has_standard_levels(self):
        for level in [1000, 925, 850, 500, 300, 200]:
            assert level in PL_LEVELS

    def test_sorted_descending(self):
        assert PL_LEVELS == sorted(PL_LEVELS, reverse=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  estimate_available_cycles
# ═══════════════════════════════════════════════════════════════════════════════

class TestEstimateAvailableCycles:
    def test_returns_expected_structure(self):
        result = estimate_available_cycles()
        assert "available" in result
        assert "latest" in result
        assert "next" in result
        assert "utc_now" in result

    def test_available_is_list(self):
        result = estimate_available_cycles()
        assert isinstance(result["available"], list)

    def test_available_entries_have_required_keys(self):
        result = estimate_available_cycles()
        for entry in result["available"]:
            assert "cycle" in entry
            assert "label" in entry
            assert "max_step" in entry
            assert "base_datetime" in entry

    def test_available_sorted_by_datetime(self):
        result = estimate_available_cycles()
        dts = [e["base_datetime"] for e in result["available"]]
        assert dts == sorted(dts, reverse=True)

    def test_max_12_results(self):
        # Janela deslizante = arquivo rotativo do ECMWF (~3 dias = 12 rodadas)
        result = estimate_available_cycles()
        assert len(result["available"]) <= 12

    @patch("cartomet_br.data.ecmwf.datetime")
    def test_at_specific_time(self, mock_dt):
        """Simula um horário conhecido para verificar lógica."""
        # 20 UTC do dia X → rodada 12Z já disponível (publica ~19:30)
        fake_now = datetime(2026, 3, 20, 20, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        result = estimate_available_cycles()
        assert len(result["available"]) > 0


# ═══════════════════════════════════════════════════════════════════════════════
#  CYCLE_SCHEDULE
# ═══════════════════════════════════════════════════════════════════════════════

class TestCycleSchedule:
    def test_has_4_cycles(self):
        assert len(CYCLE_SCHEDULE) == 4

    def test_cycle_hours(self):
        hours = [c["cycle"] for c in CYCLE_SCHEDULE]
        assert set(hours) == {0, 6, 12, 18}

    def test_labels_match_hours(self):
        for c in CYCLE_SCHEDULE:
            assert c["label"] == f"{c['cycle']:02d}Z"


# ═══════════════════════════════════════════════════════════════════════════════
#  download_ecmwf (testa validação, não download real)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDownloadEcmwf:
    def test_reuses_existing_file(self, tmp_path):
        """Se o arquivo já existe, retorna sem baixar."""
        output = tmp_path / "ecmwf_msl_f000.grib2"
        output.write_bytes(b"fake grib data")

        result = download_ecmwf(
            variables=["msl"],
            step=0,
            output_path=output,
            data_dir=tmp_path,
        )
        assert result == output

    def test_none_data_dir_uses_temp(self, tmp_path):
        """data_dir=None deve usar tempdir sem crash."""
        output = tmp_path / "test.grib2"
        output.write_bytes(b"fake")

        result = download_ecmwf(
            variables=["msl"],
            step=0,
            output_path=output,
            data_dir=None,
        )
        assert result == output

    def test_permission_error(self, tmp_path):
        """Testa tratamento de erro de permissão."""
        # Não podemos facilmente simular permissão negada no tmp_path,
        # mas verificamos que o código não crasha com diretórios válidos
        output = tmp_path / "ecmwf_test.grib2"
        output.write_bytes(b"data")

        result = download_ecmwf(
            variables=["t"],
            step=0,
            output_path=output,
            data_dir=tmp_path,
        )
        assert result.exists()


# ═══════════════════════════════════════════════════════════════════════════════
#  Modo somente-cache (abrir projeto NUNCA vai à rede)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCacheOnlyMode:
    def test_hit_returns_path_without_network(self, tmp_path):
        """Arquivo em cache → retorna o caminho, sem tocar a rede."""
        from cartomet_br.data.ecmwf import cache_only_mode
        output = tmp_path / "ecmwf_msl_20260614_12Z_f000.grib2"
        output.write_bytes(b"fake grib data")

        with patch("cartomet_br.data.ecmwf.Client") as mock_client_cls:
            with cache_only_mode():
                result = download_ecmwf(
                    variables=["msl"], step=0, cycle=12,
                    output_path=output, data_dir=tmp_path,
                )
            assert result == output
            mock_client_cls.assert_not_called()  # nenhuma rede

    def test_miss_raises_cache_miss_without_network(self, tmp_path):
        """Cache miss → CacheMissError ANTES de criar o cliente (sem rede)."""
        from cartomet_br.data.ecmwf import CacheMissError, cache_only_mode

        with patch("cartomet_br.data.ecmwf.Client") as mock_client_cls:
            with cache_only_mode():  # noqa: SIM117
                with pytest.raises(CacheMissError):
                    download_ecmwf(
                        variables=["msl"], step=0, cycle=12,
                        output_path=tmp_path / "ausente.grib2", data_dir=tmp_path,
                    )
            mock_client_cls.assert_not_called()

    def test_context_resets_after_exit(self, tmp_path):
        """Fora do contexto, o comportamento normal de cache (reuso) volta."""
        from cartomet_br.data.ecmwf import CacheMissError, _cache_only_active, cache_only_mode

        assert _cache_only_active() is False
        with cache_only_mode():
            assert _cache_only_active() is True
        assert _cache_only_active() is False

        # Um miss fora do contexto NÃO levanta CacheMissError (tentaria rede).
        output = tmp_path / "ja_existe.grib2"
        output.write_bytes(b"x")
        assert download_ecmwf(variables=["t"], step=0,
                              output_path=output, data_dir=tmp_path) == output
        _ = CacheMissError  # referência usada apenas no contexto acima


# ═══════════════════════════════════════════════════════════════════════════════
#  Tradução de exceções (IFS Cycle 50r1 — scda/scwv descontinuados)
# ═══════════════════════════════════════════════════════════════════════════════

class TestExceptionTranslation:
    @patch("cartomet_br.data.ecmwf.Client")
    def test_cannot_establish_latest_explains_50r1(self, mock_client_cls, tmp_path):
        """'Cannot establish latest' deve virar mensagem sobre o IFS 50r1,
        e NÃO a antiga mensagem enganosa sobre 'steps múltiplos de 3'."""
        mock_client = MagicMock()
        # download_ecmwf usa client.retrieve() (levtype sempre definido); cobre ambos
        err = Exception("Cannot establish latest run")
        mock_client.retrieve.side_effect = err
        mock_client.download.side_effect = err
        mock_client_cls.return_value = mock_client

        with pytest.raises(ValueError) as exc_info:
            download_ecmwf(
                variables=["msl"],
                step=0,
                cycle=6,
                output_path=tmp_path / "nao_existe.grib2",
                data_dir=tmp_path,
            )

        msg = str(exc_info.value)
        assert "50r1" in msg
        # Orientação acionável para o usuário final (sem comando pip)
        assert "00Z" in msg or "12Z" in msg
        assert "pip install" not in msg
        assert "múltiplos de 3" not in msg
        # Deve indicar a rodada selecionada
        assert "06Z" in msg

    @patch("cartomet_br.data.ecmwf.Client")
    def test_cannot_establish_latest_without_cycle(self, mock_client_cls, tmp_path):
        """Sem cycle explícito, a mensagem não deve quebrar (sem dica de rodada)."""
        mock_client = MagicMock()
        err = Exception("Cannot establish latest")
        mock_client.retrieve.side_effect = err
        mock_client.download.side_effect = err
        mock_client_cls.return_value = mock_client

        with pytest.raises(ValueError) as exc_info:
            download_ecmwf(
                variables=["msl"],
                step=0,
                cycle=None,
                output_path=tmp_path / "nao_existe.grib2",
                data_dir=tmp_path,
            )
        assert "50r1" in str(exc_info.value)

    @patch("cartomet_br.data.ecmwf.Client")
    def test_404_branch_preserved(self, mock_client_cls, tmp_path):
        """Outros ramos de erro (404) continuam funcionando."""
        mock_client = MagicMock()
        err = Exception("HTTP Error 404: Not Found")
        mock_client.retrieve.side_effect = err
        mock_client.download.side_effect = err
        mock_client_cls.return_value = mock_client

        with pytest.raises(FileNotFoundError):
            download_ecmwf(
                variables=["msl"],
                step=99,
                output_path=tmp_path / "nao_existe.grib2",
                data_dir=tmp_path,
            )


# ═══════════════════════════════════════════════════════════════════════════════
#  get_ir_colormap
# ═══════════════════════════════════════════════════════════════════════════════

class TestDateAnchoring:
    """Regressão do bug do título preso na data de hoje: a data da rodada
    selecionada (`cycle_date`) precisa virar o parâmetro `date` do pedido ECMWF.
    Sem isto, o cliente baixa a rodada MAIS RECENTE daquela hora de ciclo e grava
    sob o nome do dia escolhido — GRIB com data errada → título errado."""

    @patch("cartomet_br.data.ecmwf.Client")
    def test_date_reaches_ecmwf_request(self, mock_client_cls, tmp_path):
        """download_ecmwf(date=...) injeta `date` no pedido client.retrieve()."""
        output = tmp_path / "ecmwf_msl_20260619_06Z_f000.grib2"
        mock_client = MagicMock()
        # retrieve precisa "criar" o arquivo, senão download_ecmwf levanta FileNotFound
        mock_client.retrieve.side_effect = lambda **kw: output.write_bytes(b"fake grib")
        mock_client_cls.return_value = mock_client

        download_ecmwf(
            variables=["msl"], step=0, cycle=6, date="20260619",
            output_path=output, data_dir=tmp_path,
        )

        kwargs = mock_client.retrieve.call_args.kwargs
        assert kwargs["date"] == "20260619"

    @patch("cartomet_br.data.ecmwf.Client")
    def test_no_date_keeps_auto_latest(self, mock_client_cls, tmp_path):
        """date=None NÃO injeta `date` → cliente segue pegando a última rodada."""
        output = tmp_path / "ecmwf_msl_auto_06Z_f000.grib2"
        mock_client = MagicMock()
        mock_client.retrieve.side_effect = lambda **kw: output.write_bytes(b"fake grib")
        mock_client_cls.return_value = mock_client

        download_ecmwf(
            variables=["msl"], step=0, cycle=6, date=None,
            output_path=output, data_dir=tmp_path,
        )

        kwargs = mock_client.retrieve.call_args.kwargs
        assert "date" not in kwargs

    def test_load_synoptic_forwards_cycle_date(self, tmp_path, monkeypatch):
        """load_synoptic_data (a função do print) repassa cycle_date como `date`
        nas chamadas de download — sem isto, o GRIB volta com a data de hoje."""
        calls = []

        class _Stop(Exception):
            pass

        def _recorder(*args, **kwargs):
            calls.append(kwargs)
            raise _Stop  # corta antes do cfgrib/GRIB real

        monkeypatch.setattr("cartomet_br.data.ecmwf.download_ecmwf", _recorder)

        with pytest.raises(_Stop):
            load_synoptic_data(
                extent=[-75, -35, -30, 6],
                step=0,
                cycle=6,
                cycle_date="20260619",
                data_dir=tmp_path,
            )

        assert calls, "download_ecmwf não foi chamado"
        assert calls[0].get("date") == "20260619"


class TestIRColormap:
    def test_returns_colormap(self):
        cmap = get_ir_colormap()
        assert cmap is not None
        assert cmap.name == "ir_avhrr"

    def test_colormap_has_256_entries(self):
        cmap = get_ir_colormap()
        assert cmap.N == 256


# ═══════════════════════════════════════════════════════════════════════════════
#  load_goes_netcdf — leitura SEM REDE (download e restauração de projeto)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadGoesNetcdf:
    """Regressão do bug do GOES sumido ao abrir projeto: as coordenadas precisam
    sair em METROS (radianos × perspective_point_height), senão a projeção
    geoestacionária coloca a imagem num quadro submilimétrico (invisível)."""

    @staticmethod
    def _fake_goes_nc(path, sat_h=35786023.0):
        import xarray as xr

        x_rad = np.array([-0.1, 0.0, 0.1])      # radianos crus, como no arquivo real
        y_rad = np.array([0.05, 0.0, -0.05])
        cmi_k = np.full((3, 3), 250.0)          # Kelvin (topo frio ~ -23 °C)
        ds = xr.Dataset(
            {
                "CMI": (("y", "x"), cmi_k),
                "goes_imager_projection": ((), 0, {
                    "perspective_point_height": sat_h,
                    "longitude_of_projection_origin": -75.0,
                    "sweep_angle_axis": "x",
                }),
                "t": ((), np.datetime64("2026-06-14T12:00", "s").astype("float64")),
            },
            coords={"x": x_rad, "y": y_rad},
        )
        ds.to_netcdf(path, engine="netcdf4")
        return x_rad, y_rad, sat_h

    def test_x_y_converted_radians_to_meters(self, tmp_path):
        from cartomet_br.data.ecmwf import load_goes_netcdf

        path = tmp_path / "OR_ABI-L2-CMIPF-M6C13_G19_s2026.nc"
        x_rad, y_rad, sat_h = self._fake_goes_nc(path)

        data = load_goes_netcdf(path)

        # O cerne do bug: coordenadas em metros, não em radianos crus.
        np.testing.assert_allclose(data.x, x_rad * sat_h)
        np.testing.assert_allclose(data.y, y_rad * sat_h)
        assert abs(data.x).max() > 1e6   # ordem de milhões de metros, não ~0.1

    def test_kelvin_converted_to_celsius_and_metadata(self, tmp_path):
        from cartomet_br.data.ecmwf import load_goes_netcdf

        path = tmp_path / "OR_ABI-L2-CMIPF-M6C13_G19_s2026.nc"
        self._fake_goes_nc(path)

        data = load_goes_netcdf(path)

        np.testing.assert_allclose(data.data, 250.0 - 273.15)   # K → °C
        assert data.filename == path.name                       # basename p/ o cache
        assert data.sat_lon == -75.0 and data.sat_sweep == "x"
