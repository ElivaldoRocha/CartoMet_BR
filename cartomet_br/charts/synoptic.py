"""
Geração de cartas sinóticas profissionais.

Campos plotados:
- PNMM (Pressão ao Nível Médio do Mar)
- Espessura 1000-500 hPa
- Centros de Alta (H) e Baixa (L)
"""

import logging
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from scipy.ndimage import maximum_filter, minimum_filter

from cartomet_br.core.config import Config, COLORS, LEVELS
from cartomet_br.data.ecmwf import load_synoptic_data, SynopticData

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  FUNÇÃO PARA PLOTAR CENTROS H/L
# ═══════════════════════════════════════════════════════════════════════════════

def plot_maxmin_points(
    ax,
    lon: np.ndarray,
    lat: np.ndarray,
    data: np.ndarray,
    extrema: str,
    nsize: int,
    symbol: str,
    color: str = "k",
    transform=None,
    min_distance: int = 10,
    threshold: float | None = None,
    max_points: int = 15,
):
    """
    Plota símbolos H/L nos máximos/mínimos locais com filtros de qualidade.
    
    Parâmetros
    ----------
    ax : matplotlib axes
        Eixo do plot
    lon, lat : np.ndarray
        Coordenadas (2D meshgrid)
    data : np.ndarray
        Campo 2D (ex: PNMM)
    extrema : str
        'max' para máximos (H), 'min' para mínimos (L)
    nsize : int
        Tamanho da janela do filtro (pixels)
    symbol : str
        Símbolo a plotar ('H' ou 'L')
    color : str
        Cor do símbolo
    min_distance : int
        Distância mínima em pixels entre símbolos
    threshold : float, opcional
        Limiar de intensidade para filtrar extremos fracos
    max_points : int
        Número máximo de símbolos a plotar
    """
    if transform is None:
        transform = ccrs.PlateCarree()

    # Aplica filtro morfológico
    if extrema == "max":
        data_ext = maximum_filter(data, nsize, mode="nearest")
    elif extrema == "min":
        data_ext = minimum_filter(data, nsize, mode="nearest")
    else:
        raise ValueError("extrema deve ser 'max' ou 'min'")

    # Encontra candidatos
    mxy, mxx = np.where(data_ext == data)
    
    if len(mxy) == 0:
        return

    # Coleta candidatos com suas intensidades
    candidates = []
    for i in range(len(mxy)):
        value = data[mxy[i], mxx[i]]
        
        # Aplica threshold se especificado
        if threshold is not None:
            if extrema == "max" and value < threshold:
                continue
            if extrema == "min" and value > threshold:
                continue
        
        candidates.append({
            "y": mxy[i],
            "x": mxx[i],
            "lon": lon[mxy[i], mxx[i]],
            "lat": lat[mxy[i], mxx[i]],
            "value": value,
            "intensity": abs(value - 1013.25),
        })
    
    # Ordena por intensidade
    candidates.sort(key=lambda c: c["intensity"], reverse=True)
    
    # Filtra por distância mínima
    selected = []
    for cand in candidates:
        too_close = False
        for sel in selected:
            dist = np.sqrt((cand["y"] - sel["y"])**2 + (cand["x"] - sel["x"])**2)
            if dist < min_distance:
                too_close = True
                break
        
        if not too_close:
            selected.append(cand)
            
        if len(selected) >= max_points:
            break
    
    # Plota com efeito de contorno
    path_effects = [pe.withStroke(linewidth=3, foreground="white")]
    
    for cand in selected:
        ax.text(
            cand["lon"], cand["lat"],
            symbol,
            color=color,
            fontsize=20,
            fontweight="bold",
            ha="center",
            va="center",
            transform=transform,
            path_effects=path_effects,
            zorder=20,
        )
        ax.text(
            cand["lon"], cand["lat"],
            f"\n{int(cand['value'])}",
            color=color,
            fontsize=10,
            fontweight="bold",
            ha="center",
            va="top",
            transform=transform,
            path_effects=path_effects,
            zorder=20,
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  FUNÇÃO PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def create_synoptic_chart(
    config: Config | None = None,
    extent: list[float] | None = None,
    step: int = 0,
    output_filename: str | None = None,
    show: bool = True,
    return_fig: bool = False,
):
    """
    Gera carta sinótica com qualidade profissional.
    
    Parâmetros
    ----------
    config : Config, opcional
        Configuração do CartoMet_BR. Se None, usa padrão.
    extent : list[float], opcional
        Sobrescreve extent do config se fornecido
    step : int
        Passo de previsão em horas (0 = análise)
    output_filename : str, opcional
        Nome do arquivo de saída
    show : bool
        Se True, exibe a figura
    return_fig : bool
        Se True, retorna (fig, ax) para manipulação adicional
        
    Retorna
    -------
    Path ou tuple
        Caminho do arquivo salvo, ou (fig, ax, data) se return_fig=True
    """
    # Configuração
    if config is None:
        config = Config()
    if extent is not None:
        config.extent = extent
    
    # Carrega dados
    data = load_synoptic_data(
        extent=config.extent,
        step=step,
        data_dir=config.data_dir,
        smoothing_sigma=config.smoothing_sigma,
    )
    
    # Cria figura
    logger.info("Gerando figura...")
    
    fig = plt.figure(figsize=config.figsize, facecolor="white")
    ax = plt.axes(projection=ccrs.PlateCarree())
    ax.set_extent(
        [config.extent[0], config.extent[2], config.extent[1], config.extent[3]],
        crs=ccrs.PlateCarree()
    )
    
    # Fundo geográfico
    ax.add_feature(cfeature.OCEAN, facecolor=COLORS["ocean"], zorder=0)
    ax.add_feature(cfeature.LAND, facecolor=COLORS["land"], zorder=1)
    ax.add_feature(cfeature.LAKES, facecolor=COLORS["ocean"], edgecolor="none", zorder=1)
    ax.add_feature(cfeature.COASTLINE, linewidth=0.6, edgecolor=COLORS["coastline"], zorder=5)
    ax.add_feature(cfeature.BORDERS, linewidth=0.4, edgecolor=COLORS["borders"], linestyle="--", zorder=5)
    ax.add_feature(cfeature.STATES, linewidth=0.2, edgecolor=COLORS["states"], zorder=4)
    
    # Espessura 1000-500 hPa
    thickness_levels = np.arange(
        LEVELS["thickness"]["min"],
        LEVELS["thickness"]["max"],
        LEVELS["thickness"]["step"]
    )
    thickness_levels_no_5400 = thickness_levels[thickness_levels != 5400]
    
    cs_thickness = ax.contour(
        data.lons, data.lats, data.thickness,
        levels=thickness_levels_no_5400,
        colors=[
            COLORS["thickness_cold"] if lv < 5400 else COLORS["thickness_warm"]
            for lv in thickness_levels_no_5400
        ],
        linestyles="dashed",
        linewidths=0.8,
        transform=ccrs.PlateCarree(),
        zorder=3,
    )
    
    clabels_thick = ax.clabel(cs_thickness, inline=True, fontsize=8, fmt="%1.0f")
    for txt in clabels_thick:
        txt.set_path_effects([pe.withStroke(linewidth=2, foreground="white")])
    
    # Linha de 5400m em destaque
    cs_5400 = ax.contour(
        data.lons, data.lats, data.thickness,
        levels=[5400],
        colors=COLORS["thickness_5400"],
        linestyles="solid",
        linewidths=2.5,
        transform=ccrs.PlateCarree(),
        zorder=4,
    )
    clabels_5400 = ax.clabel(cs_5400, inline=True, fontsize=9, fmt="%1.0f")
    for txt in clabels_5400:
        txt.set_path_effects([pe.withStroke(linewidth=3, foreground="white")])
    
    # PNMM
    pnmm_levels = np.arange(
        LEVELS["pnmm"]["min"],
        LEVELS["pnmm"]["max"],
        LEVELS["pnmm"]["step"]
    )
    
    cs_pnmm = ax.contour(
        data.lons, data.lats, data.pnmm,
        levels=pnmm_levels,
        colors=COLORS["pnmm_contour"],
        linewidths=1.0,
        transform=ccrs.PlateCarree(),
        zorder=6,
    )
    
    clabels_pnmm = ax.clabel(cs_pnmm, inline=True, fontsize=9, fmt="%1.0f")
    for txt in clabels_pnmm:
        txt.set_path_effects([pe.withStroke(linewidth=2.5, foreground="white")])
    
    # Centros H/L
    plot_maxmin_points(
        ax, data.lon2d, data.lat2d, data.pnmm,
        extrema="max", nsize=80, symbol="H",
        color=COLORS["high_pressure"],
        min_distance=25, threshold=1018, max_points=8,
    )
    
    plot_maxmin_points(
        ax, data.lon2d, data.lat2d, data.pnmm,
        extrema="min", nsize=60, symbol="L",
        color=COLORS["low_pressure"],
        min_distance=20, threshold=1008, max_points=10,
    )
    
    # Gridlines
    gl = ax.gridlines(
        crs=ccrs.PlateCarree(),
        draw_labels=True,
        linewidth=0.3,
        color="#CCCCCC",
        alpha=0.8,
        linestyle="-",
        x_inline=False,
        y_inline=False,
    )
    gl.top_labels = False
    gl.right_labels = False
    gl.xlabel_style = {"size": 9, "color": "#333333"}
    gl.ylabel_style = {"size": 9, "color": "#333333"}
    
    # Títulos
    ax.set_title(
        f"ECMWF IFS — PNMM (hPa) + Espessura 1000-500 hPa (m)\n"
        f"Válido: {data.valid_time} UTC | Step: +{step}h",
        fontsize=13,
        fontweight="bold",
        loc="left",
        color="#1A1A1A",
    )
    ax.set_title(
        f"Região: [{config.extent[0]}°, {config.extent[1]}°] a [{config.extent[2]}°, {config.extent[3]}°]",
        fontsize=10,
        loc="right",
        color="#666666",
    )
    
    # Legenda fora do mapa
    legend_elements = [
        Line2D([0], [0], color=COLORS["pnmm_contour"], linewidth=1.5, label="PNMM (hPa)"),
        Line2D([0], [0], color=COLORS["thickness_warm"], linewidth=1.0, linestyle="--",
               label="Espessura > 5400m (quente)"),
        Line2D([0], [0], color=COLORS["thickness_cold"], linewidth=1.0, linestyle="--",
               label="Espessura < 5400m (frio)"),
        Line2D([0], [0], color=COLORS["thickness_5400"], linewidth=2.5,
               label="5400m (transição térmica)"),
        Line2D([0], [0], marker="$H$", color=COLORS["high_pressure"], linestyle="None",
               markersize=12, label="Centro de alta pressão"),
        Line2D([0], [0], marker="$L$", color=COLORS["low_pressure"], linestyle="None",
               markersize=12, label="Centro de baixa pressão"),
    ]
    
    legend = ax.legend(
        handles=legend_elements,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.02),
        ncol=3,
        fontsize=9,
        frameon=True,
        fancybox=True,
        framealpha=0.95,
        edgecolor="#CCCCCC",
    )
    legend.get_frame().set_linewidth(0.5)
    
    # Créditos
    fig.text(
        0.98, 0.01,
        "Dados: ECMWF Open Data (IFS) | CC BY 4.0",
        fontsize=7,
        ha="right",
        va="bottom",
        color="#888888",
        style="italic",
    )
    
    # Layout
    plt.tight_layout(rect=[0, 0.06, 1, 1])
    
    # Retorna fig/ax para modo interativo
    if return_fig:
        return fig, ax, data
    
    # Salva
    if output_filename is None:
        output_filename = f"synoptic_ecmwf_{data.valid_time.replace(':', '')}_f{step:03d}.png"
    
    output_path = config.output_dir / output_filename
    plt.savefig(
        output_path,
        dpi=config.dpi,
        bbox_inches="tight",
        pad_inches=0.1,
        facecolor="white",
        edgecolor="none",
    )
    logger.info("Figura salva em: %s", output_path)
    
    if show:
        plt.show()
    else:
        plt.close()
    
    return output_path
