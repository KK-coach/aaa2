"""Az oldal címmezői tényként, és a megnevezés szigorú (teljes szavas) összevetése.

Címmezők (`title_fields`), mind a tárolt adatból: a title a site-utótag nélkül és nyersen, a
látható cím (a tartalmi régió első címsora, az elemével), az oldal H1-einek száma, az og:title,
és a schema.org `headline` / `name` értékei típusonként. A schema `about` külön áll (nem
címmező): mire mutat, és azonos-e az oldal fő entitásával. A név nélküli `@id`-hivatkozásnál
az `@id` és a hozzá tartozó csomópontok összes neve áll (`names`, a készlet minden oldaláról,
rendezve; a több név tény): a bejárás sorrendje nem számít. `differing`: mely címmezők szövege
tér el egymástól; az összevetés előtt a szöveg egységesül (`compare_key`: kis- és nagybetű,
szóköz, írásjelek, és az „&” ↔ „and” / „és” kötőszó), a nyers értékek megmaradnak.

A megnevezés szigorú összevetése (`names_whole`): a név teljes szóként vagy kifejezésként áll
a szövegben, szóhatártól szóhatárig; az egybe- és a különírás nem számít („Levendulaolaj” =
„levendula olaj”). A név utolsó szavát az oldal nyelvén rag követheti (`ENDINGS`): magyarul a
többes szám és az esetragok („kókuszolaj” = „kókuszolajat”, „kókuszolajjal”), más nyelven az
angol többes szám. A képző nem rag: a „Kókusztej” nem áll a „Kókusztejes”-ben. Rövidebb név
hosszabb névben nem egyezés: ha a talált hely egy másik entitás hosszabb megnevezésének része
(a „Meta” a „Meta Ads”-ben), az nem megnevezés. Kimarad: a tőváltozás („bokor” – „bokrot”,
„ló” – „lovat”), a birtokos személyjel („kókuszolaja”), a képzett alak és az összetett szó
belseje.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import duckdb

from aaa2.engine import queries as crawl
from aaa2.entities import queries as extract_queries
from aaa2.entities.rules import alias_key
from aaa2.functions.graph import _as_list, _short, _walk
from aaa2.resolver.pages import schema_nodes

CONJUNCTIONS = frozenset({"and", "es"})
# a schema.org-típusok, amelyek `name`-je az oldal címe; a `headline` bármely típuson az
TITLE_TYPES = frozenset({
    "WebPage", "Article", "BlogPosting", "NewsArticle", "TechArticle", "CollectionPage",
    "AboutPage", "ContactPage", "ProfilePage", "ItemPage", "FAQPage", "Blog"})
# az oldal saját dolgának típusai: a `name`-jük csak akkor címmező, ha az oldal szerepe ilyen
# (`OWN_THING_ROLES`), és a típusból egyetlen legfelső szintű csomópont áll az oldalon
OWN_THING_TYPES = frozenset({"Product", "Service"})
OWN_THING_ROLES = frozenset({"offer", "product", "category"})
ARTICLE_TYPES = frozenset({"Article", "BlogPosting", "NewsArticle", "TechArticle"})
_HU_PLURAL = ("k", "ok", "ek", "ak")
_HU_CASE = ("t", "at", "et", "ot", "ban", "ben", "ba", "be", "bol", "rol", "tol", "nak", "nek",
            "ra", "re", "hoz", "hez", "on", "en", "n", "nal", "nel", "val", "vel", "ert", "ig",
            "kent")
# a név utolsó szava után megengedett végződés a kulcsban (ékezet nélkül), nyelvenként
ENDINGS = {
    "hu": frozenset({*_HU_PLURAL, *_HU_CASE, "kkal", "kkel",
                     *(plural + case for plural in _HU_PLURAL for case in _HU_CASE)}),
    None: frozenset({"s", "es"})}
ASSIMILATED = ("al", "el")              # a -val / -vel a tő végi mássalhangzóhoz hasonulva


@dataclass
class TitleFields:
    raw_title: str | None = None
    title: str | None = None                      # a site-utótag nélkül
    visible: str | None = None                    # a tartalmi régió első címsora
    visible_element: str | None = None            # h1 … h6
    h1_count: int = 0
    og_title: str | None = None
    og_type: str | None = None
    schema: list[tuple[str, str]] = field(default_factory=list)     # (mező és típus, szöveg)
    schema_types: list[str] = field(default_factory=list)           # a legfelső szintű típusok
    about: list[dict] = field(default_factory=list)
    differing: list[str] = field(default_factory=list)

    def compared(self) -> dict[str, str]:
        """A címmezők (az `about` nélkül) a nevükkel, az üresek nélkül."""
        found = {"title": self.title, "látható cím": self.visible, "og:title": self.og_title}
        for label, text in self.schema:
            found.setdefault(f"schema {label}", text)
        return {label: text for label, text in found.items() if text}


def words(text: str | None) -> list[str]:
    """A kulcs szavai az írásjelek és a kötőszó (és, and, &) nélkül."""
    return [w for w in re.split(r"[\W_]+", alias_key(text or "")) if w and w not in CONJUNCTIONS]


def compare_key(text: str | None) -> str:
    """A címmezők összevetésének kulcsa: kis- és nagybetű, szóköz, írásjelek és a kötőszó
    („&”, „and”, „és”) nélkül."""
    return " ".join(words(text))


def cut_suffix(text: str | None, suffixes: Iterable[str], split: re.Pattern) -> str | None:
    """A szöveg a site-utótag nélkül: az utolsó szelet (`split` mentén) elmarad, ha a kulcsa a
    site-szerte ismétlődő utótagok (`suffixes`) egyike."""
    pieces = split.split(text or "")
    if text and len(pieces) > 1 and alias_key(pieces[-1]) in suffixes:
        return text[: text.rindex(pieces[-1])].rstrip().rstrip("-–—|·:").rstrip()
    return text


def title_fields(con: duckdb.DuckDBPyConnection, pages: Mapping[int, Mapping],
                 suffixes: Mapping[str | None, Iterable[str]], split: re.Pattern,
                 h1_counts: Mapping[int, int]) -> dict[int, TitleFields]:
    """Oldalanként a címmezők (lásd a modul leírását). `pages`: oldal → {title, lang, role};
    `suffixes`: nyelv → a site-utótagok kulcsai; `h1_counts`: oldal → a H1-ek száma. Az
    `about` elemei itt még feloldatlanok: {`name`: a hivatkozás saját neve, `id`, `names`: a
    név nélküli `@id`-hivatkozás csomópontjainak nevei}."""
    first: dict[int, tuple[str, int | None]] = {}
    for block in extract_queries.blocks(con):
        if block.page_id in pages and block.page_id not in first and block.kind == "heading" \
                and block.region == "content" and block.text.strip():
            first[block.page_id] = (block.text.strip(), block.level)
    nodes = schema_nodes(con)
    id_names: dict[str, set[str]] = defaultdict(set)
    for found in nodes.values():
        for node in found:
            for item in _walk(node):
                if "@type" in item and isinstance(item.get("@id"), str) \
                        and isinstance(item.get("name"), str) and item["name"].strip():
                    id_names[item["@id"]].add(item["name"].strip())
    graph = crawl.open_graph(con)
    result: dict[int, TitleFields] = {}
    for page_id, page in pages.items():
        suffix = suffixes.get(page["lang"], ())
        meta = graph.get(page_id, {})
        fields = TitleFields(
            raw_title=page["title"], title=cut_suffix(page["title"], suffix, split),
            h1_count=h1_counts.get(page_id, 0),
            og_title=cut_suffix(meta.get("og:title"), suffix, split),
            og_type=meta.get("og:type"))
        if page_id in first:
            fields.visible = first[page_id][0]
            fields.visible_element = f"h{first[page_id][1] or 1}"
        top = nodes.get(page_id, [])
        kinds = [{_short(t) for t in _as_list(node.get("@type"))} for node in top]
        fields.schema_types = sorted({t for found in kinds for t in found})
        for node, types in zip(top, kinds, strict=True):
            own = page["role"] in OWN_THING_ROLES and any(
                sum(1 for found in kinds if t in found) == 1 for t in types & OWN_THING_TYPES)
            if not (types & TITLE_TYPES or own):
                continue
            label = ", ".join(sorted(types & (TITLE_TYPES | OWN_THING_TYPES)))
            for key in ("headline", "name"):
                if isinstance(node.get(key), str) and node[key].strip():
                    item = (f"{key} ({label})", cut_suffix(node[key].strip(), suffix, split))
                    if item not in fields.schema:
                        fields.schema.append(item)
            for value in _as_list(node.get("about")):
                name = value if isinstance(value, str) else (
                    value.get("name") if isinstance(value, dict) else None)
                ref = value.get("@id") if isinstance(value, dict) else None
                ref = ref if isinstance(ref, str) else None
                name = name.strip() if isinstance(name, str) and name.strip() else None
                item = {"name": name, "id": ref,
                        "names": sorted(id_names.get(ref, ())) if ref and not name else []}
                if (name or ref) and item not in fields.about:
                    fields.about.append(item)
        compared = fields.compared()
        labels = list(compared)
        fields.differing = [
            f"{a} ≠ {b}" for i, a in enumerate(labels) for b in labels[i + 1:]
            if compare_key(compared[a]) != compare_key(compared[b])]
        result[page_id] = fields
    return result


def _spans(form: str, tokens: list[str], endings: frozenset[str]) -> list[tuple[int, int]]:
    """A megnevezés helyei a szöveg szavai között: (első szó, utolsó szó) sorszámpárok. A név
    betűi szóhatártól szóhatárig állnak (az egybe- és különírás nem számít); az utolsó szót
    megengedett végződés követheti."""
    name = "".join(words(form))
    if not name:
        return []
    found = []
    for start in range(len(tokens)):
        joined = ""
        for end in range(start, len(tokens)):
            joined += tokens[end]
            if len(joined) < len(name):
                continue
            rest = joined[len(name):]
            if joined.startswith(name) and (
                    not rest or rest in endings
                    or ("val" in endings and len(rest) == 3 and rest[0] == name[-1]
                        and rest[1:] in ASSIMILATED)):
                found.append((start, end))
            break
    return found


def names_whole(forms: Iterable[str], text: str | None, lang: str | None,
                longer: Iterable[str] = ()) -> bool:
    """Valamelyik megnevezés áll-e a szövegben teljes szóként vagy kifejezésként, az oldal
    nyelvén megengedett végződéssel (lásd a modul leírását). `longer`: más entitások
    megnevezései; ha a talált hely egy ilyen, több szóból álló megnevezés helyének része, az
    nem egyezés."""
    tokens = words(text)
    if not tokens:
        return False
    endings = ENDINGS.get((lang or "").split("-")[0].lower(), ENDINGS[None])
    own = [span for form in forms for span in _spans(form, tokens, endings)]
    if not own:
        return False
    own_keys = {"".join(words(form)) for form in forms}
    other = [span for form in longer if "".join(words(form)) not in own_keys
             for span in _spans(form, tokens, endings)]
    return any(not any(a <= start and end <= b and (b - a) > (end - start) for a, b in other)
               for start, end in own)


def suffixes_by_language(titles: Iterable[tuple[str | None, str | None]], common) -> dict:
    """Nyelvenként a site-utótagok kulcsai (`common`: a `findings.common_suffixes`)."""
    by_lang: dict[str | None, list[str | None]] = defaultdict(list)
    for lang, title in titles:
        by_lang[lang].append(title)
    return {lang: common(found) for lang, found in by_lang.items()}
