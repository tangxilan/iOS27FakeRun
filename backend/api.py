"""FastAPI application and WebSocket hub for iOS27FakeRun."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .coords import from_wgs84, to_wgs84, wgs84_to_gcj02
from .device_manager import DeviceManager
from .env_check import check_environment, is_allowed_external_url
from .geocode import search_places
from .paths import APP_VERSION, get_data_dir, get_resource_dir
from .route_utils import (
    export_gpx,
    export_iosfakerun_json,
    generate_standard_track,
    parse_route_text,
    route_length_meters,
    smooth_route_catmull_rom,
)
from .simulation_engine import PinConfig, RunConfig, SimulationEngine
from .storage import LocalStorage

STATIC_DIR = get_resource_dir() / "static"
DATA_DIR = get_data_dir()


class WsHub:
    """Broadcasts device state, simulation telemetry, and logs to connected browser/phone clients."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, event_type: str, data: Any) -> None:
        async with self._lock:
            clients = list(self._clients)
        if not clients:
            return
        payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


def _get_lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


# --- Request schemas ---
class ConnectRequest(BaseModel):
    udid: Optional[str] = None
    transport: str = Field(default="usb", pattern="^(usb|wifi)$")
    auto_mount_ddi: bool = True


class DisconnectRequest(BaseModel):
    clear_location: bool = False


class DeviceActionRequest(BaseModel):
    udid: Optional[str] = None


class StartRunRequest(BaseModel):
    points: list[list[float]]
    closed_loop: bool = True
    speed_mps: float = Field(default=3.0, gt=0.1, le=30.0)
    mode_limit: str = Field(default="laps", pattern="^(laps|distance)$")
    laps: int = Field(default=5, ge=1, le=500)
    target_distance_m: float = Field(default=2000.0, ge=10.0, le=200000.0)
    interval_s: float = Field(default=1.0, ge=0.2, le=5.0)
    speed_jitter_pct: float = Field(default=8.0, ge=0.0, le=40.0)
    gps_drift_m: float = Field(default=0.8, ge=0.0, le=10.0)
    smooth_curves: bool = False


class UpdateRunParamsRequest(BaseModel):
    speed_mps: Optional[float] = Field(default=None, gt=0.1, le=30.0)
    speed_jitter_pct: Optional[float] = Field(default=None, ge=0.0, le=40.0)
    gps_drift_m: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    interval_s: Optional[float] = Field(default=None, ge=0.2, le=5.0)


class StopRunRequest(BaseModel):
    reset_location: bool = False


class SetPinRequest(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)
    crs: str = Field(default="wgs84")
    micro_drift_m: float = Field(default=0.0, ge=0.0, le=5.0)
    heartbeat_s: float = Field(default=2.0, ge=0.5, le=10.0)


class NudgePinRequest(BaseModel):
    north_m: float = Field(ge=-1000.0, le=1000.0)
    east_m: float = Field(ge=-1000.0, le=1000.0)


class ParseRouteRequest(BaseModel):
    text: str
    input_crs: str = Field(default="wgs84")
    lng_first: bool = False


class ExportRouteRequest(BaseModel):
    points: list[list[float]]
    format: str = Field(default="json", pattern="^(json|gpx)$")
    output_crs: str = Field(default="bd09")
    name: str = "CampusRun"
    speed_mps: float = 3.0


class GenerateTrackRequest(BaseModel):
    center_lat: float = Field(ge=-90.0, le=90.0)
    center_lng: float = Field(ge=-180.0, le=180.0)
    center_crs: str = "wgs84"
    straight_m: float = Field(default=84.39, ge=10.0, le=300.0)
    radius_m: float = Field(default=36.8, ge=5.0, le=150.0)
    rotation_deg: float = Field(default=0.0, ge=-360.0, le=360.0)
    points_per_lap: int = Field(default=60, ge=16, le=360)
    counterclockwise: bool = True


class SmoothRouteRequest(BaseModel):
    points: list[list[float]]
    closed: bool = True
    samples_per_segment: int = Field(default=8, ge=2, le=20)


class SaveRouteRequest(BaseModel):
    id: Optional[str] = None
    name: str
    points: list[list[float]]
    closed: bool = True


class SavePinRequest(BaseModel):
    id: Optional[str] = None
    name: str
    note: str = ""
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)
    crs: str = "wgs84"


