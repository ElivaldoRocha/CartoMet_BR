"""Testes para cartomet_br.core.config."""

import pytest

from cartomet_br.core.config import (
    COLORS,
    EXTENT_AMSUL,
    EXTENT_BRASIL,
    EXTENT_NORDESTE,
    EXTENT_SUDESTE,
    EXTENT_SUL,
    LEVELS,
    Config,
    validate_extent,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  validate_extent
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateExtent:
    """Testes para a função validate_extent."""

    def test_valid_brasil(self):
        validate_extent(EXTENT_BRASIL)

    def test_valid_amsul(self):
        validate_extent(EXTENT_AMSUL)

    def test_valid_all_predefined(self):
        for ext in [EXTENT_BRASIL, EXTENT_AMSUL, EXTENT_NORDESTE, EXTENT_SUDESTE, EXTENT_SUL]:
            validate_extent(ext)

    def test_valid_tuple(self):
        validate_extent((-50, -30, -30, 0))

    def test_invalid_longitude_out_of_range(self):
        with pytest.raises(ValueError, match="Longitudes"):
            validate_extent([-200, -30, -30, 6])

    def test_invalid_latitude_out_of_range(self):
        with pytest.raises(ValueError, match="Latitudes"):
            validate_extent([-75, -100, -30, 6])

    def test_invalid_lon_min_greater_than_lon_max(self):
        with pytest.raises(ValueError, match="lon_min"):
            validate_extent([-30, -35, -75, 6])

    def test_invalid_lat_min_greater_than_lat_max(self):
        with pytest.raises(ValueError, match="lat_min"):
            validate_extent([-75, 6, -30, -35])

    def test_invalid_wrong_length(self):
        with pytest.raises(ValueError, match="4 elementos"):
            validate_extent([-75, -35, -30])

    def test_invalid_not_a_list(self):
        with pytest.raises(ValueError, match="4 elementos"):
            validate_extent("invalid")

    def test_equal_lon_raises(self):
        with pytest.raises(ValueError, match="lon_min"):
            validate_extent([-30, -35, -30, 6])

    def test_equal_lat_raises(self):
        with pytest.raises(ValueError, match="lat_min"):
            validate_extent([-75, 6, -30, 6])


# ═══════════════════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfig:
    """Testes para a dataclass Config."""

    def test_default_config(self, tmp_path):
        config = Config(
            data_dir=tmp_path / "data",
            output_dir=tmp_path / "output",
        )
        assert config.extent == EXTENT_AMSUL
        assert config.dpi == 200
        assert config.smoothing_sigma == 1.5

    def test_for_brazil(self, tmp_path):
        config = Config.for_brazil()
        assert config.extent == EXTENT_BRASIL

    def test_for_south_america(self, tmp_path):
        config = Config.for_south_america()
        assert config.extent == EXTENT_AMSUL

    def test_custom_extent(self, tmp_path):
        ext = [-60, -30, -40, -10]
        config = Config(
            extent=ext,
            data_dir=tmp_path / "data",
            output_dir=tmp_path / "output",
        )
        assert config.extent == ext

    def test_invalid_extent_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            Config(
                extent=[-300, -35, -30, 6],
                data_dir=tmp_path / "data",
                output_dir=tmp_path / "output",
            )

    def test_directories_created(self, tmp_path):
        data = tmp_path / "newdata"
        output = tmp_path / "newoutput"
        Config(data_dir=data, output_dir=output)  # cria os diretórios (efeito colateral)
        assert data.exists()
        assert output.exists()

    def test_projects_dir_auto_created(self, tmp_path):
        """A pasta 'projetos' (trabalho do usuário) é criada ao ser acessada."""
        config = Config(data_dir=tmp_path / "data", output_dir=tmp_path / "out")
        pdir = config.projects_dir
        assert pdir.exists() and pdir.is_dir()
        assert pdir.name == "projetos"
        # Não é cache: não deve aparecer entre as subpastas limpáveis.
        assert "projetos" not in Config.CACHE_SUBDIRS

    def test_custom_dpi(self, tmp_path):
        config = Config(
            dpi=300,
            data_dir=tmp_path / "data",
            output_dir=tmp_path / "output",
        )
        assert config.dpi == 300


# ═══════════════════════════════════════════════════════════════════════════════
#  Constantes
# ═══════════════════════════════════════════════════════════════════════════════


class TestConstants:
    """Testes para constantes de estilo e níveis."""

    def test_colors_has_required_keys(self):
        required = [
            "pnmm_contour",
            "high_pressure",
            "low_pressure",
            "cold_front",
            "warm_front",
            "zcas",
            "zcit",
            "ocean",
            "land",
            "coastline",
        ]
        for key in required:
            assert key in COLORS, f"Chave '{key}' ausente em COLORS"

    def test_levels_has_pnmm_and_thickness(self):
        assert "pnmm" in LEVELS
        assert "thickness" in LEVELS
        for key in ["pnmm", "thickness"]:
            assert "min" in LEVELS[key]
            assert "max" in LEVELS[key]
            assert "step" in LEVELS[key]

    def test_pnmm_levels_reasonable(self):
        assert LEVELS["pnmm"]["min"] >= 900
        assert LEVELS["pnmm"]["max"] <= 1100
        assert LEVELS["pnmm"]["step"] > 0

    def test_thickness_levels_reasonable(self):
        assert LEVELS["thickness"]["min"] >= 4000
        assert LEVELS["thickness"]["max"] <= 6500
        assert LEVELS["thickness"]["step"] > 0

    def test_all_extents_valid(self):
        for ext in [EXTENT_BRASIL, EXTENT_AMSUL, EXTENT_NORDESTE, EXTENT_SUDESTE, EXTENT_SUL]:
            assert len(ext) == 4
            assert ext[0] < ext[2]  # lon_min < lon_max
            assert ext[1] < ext[3]  # lat_min < lat_max
