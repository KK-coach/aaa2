"""Strukturált adat a renderelt DOM-ból a JSON-LD mellett: microdata és RDFa.

Tiszta függvények, adatbázist nem írnak. A kimenet elemenként egy JSON-objektum a JSON-LD-hez
hasonló alakban (`@type`, `@id`, a tulajdonságok név szerint; az ismétlődő tulajdonság lista, a
beágyazott elem beágyazott objektum), hogy a `StructuredData` szerződés a jelölés fajtájától
függetlenül ugyanúgy olvasható legyen.

- Microdata: legfelső szintű elem az `itemscope`-os, `itemprop` nélküli elem. `@type` az
  `itemtype` (több értéknél lista), `@id` az `itemid`. A tulajdonság értéke: beágyazott
  `itemscope` → objektum; `meta` → `content`; `a`, `area`, `link` → `href`; `img`, `audio`,
  `video`, `source`, `track`, `embed`, `iframe` → `src`; `object` → `data`; `data`, `meter` →
  `value`; `time` → `datetime` (ha nincs, a szöveg); különben a szöveg. Az `itemref` nem
  feloldott.
- RDFa (a Lite részhalmaz: `vocab`, `typeof`, `property`, `resource`, `prefix`): legfelső
  szintű elem a `typeof`-os elem, amely nem egy típusos ős tulajdonsága. `@type` a `typeof`
  (több értéknél lista), `@context` a legközelebbi `vocab`, `@id` a `resource` (vagy `about`).
  A tulajdonság értéke: `typeof`-os elem → objektum; `content`; `href` vagy `src`; `datetime`;
  különben a szöveg. A típusos ős nélküli `property` (pl. az Open Graph `meta` elemei) nem
  RDFa-elem itt.
- A `<template>` és a `<noscript>` tartalma kimarad.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from selectolax.parser import HTMLParser, Node

_WHITESPACE = re.compile(r"\s+")
_SKIPPED = frozenset({"template", "noscript"})
_HREF_TAGS = frozenset({"a", "area", "link"})
_SRC_TAGS = frozenset({"img", "audio", "video", "source", "track", "embed", "iframe"})
_VALUE_TAGS = frozenset({"data", "meter"})


@dataclass(frozen=True)
class StructuredItem:
    syntax: str                 # microdata | rdfa
    type: str | None            # a típus rövid neve (több típus vesszővel), mint a JSON-LD-nél
    json: str
    ordinal: int


def structured_items(tree: HTMLParser) -> tuple[StructuredItem, ...]:
    """Az oldal microdata- és RDFa-elemei a DOM sorrendjében, előbb a microdata; `ordinal`
    1-től, a két jelölésen át folyamatosan."""
    found: list[tuple[str, dict]] = [("microdata", item) for item in microdata_items(tree)]
    found += [("rdfa", item) for item in rdfa_items(tree)]
    return tuple(
        StructuredItem(syntax, _short_type(item.get("@type")),
                       json.dumps(item, ensure_ascii=False, sort_keys=True), ordinal)
        for ordinal, (syntax, item) in enumerate(found, start=1))


def microdata_items(tree: HTMLParser) -> list[dict]:
    return [_microdata_item(node) for node in tree.css("[itemscope]")
            if "itemprop" not in node.attributes and not _skipped(node)]


def rdfa_items(tree: HTMLParser) -> list[dict]:
    return [_rdfa_item(node) for node in tree.css("[typeof]")
            if not _skipped(node)
            and not ("property" in node.attributes and _has_ancestor(node, "typeof"))]


def _microdata_item(node: Node) -> dict:
    item: dict = {}
    kinds = (node.attributes.get("itemtype") or "").split()
    if kinds:
        item["@type"] = kinds[0] if len(kinds) == 1 else kinds
    if node.attributes.get("itemid"):
        item["@id"] = node.attributes["itemid"]
    for child in _scope_children(node, "itemscope"):
        names = (child.attributes.get("itemprop") or "").split()
        if not names:
            continue
        value = _microdata_item(child) if "itemscope" in child.attributes \
            else _microdata_value(child)
        for name in names:
            _add(item, name, value)
    return item


def _microdata_value(node: Node) -> str:
    tag, attributes = node.tag, node.attributes
    if tag == "meta":
        return attributes.get("content") or ""
    if tag in _HREF_TAGS:
        return attributes.get("href") or ""
    if tag in _SRC_TAGS:
        return attributes.get("src") or ""
    if tag == "object":
        return attributes.get("data") or ""
    if tag in _VALUE_TAGS:
        return attributes.get("value") or ""
    if tag == "time" and attributes.get("datetime"):
        return attributes["datetime"]
    return _text(node)


def _rdfa_item(node: Node) -> dict:
    item: dict = {}
    vocab = _vocab(node)
    if vocab:
        item["@context"] = vocab
    kinds = (node.attributes.get("typeof") or "").split()
    if kinds:
        item["@type"] = kinds[0] if len(kinds) == 1 else kinds
    identifier = node.attributes.get("resource") or node.attributes.get("about")
    if identifier:
        item["@id"] = identifier
    for child in _scope_children(node, "typeof"):
        names = (child.attributes.get("property") or "").split()
        if not names:
            continue
        value = _rdfa_item(child) if "typeof" in child.attributes else _rdfa_value(child)
        for name in names:
            _add(item, name, value)
    return item


def _rdfa_value(node: Node) -> str:
    attributes = node.attributes
    for name in ("content", "href", "src", "datetime", "resource"):
        if attributes.get(name):
            return attributes[name]
    return _text(node)


def _scope_children(node: Node, boundary: str):
    """A csomópont leszármazottai a DOM sorrendjében; a beágyazott elem (`boundary`
    attribútumú leszármazott) maga még igen, a belseje már nem."""
    for child in node.iter(include_text=False):
        if child.tag in _SKIPPED:
            continue
        yield child
        if boundary not in child.attributes:
            yield from _scope_children(child, boundary)


def _vocab(node: Node) -> str | None:
    current: Node | None = node
    while current is not None:
        vocab = (current.attributes or {}).get("vocab")
        if vocab:
            return vocab
        current = current.parent
    return None


def _has_ancestor(node: Node, attribute: str) -> bool:
    current = node.parent
    while current is not None:
        if attribute in (current.attributes or {}):
            return True
        current = current.parent
    return False


def _skipped(node: Node) -> bool:
    current = node.parent
    while current is not None:
        if current.tag in _SKIPPED:
            return True
        current = current.parent
    return False


def _add(item: dict, name: str, value: object) -> None:
    if name not in item:
        item[name] = value
    elif isinstance(item[name], list):
        item[name].append(value)
    else:
        item[name] = [item[name], value]


def _short_type(kind: object) -> str | None:
    kinds = kind if isinstance(kind, list) else [kind] if kind else []
    names = [re.split(r"[/#:]", str(k).rstrip("/#"))[-1] for k in kinds]
    return ",".join(n for n in names if n) or None


def _text(node: Node) -> str:
    return _WHITESPACE.sub(" ", node.text(separator=" ", strip=True) or "").strip()
