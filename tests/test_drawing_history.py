"""Testes para o sistema de undo/redo — DrawingHistory, DrawCommand, AnnotationCommand."""

from cartomet_br.gui.draw_tools import CreateOp, PenCommand, ShapeCommand
from cartomet_br.gui.map_canvas import AnnotationCommand, DrawCommand, DrawingHistory


class TestDrawCommand:
    """Testes para a dataclass DrawCommand."""

    def test_creation(self):
        cmd = DrawCommand(
            symbol_key="1",
            points_x=[0.0, 5.0, 10.0],
            points_y=[0.0, 5.0, 0.0],
            flip=False,
        )
        assert cmd.symbol_key == "1"
        assert len(cmd.points_x) == 3
        assert cmd.flip is False
        assert cmd.artist is None

    def test_with_flip(self):
        cmd = DrawCommand(
            symbol_key="3",
            points_x=[1.0, 2.0],
            points_y=[3.0, 4.0],
            flip=True,
        )
        assert cmd.flip is True

    def test_points_are_independent_copies(self):
        original = [1.0, 2.0, 3.0]
        cmd = DrawCommand(
            symbol_key="1",
            points_x=list(original),
            points_y=list(original),
            flip=False,
        )
        original.append(4.0)
        assert len(cmd.points_x) == 3


class TestAnnotationCommand:
    """Testes para a dataclass AnnotationCommand."""

    def test_creation(self):
        cmd = AnnotationCommand(
            x=-45.0,
            y=-23.0,
            text="SP",
            color="#FFFFFF",
            fontsize=11,
        )
        assert cmd.x == -45.0
        assert cmd.text == "SP"
        assert cmd.artist is None


class TestDrawingHistory:
    """Testes para a pilha de undo/redo."""

    def _make_draw_cmd(self, key="1"):
        return DrawCommand(
            symbol_key=key,
            points_x=[0.0, 10.0],
            points_y=[0.0, 10.0],
            flip=False,
        )

    def _make_annotation_cmd(self):
        return AnnotationCommand(
            x=-50.0,
            y=-20.0,
            text="Test",
            color="#FFF",
            fontsize=10,
        )

    def test_empty_history(self):
        h = DrawingHistory()
        assert not h.can_undo
        assert not h.can_redo
        assert h.undo_count == 0
        assert h.redo_count == 0

    def test_push_enables_undo(self):
        h = DrawingHistory()
        h.push(self._make_draw_cmd())
        assert h.can_undo
        assert not h.can_redo
        assert h.undo_count == 1

    def test_undo_returns_creation_op(self):
        """v2: undo/redo devolvem a OPERAÇÃO (CreateOp) — o canvas a aplica."""
        h = DrawingHistory()
        cmd = self._make_draw_cmd("2")
        h.push(cmd)
        result = h.undo()
        assert isinstance(result, CreateOp)
        assert result.cmd is cmd
        assert not h.can_undo
        assert h.can_redo

    def test_redo_returns_undone_op(self):
        h = DrawingHistory()
        cmd = self._make_draw_cmd()
        h.push(cmd)
        h.undo()
        result = h.redo()
        assert isinstance(result, CreateOp)
        assert result.cmd is cmd
        assert h.can_undo
        assert not h.can_redo

    def test_undo_empty_returns_none(self):
        h = DrawingHistory()
        assert h.undo() is None

    def test_redo_empty_returns_none(self):
        h = DrawingHistory()
        assert h.redo() is None

    def test_push_clears_redo_stack(self):
        h = DrawingHistory()
        h.push(self._make_draw_cmd("1"))
        h.push(self._make_draw_cmd("2"))
        h.undo()
        assert h.can_redo
        h.push(self._make_draw_cmd("3"))
        assert not h.can_redo

    def test_multiple_undo_redo(self):
        h = DrawingHistory()
        cmds = [self._make_draw_cmd(str(i)) for i in range(1, 4)]
        for cmd in cmds:
            h.push(cmd)
        assert h.undo_count == 3

        r3 = h.undo()
        assert r3.cmd.symbol_key == "3"
        r2 = h.undo()
        assert r2.cmd.symbol_key == "2"

        # Redo should replay in order
        re2 = h.redo()
        assert re2.cmd.symbol_key == "2"
        re3 = h.redo()
        assert re3.cmd.symbol_key == "3"

    def test_max_size_trims_oldest_op_but_keeps_document(self):
        """O trim descarta a OPERAÇÃO mais antiga; o documento fica intacto.

        Regressão do bug antigo: o 6º push expulsava o desenho mais velho do
        salvamento (a pilha ERA o documento).
        """
        h = DrawingHistory(max_size=5)
        for i in range(10):
            h.push(self._make_draw_cmd(str(i)))
        assert h.undo_count == 5
        assert len(h.commands) == 10  # todos continuam vivos/salváveis
        oldest = h.undo()
        # A operação mais antiga desfazível é a criação do "5" (0-4 trimados)
        while h.can_undo:
            oldest = h.undo()
        assert oldest.cmd.symbol_key == "5"

    def test_clear_resets_everything(self):
        h = DrawingHistory()
        h.push(self._make_draw_cmd())
        h.push(self._make_draw_cmd())
        h.undo()
        h.clear()
        assert not h.can_undo
        assert not h.can_redo
        assert h.undo_count == 0
        assert h.redo_count == 0

    def test_mixed_commands(self):
        """Undo/redo works with both DrawCommand and AnnotationCommand."""
        h = DrawingHistory()
        draw_cmd = self._make_draw_cmd()
        annot_cmd = self._make_annotation_cmd()
        h.push(draw_cmd)
        h.push(annot_cmd)

        result = h.undo()
        assert isinstance(result.cmd, AnnotationCommand)
        result = h.undo()
        assert isinstance(result.cmd, DrawCommand)

    def test_redo_after_multiple_undos(self):
        h = DrawingHistory()
        h.push(self._make_draw_cmd("1"))
        h.push(self._make_draw_cmd("2"))
        h.push(self._make_draw_cmd("3"))
        h.undo()
        h.undo()
        h.undo()
        assert h.redo_count == 3
        r = h.redo()
        assert r.cmd.symbol_key == "1"