class LastStateRequest(BaseModel):
    state: dict[str, Any]


class OpenUrlRequest(BaseModel):
    url: str


def create_app(
    device_manager: Optional[DeviceManager] = None,
    storage: Optional[LocalStorage] = None,
    server_port: int = 8765,
) -> FastAPI:
    hub = WsHub()
    store = storage or LocalStorage(DATA_DIR)

    if device_manager is None:
        dm = DeviceManager(
            on_state_change=lambda s: hub.broadcast("device", s),
            on_log=lambda entry: hub.broadcast("log", entry),
        )
    else:
        dm = device_manager

    engine = SimulationEngine(
        device=dm,
        on_update=lambda s: hub.broadcast("simulation", s),
        on_log=lambda level, msg: dm.add_log(level, msg),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        with contextlib.suppress(Exception):
            await engine.stop(reset_location=False)
        with contextlib.suppress(Exception):
            await dm.disconnect(clear_location=False)

    app = FastAPI(title="iOS27FakeRun", version=APP_VERSION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.device_manager = dm
    app.state.engine = engine
    app.state.storage = store

    def _to_point_list(raw: list[list[float]]) -> list[tuple[float, float]]:
        pts: list[tuple[float, float]] = []
        for item in raw:
            if len(item) < 2:
                raise HTTPException(status_code=400, detail="坐标点必须包含 [lat, lng]")
            pts.append((float(item[0]), float(item[1])))
        return pts

    # --- Health & Snapshot ---
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": APP_VERSION}

    @app.get("/api/env-check")
    async def env_check() -> dict[str, Any]:
        return check_environment()

    @app.post("/api/system/open-url")
    async def system_open_url(req: OpenUrlRequest) -> dict[str, Any]:
        if not is_allowed_external_url(req.url):
            raise HTTPException(status_code=400, detail="仅支持打开 https:// 或 ms-windows-store:// 链接")
        target = req.url.strip()
        try:
            if hasattr(os, "startfile"):
                os.startfile(target)  # type: ignore[attr-defined]
            else:
                webbrowser.open(target)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"无法打开外部链接: {exc}") from exc
        return {"ok": True}

    @app.get("/api/state")
    async def get_state() -> dict[str, Any]:
        lan_urls = [f"http://{ip}:{server_port}" for ip in _get_lan_ips()]
        return {
            "device": dm.get_state(),
            "simulation": engine.get_state(),
            "logs": dm.get_logs(),
            "last_state": store.get_last_state(),
            "lan_urls": lan_urls,
        }

    @app.post("/api/last-state")
    async def save_last_state(req: LastStateRequest) -> dict[str, Any]:
        store.save_last_state(req.state)
        return {"ok": True}

    # --- Device endpoints ---
    @app.get("/api/devices")
    async def list_devices() -> dict[str, Any]:
        try:
            return await dm.list_devices()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"扫描设备失败: {exc}") from exc

    @app.post("/api/device/connect")
    async def connect_device(req: ConnectRequest) -> dict[str, Any]:
        try:
            return await dm.connect(
                udid=req.udid,
                transport=req.transport,
                auto_mount_ddi=req.auto_mount_ddi,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/device/disconnect")
    async def disconnect_device(req: DisconnectRequest) -> dict[str, Any]:
        await engine.stop(reset_location=False)
        return await dm.disconnect(clear_location=req.clear_location)

    @app.post("/api/device/reveal-dev-mode")
    async def reveal_dev_mode(req: DeviceActionRequest) -> dict[str, Any]:
        try:
            msg = await dm.reveal_developer_mode(udid=req.udid)
            return {"ok": True, "message": msg}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"发送开发者模式开关指令失败: {exc}") from exc

    @app.post("/api/device/pair-wifi")
    async def pair_wifi(req: DeviceActionRequest) -> dict[str, Any]:
        try:
            return await dm.pair_wifi(udid=req.udid)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/device/reset-location")
    async def reset_location() -> dict[str, Any]:
        try:
            state = await engine.reset_real_location()
            return {"ok": True, "simulation": state}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/device/mount-ddi")
    async def mount_ddi() -> dict[str, Any]:
        try:
            state = await dm.mount_ddi_now()
            return {"ok": True, "device": state}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # --- Campus Run simulation endpoints ---
    @app.post("/api/run/start")
    async def start_run(req: StartRunRequest) -> dict[str, Any]:
        if not dm.connected:
            raise HTTPException(status_code=400, detail="请先连接 iPhone 设备")
        try:
            pts = _to_point_list(req.points)
            store.save_last_state(
                {
                    "route_points": [[round(p[0], 7), round(p[1], 7)] for p in pts],
                    "closed_loop": req.closed_loop,
                    "speed_mps": req.speed_mps,
                    "laps": req.laps,
                    "target_distance_m": req.target_distance_m,
                    "mode_limit": req.mode_limit,
                }
            )
            cfg = RunConfig(
                points=pts,
                closed_loop=req.closed_loop,
                speed_mps=req.speed_mps,
                mode_limit=req.mode_limit,
                laps=req.laps,
                target_distance_m=req.target_distance_m,
                interval_s=req.interval_s,
                speed_jitter_pct=req.speed_jitter_pct,
                gps_drift_m=req.gps_drift_m,
                smooth_curves=req.smooth_curves,
            )
            state = await engine.start_run(cfg)
            return {"ok": True, "simulation": state}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"启动跑步失败: {exc}") from exc

    @app.post("/api/run/pause")
    async def pause_run() -> dict[str, Any]:
        return {"ok": True, "simulation": await engine.pause_run()}

    @app.post("/api/run/resume")
    async def resume_run() -> dict[str, Any]:
        return {"ok": True, "simulation": await engine.resume_run()}

    @app.post("/api/run/stop")
    async def stop_run(req: StopRunRequest) -> dict[str, Any]:
        try:
            state = await engine.stop(reset_location=req.reset_location)
            return {"ok": True, "simulation": state}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/run/params")
    async def update_run_params(req: UpdateRunParamsRequest) -> dict[str, Any]:
        try:
            state = await engine.update_run_params(
                speed_mps=req.speed_mps,
                speed_jitter_pct=req.speed_jitter_pct,
                gps_drift_m=req.gps_drift_m,
                interval_s=req.interval_s,
            )
            return {"ok": True, "simulation": state}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # --- Fixed-point Check-in endpoints ---
    @app.post("/api/pin/set")
    async def set_pin(req: SetPinRequest) -> dict[str, Any]:
        if not dm.connected:
            raise HTTPException(status_code=400, detail="请先连接 iPhone 设备")
        try:
            wgs_lat, wgs_lng = to_wgs84(req.lat, req.lng, req.crs)
            store.save_last_state({"last_pin": [round(wgs_lat, 7), round(wgs_lng, 7)]})
            cfg = PinConfig(
                lat=wgs_lat,
                lng=wgs_lng,
                micro_drift_m=req.micro_drift_m,
                heartbeat_s=req.heartbeat_s,
            )
            state = await engine.pin_location(cfg)
            return {"ok": True, "simulation": state}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/pin/nudge")
    async def nudge_pin(req: NudgePinRequest) -> dict[str, Any]:
        try:
            state = await engine.nudge_pin(req.north_m, req.east_m)
            return {"ok": True, "simulation": state}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/pin/stop")
    async def stop_pin(req: StopRunRequest) -> dict[str, Any]:
        try:
            state = await engine.stop(reset_location=req.reset_location)
            return {"ok": True, "simulation": state}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # --- Route Tools ---
    @app.post("/api/route/parse")
    async def parse_route(req: ParseRouteRequest) -> dict[str, Any]:
        try:
            pts = parse_route_text(req.text, input_crs=req.input_crs, lng_first=req.lng_first)
            gcj_pts = [list(wgs84_to_gcj02(lat, lng)) for lat, lng in pts]
            return {
                "ok": True,
                "points": [[round(lat, 7), round(lng, 7)] for lat, lng in pts],
                "gcj_points": [[round(lat, 7), round(lng, 7)] for lat, lng in gcj_pts],
                "count": len(pts),
                "length_open_m": round(route_length_meters(pts, closed=False), 1),
                "length_closed_m": round(route_length_meters(pts, closed=True), 1),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/route/export")
    async def export_route(req: ExportRouteRequest) -> PlainTextResponse:
        pts = _to_point_list(req.points)
        if req.format == "gpx":
            return PlainTextResponse(
                export_gpx(pts, name=req.name, speed_mps=req.speed_mps),
                media_type="application/gpx+xml; charset=utf-8",
            )
        return PlainTextResponse(
            export_iosfakerun_json(pts, output_crs=req.output_crs),
            media_type="application/json; charset=utf-8",
        )

    @app.post("/api/route/generate-track")
    async def generate_track(req: GenerateTrackRequest) -> dict[str, Any]:
        c_lat, c_lng = to_wgs84(req.center_lat, req.center_lng, req.center_crs)
        pts = generate_standard_track(
            center_lat=c_lat,
            center_lng=c_lng,
            straight_m=req.straight_m,
            radius_m=req.radius_m,
            rotation_deg=req.rotation_deg,
            points_per_lap=req.points_per_lap,
            counterclockwise=req.counterclockwise,
        )
        return {
            "ok": True,
            "points": [[round(lat, 7), round(lng, 7)] for lat, lng in pts],
            "length_closed_m": round(route_length_meters(pts, closed=True), 1),
        }

    @app.post("/api/route/smooth")
    async def smooth_route(req: SmoothRouteRequest) -> dict[str, Any]:
        pts = _to_point_list(req.points)
        smoothed = smooth_route_catmull_rom(
            pts,
            samples_per_segment=req.samples_per_segment,
            closed=req.closed,
        )
        return {
            "ok": True,
            "points": [[round(lat, 7), round(lng, 7)] for lat, lng in smoothed],
            "length_m": round(route_length_meters(smoothed, closed=req.closed), 1),
        }

    @app.get("/api/coords/convert")
    async def convert_coords(
        lat: float = Query(...),
        lng: float = Query(...),
        from_crs: str = Query("wgs84"),
    ) -> dict[str, Any]:
        wgs = to_wgs84(lat, lng, from_crs)
        gcj = from_wgs84(wgs[0], wgs[1], "gcj02")
        bd = from_wgs84(wgs[0], wgs[1], "bd09")
        return {
            "wgs84": [round(wgs[0], 7), round(wgs[1], 7)],
            "gcj02": [round(gcj[0], 7), round(gcj[1], 7)],
            "bd09": [round(bd[0], 7), round(bd[1], 7)],
        }

    # --- Saved routes & check-in pins CRUD ---
    @app.get("/api/routes")
    async def list_saved_routes() -> list[dict[str, Any]]:
        return store.list_routes()

    @app.post("/api/routes")
    async def save_route(req: SaveRouteRequest) -> dict[str, Any]:
        pts = _to_point_list(req.points)
        return store.save_route(name=req.name, points=pts, closed=req.closed, route_id=req.id)

    @app.delete("/api/routes/{route_id}")
    async def delete_route(route_id: str) -> dict[str, Any]:
        if not store.delete_route(route_id):
            raise HTTPException(status_code=404, detail="路线不存在")
        return {"ok": True}

    @app.get("/api/pins")
    async def list_saved_pins() -> list[dict[str, Any]]:
        return store.list_pins()

    @app.post("/api/pins")
    async def save_pin(req: SavePinRequest) -> dict[str, Any]:
        wgs_lat, wgs_lng = to_wgs84(req.lat, req.lng, req.crs)
        return store.save_pin(name=req.name, lat=wgs_lat, lng=wgs_lng, note=req.note, pin_id=req.id)

    @app.delete("/api/pins/{pin_id}")
    async def delete_pin(pin_id: str) -> dict[str, Any]:
        if not store.delete_pin(pin_id):
            raise HTTPException(status_code=404, detail="打卡点不存在")
        return {"ok": True}

    # --- Multi-source place search proxy ---
    @app.get("/api/geocode")
    async def geocode(q: str = Query(..., min_length=1)) -> list[dict[str, Any]]:
        try:
            return await search_places(q, limit=15)
        except Exception:
            return []

    # --- WebSocket ---
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await hub.connect(ws)
        try:
            await ws.send_text(
                json.dumps(
                    {
                        "type": "snapshot",
                        "data": {
                            "device": dm.get_state(),
                            "simulation": engine.get_state(),
                            "logs": dm.get_logs(),
                        },
                    },
                    ensure_ascii=False,
                )
            )
            while True:
                # Keep socket open; clients mainly receive server pushes.
                await ws.receive_text()
        except WebSocketDisconnect:
            await hub.disconnect(ws)
        except Exception:
            await hub.disconnect(ws)

    # --- Static frontend ---
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app
