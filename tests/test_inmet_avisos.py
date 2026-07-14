"""Parsing dos Avisos INMET (camada de dados pura) — SEM rede.

Alimenta ``parse_avisos`` com um payload fixo (no formato da API ``apiprevmet3``)
e verifica a normalização: Polygon e MultiPolygon viram anéis, cor default quando
``aviso_cor`` falta, descarte de geometria degenerada/fora do Brasil, e coerção
de ``riscos``/``instrucoes`` para lista.
"""

import json

from cartomet_br.data.inmet_avisos import (
    AvisoINMET,
    avisos_bbox,
    parse_avisos,
)


def _polygon(coords: list[list[float]]) -> str:
    return json.dumps({"type": "Polygon", "coordinates": [coords]})


def _multipolygon(rings: list[list[list[float]]]) -> str:
    return json.dumps({"type": "MultiPolygon", "coordinates": [[r] for r in rings]})


# Um payload representativo no formato real da API.
_PAYLOAD = {
    "hoje": [
        {
            "severidade": "Perigo",
            "descricao": "Tempestade",
            "aviso_cor": "#F96602",
            "riscos": ["Ventos intensos", "Granizo"],
            "instrucoes": ["Evite áreas abertas"],
            "estados": "PR,SC",
            "inicio": "2026-07-11 00:00",
            "fim": "2026-07-11 23:59",
            "poligono": _polygon(
                [[-53.0, -26.0], [-51.0, -26.0], [-51.0, -24.0], [-53.0, -24.0], [-53.0, -26.0]]
            ),
        },
        {
            # MultiPolygon → dois anéis; sem aviso_cor → cor default.
            "severidade": "Perigo Potencial",
            "descricao": "Chuvas Intensas",
            "riscos": "Alagamentos",  # string única (deve virar lista de 1)
            "instrucoes": [],
            "poligono": _multipolygon(
                [
                    [[-60.0, -10.0], [-58.0, -10.0], [-58.0, -8.0], [-60.0, -8.0]],
                    [[-45.0, -12.0], [-43.0, -12.0], [-43.0, -10.0], [-45.0, -10.0]],
                ]
            ),
        },
    ],
    "futuro": [
        {
            # Geometria degenerada (2 pontos) → descartada inteira (sem anel válido).
            "severidade": "Perigo",
            "descricao": "Tempestade",
            "aviso_cor": "#F96602",
            "poligono": _polygon([[-50.0, -20.0], [-49.0, -20.0]]),
        },
        {
            # Fora do Brasil (Europa) → anel descartado → aviso sem geometria → fora.
            "severidade": "Perigo",
            "descricao": "Tempestade",
            "poligono": _polygon([[10.0, 50.0], [12.0, 50.0], [12.0, 52.0], [10.0, 52.0]]),
        },
    ],
}


def test_parse_counts_only_valid_geometry():
    avisos = parse_avisos(_PAYLOAD)
    # 2 válidos em "hoje"; os 2 de "futuro" caem (degenerado + fora do Brasil).
    assert len(avisos) == 2
    assert all(isinstance(a, AvisoINMET) for a in avisos)


def test_polygon_and_multipolygon_rings():
    avisos = parse_avisos(_PAYLOAD)
    por_desc = {a.descricao: a for a in avisos}
    assert len(por_desc["Tempestade"].rings) == 1  # Polygon → 1 anel
    assert len(por_desc["Chuvas Intensas"].rings) == 2  # MultiPolygon → 2 anéis


def test_default_color_when_missing():
    avisos = parse_avisos(_PAYLOAD)
    chuva = next(a for a in avisos if a.descricao == "Chuvas Intensas")
    assert chuva.cor == "#9E9E9E"  # _DEFAULT_COLOR
    tempestade = next(a for a in avisos if a.descricao == "Tempestade")
    assert tempestade.cor == "#F96602"


def test_riscos_coerced_to_list():
    avisos = parse_avisos(_PAYLOAD)
    chuva = next(a for a in avisos if a.descricao == "Chuvas Intensas")
    assert chuva.riscos == ["Alagamentos"]  # string única → lista de 1
    assert chuva.instrucoes == []


def test_label_point_and_bbox():
    avisos = parse_avisos(_PAYLOAD)
    tempestade = next(a for a in avisos if a.descricao == "Tempestade")
    lon, lat = tempestade.label_point
    assert -53.0 <= lon <= -51.0 and -26.0 <= lat <= -24.0
    box = avisos_bbox(avisos)
    assert box is not None
    lon_min, lat_min, lon_max, lat_max = box
    assert lon_min < lon_max and lat_min < lat_max


def test_invalid_color_falls_back_to_default():
    """A cor da API vai direto ao Matplotlib no render — formato que ele não
    aceita (hex sem '#', rgb(...), lixo) estouraria ValueError no slot da GUI;
    o parse precisa devolver o default nesses casos."""
    rec = dict(_PAYLOAD["hoje"][0])
    for bad in ("F96602", "rgb(255,0,0)", "laranja", "#12345", "#GGHHII"):
        rec["aviso_cor"] = bad
        (aviso,) = parse_avisos({"hoje": [rec], "futuro": []})
        assert aviso.cor == "#9E9E9E", f"cor inválida {bad!r} não caiu no default"
    for ok in ("#F96602", "#fff", "#FF880022"):
        rec["aviso_cor"] = ok
        (aviso,) = parse_avisos({"hoje": [rec], "futuro": []})
        assert aviso.cor == ok


def test_label_point_prefers_largest_area_ring():
    """MultiPolygon: um anel pequeno porém DETALHADO (muitos vértices) não pode
    roubar o rótulo do anel grande e simples — o critério é ÁREA, não len()."""
    import math

    big = [[-60.0, -20.0], [-50.0, -20.0], [-50.0, -10.0], [-60.0, -10.0]]  # 10°×10°
    small = [  # ~0.5° de diâmetro, 24 vértices, em volta de (-45, -12)
        [
            -45.0 + 0.25 * math.cos(2 * math.pi * k / 24),
            -12.0 + 0.25 * math.sin(2 * math.pi * k / 24),
        ]
        for k in range(24)
    ]
    rec = {
        "severidade": "Perigo",
        "descricao": "Tempestade",
        "aviso_cor": "#F96602",
        "poligono": _multipolygon([big, small]),
    }
    (aviso,) = parse_avisos({"hoje": [rec], "futuro": []})
    lon, lat = aviso.label_point
    assert -60.0 <= lon <= -50.0 and -20.0 <= lat <= -10.0, "rótulo ancorou no anel pequeno"


def test_empty_and_malformed_payload():
    assert parse_avisos({"hoje": [], "futuro": []}) == []
    assert parse_avisos({}) == []
    # Buckets ausentes ou tipos errados não explodem.
    assert parse_avisos({"hoje": None, "futuro": "x"}) == []


def test_non_dict_payload_raises():
    import pytest

    from cartomet_br.data.inmet_avisos import InmetAvisosError

    with pytest.raises(InmetAvisosError):
        parse_avisos(["não", "é", "dict"])
