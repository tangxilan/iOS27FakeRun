"""Route parsing, export, smoothing, stadium track generation, and arc-length sampling."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from .coords import bearing_deg, from_wgs84, haversine_distance, offset_meters, to_wgs84

Point = tuple[float, float]


def _validate_point(lat: float, lng: float) -> Point:
    if not (math.isfinite(lat) and math.isfinite(lng)):
        raise ValueError(f"经纬度包含非有限数值: ({lat}, {lng})")
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        raise ValueError(f"经纬度超出合法范围: lat={lat}, lng={lng}")
    return float(lat), float(lng)


def _extract_json_point(item: object, lng_first: bool) -> Point:
    if isinstance(item, dict):
        lat_val = None
        lng_val = None
        for k in ("lat", "latitude", "Lat", "Latitude"):
            if k in item:
                lat_val = item[k]
                break
        for k in ("lng", "lon", "long", "longitude", "Lng", "Lon", "Longitude"):
            if k in item:
                lng_val = item[k]
                break
        if lat_val is None or lng_val is None:
            raise ValueError(f"JSON 对象缺少 lat/lng 字段: {item}")
        return _validate_point(float(lat_val), float(lng_val))
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        a, b = float(item[0]), float(item[1])
        return _validate_point(b, a) if lng_first else _validate_point(a, b)
    raise ValueError(f"无法识别的 JSON 坐标项: {item}")


def _parse_gpx(text: str) -> list[Point]:
    root = ET.fromstring(text.strip())
    points: list[Point] = []
    # Match trkpt, rtept, or wpt regardless of XML namespace prefix.
    for tag_suffix in ("trkpt", "rtept", "wpt"):
        found: list[Point] = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1].lower()
            if tag == tag_suffix:
                lat_attr = elem.attrib.get("lat")
                lon_attr = elem.attrib.get("lon") or elem.attrib.get("lng")
                if lat_attr is not None and lon_attr is not None:
                    found.append(_validate_point(float(lat_attr), float(lon_attr)))
        if found:
            points.extend(found)
            break
    if not points:
        raise ValueError("GPX 文件中未找到 <trkpt> / <rtept> / <wpt> 坐标点")
    return points


def _parse_plain_lines(text: str, lng_first: bool) -> list[Point]:
    points: list[Point] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        # Normalize delimiters: comma, semicolon, tab, or whitespace
        cleaned = line.replace(";", ",").replace("\t", ",").replace("，", ",")
        if "," in cleaned:
            parts = [p.strip() for p in cleaned.split(",") if p.strip()]
        else:
            parts = [p.strip() for p in cleaned.split() if p.strip()]
        if len(parts) < 2:
            raise ValueError(f"无法解析坐标行: {raw_line}")
        a, b = float(parts[0]), float(parts[1])
        points.append(_validate_point(b, a) if lng_first else _validate_point(a, b))
    if not points:
        raise ValueError("未在文本中找到有效坐标")
    return points


def parse_route_text(text: str, input_crs: str = "wgs84", lng_first: bool = False) -> list[Point]:
    """Parse iOSFakeRun JSON, generic JSON, GPX XML, or plain text coordinates into canonical WGS-84 points."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("输入内容为空")

    raw_points: list[Point]
    if stripped.startswith("<"):
        raw_points = _parse_gpx(stripped)
    elif stripped.startswith("{") or stripped.startswith("["):
        # Compatibility with Mythologyli/iOSFakeRun: bare comma-separated `{lat,lng},{lat,lng}` without outer []
        candidate = stripped if stripped.startswith("[") else f"[{stripped.rstrip(',')}]"
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 格式错误: {exc}") from exc
        if isinstance(data, dict):
            if "points" in data and isinstance(data["points"], list):
                data = data["points"]
            elif "lat" in data or "latitude" in data:
                data = [data]
            else:
                raise ValueError("JSON 对象未包含坐标列表")
        if not isinstance(data, list) or not data:
            raise ValueError("JSON 中没有坐标点")
        raw_points = [_extract_json_point(item, lng_first=lng_first) for item in data]
    else:
        raw_points = _parse_plain_lines(stripped, lng_first=lng_first)

    return [to_wgs84(lat, lng, input_crs) for lat, lng in raw_points]


