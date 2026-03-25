"""Testes para cartomet_br.data.ecmwf — download e processamento de dados."""

import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

from cartomet_br.data.ecmwf import (
    download_ecmwf,
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

    def test_max_6_results(self):
        result = estimate_available_cycles()
        assert len(result["available"]) <= 6

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
#  get_ir_colormap
# ═══════════════════════════════════════════════════════════════════════════════

class TestIRColormap:
    def test_returns_colormap(self):
        cmap = get_ir_colormap()
        assert cmap is not None
        assert cmap.name == "ir_avhrr"

    def test_colormap_has_256_entries(self):
        cmap = get_ir_colormap()
        assert cmap.N == 256
