"""
Serviço puro da feature "Animar Steps" (GIF/MP4) — CartoMet BR.

Núcleo testável, sem Qt/Matplotlib-GUI: montagem da lista de steps
respeitando o alcance por rodada (06Z/18Z ≤ +144h; 00Z/12Z ≤ +240h),
congelamento de escala entre quadros (a colorbar não "respira"),
nomes de arquivos e codificação GIF (Pillow) / MP4 (imageio-ffmpeg,
extra opcional ``animation``).

A orquestração (workers QThread, replot no canvas) vive em
``gui/animation_engine.py``; o diálogo em ``gui/animation_dialog.py``.
"""

from __future__ import annotations

import importlib.util
import logging
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from cartomet_br.data.olr_timing import OLR_MATURITY_HOURS
from cartomet_br.services.data_service import VALID_STEPS

logger = logging.getLogger(__name__)


class AnimationError(Exception):
    """Erro genérico da camada de animação."""


class EncoderUnavailableError(AnimationError):
    """Codificador solicitado não está instalado (ex.: MP4 sem imageio-ffmpeg)."""


# Alcance máximo de step por rodada (h). Espelha DataService.validate_cycle:
# 06Z/18Z publicam até +144h; 00Z/12Z até +240h.
ANIMATION_MAX_STEP: dict[int, int] = {0: 240, 6: 144, 12: 240, 18: 144}

# Teto do LOCZCIT-PA: a Técnica B desacumula a OLR na rodada-base madura com
# step alvo = step + OLR_MATURITY_HOURS (12h), e o IFS Open Data publica até
# +240h → step ≤ 228h. Além disso o download da OLR falharia (404).
LOCZCIT_MAX_STEP: int = VALID_STEPS[-1] - OLR_MATURITY_HOURS  # 228

# GIF: 255 cores + 1 reservada; paleta adaptativa do 1º quadro.
GIF_MAX_COLORS = 255

# Variáveis cuja escala já é fixa no canvas (olr/precip) — não congelar.
_FIXED_SCALE_VARS = ("olr", "precip")

# Espelho da lista de clamp ≥ 0 de MapCanvas._plot_scalar_contourf.
_CLAMP_ZERO_VARS = ("r", "q", "wind_speed", "temp_grad", "tcwv", "sst_grad")


# ═══════════════════════════════════════════════════════════════════════════════
#  STEPS: grade válida × alcance por rodada
# ═══════════════════════════════════════════════════════════════════════════════


def max_step_for_cycle(cycle: int | None) -> int:
    """Alcance máximo (h) da rodada. ``None`` (auto não resolvida) → 144, conservador."""
    if cycle is None:
        return 144
    return ANIMATION_MAX_STEP.get(int(cycle), 144)


def max_step_for_composition(layer_specs: Sequence[dict], cycle: int | None) -> int:
    """Alcance máximo (h) da animação para a composição.

    Parte do alcance da rodada e reduz para ``LOCZCIT_MAX_STEP`` (+228h) se a
    composição inclui o LOCZCIT-PA — a OLR madura da Técnica B precisa do step
    ``step + 12`` na rodada-base, inexistente além de +240h.
    """
    cap = max_step_for_cycle(cycle)
    if any(spec.get("kind") == "loczcit" for spec in layer_specs):
        cap = min(cap, LOCZCIT_MAX_STEP)
    return cap


def build_animation_steps(start: int, end: int, stride_hours: int, cycle: int | None) -> list[int]:
    """Monta a lista de steps da animação.

    Interseção de ``[start, end]`` com a grade ``VALID_STEPS`` (3/3h até
    +144h, 6/6h de +150h a +240h), limitada ao alcance da rodada — por
    construção, nunca produz um step que dispararia o erro de alcance.

    ``stride_hours=0`` usa a cadência nativa da grade (3h → 6h após +144h).
    Com stride explícito, steps além de +144h que não caem na grade de 6h
    são simplesmente omitidos (a UI oferece inícios alinhados à grade).
    """
    cap = max_step_for_cycle(cycle)
    end = min(end, cap)
    start = max(start, 0)
    if end < start:
        return []
    steps = [s for s in VALID_STEPS if start <= s <= end]
    if stride_hours > 0:
        steps = [s for s in steps if (s - start) % stride_hours == 0]
    return steps


