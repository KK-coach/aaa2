"""A puha típusok (concept, service) kapuja a kinyerés után, LLM nélkül.

- Tétel: az oldal azonos kanonikus kulcsú (`alias_key`) említései; a típusa az első említésé.
  Csak a concept és a service típusú tétel megy át a kapun, a többi érintetlen. A kitalált
  említés (a szöveg szerinti alak nincs a blokkban) nem számít.
- A tétel neve a kanonikus neve; a szöveg szerinti alak nem alias (lehet a névnek csak egy
  része), a modell aliast nem ad. Előfordulás: a név kulcsa a normalizált (`alias_key`)
  szövegben szókezdettől; 3 karakternél hosszabb névnél rag követheti, rövidebbnél csak teljes
  szóként.
- Feltételek:
  - szerkezet: a tételnek van említése, vagy a neve előfordul title, heading, card vagy
    table_row típusú blokkban, a chrome-régió (navigáció) szövegében, vagy egy anchor-szövegben;
  - ismétlődés: legalább 2 különböző tartalmi blokk (az említések blokkjai és a név
    előfordulásai együtt);
  - tudásbázis: a kanonikus név pontosan (kulcs szerint) egyezik
    egy Wikidata-címkével vagy -aliasszal (`wbsearchentities`, szigorú nyelv), vagy egy
    Wikipedia-címmel vagy átirányítással, az oldal nyelvén vagy angolul; egyértelműsítő lap
    nem számít.
- A concept marad, ha legalább egy feltétel teljesül; a service csak a szerkezettel.
- A tudásbázis-kérések a `validate` gyorsítótárán és naplóján át mennek (`validation_cache`,
  `validation_calls`); ismételt futásnál nincs újrahívás.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from aaa2.entities.blocks import check_surface
from aaa2.entities.rules import alias_key
from aaa2.llm.schemas import BlockEntity

SOFT_TYPES = ("concept", "service")
STRUCTURAL_KINDS = ("title", "heading", "card", "table_row")
PROMINENT_KINDS = ("title", "heading")
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKI_API = "https://{lang}.wikipedia.org/w/api.php"
DISAMBIGUATION = ("disambiguation", "egyértelműsítő")


@dataclass
class SoftItem:
    """Egy concept- vagy service-tétel az oldalon."""

    key: str
    canonical: str
    type: str
    mentions: list[Mapping] = field(default_factory=list)

    @property
    def blocks(self) -> list[str]:
        return list(dict.fromkeys(m["block_id"] for m in self.mentions))

    def names(self) -> list[str]:
        """A keresett nevek: a kanonikus név."""
        return [self.canonical] if alias_key(self.canonical) else []


def soft_items(entities: Iterable[Mapping], blocks: Mapping[str, Mapping]) -> list[SoftItem]:
    """A concept- és service-tételek az első említésük sorrendjében."""
    groups: dict[str, SoftItem] = {}
    for raw in entities:
        if not check_surface(BlockEntity.model_construct(**raw), blocks):
            continue
        key = alias_key(raw["canonical_name"])
        item = groups.setdefault(key, SoftItem(key, raw["canonical_name"], raw["type"]))
        item.mentions.append(raw)
    return [item for item in groups.values() if item.type in SOFT_TYPES]


def occurs(name: str, text: str) -> bool:
    """A `name` kulcsa szókezdettől áll a `text` kulcsában (hosszabb névnél rag követheti)."""
    needle = alias_key(name)
    if not needle:
        return False
    tail = "" if len(needle) > 3 else r"(?![^\W\d_])"
    return re.search(rf"(?<![^\W\d_]){re.escape(needle)}{tail}", alias_key(text)) is not None


@dataclass(frozen=True)
class PageContext:
    """Az oldal kapuhoz kellő része: a tartalmi blokkok (`id`, `kind`, `text`), a chrome-régió
    szövegei és az anchor-szövegek."""

    blocks: Sequence[Mapping]
    chrome: Sequence[str] = ()
    anchors: Sequence[str] = ()
    lang: str = "en"

    def by_id(self) -> dict[str, Mapping]:
        return {b["id"]: b for b in self.blocks}


def structure(item: SoftItem, page: PageContext, kinds: Sequence[str] = STRUCTURAL_KINDS
              ) -> str | None:
    """Ahol a tétel szerkezeti helyen áll (`kind:<blokk>`, `nav`, `anchor`), vagy None."""
    by_id = page.by_id()
    for block_id in item.blocks:
        if (by_id.get(block_id) or {}).get("kind") in kinds:
            return f"{by_id[block_id]['kind']}:{block_id}"
    names = item.names()
    for block in page.blocks:
        if block.get("kind") in kinds and any(occurs(n, block["text"]) for n in names):
            return f"{block['kind']}:{block['id']}"
    if kinds != STRUCTURAL_KINDS:
        return None
    if any(occurs(n, text) for text in page.chrome for n in names):
        return "nav"
    if any(occurs(n, text) for text in page.anchors for n in names):
        return "anchor"
    return None


def repetition(item: SoftItem, page: PageContext) -> int:
    """A különböző tartalmi blokkok száma, ahol a tétel szerepel."""
    names = item.names()
    found = set(item.blocks)
    found |= {b["id"] for b in page.blocks if any(occurs(n, b["text"]) for n in names)}
    return len(found)


@dataclass(frozen=True)
class GateDecision:
    key: str
    canonical: str
    type: str
    mentions: int
    structure: str | None
    blocks: int
    knowledge: str | None
    keep: bool

    @property
    def repeated(self) -> bool:
        return self.blocks >= 2


def decide(item: SoftItem, page: PageContext,
           knowledge: Callable[[Sequence[str], str], str | None]) -> GateDecision:
    """A tétel feltételei és a döntés; a tudásbázist minden concept-tételre megkérdezi (a
    feltételenkénti mérés miatt), a service-tételre nem."""
    where = structure(item, page)
    count = repetition(item, page)
    found = knowledge(item.names(), page.lang) if item.type == "concept" else None
    keep = where is not None if item.type == "service" else bool(
        where is not None or count >= 2 or found)
    return GateDecision(item.key, item.canonical, item.type, len(item.mentions), where, count,
                        found, keep)


def gate_record(record: Mapping, page: PageContext,
                knowledge: Callable[[Sequence[str], str], str | None]
                ) -> tuple[dict, list[GateDecision]]:
    """A rekord a kapu után (a kiesett tételek minden említése nélkül) és a döntések."""
    decisions = [decide(item, page, knowledge)
                 for item in soft_items(record.get("entities") or [], page.by_id())]
    dropped = {d.key for d in decisions if not d.keep}
    out = dict(record)
    out["entities"] = [raw for raw in record.get("entities") or []
                       if alias_key(raw["canonical_name"]) not in dropped]
    return out, decisions


# ---------------------------------------------------------------------------
# tudásbázis: Wikidata és Wikipedia
# ---------------------------------------------------------------------------


def base_language(lang: str | None) -> str:
    return (lang or "en").split("-")[0].lower() or "en"


def wikidata_hit(body: Mapping | None, name: str, lang: str) -> dict | None:
    """Az első találat (`id`, `match`: label / alias, `text`), amelynek a címkéje vagy aliasa
    (`lang` nyelven) a kulcs szerint egyezik a névvel, és nem egyértelműsítő lap. Ha ez csak
    alias-egyezés, a `rivals` azoknak a további elemeknek a QID-je, amelyeknek a név a címkéje
    (a név elsődleges jelentése lehet ott, pl. „forgalom”: a bevétel aliasa, a közlekedési
    forgalom címkéje)."""
    key = alias_key(name)
    found = []
    for hit in (body or {}).get("search") or []:
        match = hit.get("match") or {}
        if match.get("type") not in ("label", "alias") or match.get("language") != lang:
            continue
        if alias_key(match.get("text") or "") != key:
            continue
        if any(word in (hit.get("description") or "").lower() for word in DISAMBIGUATION):
            continue
        found.append({"id": hit.get("id"), "match": match["type"], "text": match.get("text")})
    if not found:
        return None
    first = found[0]
    rivals = [hit["id"] for hit in found[1:] if hit["match"] == "label"]
    if first["match"] == "alias" and rivals:
        return {**first, "rivals": rivals}
    return first


def wikidata_match(body: Mapping | None, name: str, lang: str) -> str | None:
    hit = wikidata_hit(body, name, lang)
    return hit["id"] if hit else None


def wikipedia_page(body: Mapping | None) -> dict | None:
    """A lekérdezett címek közül az első létező, nem egyértelműsítő lap (`title`, és
    `redirect`: átirányításon át ért-e oda)."""
    query = (body or {}).get("query") or {}
    redirected = {r.get("to") for r in query.get("redirects") or []}
    for page in query.get("pages") or []:
        if page.get("missing") or page.get("invalid"):
            continue
        if "disambiguation" in (page.get("pageprops") or {}):
            continue
        return {"title": page.get("title"), "redirect": page.get("title") in redirected}
    return None


def wikipedia_title(body: Mapping | None) -> str | None:
    page = wikipedia_page(body)
    return page["title"] if page else None


def title_variants(name: str) -> list[str]:
    """A név, és ha más, a kis kezdőbetű utáni része kisbetűvel (a Wikipedia a kezdőbetűt maga
    normalizálja)."""
    lower = name[:1] + name[1:].lower()
    return list(dict.fromkeys([name, lower]))


class KnowledgeBase:
    """A (c) feltétel: `get(service, url, params)` → (kulcs, válasz) kérő, pl. a
    `validate._Api` gyorsítótárral. `failures`: a válasz nélküli (hibás) kérések száma."""

    def __init__(self, get: Callable[[str, str, list[tuple[str, str]]], tuple[str, dict | None]]):
        self.get = get
        self.failures = 0

    def _get(self, service: str, url: str, params: list[tuple[str, str]]) -> dict | None:
        _, body = self.get(service, url, params)
        if body is None:
            self.failures += 1
        return body

    def wikidata(self, name: str, code: str) -> dict | None:
        body = self._get("wikidata", WIKIDATA_API, [
            ("action", "wbsearchentities"), ("format", "json"), ("search", name),
            ("language", code), ("strictlanguage", "1"), ("type", "item"), ("limit", "10")])
        return wikidata_hit(body, name, code)

    def classes(self, qid: str) -> tuple[list[str], str] | None:
        """A Wikidata-elem „instance of” (P31) osztályainak angol címkéi és az angol leírása;
        None, ha a kérés hibás."""
        body = self._get("wikidata", WIKIDATA_API, [
            ("action", "wbgetentities"), ("format", "json"), ("ids", qid),
            ("props", "claims|descriptions"), ("languages", "en")])
        if body is None:
            return None
        entity = (body.get("entities") or {}).get(qid) or {}
        description = ((entity.get("descriptions") or {}).get("en") or {}).get("value") or ""
        ids = []
        for claim in (entity.get("claims") or {}).get("P31") or []:
            value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
            if isinstance(value, dict) and value.get("id"):
                ids.append(value["id"])
        if not ids:
            return [], description
        labels = self._get("wikidata", WIKIDATA_API, [
            ("action", "wbgetentities"), ("format", "json"), ("ids", "|".join(ids[:50])),
            ("props", "labels"), ("languages", "en")])
        if labels is None:
            return None
        names = [((e.get("labels") or {}).get("en") or {}).get("value") or ""
                 for e in (labels.get("entities") or {}).values()]
        return [n for n in names if n], description

    def wikipedia(self, name: str, code: str) -> dict | None:
        body = self._get("wikipedia", WIKI_API.format(lang=code), [
            ("action", "query"), ("format", "json"), ("formatversion", "2"),
            ("titles", "|".join(title_variants(name))), ("redirects", "1"),
            ("prop", "pageprops"), ("ppprop", "disambiguation"), ("maxlag", "5")])
        return wikipedia_page(body)

    def __call__(self, names: Sequence[str], lang: str) -> str | None:
        langs = list(dict.fromkeys([base_language(lang), "en"]))
        for name in names:
            for code in langs:
                if found := self.wikidata(name, code):
                    return f"wikidata:{code}:{found['id']} ({name})"
            for code in langs:
                if found := self.wikipedia(name, code):
                    return f"wikipedia:{code}:{found['title']} ({name})"
        return None
