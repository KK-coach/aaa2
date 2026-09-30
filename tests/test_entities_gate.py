"""A puha típusok tételei és bizonyítékai (aaa2/entities/gate.py): előfordulás, szerkezet,
ismétlődés, tudásbázis (Wikidata, Wikipedia), hálózat nélkül."""
from datetime import UTC, datetime

import httpx
import pytest

from aaa2.db.connect import connect
from aaa2.entities.gate import (
    KnowledgeBase,
    PageContext,
    occurs,
    repetition,
    soft_items,
    structure,
    title_variants,
    wikidata_hit,
    wikipedia_page,
)
from aaa2.entities.validate import WIKI_MIN_INTERVAL, _Api
from aaa2.llm.client import Retry

NOON = datetime(2026, 9, 28, 12, 0, tzinfo=UTC).replace(tzinfo=None)
BLOCKS = [
    {"id": "b0", "kind": "title", "text": "Könyvelés és bérszámfejtés kisvállalkozásoknak"},
    {"id": "b1", "kind": "heading", "text": "Bérszámfejtés Csomag"},
    {"id": "b2", "kind": "paragraph", "text": "A készletforgási sebességet havonta nézzük."},
    {"id": "b3", "kind": "paragraph", "text": "A raktári rendetlenség lassít."},
    {"id": "b4", "kind": "paragraph", "text": "Készletforgási sebesség és cash-flow együtt."},
    {"id": "b5", "kind": "card", "text": "Éves zárás · 120 000 Ft"},
    {"id": "b6", "kind": "paragraph", "text": "Átvesszük a számlázást."},
]
PAGE = PageContext(BLOCKS, chrome=["Szolgáltatások", "Negyedéves tanácsadás"],
                   anchors=["Adóbevallás"], lang="hu-HU")


def mention(block, surface, name, kind="concept"):
    return {"block_id": block, "surface_form": surface, "canonical_name": name, "type": kind,
            "subtype": None, "description": "d"}


@pytest.mark.parametrize(("name", "text", "found"), [
    ("készletforgási sebesség", "A készletforgási sebességet havonta nézzük.", True),
    ("Készletforgási sebesség", "a KESZLETFORGASI-SEBESSEG mutató", True),   # kulcs szerint
    ("sebesség", "a készletforgási sebesség", True),
    ("forgási sebesség", "a készletforgási sebesség", False),           # szókezdettől
    ("CRM", "a CRM-ből jön", True),
    ("CRM", "a CRMek", False),                                          # rövid: teljes szó
    ("", "bármi", False),
])
def test_occurs_from_a_word_start_with_suffixes(name, text, found):
    assert occurs(name, text) is found


def test_soft_items_group_by_name_skip_fabricated_and_hard_types():
    items = soft_items([
        mention("b2", "készletforgási sebességet", "Készletforgási sebesség"),
        mention("b4", "Készletforgási sebesség", "készletforgási sebesség"),
        mention("b3", "nincs ilyen", "Kitalált"),
        mention("b1", "Bérszámfejtés Csomag", "Bérszámfejtés Csomag", "service"),
        mention("b6", "számlázást", "Számlázó", "tech"),
    ], {b["id"]: b for b in BLOCKS})
    assert [(i.canonical, i.type, i.blocks) for i in items] == [
        ("Készletforgási sebesség", "concept", ["b2", "b4"]),
        ("Bérszámfejtés Csomag", "service", ["b1"])]
    assert items[0].names() == ["Készletforgási sebesség"]


@pytest.mark.parametrize(("entity", "where"), [
    (mention("b1", "Bérszámfejtés Csomag", "Bérszámfejtés Csomag", "service"), "heading:b1"),
    (mention("b6", "számlázást", "Bérszámfejtés"), "title:b0"),          # a név a title-ben
    (mention("b5", "Éves zárás", "Éves zárás", "service"), "card:b5"),
    (mention("b6", "számlázást", "Negyedéves tanácsadás", "service"), "nav"),
    (mention("b6", "számlázást", "Adóbevallás"), "anchor"),
    (mention("b3", "raktári rendetlenség", "Raktári rendetlenség"), None),
])
def test_structure(entity, where):
    blocks = {b["id"]: b for b in BLOCKS}
    assert structure(soft_items([entity], blocks)[0], PAGE) == where


