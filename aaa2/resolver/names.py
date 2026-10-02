"""Nevek és szövegek a site-körnek: írásmód-normalizálás, névalakok, JSON-LD segédek."""
from __future__ import annotations

import html as html_lib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import duckdb

from aaa2.entities import store
from aaa2.entities.rules import (
    TITLE_SEPARATORS,
    alias_key,
    title_endings,
)
from aaa2.resolver import queries as resolver_queries

SITE_PREFIX_MIN = 4


PACKAGE_NAME_WORDS = 6


PARENTHETICAL = re.compile(r"(.+?)\s*\(([^()]+)\)")


NORMAL_DROP = re.compile(r"[\s_\-/–—‐()]+")


LEGAL_FORMS = frozenset({"kft", "zrt", "bt", "ltd", "llc", "gmbh", "inc"})


# az `alias_key` után: az „és” ékezet nélkül „es”
CONJUNCTION = re.compile(r"\s*(?:\bes\b|\band\b|&)\s*")


LABEL_SPLIT = re.compile(r"\s+(?:&|és|and|\+|–|—|-)\s+|\s*[/,]\s*")


ACRONYM = re.compile(r"[A-Z0-9][A-Z0-9&.+-]{1,5}")


@dataclass(frozen=True)
class Name:
    text: str
    source: str
    lang: str | None
    count: int = 1


def expansions(text: str) -> list[str]:
    """A zárójeles kifejtés (M2/6, 7. pont) részei külön névként, ha az egyik rész rövidítés
    (2–6 nagybetű vagy szám): „Keresőoptimalizálás (SEO)” → Keresőoptimalizálás, SEO;
    „SEO (Keresőoptimalizálás)” → ugyanígy. Más zárójeles alak nem bomlik."""
    match = PARENTHETICAL.fullmatch(text.strip())
    if not match:
        return []
    base, inner = match.group(1).strip(), match.group(2).strip()
    if ACRONYM.fullmatch(inner) or ACRONYM.fullmatch(base):
        return [base, inner]
    return []


def _title_forms(title: str | None, site_keys: set[str]) -> list[str]:
    """A title a site-nevet tartalmazó végződés nélkül, és az elválasztók (`TITLE_SEPARATORS`)
    közötti szeletei, a site-nevűek nélkül. Site-név a csonkolt alak is (a title hosszkorlátja
    levágja: „… - DUEX” a „DUEX Hungary Webshop” helyett; `site_name_form`)."""
    if not title:
        return []
    clean = title.strip()
    for ending in title_endings(clean)[1:]:
        if site_name_form(ending, site_keys, minimum=1):
            clean = clean[: clean.rfind(ending)].rstrip(" |-–—·:»•").strip()
            break
    forms = [clean] if clean and not site_name_form(clean, site_keys) else []
    pattern = "|".join(re.escape(sep) for sep in TITLE_SEPARATORS)
    for piece in re.split(pattern, clean):
        piece = piece.strip()
        if piece and piece not in forms and not site_name_form(piece, site_keys):
            forms.append(piece)
    return forms


def site_name_form(text: str, site_keys: set[str], minimum: int = SITE_PREFIX_MIN) -> bool:
    """A szöveg a site egyik neve, vagy annak legalább `minimum` jeles eleje (csonkolt alak; a
    title végén, a site-név helyén bármilyen rövid: „… - D”)."""
    key = alias_key(text)
    return key in site_keys or (len(key) >= minimum
                                and any(site.startswith(key) for site in site_keys))


def cut_off(form: str, full: str | None) -> bool:
    """A `form` a `full` levágott eleje, szó közben (a title hosszkorlátja: „… Hmv Tartá” a
    „… Hmv Tartályal 14KW …” H1 helyett); az ilyen alak nem név."""
    if not full:
        return False
    key, whole = alias_key(form), alias_key(full)
    return len(key) < len(whole) and whole.startswith(key) and whole[len(key)].isalnum()


