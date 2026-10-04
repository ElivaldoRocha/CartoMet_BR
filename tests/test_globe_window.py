"""Vista de Globo — frame assíncrono, gestos e integração (offscreen).

O render nítido roda num ``GlobeFrameWorker`` (QThread) e a GUI só troca a
imagem pronta. Nos testes o worker vira SÍNCRONO (fixture ``sync_worker``)
e o CONTEÚDO do frame é inspecionado via ``compose_globe_frame`` direto.
"""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from cartomet_br.data.ecmwf import PLFieldData
from cartomet_br.services.field_style import derive_scalar_style
from cartomet_br.services.globe_compose import GlobeLayer, GlobeScene


@pytest.fixture(autouse=True)
def sync_worker(monkeypatch):
    """Worker de frame roda SÍNCRONO nos testes (sem threads de verdade)."""
    from cartomet_br.gui import globe_window as gw

    monkeypatch.setattr(gw.GlobeFrameWorker, "start", lambda self: self.run())
    yield


def _scene(with_texture=True, warnings=()):
    layers = []
    if with_texture:
        data = PLFieldData(
            values=np.full((3, 4), 20.0),
            lons=np.array([-60.0, -55.0, -50.0, -45.0]),
            lats=np.array([-10.0, -15.0, -20.0]),
            variable="t",
            level=850,
            unit="°C",
            valid_time="2026-10-03 12Z",
        )
        style = derive_scalar_style("t", "°C", data.values, {"cmap": "RdYlBu_r"})
        layers.append(GlobeLayer("t_850", data, {"nome": "Temperatura"}, style, True))
    tex = np.zeros((91, 180, 4), dtype=np.uint8) if with_texture else None
    if tex is not None:
        tex[..., 0] = 200
        tex[..., 3] = 180
    return GlobeScene(texture=tex, filled_layers=layers, warnings=list(warnings))


def _stars():
    rng = np.random.default_rng(1)
    return (rng.random(30), rng.random(30), rng.random(30) + 0.5)


def _frame_kwargs(scene, **kw):
    base = {
        "scene": scene,
        "satellite": None,
        "skin": "Campos ativos",
        "quality": "Rascunho",
        "center": (-55.0, -15.0),
        "zoom": 1.0,
        "stars": _stars(),
        "stamp_lines": ["CartoMet BR — Vista de Globo"],
        "drawings": [],
    }
    base.update(kw)
    return base


@pytest.fixture
def canvas_globo(qapp):
    from cartomet_br.gui.globe_window import GlobeCanvas

    c = GlobeCanvas(_scene(), center=(-55.0, -15.0))
    yield c
    c.shutdown()


def _evt(**kw):
    base = {"button": 1, "dblclick": False, "x": 100.0, "y": 100.0, "xdata": None, "ydata": None}
    base.update(kw)
    return SimpleNamespace(**base)


