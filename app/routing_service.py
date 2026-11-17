from __future__ import annotations

from typing import Any

import httpx

_GEOCODE_CACHE: dict[str, dict[str, Any]] = {}


def _is_prague_place(raw_query: str, display_name: str, address: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            raw_query.lower(),
            display_name.lower(),
            str(address.get("city", "")).lower(),
            str(address.get("town", "")).lower(),
            str(address.get("municipality", "")).lower(),
            str(address.get("county", "")).lower(),
        ]
    )
    return "praha" in haystack or "prague" in haystack


async def geocode_place(client: httpx.AsyncClient, query: str) -> dict[str, Any]:
    q = query.strip()
    if not q:
        raise ValueError("Prázdné místo pro výpočet trasy.")
    cache_key = q.lower()
    if cache_key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[cache_key]

    resp = await client.get(
        "https://nominatim.openstreetmap.org/search",
        params={
            "q": q,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
            "countrycodes": "cz,sk,de,at,pl",
            "accept-language": "cs",
        },
        headers={"User-Agent": "KnihaJizdApp/1.0 (local travel book helper)"},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Geokódování selhalo (HTTP {resp.status_code}).")
    results = resp.json()
    if not results:
        raise ValueError(f"Nepodařilo se najít místo: „{q}“. Zkuste zadat přesnější název (např. město nebo adresu).")

    top = results[0]
    info = {
        "query": q,
        "lat": float(top["lat"]),
        "lon": float(top["lon"]),
        "display_name": top.get("display_name", q),
        "is_prague": _is_prague_place(q, top.get("display_name", ""), top.get("address") or {}),
    }
    _GEOCODE_CACHE[cache_key] = info
    return info


async def calculate_route_distance(
    origin: str,
    destination: str,
    waypoints: list[str] | None = None,
    round_trip: bool = True,
) -> dict[str, Any]:
    stops_raw = [origin.strip()]
    if waypoints:
        stops_raw.extend(w.strip() for w in waypoints if w and w.strip())
    stops_raw.append(destination.strip())

    if len(stops_raw) < 2 or not stops_raw[0] or not stops_raw[-1]:
        raise ValueError("Zadejte prosím místo Odkud i Kam.")

    async with httpx.AsyncClient(timeout=15.0) as client:
        geocoded = [await geocode_place(client, s) for s in stops_raw]

        route_points = list(geocoded)
        if round_trip:
            route_points.append(geocoded[0])

        coords = ";".join(f"{p['lon']},{p['lat']}" for p in route_points)
        osrm_url = f"https://router.project-osrm.org/route/v1/driving/{coords}"
        resp = await client.get(osrm_url, params={"overview": "false"})
        if resp.status_code != 200:
            raise RuntimeError(f"Výpočet trasy OSRM selhal (HTTP {resp.status_code}).")
        data = resp.json()
        routes = data.get("routes") or []
        if not routes:
            raise RuntimeError("OSRM nenašlo průjezdnou automobilovou trasu mezi zadanými body.")

        total_meters = float(routes[0]["distance"])
        total_seconds = float(routes[0]["duration"])

    distance_km = max(1, int(round(total_meters / 1000.0)))
    duration_min = max(1, int(round(total_seconds / 60.0)))
    all_prague = all(p["is_prague"] for p in geocoded)
    category = "city" if all_prague else "other"

    if round_trip:
        route_label = " - ".join(stops_raw + [stops_raw[0]])
    else:
        route_label = " - ".join(stops_raw)

    return {
        "origin": origin.strip(),
        "destination": destination.strip(),
        "waypoints": [w.strip() for w in (waypoints or []) if w and w.strip()],
        "round_trip": round_trip,
        "distance_km": distance_km,
        "duration_min": duration_min,
        "category": category,
        "km_city": distance_km if category == "city" else 0,
        "km_other": distance_km if category == "other" else 0,
        "suggested_destination_text": route_label,
        "resolved_stops": [
            {"query": p["query"], "display_name": p["display_name"], "is_prague": p["is_prague"]}
            for p in geocoded
        ],
    }
