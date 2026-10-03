"""Tudásbázis-kapcsolás a site entitásaira (M2 spec, M2/6, 8. pont, minimális rész).

- Kapcsolható típus: `LINK_TYPES` (concept, tech, org). Soha nincs automatikus kapcsolás:
  személy, `api_symbol` altípus, demó-jelölés, `MIN_NAME_CHARS`-nál rövidebb név; ezeknél és a
  többi típusnál a link törlődik, a státusz none. A rövid nevű (concept, tech, org) entitás
  csak megerősítéssel olvad össze: ha a találata egy hosszabb nevű, azonos típusú entitás
  biztos QID-je (`wikidata_short_name`).
- Egyezés: a név pontos Wikidata-címkéje vagy aliasa (`gate.wikidata_hit`) az entitás (ha nincs,
  a site) nyelvén, majd angolul. A kétértelmű találat legfeljebb probable: csak alias-egyezés,
  miközben egy másik, a típussal nem összeférhetetlen elemnek a név a címkéje (`_ambiguous`).
- Típus-kompatibilitás: a találat „instance of” osztályainak angol címkéi és az angol leírása
  (`gate.KnowledgeBase.classes`). Összeférhetetlen (→ none), ha egy osztálycímkében egész
  szóként áll egy `INCOMPATIBLE` kifejezés (a „gene” nem illeszkedik a „generative”-re), vagy
  ha egy osztálycímke fő szava (a címke maga vagy az utolsó szava) a típus `TYPE_INCOMPATIBLE`
  kifejezése; a „type of …” kezdetű címke osztályok osztálya, nem számít (a „business model” és
  a „type of business or company” nem szervezet, a „public company” az). A `tech` típusnál a
  szervezet-osztály csak akkor zár ki, ha az elemben nincs technológiai jel (`COMPATIBLE` az
  osztálycímkékben vagy a leírásban): a cégnevű platform (Shopify, Cloudflare) a cég elemére
  kapcsolódik, az „API” nevű olajcégre nem. Ha nem összeférhetetlen, és kompatibilis kifejezést
  tartalmaz (`COMPATIBLE`, az osztálycímkékben vagy a leírásban) → confident; különben probable.
- Tartalék keresés, ha az első találat összeférhetetlen (`_fallback`), ebben a sorrendben:
  1. hosszabb alak: az entitás aliasai és a mozaikszó feloldása a site szövegéből
     (`acronym_expansions`: „Generative Engine Optimization (GEO)”, „GEO (Generative Engine
     Optimization)”), legfeljebb `FALLBACK_FORMS`; a hosszabb alak pontos, nem kétértelmű
     találata, ha nem összeférhetetlen, a kapcsolás (a saját státuszával);
  2. a név további pontos találatai (minden keresett nyelven, legfeljebb `FALLBACK_HITS`): ha
     pontosan egy nem összeférhetetlen marad, és az kompatibilis → probable (több jelöltnél a
     név kétértelmű: a „Gemini” protokoll, csevegőrobot és modellcsalád is, ezért none);
  3. a név Wikipedia-szócikkének eleme (pontos cím, nem egyértelműsítő lap), ha kompatibilis →
     probable.
  A 2. és a 3. út csak tulajdonnév-jellegű típusnál (`NAME_FALLBACK_TYPES`: tech, org) él: a
  köznévi fogalomnál (backlog, nurture, coaching, COP) a név másik jelentését adná.
- A Wikipedia-link (a korábbi címegyezés) csak confident és probable státusznál marad.
- Technológiai osztály: a biztos QID-jű fogalom, amelynek QID-je egy technológiai osztály
  (`TECH_CLASSES`: software, software framework, …), tech típust kap (`type_changed_from`), az
  összevonás előtt.
- Összevonás (7. pont, 4. lépés): az azonos típusú, azonos biztos (confident) QID-jű entitások
  egy entitás (`wikidata_confident`, a `merge_log`-ban, a legutóbbi site-kör futásához kötve).
- Ha egy kérés hibára fut, az entitás ellenőrizetlen marad (a következő futás újra kérdezi).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

import duckdb

from aaa2.engine import queries as crawl
from aaa2.entities import queries as extract_queries
from aaa2.entities import store
from aaa2.entities.gate import KnowledgeBase, base_language
from aaa2.entities.rules import alias_key
from aaa2.resolver import queries as resolver_queries
from aaa2.resolver.merge import Merger, _entity_rows, _mergeable, _rank, resolve

LINK_TYPES = ("concept", "tech", "org")
NO_LINK_SUBTYPES = ("api_symbol",)
MIN_NAME_CHARS = 3
INCOMPATIBLE = ("human", "given name", "family name", "surname", "disambiguation",
                "wikimedia", "fictional", "film", "album", "song", "single", "television",
                "video game", "musical group", "band", "taxon", "species", "painting", "novel",
                "literary work", "episode", "village", "city", "town", "municipality", "river",
                "mountain", "asteroid", "gene", "protein", "chemical compound", "musician",
                "cell line", "constellation", "astrological sign", "mapping relation")
TYPE_INCOMPATIBLE = {
    "concept": ("organization", "organisation", "company", "business", "software", "website"),
    "tech": ("organization", "organisation", "company", "business"),
    "org": ("software", "programming language", "field of study", "academic discipline"),
}
# Ennél a típusnál a `TYPE_INCOMPATIBLE` osztály csak akkor zár ki, ha az elemben nincs
# `COMPATIBLE` kifejezés (a cégnevű platform a cég elemére kapcsolódik).
RESCUED_TYPES = ("tech",)
# A név további találatai és a Wikipedia-szócikk csak ezeknél a típusoknál tartalék út.
NAME_FALLBACK_TYPES = ("tech", "org")
# A tartalék keresés korlátai: ennyi hosszabb alak és ennyi további találat kerül sorra.
FALLBACK_FORMS = 3
FALLBACK_HITS = 8
# Mozaikszó: 2–6 nagybetű vagy számjegy; a feloldás szavainak kezdőbetűi adják ki.
ACRONYM = re.compile(r"[A-ZÁÉÍÓÖŐÚÜŰ0-9]{2,6}")
_WORD = r"[^\W\d_][\w'’-]*"
_INCOMPATIBLE = re.compile(
    r"\b(?:" + "|".join(re.escape(word) for word in INCOMPATIBLE) + r")s?\b")
COMPATIBLE = {
    "concept": ("concept", "discipline", "field", "branch", "academic", "practice", "skill",
                "method", "technique", "process", "activity", "theory", "strategy", "metric",
                "measure", "type of", "profession", "phenomenon", "principle", "approach"),
    "tech": ("software", "framework", "library", "programming language", "technology",
             "protocol", "standard", "file format", "website", "service on internet",
             "online service", "platform", "application", "operating system", "markup language",
             "application programming interface", "database", "algorithm", "specification",
             "web browser", "analytics", "e-commerce", "search engine", "chatbot",
             "language model"),
    "org": ("company", "business", "organization", "organisation", "enterprise", "agency",
            "foundation", "university", "institution", "nonprofit", "corporation",
            "subsidiary", "online service"),
}
# software, software framework, application software, computer program, software library,
# service on Internet, web application, programming language, database management system,
# mobile app, web browser
TECH_CLASSES = frozenset({"Q7397", "Q271680", "Q166142", "Q40056", "Q188860", "Q1668024",
                          "Q189210", "Q9143", "Q176165", "Q620615", "Q6368"})


@dataclass(frozen=True)
class KnowledgeRun:
    entities: int
    confident: int
    probable: int
    none: int
    errors: int
    merged: int
    retyped: int = 0


def clear_person_links(con: duckdb.DuckDBPyConnection,
                       clock: Callable[[], datetime]) -> int:
    """A személyek automatikus Wikidata- és Wikipedia-kapcsolása ki van kapcsolva: a meglévő
    linkjük törlődik, a státuszuk none. Visszaad: hány személy változott."""
    rows = store.update_entities_in_clear_person_links(con, clock())
    return len(rows)


def status_of(kind: str, labels: list[str], description: str) -> str:
    """confident / probable / none a típus és a találat szerint: az összeférhetetlenség az
    osztálycímkékből, a kompatibilitás az osztálycímkékből és a leírásból (a leírás szövege
    más fogalmakat is említhet, pl. „…studies software…”)."""
    lowered = [label.lower().strip() for label in labels]
    if any(_INCOMPATIBLE.search(label) for label in lowered):
        return "none"
    text = f"{' | '.join(lowered)} | {description.lower()}"
    compatible = any(word in text for word in COMPATIBLE.get(kind, ()))
    if any(incompatible_label(kind, label) for label in lowered) \
            and not (kind in RESCUED_TYPES and compatible):
        return "none"
    return "confident" if compatible else "probable"


def incompatible_label(kind: str, label: str) -> bool:
    """Egy (kisbetűs) osztálycímke összeférhetetlen-e a típussal: egész szóként áll benne egy
    `INCOMPATIBLE` kifejezés, vagy a fő szava (a címke maga vagy az utolsó szava) a típus
    `TYPE_INCOMPATIBLE` kifejezése, és a címke nem „type of …” kezdetű."""
    if _INCOMPATIBLE.search(label):
        return True
    if label.startswith("type of "):
        return False
    return any(label == word or label.endswith(f" {word}")
               for word in TYPE_INCOMPATIBLE.get(kind, ()))


def acronym_expansions(name: str, texts: Iterable[str]) -> list[str]:
    """A mozaikszó feloldásai a szövegekből, gyakoriság szerint: a zárójeles mozaikszó előtti
    („Generative Engine Optimization (GEO)”) vagy a mozaikszó utáni zárójeles („GEO (Generative
    Engine Optimization)”) szavak, ha annyi szó, ahány jel, és a kezdőbetűik a mozaikszót adják.
    Nem mozaikszóra üres."""
    name = name.strip()
    if not ACRONYM.fullmatch(name):
        return []
    count = len(name)
    words = rf"({_WORD}(?:[ \u00a0]+{_WORD}){{{count - 1}}})"
    patterns = (re.compile(rf"{words}[ \u00a0]*\([ \u00a0]*{re.escape(name)}[ \u00a0]*\)"),
                re.compile(rf"\b{re.escape(name)}[ \u00a0]*\([ \u00a0]*{words}[ \u00a0]*\)"))
    found: Counter[str] = Counter()
    shown: dict[str, str] = {}
    for text in texts:
        if name not in text:
            continue
        for pattern in patterns:
            for match in pattern.finditer(text):
                form = " ".join(match.group(1).split())
                initials = "".join(word[0] for word in form.split())
                if initials.upper() == name.upper():
                    key = alias_key(form)
                    found[key] += 1
                    shown.setdefault(key, form)
    return [shown[key] for key, _ in sorted(found.items(), key=lambda item: (-item[1], item[0]))]


def link_entities(con: duckdb.DuckDBPyConnection, knowledge: KnowledgeBase,
                  clock: Callable[[], datetime], site_lang: str | None = None) -> KnowledgeRun:
    """A még státusz nélküli, említéssel bíró entitások kapcsolása (lásd a modul leírását), és
    az összevonás a biztos QID szerint. A keresés nyelvei: az entitásé, a `site_lang`, a site
    nyelvei, végül angol."""
    clear_person_links(con, clock)
    site = crawl.site(con)
    site_langs = [base_language(code) for code in (site.languages if site else None) or []]
    short: list[tuple[int, str, list[str]]] = []
    rows = store.entities_for_link_entities(con)
    counts = defaultdict(int)
    forms = _LongForms(con)
    for entity_id, name, kind, subtype, lang, flags in rows:
        codes = list(dict.fromkeys([base_language(lang or site_lang), base_language(site_lang),
                                    *site_langs, "en"]))
        if kind not in LINK_TYPES or subtype in NO_LINK_SUBTYPES or "demo" in (flags or []) \
                or len(name.strip()) < MIN_NAME_CHARS:
            _store(con, entity_id, None, None, "none", clock)
            counts["none"] += 1
            if kind in LINK_TYPES and subtype not in NO_LINK_SUBTYPES \
                    and "demo" not in (flags or []):
                short.append((entity_id, name, codes))
            continue
        before = knowledge.failures
        hit = next((found for code in codes if (found := knowledge.wikidata(name, code))), None)
        status, qid, wiki = "none", None, None
        if hit is not None:
            classes = knowledge.classes(hit["id"])
            if classes is not None:
                status = status_of(kind, *classes)
            if status == "confident" and _ambiguous(knowledge, kind, hit):
                status = "probable"
            title = name
            if status == "none":
                qid, status, title = _fallback(knowledge, kind, name, codes, hit["id"],
                                               forms.of(entity_id, name))
            else:
                qid = hit["id"]
            if status != "none" and title is not None:
                wiki = next((f"{code}:{page['title']}" for code in codes
                             if (page := knowledge.wikipedia(title, code))), None)
        if knowledge.failures > before:
            counts["errors"] += 1
            continue
        _store(con, entity_id, qid, wiki, status, clock)
        counts[status] += 1
    retyped = retype_tech_classes(con)
    merged = _merge_confident(con, clock, _corroborated(con, knowledge, short))
    return KnowledgeRun(len(rows), counts["confident"], counts["probable"], counts["none"],
                        counts["errors"], merged, retyped)


class _LongForms:
    """Az entitások hosszabb alakjai a tartalék kereséshez: az aliasok (`entity_aliases` és az
    entitás `aliases` oszlopa) és a mozaikszó feloldásai a site blokkjaiból; csak a névnél
    hosszabb, legalább kétszavas alak, a feloldások elöl. A blokkokat az első kérdésnél olvassa."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con
        self._aliases: dict[int, list[str]] | None = None
        self._texts: list[str] | None = None

    def of(self, entity_id: int, name: str) -> list[str]:
        if self._aliases is None:
            self._aliases = defaultdict(list)
            for alias in resolver_queries.aliases(self.con):
                self._aliases[alias.entity_id].append(alias.alias)
            for other_id, aliases in store.entity_alias_lists(self.con):
                self._aliases[other_id] += aliases
        expansions: list[str] = []
        if ACRONYM.fullmatch(name.strip()):
            if self._texts is None:
                self._texts = [block.text for block in extract_queries.blocks(self.con)]
            expansions = acronym_expansions(name, self._texts)
        own = sorted(set(self._aliases.get(entity_id, [])), key=lambda a: (-len(a), a))
        key = alias_key(name)
        found: dict[str, str] = {}
        for form in [*expansions, *own]:
            if len(form.split()) >= 2 and len(form) > len(name) and alias_key(form) != key:
                found.setdefault(alias_key(form), form)
        return list(found.values())[:FALLBACK_FORMS]


