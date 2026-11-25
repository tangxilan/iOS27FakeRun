"""Simulation engine supporting Campus Run (模拟校园跑) and Fixed-Point Check-in (定点定位打卡)."""

from __future__ import annotations

import asyncio
import contextlib
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Protocol

from .coords import offset_meters, wgs84_to_gcj02
from .route_utils import (
    Point,
    build_polyline_distances,
    build_shuttle_route,
    compute_pace_multiplier,
    sample_point_at_distance,
    smooth_route_catmull_rom,
)


class LocationTarget(Protocol):
    """Abstract iOS location sink so simulation logic stays decoupled from pymobiledevice3."""

    async def set_location(self, lat: float, lng: float) -> None: ...
    async def clear_location(self) -> None: ...


StateCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class RunConfig:
    points: list[Point]
    closed_loop: bool = True
    route_mode: str = "loop"  # "loop" | "shuttle" | "open"
    speed_mps: float = 3.0
    mode_limit: str = "laps"  # "laps" or "distance"
    laps: int = 5
    target_distance_m: float = 2000.0
    interval_s: float = 1.0
    speed_jitter_pct: float = 8.0
    gps_drift_m: float = 1.5
    smooth_curves: bool = False
    pace_profile: str = "fatigue"  # "fatigue" | "steady" | "negative_split"
    fatigue_pct: float = 18.0
    end_sprint: bool = False
    shuttle_lane_offset_m: float = 1.8


@dataclass
class PinConfig:
    lat: float
    lng: float
    micro_drift_m: float = 0.0
    heartbeat_s: float = 2.0