class TestRemoveLastOf:
    """Desfazer tipo-filtrado (botões '↩ Desfazer traço/forma' — padrão do emoji)."""

    @staticmethod
    def _pen(tag: float) -> PenCommand:
        return PenCommand(points_x=[tag], points_y=[tag], style={})

    @staticmethod
    def _shape(tool: str = "rect") -> ShapeCommand:
        return ShapeCommand(tool=tool, points_x=[0, 1], points_y=[0, 1], style={})

    def test_removes_most_recent_of_type_even_from_middle(self):
        # traço(1) → forma → traço(2): remover forma tira a do MEIO da pilha
        h = DrawingHistory()
        p1, s, p2 = self._pen(1.0), self._shape(), self._pen(2.0)
        for c in (p1, s, p2):
            h.push(c)
        removed = h.remove_last_of((ShapeCommand,))
        assert removed is s
        assert h.undo_count == 2  # a op de criação da forma foi expurgada
        # ordem dos demais preservada: undo devolve o traço mais recente
        assert h.undo().cmd is p2
        assert h.undo().cmd is p1

    def test_picks_latest_when_multiple_of_type(self):
        h = DrawingHistory()
        p1, p2 = self._pen(1.0), self._pen(2.0)
        h.push(p1)
        h.push(self._shape())
        h.push(p2)
        assert h.remove_last_of((PenCommand,)) is p2
        assert h.remove_last_of((PenCommand,)) is p1
        assert h.remove_last_of((PenCommand,)) is None  # esgotou o tipo

    def test_returns_none_when_type_absent(self):
        h = DrawingHistory()
        h.push(self._shape())
        assert h.remove_last_of((PenCommand,)) is None
        assert h.undo_count == 1  # nada removido

    def test_does_not_touch_redo_stack(self):
        h = DrawingHistory()
        h.push(self._pen(1.0))
        h.push(self._shape())
        h.undo()  # forma vai p/ redo
        assert h.can_redo
        h.remove_last_of((PenCommand,))
        assert h.can_redo  # redo intacto
