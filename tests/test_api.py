import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.storage import LocalStorage


class MockDeviceManager:
    def __init__(self) -> None:
        self.connected = True
        self.transport = "usb"
        self.ddi_mounted = True
        self.status_message = "已连接 Mock iPhone (iOS 27.0)"
        self.device_info = {
            "udid": "MOCK-UDID-0001",
            "name": "Mock iPhone",
            "ios_version": "27.0",
            "developer_mode": True,
        }
        self.sent_coords: list[tuple[float, float]] = []
        self.cleared = 0
        self.logs: list[dict] = []

    def get_state(self) -> dict:
        return {
            "connected": self.connected,
            "transport": self.transport,
            "ddi_mounted": self.ddi_mounted,
            "status_message": self.status_message,
            "device_info": dict(self.device_info),
        }

    def get_logs(self) -> list[dict]:
        return list(self.logs)

    async def add_log(self, level: str, message: str) -> None:
        self.logs.append({"time": "00:00:00", "level": level, "message": message})

    async def list_devices(self) -> dict:
        return {"usb": [self.device_info], "wifi": []}

    async def set_location(self, lat: float, lng: float) -> None:
        self.sent_coords.append((lat, lng))

    async def clear_location(self) -> None:
        self.cleared += 1

    async def disconnect(self, clear_location: bool = False) -> dict:
        if clear_location:
            self.cleared += 1
        self.connected = False
        return self.get_state()


@pytest.fixture()
def client(tmp_path):
    dm = MockDeviceManager()
    storage = LocalStorage(tmp_path)
    app = create_app(device_manager=dm, storage=storage, server_port=8765)
    with TestClient(app) as tc:
        yield tc, dm


def test_health_and_static_index(client):
    tc, _ = client
    r = tc.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    home = tc.get("/")
    assert home.status_code == 200
    assert "iOS27FakeRun" in home.text


def test_route_parse_track_gen_and_crud(client):
    tc, _ = client
    gen = tc.post(
        "/api/route/generate-track",
        json={"center_lat": 39.9928, "center_lng": 116.3102, "points_per_lap": 40},
    )
    assert gen.status_code == 200
    pts = gen.json()["points"]
    assert len(pts) == 40

    saved = tc.post("/api/routes", json={"name": "操场A", "points": pts, "closed": True})
    assert saved.status_code == 200
    rid = saved.json()["id"]

    listed = tc.get("/api/routes").json()
    assert len(listed) == 1
    assert listed[0]["name"] == "操场A"

    deleted = tc.delete(f"/api/routes/{rid}")
    assert deleted.status_code == 200
    assert tc.get("/api/routes").json() == []


def test_pin_nudge_and_reset_api(client):
    tc, dm = client
    pin_resp = tc.post(
        "/api/pin/set",
        json={"lat": 39.9928, "lng": 116.3102, "crs": "gcj02", "micro_drift_m": 0.0},
    )
    assert pin_resp.status_code == 200
    assert len(dm.sent_coords) >= 1
    # Device receives WGS-84 converted from GCJ-02 (so not identical to input GCJ-02)
    sent_lat, sent_lng = dm.sent_coords[0]
    assert abs(sent_lat - 39.9928) > 1e-4 or abs(sent_lng - 116.3102) > 1e-4

    nudge_resp = tc.post("/api/pin/nudge", json={"north_m": 15.0, "east_m": 0.0})
    assert nudge_resp.status_code == 200

    reset_resp = tc.post("/api/device/reset-location")
    assert reset_resp.status_code == 200
    assert dm.cleared >= 1


def test_websocket_snapshot(client):
    tc, _ = client
    with tc.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "snapshot"
        assert msg["data"]["device"]["connected"] is True
        assert msg["data"]["simulation"]["mode"] == "idle"


def test_six_point_generator_and_run_start_v12(client):
    tc, dm = client
    six = tc.post(
        "/api/route/generate-six-point",
        json={"center_lat": 34.0165, "center_lng": 108.7496, "length_m": 100.0, "width_m": 50.0},
    )
    assert six.status_code == 200
    pts = six.json()["points"]
    assert len(pts) == 6

    start = tc.post(
        "/api/run/start",
        json={
            "points": pts,
            "route_mode": "loop",
            "speed_mps": 3.5,
            "laps": 3,
            "pace_profile": "fatigue",
            "fatigue_pct": 20.0,
            "gps_drift_m": 1.5,
        },
    )
    assert start.status_code == 200
    sim = start.json()["simulation"]
    assert sim["pace_profile"] == "fatigue"
    assert sim["route_mode"] == "loop"
    assert len(dm.sent_coords) >= 1
    tc.post("/api/run/stop", json={"reset_location": True})
