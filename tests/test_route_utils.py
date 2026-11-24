import math

import pytest

from backend.route_utils import (
    build_polyline_distances,
    export_gpx,
    export_iosfakerun_json,
    generate_standard_track,
    parse_route_text,
    route_length_meters,
    sample_point_at_distance,
    smooth_route_catmull_rom,
)


def test_parse_iosfakerun_json_with_and_without_brackets():
    text_no_brackets = '{"lat":39.9928,"lng":116.3102},{"lat":39.9938,"lng":116.3112}'
    pts = parse_route_text(text_no_brackets, input_crs="wgs84")
    assert len(pts) == 2
    assert math.isclose(pts[0][0], 39.9928, abs_tol=1e-6)

    exported_bd = export_iosfakerun_json(pts, output_crs="bd09")
    re_parsed = parse_route_text(exported_bd, input_crs="bd09")
    assert math.isclose(re_parsed[0][0], pts[0][0], abs_tol=1e-5)
    assert math.isclose(re_parsed[0][1], pts[0][1], abs_tol=1e-5)


def test_parse_plain_lines_and_gpx():
    plain = """
    # comment line
    39.9928, 116.3102
    39.9938  116.3112
    """
    pts = parse_route_text(plain, input_crs="wgs84")
    assert len(pts) == 2

    gpx = export_gpx(pts, name="TestTrack")
    gpx_pts = parse_route_text(gpx, input_crs="wgs84")
    assert len(gpx_pts) == 2
    assert math.isclose(gpx_pts[1][0], pts[1][0], abs_tol=1e-6)

    lng_first_pts = parse_route_text("116.3102, 39.9928\n116.3112, 39.9938", input_crs="wgs84", lng_first=True)
    assert math.isclose(lng_first_pts[0][0], 39.9928, abs_tol=1e-6)


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        parse_route_text("   ")
    with pytest.raises(ValueError):
        parse_route_text("95.0, 116.0")


def test_generate_standard_track_400m():
    pts = generate_standard_track(39.9928, 116.3102, straight_m=84.39, radius_m=36.8, rotation_deg=15.0, points_per_lap=80)
    assert len(pts) == 80
    length = route_length_meters(pts, closed=True)
    assert 395.0 <= length <= 405.0


def test_smooth_and_sample():
    pts = [(39.99, 116.31), (39.991, 116.31), (39.991, 116.311), (39.99, 116.311)]
    smoothed = smooth_route_catmull_rom(pts, samples_per_segment=4, closed=True)
    assert len(smoothed) == 16
    cum = build_polyline_distances(smoothed)
    lat, lng, hdg = sample_point_at_distance(smoothed, cum, cum[-1] * 0.5)
    assert 39.989 < lat < 39.992
    assert 116.309 < lng < 116.312
    assert 0.0 <= hdg < 360.0
