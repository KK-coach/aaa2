"""Webshop-entitásszintek (M2 spec, M2/6, 9a pont), LLM nélkül, a site-kör része (`run_shop`).

- Oldaltípus: `pages.page_types` (termék: az oldalra mutató JSON-LD `Product`; kategória és
  márka × kategória: a site-fájl `[page_types]` URL-mintái). Morzsamenü: `pages.breadcrumbs`.
- Kategória (concept / category, a kategóriaoldalhoz kötve): kategóriaoldal-csoportonként egy
  entitás, a neve a H1 (ha nincs, a morzsamenü utolsó eleme), aliasai a rá
  mutató morzsamenü-nevek. A gyökérkategória (minden más kategóriaoldal morzsamenüjében ős)
  kimarad. Az azonos kulcsú concept beolvad (`page_identity`); az oldalhoz nem kötött, nem
  variáns termék-entitás is, ha az írásmód-normalizált neve a kategória neve vagy aliasa
  (`shop_category`). Említés: title- és H1-blokk.
- Márka (brand): a termék saját JSON-LD `Product.brand`-je, ennek híján a morzsamenü utolsó
  eleme, ha márka × kategória oldalra mutat (az oldal H1-e); ha egyik sincs, az így ismert
  márkák közül az, amelyikkel a terméknév kezdődik (önálló előfordulás). Kanonikus alak: ha a márka
  a termékneveinek elején áll, az ottani leggyakoribb írásmód, különben a márkaoldalé. Az
  azonos kulcsú brand- vagy org-entitás beolvad (`shop_brand`). Említés: a márka × kategória
  oldalak H1-e és a termékek H1-ében a név eleji márka.
- Termék (az oldalhoz kötött product-entitás, `site._page_entities`): altípus `variant`,
  tulajdonságai (`attributes`) a terméknévből (`name_attributes`: teljesítmény, fázisszám,
  energiaosztály); `in_category` a morzsamenü legmélyebb kategóriaoldalához.
  A termék összesített értékelése (a saját `Product` csomópont `aggregateRating`-je) is attribútum:
  `rating_average`, `rating_count`.
- Termékcsalád (product / line), márkánként (`families`): a terméknév (H1, a név eleji márka
  nélkül) az első tulajdonság- vagy típuskód-token előtt (`FAMILY_STOP`); család, ha a márka
  legalább két terméke osztozik rajta (kis-nagybetű nélkül). A többi terméknél: a leghosszabb
  közös token-előtag legalább két termék között, ha a termékek utána eltérő, számjegyet
  tartalmazó tokennel folytatódnak („BlueSoft Eco” 12 / 18 / 25). A család neve a tagok H1-ében
  álló leggyakoribb alak (a márkával, ha a H1 azzal kezdődik). Ha a márka a termékneveinek
  elején áll, a márka nélküli családnév kanonikus alakja márkával kiegészítve, az eredeti alias.
- Egymásba ágyazott családok (`nest_families`): a hűtőközeg-jelölés (R32, R290) és a
  konfigurációs szavak („egységgel”, „szett”, „beltéri / kültéri egység”) előtt vágott név, ha
  legalább két családé közös, szülőcsalád (`family_parent`); ha egy család tokenjei egy másikéi
  előtt állnak, a hosszabb `part_of` a rövidebb (a leghosszabb ilyen). A család nélküli termék
  a nevét kezdő leghosszabb család tagja. Kapcsolat: termék `part_of` család, alcsalád
  `part_of` szülőcsalád, márka `brand_of` a legfelső család; család nélküli terméknél márka
  `brand_of` termék. Említés: a tagtermékek H1-ében a családnév.
- Kapcsolat nélküli márka (brand, `brand_of` nélkül, oldalhoz nem kötve): ha a neve a site neve
  (`site._site_name_keys`), a site-szervezet (org, role = brand) aliasa lesz
  (`orphan_brand_site`); különben, ha az írásmód-normalizált neve (`site.normal_key`, legalább
  `ORPHAN_MIN_CHARS` jel) egy termékcsalád vagy egy terméket hordozó márka nevével egyezik, vagy
  annak elő- vagy utótagja (a cél aliasai is), és egyetlen ilyen cél van, vagy a célok közül
  egy a többi szülőcsaládja, annak aliasa (`orphan_brand`; a pontos egyezés megelőzi a
  részlegest); egyébként marad, `orphan` jelöléssel (több cél esetén azok is a futás
  kimenetében).
- A title végén csonkolt site-név („… - DUEX Hung”): az oldalhoz nem kötött, csak title-
  említésű szervezet, amelynek a neve a site egyik nevének legalább `site.SITE_PREFIX_MIN` jeles
  eleje, de nem maga a név, a site-szervezetbe olvad (`site_name_cut`); a csonk nem lesz alias.
- A kapcsolatok forrása `shop`; a beolvasztások a `merge_log`-ban.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from aaa2.db.stable_json import dumps
from aaa2.entities import queries as extract_queries
from aaa2.entities import store
from aaa2.entities.rules import alias_key
from aaa2.resolver import queries as resolver_queries
from aaa2.resolver.overrides import SiteConfig
from aaa2.resolver.pages import PageInfo, breadcrumbs, page_types, page_url

if TYPE_CHECKING:
    from aaa2.resolver.context import _Context
    from aaa2.resolver.merge import Merger

UNITS = {"kw", "w", "m3/h", "m³/h"}
PHASE_WORDS = {"fázis", "f", "fázisú"}
CAPACITY = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(kw|w|m3/h|m³/h)$", re.IGNORECASE)
NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")
PHASE = re.compile(r"^([1-3])\s*(?:f|fázis|fázisú)$", re.IGNORECASE)
ENERGY = re.compile(r"^A\+{1,4}$")
MODEL_MIN_CHARS = 6
FAMILY_STOP = ("capacity", "phase", "energy_class", "model")
FAMILY_MIN_PRODUCTS = 2
ORPHAN_MIN_CHARS = 4
REFRIGERANT = re.compile(r"^R-?\d{2,4}[A-Za-z]?$")
CONFIG_WORDS = {"egységgel", "szett"}
UNIT_PLACES = {"beltéri", "kültéri"}


@dataclass
class ShopRun:
    categories: int = 0
    brands: int = 0
    families: int = 0
    products: int = 0
    in_family: int = 0
    parents: int = 0
    nested: int = 0
    with_attributes: int = 0
    unbranded: list[str] = field(default_factory=list)
    orphan_aliases: dict[str, str] = field(default_factory=dict)     # márka → cél
    orphans: dict[str, list[str]] = field(default_factory=dict)      # márka → jelölt célok
    category_products: list[str] = field(default_factory=list)       # kategóriába olvadt
    site_name_cuts: list[str] = field(default_factory=list)          # csonkolt site-nevek

    def as_dict(self) -> dict:
        return {"categories": self.categories, "brands": self.brands,
                "category_products": self.category_products,
                "site_name_cuts": self.site_name_cuts,
                "families": self.families, "products": self.products,
                "in_family": self.in_family, "parents": self.parents, "nested": self.nested,
                "with_attributes": self.with_attributes,
                "unbranded": self.unbranded, "orphan_aliases": self.orphan_aliases,
                "orphans": self.orphans}


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
    from aaa2.resolver.names import Name
    from aaa2.resolver.navigation import _page_mentions
    from aaa2.resolver.offers import _write_aliases

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
    _category_products(ctx, merger, run, sorted(set(category_ids.values())))

    # termékek, márkájukkal és kategóriájukkal
    brand_pages = {page_url(info.url): info for info in pages_of["brand_category"]}
    products: list[Product] = []
    ratings: dict[int, dict] = {}
    for info in sorted(pages_of["product"], key=lambda i: i.page_id):
        group = [m.page_id for m in ctx.groups.get(info.group, [info])]
        row = store.entities_for_run_shop(ctx.con, group)
        if row is None:
            continue
        trail = trails.get(info.page_id, [])
        brand = _schema_brand(ctx, info)
        ratings.setdefault(row[0], _schema_rating(ctx, info))
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
    brand_names: dict[str, str] = {}
    for key, members in sorted(by_brand.items()):
        listing_forms = sorted({p.brand for p in members if p.brand})
        branded = Counter(" ".join(p.name.split()[:len(listing_forms[0].split())])
                          for p in members if strip_brand(p.name, listing_forms[0])[1])
        canonical = branded.most_common(1)[0][0] if branded else listing_forms[0]
        brand_ids[key] = _brand_entity(ctx, merger, key, canonical, listing_forms)
        brand_names[key] = canonical
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
        attributes = {**name_attributes(product.name), **ratings.get(product.entity_id, {})}
        store.update_entities_in_run_shop(ctx.con, dumps(attributes, ensure_ascii=False) if attributes else None, product.entity_id)
        run.with_attributes += bool(attributes)
        if product.category is not None:
            _relate(ctx, product.entity_id, category_ids[product.category], "in_category",
                    {"breadcrumb": product.category})

    # termékcsaládok
    family_ids: list[int] = []
    for key, members in sorted(by_brand.items()):
        brand_id, brand = brand_ids[key], brand_names[key]
        brand_name = members[0].brand
        prefixed = any(strip_brand(p.name, brand)[1] for p in members)
        found = families({p.entity_id: p.name for p in members}, brand_name)
        grouped: dict[tuple[str, ...], list[Product]] = defaultdict(list)
        loose: list[Product] = []
        for product in members:
            if product.entity_id in found:
                grouped[found[product.entity_id]].append(product)
            else:
                loose.append(product)
        lines: list[Line] = []
        for family_key, group in sorted(grouped.items()):
            name = family_name(family_key, [p.name for p in group], brand_name)
            canonical = (f"{brand} {name}" if prefixed and not strip_brand(name, brand)[1]
                         else name)
            family_id = _family_entity(ctx, canonical, [name])
            lines.append(Line(family_id, canonical, name, group))
            for product in group:
                _relate(ctx, product.entity_id, family_id, "part_of",
                        {"family": " ".join(family_key)})
                _h1_mention(ctx, family_id, product.page_id, name)
                run.in_family += 1
        for parent_name, h1_forms, children in family_parents(lines):
            parent_id = _family_entity(ctx, parent_name, [])
            group = [p for child in children for p in child.products]
            lines.append(Line(parent_id, parent_name, parent_name, group))
            run.parents += 1
            for product in group:
                _h1_mention(ctx, parent_id, product.page_id, h1_forms[product.entity_id])
        parent_of = nest_families([(line.entity_id, line.name) for line in lines])
        for line in lines:
            family_ids.append(line.entity_id)
            run.families += 1
            if line.entity_id in parent_of:
                _relate(ctx, line.entity_id, parent_of[line.entity_id], "part_of",
                        {"nested": line.name})
                run.nested += 1
            else:
                _relate(ctx, brand_id, line.entity_id, "brand_of", {"brand": brand_name})
        names = [(line.entity_id, line.name) for line in lines]
        for product in loose:
            full = (product.name if not prefixed or strip_brand(product.name, brand)[1]
                    else f"{brand} {product.name}")
            owner = longest_prefix(full, names)
            if owner is None:
                _relate(ctx, brand_id, product.entity_id, "brand_of", {"family": None})
            else:
                _relate(ctx, product.entity_id, owner, "part_of", {"name_prefix": full})
                run.in_family += 1
    _site_name_cuts(ctx, merger, run)
    _orphan_brands(ctx, merger, run, sorted(set(brand_ids.values())), family_ids)
    return run


def _category_products(ctx: _Context, merger: Merger, run: ShopRun,
                       category_ids: list[int]) -> None:
    """Az oldalhoz nem kötött, nem variáns és nem család termék-entitás, amelynek írásmód-
    normalizált neve egy kategória neve vagy aliasa, a kategóriába olvad (`shop_category`)."""
    from aaa2.resolver.names import normal_key

    con = ctx.con
    keys: dict[str, int] = {}
    for entity_id, name, aliases in store.entities_for_category_products(con, category_ids):
        for form in [name, *(aliases or [])]:
            if normal_key(form or ""):
                keys.setdefault(normal_key(form), entity_id)
    for entity_id, name in store.entities_for_category_products_2(con):
        target = keys.get(normal_key(name or ""))
        if target is not None:
            merger.merge(target, entity_id, "shop_category", {"product": name})
            run.category_products.append(name)


def _site_name_cuts(ctx: _Context, merger: Merger, run: ShopRun) -> None:
    """A title végén csonkolt site-név (lásd a modul leírását): a site-szervezetbe olvad, a
    csonk nem marad alias."""
    from aaa2.resolver.names import _site_name_keys, site_name_form

    con = ctx.con
    site_org = store.entities_for_site_name_cuts_2(con)[0]
    if site_org is None:
        return
    keys = _site_name_keys(con)
    for entity_id, name in store.entities_for_site_name_cuts(con, site_org):
        if alias_key(name) in keys or not site_name_form(name, keys):
            continue
        merger.merge(site_org, entity_id, "site_name_cut", {"name": name})
        store.update_entities_in_site_name_cuts(con, name, site_org)
        con.execute("DELETE FROM entity_aliases WHERE entity_id = ? AND alias = ? "
                    "AND source = 'merge'", [site_org, name])
        run.site_name_cuts.append(name)


@dataclass
class Line:
    """Egy termékcsalád a futásban: entitás, kanonikus név, a H1-beli alak és a tagjai."""

    entity_id: int
    name: str
    h1_name: str
    products: list[Product]


def trim_family(tokens: Sequence[str]) -> list[str]:
    """A családnév tokenjei az első hűtőközeg-jelölés (R32, R290) vagy konfigurációs szó
    („egységgel”, „szett”, „beltéri / kültéri egység…”) előtt."""
    for i, token in enumerate(tokens):
        low = _bare(token).lower()
        following = _bare(tokens[i + 1]).lower() if i + 1 < len(tokens) else ""
        if REFRIGERANT.match(_bare(token)) or low in CONFIG_WORDS or (
                low in UNIT_PLACES and following.startswith("egység")):
            return list(tokens[:i])
    return list(tokens)


def family_parents(lines: Sequence[Line]) -> list[tuple[str, dict[int, str], list[Line]]]:
    """A közös szülőcsaládok: a vágott név (`trim_family`), ha legalább két családé közös,
    rövidebb náluk, és nincs már ilyen nevű család. (név, termék → a H1-beli alak, alcsaládok)."""
    existing = {alias_key(line.name) for line in lines}
    groups: dict[str, list[tuple[Line, str, str]]] = defaultdict(list)
    for line in lines:
        tokens, h1_tokens = line.name.split(), line.h1_name.split()
        kept = trim_family(tokens)
        if 0 < len(kept) < len(tokens):
            cut = len(tokens) - len(kept)
            h1_kept = " ".join(h1_tokens[:len(h1_tokens) - cut])
            groups[alias_key(" ".join(kept))].append((line, " ".join(kept), h1_kept))
    found = []
    for key, members in sorted(groups.items()):
        if len(members) < FAMILY_MIN_PRODUCTS or key in existing or not key:
            continue
        name = Counter(n for _, n, _ in members).most_common(1)[0][0]
        forms = {p.entity_id: h1 for line, _, h1 in members for p in line.products}
        found.append((name, forms, [line for line, _, _ in members]))
    return found


def nest_families(lines: Sequence[tuple[int, str]]) -> dict[int, int]:
    """Család → a szülője: a leghosszabb másik család, amelynek tokenjei (`alias_key`) a
    család tokenjei előtt állnak."""
    tokens = {entity_id: alias_key(name).split() for entity_id, name in lines}
    parents = {}
    for entity_id, mine in tokens.items():
        best = None
        for other, theirs in tokens.items():
            if other != entity_id and len(theirs) < len(mine) and mine[:len(theirs)] == theirs \
                    and (best is None or len(theirs) > len(tokens[best])):
                best = other
        if best is not None:
            parents[entity_id] = best
    return parents


def longest_prefix(name: str, lines: Sequence[tuple[int, str]]) -> int | None:
    """A család, amelynek tokenjei a név elején állnak (a leghosszabb), vagy None."""
    mine = alias_key(name).split()
    best, size = None, 0
    for entity_id, other in lines:
        theirs = alias_key(other).split()
        if size < len(theirs) < len(mine) and mine[:len(theirs)] == theirs:
            best, size = entity_id, len(theirs)
    return best


def orphan_target(name: str, targets: Sequence[tuple[int, str]]) -> tuple[list[int], str]:
    """A kapcsolat nélküli márka lehetséges céljai (azonosító, név) közül az egyezők:
    (azonosítók, egyezés). A pontos egyezés (`equal`) megelőzi az elő- vagy utótagot
    (`affix`); `ORPHAN_MIN_CHARS`-nál rövidebb normalizált névnek nincs célja."""
    from aaa2.resolver.names import normal_key

    key = normal_key(name)
    if len(key) < ORPHAN_MIN_CHARS:
        return [], "none"
    equal = [i for i, target in targets if normal_key(target) == key]
    if equal:
        return equal, "equal"
    return [i for i, target in targets if normal_key(target).startswith(key)
            or normal_key(target).endswith(key)], "affix"


def _orphan_brands(ctx: _Context, merger: Merger, run: ShopRun, brand_ids: list[int],
                   family_ids: list[int]) -> None:
    """A kapcsolat nélküli márkák: a site-szervezet vagy egy termékcsalád, illetve terméket
    hordozó márka aliasa, vagy `orphan` jelölés (lásd a modul leírását)."""
    from aaa2.resolver.flags import _set_flag
    from aaa2.resolver.names import _site_name_keys

    con = ctx.con
    names, targets = {}, []
    for entity_id, name, aliases in store.entities_for_category_products(con, brand_ids + family_ids):
        names[entity_id] = name
        targets += [(entity_id, form) for form in [name, *(aliases or [])]]
    parent_of = dict(con.execute("SELECT from_id, to_id FROM entity_relations WHERE type = "
                                 "'part_of' AND source = 'shop' AND list_contains(?, from_id) ORDER BY ALL",
                                 [family_ids]).fetchall())
    site_org = store.entities_for_site_name_cuts_2(con)[0]
    site_keys = _site_name_keys(con)
    orphans: list[int] = []
    for entity_id, name in store.unanchored_brands(
            con, brand_ids, resolver_queries.relation_from_ids(con, "brand_of")):
        if site_org is not None and alias_key(name) in site_keys:
            merger.merge(site_org, entity_id, "orphan_brand_site", {"brand": name})
            run.orphan_aliases[name] = store.entities_for_type_split(con, site_org)[0]
            continue
        found, match = orphan_target(name, targets)
        found = sorted(set(found))
        if len(found) > 1:
            common = [i for i in found if all(o == i or i in _ancestors(o, parent_of)
                                              for o in found)]
            found, match = (common, f"{match}, szülőcsalád") if len(common) == 1 else (
                found, match)
        if len(found) == 1:
            merger.merge(found[0], entity_id, "orphan_brand",
                         {"brand": name, "target": names[found[0]], "match": match})
            run.orphan_aliases[name] = names[found[0]]
            continue
        orphans.append(entity_id)
        run.orphans[name] = sorted(names[i] for i in found)
    _set_flag(con, "orphan", orphans)


def _ancestors(entity_id: int, parent_of: dict[int, int]) -> set[int]:
    found = set()
    while entity_id in parent_of and parent_of[entity_id] not in found:
        entity_id = parent_of[entity_id]
        found.add(entity_id)
    return found


def _anchored_entity(ctx: _Context, merger: Merger, members: list[PageInfo], kind: str,
                     subtype: str, canonical: str, names: list) -> int:
    """A csoporthoz kötött entitás: a meglévő (a csoport oldalához kötött, vagy azonos típusú
    és kulcsú) entitások egybe olvasztva (`page_identity`), különben új."""
    page_ids = [m.page_id for m in members]
    keys = {alias_key(n.text) for n in names} - {""}
    found = []
    for entity_id, name, aliases, anchor in store.entities_for_anchored_entity(ctx.con, kind):
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
        (entity_id,) = store.insert_entities_in_page_entities(ctx.con, canonical, rep.lang or ctx.site_lang, kind, subtype, merger.clock())
    forms = sorted({n.text for n in names} - {canonical})
    store.update_entities_in_anchored_entity(ctx.con, canonical, kind, subtype, rep.page_id, rep.lang or ctx.site_lang, forms, canonical, entity_id)
    return entity_id


def _brand_entity(ctx: _Context, merger: Merger, key: str, canonical: str,
                  forms: list[str]) -> int:
    """A márka entitása: az azonos kulcsú brand- vagy org-entitások egybe olvasztva
    (`shop_brand`), a típus brand; ha nincs ilyen, új."""
    found = []
    for entity_id, name, aliases, kind in store.entities_for_brand_entity(ctx.con):
        if key in {alias_key(f) for f in [name, *(aliases or [])]}:
            found.append((kind != "brand", entity_id, name))
    if found:
        found.sort()
        entity_id = found[0][1]
        for _, other, name in found[1:]:
            merger.merge(entity_id, other, "shop_brand", {"brand": canonical, "name": name})
    else:
        (entity_id,) = store.insert_entities_in_brand_entity(ctx.con, canonical, ctx.site_lang, merger.clock())
    aliases = sorted(set(forms) - {canonical})
    store.update_entities_in_brand_entity(ctx.con, canonical, aliases, canonical, entity_id)
    ctx.con.executemany("INSERT INTO entity_aliases (entity_id, alias, lang, source) "
                        "VALUES (?, ?, NULL, 'nav') ON CONFLICT DO NOTHING",
                        [(entity_id, form) for form in forms])
    return entity_id


def _family_entity(ctx: _Context, name: str, forms: Sequence[str]) -> int:
    """A termékcsalád (product / line) entitása a `name` kanonikus névvel és a `forms`
    aliasokkal: a meglévő, oldalhoz nem kötött line altípusú product, amelynek neve vagy
    aliasa egyezik a nevek valamelyikével (kulcs szerint), különben új."""
    keys = {alias_key(f) for f in [name, *forms]}
    aliases = sorted({f for f in forms if f != name})
    found = next((entity_id for entity_id, other, known in store.entities_for_family_entity(ctx.con)
        if keys & {alias_key(f) for f in [other, *(known or [])]}), None)
    if found is None:
        (found,) = store.insert_entities_in_family_entity(ctx.con, name, ctx.site_lang)
    store.update_entities_in_family_entity(ctx.con, name, aliases, name, found)
    if aliases:
        ctx.con.executemany("INSERT INTO entity_aliases (entity_id, alias, lang, source) "
                            "VALUES (?, ?, NULL, 'nav') ON CONFLICT DO NOTHING",
                            [(found, form) for form in aliases])
    return found


def _h1_mention(ctx: _Context, entity_id: int, page_id: int, name: str) -> None:
    from aaa2.resolver.navigation import _add_mention

    for ordinal in [b.ordinal for b in extract_queries.page_blocks(ctx.con, page_id)
                    if b.region == "content" and b.kind == "heading" and b.level == 1]:
        if _add_mention(ctx, entity_id, page_id, ordinal, name, "h1"):
            return


def _relate(ctx: _Context, from_id: int, to_id: int, kind: str, evidence: dict) -> None:
    ctx.con.execute(
        "INSERT INTO entity_relations (from_id, to_id, type, source, evidence) "
        "VALUES (?, ?, ?, 'shop', ?) ON CONFLICT DO NOTHING",
        [from_id, to_id, kind, dumps(evidence, ensure_ascii=False)])


def _schema_rating(ctx: _Context, info: PageInfo) -> dict:
    """A termékoldal saját `Product` csomópontjának összesített értékelése (`aggregateRating`):
    `rating_average` (a `ratingValue`, számként) és `rating_count` (a `reviewCount`, ennek híján
    a `ratingCount`, egészként); ami nincs vagy nem szám, kimarad."""
    from aaa2.resolver.offers import _self_nodes

    for node in _self_nodes(ctx.con, [info]).get(info.page_id, []):
        rating = node.get("aggregateRating")
        if isinstance(rating, list):
            rating = rating[0] if rating else None
        if not isinstance(rating, dict):
            continue
        found: dict = {}
        average = _number(rating.get("ratingValue"))
        count = _number(rating.get("reviewCount"))
        if count is None:
            count = _number(rating.get("ratingCount"))
        if average is not None:
            found["rating_average"] = average
        if count is not None and count == int(count):
            found["rating_count"] = int(count)
        if found:
            return found
    return {}


def _number(value: object) -> float | None:
    if isinstance(value, dict):
        value = value.get("@value")
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return None


def _schema_brand(ctx: _Context, info: PageInfo) -> str | None:
    """A termékoldal saját `Product` csomópontjának (JSON-LD vagy microdata) `brand`-je (név
    vagy szöveg)."""
    from aaa2.resolver.offers import _self_nodes

    for node in _self_nodes(ctx.con, [info]).get(info.page_id, []):
        brand = node.get("brand")
        if isinstance(brand, list):
            brand = brand[0] if brand else None
        if isinstance(brand, dict):
            brand = brand.get("name")
        if isinstance(brand, str) and brand.strip():
            return brand.strip()
    return None
