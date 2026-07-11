"""Testes para cartomet_br.charts.interactive — interpolação e ferramenta interativa."""

import numpy as np
import pytest

from cartomet_br.charts.interactive import N_INTERP, interpolar_pontos


class TestInterpolarPontos:
    """Testes para a função interpolar_pontos."""

    def test_two_points_returns_n_points(self):
        xs, ys = interpolar_pontos([0, 10], [0, 10])
        assert len(xs) == N_INTERP
        assert len(ys) == N_INTERP

    def test_single_point_returns_same(self):
        xs, ys = interpolar_pontos([5], [5])
        assert len(xs) == 1
        assert xs[0] == pytest.approx(5.0)

    def test_empty_returns_empty(self):
        xs, ys = interpolar_pontos([], [])
        assert len(xs) == 0
        assert len(ys) == 0

    def test_two_points_linear(self):
        xs, ys = interpolar_pontos([0, 10], [0, 0], n=5)
        np.testing.assert_allclose(ys, 0.0, atol=1e-10)
        assert xs[0] == pytest.approx(0.0)
        assert xs[-1] == pytest.approx(10.0)

    def test_custom_n(self):
        xs, ys = interpolar_pontos([0, 5, 10], [0, 5, 0], n=50)
        assert len(xs) == 50
        assert len(ys) == 50

    def test_three_points_smooth(self):
        """3 pontos devem gerar curva suave via spline cúbica."""
        xs, ys = interpolar_pontos([0, 5, 10], [0, 5, 0], n=100)
        assert len(xs) == 100
        # Início e fim devem ser preservados
        assert xs[0] == pytest.approx(0.0, abs=0.1)
        assert xs[-1] == pytest.approx(10.0, abs=0.1)

    def test_duplicate_points_handled(self):
        """Pontos duplicados devem ser filtrados sem crash."""
        xs, ys = interpolar_pontos([0, 0, 10], [0, 0, 10])
        assert len(xs) > 0

    def test_output_type(self):
        xs, ys = interpolar_pontos([0, 5, 10], [0, 5, 0])
        assert isinstance(xs, np.ndarray)
        assert isinstance(ys, np.ndarray)
