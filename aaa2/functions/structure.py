"""A site belső struktúrája: a tárolt menüfa és az oldalak mélységei, LLM nélkül.

**Menüfa (`build_menu`, a gráf építésének része).** Az oldalankénti menüpontokból
(`crawl.menu_entries`: fejléc, lábléc, oldalsáv a tárolt DOM-ból) nyelvenként és területenként
egy site-szintű fa készül a `menu_items` táblába:

- a fa egy menüpontja az, amelyik a nyelv azon oldalainak legalább felén (`MENU_ITEM_SHARE`;
  az oldalsávban legalább ötödén) szerepel, amelyeken a terület egyáltalán áll. Az oldalsáv
  oldaltípusonként más (a webshop kategóriafája csak az oldalak egy részén áll), ezért ott
  kisebb rész is elég; a fejléc minden oldalon ott van. A canonical-duplikátum oldal nem
  számít;
- a menüpont szülője az, amelyik alatt a legtöbb oldalon áll; a horgonyszövege a leggyakoribb;
  a sorrend az oldalakon mért átlagos hely szerinti; a szint a szülőlánc hossza (0 = felső
  szint);
- a **nyelvváltó link** kimarad: a cél a készlet más nyelvű oldala, és az oldalon nincs
  gyereke (különben minden oldal fája más lenne, mert a váltó az oldal saját fordítására mutat);
- a link nélküli szülő-címke (`#` című lenyíló) menüpont, cím és oldal nélkül.

Az oldalfüggő eltérések a `menu_page_differences` táblában állnak: melyik oldalon melyik
menüpont többlet (`extra`: az oldalon áll, a fában nem) vagy hiány (`missing`: a fában áll, az
oldalon nem), területenként; csak azokra az oldalakra, amelyeken a terület áll. Hiánynak csak az
a menüpont számít, amelyik a területet hordozó oldalak legalább felén áll (az oldalsáv
kisebbségi változata nem mindenhol várt).

**Mélységek (`page_structure`, a nézetek építésekor).** Oldalanként, a canonical-duplikátumok
nélkül:

- kattintási mélység a saját nyelvű kezdőoldaltól (`site.home_urls`; ha a nyelvnek nincs
  kezdőoldala, a site kiinduló oldalától), szélességi bejárással a menümásolat nélküli belső
  linkeken (`crawl.counted_links`), három változatban: minden link / csak menüterület (a link
  pozíciója nav, aside vagy footer) / csak tartalom (body). A canonical-duplikátum forrása és
  célja az eredeti oldalra számít. Az el nem ért oldal mélysége üres;
- menüszint a nyelv fejléc-fájában, és szerepel-e a lábléc-, illetve az oldalsáv-fában;
- morzsa-szint és morzsa-szülő: az oldal első `BreadcrumbList` eleme (JSON-LD vagy microdata)
  `position` szerint; ha nincs, a látható morzsa (`crawl.visible_breadcrumbs`). Az utolsó elem
  az oldal maga, ha nincs címe vagy a címe az oldalé; a szint az előtte álló elemek száma, a
  szülő az utolsó előtte álló elem;
- URL-szint: az útvonal nem üres szakaszainak száma; URL-szülő: a leghosszabb útvonalú oldal
  ugyanazon a hoston, amelynek az útvonala `/`-határon előtagja (lekérdezéses című oldal nem
  szülő).

Az URL-szülő csak **hierarchikus URL-szerkezetű site-on** számít (`url_hierarchy`): a nem
kezdőoldalak legalább `URL_DEEP_SHARE` része áll legalább két útvonal-szakaszon (a nyelvi
előtag nem számít szakasznak), és ezeknek legalább `URL_PARENT_SHARE` részénél a szülő útvonalon
létező, nem kezdőoldal áll. Lapos URL-nél az URL-szülő nem értelmezhető, és a szülők
összevetésébe nem számít. Hierarchikus site-on is csak annál az oldalnál számít, amely maga
legalább két útvonal-szakaszon áll (`url_parent_applicable`): a vegyes szerkezetű site
egyszakaszos oldalainál (a webshop gyökér alatti termékei) az URL nem mond szülőt.

A szülők összevetése (`parents_agree`): a menüfa, a morzsa és (hierarchikus site-on) az URL
szerinti szülő közül azok, amelyek az oldalra megvannak; ha legalább kettő van, egyeznek vagy
nem. A felső szintű menüpont menü-szülője a nyelv kezdőoldala. A link nélküli szülő-címke
önálló szülő; egyezik viszont a morzsa-szülővel, ha annak a neve a címkéé (kis- és nagybetű,
ékezet és írásjel nélkül összevetve), és a morzsában ugyanazon a szinten áll, mint a címke a
menüben (a morzsa kezdőoldal-eleme nem számít szintnek). A más nyelv kezdőoldalára mutató
morzsa-szülő az összevetésben az oldal saját nyelvű kezdőoldalának számít (ez a hiba a
`breadcrumb_foreign_home` megállapításé, nem szülő-eltérés).

`inbound`: oldalanként a rá belső linkkel mutató többi oldal (minden link; a
canonical-duplikátum az eredetire számít)."""
from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import duckdb