def _fallback(knowledge: KnowledgeBase, kind: str, name: str, codes: list[str], first: str,
              forms: list[str]) -> tuple[str | None, str, str | None]:
    """(QID, státusz, a Wikipedia-szócikk keresőneve) a tartalék keresésből, ha az első találat
    (`first`) összeférhetetlen (lásd a modul leírását); (None, "none", None), ha egyik út sem ad
    kapcsolást. A szócikk keresőneve a hosszabb alak, a Wikipedia-útnál a név; a további
    találatnál nincs (a név szócikke más jelentésé lehet). Hibás kérésnél a hívó a
    `knowledge.failures` alapján ellenőrizetlenül hagyja az entitást."""
    for form in forms:
        hit = next((found for code in codes if (found := knowledge.wikidata(form, code))), None)
        if hit is None or hit["id"] == first:
            continue
        classes = knowledge.classes(hit["id"])
        if classes is None:
            return None, "none", None
        status = status_of(kind, *classes)
        if status != "none" and not _ambiguous(knowledge, kind, hit):
            return hit["id"], status, form
    if kind not in NAME_FALLBACK_TYPES:
        return None, "none", None
    later: list[str] = []
    for code in codes:
        for hit in knowledge.wikidata_all(name, code) or []:
            if hit["id"] != first and hit["id"] not in later:
                later.append(hit["id"])
    open_hits: list[tuple[str, str]] = []
    for qid in later[:FALLBACK_HITS]:
        classes = knowledge.classes(qid)
        if classes is None:
            return None, "none", None
        status = status_of(kind, *classes)
        if status != "none":
            open_hits.append((qid, status))
    if len(open_hits) == 1 and open_hits[0][1] == "confident":
        return open_hits[0][0], "probable", None
    if open_hits:
        return None, "none", None
    for code in codes:
        qid = knowledge.wikipedia_item(name, code)
        if qid is None or qid == first:
            continue
        classes = knowledge.classes(qid)
        if classes is not None and status_of(kind, *classes) == "confident":
            return qid, "probable", name
        break
    return None, "none", None


