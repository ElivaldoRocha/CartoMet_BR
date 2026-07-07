"""Vento por nível de pressão num ponto (rosa em altitude) — puro, sem rede."""

import numpy as np
import pytest

from cartomet_br.data.ecmwf import (
    LEVEL_MATCH_TOLERANCE_HPA,
    ModelProfile,
    PointLevelWindSeries,
    _nearest_level_wind,
    _wind_dir_deg,
    load_point_level_wind_timeseries,
)


def _profile(pressures, u, v, **kw):
    """ModelProfile sintético — t/q/gh irrelevantes p/ o vento (preenchidos)."""
    p = np.asarray(pressures, dtype=float)
    return ModelProfile(
        lon=kw.get("lon", -48.0),
        lat=kw.get("lat", -1.5),
        grid_lon=kw.get("grid_lon", -48.0),
        grid_lat=kw.get("grid_lat", -1.5),
        pressures=p,
        t=np.full_like(p, 280.0),
        q=np.full_like(p, 0.01),
        u=np.asarray(u, dtype=float),
        v=np.asarray(v, dtype=float),
        gh=np.linspace(100.0, 12000.0, p.size),
        base_time=kw.get("base_time", "00Z 07/07/2026"),
        step=kw.get("step", 0),
    )


# ── _wind_dir_deg: convenção meteorológica "de onde sopra" ───────────────────


def test_wind_dir_de_norte():
    # Vento DE norte = ar indo para o sul (v negativo) → 0°.
    assert _wind_dir_deg(0.0, -5.0) == pytest.approx(0.0)


def test_wind_dir_de_leste():
    # Vento DE leste = ar indo para o oeste (u negativo) → 90°.
    assert _wind_dir_deg(-5.0, 0.0) == pytest.approx(90.0)


def test_wind_dir_de_sul_e_oeste():
    assert _wind_dir_deg(0.0, 5.0) == pytest.approx(180.0)
    assert _wind_dir_deg(5.0, 0.0) == pytest.approx(270.0)


def test_wind_dir_vetorizado_em_0_360():
    rng = np.random.default_rng(3)
    d = _wind_dir_deg(rng.normal(size=50), rng.normal(size=50))
    assert d.shape == (50,)
    assert np.all((d >= 0.0) & (d < 360.0))


# ── _nearest_level_wind ──────────────────────────────────────────────────────


def test_nivel_exato():
    prof = _profile([1000, 850, 500], u=[1, -6, 0], v=[0, 0, 8])
    wind = _nearest_level_wind(prof, 850.0)
    assert wind is not None
    speed, direction = wind
    assert speed == pytest.approx(6.0)
    assert direction == pytest.approx(90.0)  # u=-6 → vento DE leste


def test_nivel_mais_proximo_dentro_da_tolerancia():
    # Pedir 700 sem 700 na coluna → casa com 850? Não: 600 está mais perto.
    prof = _profile([1000, 850, 600], u=[0, 0, 3], v=[0, 0, -4])
    wind = _nearest_level_wind(prof, 700.0)
    assert wind is not None
    assert wind[0] == pytest.approx(5.0)  # hypot(3, -4) → o vento do nível 600


def test_fora_da_tolerancia_none():
    # Coluna sobre relevo alto: só restam níveis altos; pedir 1000 hPa → None.
    prof = _profile([600, 500, 400], u=[1, 1, 1], v=[1, 1, 1])
    assert _nearest_level_wind(prof, 1000.0) is None
    # Mas dentro de uma tolerância explícita maior, casa.
    assert _nearest_level_wind(prof, 1000.0, tolerance_hpa=500.0) is not None


def test_tolerancia_default_exposta():
    assert pytest.approx(100.0) == LEVEL_MATCH_TOLERANCE_HPA


def test_uv_nan_none():
    prof = _profile([850, 500], u=[np.nan, 1.0], v=[0.0, 1.0])
    assert _nearest_level_wind(prof, 850.0) is None
    assert _nearest_level_wind(prof, 500.0) is not None


def test_coluna_vazia_none():
    prof = _profile([], u=[], v=[])
    assert _nearest_level_wind(prof, 850.0) is None


# ── load_point_level_wind_timeseries (load_model_profile monkeypatched) ──────


def test_serie_montada_e_steps_com_falha_pulados(monkeypatch, tmp_path):
    def fake_profile(lon, lat, step=0, **kw):
        if step == 6:
            raise ValueError("step indisponível")  # rede falhou neste step
        return _profile([1000, 850, 500], u=[0, -6, 0], v=[0, 0, 8], step=step)

    monkeypatch.setattr("cartomet_br.data.ecmwf.load_model_profile", fake_profile)
    serie = load_point_level_wind_timeseries(
        -48.0, -1.5, level_hpa=850.0, steps=[0, 3, 6, 9], data_dir=tmp_path
    )
    assert isinstance(serie, PointLevelWindSeries)
    assert serie.level_hpa == pytest.approx(850.0)
    assert list(serie.steps) == [0.0, 3.0, 9.0]  # +6h pulado com aviso
    assert np.allclose(serie.wind_speed, 6.0)
    assert np.allclose(serie.wind_dir, 90.0)
    assert serie.base_time == "00Z 07/07/2026"


def test_nivel_fora_da_tolerancia_em_todos_os_steps_erro(monkeypatch, tmp_path):
    def fake_profile(lon, lat, step=0, **kw):
        return _profile([600, 500], u=[1, 1], v=[1, 1], step=step)

    monkeypatch.setattr("cartomet_br.data.ecmwf.load_model_profile", fake_profile)
    with pytest.raises(ValueError, match="1000"):
        load_point_level_wind_timeseries(
            -70.0, -16.0, level_hpa=1000.0, steps=[0, 3], data_dir=tmp_path
        )


def test_data_dir_none_erro():
    with pytest.raises(ValueError):
        load_point_level_wind_timeseries(-48.0, -1.5, level_hpa=850.0, data_dir=None)  # type: ignore[arg-type]
