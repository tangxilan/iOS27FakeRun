"""China coordinate system conversions (WGS-84 / GCJ-02 / BD-09) and geodetic helpers."""

from __future__ import annotations

import math
from typing import Literal

CoordinateSystem = Literal["wgs84", "gcj02", "bd09"]

_PI = math.pi
_X_PI = _PI * 3000.0 / 180.0
_KRASOVSKY_A = 6378245.0
_KRASOVSKY_EE = 0.00669342162296594323
_EARTH_RADIUS_M = 6371008.8


def out_of_china(lat: float, lng: float) -> bool:
    """Return True if coordinates fall outside the China GCJ-02 offset bounding box."""
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * _PI) + 40.0 * math.sin(y / 3.0 * _PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * _PI) + 320.0 * math.sin(y * _PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * _PI) + 40.0 * math.sin(x / 3.0 * _PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * _PI) + 300.0 * math.sin(x / 30.0 * _PI)) * 2.0 / 3.0
    return ret


def _gcj02_delta(lat: float, lng: float) -> tuple[float, float]:
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * _PI
    magic = math.sin(rad_lat)
    magic = 1.0 - _KRASOVSKY_EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_KRASOVSKY_A * (1.0 - _KRASOVSKY_EE)) / (magic * sqrt_magic) * _PI)
    d_lng = (d_lng * 180.0) / (_KRASOVSKY_A / sqrt_magic * math.cos(rad_lat) * _PI)
    return d_lat, d_lng


def wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """Convert WGS-84 (GPS / iOS) coordinates to GCJ-02 (Amap / Tencent)."""
    if out_of_china(lat, lng):
        return lat, lng
    d_lat, d_lng = _gcj02_delta(lat, lng)
    return lat + d_lat, lng + d_lng


def gcj02_to_wgs84(lat: float, lng: float) -> tuple[float, float]:
    """Convert GCJ-02 coordinates to WGS-84 using exact iterative inversion (< 0.01m error)."""
    if out_of_china(lat, lng):
        return lat, lng

    # Fixed-point iteration converges to < 1e-10 deg within ~5 iterations.
    wgs_lat, wgs_lng = lat, lng
    for _ in range(10):
        g_lat, g_lng = wgs84_to_gcj02(wgs_lat, wgs_lng)
        d_lat = g_lat - lat
        d_lng = g_lng - lng
        wgs_lat -= d_lat
        wgs_lng -= d_lng
        if abs(d_lat) < 1e-10 and abs(d_lng) < 1e-10:
            break
    return wgs_lat, wgs_lng


def gcj02_to_bd09(lat: float, lng: float) -> tuple[float, float]:
    """Convert GCJ-02 coordinates to BD-09 (Baidu)."""
    z = math.hypot(lng, lat) + 0.00002 * math.sin(lat * _X_PI)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * _X_PI)
    return z * math.sin(theta) + 0.006, z * math.cos(theta) + 0.0065


def bd09_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """Convert BD-09 (Baidu) coordinates to GCJ-02."""
    x = lng - 0.0065
    y = lat - 0.006
    z = math.hypot(x, y) - 0.00002 * math.sin(y * _X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * _X_PI)
    return z * math.sin(theta), z * math.cos(theta)


def bd09_to_wgs84(lat: float, lng: float) -> tuple[float, float]:
    """Convert BD-09 (Baidu / legacy iOSFakeRun picker) coordinates to WGS-84."""
    gcj_lat, gcj_lng = bd09_to_gcj02(lat, lng)
    return gcj02_to_wgs84(gcj_lat, gcj_lng)


def wgs84_to_bd09(lat: float, lng: float) -> tuple[float, float]:
    """Convert WGS-84 coordinates to BD-09."""
    gcj_lat, gcj_lng = wgs84_to_gcj02(lat, lng)
    return gcj02_to_bd09(gcj_lat, gcj_lng)


def _normalize_crs(crs: str) -> CoordinateSystem:
    norm = crs.strip().lower().replace("-", "").replace("_", "")
    if norm in ("wgs84", "wgs", "gps"):
        return "wgs84"
    if norm in ("gcj02", "gcj", "amap", "gaode", "tencent"):
        return "gcj02"
    if norm in ("bd09", "bd", "baidu"):
        return "bd09"
    raise ValueError(f"不支持的坐标系: {crs}")


def to_wgs84(lat: float, lng: float, crs: str) -> tuple[float, float]:
    """Convert coordinates from the specified CRS ('wgs84', 'gcj02', 'bd09') to WGS-84."""
    norm = _normalize_crs(crs)
    if norm == "wgs84":
        return float(lat), float(lng)
    if norm == "gcj02":
        return gcj02_to_wgs84(float(lat), float(lng))
    return bd09_to_wgs84(float(lat), float(lng))


def from_wgs84(lat: float, lng: float, crs: str) -> tuple[float, float]:
    """Convert WGS-84 coordinates to the target CRS ('wgs84', 'gcj02', 'bd09')."""
    norm = _normalize_crs(crs)
    if norm == "wgs84":
        return float(lat), float(lng)
    if norm == "gcj02":
        return wgs84_to_gcj02(float(lat), float(lng))
    return wgs84_to_bd09(float(lat), float(lng))


def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Compute great-circle distance in meters between two WGS-84 points."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 2.0 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def offset_meters(lat: float, lng: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Offset a WGS-84 coordinate by (north_m, east_m) meters on a local ENU tangent plane."""
    d_lat = (north_m / _EARTH_RADIUS_M) * (180.0 / _PI)
    cos_lat = max(1e-8, math.cos(math.radians(lat)))
    d_lng = (east_m / (_EARTH_RADIUS_M * cos_lat)) * (180.0 / _PI)
    return lat + d_lat, lng + d_lng


def bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Compute forward azimuth bearing in degrees [0, 360) from point 1 to point 2."""
    if lat1 == lat2 and lng1 == lng2:
        return 0.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_lambda = math.radians(lng2 - lng1)
    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
