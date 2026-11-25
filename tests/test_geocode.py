import asyncio

import httpx

from backend import geocode


def test_merge_and_rank_places_prioritizes_campus_and_dedupes():
    candidates = [
        {"name": "陕西省西安市", "address": "中国", "source": "ArcGIS", "lat": 34.343, "lng": 108.935},
        {"name": "西安建筑科技大学草堂校区", "address": "西安市 · 长安区", "source": "Photon", "lat": 34.01647, "lng": 108.74963},
        {"name": "西安建筑科技大学草堂校区", "address": "长安区 · 西安市", "source": "OSM", "lat": 34.01652, "lng": 108.74965},
        {"name": "西安建筑科技大学雁塔校区", "address": "西安市 · 碑林区", "source": "Photon", "lat": 34.23915, "lng": 108.96249},
    ]
    ranked = geocode.merge_and_rank_places(candidates, "西安建筑科技大学", limit=10)
    names = [r["name"] for r in ranked]
    assert "西安建筑科技大学草堂校区" in names
    assert "西安建筑科技大学雁塔校区" in names
    # The two identical-named campus entries within 80m must deduplicate to 1.
    assert names.count("西安建筑科技大学草堂校区") == 1
    assert ranked[0]["name"].startswith("西安建筑科技大学")


def test_search_places_resilient_when_some_providers_fail(monkeypatch):
    async def fail_photon(_client, _q):
        raise httpx.TimeoutException("timeout")

    async def ok_arcgis(_client, _q):
        return [
            {
                "name": "西安建筑科技大学草堂校区",
                "address": "陕西省西安市长安区",
                "source": "ArcGIS",
                "lat": 34.01786,
                "lng": 108.74927,
            }
        ]

    async def fail_nominatim(_client, _q):
        raise RuntimeError("dns error")

    monkeypatch.setattr(geocode, "_query_photon", fail_photon)
    monkeypatch.setattr(geocode, "_query_arcgis", ok_arcgis)
    monkeypatch.setattr(geocode, "_query_nominatim", fail_nominatim)

    res = asyncio.run(geocode.search_places("西安建筑科技大学草堂校区"))
    assert len(res) == 1
    assert res[0]["name"] == "西安建筑科技大学草堂校区"