def export_iosfakerun_json(points: Sequence[Point], output_crs: str = "bd09") -> str:
    """Export waypoints as Mythologyli/iOSFakeRun-compatible JSON array."""
    payload = []
    for lat, lng in points:
        out_lat, out_lng = from_wgs84(lat, lng, output_crs)
        payload.append({"lat": round(out_lat, 7), "lng": round(out_lng, 7)})
    return json.dumps(payload, ensure_ascii=False)


def export_gpx(points: Sequence[Point], name: str = "CampusRun", speed_mps: float = 3.0) -> str:
    """Export WGS-84 waypoints as a standard GPX 1.1 track."""
    safe_name = (
        name.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    now = datetime.now(timezone.utc)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="iOS27FakeRun" xmlns="http://www.topografix.com/GPX/1/1">',
        "  <trk>",
        f"    <name>{safe_name}</name>",
        "    <trkseg>",
    ]
    t = now
    prev: Point | None = None
    spd = max(0.5, speed_mps)
    for lat, lng in points:
        if prev is not None:
            dist = haversine_distance(prev[0], prev[1], lat, lng)
            t += timedelta(seconds=dist / spd)
        lines.append(
            f'      <trkpt lat="{lat:.7f}" lon="{lng:.7f}"><time>{t.strftime("%Y-%m-%dT%H:%M:%SZ")}</time></trkpt>'
        )
        prev = (lat, lng)
    lines.extend(["    </trkseg>", "  </trk>", "</gpx>"])
    return "\n".join(lines)


def build_polyline_distances(points: Sequence[Point]) -> list[float]:
    """Return cumulative distance in meters at each vertex starting at 0.0."""
    if not points:
        return []
    cum = [0.0]
    total = 0.0
    for i in range(1, len(points)):
        total += haversine_distance(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])
        cum.append(total)
    return cum


def route_length_meters(points: Sequence[Point], closed: bool = False) -> float:
    """Compute total polyline length in meters, optionally closing back to points[0]."""
    if len(points) < 2:
        return 0.0
    total = build_polyline_distances(points)[-1]
    if closed and (points[0][0] != points[-1][0] or points[0][1] != points[-1][1]):
        total += haversine_distance(points[-1][0], points[-1][1], points[0][0], points[0][1])
    return total


def _catmull_rom_segment(
    p0: Point, p1: Point, p2: Point, p3: Point, samples: int, alpha: float = 0.5
) -> list[Point]:
    def dist_pow(a: Point, b: Point) -> float:
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        return max(d, 1e-12) ** alpha

    t0 = 0.0
    t1 = t0 + dist_pow(p0, p1)
    t2 = t1 + dist_pow(p1, p2)
    t3 = t2 + dist_pow(p2, p3)

    result: list[Point] = []
    for i in range(samples):
        t = t1 + (t2 - t1) * (i / samples)
        a1_lat = (t1 - t) / (t1 - t0) * p0[0] + (t - t0) / (t1 - t0) * p1[0]
        a1_lng = (t1 - t) / (t1 - t0) * p0[1] + (t - t0) / (t1 - t0) * p1[1]
        a2_lat = (t2 - t) / (t2 - t1) * p1[0] + (t - t1) / (t2 - t1) * p2[0]
        a2_lng = (t2 - t) / (t2 - t1) * p1[1] + (t - t1) / (t2 - t1) * p2[1]
        a3_lat = (t3 - t) / (t3 - t2) * p2[0] + (t - t2) / (t3 - t2) * p3[0]
        a3_lng = (t3 - t) / (t3 - t2) * p2[1] + (t - t2) / (t3 - t2) * p3[1]

        b1_lat = (t2 - t) / (t2 - t0) * a1_lat + (t - t0) / (t2 - t0) * a2_lat
        b1_lng = (t2 - t) / (t2 - t0) * a1_lng + (t - t0) / (t2 - t0) * a2_lng
        b2_lat = (t3 - t) / (t3 - t1) * a2_lat + (t - t1) / (t3 - t1) * a3_lat
        b2_lng = (t3 - t) / (t3 - t1) * a2_lng + (t - t1) / (t3 - t1) * a3_lng

        c_lat = (t2 - t) / (t2 - t1) * b1_lat + (t - t1) / (t2 - t1) * b2_lat
        c_lng = (t2 - t) / (t2 - t1) * b1_lng + (t - t1) / (t2 - t1) * b2_lng
        result.append((c_lat, c_lng))
    return result


