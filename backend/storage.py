"""Atomic JSON persistence for saved routes, check-in pins, and last UI state."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class LocalStorage:
    """Simple JSON file store under `<project>/data/`."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.routes_file = self.data_dir / "routes.json"
        self.pins_file = self.data_dir / "pins.json"
        self.state_file = self.data_dir / "last_state.json"

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # --- Saved routes ---
    def list_routes(self) -> list[dict[str, Any]]:
        data = self._read_json(self.routes_file, [])
        return data if isinstance(data, list) else []

    def save_route(
        self,
        name: str,
        points: list[list[float]] | list[tuple[float, float]],
        closed: bool = True,
        route_id: str | None = None,
    ) -> dict[str, Any]:
        routes = self.list_routes()
        clean_pts = [[round(float(lat), 7), round(float(lng), 7)] for lat, lng in points]
        now = _now_iso()
        if route_id:
            for item in routes:
                if item.get("id") == route_id:
                    item["name"] = name.strip() or "未命名路线"
                    item["points"] = clean_pts
                    item["closed"] = bool(closed)
                    item["updated_at"] = now
                    self._write_json(self.routes_file, routes)
                    return item

        entry = {
            "id": route_id or uuid.uuid4().hex[:10],
            "name": name.strip() or f"校园路线 {len(routes) + 1}",
            "points": clean_pts,
            "closed": bool(closed),
            "updated_at": now,
        }
        routes.insert(0, entry)
        self._write_json(self.routes_file, routes)
        return entry

    def delete_route(self, route_id: str) -> bool:
        routes = self.list_routes()
        filtered = [r for r in routes if r.get("id") != route_id]
        if len(filtered) == len(routes):
            return False
        self._write_json(self.routes_file, filtered)
        return True

    # --- Saved check-in pins ---
    def list_pins(self) -> list[dict[str, Any]]:
        data = self._read_json(self.pins_file, [])
        return data if isinstance(data, list) else []

    def save_pin(
        self,
        name: str,
        lat: float,
        lng: float,
        note: str = "",
        pin_id: str | None = None,
    ) -> dict[str, Any]:
        pins = self.list_pins()
        now = _now_iso()
        clean_lat = round(float(lat), 7)
        clean_lng = round(float(lng), 7)
        if pin_id:
            for item in pins:
                if item.get("id") == pin_id:
                    item["name"] = name.strip() or "未命名打卡点"
                    item["note"] = note.strip()
                    item["lat"] = clean_lat
                    item["lng"] = clean_lng
                    item["updated_at"] = now
                    self._write_json(self.pins_file, pins)
                    return item

        entry = {
            "id": pin_id or uuid.uuid4().hex[:10],
            "name": name.strip() or f"打卡点 {len(pins) + 1}",
            "note": note.strip(),
            "lat": clean_lat,
            "lng": clean_lng,
            "created_at": now,
        }
        pins.insert(0, entry)
        self._write_json(self.pins_file, pins)
        return entry

    def delete_pin(self, pin_id: str) -> bool:
        pins = self.list_pins()
        filtered = [p for p in pins if p.get("id") != pin_id]
        if len(filtered) == len(pins):
            return False
        self._write_json(self.pins_file, filtered)
        return True

    # --- Last state ---
    def get_last_state(self) -> dict[str, Any]:
        data = self._read_json(self.state_file, {})
        return data if isinstance(data, dict) else {}

    def save_last_state(self, state: dict[str, Any]) -> None:
        current = self.get_last_state()
        current.update(state)
        self._write_json(self.state_file, current)
