"""Testes dos campos do preset Diagnóstico Baroclínico (θe → ∇θe → advecção → TFP).

Exercitam os helpers PUROS de ``cartomet_br.data.ecmwf`` sobre campos sintéticos,
sem rede — a matemática (gradiente, advecção, Thermal Front Parameter e a máscara
de gradiente fraco do eixo TFP) é validada isoladamente do download de GRIB.
"""

import numpy as np

from cartomet_br.data.ecmwf import (
    TFP_GRAD_MIN_K_100KM,
    _tfp_field,
    _theta_e_2d,
    _theta_e_advection,
    _theta_e_gradient,
)

R = 6.371e6  # raio da Terra (m)


def _grid(ny: int, nx: int, dlat_deg: float = 0.25, dlon_deg: float = 0.25, lat0: float = -40.0):
    """Grade regular → (lats, dy escalar em m, dx_2d (ny,nx) em m)."""
    lats = lat0 + np.arange(ny) * dlat_deg
    dy = np.deg2rad(dlat_deg) * R
    dx_2d = np.deg2rad(dlon_deg) * R * np.cos(np.deg2rad(lats))[:, None] * np.ones((1, nx))
    return lats, dy, dx_2d


def test_theta_e_2d_finito_e_realista():
    """θe de T/q típicos de 850 hPa é finito e cai numa faixa física plausível."""
    t = np.full((5, 5), 290.0)  # K
    q = np.full((5, 5), 0.008)  # kg/kg
    theta_e = _theta_e_2d(850.0, t, q)
    assert theta_e.shape == (5, 5)
    assert np.all(np.isfinite(theta_e))
    assert np.all((theta_e > 280.0) & (theta_e < 360.0))


def test_theta_e_gradient_nao_negativo_e_coerente():
    """|∇θe| ≥ 0 e ~ 1 K por ponto de grade / dy para uma rampa meridional."""
    ny, nx = 20, 30
    _, dy, dx_2d = _grid(ny, nx)
    theta_e = 300.0 + np.tile(np.arange(ny)[:, None] * 1.0, (1, nx))  # +1 K por ponto em y
    g = _theta_e_gradient(theta_e, dy, dx_2d)
    assert np.all(g[1:-1] >= 0.0)
    assert np.allclose(np.nanmedian(g[1:-1]), 1.0 / dy, rtol=0.2)


def test_theta_e_advection_troca_de_sinal_com_o_vento():
    """u>0 sobre θe crescente em x → advecção FRIA (<0); inverter o vento → QUENTE (>0)."""
    ny, nx = 20, 30
    _, dy, dx_2d = _grid(ny, nx)
    theta_e = 300.0 + np.tile(np.arange(nx)[None, :] * 1.0, (ny, 1))  # cresce com x
    u = np.full((ny, nx), 5.0)
    v = np.zeros((ny, nx))
    adv_leste = _theta_e_advection(theta_e, u, v, dy, dx_2d)
    adv_oeste = _theta_e_advection(theta_e, -u, v, dy, dx_2d)
    assert np.nanmean(adv_leste[1:-1, 1:-1]) < 0.0
    assert np.nanmean(adv_oeste[1:-1, 1:-1]) > 0.0


def test_tfp_mascara_gradiente_fraco_e_cruza_zero_no_eixo():
    """O eixo TFP=0 aparece no centro da frente; bordas de gradiente fraco → NaN."""
    ny, nx = 40, 10
    _, dy, dx_2d = _grid(ny, nx)
    yy = np.arange(ny)[:, None]
    # Frente: rampa tanh em y (gradiente forte só na banda central).
    theta_e = 300.0 + 8.0 * np.tanh((yy - ny / 2) / 2.0) * np.ones((1, nx))
    tfp = _tfp_field(theta_e, dy, dx_2d, TFP_GRAD_MIN_K_100KM)

    # Longe da frente o gradiente é fraco (< limiar) → mascarado.
    assert np.all(np.isnan(tfp[:3]))
    assert np.all(np.isnan(tfp[-3:]))

    # Na banda central há TFP finito que TROCA DE SINAL (a isolinha 0 = eixo).
    banda = tfp[ny // 2 - 6 : ny // 2 + 6, nx // 2]
    assert np.any(np.isfinite(banda))
    assert np.nanmin(banda) < 0.0 < np.nanmax(banda)


def test_tfp_todo_nan_quando_campo_e_uniforme():
    """θe uniforme → |∇θe|=0 em todo lugar → TFP inteiramente mascarado (NaN)."""
    ny, nx = 12, 12
    _, dy, dx_2d = _grid(ny, nx)
    theta_e = np.full((ny, nx), 305.0)
    tfp = _tfp_field(theta_e, dy, dx_2d, TFP_GRAD_MIN_K_100KM)
    assert np.all(np.isnan(tfp))
