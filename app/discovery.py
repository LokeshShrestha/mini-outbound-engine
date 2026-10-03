from __future__ import annotations

import re
from typing import Any

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "mini-outbound-engine/1.0 (local research tool)"}

TYPE_FILTERS = {
    "software": '[office="company"]',
    "agency": '[office="company"]',
    "clinic": '[amenity="clinic"]',
    "logistics": '[office="company"]',
    "real estate": '[office="company"]',
    "education": '[amenity="school"]',
    "restaurant": '[amenity="restaurant"]',
    "retail": '[shop] ',
}


class DiscoveryError(RuntimeError):
    pass


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def geocode(location: str) -> tuple[float, float]:
    response = requests.get(
        NOMINATIM_URL,
        params={"q": location, "format": "jsonv2", "limit": 1},
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    places = response.json()
    if not places:
        raise DiscoveryError(f"Could not find the location: {location}")
    return float(places[0]["lat"]), float(places[0]["lon"])


def discover_companies(
    company_type: str,
    location: str,
    keywords: str = "",
    limit: int = 25,
    radius: int = 15000,
) -> list[dict[str, Any]]:
    company_type = _clean(company_type).lower()
    location = _clean(location)
    if not company_type or not location:
        raise DiscoveryError("Company type and location are required")
    limit = max(1, min(limit, 100))
    radius = max(1000, min(radius, 50000))
    lat, lon = geocode(location)
    tag_filter = TYPE_FILTERS.get(company_type, '[office="company"]')
    keyword_clause = _clean(keywords)
    name_filter = f'[name~"{re.escape(keyword_clause)}",i]' if keyword_clause else '[name]'
    query = f"""
    [out:json][timeout:30];
    (
      nwr{tag_filter}{name_filter}(around:{radius},{lat},{lon});
    );
    out center tags;
    """
    response = requests.post(
        OVERPASS_URL,
        data=query,
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=45,
    )
    response.raise_for_status()
    results = []
    seen: set[tuple[str, str]] = set()
    for element in response.json().get("elements", []):
        tags = element.get("tags", {})
        name = _clean(tags.get("name", ""))
        if not name:
            continue
        website = _clean(tags.get("website") or tags.get("contact:website") or "")
        if website and not website.startswith(("http://", "https://")):
            website = f"https://{website}"
        address = _clean(
            ", ".join(
                part for part in (
                    tags.get("addr:housenumber", ""),
                    tags.get("addr:street", ""),
                    tags.get("addr:city", ""),
                )
                if part
            )
        )
        key = (name.casefold(), website.casefold())
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "name": name,
                "website": website or f"osm:{element.get('type')}:{element.get('id')}",
                "contact_email": _clean(tags.get("email") or tags.get("contact:email") or ""),
                "context": f"Discovered in OpenStreetMap near {location}. Address: {address or 'not listed'}.",
                "site_text": "",
            }
        )
        if len(results) >= limit:
            break
    return results
