"""Testes da desacumulação temporal (OLR/precipitação) — trava a ciência correta.

Confirma que a precipitação:
  • no modo "direct" usa a RODADA SELECIONADA com janela [step-3, step] (não recua 12h);
  • no modo "stabilized" recua 12h e usa a janela madura target=step+12;
  • é SEMPRE não-negativa (acúmulo monotônico → tp(hi)-tp(lo) ≥ 0; clip defensivo);
  • "não choveu na janela" (tp(hi)==tp(lo)) → 0, nunca negativo.

A alegação de "chuva negativa caótica" da desacumulação é fisicamente impossível.
"""

from unittest.mock import patch

import numpy as np
import pytest

from cartomet_br.data.ecmwf import _resolve_accum_window, load_precip

# ═══════════════════════════════════════════════════════════════════════════════
#  Janela de desacumulação — direto (rodada selecionada) vs estabilizado (Técnica B)
# ═══════════════════════════════════════════════════════════════════════════════


class TestResolveAccumWindowDirect:
    def test_usa_rodada_selecionada_janela_3h(self):
        """Direto: rodada SELECIONADA, janela de 3h [step-3, step]. NÃO recua 12h."""
        rc, rd, hi, lo, label = _resolve_accum_window("direct", 12, "20260604", 12)
        assert rc == 12  # rodada selecionada (não recua para a chuva)
        assert rd == "20260604"
        assert hi == 12 and lo == 9  # janela de 3h
        assert "Direta" in label

    def test_janela_6h_apos_144h(self):
        rc, rd, hi, lo, _ = _resolve_accum_window("direct", 0, "20260604", 150)
        assert hi == 150 and lo == 144  # após 144h o ECMWF tem resolução de 6h

    def test_step0_nao_fica_negativo(self):
        _, _, hi, lo, _ = _resolve_accum_window("direct", 0, "20260604", 0)
        assert hi == 0 and lo == 0  # step_lo travado em 0 (sem step negativo)


class TestResolveAccumWindowStabilized:
    def test_recua_12h_target_step_mais_12(self):
        """Estabilizado: rodada-base 12h antes; target=step+12; janela madura de 3h."""
        rc, rd, hi, lo, label = _resolve_accum_window("stabilized", 12, "20260604", 0)
        assert rc == 0 and rd == "20260604"  # 12Z - 12h = 00Z mesmo dia
        assert hi == 12 and lo == 9
        assert "Estabilizada" in label

    def test_cruza_meia_noite(self):
        rc, rd, hi, lo, _ = _resolve_accum_window("stabilized", 0, "20260604", 0)
        assert rc == 12 and rd == "20260603"  # 00Z - 12h = 12Z do dia anterior

    def test_previsao_futura(self):
        # 12Z + 3h → base 00Z, target=15, lo=12 (janela madura, sem spin-up)
        rc, rd, hi, lo, _ = _resolve_accum_window("stabilized", 12, "20260604", 3)
        assert rc == 0 and hi == 15 and lo == 12


# ═══════════════════════════════════════════════════════════════════════════════
#  Não-negatividade da precipitação (sem rede — _read_accum_field mockado)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPrecipNonNegative:
    @patch("cartomet_br.data.ecmwf._read_accum_field")
    def test_precip_nunca_negativa_e_janela_correta(self, mock_read, tmp_path):
        """Dado acúmulo monotônico, precip = (tp_hi - tp_lo)*1000 mm ≥ 0 sempre.

        Inclui região onde 'não choveu na janela' (tp_hi == tp_lo) → 0, não negativo.
        """
        lons = np.linspace(-50, -30, 5)
        lats = np.linspace(-10, 5, 4)
        tp_hi = np.full((4, 5), 0.012)  # 12 mm acumulados (step alto)
        tp_lo = np.full((4, 5), 0.009)  # 9 mm acumulados (3h antes)
        tp_hi[0, 0] = tp_lo[0, 0] = 0.020  # ponto seco na janela: hi == lo

        def _side(param, cyc, date, step, *args, **kwargs):
            vals = tp_hi if step >= 12 else tp_lo
            return vals, lons, lats, "2026-06-04T12:00", "12Z 04/06/2026"

        mock_read.side_effect = _side

        r = load_precip(
            [-50, -10, -30, 5],
            step=12,
            cycle=12,
            cycle_date="20260604",
            data_dir=tmp_path,
            smoothing_sigma=0.0,
            technique="direct",
        )

        assert np.nanmin(r.values) >= 0.0  # NUNCA negativo
        assert r.values[0, 0] == pytest.approx(0.0)  # "não choveu" → 0
        assert np.nanmax(r.values) == pytest.approx(3.0)  # 12 − 9 = 3 mm/3h

    @patch("cartomet_br.data.ecmwf._read_accum_field")
    def test_clip_protege_contra_negativo_espurio(self, mock_read, tmp_path):
        """Mesmo se um ponto vier com hi < lo (artefato), o clip garante ≥ 0."""
        lons = np.linspace(-50, -30, 4)
        lats = np.linspace(-10, 5, 3)
        tp_hi = np.full((3, 4), 0.010)
        tp_lo = np.full((3, 4), 0.009)
        tp_hi[1, 1] = 0.008  # artefato: hi < lo → diferença negativa

        def _side(param, cyc, date, step, *args, **kwargs):
            return (tp_hi if step >= 12 else tp_lo), lons, lats, "", ""

        mock_read.side_effect = _side
        r = load_precip(
            [-50, -10, -30, 5],
            step=12,
            cycle=0,
            cycle_date="20260604",
            data_dir=tmp_path,
            smoothing_sigma=0.0,
            technique="direct",
        )
        assert np.nanmin(r.values) >= 0.0  # clip(…, 0, None) elimina o negativo