from aaa2.contracts import MenuDifference, MenuItem
from aaa2.engine import queries as crawl
from aaa2.engine.menu import AREAS, label_key, url_key
from aaa2.entities.rules import alias_key
from aaa2.functions import graph_queries
from aaa2.resolver.pages import canonical_key

# a menüpont a területet hordozó oldalak ekkora részén áll
MENU_ITEM_SHARE = {"header": 0.5, "footer": 0.5, "sidebar": 0.2}
MENU_LINK_POSITIONS = ("nav", "aside", "footer")
CONTENT_LINK_POSITION = "body"
URL_DEEP_SHARE = 0.2            # a nem kezdőoldalak ekkora része áll legalább két szakaszon
URL_PARENT_SHARE = 0.5          # … és ezek ekkora részénél létező oldal áll a szülő útvonalon
DEPTH_BUCKETS = ("0", "1", "2", "3", "4+", "elérhetetlen")
AREA_LABELS = {"header": "fejléc", "footer": "lábléc", "sidebar": "oldalsáv"}
TOP_LEVEL = "(felső szint)"
_LANGUAGE_SEGMENT = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,4})?$", re.IGNORECASE)
_NOT_NAME = re.compile(r"[^0-9a-z]+")


def name_key(text: str) -> str:
    """A név az összevetéshez: kisbetűvel, ékezet, írásjel és szóköz nélkül."""
    return _NOT_NAME.sub("", alias_key(text or ""))


@dataclass
class MenuRun:
    items: Counter = field(default_factory=Counter)           # (nyelv, terület) → menüpont
    differences: int = 0


def _rows(con: duckdb.DuckDBPyConnection, query: str) -> list[dict]:
    cursor = con.execute(query)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def menu_items(con: duckdb.DuckDBPyConnection) -> list[MenuItem]:
    """A site-szintű menüfa menüpontjai, nyelv, terület és sorrend szerint."""
    return [MenuItem.from_row(row) for row in _rows(
        con, "SELECT * FROM menu_items ORDER BY item_id")]


def menu_differences(con: duckdb.DuckDBPyConnection) -> list[MenuDifference]:
    """Az oldalfüggő eltérések a site-szintű menüfától, oldal, terület és fajta szerint."""
    return [MenuDifference.from_row(row) for row in _rows(
        con, "SELECT * FROM menu_page_differences ORDER BY page_id, area, kind, url, anchor")]


def _most(counter: Counter):
    """A leggyakoribb érték; döntetlennél a szöveg szerint nagyobb."""
    return max(counter.items(), key=lambda item: (item[1], str(item[0])))[0]


