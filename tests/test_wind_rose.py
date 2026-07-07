"""Rosa dos ventos: binagem pura direção×velocidade (sem GUI/rede)."""

import math

import numpy as np
import pytest

from cartomet_br.data.wind_rose import (
    CALM_THRESHOLD,
    DEFAULT_SECTORS,
    DEFAULT_SPEED_BINS,
    compute_wind_rose,
)


def _total_freq(rose):
    return sum(v for row in rose.freq for v in row)


# ── Geometria dos setores ────────────────────────────────────────────────────


def test_setores_fecham_360_e_centram_no_norte():
    rose = compute_wind_rose([5.0], [0.0], n_sectors=16)
    assert len(rose.sector_centers) == 16
    assert rose.sector_width_deg == pytest.approx(22.5)
    assert rose.sector_centers[0] == pytest.approx(0.0)  # setor 0 no Norte
    assert rose.sector_centers[4] == pytest.approx(90.0)  # setor 4 no Leste


def test_n_sectors_variavel():
    for n in (8, 16, 36):
        rose = compute_wind_rose([5.0], [0.0], n_sectors=n)
        assert len(rose.sector_centers) == n
        assert rose.sector_width_deg == pytest.approx(360.0 / n)


def test_n_sectors_invalido():
    with pytest.raises(ValueError):
        compute_wind_rose([5.0], [0.0], n_sectors=0)


# ── Convenção de direção (de onde sopra) ─────────────────────────────────────


def test_vento_todo_de_norte_uma_petala_no_norte():
    rose = compute_wind_rose([5.0] * 10, [0.0] * 10, n_sectors=16)
    assert rose.prevailing_deg == pytest.approx(0.0)
    # Toda a energia no setor 0 (Norte).
    assert rose.freq[0][2] == pytest.approx(100.0)  # 5 m/s cai na faixa 4–6
    assert sum(rose.freq[1]) == 0.0


def test_vento_de_leste_petala_no_leste():
    # Direção meteorológica 90 = vento SOPRANDO DE leste → pétala aponta p/ E.
    rose = compute_wind_rose([6.0] * 5, [90.0] * 5, n_sectors=16)
    assert rose.prevailing_deg == pytest.approx(90.0)
    assert rose.freq[4][3] == pytest.approx(100.0)  # setor Leste, faixa 6–8


def test_fronteira_de_setor():
    # 350° está a 10° do Norte → cai no setor 0 (fronteira em ±11,25°).
    rose = compute_wind_rose([5.0], [350.0], n_sectors=16)
    assert rose.prevailing_deg == pytest.approx(0.0)


# ── Calmaria ─────────────────────────────────────────────────────────────────


def test_calmaria_conta_no_centro_e_sai_dos_setores():
    speed = [0.1, 0.2, 5.0, 5.0]  # 2 calmos, 2 ativos
    direction = [0.0, 90.0, 0.0, 0.0]
    rose = compute_wind_rose(speed, direction, calm_threshold=CALM_THRESHOLD)
    assert rose.calm_fraction == pytest.approx(0.5)
    assert rose.n_total == 4
    # Frequências dos setores + calmaria fecham 100%.
    assert _total_freq(rose) + rose.calm_fraction * 100.0 == pytest.approx(100.0)


def test_tudo_calmaria_prevailing_nan():
    rose = compute_wind_rose([0.0, 0.1], [10.0, 200.0])
    assert rose.calm_fraction == pytest.approx(1.0)
    assert math.isnan(rose.prevailing_deg)
    assert _total_freq(rose) == 0.0


# ── Robustez: NaN, vazio, tamanhos ───────────────────────────────────────────


def test_nan_descartado():
    speed = [5.0, np.nan, 5.0, 4.0]
    direction = [0.0, 0.0, np.nan, 0.0]
    rose = compute_wind_rose(speed, direction)
    assert rose.n_total == 2  # só o 1º e o 4º são válidos


def test_sem_amostras_validas_rosa_vazia():
    rose = compute_wind_rose([np.nan, np.nan], [np.nan, np.nan])
    assert rose.n_total == 0
    assert rose.calm_fraction == 0.0
    assert math.isnan(rose.mean_speed)
    assert _total_freq(rose) == 0.0


def test_tamanhos_diferentes_erro():
    with pytest.raises(ValueError):
        compute_wind_rose([1.0, 2.0], [10.0])


# ── Determinismo e somatório ─────────────────────────────────────────────────


def test_determinismo():
    rng = np.random.default_rng(42)
    speed = rng.uniform(0, 12, 200)
    direction = rng.uniform(0, 360, 200)
    a = compute_wind_rose(speed, direction)
    b = compute_wind_rose(speed, direction)
    assert a == b


def test_frequencias_somam_com_calmaria():
    rng = np.random.default_rng(7)
    speed = rng.uniform(0, 12, 500)
    direction = rng.uniform(0, 360, 500)
    rose = compute_wind_rose(speed, direction)
    assert _total_freq(rose) + rose.calm_fraction * 100.0 == pytest.approx(100.0)


def test_bins_default_seis_faixas():
    rose = compute_wind_rose([5.0], [0.0])
    assert len(rose.speed_bin_edges) == len(DEFAULT_SPEED_BINS)
    assert len(rose.freq[0]) == len(DEFAULT_SPEED_BINS) - 1
    assert rose.sector_centers  # sanity
    assert len(rose.freq) == DEFAULT_SECTORS
