"""Reference data: the markets a brand can be measured in.

DataForSEO identifies markets by numeric location codes; AI-answer endpoints
take an ISO country code for web-search localisation. Kept deliberately small;
`GET /api/v1/reference/locations` exposes it so the UI never hardcodes it.
"""

from __future__ import annotations

from typing import TypedDict


class Location(TypedDict):
    code: int
    name: str
    iso: str
    languages: list[str]


LOCATIONS: list[Location] = [
    {"code": 2840, "name": "United States", "iso": "US", "languages": ["en", "es"]},
    {"code": 2826, "name": "United Kingdom", "iso": "GB", "languages": ["en"]},
    {"code": 2124, "name": "Canada", "iso": "CA", "languages": ["en", "fr"]},
    {"code": 2036, "name": "Australia", "iso": "AU", "languages": ["en"]},
    {"code": 2356, "name": "India", "iso": "IN", "languages": ["en", "hi"]},
    {"code": 2586, "name": "Pakistan", "iso": "PK", "languages": ["en", "ur"]},
    {"code": 2784, "name": "United Arab Emirates", "iso": "AE", "languages": ["en", "ar"]},
    {"code": 2276, "name": "Germany", "iso": "DE", "languages": ["de", "en"]},
    {"code": 2250, "name": "France", "iso": "FR", "languages": ["fr", "en"]},
    {"code": 2724, "name": "Spain", "iso": "ES", "languages": ["es", "en"]},
]

LANGUAGE_NAMES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "hi": "Hindi",
    "ur": "Urdu",
    "ar": "Arabic",
}

_BY_CODE = {loc["code"]: loc for loc in LOCATIONS}


def get_location(code: int) -> Location | None:
    return _BY_CODE.get(code)


def country_iso(code: int) -> str | None:
    loc = _BY_CODE.get(code)
    return loc["iso"] if loc else None
