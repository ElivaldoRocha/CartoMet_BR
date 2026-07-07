"""Sanidade dos recortes por estado (EXTENT_UFS) — combo "Estado:" da GUI."""

from cartomet_br.core.config import EXTENT_BRASIL, EXTENT_UFS, UF_NOMES, validate_extent

# Capitais (lon, lat) para spot-check de continência do recorte
_CAPITAIS = {
    "AC": (-67.81, -9.97),  # Rio Branco
    "AM": (-60.02, -3.12),  # Manaus
    "BA": (-38.51, -12.97),  # Salvador
    "DF": (-47.93, -15.78),  # Brasília
    "PA": (-48.50, -1.46),  # Belém
    "RO": (-63.90, -8.76),  # Porto Velho
    "RS": (-51.23, -30.03),  # Porto Alegre
    "SP": (-46.63, -23.55),  # São Paulo
}


def test_todas_as_27_ufs_presentes():
    assert len(EXTENT_UFS) == 27
    assert set(EXTENT_UFS) == set(UF_NOMES)


def test_extents_validos_e_inteiros():
    for uf, extent in EXTENT_UFS.items():
        validate_extent(extent)  # lança ValueError se inconsistente
        assert all(float(v).is_integer() for v in extent), (
            f"{uf}: spinboxes da GUI são inteiros — extent {extent} dessincronizaria o combo"
        )


def test_extents_dentro_do_envelope_brasil():
    lon_min_br, lat_min_br, lon_max_br, lat_max_br = EXTENT_BRASIL
    for uf, (lon_min, lat_min, lon_max, lat_max) in EXTENT_UFS.items():
        assert lon_min >= lon_min_br and lon_max <= lon_max_br, f"{uf} fora do envelope (lon)"
        assert lat_min >= lat_min_br and lat_max <= lat_max_br, f"{uf} fora do envelope (lat)"


def test_capitais_contidas_no_recorte():
    for uf, (lon, lat) in _CAPITAIS.items():
        lon_min, lat_min, lon_max, lat_max = EXTENT_UFS[uf]
        assert lon_min < lon < lon_max, f"{uf}: capital fora do recorte (lon)"
        assert lat_min < lat < lat_max, f"{uf}: capital fora do recorte (lat)"


def test_recorte_tem_folga_para_carta():
    # Recorte estreito demais degenera a carta (aspecto extremo) e não deixa
    # espaço para rótulos/símbolos — exige-se um mínimo de 2° em cada eixo.
    for uf, (lon_min, lat_min, lon_max, lat_max) in EXTENT_UFS.items():
        assert lon_max - lon_min >= 2.0, f"{uf}: recorte estreito demais em lon"
        assert lat_max - lat_min >= 2.0, f"{uf}: recorte estreito demais em lat"
