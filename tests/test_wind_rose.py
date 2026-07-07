"""Rosa dos ventos: binagem pura direção×velocidade (sem GUI/rede)."""

import json
import math

import numpy as np
import pytest

from cartomet_br.data.wind_rose import (
    CALM_THRESHOLD,
    CARDINAL_8,
    DEFAULT_SECTORS,
    DEFAULT_SPEED_BINS,
    compass_label,
    compute_wind_rose,
    level_label,
    wind_rose_from_dict,
    wind_rose_to_dict,
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


# ── Rótulos (rumo de 8 pontos e nível) ───────────────────────────────────────


def test_compass_label_nos_8_rumos():
    for i, name in enumerate(CARDINAL_8):
        assert compass_label(i * 45.0) == name


def test_compass_label_arredonda_e_da_wrap():
    assert compass_label(350.0) == "N"  # wrap em 360°
    assert compass_label(360.0) == "N"
    assert compass_label(-45.0) == "NW"  # negativo normalizado
    assert compass_label(100.0) == "E"  # 100° → mais perto de E (90) que SE (135)
    assert compass_label(113.0) == "SE"  # 113° → mais perto de SE


def test_level_label_superficie_e_pressao():
    assert level_label(None) == "10 m"
    assert level_label(850.0) == "850 hPa"
    assert level_label(925) == "925 hPa"


# ── Faixas e calmaria customizadas (diálogo de config — Fase 3) ──────────────


def test_bins_customizados_respeitados():
    edges = (1.0, 5.0, math.inf)
    rose = compute_wind_rose([3.0, 8.0, 8.0], [0.0, 0.0, 0.0], speed_bin_edges=edges)
    assert rose.speed_bin_edges == edges
    assert len(rose.freq[0]) == 2
    assert rose.freq[0][0] == pytest.approx(100.0 / 3)  # 3 m/s na faixa 1–5
    assert rose.freq[0][1] == pytest.approx(200.0 / 3)  # 8 m/s (×2) na faixa ≥5


def test_calm_threshold_customizado():
    # Limiar alto: 1.5 m/s vira calmaria; com o default (0.5) seria ativo.
    rose = compute_wind_rose([1.5, 5.0], [0.0, 0.0], calm_threshold=2.0)
    assert rose.calm_fraction == pytest.approx(0.5)
    assert rose.calm_threshold == pytest.approx(2.0)
    assert _total_freq(rose) == pytest.approx(50.0)


# ── Serialização (persistência .cmbr) ────────────────────────────────────────


def test_roundtrip_preserva_rosa():
    rng = np.random.default_rng(1)
    rose = compute_wind_rose(rng.uniform(0, 12, 300), rng.uniform(0, 360, 300))
    back = wind_rose_from_dict(wind_rose_to_dict(rose))
    assert back == rose


def test_serializacao_e_json_limpo_sem_inf_nan():
    # Rosa toda calmaria → prevailing_deg = NaN; edges têm inf. O dict não pode
    # carregar Infinity/NaN (JSON não-padrão) — viram null.
    rose = compute_wind_rose([0.0, 0.1], [10.0, 200.0])
    d = wind_rose_to_dict(rose)
    text = json.dumps(d, allow_nan=False)  # falharia se houvesse inf/nan
    assert "Infinity" not in text and "NaN" not in text
    assert d["speed_bin_edges"][-1] is None  # inf → null
    assert d["prevailing_deg"] is None  # NaN → null


def test_roundtrip_calmaria_total():
    rose = compute_wind_rose([0.0, 0.1], [10.0, 200.0])
    back = wind_rose_from_dict(wind_rose_to_dict(rose))
    assert math.isinf(back.speed_bin_edges[-1])
    assert math.isnan(back.prevailing_deg)
    assert back.calm_fraction == pytest.approx(1.0)
