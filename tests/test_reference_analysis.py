"""Análise de referência .cmbr (Onda 6 da v3.2) — offscreen.

O aluno abre um segundo ``.cmbr`` (a análise do professor) como overlay
cinza/translúcido: fora do histórico (Desfazer nunca o toca), fora do modo
edição, fora do Salvar Projeto, com grupo de visibilidade próprio. Morre no
rebuild do mapa base, sobrevive ao Limpar desenhos.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from cartomet_br.gui import project_io

REF_RECORDS = [
    {
        "type": "symbol_line",
        "symbol_key": "1",
        "points_x": [-52.0, -48.0, -44.0],
        "points_y": [-26.0, -24.0, -23.0],
        "flip": False,
        "intensity": 1,
    },
    {"type": "symbol_point", "symbol_key": "a", "x": -45.0, "y": -18.0},
    {"type": "annotation", "x": -40.0, "y": -12.0, "text": "cavado", "color": "#000000"},
    {
        "type": "pen",
        "points_x": [-55.0, -54.0, -53.0],
        "points_y": [-10.0, -11.0, -10.5],
        "style": {},
    },
    {"type": "emoji", "x": -38.0, "y": -8.0, "emoji": "CB", "fontsize": 28},
]


def _user_state(canvas) -> tuple:
    return (
        len(canvas.lines),
        len(canvas._annotations),
        len(canvas._emoji_annotations),
        len(canvas._emoji_records),
        len(canvas.history.commands),
    )


class TestImportReferencia:
    def test_overlay_fora_do_documento_do_aluno(self, canvas):
        antes = _user_state(canvas)
        canvas.import_drawings_state(REF_RECORDS, reference=True)
        # Artistas no grupo próprio; NADA vazou para as listas do usuário.
        assert len(canvas._reference_artists) == len(REF_RECORDS)
        assert _user_state(canvas) == antes
        assert not canvas.history.can_undo  # Desfazer do aluno não toca o professor
        assert canvas.has_reference()

    def test_salvar_projeto_nao_absorve_a_referencia(self, canvas):
        canvas.import_drawings_state(REF_RECORDS, reference=True)
        assert canvas.export_drawings_state() == []

    def test_estilo_cinza_translucido_por_baixo(self, canvas):
        from matplotlib.colors import to_rgba

        ref_rgba = to_rgba(canvas._REFERENCE_COLOR, canvas._REFERENCE_ALPHA)
        canvas.import_drawings_state(REF_RECORDS, reference=True)
        line = canvas._reference_artists[0]  # Line2D da frente
        assert line.get_alpha() == pytest.approx(canvas._REFERENCE_ALPHA)
        assert line.get_color() == canvas._REFERENCE_COLOR
        # Glifos do efeito recoloridos com RGBA COM alpha: os efeitos
        # redesenham com set_foreground(self.color)/to_rgba(color), que
        # descartariam o alpha do Line2D — só a tupla RGBA o leva ao render.
        for ef in line.get_path_effects():
            if hasattr(ef, "color"):
                assert ef.color == ref_rgba
                assert ef.color[3] == pytest.approx(canvas._REFERENCE_ALPHA)
        # Por baixo do traçado do aluno (linhas do usuário têm zorder 20).
        assert line.get_zorder() < 20

    def test_frente_estacionaria_tambem_vira_cinza(self, canvas):
        # FrenteEstacionaria nunca lê self.color — desenha com as constantes
        # de classe _COR_FRIA/_COR_QUENTE. O override sobrescreve as DUAS na
        # instância (cada artista tem efeitos próprios — não vaza p/ o aluno).
        from matplotlib.colors import to_rgba

        from cartomet_br.symbols.fronts import FrenteEstacionaria

        ref_rgba = to_rgba(canvas._REFERENCE_COLOR, canvas._REFERENCE_ALPHA)
        rec = dict(REF_RECORDS[0], symbol_key="3")
        canvas.import_drawings_state([rec], reference=True)
        line = canvas._reference_artists[0]
        efs = [ef for ef in line.get_path_effects() if isinstance(ef, FrenteEstacionaria)]
        assert efs, "symbol_key '3' deveria usar FrenteEstacionaria"
        for ef in efs:
            assert ref_rgba == ef._COR_FRIA
            assert ref_rgba == ef._COR_QUENTE
        # As constantes de CLASSE ficam intactas (o aluno desenha colorido).
        assert FrenteEstacionaria._COR_FRIA == "#1a6faf"
        assert FrenteEstacionaria._COR_QUENTE == "#c0392b"

    def test_emoji_translucido_na_imagem_e_zorder_na_banda(self, canvas):
        # AnnotationBbox ignora set_alpha no wrapper — o alpha vai nos filhos
        # do offsetbox (BboxImage honra no make_image). E TODO artista da
        # referência cai na banda (15, 19.5): acima dos campos (<=14), abaixo
        # de tudo do aluno (linhas 20, fill 21, textos 25, emojis 26).
        canvas.import_drawings_state(REF_RECORDS, reference=True)
        for artist in canvas._reference_artists:
            ob = getattr(artist, "offsetbox", None)
            if ob is not None:  # emoji via imagem
                for child in ob.get_children():
                    assert child.get_alpha() == pytest.approx(canvas._REFERENCE_ALPHA)
            z = getattr(artist, "get_zorder", lambda: None)()
            if z is not None:
                assert 14.0 < z < 20.0, f"zorder {z} fora da banda de referência"

    def test_referencia_nova_substitui_a_anterior(self, canvas):
        canvas.import_drawings_state(REF_RECORDS, reference=True)
        primeiro = list(canvas._reference_artists)
        canvas.import_drawings_state(REF_RECORDS[:2], reference=True)
        assert len(canvas._reference_artists) == 2
        assert all(a not in canvas._reference_artists for a in primeiro)

    def test_import_normal_segue_intacto(self, canvas):
        # O seam continua servindo o abrir-projeto comum (histórico + listas).
        canvas.import_drawings_state(REF_RECORDS)
        assert canvas.history.can_undo
        assert len(canvas._reference_artists) == 0
        assert len(canvas._emoji_records) == 1


class TestVisibilidadeEVida:
    def test_grupo_de_visibilidade_proprio(self, canvas):
        canvas.import_drawings_state(REF_RECORDS, reference=True)
        canvas.set_drawings_visible("reference", False)
        assert all(not a.get_visible() for a in canvas._drawing_kind_artists("reference"))
        # Os grupos do aluno não são afetados nem o afetam.
        canvas.set_drawings_visible("symbology", False)
        canvas.set_drawings_visible("reference", True)
        assert all(a.get_visible() for a in canvas._drawing_kind_artists("reference"))

    def test_importa_com_symbology_oculto_nao_contamina(self, canvas):
        # O _rebuild_artist aplicaria o toggle de "symbology" — a referência
        # obedece ao próprio grupo, não ao do aluno.
        canvas.set_drawings_visible("symbology", False)
        canvas.import_drawings_state(REF_RECORDS[:1], reference=True)
        assert canvas._reference_artists[0].get_visible()

    def test_fechar_referencia(self, canvas):
        canvas.import_drawings_state(REF_RECORDS, reference=True)
        canvas.remove_reference()
        assert not canvas.has_reference()
        assert canvas._reference_artists == []

    def test_sobrevive_ao_limpar_desenhos(self, canvas):
        canvas.import_drawings_state(REF_RECORDS, reference=True)
        canvas.set_drawings_visible("reference", False)
        canvas.add_annotation(-40.0, -10.0, "meu traçado")
        canvas.clear_all()
        # O traçado do aluno foi; o do professor ficou, com o toggle preservado.
        assert canvas.has_reference()
        assert not canvas._annotations
        assert canvas._drawings_visible["reference"] is False

    def test_morre_no_rebuild_do_mapa_base(self, canvas):
        canvas.import_drawings_state(REF_RECORDS, reference=True)
        canvas._setup_base_map()
        assert not canvas.has_reference()
        assert canvas._drawings_visible["reference"] is True  # re-armado

    def test_clear_map_fecha_a_referencia(self, canvas):
        canvas.import_drawings_state(REF_RECORDS, reference=True)
        canvas.clear_map()
        assert not canvas.has_reference()

    def test_modo_edicao_nao_seleciona_a_referencia(self, canvas):
        canvas.import_drawings_state(REF_RECORDS, reference=True)
        # Candidatos à edição vêm do documento do aluno — vazio.
        assert canvas._edit_candidates() == []

    def test_prancha_exportada_carimba_a_referencia(self, canvas):
        # O PNG absorve o overlay visível — a prancha DIZ que há uma segunda
        # análise (e de quem), em vez de atribuí-la ao analista do cabeçalho.
        canvas.import_drawings_state(REF_RECORDS[:1], reference=True)
        canvas.set_reference_note("análise de Prof. Everaldo, 2 revisões")
        added = canvas.render_chart_furniture({"institution": "UFPA"})
        texts = [a.get_text() for a in added if hasattr(a, "get_text")]
        assert any("Sobreposição" in t and "Prof. Everaldo" in t for t in texts)
        canvas.clear_chart_furniture()
        # Com o grupo OCULTO o overlay não sai no PNG — carimbo seria mentira.
        canvas.set_drawings_visible("reference", False)
        added2 = canvas.render_chart_furniture({"institution": "UFPA"})
        texts2 = [a.get_text() for a in added2 if hasattr(a, "get_text")]
        assert not any("Sobreposição" in t for t in texts2)


def test_bbox_cobre_caneta_formas_emojis():
    # commands_bbox ignorava Pen/Shape/Emoji (escopo CODSAS) — um gabarito só
    # de anotações à mão livre voltava None e silenciava o aviso de vista.
    from cartomet_br.gui.bulletin_io import commands_bbox
    from cartomet_br.gui.draw_tools import EmojiCommand, PenCommand, ShapeCommand

    cmds = [
        PenCommand(points_x=[-55.0, -53.0], points_y=[-10.0, -12.0], style={}),
        ShapeCommand(
            tool="rect",
            points_x=[-40.0],
            points_y=[-5.0],
            style={},
            head_size_deg=0.0,
            rotation_deg=0.0,
        ),
        EmojiCommand(x=-38.0, y=-8.0, emoji="CB", fontsize=28),
    ]
    assert commands_bbox(cmds) == (-55.0, -12.0, -38.0, -5.0)


class TestFluxoNaJanela:
    @pytest.fixture
    def window(self, qapp, tmp_path):
        from cartomet_br.gui.main_window import MainWindow

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        try:
            return MainWindow(data_dir=data_dir)
        except Exception as exc:  # noqa: BLE001 — ambiente sem render
            pytest.skip(f"MainWindow não pôde ser criada offscreen: {exc}")

    def _cmbr(self, tmp_path, *, drawings=None, author="Prof. Everaldo", revisions=2):
        revs = [project_io.make_revision(author) for _ in range(revisions)]
        project = project_io.build_project(
            extent=[-80.0, -40.0, -30.0, 10.0],
            theme="classico",
            data_context={},
            layers=[],
            drawings=REF_RECORDS if drawings is None else drawings,
            app_version="test",
            author=author,
            revisions=revs,
        )
        path = tmp_path / "gabarito.cmbr"
        path.write_text(project_io.dump_project(project), encoding="utf-8")
        return path

    def _patch_dialog(self, monkeypatch, path):
        from PyQt6.QtWidgets import QFileDialog

        monkeypatch.setattr(
            QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), ""))
        )

    def test_abrir_mostra_autoria_e_arma_o_toggle(self, window, tmp_path, monkeypatch):
        self._patch_dialog(monkeypatch, self._cmbr(tmp_path))
        window._open_reference()
        assert window.canvas.has_reference()
        status = window.status_label.text()
        assert "Prof. Everaldo" in status
        assert "2 revisões" in status
        assert f"{len(REF_RECORDS)} feições" in status

    def test_abrir_nao_mexe_no_mapa_do_aluno(self, window, tmp_path, monkeypatch):
        extent_antes = list(window.config.extent)
        tema_antes = window.canvas.current_theme
        self._patch_dialog(monkeypatch, self._cmbr(tmp_path))
        window._open_reference()
        # O .cmbr pede outro extent/tema — a referência NÃO os aplica.
        assert window.config.extent == extent_antes
        assert window.canvas.current_theme == tema_antes

    def test_projeto_sem_desenhos_avisa_e_nao_abre(self, window, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        avisos = []
        monkeypatch.setattr(
            QMessageBox,
            "information",
            staticmethod(lambda *a, **k: avisos.append(a)),
        )
        self._patch_dialog(monkeypatch, self._cmbr(tmp_path, drawings=[]))
        window._open_reference()
        assert not window.canvas.has_reference()
        assert avisos  # avisou em vez de abrir um overlay vazio

    def test_arquivo_invalido_nao_derruba(self, window, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        avisos = []
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: avisos.append(a)))
        bad = tmp_path / "quebrado.cmbr"
        bad.write_text("{nao é json", encoding="utf-8")
        self._patch_dialog(monkeypatch, bad)
        window._open_reference()
        assert avisos and not window.canvas.has_reference()

    def test_fechar_referencia_via_menu(self, window, tmp_path, monkeypatch):
        self._patch_dialog(monkeypatch, self._cmbr(tmp_path))
        window._open_reference()
        window._close_reference()
        assert not window.canvas.has_reference()
        assert "fechada" in window.status_label.text()
        # Fechar sem referência é no-op com status honesto.
        window._close_reference()
        assert "Nenhuma" in window.status_label.text()

    def test_painel_tem_o_checkbox_do_grupo(self, window):
        chk = window.symbol_panel._visibility_checks.get("reference")
        assert chk is not None
        assert chk.isChecked()

    def test_abrir_com_grupo_oculto_reexibe_de_verdade(self, window, tmp_path, monkeypatch):
        # Bug confirmado na revisão: re-armar SÓ o checkbox (sinais
        # bloqueados) deixava o flag do canvas em False e a referência nova
        # abria invisível com a caixa marcada. O flag vem primeiro agora.
        window.canvas.set_drawings_visible("reference", False)
        self._patch_dialog(monkeypatch, self._cmbr(tmp_path))
        window._open_reference()
        assert window.canvas._drawings_visible["reference"] is True
        assert all(
            a.get_visible() for a in window.canvas._reference_artists if hasattr(a, "get_visible")
        )
        assert window.symbol_panel._visibility_checks["reference"].isChecked()

    def test_uma_feicao_no_singular(self, window, tmp_path, monkeypatch):
        self._patch_dialog(monkeypatch, self._cmbr(tmp_path, drawings=REF_RECORDS[:1]))
        window._open_reference()
        assert "1 feição (" in window.status_label.text()

    def test_aviso_de_vista_usa_o_que_esta_na_tela(self, window, tmp_path, monkeypatch):
        # Scroll/pan mudam só a VISTA (ax.get_extent), não config.extent — o
        # aviso compara com a tela: zoom longe da referência deve avisar
        # mesmo com o centro dela dentro da régua configurada.
        from PyQt6.QtWidgets import QMessageBox

        avisos = []
        monkeypatch.setattr(
            QMessageBox, "information", staticmethod(lambda *a, **k: avisos.append(a[2]))
        )
        # Vista simulada no canto noroeste, longe do centro dos records (~-46,-17).
        monkeypatch.setattr(
            window.canvas.ax, "get_extent", lambda crs=None: (-75.0, -70.0, 4.0, 6.0)
        )
        self._patch_dialog(monkeypatch, self._cmbr(tmp_path))
        window._open_reference()
        assert any("fora da vista" in msg for msg in avisos)
