"""Testes do piso seco de precipitação (transparência abaixo do limiar).

O piso é POR UNIDADE: mm/h e mm/3h (janelas curtas) usam 0,1 mm (precip.
mensurável); mm/dia e mm (diário/total) usam 1,0 mm (dia com chuva, OMM). Abaixo
do piso o pixel vira transparente no render. Aqui só se testa a lógica pura
(mapeamento e construção dos níveis), sem instanciar o canvas Qt.
"""

from __future__ import annotations

import pytest

from cartomet_br.gui.map_canvas import PRECIP_DRY_FLOOR, MapCanvas, precip_dry_floor


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("mm/h", 0.1),
        ("mm/3h", 0.1),
        ("mm/dia", 1.0),
        ("mm", 1.0),
    ],
)
def test_dry_floor_per_unit(unit, expected):
    assert precip_dry_floor(unit) == expected
    assert PRECIP_DRY_FLOOR[unit] == expected


def test_dry_floor_unknown_unit_falls_back_to_measurable():
    # unidade desconhecida → 0,1 mm (precip. mensurável), nunca 0
    assert precip_dry_floor("") == 0.1
    assert precip_dry_floor("kg/m²") == 0.1


@pytest.mark.parametrize("unit", ["mm/h", "mm/3h", "mm/dia", "mm"])
def test_precip_levels_start_at_floor_and_increase(unit):
    floor = precip_dry_floor(unit)
    levels = [floor, *[lv for lv in MapCanvas._PRECIP_LEVELS if lv > floor]]
    assert levels[0] == floor  # o menor nível é exatamente o piso
    assert all(b > a for a, b in zip(levels, levels[1:], strict=False))  # estritamente crescente
    assert all(lv >= floor for lv in levels)  # nenhum nível abaixo do piso
