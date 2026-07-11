"""Round-trip end-to-end do projeto (.cmbr) no MapCanvas — offscreen.

Exercita o caminho real de reconstrução (``import_drawings_state`` →
``_rebuild_artist``) e de serialização (``export_drawings_state``) com um
MapCanvas de verdade. Roda sob ``QT_QPA_PLATFORM=offscreen``; se o Qt não puder
iniciar neste ambiente, o módulo é pulado em vez de falhar.
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from cartomet_br.core.config import EXTENT_BRASIL, Config
from cartomet_br.gui.draw_tools import (
    AnnotationCommand,
    DrawCommand,
    EmojiCommand,
    PenCommand,
    PointCommand,
    ShapeCommand,
)
from cartomet_br.gui.project_io import commands_to_records


def _sample_commands() -> list:
    style = {
        "edge_color": "#2E86C1",
        "fill_color": None,
        "linewidth": 2.0,
        "linestyle": "dashed",
        "alpha": 0.8,
    }
    return [
        DrawCommand("6", [-40.0, -35.0, -30.0], [2.0, 1.5, 1.0], flip=True, intensity=3),
        PointCommand("a", -45.0, -10.0),
        AnnotationCommand(-50.0, 5.0, "ZCIT forte", "#CC0000", 12),
        PenCommand([-20.0, -21.0, -22.0], [-3.0, -3.5, -4.0], dict(style)),
        ShapeCommand("rect", [-10.0, -5.0], [0.0, 4.0], dict(style), 0.0),
        EmojiCommand(-38.0, 0.5, "⛈", 40),
    ]


@pytest.fixture(scope="module")
def qapp():
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PyQt6 indisponível: {exc}")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def canvas(qapp, tmp_path):
    from cartomet_br.gui.map_canvas import MapCanvas

    data_dir = tmp_path / "data"
    out_dir = tmp_path / "out"
    data_dir.mkdir()
    out_dir.mkdir()
    cfg = Config(extent=EXTENT_BRASIL.copy(), data_dir=data_dir, output_dir=out_dir)
    try:
        return MapCanvas(config=cfg)
    except Exception as exc:  # noqa: BLE001 — ambiente sem render
        pytest.skip(f"MapCanvas não pôde ser criado offscreen: {exc}")


def _multiset(records):
    return sorted(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records)


def test_import_then_export_preserves_every_drawing(canvas):
    records_in = commands_to_records(_sample_commands())

    canvas.import_drawings_state(records_in)

    # Artistas reconstruídos foram parar nas listas certas.
    assert len(canvas.history.commands) == 5  # tudo menos o emoji
    assert len(canvas._emoji_records) == 1
    assert len(canvas._annotations) == 1  # a anotação de texto

    records_out = canvas.export_drawings_state()
    assert len(records_out) == len(records_in)
    # Cada desenho sobreviveu com os mesmos dados (ordem pode diferir).
    assert _multiset(records_out) == _multiset(records_in)


def test_clear_all_resets_drawings_and_emojis(canvas):
    canvas.import_drawings_state(commands_to_records(_sample_commands()))
    canvas.clear_all()
    assert canvas.history.commands == []
    assert canvas._emoji_records == []
    assert canvas.export_drawings_state() == []


def test_loaded_drawings_are_undoable(canvas):
    canvas.import_drawings_state(commands_to_records(_sample_commands()))
    assert canvas.history.can_undo
    n_before = len(canvas.history.commands)
    canvas.undo_action() if hasattr(canvas, "undo_action") else canvas.history.undo()
    assert len(canvas.history.commands) == n_before - 1


def test_pending_line_is_committed_before_save(canvas):
    """Regressão: linha em rascunho (sem [Enter]) não pode ser perdida ao salvar.

    Reproduz o caso do usuário — vários cliques de uma linha de símbolo que nunca
    foi finalizada com [Enter] — e garante que ``commit_pending_line`` a leva ao
    histórico, fazendo ``export_drawings_state`` enxergá-la.
    """
    canvas.set_drawing_mode(True)
    canvas.set_symbol("6")  # ZCIT (linha, não símbolo pontual)
    for lon, lat in [(-40.0, 2.0), (-35.0, 1.0), (-30.0, 0.0)]:
        canvas.points_x.append(lon)
        canvas.points_y.append(lat)
        canvas._update_preview()

    # Antes de finalizar: rascunho fora do histórico → export vazio (o bug).
    assert canvas.history.commands == []
    assert canvas.export_drawings_state() == []

    assert canvas.commit_pending_line() is True
    records = canvas.export_drawings_state()
    assert len(records) == 1 and records[0]["type"] == "symbol_line"
    assert records[0]["points_x"] == [-40.0, -35.0, -30.0]

    # Idempotente: sem rascunho pendente, é no-op.
    assert canvas.commit_pending_line() is False


def test_commit_pending_line_noop_without_draft(canvas):
    """Sem rascunho ativo, commit_pending_line não altera nada."""
    assert canvas.commit_pending_line() is False
    assert canvas.export_drawings_state() == []


def test_export_layers_state_builds_restore_manifest(canvas):
    """O manifesto de camadas reflete carta sinótica + campos PL + satélite,
    com os parâmetros (variável/nível/step/wind_type/visibilidade) p/ o restore."""
    import numpy as np

    from cartomet_br.data.ecmwf import PLFieldData, SynopticData

    # Sem camadas → manifesto vazio.
    assert canvas.export_layers_state() == []

    # Carta sinótica + visibilidade (espessura desligada).
    canvas.synoptic_data = SynopticData(
        pnmm=np.zeros((3, 3)),
        thickness=np.ones((3, 3)),
        lons=np.array([-50.0, -45.0, -40.0]),
        lats=np.array([0.0, -5.0, -10.0]),
        lon2d=np.zeros((3, 3)),
        lat2d=np.zeros((3, 3)),
        valid_time="2026-06-14T12:00",
        extent=[-55.0, -15.0, 15.0, 15.0],
        step=0,
    )
    canvas.plot_options = {"pnmm": True, "thickness": False, "centers": True}

    # Campo PL (vento 850) — injeta direto nos stores (sem plotar).
    canvas._pl_data["wind_850_barbs"] = PLFieldData(
        values=np.zeros((3, 3)),
        lons=np.array([-50.0, -45.0, -40.0]),
        lats=np.array([0.0, -5.0, -10.0]),
        variable="wind",
        level=850,
        step=24,
    )
    canvas._pl_wind_types["wind_850_barbs"] = "barbs"
    canvas._pl_wind_color["wind_850_barbs"] = "#E74C3C"
    canvas._pl_wind_density["wind_850_barbs"] = "alta"

    # Satélite GOES em cache.
    class _Sat:
        filename = "OR_ABI-L2-CMIPF-M6C13_G19_s2026.nc"

    canvas._sat_data = _Sat()

    manifest = canvas.export_layers_state()
    by_kind = {m["kind"]: m for m in manifest}

    assert by_kind["synoptic"]["visibility"] == {"pnmm": True, "thickness": False, "centers": True}
    assert by_kind["synoptic"]["step"] == 0

    field = by_kind["field"]
    assert field["variable"] == "wind" and field["level"] == 850
    assert field["step"] == 24 and field["wind_type"] == "barbs"
    assert field["color"] == "#E74C3C" and field["density"] == "alta"

    assert by_kind["satellite"]["filename"] == "OR_ABI-L2-CMIPF-M6C13_G19_s2026.nc"


def test_restore_layers_from_cache_miss_never_networks(canvas, tmp_path):
    """Abrir um projeto cujas camadas NÃO estão no cache: cada uma vira *miss*,
    nenhuma rede é tocada e a GUI não quebra (human-in-the-loop preservado).

    Liga os métodos reais de MainWindow a um ``self`` mínimo (canvas real + stub
    de painel) — evita instanciar a janela inteira, que tem init pesado.
    """
    import types
    from unittest.mock import patch

    from cartomet_br.core.config import Config
    from cartomet_br.gui.main_window import MainWindow

    class _FieldPanelStub:
        def remove_layer_entry(self, *a):  # só usado no caminho de sucesso
            pass

        def add_layer_entry(self, *a):
            pass

    fake = types.SimpleNamespace(
        config=Config(data_dir=tmp_path / "data", output_dir=tmp_path / "out"),
        canvas=canvas,
        field_panel=_FieldPanelStub(),
    )
    for name in (
        "_restore_layers_from_cache",
        "_add_field_panel_entry",
        "_restore_satellite_from_cache",
        "_layer_label",
    ):
        setattr(fake, name, types.MethodType(getattr(MainWindow, name), fake))

    manifest = [
        {
            "kind": "synoptic",
            "step": 0,
            "visibility": {"pnmm": True, "thickness": True, "centers": True},
        },
        {"kind": "field", "variable": "wind", "level": 850, "step": 0, "wind_type": "barbs"},
        {"kind": "satellite", "filename": "ausente.nc"},
        {"kind": "sst", "time_str": "2026-06-14", "stride": 5},
        {"kind": "loczcit", "axis": True},
        {"kind": "blocking"},
        {"kind": "observations", "metar": True, "synop": False},
    ]
    ctx = {"cycle": 12, "cycle_date": "20260614", "step": 0, "technique": "direct"}

    with patch("cartomet_br.data.ecmwf.Client") as mock_client_cls:
        n_restored, missed, reactivate = fake._restore_layers_from_cache(manifest, ctx)

    assert n_restored == 0  # cache vazio → nada restaurado do cache
    assert len(missed) == 4  # sinótica, campo, satélite e TSM fora do cache
    # Calculadas/externas memorizadas p/ reativação manual (nunca recomputam só).
    assert len(reactivate) == 3
    assert any("LOCZCIT" in r for r in reactivate)
    assert any("METAR" in r for r in reactivate)
    mock_client_cls.assert_not_called()  # NUNCA foi à rede