def smooth_route_catmull_rom(
    points: Sequence[Point], samples_per_segment: int = 8, closed: bool = False
) -> list[Point]:
    """Apply centripetal Catmull-Rom spline smoothing to produce natural curved bends."""
    pts = list(points)
    if len(pts) < 3 or samples_per_segment < 2:
        return pts

    # Deduplicate consecutive identical points
    deduped: list[Point] = [pts[0]]
    for pt in pts[1:]:
        if haversine_distance(deduped[-1][0], deduped[-1][1], pt[0], pt[1]) > 0.05:
            deduped.append(pt)
    if closed and len(deduped) > 2 and haversine_distance(deduped[0][0], deduped[0][1], deduped[-1][0], deduped[-1][1]) < 0.05:
        deduped.pop()
    if len(deduped) < 3:
        return deduped

    out: list[Point] = []
    n = len(deduped)
    if closed:
        for i in range(n):
            p0 = deduped[(i - 1) % n]
            p1 = deduped[i]
            p2 = deduped[(i + 1) % n]
            p3 = deduped[(i + 2) % n]
            out.extend(_catmull_rom_segment(p0, p1, p2, p3, samples_per_segment))
    else:
        for i in range(n - 1):
            p0 = deduped[i - 1] if i > 0 else (2 * deduped[0][0] - deduped[1][0], 2 * deduped[0][1] - deduped[1][1])
            p1 = deduped[i]
            p2 = deduped[i + 1]
            p3 = deduped[i + 2] if i + 2 < n else (2 * deduped[-1][0] - deduped[-2][0], 2 * deduped[-1][1] - deduped[-2][1])
            out.extend(_catmull_rom_segment(p0, p1, p2, p3, samples_per_segment))
        out.append(deduped[-1])
    return out


def generate_standard_track(
    center_lat: float,
    center_lng: float,
    straight_m: float = 84.39,
    radius_m: float = 36.8,
    rotation_deg: float = 0.0,
    points_per_lap: int = 60,
    counterclockwise: bool = True,
) -> list[Point]:
    """Generate a standard stadium oval track (default perimeter ~400m) centered on (center_lat, center_lng).

    When rotation_deg == 0, the straights run North-South; positive rotation rotates clockwise.
    """
    straight_m = max(10.0, float(straight_m))
    radius_m = max(5.0, float(radius_m))
    points_per_lap = max(16, min(360, int(points_per_lap)))

    half_l = straight_m / 2.0
    arc_len = math.pi * radius_m
    perimeter = 2.0 * straight_m + 2.0 * arc_len

    # Parameterize local (east_0, north_0) counterclockwise starting at east straight midpoint -> north
    local_pts: list[tuple[float, float]] = []
    for idx in range(points_per_lap):
        s = (idx / points_per_lap) * perimeter
        if s < straight_m:
            # East straight: south -> north
            east0 = radius_m
            north0 = -half_l + s
        elif s < straight_m + arc_len:
            # North semicircle: 0 -> pi
            theta = (s - straight_m) / radius_m
            east0 = radius_m * math.cos(theta)
            north0 = half_l + radius_m * math.sin(theta)
        elif s < 2.0 * straight_m + arc_len:
            # West straight: north -> south
            d = s - (straight_m + arc_len)
            east0 = -radius_m
            north0 = half_l - d
        else:
            # South semicircle: pi -> 2*pi
            theta = math.pi + (s - (2.0 * straight_m + arc_len)) / radius_m
            east0 = radius_m * math.cos(theta)
            north0 = -half_l + radius_m * math.sin(theta)
        local_pts.append((north0, east0))

    if not counterclockwise:
        local_pts = [local_pts[0], *reversed(local_pts[1:])]

    # Clockwise rotation by rotation_deg around center
    rad = math.radians(rotation_deg)
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)

    wgs_pts: list[Point] = []
    for n0, e0 in local_pts:
        # Clockwise rotation in (east, north):
        # e' = e*cos + n*sin, n' = -e*sin + n*cos
        east = e0 * cos_r + n0 * sin_r
        north = -e0 * sin_r + n0 * cos_r
        wgs_pts.append(offset_meters(center_lat, center_lng, north, east))
    return wgs_pts


