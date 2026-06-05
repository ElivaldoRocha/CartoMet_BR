"""Testes para a lógica de recálculo de extent no zoom/recorte (sem GUI)."""


from cartomet_br.gui.map_canvas import MapCanvas

# ═══════════════════════════════════════════════════════════════════════════════
#  _clamp_extent — mantém o extent dentro de limites geográficos válidos
# ═══════════════════════════════════════════════════════════════════════════════

class TestClampExtent:
    def test_valid_extent_unchanged(self):
        ext = [-75.0, -35.0, -30.0, 6.0]
        assert MapCanvas._clamp_extent(list(ext)) == ext

    def test_clamps_longitude_bounds(self):
        out = MapCanvas._clamp_extent([-200.0, -35.0, 200.0, 6.0])
        assert out[0] >= -180.0
        assert out[2] <= 180.0

    def test_clamps_latitude_bounds(self):
        out = MapCanvas._clamp_extent([-75.0, -120.0, -30.0, 120.0])
        assert out[1] >= -90.0
        assert out[3] <= 90.0

    def test_enforces_minimum_span(self):
        # lon_max deve ficar pelo menos 1° acima de lon_min
        out = MapCanvas._clamp_extent([-50.0, -20.0, -50.0, -20.0])
        assert out[2] > out[0]
        assert out[3] > out[1]

    def test_returns_canonical_order(self):
        out = MapCanvas._clamp_extent([-75.0, -35.0, -30.0, 6.0])
        lon_min, lat_min, lon_max, lat_max = out
        assert lon_min < lon_max
        assert lat_min < lat_max
