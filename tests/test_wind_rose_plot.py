"""Render da rosa dos ventos: roda sob Agg (headless), sem exceção."""

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

from cartomet_br.charts.wind_rose_plot import (
    render_wind_rose,
    speed_bin_colors,
    speed_bin_labels,
)
from cartomet_br.data.wind_rose import DEFAULT_SPEED_BINS, compute_wind_rose


def _polar_ax():
    fig = plt.figure()
    ax = fig.add_subplot(111, projection="polar")
    return fig, ax


def test_render_devolve_artistas():
    rng = np.random.default_rng(3)
    rose = compute_wind_rose(rng.uniform(0, 12, 300), rng.uniform(0, 360, 300))
    fig, ax = _polar_ax()
    artists = render_wind_rose(ax, rose, title="Teste")
    assert len(artists) > 0
    plt.close(fig)


def test_render_rosa_vazia_mostra_mensagem():
    rose = compute_wind_rose([np.nan], [np.nan])
    fig, ax = _polar_ax()
    artists = render_wind_rose(ax, rose)
    assert len(artists) == 1  # só a mensagem
    plt.close(fig)


def test_render_sem_legenda_e_sem_calmo():
    rose = compute_wind_rose([5.0] * 20, [45.0] * 20)
    fig, ax = _polar_ax()
    artists = render_wind_rose(ax, rose, show_legend=False, show_calm=False)
    assert artists  # barras desenhadas
    plt.close(fig)


def test_speed_bin_labels_formata_infinito():
    labels = speed_bin_labels(DEFAULT_SPEED_BINS)
    assert labels[0] == "0.5–2"
    assert labels[-1].startswith("≥")


def test_speed_bin_colors_conta_certo():
    assert len(speed_bin_colors(6)) == 6
    assert len(speed_bin_colors(1)) == 1
