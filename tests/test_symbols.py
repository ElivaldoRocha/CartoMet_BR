"""Testes para cartomet_br.symbols — símbolos meteorológicos WMO."""

import numpy as np
import pytest
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.path import Path
from matplotlib.figure import Figure

from cartomet_br.symbols.base import (
    _FrenteBase,
    make_triangle,
    make_semicircle,
    make_circle,
    draw_filled,
    draw_open,
)
from cartomet_br.symbols.fronts import (
    FrenteFria,
    FrenteQuente,
    FrenteEstacionaria,
    FrenteOclusa,
)
from cartomet_br.symbols.effects import (
    ZCASEffect,
    ZCITEffect,
    CavadoEffect,
    Crista,
    LinhaInstabilidade,
    LinhaSeca,
)
from cartomet_br.symbols import MODOS


# ═══════════════════════════════════════════════════════════════════════════════
#  Formas geométricas
# ═══════════════════════════════════════════════════════════════════════════════

class TestMakeTriangle:
    def test_returns_verts_and_codes(self):
        verts, codes = make_triangle(10)
        assert isinstance(verts, np.ndarray)
        assert isinstance(codes, list)

    def test_verts_shape(self):
        verts, codes = make_triangle(10)
        assert verts.shape == (4, 2)  # 3 vértices + fechamento

    def test_codes_match_verts(self):
        verts, codes = make_triangle(10)
        assert len(codes) == len(verts)
        assert codes[0] == Path.MOVETO
        assert codes[-1] == Path.CLOSEPOLY

    def test_direction_inverts(self):
        verts_up, _ = make_triangle(10, direction=1)
        verts_down, _ = make_triangle(10, direction=-1)
        # Ponto do topo deve inverter
        assert verts_up[2, 1] > 0
        assert verts_down[2, 1] < 0


class TestMakeSemicircle:
    def test_returns_verts_and_codes(self):
        verts, codes = make_semicircle(10)
        assert isinstance(verts, np.ndarray)
        assert isinstance(codes, list)

    def test_codes_match_verts(self):
        verts, codes = make_semicircle(10)
        assert len(codes) == len(verts)

    def test_direction_inverts(self):
        verts_up, _ = make_semicircle(10, direction=1)
        verts_down, _ = make_semicircle(10, direction=-1)
        # Y do meio deve inverter
        mid = len(verts_up) // 2
        assert verts_up[mid, 1] > 0
        assert verts_down[mid, 1] < 0


class TestMakeCircle:
    def test_returns_verts_and_codes(self):
        verts, codes = make_circle(5)
        assert isinstance(verts, np.ndarray)
        assert isinstance(codes, list)

    def test_is_closed(self):
        verts, codes = make_circle(5)
        assert codes[-1] == Path.CLOSEPOLY
        np.testing.assert_allclose(verts[0], verts[-1], atol=1e-10)


# ═══════════════════════════════════════════════════════════════════════════════
#  Classe base _FrenteBase
# ═══════════════════════════════════════════════════════════════════════════════

class TestFrenteBase:
    def test_flip_sign_default(self):
        fb = FrenteFria(flip=False)
        assert fb._flip_sign() == 1

    def test_flip_sign_active(self):
        fb = FrenteFria(flip=True)
        assert fb._flip_sign() == -1

    def test_path_geometry(self):
        verts = np.array([[0, 0], [10, 0], [20, 0]], dtype=float)
        tx, ty, nx, ny, cum_dist, total = _FrenteBase._path_geometry(verts)
        assert total == pytest.approx(20.0)
        assert len(cum_dist) == 3
        np.testing.assert_allclose(tx, [1.0, 1.0, 1.0])
        np.testing.assert_allclose(ty, [0.0, 0.0, 0.0])

    def test_interp_at_midpoint(self):
        verts = np.array([[0, 0], [10, 0]], dtype=float)
        cum_dist = np.array([0.0, 10.0])
        pt, i = _FrenteBase._interp_at(verts, cum_dist, 5.0)
        np.testing.assert_allclose(pt, [5.0, 0.0])
        assert i == 0

    def test_rotate_identity(self):
        xy = np.array([[1, 0]], dtype=float)
        rotated = _FrenteBase._rotate(xy, 0.0)
        np.testing.assert_allclose(rotated, [[1, 0]], atol=1e-10)

    def test_rotate_90_degrees(self):
        xy = np.array([[1, 0]], dtype=float)
        rotated = _FrenteBase._rotate(xy, np.pi / 2)
        np.testing.assert_allclose(rotated, [[0, 1]], atol=1e-10)

    def test_draw_symbol_not_implemented(self):
        """A base deve levantar NotImplementedError se não sobrescrita."""
        fb = _FrenteBase()
        with pytest.raises(NotImplementedError):
            fb._draw_symbol(None, None, None, None, None, None)