# Extremos de janela (Tmax/Tmin 2 m): step ≥ 3h SEMPRE (janela degenerada no 0),
# independente da técnica — diferente de OLR/precip, que liberam o 0 na Técnica B.
_WINDOW_MIN3_VARS = ("tmax2m", "tmin2m")


def min_start_for_composition(layer_specs: Sequence[dict], technique: str) -> int:
    """Step inicial mínimo permitido pela composição.

    OLR/precipitação no modo Direto valem zero no step 0 (campo acumulado
    desde a análise) — exigem step ≥ 3h. No modo Estabilizada (Técnica B)
    o step 0 é permitido (desacumula da rodada anterior madura). Extremos
    de T 2 m exigem step ≥ 3h em qualquer método.
    """
    for spec in layer_specs:
        if spec.get("kind") != "field":
            continue
        variable = spec.get("variable")
        if variable in _WINDOW_MIN3_VARS:
            return 3
        if technique == "direct" and variable in _FIXED_SCALE_VARS:
            return 3
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
#  NOMES DE ARQUIVO
# ═══════════════════════════════════════════════════════════════════════════════


def _slugify(text: str) -> str:
    """Rótulo seguro para nome de arquivo (sem acentos/espacos/reservados)."""
    text = re.sub(r"[áàâãä]", "a", text.lower())
    text = re.sub(r"[éèêë]", "e", text)
    text = re.sub(r"[íìîï]", "i", text)
    text = re.sub(r"[óòôõö]", "o", text)
    text = re.sub(r"[úùûü]", "u", text)
    text = re.sub(r"[ç]", "c", text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "carta"


def default_animation_filename(
    tag: str,
    cycle: int,
    cycle_date: str,
    start: int,
    end: int,
    stride_hours: int,
    fmt: str,
) -> str:
    """Nome padrão: ``anim_{tag}_{data}_{rodada}Z_f{ini}-f{fim}_{passo}.{fmt}``."""
    stride_label = f"{stride_hours}h" if stride_hours > 0 else "nat"
    return (
        f"anim_{_slugify(tag)}_{cycle_date}_{cycle:02d}Z"
        f"_f{start:03d}-f{end:03d}_{stride_label}.{fmt.lower()}"
    )


def frame_filename(index: int, step: int) -> str:
    """Nome do PNG intermediário de um quadro (ordenável lexicograficamente)."""
    return f"frame_{index:04d}_f{step:03d}.png"


def composition_tag(layer_specs: Sequence[dict]) -> str:
    """Rótulo curto da composição p/ o nome do arquivo (ex.: "sinotica-gh500")."""
    parts: list[str] = []
    for spec in layer_specs:
        kind = spec.get("kind")
        if kind == "synoptic":
            parts.append("sinotica")
        elif kind == "field":
            var = str(spec.get("variable") or "campo")
            level = spec.get("level") or 0
            parts.append(f"{var}{level}" if level else var)
        elif kind == "blocking":
            parts.append("bloqueio")
        elif kind == "loczcit":
            parts.append("zcit")
    return "-".join(parts[:4]) or "carta"


# ═══════════════════════════════════════════════════════════════════════════════
#  ESCALA CONGELADA (a colorbar não pode "respirar" entre quadros)
# ═══════════════════════════════════════════════════════════════════════════════


class FrameScaleTracker:
    """Agrega estatísticas de todos os quadros e emite níveis fixos por camada.

    Replica as regras de escala de ``MapCanvas._plot_scalar_contourf`` e
    ``_plot_scalar_contour`` — que hoje derivam níveis do quadro corrente —
    usando extremos/percentis agregados do intervalo inteiro. O resultado
    alimenta ``MapCanvas.freeze_levels()`` durante a renderização.
    """

    def __init__(self) -> None:
        self._stats: dict[str, dict] = {}

    def observe(self, layer_id: str, variable: str, var_info: dict, values: np.ndarray) -> None:
        """Registra as estatísticas de um quadro para uma camada escalar."""
        if var_info.get("category") == "wind":
            return  # vento não tem escala de preenchimento
        if variable in _FIXED_SCALE_VARS:
            return  # níveis já fixos no canvas
        arr = np.asarray(values, dtype=float)
        if not np.isfinite(arr).any():
            return
        p2, p98 = np.nanpercentile(arr, [2, 98])
        vmin = float(np.nanmin(arr))
        vmax = float(np.nanmax(arr))
        st = self._stats.setdefault(
            layer_id,
            {
                "variable": variable,
                "var_info": var_info,
                "p2": np.inf,
                "p98": -np.inf,
                "vmin": np.inf,
                "vmax": -np.inf,
            },
        )
        st["p2"] = min(st["p2"], float(p2))
        st["p98"] = max(st["p98"], float(p98))
        st["vmin"] = min(st["vmin"], vmin)
        st["vmax"] = max(st["vmax"], vmax)

    def frozen_levels(self) -> dict[str, np.ndarray]:
        """Níveis fixos por ``layer_id`` (camadas sem escala ficam de fora)."""
        out: dict[str, np.ndarray] = {}
        for layer_id, st in self._stats.items():
            levels = self._levels_for(st)
            if levels is not None:
                out[layer_id] = levels
        return out

    @staticmethod
    def _levels_for(st: dict) -> np.ndarray | None:
        var_info: dict = st["var_info"]
        variable: str = st["variable"]

        # Espelho de _plot_scalar_contour (linhas apenas, ex.: gh).
        if var_info.get("plot_type") == "contour":
            vmin, vmax = st["p2"], st["p98"]
            if abs(vmax - vmin) < 1e-10:
                return None
            step = max(1, int((vmax - vmin) / 20))
            levels = np.arange(int(vmin), int(vmax) + step, step)
            return levels if len(levels) >= 2 else None

        # Espelho de _plot_scalar_contourf, caso simétrico (ω, div, vort).
        if var_info.get("symmetric", False):
            vmax = max(abs(st["vmin"]), abs(st["vmax"])) * 0.9
            if vmax < 1e-10:
                vmax = 1.0
            return np.linspace(-vmax, vmax, 21)

        # Espelho de _plot_scalar_contourf, caso geral (percentis 2–98).
        vmin, vmax = st["p2"], st["p98"]
        margin = (vmax - vmin) * 0.05
        lv_min = vmin - margin
        lv_max = vmax + margin
        if var_info.get("category") == "wind_speed" or variable in _CLAMP_ZERO_VARS:
            lv_min = max(0, lv_min)
        if abs(lv_max - lv_min) < 1e-10:
            lv_min -= 1.0
            lv_max += 1.0
        return np.linspace(lv_min, lv_max, 21)


# ═══════════════════════════════════════════════════════════════════════════════
#  CODIFICAÇÃO (streaming: nunca mais que ~2 quadros decodificados em RAM)
# ═══════════════════════════════════════════════════════════════════════════════


def mp4_available() -> bool:
    """True se o extra ``animation`` (imageio-ffmpeg) está instalado."""
    return importlib.util.find_spec("imageio_ffmpeg") is not None


def even_crop_size(width: int, height: int) -> tuple[int, int]:
    """Maior tamanho par ≤ (width, height) — o yuv420p exige dimensões pares."""
    return width - (width % 2), height - (height % 2)


MACRO_BLOCK = 16


def block_pad_size(width: int, height: int, block: int = MACRO_BLOCK) -> tuple[int, int]:
    """Menor tamanho múltiplo de ``block`` ≥ (width, height).

    O H.264 trabalha com macroblocos de 16 px; se as dimensões não forem
    múltiplas, o imageio-ffmpeg REDIMENSIONA (esticando/distorcendo) o vídeo.
    Acolchoar com branco até o múltiplo preserva a geometria da carta.
    """
    return -(-width // block) * block, -(-height // block) * block


def _conform(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Normaliza um quadro ao tamanho do 1º (letterbox branco se divergir)."""
    if im.size == size:
        return im
    logger.warning("Quadro com dimensão %s ≠ %s — aplicando letterbox.", im.size, size)
    return ImageOps.pad(im, size, color="white")


def _master_palette(frames: Sequence[Path], size: tuple[int, int]) -> Image.Image:
    """Paleta adaptativa (mediancut) de uma amostra de até 5 quadros.

    Usar só o 1º quadro distorceria cores que aparecem apenas em quadros
    posteriores (ex.: categoria convectiva que surge em steps tardios);
    amostrar início/meio/fim cobre a união das cores do intervalo.
    """
    n = len(frames)
    idx = sorted({round(i * (n - 1) / 4) for i in range(5)}) if n > 5 else range(n)
    thumb_size = (max(1, size[0] // 2), max(1, size[1] // 2))
    thumbs = []
    for i in idx:
        with Image.open(frames[i]) as im:
            thumbs.append(_conform(im.convert("RGB"), size).resize(thumb_size))
    strip = Image.new("RGB", (thumb_size[0] * len(thumbs), thumb_size[1]))
    for i, thumb in enumerate(thumbs):
        strip.paste(thumb, (i * thumb_size[0], 0))
    return strip.quantize(colors=GIF_MAX_COLORS, method=Image.Quantize.MEDIANCUT)


def encode_gif(frames: Sequence[Path], out_path: Path, fps: float, loop: int = 0) -> Path:
    """Codifica PNGs em GIF animado (Pillow).

    Uma paleta mestra única (mediancut, 255 cores, amostrada do intervalo)
    é aplicada a todos os quadros — cores estáveis (a colorbar não "pisca")
    e arquivo menor. ``loop=0`` = repetição infinita.
    """
    if not frames:
        raise AnimationError("Nenhum quadro para codificar.")
    if fps <= 0:
        raise AnimationError(f"fps inválido: {fps}")
    duration_ms = max(20, round(1000.0 / fps))

    with Image.open(frames[0]) as im:
        size = im.convert("RGB").size
    palette = _master_palette(frames, size)

    def _quantized(path: Path) -> Image.Image:
        with Image.open(path) as im:
            return _conform(im.convert("RGB"), size).quantize(palette=palette)

    def _rest() -> Iterator[Image.Image]:
        for path in frames[1:]:
            yield _quantized(path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _quantized(frames[0]).save(
        out_path,
        save_all=True,
        append_images=_rest(),
        duration=duration_ms,
        loop=loop,
        disposal=2,
    )
    logger.info("GIF gerado: %s (%d quadros, %d ms/quadro)", out_path, len(frames), duration_ms)
    return out_path


def encode_mp4(frames: Sequence[Path], out_path: Path, fps: float, crf: int = 23) -> Path:
    """Codifica PNGs em MP4 H.264 (imageio-ffmpeg — extra ``animation``).

    Os quadros são acolchoados com branco até o múltiplo de 16 (macrobloco
    do H.264) — sem isso o ffmpeg REDIMENSIONA o vídeo, esticando a carta.
    O acolchoamento também satisfaz o requisito de dimensões pares do
    ``yuv420p`` (compatível com players comuns). ``+faststart`` posiciona o
    moov atom no início (streaming/preview imediato).
    """
    if not frames:
        raise AnimationError("Nenhum quadro para codificar.")
    if fps <= 0:
        raise AnimationError(f"fps inválido: {fps}")
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise EncoderUnavailableError(
            "Exportação MP4 requer o extra de animação.\nInstale com: uv sync --extra animation"
        ) from exc

    with Image.open(frames[0]) as im:
        base_size = im.size
    width, height = block_pad_size(*base_size)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = imageio_ffmpeg.write_frames(
        str(out_path),
        size=(width, height),
        fps=fps,
        codec="libx264",
        pix_fmt_out="yuv420p",
        output_params=["-crf", str(crf), "-preset", "medium", "-movflags", "+faststart"],
    )
    writer.send(None)  # inicializa o subprocesso ffmpeg
    try:
        for path in frames:
            with Image.open(path) as im:
                rgb = _conform(im.convert("RGB"), base_size)
            if (width, height) != base_size:
                padded = Image.new("RGB", (width, height), "white")
                padded.paste(rgb, (0, 0))
                rgb = padded
            writer.send(np.ascontiguousarray(np.asarray(rgb)).tobytes())
    finally:
        writer.close()
    logger.info("MP4 gerado: %s (%d quadros, %.1f fps)", out_path, len(frames), fps)
    return out_path