def _level(parent_of: Mapping[str, str | None], key: str) -> int:
    depth, seen = 0, {key}
    while parent_of[key] is not None and parent_of[key] not in seen:
        key = parent_of[key]
        seen.add(key)
        depth += 1
    return depth


def build_menu(con: duckdb.DuckDBPyConnection) -> MenuRun:
    """A `menu_items` és a `menu_page_differences` tábla újraépítése a tárolt DOM menüpontjaiból
    és az oldal-csomópontokból (lásd a modul leírását)."""
    con.execute("DELETE FROM menu_page_differences")
    con.execute("DELETE FROM menu_items")
    run = MenuRun()
    nodes = graph_queries.page_nodes(con)
    originals = {node.page_id: node for node in nodes if node.canonical_page is None}
    by_key = {canonical_key(node.url): node.canonical_page or node.page_id for node in nodes}

    def target(url: str | None) -> int | None:
        found = by_key.get(canonical_key(url)) if url else None
        return found if found in originals else None

    # oldal → terület → kulcs → (cím, horgony, szülő kulcsa, jelölés), a dokumentum sorrendjében
    pages: dict[int, dict[str, dict[str, tuple]]] = {}
    for page_id, entries in sorted(crawl.menu_entries(con).items()):
        if page_id not in originals:
            continue
        lang = originals[page_id].lang or ""
        found: dict[str, dict[str, tuple]] = {area: {} for area in AREAS}
        for entry in entries:
            key = url_key(entry.url) if entry.url else label_key(entry.anchor)
            parent = None if entry.parent is None else entry.parent if entry.parent_is_label \
                else url_key(entry.parent)
            found[entry.area][key] = (entry.url, entry.anchor, parent, entry.marker)
        for items in found.values():
            parents = {item[2] for item in items.values()}
            for key in [k for k, item in items.items() if item[0]]:
                other = target(items[key][0])
                if other is not None and (originals[other].lang or "") != lang \
                        and key not in parents:
                    del items[key]                           # nyelvváltó link
        pages[page_id] = found

    rows = []
    differences = []
    for lang in sorted({originals[page_id].lang or "" for page_id in pages}):
        own = [page_id for page_id in pages if (originals[page_id].lang or "") == lang]
        for area in AREAS:
            carrying = [page_id for page_id in own if pages[page_id][area]]
            if not carrying:
                continue
            count: Counter[str] = Counter()
            parents: dict[str, Counter] = defaultdict(Counter)
            anchors: dict[str, Counter] = defaultdict(Counter)
            markers: dict[str, Counter] = defaultdict(Counter)
            urls: dict[str, str | None] = {}
            places: dict[str, list[int]] = defaultdict(list)
            for page_id in carrying:
                for place, (key, (url, anchor, parent, marker)) in enumerate(
                        pages[page_id][area].items()):
                    count[key] += 1
                    parents[key][parent] += 1
                    anchors[key][anchor] += 1
                    markers[key][marker] += 1
                    urls.setdefault(key, url)
                    places[key].append(place)
            kept = {key for key, pages_with in count.items()
                    if pages_with >= MENU_ITEM_SHARE[area] * len(carrying)}
            parent_of: dict[str, str | None] = {}
            for key in kept:
                parent = _most(parents[key])
                parent_of[key] = parent if parent in kept and parent != key else None
            ordered = sorted(kept, key=lambda key: (sum(places[key]) / len(places[key]), key))
            for ordinal, key in enumerate(ordered):
                rows.append({"lang": lang, "area": area, "ordinal": ordinal, "key": key,
                             "url": urls[key], "page_id": target(urls[key]),
                             "anchor": _most(anchors[key]), "parent": parent_of[key],
                             "level": _level(parent_of, key), "marker": _most(markers[key]),
                             "pages": count[key], "area_pages": len(carrying)})
            run.items[(lang, area)] = len(ordered)
            for page_id in carrying:
                items = pages[page_id][area]
                for key in sorted(set(items) - kept):
                    url, anchor, parent, _ = items[key]
                    differences.append((page_id, lang, area, "extra", url or key, anchor,
                                        parent))
                for key in sorted(kept - set(items)):
                    if count[key] < MENU_ITEM_SHARE["header"] * len(carrying):
                        continue                # kisebbségi változat: nem mindenhol várt
                    differences.append((page_id, lang, area, "missing", urls[key] or key,
                                        _most(anchors[key]), parent_of[key]))
    ids = {(row["lang"], row["area"], row["key"]): number
           for number, row in enumerate(rows, 1)}
    for number, row in enumerate(rows, 1):
        con.execute(
            "INSERT INTO menu_items (item_id, lang, area, ordinal, url, page_id, anchor, "
            "parent_id, level, marker, pages, area_pages) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?)",
            [number, row["lang"], row["area"], row["ordinal"], row["url"], row["page_id"],
             row["anchor"], ids.get((row["lang"], row["area"], row["parent"])), row["level"],
             row["marker"], row["pages"], row["area_pages"]])
    for row in sorted(differences, key=lambda r: tuple("" if v is None else str(v) for v in r)):
        con.execute("INSERT INTO menu_page_differences (page_id, lang, area, kind, url, anchor, "
                    "parent) VALUES (?, ?, ?, ?, ?, ?, ?)", list(row))
    run.differences = len(differences)
    return run


