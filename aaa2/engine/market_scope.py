"""Piaci hatókör a kezdőoldalakból és a sitewide strukturált tényekből.

Kezdőoldalak: a seed oldal és a hreflang-alternatívái. Rajtuk számít:

- a város (`CITY_TOKENS`) a title-ben, a meta descriptionben és a H1–H3-ban, vagy postai címben
  a main contentben: `1073 Budapest`, `H-1148 Budapest`, `London SW1A 1AA`, `New York, NY 10001`;
- a nemzetközi jel (`INTL_RE`) a fejrészben, a main contentben és a JSON-LD-ben.

Sitewide: az ország (ccTLD vagy schema-cím, a hívó adja), a schema-cím városa
(`addressLocality`), és az `areaServed` / `serviceArea` nemzetközi értéke (`Worldwide`). A többi
oldal szabad szövege nem számít.

Döntés, ebben a sorrendben:

1. város és (ország vagy nemzetközi jel) → `mixed`: leírva, nem feloldva;
2. ország → `country_specific`;
3. nemzetközi jel → `international_global`;
4. város → `local`;
5. különben `not_country_specific`.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

from aaa2.engine.target_country import schema_dicts

CITY_TOKENS = (
    "london", "manchester", "birmingham", "leeds", "glasgow", "edinburgh", "bristol",
    "liverpool", "cardiff", "belfast", "budapest", "vienna", "berlin", "munich", "paris",
    "amsterdam", "dublin", "madrid", "barcelona", "milan", "rome", "zurich", "new york",
    "los angeles", "san francisco", "chicago", "boston", "toronto", "sydney", "melbourne",
    "singapore", "dubai",
)
INTL_RE = re.compile(
    r"\b(worldwide|international|internationally|globally|global|online|remotely|remote)\b",
    re.IGNORECASE,
)
AREA_SERVED_KEYS = ("areaServed", "serviceArea")

# Irányítószám a város előtt (HU, DE, AT, IT, FR, ES, NL), országjellel vagy anélkül; utána
# (UK postcode, US állam + ZIP).
_POSTCODE_BEFORE = r"(?<![\w-])(?:[A-Z]{1,3}-)?\d{4,5}(?:\s?[A-Z]{2})?\s+"
_POSTCODE_AFTER = r",?\s+(?:[A-Z]{2}\s+\d{5}(?:-\d{4})?|[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b"
_CITY_IN_HEAD = tuple((city, re.compile(rf"\b{re.escape(city)}\b")) for city in CITY_TOKENS)
_CITY_IN_ADDRESS = tuple(
    (city, re.compile(
        rf"{_POSTCODE_BEFORE}(?i:{re.escape(city)})\b|\b(?i:{re.escape(city)}){_POSTCODE_AFTER}"))
    for city in CITY_TOKENS
)


@dataclass(frozen=True)
class ScopePage:
    """Egy kezdőoldal: fejrész (title, meta description, H1–H3), main content, JSON-LD."""

    head_text: str
    main_content: str
    schema_items: tuple[object, ...]


@dataclass(frozen=True)
class MarketScope:
    scope: str
    city: str | None


def market_scope(
    home_pages: Iterable[ScopePage], site_schema: Iterable[object], country: str | None
) -> MarketScope:
    home_pages = list(home_pages)
    site_schema = list(site_schema)
    head = " ".join(page.head_text for page in home_pages).lower()
    main = " ".join(page.main_content for page in home_pages)
    home_schema = json.dumps([item for page in home_pages for item in page.schema_items],
                             ensure_ascii=False)
    city = (_first_city(head, _CITY_IN_HEAD) or _first_city(main, _CITY_IN_ADDRESS)
            or _schema_city(site_schema))
    international = (
        bool(INTL_RE.search(head)) or bool(INTL_RE.search(f"{main} {home_schema}"))
        or _serves_internationally(site_schema)
    )
    if city and (country or international):
        scope = "mixed"
    elif country:
        scope = "country_specific"
    elif international:
        scope = "international_global"
    elif city:
        scope = "local"
    else:
        scope = "not_country_specific"
    return MarketScope(scope, city.title() if city else None)


def _first_city(text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> str | None:
    return next((city for city, pattern in patterns if pattern.search(text)), None)


def _schema_city(site_schema: list[object]) -> str | None:
    localities = []
    for node in schema_dicts(site_schema):
        address = node.get("address")
        for part in address if isinstance(address, list) else [address]:
            if isinstance(part, dict) and isinstance(part.get("addressLocality"), str):
                localities.append(part["addressLocality"])
    return _first_city(" ".join(localities).lower(), _CITY_IN_HEAD)


def _serves_internationally(site_schema: list[object]) -> bool:
    for node in schema_dicts(site_schema):
        for key in AREA_SERVED_KEYS:
            value = node.get(key)
            for area in value if isinstance(value, list) else [value]:
                name = area.get("name") if isinstance(area, dict) else area
                if isinstance(name, str) and INTL_RE.search(name):
                    return True
    return False