@dataclass
class SimulationTelemetry:
    mode: str = "idle"  # idle | running | paused | completed_holding | pinned
    route_mode: str = "loop"
    pace_profile: str = "fatigue"
    phase_label: str = "待机"
    current_lat: Optional[float] = None
    current_lng: Optional[float] = None
    gcj_lat: Optional[float] = None
    gcj_lng: Optional[float] = None
    heading_deg: float = 0.0
    current_speed_mps: float = 0.0
    base_speed_mps: float = 3.0
    pace_sec_per_km: float = 0.0
    distance_m: float = 0.0
    target_total_m: float = 0.0
    lap_length_m: float = 0.0
    current_lap: int = 0
    total_laps: int = 0
    elapsed_s: float = 0.0
    eta_s: float = 0.0
    progress_pct: float = 0.0
    message: str = "空闲"
    lap_stats: list[dict[str, Any]] = field(default_factory=list)
    trail: list[list[float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "route_mode": self.route_mode,
            "pace_profile": self.pace_profile,
            "phase_label": self.phase_label,
            "current_lat": self.current_lat,
            "current_lng": self.current_lng,
            "gcj_lat": self.gcj_lat,
            "gcj_lng": self.gcj_lng,
            "heading_deg": round(self.heading_deg, 1),
            "current_speed_mps": round(self.current_speed_mps, 2),
            "base_speed_mps": round(self.base_speed_mps, 2),
            "pace_sec_per_km": round(self.pace_sec_per_km, 1),
            "distance_m": round(self.distance_m, 1),
            "target_total_m": round(self.target_total_m, 1),
            "lap_length_m": round(self.lap_length_m, 1),
            "current_lap": self.current_lap,
            "total_laps": self.total_laps,
            "elapsed_s": round(self.elapsed_s, 1),
            "eta_s": round(self.eta_s, 1),
            "progress_pct": round(self.progress_pct, 2),
            "message": self.message,
            "lap_stats": self.lap_stats,
            "trail": self.trail,
        }


class SimulationEngine:
    """Async simulation coordinator for Campus Run and Fixed-Point Check-in."""

    def __init__(
        self,
        device: LocationTarget,
        on_update: Optional[StateCallback] = None,
        on_log: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ) -> None:
        self._device = device
        self._on_update = on_update
        self._on_log = on_log
        self._op_lock = asyncio.Lock()
        self._task: Optional[asyncio.Task[None]] = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_requested = False

        self._run_cfg: Optional[RunConfig] = None
        self._pin_cfg: Optional[PinConfig] = None
        self._trail: deque[list[float]] = deque(maxlen=2000)
        self.telemetry = SimulationTelemetry()

        # Frenet-frame correlated GPS drift state (lateral/longitudinal + per-lap lane bias)
        self._lat_drift_m = 0.0
        self._lon_drift_m = 0.0
        self._lap_lane_bias_m = 0.0
        self._last_heading_deg: Optional[float] = None
        self._drift_phase = random.uniform(0.0, math.tau)
        # Isotropic drift state for stationary pin micro-drift
        self._drift_n = 0.0
        self._drift_e = 0.0
        # Slow Ornstein-Uhlenbeck speed wander
        self._speed_jitter_offset = 0.0

    def get_state(self) -> dict[str, Any]:
        return self.telemetry.to_dict()

    async def _emit_update(self) -> None:
        self.telemetry.trail = list(self._trail)
        if self._on_update is not None:
            with contextlib.suppress(Exception):
                await self._on_update(self.get_state())

    async def _log(self, level: str, msg: str) -> None:
        if self._on_log is not None:
            with contextlib.suppress(Exception):
                await self._on_log(level, msg)

    def _update_point_telemetry(self, lat: float, lng: float, record_trail: bool = True) -> None:
        g_lat, g_lng = wgs84_to_gcj02(lat, lng)
        self.telemetry.current_lat = round(lat, 7)
        self.telemetry.current_lng = round(lng, 7)
        self.telemetry.gcj_lat = round(g_lat, 7)
        self.telemetry.gcj_lng = round(g_lng, 7)
        if record_trail:
            if not self._trail or (
                abs(self._trail[-1][0] - lat) > 1e-7 or abs(self._trail[-1][1] - lng) > 1e-7
            ):
                self._trail.append([round(lat, 7), round(lng, 7)])

    def _reset_drift_state(self, max_drift_m: float) -> None:
        self._lat_drift_m = 0.0
        self._lon_drift_m = 0.0
        self._lap_lane_bias_m = (
            random.uniform(-0.5 * max_drift_m, 0.5 * max_drift_m) if max_drift_m > 0 else 0.0
        )
        self._last_heading_deg = None
        self._drift_phase = random.uniform(0.0, math.tau)
        self._drift_n = 0.0
        self._drift_e = 0.0
        self._speed_jitter_offset = 0.0

    def _apply_realistic_drift(
        self,
        lat: float,
        lng: float,
        heading_deg: float,
        max_drift_m: float,
        dt: float,
        step_m: float,
        elapsed_s: float,
    ) -> tuple[float, float]:
        """Apply smooth Frenet-frame lateral wander + lane bias without backwards jumps."""
        if max_drift_m <= 0.0:
            self._lat_drift_m = 0.0
            self._lon_drift_m = 0.0
            return lat, lng

        dt_eff = max(0.05, min(3.0, dt))
        # Ornstein-Uhlenbeck mean reversion toward current lap's lane bias (tau ~ 8s)
        alpha_lat = 1.0 - math.exp(-dt_eff / 8.0)
        noise_sigma = max_drift_m * 0.22 * math.sqrt(dt_eff)
        self._lat_drift_m += alpha_lat * (self._lap_lane_bias_m - self._lat_drift_m) + random.gauss(
            0.0, noise_sigma
        )

        # Gentle cornering sway when turning sharply on curves
        if self._last_heading_deg is not None:
            d_hdg = ((heading_deg - self._last_heading_deg + 540.0) % 360.0) - 180.0
            if abs(d_hdg) > 2.0:
                self._lat_drift_m += max(-0.25 * max_drift_m, min(0.25 * max_drift_m, -d_hdg * 0.012 * max_drift_m))
        self._last_heading_deg = heading_deg

        # Subtle low-frequency human stride/GPS sway
        sway = 0.18 * max_drift_m * math.sin(elapsed_s * 0.45 + self._drift_phase)
        lateral = max(-max_drift_m, min(max_drift_m, self._lat_drift_m + sway))
        self._lat_drift_m = max(-max_drift_m, min(max_drift_m, self._lat_drift_m))

        # Tightly bounded longitudinal jitter (< 35% of step_m so trajectory never jumps backwards)
        lon_cap = min(0.35 * max_drift_m, max(0.05, 0.35 * step_m))
        alpha_lon = 1.0 - math.exp(-dt_eff / 5.0)
        self._lon_drift_m = (1.0 - alpha_lon) * self._lon_drift_m + random.gauss(0.0, lon_cap * 0.3)
        longitudinal = max(-lon_cap, min(lon_cap, self._lon_drift_m))

        # Project Frenet (longitudinal, lateral-right) -> ENU (north, east)
        hdg_rad = math.radians(heading_deg)
        right_rad = hdg_rad + math.pi * 0.5
        north_m = longitudinal * math.cos(hdg_rad) + lateral * math.cos(right_rad)
        east_m = longitudinal * math.sin(hdg_rad) + lateral * math.sin(right_rad)
        return offset_meters(lat, lng, north_m, east_m)

    def _apply_pin_drift(self, lat: float, lng: float, max_drift_m: float) -> tuple[float, float]:
        if max_drift_m <= 0.0:
            self._drift_n = 0.0
            self._drift_e = 0.0
            return lat, lng
        step_sigma = max_drift_m * 0.35
        self._drift_n = 0.65 * self._drift_n + random.gauss(0.0, step_sigma)
        self._drift_e = 0.65 * self._drift_e + random.gauss(0.0, step_sigma)
        r = math.hypot(self._drift_n, self._drift_e)
        if r > max_drift_m:
            scale = max_drift_m / r
            self._drift_n *= scale
            self._drift_e *= scale
        return offset_meters(lat, lng, self._drift_n, self._drift_e)

    async def _cancel_task(self) -> None:
        self._stop_requested = True
        self._pause_event.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    @staticmethod
    def _resolve_route_mode(cfg: RunConfig) -> str:
        mode = (cfg.route_mode or "loop").strip().lower()
        if mode not in ("loop", "shuttle", "open"):
            mode = "loop"
        if mode == "loop" and not cfg.closed_loop:
            mode = "open"
        return mode

    async def start_run(self, cfg: RunConfig) -> dict[str, Any]:
        """Start Campus Run simulation along user-defined waypoints."""
        if len(cfg.points) < 2:
            raise ValueError("至少需要 2 个路径点才能开始模拟跑步")
        if cfg.speed_mps <= 0.1 or cfg.speed_mps > 30.0:
            raise ValueError("跑步速度必须在 0.1 ~ 30.0 m/s 之间")

        async with self._op_lock:
            await self._cancel_task()
            self._stop_requested = False
            self._pause_event.set()
            self._pin_cfg = None
            self._reset_drift_state(cfg.gps_drift_m)
            self._trail.clear()

            route_mode = self._resolve_route_mode(cfg)
            pts = list(cfg.points)

            if route_mode == "shuttle":
                if cfg.smooth_curves and len(pts) >= 3:
                    pts = smooth_route_catmull_rom(pts, samples_per_segment=6, closed=False)
                pts = build_shuttle_route(pts, lane_offset_m=cfg.shuttle_lane_offset_m)
            elif route_mode == "loop":
                if cfg.smooth_curves and len(pts) >= 3:
                    pts = smooth_route_catmull_rom(pts, samples_per_segment=8, closed=True)
                if pts[0][0] != pts[-1][0] or pts[0][1] != pts[-1][1]:
                    pts.append(pts[0])
            else:  # "open"
                if cfg.smooth_curves and len(pts) >= 3:
                    pts = smooth_route_catmull_rom(pts, samples_per_segment=8, closed=False)

            cum_dists = build_polyline_distances(pts)
            lap_len = cum_dists[-1]
            if lap_len <= 0.5:
                raise ValueError("路径总长度太短（<= 0.5 米），请检查坐标点")

            if route_mode == "open":
                total_laps = 1
                if cfg.mode_limit == "distance":
                    target_total_m = min(lap_len, max(10.0, float(cfg.target_distance_m)))
                else:
                    target_total_m = lap_len
            elif cfg.mode_limit == "distance":
                target_total_m = max(10.0, float(cfg.target_distance_m))
                total_laps = max(1, math.ceil(target_total_m / lap_len))
            else:
                total_laps = max(1, int(cfg.laps))
                target_total_m = lap_len * total_laps

            mult0, phase0 = compute_pace_multiplier(0.0, cfg.pace_profile, cfg.fatigue_pct, cfg.end_sprint)
            init_speed = max(0.2, cfg.speed_mps * mult0)

            self._run_cfg = cfg
            self.telemetry = SimulationTelemetry(
                mode="running",
                route_mode=route_mode,
                pace_profile=cfg.pace_profile,
                phase_label=phase0,
                base_speed_mps=cfg.speed_mps,
                current_speed_mps=init_speed,
                pace_sec_per_km=1000.0 / init_speed,
                distance_m=0.0,
                target_total_m=target_total_m,
                lap_length_m=lap_len,
                current_lap=1,
                total_laps=total_laps,
                elapsed_s=0.0,
                eta_s=target_total_m / max(0.2, cfg.speed_mps),
                progress_pct=0.0,
                message="正在模拟校园跑...",
                lap_stats=[],
            )

            # Immediately push initial start coordinate so device jumps to start line without delay.
            start_lat, start_lng, start_hdg = sample_point_at_distance(pts, cum_dists, 0.0)
            await self._device.set_location(start_lat, start_lng)
            self.telemetry.heading_deg = start_hdg
            self._last_heading_deg = start_hdg
            self._update_point_telemetry(start_lat, start_lng, record_trail=True)
            await self._emit_update()
            await self._log(
                "info",
                f"开始模拟校园跑({route_mode}/{cfg.pace_profile}): 单圈 {lap_len:.1f}m，目标 {target_total_m:.1f}m，基准速度 {cfg.speed_mps:.2f} m/s",
            )

            self._task = asyncio.create_task(
                self._run_loop(pts, cum_dists, lap_len, target_total_m),
                name="campus-run-loop",
            )
            return self.get_state()

    async def _run_loop(
        self,
        pts: list[Point],
        cum_dists: list[float],
        lap_len: float,
        target_total_m: float,
    ) -> None:
        cfg = self._run_cfg
        assert cfg is not None
        distance_m = 0.0
        elapsed_s = 0.0
        last_tick = time.monotonic()
        last_completed_lap = 0
        lap_start_time = 0.0
        lap_start_dist = 0.0

        try:
            while not self._stop_requested:
                interval = max(0.2, min(5.0, float(cfg.interval_s)))
                await asyncio.sleep(interval)

                if not self._pause_event.is_set():
                    await self._pause_event.wait()
                    last_tick = time.monotonic()
                    continue

                if self._stop_requested:
                    break

                now = time.monotonic()
                dt = max(0.0, min(now - last_tick, interval * 3.0))
                last_tick = now

                progress = min(1.0, distance_m / max(1e-6, target_total_m))
                pace_mult, phase_label = compute_pace_multiplier(
                    progress,
                    cfg.pace_profile,
                    cfg.fatigue_pct,
                    cfg.end_sprint,
                )

                # Slow Ornstein-Uhlenbeck stride variation (tau ~ 14s)
                jitter_frac = max(0.0, min(0.4, cfg.speed_jitter_pct / 100.0))
                if jitter_frac > 0.0:
                    alpha_spd = 1.0 - math.exp(-max(0.05, dt) / 14.0)
                    self._speed_jitter_offset += -alpha_spd * self._speed_jitter_offset + random.gauss(
                        0.0, jitter_frac * 0.45 * math.sqrt(max(0.05, dt))
                    )
                    self._speed_jitter_offset = max(-jitter_frac, min(jitter_frac, self._speed_jitter_offset))
                else:
                    self._speed_jitter_offset = 0.0

                eff_speed = max(0.2, cfg.speed_mps * pace_mult * (1.0 + self._speed_jitter_offset))
                step_m = eff_speed * dt
                distance_m = min(target_total_m, distance_m + step_m)
                elapsed_s += dt

                # Detect lap completion and shift lane bias naturally for the next lap.
                completed_laps_now = int(distance_m // lap_len) if distance_m < target_total_m else self.telemetry.total_laps
                if completed_laps_now > last_completed_lap:
                    for lap_idx in range(last_completed_lap + 1, completed_laps_now + 1):
                        lap_end_dist = min(target_total_m, lap_idx * lap_len)
                        d_dist = max(0.1, lap_end_dist - lap_start_dist)
                        d_time = max(0.05, elapsed_s - lap_start_time)
                        avg_spd = d_dist / d_time
                        self.telemetry.lap_stats.append(
                            {
                                "lap": lap_idx,
                                "distance_m": round(d_dist, 1),
                                "duration_s": round(d_time, 1),
                                "avg_speed_mps": round(avg_spd, 2),
                                "pace_sec_per_km": round(1000.0 / max(0.2, avg_spd), 1),
                            }
                        )
                        lap_start_dist = lap_end_dist
                        lap_start_time = elapsed_s
                    last_completed_lap = completed_laps_now
                    if cfg.gps_drift_m > 0:
                        self._lap_lane_bias_m = random.uniform(-0.55 * cfg.gps_drift_m, 0.55 * cfg.gps_drift_m)

                if distance_m >= target_total_m:
                    lap_pos = target_total_m % lap_len
                    if lap_pos == 0.0:
                        lap_pos = lap_len
                    current_lap = self.telemetry.total_laps
                else:
                    lap_pos = distance_m % lap_len
                    current_lap = min(self.telemetry.total_laps, int(distance_m // lap_len) + 1)

                base_lat, base_lng, hdg = sample_point_at_distance(pts, cum_dists, lap_pos)
                sim_lat, sim_lng = self._apply_realistic_drift(
                    base_lat,
                    base_lng,
                    hdg,
                    cfg.gps_drift_m,
                    dt,
                    step_m,
                    elapsed_s,
                )
                await self._device.set_location(sim_lat, sim_lng)

                self.telemetry.phase_label = phase_label
                self.telemetry.heading_deg = hdg
                self.telemetry.base_speed_mps = cfg.speed_mps
                self.telemetry.current_speed_mps = eff_speed
                self.telemetry.pace_sec_per_km = 1000.0 / eff_speed
                self.telemetry.distance_m = distance_m
                self.telemetry.current_lap = current_lap
                self.telemetry.elapsed_s = elapsed_s
                remaining = max(0.0, target_total_m - distance_m)
                self.telemetry.eta_s = remaining / max(0.2, eff_speed)
                self.telemetry.progress_pct = min(100.0, (distance_m / target_total_m) * 100.0)
                self._update_point_telemetry(sim_lat, sim_lng, record_trail=True)
                await self._emit_update()

                if distance_m >= target_total_m:
                    self.telemetry.mode = "completed_holding"
                    self.telemetry.phase_label = "已完成"
                    self.telemetry.current_speed_mps = 0.0
                    self.telemetry.eta_s = 0.0
                    self.telemetry.progress_pct = 100.0
                    self.telemetry.message = "跑步完成，已保持终点定位（请在手机提交记录后点击【恢复真实定位】）"
                    await self._emit_update()
                    await self._log("info", "校园跑模拟已完成，正在保持终点定位防止回弹")
                    await self._hold_loop(base_lat, base_lng, drift_m=0.0, heartbeat_s=2.0)
                    break

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.telemetry.mode = "idle"
            self.telemetry.message = f"模拟中断: {exc}"
            await self._log("error", f"模拟跑步异常中断: {exc}")
            await self._emit_update()

    async def _hold_loop(self, lat: float, lng: float, drift_m: float, heartbeat_s: float) -> None:
        while not self._stop_requested:
            await asyncio.sleep(max(0.5, min(10.0, heartbeat_s)))
            if self._stop_requested:
                break
            target_lat, target_lng = (
                self._apply_pin_drift(lat, lng, drift_m) if drift_m > 0 else (lat, lng)
            )
            await self._device.set_location(target_lat, target_lng)
            self._update_point_telemetry(target_lat, target_lng, record_trail=False)
            await self._emit_update()

    async def pause_run(self) -> dict[str, Any]:
        if self.telemetry.mode == "running":
            self._pause_event.clear()
            self.telemetry.mode = "paused"
            self.telemetry.message = "已暂停跑步（保持当前位置）"
            await self._log("info", "已暂停模拟跑步")
            await self._emit_update()
        return self.get_state()

    async def resume_run(self) -> dict[str, Any]:
        if self.telemetry.mode == "paused":
            self.telemetry.mode = "running"
            self.telemetry.message = "正在模拟校园跑..."
            self._pause_event.set()
            await self._log("info", "继续模拟跑步")
            await self._emit_update()
        return self.get_state()

    async def update_run_params(
        self,
        speed_mps: Optional[float] = None,
        speed_jitter_pct: Optional[float] = None,
        gps_drift_m: Optional[float] = None,
        interval_s: Optional[float] = None,
        pace_profile: Optional[str] = None,
        fatigue_pct: Optional[float] = None,
        end_sprint: Optional[bool] = None,
    ) -> dict[str, Any]:
        if self._run_cfg is None:
            raise ValueError("当前没有正在进行的跑步任务")
        if speed_mps is not None:
            if speed_mps <= 0.1 or speed_mps > 30.0:
                raise ValueError("跑步速度必须在 0.1 ~ 30.0 m/s 之间")
            self._run_cfg.speed_mps = float(speed_mps)
            self.telemetry.base_speed_mps = float(speed_mps)
        if speed_jitter_pct is not None:
            self._run_cfg.speed_jitter_pct = max(0.0, min(40.0, float(speed_jitter_pct)))
        if gps_drift_m is not None:
            self._run_cfg.gps_drift_m = max(0.0, min(10.0, float(gps_drift_m)))
        if interval_s is not None:
            self._run_cfg.interval_s = max(0.2, min(5.0, float(interval_s)))
        if pace_profile is not None and pace_profile in ("fatigue", "steady", "negative_split"):
            self._run_cfg.pace_profile = pace_profile
            self.telemetry.pace_profile = pace_profile
        if fatigue_pct is not None:
            self._run_cfg.fatigue_pct = max(0.0, min(40.0, float(fatigue_pct)))
        if end_sprint is not None:
            self._run_cfg.end_sprint = bool(end_sprint)
        await self._emit_update()
        return self.get_state()

    async def stop(self, reset_location: bool = False) -> dict[str, Any]:
        async with self._op_lock:
            await self._cancel_task()
            self._run_cfg = None
            self._pin_cfg = None
            if reset_location:
                await self._device.clear_location()
                self._trail.clear()
                self.telemetry = SimulationTelemetry(mode="idle", message="已停止模拟并恢复真实定位")
                await self._log("info", "已停止模拟并恢复真实 GPS 定位")
            else:
                self.telemetry.mode = "idle"
                self.telemetry.current_speed_mps = 0.0
                self.telemetry.message = "已停止模拟（未重置定位，如需恢复请点击【恢复真实定位】）"
                await self._log("info", "已停止模拟")
            await self._emit_update()
            return self.get_state()

    async def pin_location(self, cfg: PinConfig) -> dict[str, Any]:
        """Lock iOS location to a fixed coordinate for check-in (定点定位打卡)."""
        if not (-90.0 <= cfg.lat <= 90.0 and -180.0 <= cfg.lng <= 180.0):
            raise ValueError("经纬度超出合法范围")

        async with self._op_lock:
            await self._cancel_task()
            self._stop_requested = False
            self._pause_event.set()
            self._run_cfg = None
            self._pin_cfg = cfg
            self._reset_drift_state(cfg.micro_drift_m)
            self._trail.clear()

            await self._device.set_location(cfg.lat, cfg.lng)
            self.telemetry = SimulationTelemetry(
                mode="pinned",
                phase_label="定点锁定",
                message=f"已锁定打卡定位 ({cfg.lat:.6f}, {cfg.lng:.6f})",
            )
            self._update_point_telemetry(cfg.lat, cfg.lng, record_trail=False)
            await self._emit_update()
            await self._log("info", f"锁定定点打卡位置: WGS84 ({cfg.lat:.6f}, {cfg.lng:.6f})")

            self._task = asyncio.create_task(
                self._pin_loop(cfg),
                name="fixed-pin-heartbeat",
            )
            return self.get_state()

    async def _pin_loop(self, cfg: PinConfig) -> None:
        try:
            await self._hold_loop(cfg.lat, cfg.lng, drift_m=cfg.micro_drift_m, heartbeat_s=cfg.heartbeat_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.telemetry.mode = "idle"
            self.telemetry.message = f"定点保持中断: {exc}"
            await self._log("error", f"定点打卡保持中断: {exc}")
            await self._emit_update()

    async def nudge_pin(self, north_m: float, east_m: float) -> dict[str, Any]:
        """Shift the currently pinned coordinate by (north_m, east_m) meters."""
        if self._pin_cfg is None or self.telemetry.mode != "pinned":
            raise ValueError("当前未处于定点锁定状态")
        new_lat, new_lng = offset_meters(self._pin_cfg.lat, self._pin_cfg.lng, north_m, east_m)
        updated = PinConfig(
            lat=new_lat,
            lng=new_lng,
            micro_drift_m=self._pin_cfg.micro_drift_m,
            heartbeat_s=self._pin_cfg.heartbeat_s,
        )
        return await self.pin_location(updated)

    async def reset_real_location(self) -> dict[str, Any]:
        """Stop any simulation/pin and restore the iPhone's real hardware GPS."""
        return await self.stop(reset_location=True)
