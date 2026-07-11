"""Testes do filtro de Coerência Espacial (LISA / Moran Local).

Cobre:
  • caminho real (esda/libpysal): a banda contígua sobrevive, células órfãs são
    descartadas, e o resultado é reprodutível (seed fixo);
  • ausência de dependências → ``SpatialDepsMissing`` (sem travar);
  • o motor ``build_raster(filter_method="coherence")`` cai no IQR quando as deps
    faltam, sem crash (blindagem da GUI).

As deps (esda, libpysal) são OPCIONAIS (extra ``spatial``); os testes do caminho
real são pulados se não instaladas.
"""

import importlib.util
import sys

import numpy as np
import pytest

from cartomet_br.data.loczcit_pa_engine import NormalizedForcings as NF
from cartomet_br.data.loczcit_pa_engine import build_raster
from cartomet_br.data.spatial_coherence import SpatialDepsMissing, coherence_mask

HAVE_SPATIAL = (
    importlib.util.find_spec("esda") is not None
    and importlib.util.find_spec("libpysal") is not None
)
needs_spatial = pytest.mark.skipif(
    not HAVE_SPATIAL, reason="extra 'spatial' (esda/libpysal) não instalado"
)


def _synthetic_band_with_orphans():
    """I_ZCIT com banda central contígua (envelope ZCIT) + 2 células órfãs altas."""
    rng = np.random.default_rng(0)
    nlat, nlon = 40, 60
    izcit = 0.1 + 0.02 * rng.standard_normal((nlat, nlon))
    izcit[18:23, :] = 0.9 + 0.02 * rng.standard_normal((5, nlon))  # banda ~300 px
    izcit[3, 5] = 0.95  # órfã 1 (longe)
    izcit[35, 50] = 0.95  # órfã 2 (longe)
    return izcit


# ═══════════════════════════════════════════════════════════════════════════════
#  Caminho real (LISA) — requer esda/libpysal
# ═══════════════════════════════════════════════════════════════════════════════


@needs_spatial
class TestCoherenceMaskReal:
    def test_band_survives_orphans_dropped(self):
        izcit = _synthetic_band_with_orphans()
        mask = coherence_mask(izcit, min_pixels=30, permutations=199, seed=42)
        assert mask.dtype == bool
        assert mask.shape == izcit.shape
        # A banda central (rows 18-22) é mantida integralmente
        assert mask[18:23, :].all()
        # As células órfãs (isoladas) são descartadas pelo isolamento morfológico
        assert not mask[3, 5]
        assert not mask[35, 50]

    def test_reproducible_with_seed(self):
        izcit = _synthetic_band_with_orphans()
        a = coherence_mask(izcit, min_pixels=30, permutations=199, seed=42)
        b = coherence_mask(izcit, min_pixels=30, permutations=199, seed=42)
        assert np.array_equal(a, b)

    def test_insufficient_valid_pixels_returns_empty(self):
        izcit = np.full((20, 20), np.nan)
        izcit[0, 0] = 0.9  # só 1 pixel válido → amostra insuficiente
        mask = coherence_mask(izcit, min_pixels=50)
        assert mask.dtype == bool
        assert not mask.any()

    def test_constant_field_returns_empty(self):
        # Campo finito mas constante → variância ~0 → sem hotspots
        mask = coherence_mask(np.full((30, 30), 0.5), min_pixels=10, permutations=99)
        assert not mask.any()


# ═══════════════════════════════════════════════════════════════════════════════
#  Dependências ausentes — não trava
# ═══════════════════════════════════════════════════════════════════════════════


class TestDepsMissing:
    def test_coherence_mask_raises_spatial_deps_missing(self, monkeypatch):
        """Sem esda no ambiente, ``coherence_mask`` levanta ``SpatialDepsMissing``."""
        monkeypatch.setitem(sys.modules, "esda", None)
        monkeypatch.setitem(sys.modules, "esda.moran", None)
        izcit = np.random.default_rng(0).random((20, 20))
        with pytest.raises(SpatialDepsMissing):
            coherence_mask(izcit)

    def test_build_raster_coherence_falls_back_to_iqr(self, monkeypatch):
        """``filter_method='coherence'`` sem deps → cai no IQR, sem crash."""
        import cartomet_br.data.spatial_coherence as sc

        def _boom(*_a, **_k):
            raise sc.SpatialDepsMissing("deps ausentes (teste)")

        monkeypatch.setattr(sc, "coherence_mask", _boom)

        shape = (20, 30)
        rng = np.random.default_rng(1)
        nf = NF(
            tsm_n=rng.random(shape),
            conv_n=rng.random(shape),
            olr_n_inv=rng.random(shape),
            olr_abs=rng.uniform(150, 260, shape),
            lons=np.linspace(-55, 15, 30),
            lats=np.linspace(-15, 15, 20),
        )
        raster, izcit, _active = build_raster(nf, filter_method="coherence")
        # Não levantou exceção: caiu na máscara ativa morfológica, categorias válidas
        vals = raster[np.isfinite(raster)]
        assert set(np.unique(vals)).issubset({0.0, 1.0, 2.0, 3.0})
        assert izcit.shape == shape


# ═══════════════════════════════════════════════════════════════════════════════
#  Integração no motor (caminho real) — coerência produz raster categórico
# ═══════════════════════════════════════════════════════════════════════════════


@needs_spatial
class TestBuildRasterCoherence:
    def test_coherence_method_produces_categorical_raster(self):
        nlat, nlon = 40, 60
        rng = np.random.default_rng(2)
        band = 0.1 + 0.02 * rng.standard_normal((nlat, nlon))
        band[18:23, :] = 0.95
        nf = NF(
            tsm_n=band,
            conv_n=band,
            olr_n_inv=band,
            olr_abs=np.full((nlat, nlon), 175.0),  # tudo "Forte" onde sobreviver
            lons=np.linspace(-55, 15, nlon),
            lats=np.linspace(-15, 15, nlat),
        )
        raster, _izcit, _active = build_raster(nf, filter_method="coherence")
        vals = raster[np.isfinite(raster)]
        assert vals.size > 0  # algo sobrevive (a banda)
        assert set(np.unique(vals)).issubset({0.0, 1.0, 2.0, 3.0})
