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
        canvas.import_drawings_state(REF_RECORDS, reference=True)
        line = canvas._reference_artists[0]  # Line2D da frente
        assert line.get_alpha() == pytest.approx(canvas._REFERENCE_ALPHA)
        assert line.get_color() == canvas._REFERENCE_COLOR
        # Glifos do efeito (triângulos da frente) recoloridos junto.
        for ef in line.get_path_effects():
            if hasattr(ef, "color"):
                assert ef.color == canvas._REFERENCE_COLOR
        # Por baixo do traçado do aluno (linhas do usuário têm zorder 20).
        assert line.get_zorder() < 20

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
