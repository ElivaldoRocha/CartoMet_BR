"""Testes do serviço puro de animação (steps, escala congelada, encoders)."""

import numpy as np
import pytest
from PIL import Image

from cartomet_br.services.animation_service import (
    LOCZCIT_MAX_STEP,
    AnimationError,
    FrameScaleTracker,
    block_pad_size,
    build_animation_steps,
    composition_tag,
    default_animation_filename,
    encode_gif,
    encode_mp4,
    even_crop_size,
    frame_filename,
    max_step_for_composition,
    max_step_for_cycle,
    min_start_for_composition,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  Steps: grade válida × alcance por rodada
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildAnimationSteps:
    def test_grade_nativa_3h(self):
        assert build_animation_steps(0, 12, 0, 0) == [0, 3, 6, 9, 12]

    def test_travessia_de_144h_muda_cadencia(self):
        # 3/3h até +144, 6/6h depois — nunca gera step inexistente (ex.: 147)
        assert build_animation_steps(138, 162, 0, 0) == [138, 141, 144, 150, 156, 162]

    def test_cap_rodada_06z_e_18z(self):
        assert build_animation_steps(120, 240, 0, 6)[-1] == 144
        assert build_animation_steps(120, 240, 0, 18)[-1] == 144

    def test_rodadas_00z_12z_alcancam_240(self):
        assert build_animation_steps(228, 240, 0, 0) == [228, 234, 240]
        assert build_animation_steps(228, 240, 0, 12) == [228, 234, 240]

    def test_stride_12h(self):
        assert build_animation_steps(0, 48, 12, 0) == [0, 12, 24, 36, 48]

    def test_stride_24h_ate_240(self):
        steps = build_animation_steps(0, 240, 24, 0)
        assert steps == list(range(0, 241, 24))

    def test_intervalo_invertido_vazio(self):
        assert build_animation_steps(48, 24, 0, 0) == []

    def test_start_negativo_clampa_em_zero(self):
        assert build_animation_steps(-6, 6, 0, 0) == [0, 3, 6]

    def test_cycle_none_conservador(self):
        assert max_step_for_cycle(None) == 144
        assert build_animation_steps(138, 240, 0, None)[-1] == 144

    def test_max_step_for_cycle(self):
        assert max_step_for_cycle(0) == 240
        assert max_step_for_cycle(6) == 144
        assert max_step_for_cycle(12) == 240
        assert max_step_for_cycle(18) == 144

    def test_max_step_for_composition_loczcit(self):
        # Técnica B: OLR madura precisa de base step = step + 12 ≤ 240 → 228
        assert LOCZCIT_MAX_STEP == 228
        loczcit = [{"kind": "loczcit", "axis": False}]
        assert max_step_for_composition(loczcit, 0) == 228
        assert max_step_for_composition(loczcit, 12) == 228
        assert max_step_for_composition(loczcit, 6) == 144  # cap da rodada prevalece
        assert max_step_for_composition([{"kind": "synoptic"}], 0) == 240
        assert max_step_for_composition([{"kind": "blocking"}, *loczcit], 0) == 228


class TestMinStartForComposition:
    def test_olr_direto_exige_3h(self):
        specs = [{"kind": "field", "variable": "olr", "step": 12}]
        assert min_start_for_composition(specs, "direct") == 3

    def test_precip_direto_exige_3h(self):
        specs = [{"kind": "field", "variable": "precip", "step": 12}]
        assert min_start_for_composition(specs, "direct") == 3

    def test_olr_estabilizada_permite_0h(self):
        specs = [{"kind": "field", "variable": "olr", "step": 12}]
        assert min_start_for_composition(specs, "stabilized") == 0

    def test_sinotica_sem_restricao(self):
        specs = [{"kind": "synoptic", "step": 0}]
        assert min_start_for_composition(specs, "direct") == 0

    def test_t2_extremos_exigem_3h_em_qualquer_tecnica(self):
        # Extremos de janela: diferente de OLR/precip, a Técnica B NÃO libera o 0
        for var in ("tmax2m", "tmin2m"):
            specs = [{"kind": "field", "variable": var, "step": 12}]
            assert min_start_for_composition(specs, "direct") == 3
            assert min_start_for_composition(specs, "stabilized") == 3


# ═══════════════════════════════════════════════════════════════════════════════
#  Nomes de arquivo
# ═══════════════════════════════════════════════════════════════════════════════


class TestFilenames:
    def test_default_animation_filename(self):
        name = default_animation_filename("Sinótica + gh500", 0, "20260702", 0, 48, 0, "GIF")
        assert name == "anim_sinotica-gh500_20260702_00Z_f000-f048_nat.gif"

    def test_stride_explicito(self):
        name = default_animation_filename("bloqueio", 12, "20260702", 0, 240, 24, "mp4")
        assert name == "anim_bloqueio_20260702_12Z_f000-f240_24h.mp4"

    def test_frame_filename_ordenavel(self):
        assert frame_filename(7, 18) == "frame_0007_f018.png"
        assert frame_filename(0, 0) < frame_filename(10, 240)

    def test_composition_tag(self):
        specs = [
            {"kind": "synoptic", "step": 0},
            {"kind": "field", "variable": "gh", "level": 500},
            {"kind": "blocking"},
            {"kind": "loczcit", "axis": True},
        ]
        assert composition_tag(specs) == "sinotica-gh500-bloqueio-zcit"
        assert composition_tag([{"kind": "field", "variable": "olr"}]) == "olr"
        assert composition_tag([]) == "carta"


# ═══════════════════════════════════════════════════════════════════════════════
#  Escala congelada (espelha _plot_scalar_contourf / _plot_scalar_contour)
# ═══════════════════════════════════════════════════════════════════════════════


class TestFrameScaleTracker:
    def test_caso_geral_agrega_percentis(self):
        tracker = FrameScaleTracker()
        info = {"plot_type": "contourf", "symmetric": False, "category": "scalar"}
        # Quadro 1: 0..100 | Quadro 2: 50..200 → escala global deve cobrir ambos
        tracker.observe("t_850", "t", info, np.linspace(0, 100, 1000).reshape(20, 50))
        tracker.observe("t_850", "t", info, np.linspace(50, 200, 1000).reshape(20, 50))
        levels = tracker.frozen_levels()["t_850"]
        assert len(levels) == 21
        assert levels[0] < 5  # cobre o mínimo do quadro 1 (p2≈2, com margem)
        assert levels[-1] > 195  # cobre o máximo do quadro 2 (p98≈197, com margem)

    def test_simetrico_usa_max_global(self):
        tracker = FrameScaleTracker()
        info = {"plot_type": "contourf", "symmetric": True, "category": "scalar"}
        tracker.observe("w_500", "w", info, np.full((10, 10), -2.0))
        tracker.observe("w_500", "w", info, np.full((10, 10), 8.0))
        levels = tracker.frozen_levels()["w_500"]
        assert levels[0] == pytest.approx(-7.2)  # -0.9 * 8
        assert levels[-1] == pytest.approx(7.2)
        assert len(levels) == 21

    def test_clamp_zero_para_umidade(self):
        tracker = FrameScaleTracker()
        info = {"plot_type": "contourf", "symmetric": False, "category": "scalar"}
        tracker.observe("r_850", "r", info, np.linspace(0, 100, 400).reshape(20, 20))
        levels = tracker.frozen_levels()["r_850"]
        assert levels[0] >= 0  # clamp da lista de variáveis não-negativas

    def test_contour_gera_arange_inteiro(self):
        tracker = FrameScaleTracker()
        info = {"plot_type": "contour", "symmetric": False, "category": "scalar"}
        tracker.observe("gh_500", "gh", info, np.linspace(5400, 5800, 400).reshape(20, 20))
        levels = tracker.frozen_levels()["gh_500"]
        assert levels.dtype.kind in "iu" or np.allclose(levels, np.round(levels))
        assert len(levels) >= 2

    def test_campo_constante_nao_quebra(self):
        tracker = FrameScaleTracker()
        info_f = {"plot_type": "contourf", "symmetric": False, "category": "scalar"}
        tracker.observe("t_850", "t", info_f, np.full((10, 10), 25.0))
        levels = tracker.frozen_levels()["t_850"]
        assert len(levels) == 21
        assert levels[0] < levels[-1]  # guarda anti-degenerado (±1.0)

        info_c = {"plot_type": "contour", "symmetric": False, "category": "scalar"}
        tracker.observe("gh_500", "gh", info_c, np.full((10, 10), 5500.0))
        assert "gh_500" not in tracker.frozen_levels()  # contour constante → sem níveis

    def test_pula_vento_olr_precip(self):
        tracker = FrameScaleTracker()
        tracker.observe("wind_850", "wind", {"category": "wind"}, np.ones((5, 5)))
        tracker.observe(
            "olr", "olr", {"plot_type": "contourf", "category": "radiation"}, np.ones((5, 5))
        )
        tracker.observe(
            "precip", "precip", {"plot_type": "contourf", "category": "scalar"}, np.ones((5, 5))
        )
        assert tracker.frozen_levels() == {}

    def test_tudo_nan_ignorado(self):
        tracker = FrameScaleTracker()
        info = {"plot_type": "contourf", "symmetric": False, "category": "scalar"}
        tracker.observe("t_850", "t", info, np.full((10, 10), np.nan))
        assert tracker.frozen_levels() == {}


# ═══════════════════════════════════════════════════════════════════════════════
#  Encoders
# ═══════════════════════════════════════════════════════════════════════════════


def _make_frames(tmp_path, n=3, size=(64, 48)):
    """Gera PNGs sintéticos com conteúdo distinto por quadro."""
    paths = []
    for i in range(n):
        arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        arr[:, :, i % 3] = 60 + 60 * i  # cor muda por quadro
        path = tmp_path / f"frame_{i:04d}.png"
        Image.fromarray(arr).save(path)
        paths.append(path)
    return paths


class TestEncodeGif:
    def test_gif_com_3_quadros(self, tmp_path):
        frames = _make_frames(tmp_path, n=3)
        out = encode_gif(frames, tmp_path / "anim.gif", fps=2)
        with Image.open(out) as gif:
            assert gif.n_frames == 3
            assert gif.info["duration"] == 500  # 1000/2 fps
            assert gif.info["loop"] == 0  # infinito

    def test_quadro_divergente_recebe_letterbox(self, tmp_path):
        frames = _make_frames(tmp_path, n=2)
        odd = tmp_path / "frame_9999.png"
        Image.new("RGB", (32, 24), "red").save(odd)
        out = encode_gif([*frames, odd], tmp_path / "anim.gif", fps=1)
        with Image.open(out) as gif:
            assert gif.n_frames == 3
            assert gif.size == (64, 48)  # tamanho do 1º quadro prevalece

    def test_sem_quadros_levanta_erro(self, tmp_path):
        with pytest.raises(AnimationError):
            encode_gif([], tmp_path / "anim.gif", fps=2)

    def test_fps_invalido(self, tmp_path):
        frames = _make_frames(tmp_path, n=1)
        with pytest.raises(AnimationError):
            encode_gif(frames, tmp_path / "anim.gif", fps=0)


class TestEncodeMp4:
    def test_mp4_com_dimensao_impar(self, tmp_path):
        pytest.importorskip("imageio_ffmpeg")
        frames = _make_frames(tmp_path, n=4, size=(65, 49))  # ímpar → crop par
        out = encode_mp4(frames, tmp_path / "anim.mp4", fps=2)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_even_crop_size(self):
        assert even_crop_size(65, 49) == (64, 48)
        assert even_crop_size(64, 48) == (64, 48)

    def test_block_pad_size(self):
        # Caso real do usuário: 1266×912 → 1280×912 (pad, NÃO resize do ffmpeg)
        assert block_pad_size(1266, 912) == (1280, 912)
        assert block_pad_size(1280, 912) == (1280, 912)
        assert block_pad_size(65, 49) == (80, 64)

    def test_sem_quadros_levanta_erro(self, tmp_path):
        with pytest.raises(AnimationError):
            encode_mp4([], tmp_path / "anim.mp4", fps=2)
