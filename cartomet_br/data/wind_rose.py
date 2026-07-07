"""Rosa dos ventos — binagem direção×velocidade (lógica PURA, sem Qt/Matplotlib).

Recebe séries de intensidade (m/s) e direção (graus, convenção meteorológica
"de onde sopra": 0=N, 90=E, horário) e devolve um ``WindRose`` — a distribuição
de frequência por setor e faixa de velocidade, mais a fração de calmaria. O
desenho vive em ``cartomet_br/charts/wind_rose_plot.py``; a fonte dos dados
(vento de 10 m do IFS ao longo dos steps) vem de ``load_point_timeseries``.

Honestidade: montada com os poucos steps de UMA rodada, a rosa é a distribuição
do vento PREVISTO — não uma climatologia. O badge do painel deixa isso explícito.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

# Limiar de calmaria (m/s): abaixo disso a direção não tem sentido físico e a
# amostra conta só como fração de calmaria no centro da rosa. Calibrável — na
# Amazônia (ventos fracos) muda bastante a leitura da rosa.
CALM_THRESHOLD = 0.5

# Bordas das faixas de velocidade (m/s); a primeira coincide com o limiar de
# calmaria. ``math.inf`` como última borda captura o extremo superior.
DEFAULT_SPEED_BINS: tuple[float, ...] = (0.5, 2.0, 4.0, 6.0, 8.0, 10.0, math.inf)

# Nº de setores de direção (16 = 22,5° cada, rosa clássica de 16 rumos).
DEFAULT_SECTORS = 16


@dataclass(frozen=True)
class WindRose:
    """Distribuição binada de direção×velocidade num ponto (serializável).

    ``freq`` é frequência relativa em PERCENTO do total válido (calmaria +
    ativos): ``sum(freq) + calm_fraction*100 ≈ 100``. Setores em convenção
    meteorológica (de onde sopra), centrados nos rumos (setor 0 no Norte).
    """

    sector_centers: tuple[float, ...]  # graus (n_setores), 0=N horário
    sector_width_deg: float
    speed_bin_edges: tuple[float, ...]  # m/s (n_bins+1); última pode ser inf
    freq: tuple[tuple[float, ...], ...]  # [n_setores][n_bins], %
    calm_fraction: float  # [0,1] fração de amostras < calm_threshold
    calm_threshold: float  # m/s (o limiar usado)
    n_total: int  # amostras válidas (não-NaN), inclusive calmaria
    mean_speed: float  # m/s (média sobre válidos, inclusive calmaria)
    prevailing_deg: float  # centro do setor dominante (NaN se tudo calmaria)


@dataclass(frozen=True)
class WindRoseResult:
    """Rosa + série-fonte e metadados do ponto/rodada — o que o worker entrega.

    Guarda ``speed``/``direction`` por step para o painel **re-binar** (mudar o
    nº de setores) instantaneamente, sem baixar de novo. ``grid_*`` é o ponto de
    grade do modelo efetivamente amostrado.
    """

    rose: WindRose
    speed: tuple[float, ...]  # m/s por step (fonte)
    direction: tuple[float, ...]  # graus (de onde sopra) por step
    lon: float
    lat: float
    grid_lon: float
    grid_lat: float
    level: str
    base_time: str
    steps: tuple[int, ...]


def _empty_rose(
    sector_centers: tuple[float, ...],
    sector_width: float,
    speed_bin_edges: tuple[float, ...],
    calm_threshold: float,
) -> WindRose:
    """Rosa vazia (sem amostras válidas) — o painel mostra aviso amigável."""
    n_sectors = len(sector_centers)
    n_bins = len(speed_bin_edges) - 1
    return WindRose(
        sector_centers=sector_centers,
        sector_width_deg=sector_width,
        speed_bin_edges=speed_bin_edges,
        freq=tuple((0.0,) * n_bins for _ in range(n_sectors)),
        calm_fraction=0.0,
        calm_threshold=calm_threshold,
        n_total=0,
        mean_speed=math.nan,
        prevailing_deg=math.nan,
    )


def compute_wind_rose(
    speed: Sequence[float] | np.ndarray,
    direction: Sequence[float] | np.ndarray,
    *,
    n_sectors: int = DEFAULT_SECTORS,
    speed_bin_edges: Sequence[float] = DEFAULT_SPEED_BINS,
    calm_threshold: float = CALM_THRESHOLD,
) -> WindRose:
    """Bina (direção×velocidade) em setores e faixas → ``WindRose``.

    - ``direction`` é a direção METEOROLÓGICA (de onde sopra), em graus.
    - Amostras com NaN em intensidade OU direção são descartadas.
    - Amostras com ``speed < calm_threshold`` viram fração de calmaria (não
      entram nos setores — direção de vento ~nulo não tem sentido).
    - Setor 0 é centrado no Norte; a fronteira fica em ±(largura/2).
    """
    if n_sectors < 1:
        raise ValueError("n_sectors deve ser ≥ 1")
    edges = np.asarray(speed_bin_edges, dtype=float)
    if edges.size < 2:
        raise ValueError("speed_bin_edges precisa de ao menos 2 bordas")

    sector_width = 360.0 / n_sectors
    sector_centers = tuple(round(i * sector_width, 6) for i in range(n_sectors))
    edges_t = tuple(float(e) for e in edges)
    n_bins = edges.size - 1

    spd = np.asarray(speed, dtype=float).ravel()
    drc = np.asarray(direction, dtype=float).ravel()
    if spd.shape != drc.shape:
        raise ValueError("speed e direction precisam ter o mesmo tamanho")

    valid = np.isfinite(spd) & np.isfinite(drc)
    n_total = int(valid.sum())
    if n_total == 0:
        return _empty_rose(sector_centers, sector_width, edges_t, calm_threshold)

    spd_v = spd[valid]
    drc_v = drc[valid]
    mean_speed = float(np.mean(spd_v))

    calm = spd_v < calm_threshold
    calm_fraction = float(calm.sum()) / n_total

    active = ~calm
    counts = np.zeros((n_sectors, n_bins), dtype=float)
    if active.any():
        drc_a = drc_v[active] % 360.0
        # Setor centrado no rumo: desloca meia-largura antes de fatiar.
        sec_idx = np.floor(((drc_a + sector_width / 2.0) % 360.0) / sector_width).astype(int)
        sec_idx = np.clip(sec_idx, 0, n_sectors - 1)
        # Faixa de velocidade: digitize e clampa às faixas válidas.
        bin_idx = np.digitize(spd_v[active], edges) - 1
        bin_idx = np.clip(bin_idx, 0, n_bins - 1)
        np.add.at(counts, (sec_idx, bin_idx), 1.0)

    freq = counts / n_total * 100.0
    sector_totals = counts.sum(axis=1)
    if sector_totals.any():
        prevailing_deg = float(sector_centers[int(np.argmax(sector_totals))])
    else:
        prevailing_deg = math.nan

    freq_t = tuple(tuple(float(v) for v in row) for row in freq)
    return WindRose(
        sector_centers=sector_centers,
        sector_width_deg=sector_width,
        speed_bin_edges=edges_t,
        freq=freq_t,
        calm_fraction=calm_fraction,
        calm_threshold=float(calm_threshold),
        n_total=n_total,
        mean_speed=mean_speed,
        prevailing_deg=prevailing_deg,
    )
