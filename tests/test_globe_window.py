"""Vista de Globo — janela, gestos e integração com o MainWindow (offscreen)."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from cartomet_br.data.ecmwf import PLFieldData
from cartomet_br.services.field_style import derive_scalar_style
from cartomet_br.services.globe_compose import GlobeLayer, GlobeScene


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


class TestRender:
    def test_render_full_poe_textura_costa_e_carimbo(self, qapp):
        from cartomet_br.gui.globe_window import GlobeCanvas

        c = GlobeCanvas(
            _scene(warnings=["t: sem cache global — exibindo o recorte regional"]),
            center=(-55.0, -15.0),
            stamp_lines=["CartoMet BR — Vista de Globo", "Temperatura 850 hPa"],
        )
        c.render_full()
        assert c._ax is not None
        assert c._ax.get_images(), "textura (imshow) ausente"
        textos = [t.get_text() for t in c.fig.texts]
        assert any("Vista de Globo" in t for t in textos)
        assert any("recorte regional" in t for t in textos)  # aviso honesto no PNG
        c.shutdown()

    def test_sem_campos_pele_relevo(self, qapp):
        from cartomet_br.gui.globe_window import GlobeCanvas

        c = GlobeCanvas(_scene(with_texture=False), center=(0.0, 0.0))
        assert c.available_skins() == ["Relevo natural"]
        c.render_full()  # stock_img — não pode levantar
        assert c._ax is not None
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
        assert c._settle_timer.isActive()  # render completo agendado

    def test_clamp_de_latitude_e_wrap_de_longitude(self, canvas_globo):
        c = canvas_globo
        c._set_center(179.0, 0.0)
        c._set_center(c.center[0] + 10.0, 95.0)
        lon, lat = c.center
        assert -180.0 <= lon <= 180.0  # wrap
        assert lat == 89.0  # clamp

    def test_scroll_zoom_com_limites(self, canvas_globo):
        c = canvas_globo
        c.render_full()
        for _ in range(20):
            c._on_scroll(_evt(button="up"))
        assert c._zoom == pytest.approx(8.0)
        for _ in range(30):
            c._on_scroll(_evt(button="down"))
        assert c._zoom == pytest.approx(1.0)

    def test_duplo_clique_fora_do_disco_e_noop(self, canvas_globo):
        c = canvas_globo
        c.render_full()
        antes = c.center
        c._on_press(_evt(dblclick=True, xdata=None, inaxes=c._ax))
        assert c.center == antes

    def test_apresentacao_gira_e_gesto_pausa(self, canvas_globo):
        c = canvas_globo
        c.set_spinning(True)
        assert c.spinning
        lon0 = c.center[0]
        c._spin_tick()
        assert c.center[0] != lon0
        c._on_press(_evt())  # qualquer press pausa o giro
        assert not c.spinning


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
        win.globe.render_full()
        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier)
        win.keyPressEvent(ev)
        assert win.globe.center[0] == pytest.approx(-65.0)
        win.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Home, Qt.KeyboardModifier.NoModifier)
        )
        assert win.globe.center == (-55.0, -15.0)
        win.close()

    def test_colorbar_diz_o_que_a_textura_mostra(self, qapp):
        # Achado da revisão: BoundaryNorm(levels, cmap.N) distribui as bandas
        # uniformemente pelo cmap — para níveis IRREGULARES (precipitação) a
        # legenda divergia da textura em várias categorias. A colorbar agora
        # usa as cores REAIS das bandas (band_colors), achatadas sobre branco.
        import numpy as np

        from cartomet_br.gui.globe_window import GlobeCanvas
        from cartomet_br.services.field_style import derive_scalar_style
        from cartomet_br.services.globe_compose import GlobeLayer, GlobeScene, band_colors

        data = PLFieldData(
            values=np.full((3, 4), 12.0),
            lons=np.array([-60.0, -55.0, -50.0, -45.0]),
            lats=np.array([-10.0, -15.0, -20.0]),
            variable="precip",
            unit="mm/3h",
        )
        style = derive_scalar_style(
            "precip", "mm/3h", data.values, {"cmap": "precip_classic"}
        )  # níveis irregulares de verdade
        layer = GlobeLayer("precip", data, {"nome": "Precipitação"}, style, True)
        tex = np.zeros((10, 20, 4), dtype=np.uint8)
        scene = GlobeScene(texture=tex, filled_layers=[layer])
        c = GlobeCanvas(scene, center=(-55.0, -15.0))
        c.render_full()
        assert c._colorbars, "colorbar não construída"
        interior, _under, _over = band_colors(style)
        cores_cbar = np.asarray(c._colorbars[0].cmap.colors)
        esperadas = np.asarray([(*(np.asarray(k[:3]) * 0.85 + 0.15), 1.0) for k in interior])
        assert cores_cbar.shape == esperadas.shape
        assert np.allclose(cores_cbar, esperadas, atol=1e-6)
        c.shutdown()

    def test_carimbo_acompanha_a_pele(self, qapp):
        from cartomet_br.gui.globe_window import GlobeCanvas

        sat = SimpleNamespace(
            data=np.zeros((4, 4)),
            x=np.array([-5e6, 0.0, 5e6, 5.5e6]),
            y=np.array([5e6, 0.0, -5e6, -5.5e6]),
            sat_lon=-75.0,
            sat_h=35786023.0,
            sat_sweep="x",
            time_str="03/10/2026 23:50 UTC",
        )
        c = GlobeCanvas(
            _scene(),
            center=(-55.0, -15.0),
            satellite=sat,
            stamp_lines=["CartoMet BR — Vista de Globo", "Temperatura 850 hPa — ECMWF IFS"],
        )
        c.set_skin("Satélite GOES")
        textos = " | ".join(t.get_text() for t in c.fig.texts)
        assert "GOES-East" in textos and "23:50" in textos
        assert "Temperatura" not in textos  # o step do modelo não assina a imagem
        c.set_skin("Relevo natural")
        textos = " | ".join(t.get_text() for t in c.fig.texts)
        assert "relevo natural" in textos and "Temperatura" not in textos
        c.set_skin("Campos ativos")
        textos = " | ".join(t.get_text() for t in c.fig.texts)
        assert "Temperatura 850 hPa" in textos
        c.shutdown()

    def test_salvar_png(self, qapp, tmp_path):
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
        # Sem rede e sem showFullScreen de verdade no offscreen.
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
        # cada ciclo abrir/fechar acumulava uma GlobeWindow viva (textura +
        # campos globais + canvas fullscreen). Sem parent, fechar + soltar a
        # referência destrói de verdade.
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