class TestComposeFrame:
    """Conteúdo do frame nítido (função pura executada pelo worker)."""

    def test_textura_costa_carimbo_e_avisos(self):
        from matplotlib.figure import Figure

        from cartomet_br.gui.globe_window import compose_globe_frame

        fig = Figure(figsize=(6, 4), dpi=100)
        scene = _scene(warnings=["t: sem cache global compatível — exibindo o recorte"])
        info = compose_globe_frame(
            fig,
            **_frame_kwargs(
                scene,
                stamp_lines=["CartoMet BR — Vista de Globo", "Temperatura 850 hPa"],
            ),
        )
        assert info["ax"].get_images(), "textura (imshow) ausente"
        textos = [t.get_text() for t in fig.texts]
        assert any("Vista de Globo" in t for t in textos)
        assert any("recorte" in t for t in textos)  # aviso honesto no frame

    def test_colorbar_diz_o_que_a_textura_mostra(self):
        # Achado da revisão: BoundaryNorm(levels, cmap.N) distribui as bandas
        # uniformemente pelo cmap — para níveis IRREGULARES (precipitação) a
        # legenda divergia da textura. A colorbar usa as cores REAIS das
        # bandas (band_colors), achatadas sobre branco como a textura.
        from matplotlib.figure import Figure

        from cartomet_br.gui.globe_window import compose_globe_frame
        from cartomet_br.services.globe_compose import band_colors

        data = PLFieldData(
            values=np.full((3, 4), 12.0),
            lons=np.array([-60.0, -55.0, -50.0, -45.0]),
            lats=np.array([-10.0, -15.0, -20.0]),
            variable="precip",
            unit="mm/3h",
        )
        style = derive_scalar_style("precip", "mm/3h", data.values, {"cmap": "precip_classic"})
        layer = GlobeLayer("precip", data, {"nome": "Precipitação"}, style, True)
        tex = np.zeros((10, 20, 4), dtype=np.uint8)
        scene = GlobeScene(texture=tex, filled_layers=[layer])

        fig = Figure(figsize=(6, 4), dpi=100)
        info = compose_globe_frame(fig, **_frame_kwargs(scene))
        assert info["colorbars"], "colorbar não construída"
        interior, _under, _over = band_colors(style)
        cores_cbar = np.asarray(info["colorbars"][0].cmap.colors)
        esperadas = np.asarray([(*(np.asarray(k[:3]) * 0.85 + 0.15), 1.0) for k in interior])
        assert cores_cbar.shape == esperadas.shape
        assert np.allclose(cores_cbar, esperadas, atol=1e-6)

    def test_sinotico_vetorial_e_tracado_com_descarte_do_lado_oculto(self):
        from matplotlib.figure import Figure

        from cartomet_br.data.ecmwf import SynopticData
        from cartomet_br.gui.globe_window import compose_globe_frame

        lats = np.linspace(20, -40, 7)
        lons = np.linspace(-80, -20, 7)
        lon2d, lat2d = np.meshgrid(lons, lats)
        syn = SynopticData(
            pnmm=1004.0 + lat2d * 0.5,
            thickness=5400.0 + lat2d * 2.0,
            lons=lons,
            lats=lats,
            lon2d=lon2d,
            lat2d=lat2d,
            valid_time="2026-10-04 00Z",
            extent=[-80.0, -40.0, -20.0, 20.0],
            base_time="00Z 04/10/2026",
            step=0,
        )
        scene = GlobeScene(texture=None, synoptic=syn, synoptic_kinds=("pnmm", "thickness"))
        recs = [
            {
                "type": "symbol_line",
                "symbol_key": "1",
                "points_x": [-60.0, -50.0],
                "points_y": [-20.0, -25.0],
                "flip": False,
                "intensity": 1,
            },
            {"type": "annotation", "x": -45.0, "y": -10.0, "text": "frente fria", "color": "#fff"},
            # Anotação na ANTÍPODA (lado oculto) — descartada, não vira lixo visual.
            {"type": "annotation", "x": 125.0, "y": 15.0, "text": "oculta", "color": "#fff"},
            {"type": "emoji", "x": -40.0, "y": -5.0, "emoji": "CB", "fontsize": 28},
        ]
        fig = Figure(figsize=(6, 4), dpi=100)
        info = compose_globe_frame(fig, **_frame_kwargs(scene, drawings=recs))
        ax = info["ax"]
        assert ax.collections, "isolinhas sinoticas (vetoriais) ausentes"
        assert ax.lines, "frente do tracado ausente"
        textos_ax = [t.get_text() for t in ax.texts]
        assert any("frente fria" in t for t in textos_ax)
        assert not any("oculta" in t for t in textos_ax)  # lado oculto descartado

    def test_simbolo_pontual_dimensionado_em_graus_no_globo(self):
        # Bug de campo: _symbol_size usava xlim (METROS na ortográfica) e o
        # símbolo de baixa pressão virava uma mancha do tamanho do globo.
        from cartomet_br.symbols.point_symbols import _symbol_size

        ax_orto = SimpleNamespace(get_xlim=lambda: (-6.4e6, 6.4e6))
        ax_carta = SimpleNamespace(get_xlim=lambda: (-75.0, -35.0))
        assert _symbol_size(ax_carta) == pytest.approx(40.0 * 0.035)
        tam_globo = _symbol_size(ax_orto)
        assert 2.0 < tam_globo < 6.0  # ~115° de disco → ~4° de símbolo


