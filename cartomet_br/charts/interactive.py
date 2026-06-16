"""
Ferramenta interativa para desenho de frentes meteorológicas.

Layout profissional:
- Mapa central com carta sinótica (opcional)
- Painel lateral direito com controles e legenda de simbologias
- Legenda sinótica na parte inferior (quando aplicável)
- Título limpo no topo
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.backend_bases import KeyEvent, MouseEvent
from matplotlib.figure import Figure
from matplotlib.text import Text
from scipy.interpolate import make_interp_spline

from cartomet_br.charts.synoptic import create_synoptic_chart
from cartomet_br.core.config import Config
from cartomet_br.data.ecmwf import SynopticData
from cartomet_br.symbols import MODOS

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  INTERPOLAÇÃO SPLINE
# ═══════════════════════════════════════════════════════════════════════════════

N_INTERP = 150


def interpolar_pontos(
    xs: list[float] | np.ndarray, ys: list[float] | np.ndarray, n: int = N_INTERP
) -> tuple[np.ndarray, np.ndarray]:
    """Densifica pontos clicados em curva suave via spline cúbica."""
    xs = np.array(xs, dtype=float)
    ys = np.array(ys, dtype=float)
    if len(xs) < 2:
        return xs, ys

    dists = np.hypot(np.diff(xs), np.diff(ys))
    t = np.concatenate([[0], np.cumsum(dists)])

    mask = np.concatenate([[True], dists > 1e-10])
    t, xs, ys = t[mask], xs[mask], ys[mask]
    if len(xs) < 2:
        return xs, ys

    t_new = np.linspace(t[0], t[-1], n)
    if len(xs) == 2:
        return np.interp(t_new, t, xs), np.interp(t_new, t, ys)

    k = min(3, len(xs) - 1)
    spl_x = make_interp_spline(t, xs, k=k)
    spl_y = make_interp_spline(t, ys, k=k)
    return spl_x(t_new), spl_y(t_new)


# ═══════════════════════════════════════════════════════════════════════════════
#  CLASSE PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════


class InteractiveChart:
    """
    Ferramenta interativa para desenhar frentes sobre mapa ou carta sinótica.

    Layout:
    ┌────────────────────────────────────────┬──────────────┐
    │                                        │  CONTROLES   │
    │                                        │  [teclas]    │
    │              MAPA                      ├──────────────┤
    │           (carta sinótica)             │  SIMBOLOGIAS │
    │                                        │  [legenda]   │
    ├────────────────────────────────────────┴──────────────┤
    │              LEGENDA SINÓTICA (PNMM, espessura, H/L)  │
    └───────────────────────────────────────────────────────┘
    """

    def __init__(
        self,
        config: Config | None = None,
        use_synoptic_background: bool = False,
        step: int = 0,
    ):
        """
        Inicializa a ferramenta interativa.

        Parâmetros
        ----------
        config : Config, opcional
            Configuração do CartoMet_BR
        use_synoptic_background : bool
            Se True, plota carta sinótica como fundo
        step : int
            Passo de previsão (só usado se use_synoptic_background=True)
        """
        self.config: Config = config or Config()
        self.use_synoptic_background: bool = use_synoptic_background
        self.step: int = step

        # Estado
        self.estado: dict[str, Any] = {
            "modo": "1",
            "pts_x": [],
            "pts_y": [],
            "linhas": [],
            "preview": None,
            "flip": False,
        }

        # Referência para texto de status (será atualizado dinamicamente)
        self.status_text: Text | None = None

        # Atributos definidos em _setup_figure
        self.fig: Figure
        self.ax: Axes
        self.ax_panel: Axes
        self.synoptic_data: SynopticData | None

        # Cria figura e axes
        self._setup_figure()

        # Conecta eventos
        self.fig.canvas.mpl_connect("button_press_event", self._ao_clicar)
        self.fig.canvas.mpl_connect("key_press_event", self._ao_pressionar_tecla)

        # Atualiza status inicial
        self._atualizar_status()

    def _setup_figure(self) -> None:
        """Configura figura com layout profissional."""

        if self.use_synoptic_background:
            # ═══════════════════════════════════════════════════════════════
            # MODO COM CARTA SINÓTICA
            # ═══════════════════════════════════════════════════════════════

            # Gera carta sinótica e obtém fig/ax
            self.fig, self.ax, self.synoptic_data = create_synoptic_chart(
                config=self.config,
                step=self.step,
                show=False,
                return_fig=True,
            )

            # Ajusta layout para adicionar painel lateral
            # A carta sinótica já tem a legenda na parte inferior
            # Vamos adicionar painel de controles à direita

            # Redimensiona o axes do mapa para dar espaço ao painel lateral
            pos = self.ax.get_position()
            self.ax.set_position([pos.x0, pos.y0, pos.width * 0.85, pos.height])

            # Cria painel lateral direito para controles e legenda de simbologias
            self.ax_panel = self.fig.add_axes([0.86, pos.y0, 0.13, pos.height])
            self.ax_panel.set_facecolor("#F8F8F8")
            self.ax_panel.set_xticks([])
            self.ax_panel.set_yticks([])
            for spine in self.ax_panel.spines.values():
                spine.set_color("#CCCCCC")
                spine.set_linewidth(0.5)

            self._criar_painel_lateral()

        else:
            # ═══════════════════════════════════════════════════════════════
            # MODO MAPA EM BRANCO
            # ═══════════════════════════════════════════════════════════════

            self.fig = plt.figure(figsize=(16, 10), facecolor="white")

            # GridSpec: mapa ocupa 85% da largura, painel lateral 15%
            gs = gridspec.GridSpec(
                1,
                2,
                width_ratios=[6, 1],
                wspace=0.02,
            )

            # Axes do mapa
            crs_mapa = ccrs.PlateCarree()
            self.ax = self.fig.add_subplot(gs[0], projection=crs_mapa)
            self.ax.set_extent(
                [
                    self.config.extent[0],
                    self.config.extent[2],
                    self.config.extent[1],
                    self.config.extent[3],
                ],
                crs=crs_mapa,
            )

            # Features do mapa
            self.ax.add_feature(cfeature.LAND, facecolor="#fafafa", zorder=1)
            self.ax.add_feature(cfeature.OCEAN, facecolor="#d4e6f1", zorder=1)
            self.ax.add_feature(cfeature.LAKES, facecolor="#d4e6f1", alpha=0.7, zorder=2)
            self.ax.add_feature(cfeature.COASTLINE, linewidth=0.7, edgecolor="#333333", zorder=3)
            self.ax.add_feature(
                cfeature.BORDERS, linewidth=0.5, edgecolor="#888888", linestyle=":", zorder=3
            )
            self.ax.add_feature(
                cfeature.NaturalEarthFeature(
                    "cultural",
                    "admin_1_states_provinces_lines",
                    "50m",
                    edgecolor="#aaaaaa",
                    facecolor="none",
                ),
                linewidth=0.3,
                zorder=3,
            )

            # Gridlines
            gl = self.ax.gridlines(
                crs=crs_mapa,
                draw_labels=True,
                linewidth=0.3,
                color="#999999",
                alpha=0.5,
                linestyle="--",
                xlocs=range(-100, 10, 10),
                ylocs=range(-80, 20, 10),
            )
            gl.top_labels = False
            gl.right_labels = False
            gl.xlabel_style = {"size": 8, "color": "#333333"}
            gl.ylabel_style = {"size": 8, "color": "#333333"}
            self.ax.set_facecolor("#d4e6f1")

            # Título do mapa
            self.ax.set_title(
                "CartoMet_BR — Ferramenta de Análise Sinótica",
                fontsize=12,
                fontweight="bold",
                color="#1A1A1A",
                pad=10,
            )

            # Painel lateral
            self.ax_panel = self.fig.add_subplot(gs[1])
            self.ax_panel.set_facecolor("#F8F8F8")
            self.ax_panel.set_xticks([])
            self.ax_panel.set_yticks([])
            for spine in self.ax_panel.spines.values():
                spine.set_color("#CCCCCC")
                spine.set_linewidth(0.5)

            self._criar_painel_lateral()

            self.synoptic_data = None

    def _criar_painel_lateral(self) -> None:
        """Cria painel lateral com controles e legenda de simbologias."""

        ax = self.ax_panel

        # ─── SEÇÃO: STATUS ATUAL ───────────────────────────────────────────
        y_pos = 0.97

        ax.text(
            0.5,
            y_pos,
            "STATUS",
            fontsize=9,
            fontweight="bold",
            ha="center",
            va="top",
            color="#333333",
            transform=ax.transAxes,
        )
        y_pos -= 0.04

        # Texto de status (será atualizado dinamicamente)
        self.status_text = ax.text(
            0.5,
            y_pos,
            "",
            fontsize=8,
            ha="center",
            va="top",
            color="#1a6faf",
            fontweight="bold",
            transform=ax.transAxes,
            bbox={
                "boxstyle": "round,pad=0.3",
                "facecolor": "white",
                "edgecolor": "#1a6faf",
                "linewidth": 0.5,
            },
        )
        y_pos -= 0.08

        # ─── SEÇÃO: CONTROLES ──────────────────────────────────────────────
        ax.plot(
            [0.1, 0.9],
            [y_pos + 0.02, y_pos + 0.02],
            color="#CCCCCC",
            linewidth=0.5,
            transform=ax.transAxes,
            clip_on=False,
        )
        y_pos -= 0.02

        ax.text(
            0.5,
            y_pos,
            "CONTROLES",
            fontsize=9,
            fontweight="bold",
            ha="center",
            va="top",
            color="#333333",
            transform=ax.transAxes,
        )
        y_pos -= 0.04

        controles = [
            ("Botão Esq.", "adicionar ponto"),
            ("Enter", "finalizar linha"),
            ("F", "inverter lado"),
            ("Z", "desfazer ponto"),
            ("C", "limpar tudo"),
            ("S", "salvar imagem"),
        ]

        for tecla, acao in controles:
            ax.text(
                0.08,
                y_pos,
                f"[{tecla}]",
                fontsize=7,
                fontweight="bold",
                ha="left",
                va="top",
                color="#555555",
                family="monospace",
                transform=ax.transAxes,
            )
            ax.text(
                0.92,
                y_pos,
                acao,
                fontsize=7,
                ha="right",
                va="top",
                color="#666666",
                transform=ax.transAxes,
            )
            y_pos -= 0.035

        # ─── SEÇÃO: SIMBOLOGIAS ────────────────────────────────────────────
        y_pos -= 0.02
        ax.plot(
            [0.1, 0.9],
            [y_pos + 0.02, y_pos + 0.02],
            color="#CCCCCC",
            linewidth=0.5,
            transform=ax.transAxes,
            clip_on=False,
        )
        y_pos -= 0.02

        ax.text(
            0.5,
            y_pos,
            "SIMBOLOGIAS",
            fontsize=9,
            fontweight="bold",
            ha="center",
            va="top",
            color="#333333",
            transform=ax.transAxes,
        )
        y_pos -= 0.04

        for tecla, modo in MODOS.items():
            # Tecla
            ax.text(
                0.08,
                y_pos,
                f"[{tecla}]",
                fontsize=7,
                fontweight="bold",
                ha="left",
                va="top",
                color=modo["cor"],
                family="monospace",
                transform=ax.transAxes,
            )
            # Nome
            nome_curto = modo["nome"].replace(" (RidgeAxis)", "").replace(" (Dryline)", "")
            ax.text(
                0.92,
                y_pos,
                nome_curto,
                fontsize=7,
                ha="right",
                va="top",
                color=modo["cor"],
                transform=ax.transAxes,
            )
            y_pos -= 0.035

        # ─── RODAPÉ ────────────────────────────────────────────────────────
        ax.text(
            0.5,
            0.02,
            "CartoMet_BR v1.0",
            fontsize=6,
            ha="center",
            va="bottom",
            color="#999999",
            style="italic",
            transform=ax.transAxes,
        )

    def _kwargs_plot(self, modo_key: str, flip: bool = False) -> dict[str, Any]:
        """Retorna kwargs para plotagem de linha."""
        m = MODOS[modo_key]
        return {
            "color": m["cor"],
            "linewidth": 1.5,
            "path_effects": m["efeito"](flip=flip),
            "transform": ccrs.PlateCarree(),
            "zorder": 15,
        }

    def _atualizar_status(self) -> None:
        """Atualiza texto de status no painel lateral."""
        m = MODOS[self.estado["modo"]]
        npts = len(self.estado["pts_x"])

        flip_tag = ""
        if m["tem_flip"] and self.estado["flip"]:
            flip_tag = " ⟲"

        status = f"{m['nome']}{flip_tag}\nPontos: {npts}"

        if self.status_text is not None:
            self.status_text.set_text(status)
            self.status_text.set_color(m["cor"])
            # Atualiza cor da borda do bbox
            self.status_text.set_bbox(
                {
                    "boxstyle": "round,pad=0.3",
                    "facecolor": "white",
                    "edgecolor": m["cor"],
                    "linewidth": 1.0,
                }
            )

        self.fig.canvas.draw_idle()

    def _redesenhar_preview(self) -> None:
        """Redesenha preview da linha atual."""
        if self.estado["preview"] is not None:
            with contextlib.suppress(ValueError):
                self.estado["preview"].remove()
            self.estado["preview"] = None

        if len(self.estado["pts_x"]) >= 2:
            xi, yi = interpolar_pontos(self.estado["pts_x"], self.estado["pts_y"])
            (linha,) = self.ax.plot(
                xi, yi, **self._kwargs_plot(self.estado["modo"], flip=self.estado["flip"])
            )
            self.estado["preview"] = linha

        self._atualizar_status()

    def _ao_clicar(self, event: MouseEvent) -> None:
        """Handler de clique do mouse."""
        if event.inaxes != self.ax or event.xdata is None:
            return
        if event.button == 1:  # botão esquerdo
            modo = MODOS[self.estado["modo"]]
            if modo.get("ponto", False):
                self._colocar_ponto(event.xdata, event.ydata)
            else:
                self.estado["pts_x"].append(event.xdata)
                self.estado["pts_y"].append(event.ydata)
                self._redesenhar_preview()

    def _colocar_ponto(self, x: float, y: float) -> None:
        """Coloca símbolo pontual (ex.: centros de pressão, furacão) com clique único."""
        import matplotlib.patheffects as pe

        modo = MODOS[self.estado["modo"]]
        cor = modo["cor"]

        if "draw_func" in modo:
            artist = modo["draw_func"](self.ax, x, y, color=cor)
        else:
            label = modo.get("label", "?")
            fontsize = modo.get("fontsize", 22)
            artist = self.ax.text(
                x,
                y,
                label,
                fontsize=fontsize,
                fontweight="bold",
                color=cor,
                ha="center",
                va="center",
                transform=self.ax.transData,
                zorder=25,
                path_effects=[pe.withStroke(linewidth=3, foreground="white")],
            )

        self.estado["linhas"].append(artist)
        self.fig.canvas.draw_idle()

    def _ao_pressionar_tecla(self, event: KeyEvent) -> None:
        """Handler de teclas."""
        tecla = event.key

        # Trocar modo
        if tecla in MODOS:
            self.estado["modo"] = tecla
            self.estado["flip"] = False
            if len(self.estado["pts_x"]) >= 2:
                self._redesenhar_preview()
            else:
                self._atualizar_status()
            return

        # Flip
        if tecla == "f":
            m = MODOS[self.estado["modo"]]
            if m["tem_flip"]:
                self.estado["flip"] = not self.estado["flip"]
                if len(self.estado["pts_x"]) >= 2:
                    self._redesenhar_preview()
                else:
                    self._atualizar_status()
            return

        # Finalizar linha
        if tecla == "enter":
            if len(self.estado["pts_x"]) >= 2:
                if self.estado["preview"] is not None:
                    with contextlib.suppress(ValueError):
                        self.estado["preview"].remove()
                    self.estado["preview"] = None

                xi, yi = interpolar_pontos(self.estado["pts_x"], self.estado["pts_y"])
                (linha,) = self.ax.plot(
                    xi, yi, **self._kwargs_plot(self.estado["modo"], flip=self.estado["flip"])
                )
                self.estado["linhas"].append(linha)

            self.estado["pts_x"].clear()
            self.estado["pts_y"].clear()
            self.estado["flip"] = False
            self._atualizar_status()
            return

        # Desfazer
        if tecla == "z":
            if self.estado["pts_x"]:
                self.estado["pts_x"].pop()
                self.estado["pts_y"].pop()
                self._redesenhar_preview()
            return

        # Limpar tudo
        if tecla == "c":
            for ln in self.estado["linhas"]:
                with contextlib.suppress(ValueError):
                    ln.remove()
            self.estado["linhas"].clear()
            if self.estado["preview"] is not None:
                with contextlib.suppress(ValueError):
                    self.estado["preview"].remove()
                self.estado["preview"] = None
            self.estado["pts_x"].clear()
            self.estado["pts_y"].clear()
            self.estado["flip"] = False
            self._atualizar_status()
            return

        # Salvar
        if tecla == "s":
            self._salvar()
            return

    def _salvar(self) -> None:
        """Salva a imagem atual."""
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"cartomet_interactive_{timestamp}.png"
        output_path = self.config.output_dir / filename
        self.fig.savefig(
            output_path,
            dpi=self.config.dpi,
            bbox_inches="tight",
            facecolor="white",
        )
        logger.info("Imagem salva em: %s", output_path)

    def show(self) -> None:
        """Exibe a ferramenta interativa."""
        plt.tight_layout()
        plt.show()


# ═══════════════════════════════════════════════════════════════════════════════
#  FUNÇÃO DE CONVENIÊNCIA
# ═══════════════════════════════════════════════════════════════════════════════


def run_interactive(
    config: Config | None = None,
    use_synoptic_background: bool = False,
    step: int = 0,
) -> None:
    """
    Inicia a ferramenta interativa.

    Parâmetros
    ----------
    config : Config, opcional
        Configuração do CartoMet_BR
    use_synoptic_background : bool
        Se True, usa carta sinótica como fundo
    step : int
        Passo de previsão (se usar fundo sinótico)
    """
    chart = InteractiveChart(
        config=config,
        use_synoptic_background=use_synoptic_background,
        step=step,
    )
    chart.show()