def sample_point_at_distance(
    points: Sequence[Point], cum_dists: Sequence[float], dist_m: float
) -> tuple[float, float, float]:
    """Sample interpolated (lat, lng, heading_deg) at dist_m along polyline."""
    if not points:
        raise ValueError("路径不能为空")
    if len(points) == 1 or not cum_dists or cum_dists[-1] <= 0.0:
        return points[0][0], points[0][1], 0.0

    total = cum_dists[-1]
    d = max(0.0, min(dist_m, total))
    if d >= total:
        hdg = bearing_deg(points[-2][0], points[-2][1], points[-1][0], points[-1][1])
        return points[-1][0], points[-1][1], hdg

    # Binary search for segment containing d
    lo, hi = 0, len(cum_dists) - 2
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if cum_dists[mid] <= d:
            lo = mid
        else:
            hi = mid - 1

    seg_start = cum_dists[lo]
    seg_end = cum_dists[lo + 1]
    seg_len = seg_end - seg_start
    p1 = points[lo]
    p2 = points[lo + 1]
    hdg = bearing_deg(p1[0], p1[1], p2[0], p2[1])
    if seg_len <= 1e-9:
        return p1[0], p1[1], hdg

    t = (d - seg_start) / seg_len
    lat = p1[0] + (p2[0] - p1[0]) * t
    lng = p1[1] + (p2[1] - p1[1]) * t
    return lat, lng, hdg


def generate_six_point_track(
    center_lat: float,
    center_lng: float,
    length_m: float = 120.0,
    width_m: float = 60.0,
    rotation_deg: float = 0.0,
    counterclockwise: bool = True,
) -> list[Point]:
    """Generate 6 draggable anchor points forming an oval/hex track of arbitrary dimensions."""
    half_l = max(10.0, float(length_m) / 2.0)
    half_w = max(5.0, float(width_m) / 2.0)
    # 4 straight-side corners + 2 curved-end apexes (6 points total).
    straight_half = half_l * 0.55
    local_pts: list[tuple[float, float]] = [
        (-straight_half, half_w),   # East straight south corner
        (straight_half, half_w),    # East straight north corner
        (half_l, 0.0),              # North apex
        (straight_half, -half_w),   # West straight north corner
        (-straight_half, -half_w),  # West straight south corner
        (-half_l, 0.0),             # South apex
    ]
    if not counterclockwise:
        local_pts = [local_pts[0], *reversed(local_pts[1:])]

    rad = math.radians(rotation_deg)
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)

    out: list[Point] = []
    for n0, e0 in local_pts:
        east = e0 * cos_r + n0 * sin_r
        north = -e0 * sin_r + n0 * cos_r
        out.append(offset_meters(center_lat, center_lng, north, east))
    return out