class TestFrameAssincrono:
    def test_render_full_troca_a_tela_pelo_frame(self, canvas_globo):
        c = canvas_globo
        estados = []
        c.render_state.connect(estados.append)
        c.render_full()  # worker síncrono na fixture
        assert c._last_frame is not None
        assert c._last_frame.ndim == 3 and c._last_frame.shape[2] == 4
        assert c._image_ax is not None and c._image_ax.get_images()
        assert "Renderizando" in estados[0]
        assert estados[-1] == ""  # pronto → status limpo

    def test_frame_obsoleto_e_descartado(self, qapp):
        from cartomet_br.gui.globe_window import GlobeCanvas

        c = GlobeCanvas(_scene(), center=(-55.0, -15.0))
        c._frame_gen = 7
        c._on_frame_ready(3, np.zeros((4, 4, 4), dtype=np.uint8))  # geração velha
        assert c._last_frame is None
        c.shutdown()

    def test_sem_campos_pele_relevo(self, qapp):
        from cartomet_br.gui.globe_window import GlobeCanvas

        c = GlobeCanvas(_scene(with_texture=False), center=(0.0, 0.0))
        assert c.available_skins() == ["Relevo natural"]
        c.render_full()  # stock_img no worker — não pode levantar
        assert c._last_frame is not None
        c.shutdown()

    def test_render_draft_e_barato_sem_textura(self, canvas_globo):
        canvas_globo.render_draft()
        assert canvas_globo._ax is not None
        assert not canvas_globo._ax.get_images()  # rascunho nunca reprojeta textura


class TestGestos:
    def test_arraste_gira_na_direcao_da_mao(self, canvas_globo):
        c = canvas_globo
        c.render_draft()
        lon0, lat0 = c.center
        c._on_press(_evt(x=100.0, y=100.0))
        assert c._dragging
        c._on_motion(_evt(x=180.0, y=100.0))  # arrasta p/ leste
        lon1, _ = c.center
        assert lon1 < lon0  # superfície segue a mão → centro vai p/ oeste
        c._on_release(_evt())
        assert not c._dragging
        assert c._settle_timer.isActive()  # frame nítido agendado

    def test_clamp_de_latitude_e_wrap_de_longitude(self, canvas_globo):
        c = canvas_globo
        c._set_center(179.0, 0.0)
        c._set_center(c.center[0] + 10.0, 95.0)
        lon, lat = c.center
        assert -180.0 <= lon <= 180.0  # wrap
        assert lat == 89.0  # clamp

    def test_scroll_zoom_com_limites(self, canvas_globo):
        c = canvas_globo
        c.render_draft()
        for _ in range(20):
            c._on_scroll(_evt(button="up"))
        assert c._zoom == pytest.approx(8.0)
        for _ in range(30):
            c._on_scroll(_evt(button="down"))
        assert c._zoom == pytest.approx(1.0)

    def test_scroll_recorta_o_frame_pronto(self, canvas_globo):
        c = canvas_globo
        c.render_full()
        assert c._image_ax is not None
        xlim0 = c._image_ax.get_xlim()
        c._on_scroll(_evt(button="up"))
        xlim1 = c._image_ax.get_xlim()
        assert (xlim1[1] - xlim1[0]) < (xlim0[1] - xlim0[0])  # recorte = zoom

    def test_duplo_clique_fora_do_disco_e_noop(self, canvas_globo):
        c = canvas_globo
        c.render_full()
        antes = c.center
        c._on_press(_evt(dblclick=True, x=1.0, y=1.0))  # canto: fora do disco
        assert c.center == antes

    def test_px_to_lonlat_no_centro_do_canvas(self, qapp):
        from cartomet_br.gui.globe_window import _AX_RECT, GlobeCanvas

        c = GlobeCanvas(_scene(), center=(0.0, 0.0))
        c.render_draft()
        w = float(c.fig.bbox.width)
        h = float(c.fig.bbox.height)
        cx = (_AX_RECT[0] + _AX_RECT[2] / 2.0) * w
        cy = (_AX_RECT[1] + _AX_RECT[3] / 2.0) * h
        lonlat = c._px_to_lonlat(cx, cy)
        assert lonlat is not None
        assert lonlat[0] == pytest.approx(0.0, abs=1.0)
        assert lonlat[1] == pytest.approx(0.0, abs=1.0)
        c.shutdown()

    def test_apresentacao_gira_e_gesto_pausa(self, canvas_globo):
        c = canvas_globo
        c.set_spinning(True)
        assert c.spinning
        lon0 = c.center[0]
        c._spin_tick()
        assert c.center[0] != lon0
        c._on_press(_evt())  # qualquer press pausa o giro
        assert not c.spinning


