"""Camada Hidrografia — asset embarcado e lógica pura (sem GUI).

Valida o ``hidrografia_sa.npz`` gerado por ``tools/gera_hidrografia_sa.py``
(HydroRIVERS Strahler >= 5 + LakeATLAS >= 10 km², América do Sul) e os
helpers de ``cartomet_br.data.hydrography``.
"""

from __future__ import annotations

import numpy as np

from cartomet_br.data.hydrography import (
    DEFAULT_HYDRO_DETAIL,
    HYDRO_DETAIL_LEVELS,
    RIVER_LINEWIDTHS,
    _assets_path,
    load_hydrography,
    river_linewidths,
)

BBOX_AMSUL = (-93.0, -56.5, -32.0, 13.5)


def test_asset_parseia_e_tem_volume_esperado():
    hydro = load_hydrography()
    # Limites frouxos: pegam asset truncado/vazio sem engessar a regeneração
    assert 1_000 <= len(hydro.river_lines) <= 50_000
    assert 500 <= len(hydro.lake_rings) <= 5_000
    assert len(hydro.river_orders) == len(hydro.river_lines)


def test_geometrias_consistentes():
    hydro = load_hydrography()
    for linha in hydro.river_lines:
        assert linha.ndim == 2 and linha.shape[1] == 2
        assert len(linha) >= 2
        assert not np.isnan(linha).any()
    for anel in hydro.lake_rings:
        assert anel.ndim == 2 and anel.shape[1] == 2
        assert len(anel) >= 4
        assert not np.isnan(anel).any()


def test_ordens_strahler_validas():
    hydro = load_hydrography()
    ordens = set(np.unique(hydro.river_orders).tolist())
    assert ordens.issubset(set(range(5, 11)))
    # O tronco do Amazonas é o único ordem 10 da AmSul — se sumiu, o corte quebrou
    assert 10 in ordens


def test_coordenadas_na_america_do_sul():
    hydro = load_hydrography()
    lon_min, lat_min, lon_max, lat_max = BBOX_AMSUL
    for grupo in (hydro.river_lines, hydro.lake_rings):
        coords = np.concatenate(grupo)
        assert coords[:, 0].min() >= lon_min and coords[:, 0].max() <= lon_max
        assert coords[:, 1].min() >= lat_min and coords[:, 1].max() <= lat_max


def test_lagos_sentinela():
    """Titicaca e Lagoa dos Patos precisam estar no asset (centroide ± 0,5°)."""
    hydro = load_hydrography()
    centroides = np.array([anel.mean(axis=0) for anel in hydro.lake_rings])
    for lon, lat in ((-69.3, -15.8), (-51.2, -31.0)):
        dist = np.abs(centroides - [lon, lat]).max(axis=1)
        assert (dist < 0.5).any(), f"lago-sentinela ausente perto de ({lon}, {lat})"


def test_linewidth_cresce_com_ordem_e_clampa():
    ordens = np.array([5, 6, 7, 8, 9, 10], dtype=np.uint8)
    larguras = river_linewidths(ordens)
    assert (np.diff(larguras) > 0).all()  # monotônico: rio maior, traço mais grosso
    # Fora da faixa: clamp nos extremos, nunca largura zero
    fora = river_linewidths(np.array([2, 15], dtype=np.int64))
    assert fora[0] == RIVER_LINEWIDTHS[5]
    assert fora[1] == RIVER_LINEWIDTHS[10]


def test_tamanho_do_asset():
    tamanho_mb = (_assets_path() / "hidrografia_sa.npz").stat().st_size / 1024 / 1024
    assert tamanho_mb < 8.0


def test_niveis_declarados():
    assert DEFAULT_HYDRO_DETAIL in HYDRO_DETAIL_LEVELS
    assert HYDRO_DETAIL_LEVELS == ("Principais", "Detalhado")