def build_shuttle_route(points: Sequence[Point], lane_offset_m: float = 1.8) -> list[Point]:
    """Build a closed out-and-back road shuttle loop with lateral lane separation and U-turn arcs."""
    pts = list(points)
    if len(pts) < 2:
        raise ValueError("往返折返路线至少需要 2 个路径点")

    lane = max(0.0, min(8.0, float(lane_offset_m)))

    def _vertex_right_normal(idx: int) -> tuple[float, float]:
        if idx == 0:
            hdg = bearing_deg(pts[0][0], pts[0][1], pts[1][0], pts[1][1])
        elif idx == len(pts) - 1:
            hdg = bearing_deg(pts[-2][0], pts[-2][1], pts[-1][0], pts[-1][1])
        else:
            h1 = bearing_deg(pts[idx - 1][0], pts[idx - 1][1], pts[idx][0], pts[idx][1])
            h2 = bearing_deg(pts[idx][0], pts[idx][1], pts[idx + 1][0], pts[idx + 1][1])
            # Circular mean of headings
            sx = math.sin(math.radians(h1)) + math.sin(math.radians(h2))
            cx = math.cos(math.radians(h1)) + math.cos(math.radians(h2))
            hdg = math.degrees(math.atan2(sx, cx))
        right_rad = math.radians(hdg + 90.0)
        return math.cos(right_rad), math.sin(right_rad)

    if lane <= 0.05:
        # Pure out-and-back back to start
        return [*pts, *reversed(pts[:-1])]

    half_lane = lane * 0.5
    outbound: list[Point] = []
    inbound: list[Point] = []
    for i, (lat, lng) in enumerate(pts):
        rn, re = _vertex_right_normal(i)
        outbound.append(offset_meters(lat, lng, rn * half_lane, re * half_lane))
        inbound.append(offset_meters(lat, lng, -rn * half_lane, -re * half_lane))

    # Add smooth U-turn midpoint apex at far end & start end
    end_hdg = math.radians(bearing_deg(pts[-2][0], pts[-2][1], pts[-1][0], pts[-1][1]))
    far_apex = offset_meters(
        pts[-1][0], pts[-1][1], math.cos(end_hdg) * half_lane, math.sin(end_hdg) * half_lane
    )
    start_hdg = math.radians(bearing_deg(pts[0][0], pts[0][1], pts[1][0], pts[1][1]))
    near_apex = offset_meters(
        pts[0][0], pts[0][1], -math.cos(start_hdg) * half_lane, -math.sin(start_hdg) * half_lane
    )

    return [*outbound, far_apex, *reversed(inbound), near_apex, outbound[0]]


def compute_pace_multiplier(
    progress: float,
    profile: str = "fatigue",
    fatigue_pct: float = 18.0,
    end_sprint: bool = False,
) -> tuple[float, str]:
    """Return (speed_multiplier, chinese_phase_label) for normalized run progress in [0, 1]."""
    p = max(0.0, min(1.0, float(progress)))
    prof = (profile or "fatigue").strip().lower()
    f = max(0.0, min(0.40, float(fatigue_pct) / 100.0))

    if prof == "steady" or (prof == "fatigue" and f <= 1e-6 and not end_sprint):
        return 1.0, "匀速巡航"

    if prof == "negative_split":
        low = max(0.60, 1.0 - 0.45 * f)
        high = 1.0 + 0.45 * f
        if p < 0.04:
            t = p / 0.04
            return low * (0.92 + 0.08 * t), "起步热身"
        # Smooth cosine ease-in-out from low -> high
        s = 0.5 * (1.0 - math.cos(math.pi * ((p - 0.04) / 0.96)))
        mult = low + (high - low) * s
        if p < 0.35:
            return mult, "前段·热身慢起"
        if p < 0.75:
            return mult, "中段·稳步提速"
        return mult, "后段·加速冲刺"

    # Default: "fatigue" — fast energized opening lap(s), progressively slower in later laps.
    peak_mult = 1.0 + 0.55 * f
    trough_mult = max(0.55, 1.0 - 0.60 * f)

    if p < 0.03:
        t = p / 0.03
        mult = 0.88 + (peak_mult - 0.88) * math.sin(t * math.pi * 0.5)
        return mult, "起步加速"

    if p < 0.22:
        # Hold high energized opening pace with very mild early settle
        local = (p - 0.03) / 0.19
        mult = peak_mult - 0.08 * f * local
        return mult, "前段·体力充沛"

    decay_end = 0.92 if end_sprint else 1.0
    if p <= decay_end:
        t = (p - 0.22) / max(1e-6, decay_end - 0.22)
        # Smooth nonlinear stamina curve (gentle mid-run, noticeably tired toward end)
        eased = t ** 1.15
        start_val = peak_mult - 0.08 * f
        mult = start_val + (trough_mult - start_val) * eased
        if p < 0.58:
            return mult, "中段·配速回落"
        return mult, "后段·体力下降"

    # Optional final finish kick (92% -> 100%)
    t = (p - 0.92) / 0.08
    kick_target = min(1.05, trough_mult * 1.08)
    mult = trough_mult + (kick_target - trough_mult) * math.sin(t * math.pi * 0.5)
    return mult, "终点前·小幅冲刺"

