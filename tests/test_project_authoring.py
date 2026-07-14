"""Autoria leve no envelope do projeto (.cmbr schema v4) — módulo puro.

O arquivo ganha ``author`` (analista original) e ``revisions`` (trilha de
salvamentos {"name","saved_at"}). Projetos ≤ v3 abrem com defaults; entradas
malformadas são ignoradas na leitura (validação tolerante).
"""

import json

from cartomet_br.gui import project_io


def _project(**kwargs):
    return project_io.build_project(
        extent=[-80.0, -50.0, -30.0, 10.0],
        theme="relevo",
        data_context={},
        layers=[],
        drawings=[],
        **kwargs,
    )


def test_v4_roundtrip_preserves_authorship():
    rev_a = project_io.make_revision("Analista A")
    proj = _project(author="Analista A", revisions=[rev_a])
    assert proj["schema_version"] == 4

    data = project_io.load_project(project_io.dump_project(proj))
    auth = project_io.read_authorship(data)
    assert auth["author"] == "Analista A"
    assert auth["revisions"] == [rev_a]


def test_revision_appends_preserving_original_author():
    """Fluxo A→B: B salva por cima e vira revisão, sem roubar a autoria de A."""
    proj = _project(author="A", revisions=[project_io.make_revision("A")])
    data = project_io.load_project(project_io.dump_project(proj))
    auth = project_io.read_authorship(data)

    # O que _authorship_for_save faz no salvamento de B:
    author = auth["author"] or "B"
    revisions = [*auth["revisions"], project_io.make_revision("B")]
    proj2 = _project(author=author, revisions=revisions)

    auth2 = project_io.read_authorship(json.loads(project_io.dump_project(proj2)))
    assert auth2["author"] == "A"
    assert [r["name"] for r in auth2["revisions"]] == ["A", "B"]


def test_v3_project_opens_with_defaults():
    """Projeto antigo (sem as chaves de autoria) → autor None, revisões []."""
    proj = _project()
    proj["schema_version"] = 3
    del proj["author"]
    del proj["revisions"]
    data = project_io.load_project(project_io.dump_project(proj))
    auth = project_io.read_authorship(data)
    assert auth == {"author": None, "revisions": []}


def test_read_authorship_is_tolerant():
    """Lixo nas chaves não derruba a abertura: entradas inválidas são ignoradas."""
    data = {
        "author": "   ",  # em branco → None
        "revisions": [
            {"name": "A", "saved_at": "2026-07-13T12:00:00+00:00"},
            {"saved_at": "sem nome"},  # inválida
            "não é dict",  # inválida
            {"name": "", "saved_at": "vazio"},  # inválida
            {"name": "B"},  # sem data → data vazia
        ],
    }
    auth = project_io.read_authorship(data)
    assert auth["author"] is None
    assert [r["name"] for r in auth["revisions"]] == ["A", "B"]
    assert auth["revisions"][1]["saved_at"] == ""


def test_make_revision_shape():
    rev = project_io.make_revision("Fulano")
    assert rev["name"] == "Fulano"
    assert "T" in rev["saved_at"]  # ISO-8601
