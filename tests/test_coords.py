import math

from backend.coords import (
    bd09_to_wgs84,
    bearing_deg,
    from_wgs84,
    gcj02_to_wgs84,
    haversine_distance,
    offset_meters,
    out_of_china,
    to_wgs84,
    wgs84_to_bd09,
    wgs84_to_gcj02,
)


def test_out_of_china():
    assert out_of_china(35.6812, 139.7671) is True  # Tokyo
    assert out_of_china(39.9928, 116.3102) is False  # Beijing


def test_wgs84_gcj02_roundtrip_high_precision():
    w_lat, w_lng = 39.9928123, 116.3102456
    g_lat, g_lng = wgs84_to_gcj02(w_lat, w_lng)
    assert abs(g_lat - w_lat) > 1e-4 or abs(g_lng - w_lng) > 1e-4
    back_lat, back_lng = gcj02_to_wgs84(g_lat, g_lng)
    err_m = haversine_distance(w_lat, w_lng, back_lat, back_lng)
    assert err_m < 0.01


def test_wgs84_bd09_roundtrip():
    w_lat, w_lng = 31.230416, 121.473701
    b_lat, b_lng = wgs84_to_bd09(w_lat, w_lng)
    back_lat, back_lng = bd09_to_wgs84(b_lat, b_lng)
    err_m = haversine_distance(w_lat, w_lng, back_lat, back_lng)
    assert err_m < 0.2


def test_crs_dispatch():
    lat, lng = 30.534, 114.365
    g = from_wgs84(lat, lng, "gcj02")
    w = to_wgs84(g[0], g[1], "gcj02")
    assert haversine_distance(lat, lng, w[0], w[1]) < 0.01


def test_offset_and_bearing():
    lat, lng = 39.9928, 116.3102
    n_lat, n_lng = offset_meters(lat, lng, 100.0, 0.0)
    dist = haversine_distance(lat, lng, n_lat, n_lng)
    assert math.isclose(dist, 100.0, rel_tol=0.01)
    assert bearing_deg(lat, lng, n_lat, n_lng) < 0.5

    e_lat, e_lng = offset_meters(lat, lng, 0.0, 100.0)
    assert math.isclose(haversine_distance(lat, lng, e_lat, e_lng), 100.0, rel_tol=0.01)
    assert math.isclose(bearing_deg(lat, lng, e_lat, e_lng), 90.0, abs_tol=0.5)