def normal_key(text: str) -> str:
    """Az írásmód-normalizált kulcs: kis-nagybetű és ékezet nélkül (`alias_key`), az „és” és az
    „and” „&”-ként, az elválasztók (szóköz, aláhúzás, kötőjel, perjel, gondolatjel) és a
    zárójelek nélkül. A „/” vagy „@” tartalmú név (csomag- és útvonalszerű) csak az
    `alias_key`-t kapja: ott az elválasztó a név része."""
    key = alias_key(text)
    if "/" in key or "@" in key:
        return key
    return NORMAL_DROP.sub("", CONJUNCTION.sub("&", key))


def without_legal_form(name: str) -> str:
    """A szervezetnév a végén álló jogi forma (`LEGAL_FORMS`: Kft., Zrt., Bt., Ltd, LLC, GmbH,
    Inc.) nélkül; ha más nem marad, a név."""
    tokens = name.split()
    while len(tokens) > 1 and alias_key(tokens[-1]).strip(".,") in LEGAL_FORMS:
        tokens.pop()
    return " ".join(tokens).rstrip(",")


def long_form(text: str) -> str | None:
    """A zárójeles rövidítés-kifejtés hosszú része („GEO (Generative Engine Optimization)” →
    Generative Engine Optimization), vagy None. A rövidítés önmagában nem von össze."""
    parts = expansions(text)
    if not parts:
        return None
    base, inner = parts
    return inner if ACRONYM.fullmatch(base) else base


def label_parts(text: str) -> list[str]:
    """Az összetett ajánlatcímke részei (M2/6, 9. pont): az „&”, „és”, „and”, „+”, „/”, a
    gondolatjel és a vessző mentén, és a zárójeles rövidítés-kifejtés."""
    parts = [p.strip() for p in LABEL_SPLIT.split(text) if p.strip()]
    return list(dict.fromkeys([*parts, *(e for p in [text, *parts] for e in expansions(p))]))


def _site_name_keys(con: duckdb.DuckDBPyConnection) -> set[str]:
    """A site nevei: a brand szerepű (site-név) entitások és a brand típusú szabály-entitások
    neve és aliasai; a webshop termékmárkái (`brand_of` kapcsolattal) nem."""
    keys = set()
    for name, aliases in store.site_name_rows(
            con, resolver_queries.relation_from_ids(con, "brand_of")):
        keys |= {alias_key(f) for f in [name, *(aliases or [])]}
    return keys - {""}


def _catalog_names(node: dict) -> list[str]:
    names = []
    for item in _as_list((node.get("hasOfferCatalog") or {}).get("itemListElement")):
        offered = item.get("itemOffered") if isinstance(item, dict) else None
        for candidate in (offered, item):
            if isinstance(candidate, dict) and {_short(t) for t in _as_list(
                    candidate.get("@type"))} & {"Service", "Product"}:
                names += [_unescape(t) for t in _texts(candidate.get("name"))]
                break
    return names


def _name_like(text: str) -> bool:
    words = text.split()
    return 0 < len(words) <= PACKAGE_NAME_WORDS and not re.search(r"\d", text) \
        and len(text) <= 80


def _line(text: str, start: int | None) -> str:
    start = start or 0
    begin = text.rfind("\n", 0, start) + 1
    end = text.find("\n", start)
    return text[begin: end if end != -1 else len(text)]


def _exists(con: duckdb.DuckDBPyConnection, entity_id: int) -> bool:
    return store.entities_for_exists(con, entity_id)[0] > 0


def _typed_nodes(value: object) -> Iterable[dict]:
    if isinstance(value, dict):
        if "@type" in value:
            yield value
        for child in value.values():
            yield from _typed_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _typed_nodes(child)


def _texts(value: object) -> list[str]:
    return [v.strip() for v in _as_list(value) if isinstance(v, str) and v.strip()]


def _unescape(text: str) -> str:
    return html_lib.unescape(text).strip()


def _loads(raw: str) -> object:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _short(value: object) -> str:
    text = str(value).strip()
    return re.split(r"[/#:]", text)[-1] if text else text


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else [value] if value is not None else []


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