class TestCarimboPorPele:
    def test_carimbo_acompanha_a_pele(self):
        from cartomet_br.gui.globe_window import _stamp_for_skin

        sat = SimpleNamespace(time_str="03/10/2026 23:50 UTC")
        linhas = ["CartoMet BR — Vista de Globo", "Temperatura 850 hPa — ECMWF IFS"]
        scene = _scene()
        goes = _stamp_for_skin(scene, "Satélite GOES", sat, linhas)
        assert any("GOES-East" in t and "23:50" in t for t in goes)
        assert not any("Temperatura" in t for t in goes)  # step do modelo não assina a imagem
        relevo = _stamp_for_skin(scene, "Relevo natural", None, linhas)
        assert any("relevo natural" in t for t in relevo)
        campos = _stamp_for_skin(scene, "Campos ativos", None, linhas)
        assert campos == linhas


class TestJanela:
    def test_janela_monta_com_controles_e_esc_fecha(self, qapp):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        from cartomet_br.gui.globe_window import GlobeWindow

        win = GlobeWindow(_scene(), center=(-55.0, -15.0))
        assert win.skin_combo.count() >= 1
        win.show()
        QTest.keyClick(win, Qt.Key.Key_Escape)
        qapp.processEvents()
        assert not win.isVisible()

    def test_setas_giram_e_home_reseta(self, qapp):
        from PyQt6.QtCore import QEvent, Qt
        from PyQt6.QtGui import QKeyEvent

        from cartomet_br.gui.globe_window import GlobeWindow

        win = GlobeWindow(_scene(), center=(-55.0, -15.0))
        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier)
        win.keyPressEvent(ev)
        assert win.globe.center[0] == pytest.approx(-65.0)
        win.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Home, Qt.KeyboardModifier.NoModifier)
        )
        assert win.globe.center == (-55.0, -15.0)
        win.close()

    def test_salvar_png_usa_o_frame_pronto(self, qapp, tmp_path):
        from cartomet_br.gui.globe_window import GlobeWindow

        win = GlobeWindow(_scene(), center=(-55.0, -15.0), output_dir=tmp_path)
        win.globe.render_full()
        win._save_png()
        pngs = list(tmp_path.glob("globo_*.png"))
        assert len(pngs) == 1 and pngs[0].stat().st_size > 0
        assert "salvo" in win.status.text()
        win.close()