def test_repetition_counts_mention_blocks_and_occurrences():
    blocks = {b["id"]: b for b in BLOCKS}
    once = soft_items([mention("b2", "készletforgási sebességet", "Készletforgási sebesség")],
                      blocks)[0]
    assert repetition(once, PAGE) == 2                      # b2 (említés) és b4 (előfordulás)
    single = soft_items([mention("b3", "raktári rendetlenség", "Raktári rendetlenség")],
                        blocks)[0]
    assert repetition(single, PAGE) == 1


# ---------------------------------------------------------------------------
# tudásbázis
# ---------------------------------------------------------------------------


def hit(qid, text, kind="label", lang="hu", description=""):
    return {"id": qid, "description": description,
            "match": {"type": kind, "language": lang, "text": text}}


@pytest.mark.parametrize(("body", "found"), [
    ({"search": [hit("Q1", "Készletforgási sebesség")]}, "Q1"),
    ({"search": [hit("Q2", "keszletforgasi sebesseg", "alias")]}, "Q2"),
    ({"search": [hit("Q3", "Készletforgási sebesség mérése")]}, None),     # nem pontos
    ({"search": [hit("Q4", "Készletforgási sebesség", lang="de")]}, None),
    ({"search": [hit("Q5", "Készletforgási sebesség", description="egyértelműsítő lap"),
                 hit("Q6", "Készletforgási sebesség")]}, "Q6"),
    ({"search": [hit("Q7", "Készletforgási sebesség", "description")]}, None),
    (None, None),
])
def test_wikidata_hit_is_exact_in_the_asked_language(body, found):
    hit_ = wikidata_hit(body, "készletforgási sebesség", "hu")
    assert (hit_["id"] if hit_ else None) == found


@pytest.mark.parametrize(("pages", "title"), [
    ([{"title": "Készletforgás", "missing": True}], None),
    ([{"title": "Forgás", "pageprops": {"disambiguation": ""}}], None),
    ([{"title": "Készletforgás", "missing": True}, {"title": "Készlet forgás"}], "Készlet forgás"),
])
def test_wikipedia_page_exists_and_is_not_a_disambiguation(pages, title):
    page = wikipedia_page({"query": {"pages": pages}})
    assert (page["title"] if page else None) == title


def test_title_variants():
    assert title_variants("Last-Click Attribúció") == ["Last-Click Attribúció",
                                                       "Last-click attribúció"]
    assert title_variants("SEO") == ["SEO", "Seo"]


class Calls:
    def __init__(self, bodies):
        self.bodies, self.urls = bodies, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(request.url)
        host, params = request.url.host, dict(request.url.params)
        key = (host, params.get("language") or params.get("titles"))
        return httpx.Response(200, json=self.bodies.get(key, {"search": [], "query": {
            "pages": [{"title": "x", "missing": True}]}}))


def test_knowledge_base_asks_page_language_then_english_and_caches():
    calls = Calls({("en.wikipedia.org", "Raktári rendetlenség"): {"query": {"pages": [
        {"title": "Raktári rendetlenség"}]}}})
    con = connect(":memory:")
    sleeps = []
    api = _Api(con, None, httpx.Client(transport=httpx.MockTransport(calls)),
               Retry(sleep=sleeps.append), lambda: NOON, iter([0.0] * 20).__next__)
    kb = KnowledgeBase(api.get)
    assert kb(["Raktári rendetlenség"], "hu-HU") == \
        "wikipedia:en:Raktári rendetlenség (Raktári rendetlenség)"
    assert [u.host for u in calls.urls] == ["www.wikidata.org", "www.wikidata.org",
                                            "hu.wikipedia.org", "en.wikipedia.org"]
    assert [dict(u.params).get("language") for u in calls.urls[:2]] == ["hu", "en"]
    assert sleeps == [pytest.approx(WIKI_MIN_INTERVAL)] * 3        # a Wikidata is kivár
    assert kb(["Raktári rendetlenség"], "hu-HU") is not None
    assert len(calls.urls) == 4                                   # gyorsítótárból
    assert con.execute("SELECT service, count(*) FROM validation_calls GROUP BY 1 ORDER BY 1"
                       ).fetchall() == [("wikidata", 2), ("wikipedia", 2)]