# ---------------------------------------------------------------------------
# mélységek
# ---------------------------------------------------------------------------


def crumb_trail(data: object) -> list[tuple[str | None, str]]:
    """A `BreadcrumbList` elemei `position` szerint: (cím vagy None, név)."""
    elements = data.get("itemListElement") if isinstance(data, dict) else None
    if isinstance(elements, dict):
        elements = [elements]
    rows = []
    for index, element in enumerate(elements or []):
        if not isinstance(element, dict):
            continue
        item = element.get("item")
        url = item if isinstance(item, str) else None
        name = element.get("name")
        if isinstance(item, dict):
            url = item.get("@id") or item.get("url")
            name = name or item.get("name")
        try:
            position = float(element.get("position"))
        except (TypeError, ValueError):
            position = float(index + 1)
        rows.append((position, index, url if isinstance(url, str) else None, str(name or "")))
    return [(url, name) for _, _, url, name in sorted(rows)]


def _bfs(start: int | None, edges: Mapping[int, set[int]]) -> dict[int, int]:
    if start is None:
        return {}
    depth, queue = {start: 0}, deque([start])
    while queue:
        page = queue.popleft()
        for other in sorted(edges.get(page, ())):
            if other not in depth:
                depth[other] = depth[page] + 1
                queue.append(other)
    return depth


def _folder(url: str) -> str:
    path = urlsplit(url).path
    return path if path.endswith("/") else path + "/"


def depth_bucket(depth: int | None) -> str:
    return "elérhetetlen" if depth is None else str(depth) if depth < 4 else "4+"


@dataclass
class SiteStructure:
    """A site struktúrája a nézetekhez: oldalanként a mélységek és a szülők (`pages`), a
    nyelvek kezdőoldala, az URL-hierarchia mért feltétele, a morzsák és a tárolt menüfa."""

    pages: dict[int, dict]
    homes: dict[str, int]
    start: int | None
    url_hierarchy: dict
    crumbs: dict[int, tuple[str, list[tuple[str | None, str]]]]
    menu: list[MenuItem]
    inbound: dict[int, set[int]] = field(default_factory=dict)

    def distribution(self) -> list[dict]:
        """Nyelvenként a kattintási mélység eloszlása (minden link): a nyelv, a kiindulás
        oldala és vödrönként az oldalszám."""
        found: dict[str, Counter] = defaultdict(Counter)
        for page in self.pages.values():
            found[page["lang"]][depth_bucket(page["depth"]["all"])] += 1
        return [{"lang": lang, "home": self.homes.get(lang, self.start),
                 "counts": {bucket: counts[bucket] for bucket in DEPTH_BUCKETS}}
                for lang, counts in sorted(found.items())]


