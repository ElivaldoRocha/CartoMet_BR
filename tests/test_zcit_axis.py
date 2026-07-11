"""Testes da detecção de eixo da ZCIT (overlay opcional) — funções puras, sem rede.

Espelha os casos sintéticos validados em _rascunhos/Projeto_ZCIT_AXIS/test_zcit_regression.py:
banda simples → 0% dupla; banda dupla → is_double. Também cobre o gancho ACOPLADO
(intensity=I_ZCIT, mask_override=máscara ativa) usado pelo motor LOCZCIT-PA.
"""

import numpy as np

from cartomet_br.data.zcit_axis import detect_zcit_axis, meridional_centroid
from cartomet_br.data.zcit_dual import detect_zcit_axis_dual


def _grid(nlat=60, nlon=120):
    lats = np.linspace(15, -15, nlat)  # descendente (norte → sul)
    lons = np.linspace(-55, 15, nlon)
    return lats, lons


def _band(lats, lons, center, width=2.0, floor=290.0, depth=130.0):
    LAT = lats[:, None] * np.ones((1, len(lons)))
    return floor - depth * np.exp(-(((LAT - center) / width) ** 2))


class TestSingleBand:
    def test_unimodal_zero_double_fraction(self):
        lats, lons = _grid()
        olr = _band(lats, lons, center=3.0)
        lsm = np.zeros((len(lats), len(lons)))
        res = detect_zcit_axis_dual(olr, lats, lons, lsm=lsm)
        assert res.is_double is False
        assert res.double_fraction == 0.0
        # eixo aproximadamente em 3°N
        med = np.nanmedian(res.lat_north)
        assert abs(med - 3.0) < 2.0


class TestDoubleBand:
    def test_two_bands_detected_as_double(self):
        lats, lons = _grid()
        north = _band(lats, lons, center=7.0, depth=120.0)
        south = _band(lats, lons, center=-5.0, depth=120.0)
        olr = np.minimum(north, south)  # dois lobos com vão limpo entre eles
        lsm = np.zeros((len(lats), len(lons)))
        res = detect_zcit_axis_dual(olr, lats, lons, lsm=lsm)
        assert res.is_double is True
        assert res.double_fraction > 0.5
        # na zona dupla, o ramo norte fica acima do sul
        zone = res.n_modes == 2
        assert np.nanmean(res.lat_north[zone]) > np.nanmean(res.lat_south[zone])


class TestCoupledInjection:
    def test_intensity_none_equals_default_olr_weight(self):
        # intensity=(t_env-OLR) reproduz o comportamento OLR-only (regra de ouro #3).
        # Sem suavização (smooth_sigma=0) e tudo-oceano, olr_ocean == olr, então o peso
        # injetado (240-olr) coincide com o peso interno.
        lats, lons = _grid()
        olr = _band(lats, lons, center=2.0)
        lsm = np.zeros((len(lats), len(lons)))
        a = detect_zcit_axis(olr, lats, lons, lsm=lsm, smooth_sigma=0.0)
        b = detect_zcit_axis(
            olr, lats, lons, lsm=lsm, smooth_sigma=0.0, intensity=np.clip(240.0 - olr, 0.0, None)
        )
        fa, fb = a.lat_axis, b.lat_axis
        both = np.isfinite(fa) & np.isfinite(fb)
        assert both.any()
        assert np.allclose(fa[both], fb[both], atol=1e-6)

    def test_mask_override_drives_centroid(self):
        # injetar máscara/peso acoplado muda o eixo (gancho do motor LOCZCIT-PA)
        lats, lons = _grid()
        olr = _band(lats, lons, center=4.0)
        izcit = np.zeros((len(lats), len(lons)))
        rows = np.argsort(np.abs(lats + 6.0))[:5]  # 5 linhas em torno de ~6°S
        izcit[rows, :] = 1.0  # força energia (acima de min_pixels)
        mask = izcit > 0
        lat_raw, cov = meridional_centroid(olr, mask, lats, intensity=izcit)
        assert np.nanmedian(lat_raw) < -4.0  # centroide puxado p/ o sul