def retype_tech_classes(con: duckdb.DuckDBPyConnection) -> int:
    """A biztos QID-jű fogalom, amely technológiai osztály (`TECH_CLASSES`), tech típust kap;
    a korábbi típus a `type_changed_from`-ban. Visszaad: hány entitás változott."""
    return len(store.update_entities_in_retype_tech_classes(con, sorted(TECH_CLASSES)))


def _ambiguous(knowledge: KnowledgeBase, kind: str, hit: dict) -> bool:
    """Az alias-találat kétértelmű, ha egy vetélytárs címke-találat (`gate.wikidata_hit`
    `rivals`) sem az osztályai, sem a leírása szerint nem összeférhetetlen a típussal (pl.
    „forgalom”: a közlekedési forgalom; nem az a „Seo” családnév és indonéz falu a „SEO”
    mellett). Hibás kérésnél kétértelmű."""
    blocked = (*INCOMPATIBLE, *TYPE_INCOMPATIBLE.get(kind, ()))
    for qid in hit.get("rivals", ()):
        classes = knowledge.classes(qid)
        if classes is None:
            return True
        text = " | ".join([*classes[0], classes[1]]).lower()
        if not any(word in text for word in blocked):
            return True
    return False


def _corroborated(con: duckdb.DuckDBPyConnection, knowledge: KnowledgeBase,
                  short: list[tuple[int, str, list[str]]]) -> list[tuple[int, int, str]]:
    """A `MIN_NAME_CHARS`-nál rövidebb nevű entitás önmagában nem kap linket; ha a pontos
    címke- vagy alias-találata ugyanaz a QID, amelyet egy azonos típusú, hosszabb nevű entitás
    biztosan (confident) kapott, ahhoz olvad (`wikidata_short_name`). (rövid, cél, QID)."""
    confident = {(kind, qid): entity_id for entity_id, kind, qid in store.entities_for_corroborated_2(con)}
    pairs = []
    for entity_id, name, codes in short:
        (kind,) = store.entities_for_corroborated(con, entity_id)
        hit = next((found for code in codes if (found := knowledge.wikidata(name, code))), None)
        if hit is not None and (kind, hit["id"]) in confident                 and not _ambiguous(knowledge, kind, hit):
            pairs.append((entity_id, confident[(kind, hit["id"])], hit["id"]))
    return pairs