def page_structure(con: duckdb.DuckDBPyConnection, pages: Mapping[int, Mapping]
                   ) -> SiteStructure:
    """Az oldalak mélységei és szülői (lásd a modul leírását). `pages`: oldal → {`url`, `lang`,
    `canonical`} a készlet minden oldalára (a canonical-duplikátummal együtt)."""
    nodes = {page_id: page for page_id, page in pages.items() if page["canonical"] is None}
    by_key = {canonical_key(page["url"]): page["canonical"] or page_id
              for page_id, page in pages.items()}

    def resolve(url: str | None) -> int | None:
        found = by_key.get(canonical_key(url)) if url else None
        return found if found in nodes else None

    def own(page_id: int | None) -> int | None:
        if page_id in pages:
            page_id = pages[page_id]["canonical"] or page_id
        return page_id if page_id in nodes else None

    profile = crawl.site(con)
    home_urls = list((profile.home_urls if profile else None) or [])
    if profile is not None and not home_urls:
        home_urls = [profile.seed_url]
    start = resolve(profile.seed_url) if profile is not None else None
    home_ids = [page_id for page_id in map(resolve, home_urls) if page_id is not None]
    homes: dict[str, int] = {}
    for page_id in home_ids:
        homes.setdefault(nodes[page_id]["lang"] or "", page_id)

    edges: dict[str, dict[int, set[int]]] = {kind: defaultdict(set)
                                             for kind in ("all", "menu", "body")}
    for link in crawl.counted_links(con):
        source, goal = own(link.from_page_id), own(link.to_page_id)
        if source is None or goal is None or source == goal:
            continue
        edges["all"][source].add(goal)
        if link.position in MENU_LINK_POSITIONS:
            edges["menu"][source].add(goal)
        elif link.position == CONTENT_LINK_POSITION:
            edges["body"][source].add(goal)
    depths = {home: {kind: _bfs(home, found) for kind, found in edges.items()}
              for home in {*homes.values(), *([start] if start is not None else [])}}

    menu = menu_items(con)
    by_id = {item.item_id: item for item in menu}
    tree: dict[tuple[str, str], dict[int, MenuItem]] = defaultdict(dict)
    for item in menu:
        if item.page_id is not None:
            tree[(item.lang, item.area)].setdefault(item.page_id, item)

    crumbs: dict[int, tuple[str, list[tuple[str | None, str]]]] = {}
    for item in crawl.schema_items(con):
        if (item.type or "").rsplit("/", 1)[-1] != "BreadcrumbList" \
                or item.page_id not in nodes or item.page_id in crumbs:
            continue
        trail = crumb_trail(item.data)
        if trail:
            crumbs[item.page_id] = (f"schema ({item.syntax})", trail)
    for page_id, trail in crawl.visible_breadcrumbs(
            con, [page_id for page_id in nodes if page_id not in crumbs]).items():
        crumbs[page_id] = ("látható morzsa", trail)

    languages = {(page["lang"] or "").split("-")[0].lower() for page in nodes.values()} - {""}
    plain = {page_id: page["url"] for page_id, page in nodes.items()
             if not urlsplit(page["url"]).query}

    def url_parent(page_id: int) -> int | None:
        parts = urlsplit(nodes[page_id]["url"])
        mine = _folder(nodes[page_id]["url"])
        best = None
        for other, url in plain.items():
            their = _folder(url)
            if other == page_id or urlsplit(url).netloc != parts.netloc:
                continue
            if mine.startswith(their) and (their != mine or parts.query) \
                    and (best is None or len(their) > len(_folder(plain[best]))):
                best = other
        return best

    def segments(url: str) -> list[str]:
        return [part for part in urlsplit(url).path.split("/") if part]

    home_set = set(home_ids) | ({start} if start is not None else set())
    counted = deep = with_parent = 0
    url_parents = {}
    deep_pages: set[int] = set()
    for page_id, page in nodes.items():
        url_parents[page_id] = url_parent(page_id)
        parts = segments(page["url"])
        if parts and _LANGUAGE_SEGMENT.match(parts[0]) \
                and parts[0].split("-")[0].lower() in languages:
            parts = parts[1:]
        if len(parts) >= 2:
            deep_pages.add(page_id)
        if page_id in home_set:
            continue
        counted += 1
        if page_id in deep_pages:
            deep += 1
            with_parent += url_parents[page_id] is not None \
                and url_parents[page_id] not in home_set
    hierarchical = counted > 0 and deep >= URL_DEEP_SHARE * counted \
        and deep > 0 and with_parent >= URL_PARENT_SHARE * deep
    url_hierarchy = {"hierarchical": hierarchical, "pages": counted, "deep": deep,
                     "with_parent": with_parent}

    found: dict[int, dict] = {}
    for page_id, page in nodes.items():
        lang = page["lang"] or ""
        home = homes.get(lang, start)
        reached = depths.get(home, {})
        depth = {kind: reached.get(kind, {}).get(page_id) for kind in ("all", "menu", "body")}
        is_home = page_id in home_set
        parents: dict[str, object] = {}
        item = tree[(lang, "header")].get(page_id)
        menu_parent = label = None
        if item is not None:
            above = by_id.get(item.parent_id) if item.parent_id is not None else None
            if above is None:
                menu_parent = TOP_LEVEL
                if not is_home and home is not None:
                    parents["menu"] = home
            elif above.url is None:
                menu_parent = label_key(above.anchor)
                parents["menu"] = menu_parent
                label = above
            else:
                menu_parent = above.url
                parents["menu"] = above.page_id if above.page_id is not None \
                    else canonical_key(above.url)
        crumb_level = crumb_parent = crumb_source = None
        if page_id in crumbs:
            crumb_source, trail = crumbs[page_id]
            last = trail[-1][0]
            before = trail[:-1] if last is None \
                or canonical_key(last) == canonical_key(page["url"]) else trail
            crumb_level = len(before)
            if before:
                parent_url, parent_name = before[-1]
                if parent_url is None:
                    crumb_parent = f"(link nélküli elem: {parent_name})"
                    parents["crumb"] = crumb_parent
                else:
                    crumb_parent = parent_url
                    target = resolve(parent_url)
                    if target in home_set and target != home and home is not None:
                        target = home               # más nyelv kezdőoldala
                    parents["crumb"] = target or canonical_key(parent_url)
                first_home = resolve(before[0][0]) in home_set
                if label is not None and name_key(parent_name) == name_key(label.anchor) \
                        and len(before) - first_home == item.level:
                    parents["menu"] = parents["crumb"]
        applicable = hierarchical and page_id in deep_pages
        above_url = url_parents[page_id] if applicable else None
        if above_url is not None:
            parents["url"] = above_url
        agree = None
        if not is_home and len(parents) >= 2:
            agree = len({str(value) for value in parents.values()}) == 1
        found[page_id] = {
            "lang": lang, "depth": depth, "unreachable": depth["all"] is None,
            "menu_level": item.level if item is not None else None,
            "menu_parent": menu_parent,
            "in_footer": page_id in tree[(lang, "footer")],
            "in_sidebar": page_id in tree[(lang, "sidebar")],
            "crumb_source": crumb_source, "crumb_level": crumb_level,
            "crumb_parent": crumb_parent,
            "url_level": len(segments(page["url"])),
            "url_parent": nodes[above_url]["url"] if above_url is not None else None,
            "url_parent_applicable": applicable,
            "parent_sources": sorted(parents), "parents_agree": agree}
    inbound: dict[int, set[int]] = defaultdict(set)
    for source, goals in edges["all"].items():
        for goal in goals:
            inbound[goal].add(source)
    return SiteStructure(pages=found, homes=homes, start=start, url_hierarchy=url_hierarchy,
                         crumbs=crumbs, menu=menu, inbound=dict(inbound))
