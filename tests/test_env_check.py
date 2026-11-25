from fastapi.testclient import TestClient

from backend.api import create_app
from backend.env_check import check_environment, is_allowed_external_url
from backend.storage import LocalStorage
from tests.test_api import MockDeviceManager


def test_is_allowed_external_url():
    assert is_allowed_external_url("https://apps.microsoft.com/detail/9np83lwlpz9k") is True
    assert is_allowed_external_url("ms-windows-store://pdp/?productid=9NP83LWLPZ9K") is True
    assert is_allowed_external_url("http://example.com") is False
    assert is_allowed_external_url("javascript:alert(1)") is False
    assert is_allowed_external_url("file:///C:/Windows/System32/cmd.exe") is False
    assert is_allowed_external_url("") is False


def test_check_environment_shape():
    info = check_environment()
    assert "ready" in info
    assert info["apple_driver"]["status"] in ("ok", "stopped", "missing")
    assert info["webview2"]["status"] in ("ok", "missing")
    assert info["runtime"]["version"] == "1.2.0"


def test_env_check_and_open_url_api(tmp_path):
    app = create_app(device_manager=MockDeviceManager(), storage=LocalStorage(tmp_path))
    with TestClient(app) as tc:
        r = tc.get("/api/env-check")
        assert r.status_code == 200
        body = r.json()
        assert "apple_driver" in body
        assert "runtime" in body

        bad = tc.post("/api/system/open-url", json={"url": "file:///C:/test.exe"})
        assert bad.status_code == 400
