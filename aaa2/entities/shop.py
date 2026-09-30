"""Webshop-entitásszintek (M2 spec, M2/6, 9a pont), LLM nélkül, a site-kör része (`run_shop`).

- Oldaltípus: `pages.page_types` (termék: az oldalra mutató JSON-LD `Product`; kategória és
  márka × kategória: a site-fájl `[page_types]` URL-mintái). Morzsamenü: `pages.breadcrumbs`.
- Kategória (concept / category, a kategóriaoldalhoz kötve): kategóriaoldal-csoportonként egy
  entitás, a neve a H1 (ha nincs, a morzsamenü utolsó eleme), aliasai a rá
  mutató morzsamenü-nevek. A gyökérkategória (minden más kategóriaoldal morzsamenüjében ős)
  kimarad. Az azonos kulcsú concept beolvad (`page_identity`). Említés: title- és H1-blokk.
- Márka (brand): a termék saját JSON-LD `Product.brand`-je, ennek híján a morzsamenü utolsó
  eleme, ha márka × kategória oldalra mutat (az oldal H1-e); ha egyik sincs, az így ismert
  márkák közül az, amelyikkel a terméknév kezdődik (önálló előfordulás). Kanonikus alak: ha a márka
  a termékneveinek elején áll, az ottani leggyakoribb írásmód, különben a márkaoldalé. Az
  azonos kulcsú brand- vagy org-entitás beolvad (`shop_brand`). Említés: a márka × kategória
  oldalak H1-e és a termékek H1-ében a név eleji márka.
- Termék (az oldalhoz kötött product-entitás, `site._page_entities`): altípus `variant`,
  tulajdonságai (`attributes`) a terméknévből (`name_attributes`: teljesítmény, fázisszám,
  energiaosztály); `in_category` a morzsamenü legmélyebb kategóriaoldalához.
- Termékcsalád (product / line), márkánként (`families`): a terméknév (H1, a név eleji márka
  nélkül) az első tulajdonság- vagy típuskód-token előtt (`FAMILY_STOP`); család, ha a márka
  legalább két terméke osztozik rajta (kis-nagybetű nélkül). A többi terméknél: a leghosszabb
  közös token-előtag legalább két termék között, ha a termékek utána eltérő, számjegyet
  tartalmazó tokennel folytatódnak („BlueSoft Eco” 12 / 18 / 25). A család neve a tagok H1-ében
  álló leggyakoribb alak (a márkával, ha a H1 azzal kezdődik). Kapcsolat: termék `part_of`
  család, márka `brand_of` család; család nélküli terméknél márka `brand_of` termék. Említés: a
  tagtermékek H1-ében a családnév.
- A kapcsolatok forrása `shop`; a beolvasztások a `merge_log`-ban.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from aaa2.entities.overrides import SiteConfig
from aaa2.entities.pages import PageInfo, breadcrumbs, page_types, page_url
from aaa2.entities.rules import alias_key

if TYPE_CHECKING:
    from aaa2.entities.site import Merger, _Context

UNITS = {"kw", "w", "m3/h", "m³/h"}
PHASE_WORDS = {"fázis", "f", "fázisú"}
CAPACITY = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(kw|w|m3/h|m³/h)$", re.IGNORECASE)
NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")
PHASE = re.compile(r"^([1-3])\s*(?:f|fázis|fázisú)$", re.IGNORECASE)
ENERGY = re.compile(r"^A\+{1,4}$")
MODEL_MIN_CHARS = 6
FAMILY_STOP = ("capacity", "phase", "energy_class", "model")
FAMILY_MIN_PRODUCTS = 2


@dataclass
class ShopRun:
    categories: int = 0
    brands: int = 0
    families: int = 0
    products: int = 0
    in_family: int = 0
    with_attributes: int = 0
    unbranded: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"categories": self.categories, "brands": self.brands,
                "families": self.families, "products": self.products,
                "in_family": self.in_family, "with_attributes": self.with_attributes,
                "unbranded": self.unbranded}


@dataclass(frozen=True)
class Product:
    entity_id: int
    page_id: int
    name: str                          # a H1 (ha nincs, az entitás neve)
    brand: str | None                  # a márkaoldal vagy a JSON-LD alakja
    category: str | None               # a kategóriaoldal csoportja


# ---------------------------------------------------------------------------
# a terméknév tokenjei
# ---------------------------------------------------------------------------


def _bare(token: str) -> str:
    return token.strip(",;:()")


def token_kind(tokens: Sequence[str], i: int) -> str | None:
    """A `tokens[i]` tulajdonság- vagy típuskód-token-e: capacity (3,5 kW, 400m3/h), phase
    (1 Fázis, 3Fázis, 1F), energy_class (A+++), model (legalább `MODEL_MIN_CHARS` jel,
    betűvel és számjeggyel, pl. CWH09VN-K6DNB6F); különben None."""
    token = _bare(tokens[i])
    after = _bare(tokens[i + 1]).lower() if i + 1 < len(tokens) else ""
    if CAPACITY.match(token) or (NUMBER.match(token) and after in UNITS):
        return "capacity"
    if PHASE.match(token) or (token in ("1", "2", "3") and after in PHASE_WORDS):
        return "phase"
    if ENERGY.match(token):
        return "energy_class"
    if len(token) >= MODEL_MIN_CHARS and re.search(r"\d", token) and re.search(r"[A-Za-z]",
                                                                              token):
        return "model"
    return None


def name_attributes(name: str) -> dict:
    """A terméknév tulajdonságai: `capacity` (szám és egység, ahogy a névben áll, pl.
    „3,5 kW”), `phase` (1–3), `energy_class` (A+++); ami nincs, kimarad."""
    tokens = name.split()
    found: dict = {}
    for i, raw in enumerate(tokens):
        kind = token_kind(tokens, i)
        token = _bare(raw)
        if kind == "capacity" and "capacity" not in found:
            match = CAPACITY.match(token)
            found["capacity"] = (f"{match.group(1)} {match.group(2)}" if match
                                 else f"{token} {_bare(tokens[i + 1])}")
        elif kind == "phase" and "phase" not in found:
            found["phase"] = int(token[0])
        elif kind == "energy_class" and "energy_class" not in found:
            found["energy_class"] = token
    return found


def strip_brand(name: str, brand: str | None) -> tuple[list[str], bool]:
    """A név tokenjei a név eleji márka nélkül (kis-nagybetű nélkül), és hogy a név a márkával
    kezdődött-e."""
    tokens = name.split()
    brand_tokens = (brand or "").lower().split()
    if brand_tokens and [t.lower() for t in tokens[:len(brand_tokens)]] == brand_tokens:
        return tokens[len(brand_tokens):], True
    return tokens, False


def base_tokens(tokens: Sequence[str]) -> list[str]:
    """A tokenek az első tulajdonság- vagy típuskód-token (`FAMILY_STOP`) előtt."""
    for i in range(len(tokens)):
        if token_kind(tokens, i) in FAMILY_STOP:
            return list(tokens[:i])
    return list(tokens)


def families(names: dict[int, str], brand: str | None) -> dict[int, tuple[str, ...]]:
    """Egy márka termékei (azonosító → név) → a család kulcsa (kisbetűs tokenek, a márka
    nélkül); a család nélküli termék kimarad. Lásd a modul leírását."""
    stripped = {pid: strip_brand(name, brand)[0] for pid, name in names.items()}
    found: dict[int, tuple[str, ...]] = {}
    bases = {pid: tuple(t.lower() for t in base_tokens(tokens))
             for pid, tokens in stripped.items()}
    shared = Counter(bases.values())
    for pid, key in bases.items():
        if key and shared[key] >= FAMILY_MIN_PRODUCTS:
            found[pid] = key
    rest = {pid: [t.lower() for t in tokens] for pid, tokens in stripped.items()
            if pid not in found}
    for pid, tokens in rest.items():
        for k in range(len(tokens) - 1, 0, -1):
            prefix = tuple(tokens[:k])
            peers = [p for p, other in rest.items() if tuple(other[:k]) == prefix
                     and len(other) > k]
            following = {rest[p][k] for p in peers}
            if len(peers) >= FAMILY_MIN_PRODUCTS and len(following) > 1 \
                    and all(re.search(r"\d", t) for t in following):
                found[pid] = prefix
                break
    return found


def family_name(key: tuple[str, ...], names: Sequence[str], brand: str | None) -> str:
    """A család neve a tagok H1-ében álló leggyakoribb alak (a márkával, ha a H1 azzal kezdődik;
    azonos számnál az első)."""
    forms: Counter[str] = Counter()
    for name in names:
        tokens, branded = strip_brand(name, brand)
        head = name.split()[:len(name.split()) - len(tokens)] if branded else []
        forms[" ".join([*head, *tokens[:len(key)]]).strip(",; ")] += 1
    return forms.most_common(1)[0][0]


# ---------------------------------------------------------------------------
# a futás
# ---------------------------------------------------------------------------


def run_shop(ctx: _Context, merger: Merger, config: SiteConfig) -> ShopRun:
    """A webshop-szintek (lásd a modul leírását); termékoldal nélküli site-on üres. A `shop`
    forrású kapcsolatok minden futásban újraépülnek."""
    from aaa2.entities.site import Name, _page_mentions, _write_aliases

    run = ShopRun()
    ctx.con.execute("DELETE FROM entity_relations WHERE source = 'shop'")
    kinds = page_types(ctx.con, config.page_types)
    if "product" not in kinds.values():
        return run
    trails = breadcrumbs(ctx.con)
    pages_of = {kind: [ctx.roles[p] for p, k in kinds.items() if k == kind and p in ctx.roles]
                for kind in ("category", "brand_category", "product")}

    # kategóriák
    category_groups: dict[str, list[PageInfo]] = defaultdict(list)
    for info in pages_of["category"]:
        category_groups[page_url(info.url)].append(info)
    ancestors = [{page_url(u) for _, u in trails.get(info.page_id, [])[:-1] if u}
                 for info in pages_of["category"]]
    roots = {key for key in category_groups
             if len(category_groups) > 1 and all(key in a for a, info in zip(
                 ancestors, pages_of["category"], strict=True) if page_url(info.url) != key)}
    category_ids: dict[str, int] = {}
    for key, members in sorted(category_groups.items()):
        if key in roots:
            continue
        rep = min(members, key=lambda m: m.page_id)
        trail = trails.get(rep.page_id, [])
        canonical = (rep.h1 or (trail[-1][0] if trail else "") or rep.url).strip()
        names = [Name(canonical, "h1", rep.lang)]
        names += [Name(n, "nav", rep.lang) for trail in trails.values() for n, u in trail
                  if u and page_url(u) == key and n != canonical]
        entity_id = _anchored_entity(ctx, merger, members, "concept", "category", canonical,
                                     names)
        _write_aliases(ctx.con, entity_id, names)
        for info in members:
            _page_mentions(ctx, entity_id, info, names)
        category_ids[key] = entity_id
        run.categories += 1

    # termékek, márkájukkal és kategóriájukkal
    brand_pages = {page_url(info.url): info for info in pages_of["brand_category"]}
    products: list[Product] = []
    for info in sorted(pages_of["product"], key=lambda i: i.page_id):
        group = [m.page_id for m in ctx.groups.get(info.group, [info])]
        row = ctx.con.execute(
            "SELECT entity_id, name FROM entities WHERE list_contains(?, anchor_page_id) "
            "AND type = 'product' ORDER BY entity_id", [group]).fetchone()
        if row is None:
            continue
        trail = trails.get(info.page_id, [])
        brand = _schema_brand(ctx, info)
        if brand is None and trail and trail[-1][1] and page_url(trail[-1][1]) in brand_pages:
            listing = brand_pages[page_url(trail[-1][1])]
            brand = (listing.h1 or trail[-1][0]).strip()
        category = next((page_url(u) for _, u in reversed(trail)
                         if u and page_url(u) in category_ids), None)
        products.append(Product(row[0], info.page_id, (info.h1 or row[1]).strip(), brand,
                                category))
    seen: set[int] = set()
    products = [p for p in products if not (p.entity_id in seen or seen.add(p.entity_id))]
    known = sorted({p.brand for p in products if p.brand}, key=lambda b: -len(b.split()))
    products = [p if p.brand else replace(p, brand=next(
        (b for b in known if strip_brand(p.name, b)[1]), None)) for p in products]
    run.products = len(products)

    # márkák
    by_brand: dict[str, list[Product]] = defaultdict(list)
    for product in products:
        if product.brand:
            by_brand[alias_key(product.brand)].append(product)
        else:
            run.unbranded.append(product.name)
    brand_ids: dict[str, int] = {}
    for key, members in sorted(by_brand.items()):
        listing_forms = sorted({p.brand for p in members if p.brand})
        branded = Counter(" ".join(p.name.split()[:len(listing_forms[0].split())])
                          for p in members if strip_brand(p.name, listing_forms[0])[1])
        canonical = branded.most_common(1)[0][0] if branded else listing_forms[0]
        brand_ids[key] = _brand_entity(ctx, merger, key, canonical, listing_forms)
        run.brands += 1
        for info in pages_of["brand_category"]:
            if alias_key((info.h1 or "").strip()) == key:
                _page_mentions(ctx, brand_ids[key], info,
                               [Name(f, "nav", info.lang) for f in listing_forms])
        for product in members:
            if strip_brand(product.name, canonical)[1]:
                _h1_mention(ctx, brand_ids[key], product.page_id, canonical)

    # termék-tulajdonságok, kategória
    for product in products:
        attributes = name_attributes(product.name)
        ctx.con.execute("UPDATE entities SET subtype = 'variant', attributes = ? "
                        "WHERE entity_id = ?",
                        [json.dumps(attributes, ensure_ascii=False) if attributes else None,
                         product.entity_id])
        run.with_attributes += bool(attributes)
        if product.category is not None:
            _relate(ctx, product.entity_id, category_ids[product.category], "in_category",
                    {"breadcrumb": product.category})

    # termékcsaládok
    for key, members in sorted(by_brand.items()):
        brand_id = brand_ids[key]
        brand_name = members[0].brand
        found = families({p.entity_id: p.name for p in members}, brand_name)
        grouped: dict[tuple[str, ...], list[Product]] = defaultdict(list)
        for product in members:
            if product.entity_id in found:
                grouped[found[product.entity_id]].append(product)
            else:
                _relate(ctx, brand_id, product.entity_id, "brand_of", {"family": None})
        for family_key, group in sorted(grouped.items()):
            name = family_name(family_key, [p.name for p in group], brand_name)
            family_id = _family_entity(ctx, name, group)
            run.families += 1
            _relate(ctx, brand_id, family_id, "brand_of", {"brand": brand_name})
            for product in group:
                _relate(ctx, product.entity_id, family_id, "part_of",
                        {"family": " ".join(family_key)})
                _h1_mention(ctx, family_id, product.page_id, name)
                run.in_family += 1
    return run


def _anchored_entity(ctx: _Context, merger: Merger, members: list[PageInfo], kind: str,
                     subtype: str, canonical: str, names: list) -> int:
    """A csoporthoz kötött entitás: a meglévő (a csoport oldalához kötött, vagy azonos típusú
    és kulcsú) entitások egybe olvasztva (`page_identity`), különben új."""
    page_ids = [m.page_id for m in members]
    keys = {alias_key(n.text) for n in names} - {""}
    found = []
    for entity_id, name, aliases, anchor in ctx.con.execute(
            "SELECT entity_id, name, aliases, anchor_page_id FROM entities WHERE type = ? "
            "ORDER BY entity_id", [kind]).fetchall():
        own = anchor is not None and anchor in page_ids
        if own or (anchor is None and {alias_key(f) for f in [name, *(aliases or [])]} & keys):
            found.append((not own, entity_id, name))
    rep = min(members, key=lambda m: m.page_id)
    if found:
        found.sort()
        entity_id = found[0][1]
        for _, other, name in found[1:]:
            merger.merge(entity_id, other, "page_identity",
                         {"pages": page_ids, "name": name, "canonical": canonical})
    else:
        (entity_id,) = ctx.con.execute(
            "INSERT INTO entities (name, lang, type, subtype, aliases, source, created_at) "
            "VALUES (?, ?, ?, ?, [], 'rule', ?) RETURNING entity_id",
            [canonical, rep.lang or ctx.site_lang, kind, subtype, merger.clock()]).fetchone()
    forms = sorted({n.text for n in names} - {canonical})
    ctx.con.execute(
        "UPDATE entities SET name = ?, type = ?, subtype = ?, anchor_page_id = ?, "
        "lang = coalesce(lang, ?), aliases = list_distinct(list_filter(list_concat("
        "coalesce(aliases, []), ?), x -> x <> ?)) WHERE entity_id = ?",
        [canonical, kind, subtype, rep.page_id, rep.lang or ctx.site_lang, forms, canonical,
         entity_id])
    return entity_id


def _brand_entity(ctx: _Context, merger: Merger, key: str, canonical: str,
                  forms: list[str]) -> int:
    """A márka entitása: az azonos kulcsú brand- vagy org-entitások egybe olvasztva
    (`shop_brand`), a típus brand; ha nincs ilyen, új."""
    found = []
    for entity_id, name, aliases, kind in ctx.con.execute(
            "SELECT entity_id, name, aliases, type FROM entities WHERE type IN ('brand', 'org') "
            "AND anchor_page_id IS NULL ORDER BY entity_id").fetchall():
        if key in {alias_key(f) for f in [name, *(aliases or [])]}:
            found.append((kind != "brand", entity_id, name))
    if found:
        found.sort()
        entity_id = found[0][1]
        for _, other, name in found[1:]:
            merger.merge(entity_id, other, "shop_brand", {"brand": canonical, "name": name})
    else:
        (entity_id,) = ctx.con.execute(
            "INSERT INTO entities (name, lang, type, aliases, source, created_at) "
            "VALUES (?, ?, 'brand', [], 'rule', ?) RETURNING entity_id",
            [canonical, ctx.site_lang, merger.clock()]).fetchone()
    aliases = sorted(set(forms) - {canonical})
    ctx.con.execute(
        "UPDATE entities SET name = ?, type = 'brand', aliases = list_distinct(list_filter("
        "list_concat(coalesce(aliases, []), ?), x -> x <> ?)) WHERE entity_id = ?",
        [canonical, aliases, canonical, entity_id])
    ctx.con.executemany("INSERT INTO entity_aliases (entity_id, alias, lang, source) "
                        "VALUES (?, ?, NULL, 'nav') ON CONFLICT DO NOTHING",
                        [(entity_id, form) for form in forms])
    return entity_id


def _family_entity(ctx: _Context, name: str, group: list[Product]) -> int:
    """A termékcsalád (product / line) entitása: a meglévő, oldalhoz nem kötött azonos kulcsú
    line altípusú product, különben új."""
    key = alias_key(name)
    for entity_id, other, aliases in ctx.con.execute(
            "SELECT entity_id, name, aliases FROM entities WHERE type = 'product' "
            "AND subtype = 'line' AND anchor_page_id IS NULL ORDER BY entity_id").fetchall():
        if key in {alias_key(f) for f in [other, *(aliases or [])]}:
            return entity_id
    (entity_id,) = ctx.con.execute(
        "INSERT INTO entities (name, lang, type, subtype, aliases, source, created_at) "
        "VALUES (?, ?, 'product', 'line', [], 'rule', current_timestamp) RETURNING entity_id",
        [name, ctx.site_lang]).fetchone()
    return entity_id


def _h1_mention(ctx: _Context, entity_id: int, page_id: int, name: str) -> None:
    from aaa2.entities.site import _add_mention

    for (ordinal,) in ctx.con.execute(
            "SELECT ordinal FROM blocks WHERE page_id = ? AND region = 'content' "
            "AND kind = 'heading' AND level = 1 ORDER BY ordinal", [page_id]).fetchall():
        if _add_mention(ctx, entity_id, page_id, ordinal, name, "h1"):
            return


def _relate(ctx: _Context, from_id: int, to_id: int, kind: str, evidence: dict) -> None:
    ctx.con.execute(
        "INSERT INTO entity_relations (from_id, to_id, type, source, evidence) "
        "VALUES (?, ?, ?, 'shop', ?) ON CONFLICT DO NOTHING",
        [from_id, to_id, kind, json.dumps(evidence, ensure_ascii=False)])


def _schema_brand(ctx: _Context, info: PageInfo) -> str | None:
    """A termékoldal saját JSON-LD `Product` csomópontjának `brand`-je (név vagy szöveg)."""
    from aaa2.entities.site import _self_nodes

    for node in _self_nodes(ctx.con, [info]).get(info.page_id, []):
        brand = node.get("brand")
        if isinstance(brand, list):
            brand = brand[0] if brand else None
        if isinstance(brand, dict):
            brand = brand.get("name")
        if isinstance(brand, str) and brand.strip():
            return brand.strip()
    return None