def _store(con: duckdb.DuckDBPyConnection, entity_id: int, qid: str | None, wiki: str | None,
           status: str, clock: Callable[[], datetime]) -> None:
    store.update_entities_in_store(con, qid, wiki, status, clock(), entity_id)


def _merge_confident(con: duckdb.DuckDBPyConnection, clock: Callable[[], datetime],
                     short: list[tuple[int, int, str]] = ()) -> int:
    """Az azonos típusú, azonos biztos QID-jű entitások összevonása, és a megerősített rövid
    nevek (`short`) beolvasztása, a legutóbbi site-körhöz kötött `merge_log`-gal; ha nincs
    site-kör, nincs összevonás."""
    run = store.entity_runs_for_merge_confident(con)[0]
    if run is None:
        return 0
    merger = Merger(con, run, clock)
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for entity_id, kind, qid in store.entities_for_merge_confident(con):
        groups[(kind, qid)].append(entity_id)
    rows = _entity_rows(con)
    for (kind, qid), ids in sorted(groups.items()):
        if len(ids) < 2:
            continue
        ordered = sorted((rows[i] for i in ids), key=_rank)
        keep = ordered[0]
        for other in ordered[1:]:
            if _mergeable(keep, other):
                merger.merge(keep[0], other[0], "wikidata_confident", {"qid": qid, "type": kind})
    for entity_id, target, qid in short:
        target = resolve(con, target)
        if target is not None and target != entity_id \
                and _mergeable(_entity_rows(con)[target], _entity_rows(con)[entity_id]):
            merger.merge(target, entity_id, "wikidata_short_name", {"qid": qid})
    return merger.counts["wikidata_confident"] + merger.counts["wikidata_short_name"]