class TestIntegracaoMainWindow:
    @pytest.fixture
    def window(self, qapp, tmp_path):
        from cartomet_br.gui.main_window import MainWindow

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        try:
            return MainWindow(data_dir=data_dir)
        except Exception as exc:  # noqa: BLE001 — ambiente sem render
            pytest.skip(f"MainWindow não pôde ser criada offscreen: {exc}")

    def test_visible_pl_layers_filtra_ocultas(self, window):
        data = PLFieldData(
            values=np.zeros((3, 4)),
            lons=np.array([-60.0, -55.0, -50.0, -45.0]),
            lats=np.array([-10.0, -15.0, -20.0]),
            variable="t",
            level=850,
            unit="°C",
        )
        window.canvas._pl_data["t_850"] = data
        window.canvas._pl_artists["t_850"] = [object()]
        window.canvas._pl_data["gh_500"] = data
        window.canvas._pl_artists["gh_500"] = []  # oculta pelo toggle
        vis = window.canvas.visible_pl_layers()
        assert list(vis) == ["t_850"]

    def test_abrir_globo_sem_campos_usa_relevo(self, window, monkeypatch):
        aberto = {}
        from cartomet_br.gui import globe_window as gw

        monkeypatch.setattr(
            gw.GlobeWindow,
            "show_fullscreen_on_screen",
            lambda self, screen=None: aberto.setdefault("ok", True),
        )
        window._open_globe_view()
        assert aberto.get("ok")
        assert window._globe_window is not None
        assert window._globe_window.globe.available_skins() == ["Relevo natural"]
        window._globe_window.close()

    def test_ciclo_abrir_fechar_nao_vaza_a_janela(self, window, monkeypatch):
        # Achado da revisão: com parent Qt, a posse C++ era do MainWindow e
        # cada ciclo abrir/fechar acumulava uma GlobeWindow viva.
        import gc
        import weakref

        from cartomet_br.gui import globe_window as gw

        monkeypatch.setattr(
            gw.GlobeWindow, "show_fullscreen_on_screen", lambda self, screen=None: None
        )
        window._open_globe_view()
        win = window._globe_window
        assert win is not None
        assert win.parent() is None  # posse 100% do Python
        assert all(not isinstance(ch, gw.GlobeWindow) for ch in window.children())
        ref = weakref.ref(win)
        win.close()
        assert window._globe_window is None  # sinal closed soltou a referência
        del win
        gc.collect()
        assert ref() is None, "GlobeWindow fechada continua viva (vazamento)"

    def test_globo_bloqueado_durante_animacao(self, window, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        avisos = []
        monkeypatch.setattr(
            QMessageBox, "information", staticmethod(lambda *a, **k: avisos.append(a))
        )
        window._animation_controller = object()
        window._open_globe_view()
        assert avisos and window._globe_window is None


class TestSnapshotCompleto:
    @pytest.fixture
    def window(self, qapp, tmp_path):
        from cartomet_br.gui.main_window import MainWindow

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        try:
            return MainWindow(data_dir=data_dir)
        except Exception as exc:  # noqa: BLE001 — ambiente sem render
            pytest.skip(f"MainWindow nao pode ser criada offscreen: {exc}")

    def test_sinotico_e_tracado_entram_no_snapshot(self, window, monkeypatch):
        from cartomet_br.gui import globe_window as gw

        monkeypatch.setattr(
            gw.GlobeWindow, "show_fullscreen_on_screen", lambda self, screen=None: None
        )
        window.canvas.add_annotation(-40.0, -10.0, "cavado")
        window.canvas.add_emoji(-45.0, -15.0, "CB", 28)
        # SynopticData real (o reload cai em CacheMissError -> recorte honesto)
        from cartomet_br.data.ecmwf import SynopticData

        lats = np.linspace(5, -35, 5)
        lons = np.linspace(-75, -35, 5)
        lon2d, lat2d = np.meshgrid(lons, lats)
        window.canvas.synoptic_data = SynopticData(
            pnmm=np.full((5, 5), 1013.0),
            thickness=np.full((5, 5), 5500.0),
            lons=lons,
            lats=lats,
            lon2d=lon2d,
            lat2d=lat2d,
            valid_time="2026-10-04 00Z",
            extent=[-75.0, -35.0, -35.0, 5.0],
            base_time="00Z 04/10/2026",
            step=0,
        )
        window._open_globe_view()
        win = window._globe_window
        assert win is not None
        assert win.globe._scene.synoptic is not None
        assert win.globe._scene.synoptic_kinds == ("pnmm", "thickness")
        assert any("Centros H/L" in w for w in win.globe._scene.warnings)
        assert any(r["type"] == "annotation" for r in win.globe._drawings)
        assert any("emoji" in w for w in win.globe._scene.warnings)
        win.close()
