"""Verificação de atualização (GitHub Releases) — camada pura, sem rede real."""

import pytest

from cartomet_br.data.update_check import (
    GITHUB_RELEASES_PAGE,
    ReleaseInfo,
    UpdateCheckError,
    fetch_latest_release,
    is_newer,
    parse_version,
)


class TestParseVersion:
    def test_with_v_prefix(self):
        assert parse_version("v3.2.0") == (3, 2, 0)

    def test_without_prefix(self):
        assert parse_version("3.10.1") == (3, 10, 1)

    def test_ignores_suffix(self):
        assert parse_version("v3.2.0-beta1") == (3, 2, 0, 1)  # dígitos na ordem

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            parse_version("release-final")


class TestIsNewer:
    def test_newer(self):
        assert is_newer("v3.2.0", "3.1.0")

    def test_equal_is_not_newer(self):
        assert not is_newer("v3.1.0", "3.1.0")

    def test_older(self):
        assert not is_newer("v3.0.2", "3.1.0")

    def test_padding(self):
        # "3.2" == "3.2.0" — não anuncia atualização fantasma
        assert not is_newer("v3.2", "3.2.0")
        assert is_newer("v3.2.1", "3.2")

    def test_exotic_tag_is_never_newer(self):
        # Tag sem dígitos não pode gerar falso "atualize agora"
        assert not is_newer("nightly", "3.1.0")


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class TestFetchLatestRelease:
    def test_success(self, monkeypatch):
        payload = {
            "tag_name": "v3.2.0",
            "html_url": "https://github.com/ElivaldoRocha/CartoMet_BR/releases/tag/v3.2.0",
            "published_at": "2026-08-20T12:00:00Z",
        }
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp(payload))
        info = fetch_latest_release()
        assert isinstance(info, ReleaseInfo)
        assert info.version == "3.2.0"
        assert info.tag == "v3.2.0"
        assert info.url.endswith("v3.2.0")

    def test_missing_html_url_falls_back_to_releases_page(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp({"tag_name": "v3.2.0"}))
        assert fetch_latest_release().url == GITHUB_RELEASES_PAGE

    def test_rate_limited_403(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp({}, status=403))
        with pytest.raises(UpdateCheckError, match="403"):
            fetch_latest_release()

    def test_no_release_404(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp({}, status=404))
        with pytest.raises(UpdateCheckError, match="[Nn]enhuma release"):
            fetch_latest_release()

    def test_network_error_is_friendly(self, monkeypatch):
        import requests

        def _boom(*_a, **_k):
            raise requests.ConnectionError("DNS morto")

        monkeypatch.setattr("requests.get", _boom)
        with pytest.raises(UpdateCheckError, match="[Ss]em conexão"):
            fetch_latest_release()

    def test_tagless_response(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp({"name": "sem tag"}))
        with pytest.raises(UpdateCheckError, match="sem tag"):
            fetch_latest_release()


class TestVersionConsistency:
    def test_app_version_matches_pyproject(self):
        """APP_VERSION é a fonte exibida na GUI; não pode derivar do pyproject."""
        import tomllib
        from pathlib import Path

        from cartomet_br.gui._constants import APP_VERSION

        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        version = tomllib.loads(pyproject.read_text("utf-8"))["project"]["version"]
        assert version == APP_VERSION

    def test_package_version_matches_app_version(self):
        import cartomet_br
        from cartomet_br.gui._constants import APP_VERSION

        assert cartomet_br.__version__ == APP_VERSION
