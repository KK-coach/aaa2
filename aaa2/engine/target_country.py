"""Célország sitewide: országjelek minden sikeres oldalról, súlyozott szavazás, LLM nélkül.

Jelek oldalanként, az ország ISO 3166-1 alpha-2 kódjával:

- `hreflang`: a hreflang régiókódja (`en-GB` → GB);
- `path_prefix`: az URL első szegmense, ha `xx` vagy `xx-yy` alakú; a régiókód, ha van, különben
  maga a kód (`/hu/` → HU, `/en-gb/` → GB, `/en/` → semmi);
- `phone`: telefon-előhívó (`+36`, `0036`) a main contentben, a title-ben és a meta
  descriptionben;
- `currency`: egyértelmű pénznem ugyanabból a szövegből. A szimbólum (`€`, `£`, `Kč`, `zł`,
  `Ft`) szó szerint számít, az ISO-kód csak szóhatárral és kis-nagybetű-érzékenyen. Az EUR és
  az USD nem ad országot;
- `og_locale`: az `og:locale` régiókódja (`hu_HU` → HU);
- `schema`: JSON-LD `address.addressCountry` (kód vagy országnév).

Site-szinten ehhez jön a `tld`: országkódos TLD, két címkés suffixnél az utolsó címke
(`co.uk` → GB). A generikus TLD (`.com`, `.coach`, `.io`) nem jel. A tartalom nyelve sosem jel.

Súlyok: tld 3, phone 2, schema 2, og_locale 2 (1, ha elszigetelt: a tld, schema, phone és
hreflang jelek egyike sem adja az országát), path_prefix 1,5, hreflang 1, currency 1. Egy
jelfajta súlya az országai között oszlik meg aszerint, hány oldalon adta az adott országot;
oldalanként egy ország egyszer számít.

Konfidencia az erős jelfajtákból (tld, hreflang, phone, schema, és az og_locale, ha nem
elszigetelt), amelyeknek egyetlen vezető országuk van:

- legalább 3 a győztest vezeti → high;
- egy erős jelfajta más országot vezet (ütközés) → low;
- 1–2 a győztest vezeti → medium;
- egy sem → low.

Holtversenyben az élen nincs célország; a jelöltek ilyenkor is megmaradnak.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from urllib.parse import urlsplit

from aaa2.engine.normalize import public_suffix

COUNTRY_NAMES = {
    "HU": "Hungary", "DE": "Germany", "AT": "Austria", "CH": "Switzerland",
    "GB": "United Kingdom", "US": "United States", "CA": "Canada", "AU": "Australia",
    "IE": "Ireland", "IT": "Italy", "FR": "France", "ES": "Spain", "NL": "Netherlands",
    "BE": "Belgium", "CZ": "Czech Republic", "PL": "Poland", "RO": "Romania", "HR": "Croatia",
}
CODE_ALIASES = {"UK": "GB"}
TLD_COUNTRY = {
    "hu": "HU", "de": "DE", "at": "AT", "ch": "CH", "uk": "GB", "us": "US", "ca": "CA",
    "au": "AU", "ie": "IE", "it": "IT", "fr": "FR", "es": "ES", "nl": "NL", "be": "BE",
    "cz": "CZ", "pl": "PL", "ro": "RO", "hr": "HR",
}
PHONE_PREFIX_COUNTRY = {
    "+36": "HU", "+49": "DE", "+43": "AT", "+41": "CH", "+44": "GB", "+1": "US", "+61": "AU",
    "+353": "IE", "+39": "IT", "+33": "FR", "+34": "ES", "+31": "NL", "+32": "BE",
    "+420": "CZ", "+48": "PL", "+40": "RO", "+385": "HR",
}
CURRENCY_SYMBOLS = {"€": "EUR", "£": "GBP", "Kč": "CZK", "zł": "PLN", "Ft": "HUF"}
CURRENCY_ISO = ("HUF", "EUR", "GBP", "USD", "CHF", "CZK", "PLN", "RON", "HRK")
CURRENCY_COUNTRY = {
    "HUF": "HU", "GBP": "GB", "CHF": "CH", "CZK": "CZ", "PLN": "PL", "RON": "RO", "HRK": "HR",
}

WEIGHTS = {
    "tld": 3.0, "hreflang": 1.0, "path_prefix": 1.5, "phone": 2.0, "schema": 2.0,
    "og_locale": 2.0, "currency": 1.0,
}
ISOLATED_OG_LOCALE_WEIGHT = 1.0
STRONG_KINDS = ("tld", "hreflang", "phone", "schema", "og_locale")
CORROBORATING_KINDS = ("tld", "schema", "phone", "hreflang")

_PHONE = re.compile(r"(?<!\d)(?:\+|00)\s?(\d{1,3})(?:[\s.\-/()]?\d){5,}")
_PHONE_PREFIXES = sorted(PHONE_PREFIX_COUNTRY, key=len, reverse=True)
_CURRENCY_ISO = re.compile(r"\b(" + "|".join(CURRENCY_ISO) + r")\b")
_PATH_PREFIX = re.compile(r"([a-z]{2})(?:[-_]([a-z]{2}))?")
_OG_REGION = re.compile(r"[a-z]{2}[-_]([A-Za-z]{2})")
_NAME_TO_CODE = {name.lower(): code for code, name in COUNTRY_NAMES.items()}


@dataclass(frozen=True)
class Candidate:
    country: str
    score: float
    signals: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"country": self.country, "score": self.score, "signals": list(self.signals)}


@dataclass(frozen=True)
class TargetCountry:
    country: str | None
    confidence: str | None
    candidates: tuple[Candidate, ...]


def tld_country(domain: str) -> str | None:
    return TLD_COUNTRY.get(public_suffix(domain).rsplit(".", 1)[-1])


def page_country_signals(
    url: str, text: str, og_locale: str | None, hreflang_codes: Iterable[str],
    schema_items: Iterable[object],
) -> dict[str, set[str]]:
    """Egy oldal országjelei jelfajtánként; a szöveg a main content, a title és a meta
    description együtt."""
    signals = {
        "hreflang": {c for code in hreflang_codes if (c := _country_code(_region(code)))},
        "path_prefix": _path_prefix_country(url),
        "phone": phone_countries(text),
        "currency": {CURRENCY_COUNTRY[iso] for iso in currencies(text) if iso in CURRENCY_COUNTRY},
        "og_locale": _og_locale_country(og_locale),
        "schema": address_countries(schema_items),
    }
    return {kind: countries for kind, countries in signals.items() if countries}


def vote(tld: str | None, pages: Iterable[dict[str, set[str]]]) -> TargetCountry:
    counts: dict[str, Counter[str]] = {kind: Counter() for kind in WEIGHTS}
    if tld:
        counts["tld"][tld] = 1
    for page in pages:
        for kind, countries in page.items():
            counts[kind].update(countries)
    isolated = {country for country in counts["og_locale"]
                if not any(counts[kind][country] for kind in CORROBORATING_KINDS)}
    scores: dict[str, float] = defaultdict(float)
    kinds: dict[str, list[str]] = defaultdict(list)
    for kind, weight in WEIGHTS.items():
        total = counts[kind].total()
        for country, pages_with in counts[kind].items():
            if kind == "og_locale" and country in isolated:
                weight_here = ISOLATED_OG_LOCALE_WEIGHT
            else:
                weight_here = weight
            scores[country] += weight_here * pages_with / total
            kinds[country].append(kind)
    if not scores:
        return TargetCountry(None, None, ())
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    grand_total = sum(scores.values())
    candidates = tuple(Candidate(country, round(score / grand_total, 2), tuple(kinds[country]))
                       for country, score in ranked[:3])
    if len(ranked) > 1 and math.isclose(ranked[0][1], ranked[1][1]):
        return TargetCountry(None, None, candidates)
    top = ranked[0][0]
    return TargetCountry(top, _confidence(top, counts, isolated), candidates)


def leader(counter: Counter[str]) -> str | None:
    """A legtöbbször szereplő elem; holtversenyben és üresen None."""
    common = counter.most_common(2)
    if not common or (len(common) == 2 and common[0][1] == common[1][1]):
        return None
    return common[0][0]


def phone_countries(text: str) -> set[str]:
    found = set()
    for match in _PHONE.finditer(text):
        digits = "+" + match.group(1)
        for prefix in _PHONE_PREFIXES:
            if digits.startswith(prefix):
                found.add(PHONE_PREFIX_COUNTRY[prefix])
                break
    return found


def currencies(text: str) -> set[str]:
    found = {iso for symbol, iso in CURRENCY_SYMBOLS.items() if symbol in text}
    found.update(_CURRENCY_ISO.findall(text))
    return found


def address_countries(schema_items: Iterable[object]) -> set[str]:
    found = set()
    for node in schema_dicts(schema_items):
        address = node.get("address")
        for part in address if isinstance(address, list) else [address]:
            if isinstance(part, dict) and (code := _schema_country(part.get("addressCountry"))):
                found.add(code)
    return found


def schema_dicts(schema_items: Iterable[object]) -> Iterator[dict]:
    """Minden JSON-LD objektum, a beágyazottak is."""
    stack = list(schema_items)
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def _confidence(top: str, counts: dict[str, Counter[str]], isolated: set[str]) -> str:
    leaders = {}
    for kind in STRONG_KINDS:
        country = leader(counts[kind])
        if country is not None and not (kind == "og_locale" and country in isolated):
            leaders[kind] = country
    agreeing = [kind for kind, country in leaders.items() if country == top]
    if len(agreeing) >= 3:
        return "high"
    if any(country != top for country in leaders.values()):
        return "low"
    return "medium" if agreeing else "low"


def _path_prefix_country(url: str) -> set[str]:
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    match = _PATH_PREFIX.fullmatch(segments[0].lower()) if segments else None
    code = _country_code(match.group(2) or match.group(1)) if match else None
    return {code} if code else set()


def _og_locale_country(og_locale: str | None) -> set[str]:
    match = _OG_REGION.search((og_locale or "").strip())
    code = _country_code(match.group(1)) if match else None
    return {code} if code else set()


def _schema_country(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("@id")
    if not isinstance(value, str):
        return None
    return _country_code(value.strip()) or _NAME_TO_CODE.get(value.strip().lower())


def _country_code(code: str | None) -> str | None:
    code = (code or "").upper()
    code = CODE_ALIASES.get(code, code)
    return code if code in COUNTRY_NAMES else None


def _region(tag: str) -> str | None:
    parts = tag.replace("_", "-").split("-")
    return parts[1] if len(parts) >= 2 and parts[0].lower() != "x" else None
