import asyncio
import math

import pytest

from backend.coords import haversine_distance
from backend.route_utils import generate_standard_track
from backend.simulation_engine import PinConfig, RunConfig, SimulationEngine


class FakeDevice:
    def __init__(self) -> None:
        self.points: list[tuple[float, float]] = []
        self.cleared = 0

    async def set_location(self, lat: float, lng: float) -> None:
        self.points.append((lat, lng))

    async def clear_location(self) -> None:
        self.cleared += 1


def test_campus_run_completes_and_holds_then_resets():
    async def _scenario() -> None:
        dev = FakeDevice()
        engine = SimulationEngine(device=dev)
        # Small 40m track run at 20 m/s with 0.2s ticks -> finishes quickly (~2s)
        pts = generate_standard_track(39.9928, 116.3102, straight_m=10.0, radius_m=5.0, points_per_lap=16)
        await engine.start_run(
            RunConfig(
                points=pts,
                closed_loop=True,
                speed_mps=25.0,
                mode_limit="laps",
                laps=1,
                interval_s=0.2,
                speed_jitter_pct=0.0,
                gps_drift_m=0.0,
            )
        )
        # Wait until completion
        for _ in range(30):
            if engine.telemetry.mode == "completed_holding":
                break
            await asyncio.sleep(0.15)
        assert engine.telemetry.mode == "completed_holding"
        assert engine.telemetry.progress_pct == 100.0
        assert len(dev.points) >= 3

        await engine.reset_real_location()
        assert engine.telemetry.mode == "idle"
        assert dev.cleared == 1

    asyncio.run(_scenario())


def test_pin_nudge_and_clear():
    async def _scenario() -> None:
        dev = FakeDevice()
        engine = SimulationEngine(device=dev)
        await engine.pin_location(PinConfig(lat=39.9928, lng=116.3102, micro_drift_m=0.0, heartbeat_s=0.5))
        assert engine.telemetry.mode == "pinned"
        first = dev.points[-1]

        await engine.nudge_pin(north_m=20.0, east_m=0.0)
        second = dev.points[-1]
        moved_m = haversine_distance(first[0], first[1], second[0], second[1])
        assert math.isclose(moved_m, 20.0, rel_tol=0.05)

        await engine.reset_real_location()
        assert dev.cleared == 1
        assert engine.telemetry.mode == "idle"

    asyncio.run(_scenario())
