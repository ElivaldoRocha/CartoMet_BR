"""Caracterização do estilo escalar extraído (services/field_style.py).

O refactor tirou a derivação de níveis/cmap/extend de dentro do
``_plot_scalar_contourf`` — estes testes travam o comportamento byte-idêntico
ao bloco original (contrato da carta 2D, agora compartilhado com o globo).
"""

import numpy as np
import pytest
from matplotlib.colors import LinearSegmentedColormap

from cartomet_br.services.field_style import (
    PRECIP_LEVELS,
    derive_scalar_style,
    mask_low_signal,
    precip_dry_floor,
)


class TestMaskLowSignal:
    def test_piso_seco_por_unidade(self):
        v = np.array([0.05, 0.5, 2.0])
        out = mask_low_signal("precip", "mm/3h", v)
        assert np.isnan(out[0]) and out[1] == 0.5
        out_dia = mask_low_signal("era5_precip", "mm/dia", v)
        assert np.isnan(out_dia[0]) and np.isnan(out_dia[1]) and out_dia[2] == 2.0

    def test_piso_de_10_por_cento_do_ens(self):
        v = np.array([5.0, 10.0, 60.0])
        out = mask_low_signal("ens_prob", "%", v)
        assert np.isnan(out[0]) and out[1] == 10.0

    def test_demais_variaveis_intactas(self):
        v = np.array([-5.0, 0.0, 5.0])
        assert mask_low_signal("t", "°C", v) is v


class TestDeriveScalarStyle:
    def test_olr_escala_fixa(self):
        st = derive_scalar_style("olr", "W/m²", np.zeros((3, 3)), {"cmap": "olr_classic"})
        assert np.allclose(st.levels, np.linspace(100, 310, 22))
        assert isinstance(st.cmap, LinearSegmentedColormap)
        assert st.extend == "both"

    def test_ens_prob_fixa_10_a_100_extend_max(self):
        st = derive_scalar_style("ens_prob", "%", np.zeros((3, 3)), {"cmap": "YlGnBu"})
        assert list(st.levels) == list(range(10, 101, 10))
        assert st.extend == "max"

    def test_precip_comeca_no_piso_seco(self):
        st = derive_scalar_style("precip", "mm/3h", np.zeros((3, 3)), {"cmap": "precip_classic"})
        floor = precip_dry_floor("mm/3h")
        assert st.levels[0] == floor
        assert st.levels[1:] == [lv for lv in PRECIP_LEVELS if lv > floor]
        assert st.extend == "max"

    def test_simetrico_90_por_cento_do_extremo(self):
        v = np.array([[-10.0, 0.0], [5.0, 8.0]])
        st = derive_scalar_style("w", "Pa/s", v, {"symmetric": True})
        assert st.levels[0] == pytest.approx(-9.0)
        assert st.levels[-1] == pytest.approx(9.0)
        assert len(st.levels) == 21

    def test_percentil_com_piso_zero_para_isotacas(self):
        v = np.linspace(0.5, 60.0, 100).reshape(10, 10)
        st = derive_scalar_style("wind_speed", "kt", v, {"category": "wind_speed"})
        assert st.levels[0] >= 0.0
        assert len(st.levels) == 21

    def test_campo_constante_nao_quebra(self):
        v = np.full((4, 4), 7.0)
        st = derive_scalar_style("t", "°C", v, {})
        assert st.levels[0] < 7.0 < st.levels[-1]

    def test_fixed_levels_tem_precedencia(self):
        st = derive_scalar_style("t", "°C", np.zeros((3, 3)), {}, fixed_levels=[1, 2, 3])
        assert list(st.levels) == [1, 2, 3]

    def test_alpha_da_carta(self):
        st = derive_scalar_style("t", "°C", np.zeros((3, 3)), {})
        assert st.alpha == pytest.approx(0.85)
