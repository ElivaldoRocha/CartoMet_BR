"""Testes do motor LOCZCIT-PA — funções puras (sem rede)."""

import numpy as np
import pytest

from cartomet_br.data.loczcit_pa_engine import (
    CATEGORY_COLORS,
    CATEGORY_LABELS,
    C_THR,
    IQR_CONSTANT,
    OCEAN_MASK_THRESHOLD,
    OLR_THRESHOLD_MODERATE,
    OLR_THRESHOLD_STRONG,
    OLR_THRESHOLD_WEAK,
    OLR_WINDOW_SECONDS,
    SKT_SMOOTH_SIGMA,
    STRICT_EXTENT,
    CAT_CINEMATICA,
    LoczcitResult,
    _nanaware_gaussian,
    active_mask,
    build_raster,
    categorical_raster,
    classify_by_olr,
    couple_ockham,
    iqr_latitude_band,
    itcz_central_latitude,
    itcz_lat_band,
    normalize_meridional,
    plan_olr_deaccumulation,
    save_loczcit_netcdf,
)
from cartomet_br.data.loczcit_pa_engine import (
    NormalizedForcings as NF,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  Constantes / blindagens
# ═══════════════════════════════════════════════════════════════════════════════

class TestConstants:
    def test_ocean_mask_threshold_is_02(self):
        # Blindagem #1: limiar (não == 0), preserva costa do Amapá/Marajó
        assert OCEAN_MASK_THRESHOLD == 0.2

    def test_olr_thresholds(self):
        assert OLR_THRESHOLD_STRONG == 180.0
        assert OLR_THRESHOLD_MODERATE == 210.0
        assert OLR_THRESHOLD_WEAK == 240.0

    def test_strict_domain(self):
        # 15°S–15°N, 55°W–15°E
        assert STRICT_EXTENT == [-55.0, -15.0, 15.0, 15.0]

    def test_olr_window_is_3h(self):
        assert OLR_WINDOW_SECONDS == 10800.0

    def test_four_categories(self):
        # 0 Cinemática (magenta), 1 Fraca, 2 Moderada, 3 Forte
        assert len(CATEGORY_COLORS) == 4
        assert set(CATEGORY_LABELS) == {0, 1, 2, 3}

    def test_iqr_constant_tukey(self):
        assert IQR_CONSTANT == 1.5


# ═══════════════════════════════════════════════════════════════════════════════
#  Técnica B — mapeamento da desacumulação
# ═══════════════════════════════════════════════════════════════════════════════

class TestOLRPlan:
    # Casos documentados (triangulação temporal dinâmica) ─────────────────────
    def test_caso_a_analise_step0(self):
        """CASO A: run 12Z, +0h → base 00Z, steps 12−9, janela 3 h."""
        p = plan_olr_deaccumulation(12, "20260604", step=0)
        assert (p.base_cycle, p.base_date) == (0, "20260604")
        assert (p.step_hi, p.step_lo) == (12, 9)
        assert p.window_seconds == 10800.0

    def test_caso_b_previsao_step3(self):
        """CASO B: run 12Z, +3h → base 00Z, steps 15−12, janela 3 h."""
        p = plan_olr_deaccumulation(12, "20260604", step=3)
        assert (p.base_cycle, p.base_date) == (0, "20260604")
        assert (p.step_hi, p.step_lo) == (15, 12)

    def test_caso_c_meia_noite_step0(self):
        """CASO C: run 00Z dia 04, +0h → base 12Z dia 03, steps 12−9."""
        p = plan_olr_deaccumulation(0, "20260604", step=0)
        assert (p.base_cycle, p.base_date) == (12, "20260603")
        assert (p.step_hi, p.step_lo) == (12, 9)

    def test_dynamic_target_is_step_plus_12(self):
        """target = step + 12 (sem hardcode) p/ vários steps."""
        for step in (0, 3, 6, 24, 48):
            p = plan_olr_deaccumulation(12, "20260604", step=step)
            assert p.step_hi == step + 12
            assert p.step_lo == p.step_hi - 3   # janela 3 h enquanto ≤144

    def test_long_range_uses_6h_window(self):
        """Além de 144 h, a janela vira 6 h (resolução do IFS)."""
        p = plan_olr_deaccumulation(12, "20260604", step=138)   # target=150 >144
        assert p.step_hi == 150
        assert p.step_lo == 144                                 # janela 6 h
        assert p.window_seconds == 21600.0

    def test_never_step_zero(self):
        # Blindagem #3: nunca usa o step 0 da OLR (ttr=0 global)
        for c in (0, 6, 12, 18):
            p = plan_olr_deaccumulation(c, "20260604", step=0)
            assert p.step_lo >= 9 and p.step_hi >= 12


# ═══════════════════════════════════════════════════════════════════════════════
#  Normalização meridional (blindagem #2)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizeMeridional:
    def test_range_0_1(self):
        field = np.array([[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]])
        n = normalize_meridional(field)
        assert np.nanmin(n) >= 0.0
        assert np.nanmax(n) <= 1.0 + 1e-9

    def test_min_maps_to_zero_max_to_one(self):
        field = np.array([[0.0], [5.0], [10.0]])
        n = normalize_meridional(field)
        assert n[0, 0] == pytest.approx(0.0, abs=1e-5)
        assert n[2, 0] == pytest.approx(1.0, rel=1e-3)

    def test_degenerate_column_becomes_nan(self):
        # Coluna constante (max-min < eps) → NaN explícito (não faixa de zeros)
        field = np.array([[5.0, 1.0], [5.0, 2.0], [5.0, 3.0]])
        n = normalize_meridional(field)
        assert np.all(np.isnan(n[:, 0]))      # coluna degenerada
        assert not np.any(np.isnan(n[:, 1]))  # coluna normal preservada

    def test_all_nan_column_stays_nan(self):
        field = np.array([[np.nan, 1.0], [np.nan, 2.0], [np.nan, 3.0]])
        n = normalize_meridional(field)
        assert np.all(np.isnan(n[:, 0]))

    def test_skipna_ignores_nan_in_minmax(self):
        # NaN no meio não deve quebrar a normalização da coluna
        field = np.array([[0.0], [np.nan], [10.0]])
        n = normalize_meridional(field)
        assert n[0, 0] == pytest.approx(0.0, abs=1e-5)
        assert n[2, 0] == pytest.approx(1.0, rel=1e-3)
        assert np.isnan(n[1, 0])


# ═══════════════════════════════════════════════════════════════════════════════
#  Classificação por OLR (Seção 6)
# ═══════════════════════════════════════════════════════════════════════════════

class TestClassifyByOLR:
    def test_thresholds(self):
        olr = np.array([[150.0, 195.0, 225.0, 260.0]])
        r = classify_by_olr(olr)
        assert r[0, 0] == 3.0   # <=180 forte
        assert r[0, 1] == 2.0   # 180<x<=210 moderada
        assert r[0, 2] == 1.0   # 210<x<=240 fraca
        assert np.isnan(r[0, 3])  # >240 céu limpo → NaN

    def test_boundaries_inclusive(self):
        olr = np.array([[180.0, 210.0, 240.0]])
        r = classify_by_olr(olr)
        assert r[0, 0] == 3.0    # exatamente 180 → forte
        assert r[0, 1] == 2.0    # exatamente 210 → moderada
        assert r[0, 2] == 1.0    # exatamente 240 → fraca

    def test_nan_olr_stays_nan(self):
        r = classify_by_olr(np.array([[np.nan]]))
        assert np.isnan(r[0, 0])


# ═══════════════════════════════════════════════════════════════════════════════
#  Acoplamento (Ockham) + IQR
# ═══════════════════════════════════════════════════════════════════════════════

class TestCoupleOckham:
    def test_arithmetic_mean(self):
        nf = NF(
            tsm_n=np.array([[0.0, 1.0]]),
            conv_n=np.array([[0.0, 1.0]]),
            olr_n_inv=np.array([[0.0, 1.0]]),
            olr_abs=np.zeros((1, 2)),
            lons=np.array([0.0, 1.0]), lats=np.array([0.0]),
        )
        izcit = couple_ockham(nf)
        assert izcit[0, 0] == pytest.approx(0.0)
        assert izcit[0, 1] == pytest.approx(1.0)

    def test_nan_propagates(self):
        nf = NF(
            tsm_n=np.array([[np.nan]]),
            conv_n=np.array([[1.0]]),
            olr_n_inv=np.array([[1.0]]),
            olr_abs=np.zeros((1, 1)),
            lons=np.array([0.0]), lats=np.array([0.0]),
        )
        assert np.isnan(couple_ockham(nf)[0, 0])


class TestIQRLatitudeBand:
    def test_outlier_latitude_excluded(self):
        # Eixo central ~0°; uma coluna com máximo em +12° é outlier
        lats = np.linspace(-15, 15, 31)
        nlon = 20
        izcit = np.full((31, nlon), 0.1)
        center = np.argmin(np.abs(lats - 0.0))
        izcit[center, :] = 0.9                # máximo no centro p/ quase todas
        out_lat = np.argmin(np.abs(lats - 12.0))
        izcit[out_lat, -1] = 2.0              # coluna anômala: máximo em +12°
        band = iqr_latitude_band(izcit, lats)
        # A latitude +12° deve cair fora da banda do IQR
        assert not band[out_lat, 0]
        assert band[center, 0]               # centro dentro da banda


# ═══════════════════════════════════════════════════════════════════════════════
#  Pipeline F3→F4 (build_raster) com dados sintéticos
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildRaster:
    def test_raster_values_in_categories(self):
        rng = np.random.default_rng(0)
        shape = (20, 30)
        nf = NF(
            tsm_n=rng.random(shape),
            conv_n=rng.random(shape),
            olr_n_inv=rng.random(shape),
            olr_abs=rng.uniform(150, 260, shape),   # mistura de categorias e céu limpo
            lons=np.linspace(-55, 15, 30),
            lats=np.linspace(-15, 15, 20),
        )
        raster, izcit, active = build_raster(nf)   # agora retorna a máscara ativa também
        vals = raster[np.isfinite(raster)]
        # Sem convergência (conv_abs=None), a banda só ativa onde OLR<240 → cats {1,2,3}
        assert set(np.unique(vals)).issubset({1.0, 2.0, 3.0})
        # I_ZCIT contínuo (potencial acoplado) em [0,1]
        fin = izcit[np.isfinite(izcit)]
        assert fin.min() >= 0.0 and fin.max() <= 1.0
        assert active.dtype == bool and active.shape == shape

    def test_clear_sky_excluded_without_convergence(self):
        shape = (10, 10)
        nf = NF(
            tsm_n=np.ones(shape), conv_n=np.ones(shape), olr_n_inv=np.ones(shape),
            olr_abs=np.full(shape, 300.0),          # tudo céu limpo (>240)
            lons=np.linspace(-55, 15, 10), lats=np.linspace(-15, 15, 10),
        )
        raster, _izcit, _active = build_raster(nf)
        assert np.all(np.isnan(raster))             # sem convergência, nada sobrevive

    def test_clear_sky_rescued_by_convergence_is_cinematica(self):
        # OLR>240 (céu limpo radiativo) MAS convergência organizada → categoria Cinemática.
        shape = (12, 12)
        conv = np.full(shape, 5e-5)                 # > C_THR em toda parte
        nf = NF(
            tsm_n=np.ones(shape), conv_n=np.ones(shape), olr_n_inv=np.ones(shape),
            olr_abs=np.full(shape, 300.0),          # nenhuma assinatura radiativa
            lons=np.linspace(-55, 15, 12), lats=np.linspace(-15, 15, 12),
            conv_abs=conv, lsm=np.zeros(shape),     # tudo oceano
        )
        raster, _izcit, active = build_raster(nf)
        vals = raster[np.isfinite(raster)]
        assert active.any()
        assert set(np.unique(vals)) == {float(CAT_CINEMATICA)}   # só Cinemática


class TestActiveMaskAndEnvelope:
    def test_c_thr_value(self):
        assert C_THR == 3.0e-5

    def test_active_mask_union_rescues_convergence(self):
        shape = (40, 40)
        olr = np.full(shape, 300.0)                 # tudo céu limpo radiativo
        conv = np.zeros(shape)
        conv[10:30, 10:30] = 5e-5                   # bloco coeso de convergência (>C_THR)
        lsm = np.zeros(shape)                       # oceano
        am = active_mask(olr, conv, lsm)
        assert am[20, 20]                           # ativado pela convergência
        assert not am[0, 0]                         # céu limpo sem convergência → inativo

    def test_envelope_rejects_subtropics_keeps_equatorial_in_april(self):
        # doy ~103 (13 abr): φ_c ≈ 1.6°N, faixa ≈ [-5.9, 9.1] → 12°N fora, 3°N dentro.
        lats = np.linspace(15, -15, 61)
        inside, (lo, phi_c, hi) = itcz_lat_band(lats, 103)
        assert phi_c < 3.0                          # posição ao sul na primavera boreal
        assert not inside[np.argmin(np.abs(lats - 12.0))]
        assert inside[np.argmin(np.abs(lats - 3.0))]

    def test_central_latitude_peaks_in_september(self):
        # doy 245 (~2 set): posição mais ao norte (≈ φ_mean + amp = 9.5°N).
        assert itcz_central_latitude(245) == pytest.approx(9.5, abs=0.2)

    def test_categorical_raster_four_classes(self):
        olr = np.array([[170.0, 195.0], [225.0, 300.0]])   # Forte, Moderada, Fraca, Cinemática
        active = np.ones((2, 2), dtype=bool)
        cr = categorical_raster(olr, active)
        assert cr[0, 0] == 3.0 and cr[0, 1] == 2.0
        assert cr[1, 0] == 1.0 and cr[1, 1] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  Dataclass de resultado
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoczcitResult:
    def test_creation(self):
        r = LoczcitResult(
            raster=np.zeros((5, 5)), lons=np.zeros(5), lats=np.zeros(5),
            valid_time="2026-05-31T12:00", base_time="00Z 31/05/2026",
        )
        assert r.raster.shape == (5, 5)
        assert r.meta == {}
        assert r.index is None


class TestSktSmoothing:
    """Mitigação da DWL: suavização Gaussiana ciente-de-NaN da skt (revisões N1/N2)."""

    def test_default_sigma(self):
        # Calibração exposta; 1.0 é a convenção do projeto (OLR/vento)
        assert SKT_SMOOTH_SIGMA == 1.0

    def test_reduces_noise(self):
        rng = np.random.default_rng(0)
        field = 25.0 + rng.standard_normal((30, 30))      # oceano ruidoso (DWL)
        out = _nanaware_gaussian(field, sigma=1.0)
        assert np.nanstd(out) < np.nanstd(field)          # suaviza

    def test_preserves_land_nan(self):
        field = np.full((10, 10), 25.0)
        field[4, 4] = np.nan                              # "continente" mascarado
        out = _nanaware_gaussian(field, sigma=1.0)
        assert np.isnan(out[4, 4])                        # continente segue NaN
        assert np.isfinite(out[0, 0])                     # oceano permanece válido

    def test_no_land_bleed_across_coast(self):
        # Oceano uniforme + 1 pixel de "terra" (NaN). O denoise NÃO puxa os
        # vizinhos oceânicos: a terra está mascarada ANTES de suavizar (Blindagem #1).
        field = np.full((9, 9), 25.0)
        field[4, 4] = np.nan
        out = _nanaware_gaussian(field, sigma=1.0)
        block = out[3:6, 3:6]
        neigh = block[np.isfinite(block)]
        assert np.allclose(neigh, 25.0, atol=1e-6)        # vizinhos intactos


class TestSaveNetCDF:
    def test_saves_named_netcdf_with_both_vars(self, tmp_path):
        """Salva NetCDF nomeado pelo valid_time, com raster + índice contínuo."""
        import xarray as xr

        lats = np.linspace(-15, 15, 6)
        lons = np.linspace(-55, 15, 8)
        raster = np.full((6, 8), np.nan)
        raster[2, 3] = 2.0
        izcit = np.random.default_rng(0).random((6, 8))
        r = LoczcitResult(
            raster=raster, index=izcit, lons=lons, lats=lats,
            valid_time="2026-05-31T12:00", base_time="00Z 31/05/2026",
        )
        path = save_loczcit_netcdf(r, tmp_path)
        # Nome ordenável e autoexplicativo
        assert path.name == "loczcit_pa_20260531T1200Z.nc"
        assert path.parent == tmp_path
        ds = xr.open_dataset(path)
        try:
            assert "loczcit_raster" in ds.data_vars
            assert "izcit" in ds.data_vars
            assert "lat" in ds.coords and "lon" in ds.coords
            assert ds.attrs.get("valid_time") == "2026-05-31T12:00"
            assert "LOCZCIT-PA" in ds.attrs.get("method", "")
        finally:
            ds.close()
