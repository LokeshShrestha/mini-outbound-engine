from __future__ import annotations

import re
from typing import Any

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
HEADERS = {"User-Agent": "mini-outbound-engine/1.0 (local research tool)"}

TYPE_FILTERS = {
    "software": ['[office="company"]', '[office="it"]', '[amenity="coworking_space"]'],
    "agency": ['[office="company"]'],
    "clinic": ['[amenity="clinic"]', '[amenity="doctors"]'],
    "logistics": ['[office="company"]', '[office="logistics"]'],
    "real estate": ['[office="company"]'],
    "education": ['[amenity="school"]', '[amenity="college"]'],
    "restaurant": ['[amenity="restaurant"]'],
    "retail": ['[shop]'],
}


class DiscoveryError(RuntimeError):
    pass


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def geocode(location: str) -> tuple[float, float]:
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": location, "format": "jsonv2", "limit": 1},
            headers=HEADERS,
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise DiscoveryError("The free location service is temporarily unavailable") from error
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
    type_key = next((key for key in TYPE_FILTERS if key in company_type), "company")
    tag_filters = TYPE_FILTERS.get(type_key, ['[office="company"]'])
    keyword_clause = _clean(keywords)
    name_filter = f'[name~"{re.escape(keyword_clause)}",i]' if keyword_clause else '[name]'
    selectors = "\n".join(
        f"nwr{tag_filter}{name_filter}(around:{radius},{lat},{lon});"
        for tag_filter in tag_filters
    )
    query = f"""
    [out:json][timeout:30];
    (
      {selectors}
    );
    out center tags;
    """
    response = None
    last_error: Exception | None = None
    for endpoint in OVERPASS_URLS:
        try:
            candidate = requests.post(
                endpoint,
                data=query,
                headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            candidate.raise_for_status()
            response = candidate
            break
        except requests.RequestException as error:
            last_error = error
    if response is None:
        raise DiscoveryError(
            "The free company search service timed out. Please try again in a moment."
        ) from last_error
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