# ═══════════════════════════════════════════════════════════════════════════════
#  Frentes (instanciação e rendering via matplotlib)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFrontInstantiation:
    """Testa que todas as classes de frente podem ser instanciadas."""

    @pytest.mark.parametrize("cls,default_color", [
        (FrenteFria, "#1a6faf"),
        (FrenteQuente, "#c0392b"),
        (FrenteEstacionaria, "#6a0dad"),
        (FrenteOclusa, "#8e44ad"),
    ])
    def test_instantiation(self, cls, default_color):
        obj = cls()
        assert obj.color == default_color
        assert obj.linewidth > 0
        assert obj.symbol_size > 0
        assert obj.spacing > 0

    @pytest.mark.parametrize("cls", [
        FrenteFria, FrenteQuente, FrenteOclusa,
    ])
    def test_flip_parameter(self, cls):
        obj = cls(flip=True)
        assert obj.flip is True
        assert obj._flip_sign() == -1

    @pytest.mark.parametrize("cls", [
        FrenteFria, FrenteQuente, FrenteEstacionaria, FrenteOclusa,
    ])
    def test_renders_on_plot(self, cls):
        """Verifica que o path effect pode ser aplicado a um plot sem erro."""
        fig, ax = plt.subplots()
        effect = cls()
        ax.plot([0, 1, 2], [0, 1, 0], path_effects=[effect])
        fig.canvas.draw()
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════════
#  Efeitos meteorológicos
# ═══════════════════════════════════════════════════════════════════════════════

class TestEffectInstantiation:
    """Testa instanciação de todos os efeitos meteorológicos."""

    @pytest.mark.parametrize("cls,default_color", [
        (ZCASEffect, "#008000"),
        (ZCITEffect, "darkorange"),
        (CavadoEffect, "saddlebrown"),
        (Crista, "#005500"),
        (LinhaInstabilidade, "#8B0000"),
        (LinhaSeca, "#b5651d"),
    ])
    def test_instantiation(self, cls, default_color):
        obj = cls()
        assert obj.color == default_color

    @pytest.mark.parametrize("cls", [
        ZCASEffect, ZCITEffect, CavadoEffect,
        Crista, LinhaInstabilidade, LinhaSeca,
    ])
    def test_renders_on_plot(self, cls):
        """Verifica que o path effect pode ser aplicado sem erro."""
        fig, ax = plt.subplots()
        effect = cls()
        ax.plot([0, 1, 2, 3], [0, 1, 0, 1], path_effects=[effect])
        fig.canvas.draw()
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════════
#  MODOS (tabela de modos interativos)
# ═══════════════════════════════════════════════════════════════════════════════

class TestModos:
    """Testa o dicionário MODOS usado pela interface interativa."""

    def test_has_10_modes(self):
        assert len(MODOS) == 10

    def test_keys_are_single_digits(self):
        expected = set("1234567890")
        assert set(MODOS.keys()) == expected

    def test_each_mode_has_required_keys(self):
        for key, modo in MODOS.items():
            assert "nome" in modo, f"Modo '{key}' sem 'nome'"
            assert "cor" in modo, f"Modo '{key}' sem 'cor'"
            assert "tem_flip" in modo, f"Modo '{key}' sem 'tem_flip'"
            assert "efeito" in modo, f"Modo '{key}' sem 'efeito'"

    def test_efeito_is_callable(self):
        for key, modo in MODOS.items():
            effects = modo["efeito"]()
            assert isinstance(effects, list)
            assert len(effects) >= 1

    def test_flip_modes_accept_flip(self):
        for key, modo in MODOS.items():
            if modo["tem_flip"]:
                effects = modo["efeito"](flip=True)
                assert isinstance(effects, list)
