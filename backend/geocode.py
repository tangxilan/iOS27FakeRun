"""Multi-source Chinese place/campus geocoder returning canonical WGS-84 coordinates."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .coords import haversine_distance

_CAMPUS_KEYWORDS = ("校区", "大学", "学院", "学校", "操场", "体育场", "田径场", "中学", "小学")
_SOURCE_WEIGHT = {"Photon": 3.0, "ArcGIS": 2.0, "OSM": 1.0}


async def _query_photon(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    resp = await client.get(
        "https://photon.komoot.io/api/",
        params={"q": query, "limit": 15},
    )
    resp.raise_for_status()
    features = resp.json().get("features", [])
    results: list[dict[str, Any]] = []
    for feat in features:
        coords = feat.get("geometry", {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        props = feat.get("properties") or {}
        name = str(props.get("name") or props.get("street") or "").strip()
        addr_parts = [
            str(props.get(k) or "").strip()
            for k in ("state", "city", "county", "district", "street")
            if props.get(k)
        ]
        address = " · ".join(dict.fromkeys(addr_parts))
        if not name:
            name = address or query
        results.append(
            {
                "name": name,
                "address": address,
                "source": "Photon",
                "lat": float(coords[1]),
                "lng": float(coords[0]),
            }
        )
    return results


async def _query_arcgis(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    resp = await client.get(
        "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates",
        params={
            "SingleLine": query,
            "f": "json",
            "maxLocations": 10,
            "outFields": "Match_addr,PlaceName,City,Region,Type",
            "langCode": "ZH",
        },
    )
    resp.raise_for_status()
    candidates = resp.json().get("candidates", [])
    results: list[dict[str, Any]] = []
    for cand in candidates:
        score = float(cand.get("score") or 0)
        if score < 80.0:
            continue
        loc = cand.get("location") or {}
        if "x" not in loc or "y" not in loc:
            continue
        attrs = cand.get("attributes") or {}
        name = str(attrs.get("PlaceName") or cand.get("address") or query).strip()
        addr = str(attrs.get("Match_addr") or cand.get("address") or "").strip()
        if addr == name:
            city = str(attrs.get("City") or "").strip()
            region = str(attrs.get("Region") or "").strip()
            addr = " · ".join(p for p in (region, city) if p)
        results.append(
            {
                "name": name,
                "address": addr,
                "source": "ArcGIS",
                "lat": float(loc["y"]),
                "lng": float(loc["x"]),
            }
        )
    return results


async def _query_nominatim(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    resp = await client.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "jsonv2", "limit": 10, "accept-language": "zh-CN"},
    )
    resp.raise_for_status()
    items = resp.json()
    results: list[dict[str, Any]] = []
    for item in items:
        if "lat" not in item or "lon" not in item:
            continue
        disp = str(item.get("display_name") or query).strip()
        parts = [p.strip() for p in disp.split(",") if p.strip()]
        name = str(item.get("name") or (parts[0] if parts else query)).strip()
        addr = " · ".join(parts[1:5]) if len(parts) > 1 else disp
        results.append(
            {
                "name": name,
                "address": addr,
                "source": "OSM",
                "lat": float(item["lat"]),
                "lng": float(item["lon"]),
            }
        )
    return results


def _rank_score(item: dict[str, Any], query_lower: str, tokens: list[str]) -> tuple[float, int]:
    name = item["name"].lower()
    addr = item["address"].lower()
    combined = f"{name} {addr}"
    score = _SOURCE_WEIGHT.get(item["source"], 0.0)
    if query_lower in name:
        score += 50.0
    elif query_lower in combined:
        score += 30.0
    for tok in tokens:
        if tok in name:
            score += 12.0
        elif tok in combined:
            score += 6.0
    for kw in _CAMPUS_KEYWORDS:
        if kw in name:
            score += 4.0
    # Slightly prefer concise, specific POI names over overly long descriptions
    return (score, -len(name))


def merge_and_rank_places(
    candidates: list[dict[str, Any]], query: str, limit: int = 15
) -> list[dict[str, Any]]:
    """Rank and deduplicate place candidates within 80 meters."""
    q_norm = query.strip().lower()
    tokens = [t for t in q_norm.replace("，", " ").split() if t]
    ranked = sorted(candidates, key=lambda it: _rank_score(it, q_norm, tokens), reverse=True)

    deduped: list[dict[str, Any]] = []
    for item in ranked:
        duplicate = False
        for kept in deduped:
            dist = haversine_distance(item["lat"], item["lng"], kept["lat"], kept["lng"])
            if dist < 80.0 and (
                item["name"] == kept["name"]
                or item["name"] in kept["name"]
                or kept["name"] in item["name"]
            ):
                duplicate = True
                break
        if not duplicate:
            deduped.append(
                {
                    "name": item["name"],
                    "address": item["address"],
                    "source": item["source"],
                    "lat": round(float(item["lat"]), 7),
                    "lng": round(float(item["lng"]), 7),
                }
            )
            if len(deduped) >= limit:
                break
    return deduped


async def search_places(query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Query Photon, ArcGIS, and OSM Nominatim in parallel for Chinese campus & POI coverage."""
    clean = query.strip()
    if not clean:
        return []

    async with httpx.AsyncClient(timeout=5.0, headers={"User-Agent": "iOS27FakeRun/1.1"}) as client:
        raw_groups = await asyncio.gather(
            _query_photon(client, clean),
            _query_arcgis(client, clean),
            _query_nominatim(client, clean),
            return_exceptions=True,
        )

    all_candidates: list[dict[str, Any]] = []
    for group in raw_groups:
        if isinstance(group, list):
            all_candidates.extend(group)

    return merge_and_rank_places(all_candidates, clean, limit=limit)
