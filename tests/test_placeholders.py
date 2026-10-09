"""A kitöltetlen sablon-tartalom mintái (functions.placeholders), az oldal szövegmezői
(engine.textfields) és a „lehetséges hiányosság: kitöltetlen sablon-tartalom” megállapítás;
szintetikus site-on, hálózat és LLM nélkül."""
from aaa2.engine.textfields import page_texts
from aaa2.entities.rules import run_rules
from aaa2.functions import placeholders
from aaa2.functions.findings import _finding_html, build_findings, stored_findings
from aaa2.functions.graph import build_graph
from aaa2.functions.placeholders import lorem_in_short, lorem_in_text, page_hits
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, site
from tests.test_entities_site import NOON

BASE = "https://pelda.hu"
LOREM = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
         "incididunt ut labore et dolore magna aliqua.")


def patterns(hits):
    return [(hit["pattern"], hit["field"], hit["area"]) for hit in hits]


def test_bracket_placeholders_are_upper_case_tokens():
    def found(text):
        return [hit["snippet"] for hit in page_hits(f"{BASE}/", text, None, None, None)
                if hit["pattern"] == "placeholder"]
    assert found("Írj nekünk: [IDE_JÖN_A_KAPCSOLAT_OLDAL_LINKJE_VAGY_EMAIL_LINK]")
    assert found("Welcome to [COMPANY NAME]") and found("Ár: [PLACEHOLDER]")
    assert found("Írj ide: [IDE_JÖN_A_LINK]") and found("[COMPANY NAME 2] rólunk")
    # a csak számjegyből álló tag nem szó
    for plain in ("Forrás [1] és [2]", "Állapot: [OK]", "[Lásd a táblázatot]", "[A] változat",
                  "Hírek [UPDATED 2025]", "Nézd meg: [VIDEO 2]", "[2025 UPDATED]", "[10_20]",
                  "{{ nev }} és TODO: megírni"):
        assert found(plain) == [], plain


def test_lorem_ipsum_in_running_text_needs_the_threshold():
    assert lorem_in_text(f"Bevezető mondat. {LOREM}").startswith("Bevezető mondat Lorem ipsum")
    assert lorem_in_text("A lorem ipsum egy töltőszöveg, amelyet a nyomdászok használnak.") is None
    # öt erős szó, de csak kétféle
    assert lorem_in_text("lorem ipsum lorem ipsum lorem a b c d e f g") is None
    # az élő nyelvekkel ütköző szavak önmagukban nem számítanak
    assert lorem_in_text("in est id ut et sed sit ad non qui ea in est id ut et") is None


def test_lorem_ipsum_in_a_short_field_needs_a_strong_word_and_a_majority():
    assert lorem_in_short("Qui in ea voluptate")
    assert lorem_in_short("Lorem ipsum dolor - DUEX Hungary Webshop")
    assert lorem_in_short("Culpa qui official")
    assert not lorem_in_short("Lorem")                           # egyetlen szó nem elég
    assert not lorem_in_short("in est id ut et")                 # nincs erős töltőszó
    assert not lorem_in_short("Qui est in")                      # francia cím is lehet
    assert not lorem_in_short("Error in the id of the non member")
    assert not lorem_in_short("Lorem ipsum generator for designers and developers")   # kisebbség


def test_the_url_path_is_checked_by_segment():
    hits = page_hits(f"{BASE}/spg/930300,2744904/Qui-in-ea-voluptate", "Cikk", None, "Cikk", None)
    assert patterns(hits) == [("lorem_ipsum", "url", None)]
    assert hits[0]["snippet"] == "Qui-in-ea-voluptate"
    assert page_hits(f"{BASE}/blog/in-est-id/", None, None, None, None) == []
    bracket = page_hits(f"{BASE}/spg/1/%5BIDE_J%C3%96N_A_LINK%5D", None, None, None, None)
    assert patterns(bracket) == [("placeholder", "url", None)]


def test_cms_defaults_and_text_slots():
    hits = page_hits(f"{BASE}/", "Just another WordPress site", "Ide jön a leírás", "Hello world!",
                     None)
    assert patterns(hits) == [("cms_default", "title", None), ("text_slot", "description", None),
                              ("cms_default", "h1", None)]
    assert page_hits(f"{BASE}/", "Mintaoldalak gyűjteménye", None, "Szia, világ", None) == []


def test_ide_jon_counts_only_as_a_slot():
    def slots(text):
        return [hit["pattern"] for hit in page_hits(f"{BASE}/", text, None, None, None)]
    for slot in ("[Ide jön a leírás]", "Ide jön a szöveg", "(ide jön majd valami)",
                 "Ide jön az ajánlat címe: ide jön a cím", "ide jön a logó", "Szöveg helye",
                 "Your text here"):
        assert slots(slot) == ["text_slot"], slot
    for plain in ("Aki egyszer ide jön, visszatér.", "Ide jön a vendég minden nyáron",
                  "Sokan azért jönnek ide, mert csend van", "Ide jön a címzett levele"):
        assert slots(plain) == [], plain


def test_page_texts_by_area_without_code_blocks():
    texts = page_texts(
        "<html><head><base href='/alap/'></head><body>"
        "<nav><a href='/a/'>Menüpont</a><img src='l.png' alt='Logó'></nav>"
        "<main><p>Fő szöveg.</p><pre>{{ sablon }} Lorem ipsum</pre><p>Kód: <code>[IDE_JÖN]"
        "</code> vége.</p><a href='cel%5B1%5D#resz'>Tovább</a><a href='#fent'>Fel</a>"
        "<img src='k.png' alt=''><script>var x = '[NEM_SZÖVEG]'</script></main>"
        "<footer><p>Lábléc szöveg</p></footer></body></html>", f"{BASE}/oldal/")
    assert texts.areas == {"nav": "Menüpont", "body": "Fő szöveg. Kód: vége. Tovább Fel",
                           "footer": "Lábléc szöveg"}
    assert texts.alts == (("nav", "Logó"),)
    assert texts.links == (("nav", f"{BASE}/a/", "Menüpont"),
                           ("body", f"{BASE}/alap/cel[1]", "Tovább"))


def test_a_hit_is_reported_once_per_field_area_and_snippet():
    texts = page_texts(html("x", f"<main><p>{LOREM}</p><img src='a.png' alt='Lorem ipsum'>"
                                 "<img src='b.png' alt='Lorem ipsum'></main>"), f"{BASE}/")
    assert patterns(page_hits(f"{BASE}/", "Oldal", None, "Oldal", texts)) == [
        ("lorem_ipsum", "text", "body"), ("lorem_ipsum", "alt", "body")]


def placeholder_site(pages, noindex=(), canonical=None, descriptions=None):
    def page(title, body="", footer=""):
        return html(f"{title} · Pelda", '<header><nav><a href="/">Kezdőlap</a><a href="/blog/">'
                    f'Blog</a></nav></header><main><h1>{title}</h1><p>Rendes szöveg az oldalon.'
                    f"</p>{body}</main><footer><a href=\"/adat/\">Adatvédelem</a>{footer}</footer>")
    con = site({"/": page("Pelda"), "/blog/": page("Blog"), "/adat/": page("Adatvédelem"),
                **{path: page(*parts) for path, parts in pages.items()}})
    for path in noindex:
        con.execute("UPDATE pages SET noindex = true WHERE url = ?", [f"{BASE}{path}"])
    for path, target in (canonical or {}).items():
        con.execute("UPDATE pages SET canonical = ? WHERE url = ?",
                    [f"{BASE}{target}", f"{BASE}{path}"])
    for path, text in (descriptions or {}).items():
        con.execute("UPDATE pages SET meta_description = ? WHERE url = ?", [text, f"{BASE}{path}"])
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    return con


def found(con):
    return {finding.evidence["pattern"]: finding for finding in stored_findings(con)
            if finding.type == "template_placeholder"}


def test_one_finding_per_pattern_with_pages_fields_and_snippets():
    con = placeholder_site({
        "/katalogus/": ("Katalógusok", '<a href="[IDE_JÖN_A_LINK]">Kapcsolat</a>'),
        "/cikk-1/": ("Első cikk", f"<p>{LOREM}</p>"),
        "/cikk-2/": ("Lorem ipsum dolor", f"<p>{LOREM}</p>"),
    })
    hits = found(con)
    assert sorted(hits) == ["lorem_ipsum", "placeholder"]
    bracket = hits["placeholder"]
    assert bracket.severity == "medium" and bracket.page_id is None
    (page,) = bracket.evidence["pages"]
    assert page["url"] == f"{BASE}/katalogus/" and page["indexable"]
    assert page["hits"] == [{"field": "link href", "area": "fő tartalom",
                             "snippet": f"{BASE}/katalogus/[IDE_JÖN_A_LINK]"}]
    assert "link href, fő tartalom" in bracket.summary and "[IDE_JÖN_A_LINK]" in bracket.summary
    lorem = hits["lorem_ipsum"]
    assert [page["url"] for page in lorem.evidence["pages"]] == [f"{BASE}/cikk-1/",
                                                                 f"{BASE}/cikk-2/"]
    assert [hit["field"] for hit in lorem.evidence["pages"][1]["hits"]] == [
        "title", "H1", "látható szöveg"]
    assert all(len(hit["snippet"]) <= placeholders.SNIPPET_CHARS
               for page in lorem.evidence["pages"] for hit in page["hits"])
    assert "2 oldalon" in lorem.summary
    shown = _finding_html({"type": "template_placeholder", "severity": "medium", "summary": "x",
                           "page_url": None, "entity": None, "evidence": bracket.evidence})
    assert "helykitöltő szögletes zárójelben" in shown and "link href (fő tartalom)" in shown


def test_medium_only_for_prominent_hits_on_indexable_pages():
    def severity(**kwargs):
        (finding,) = found(placeholder_site(**kwargs)).values()
        return finding.severity, [page["severity"] for page in finding.evidence["pages"]]
    body = {"/cikk/": ("Cikk", f"<p>{LOREM}</p>")}
    assert severity(pages=body) == ("medium", ["medium"])
    assert severity(pages=body, noindex=["/cikk/"]) == ("low", ["low"])
    assert severity(pages=body, canonical={"/cikk/": "/blog/"}) == ("low", ["low"])
    assert severity(pages=body, canonical={"/cikk/": "/cikk/"}) == ("medium", ["medium"])
    # csak alt-ban, csak láblécben, csak meta descriptionben
    assert severity(pages={"/kep/": ("Kép", '<img src="a.png" alt="Lorem ipsum dolor">')}) == (
        "low", ["low"])
    assert severity(pages={"/lab/": ("Lábas", "", f"<p>{LOREM}</p>")})[0] == "low"
    assert severity(pages={"/leiras/": ("Leírás",)},
                    descriptions={"/leiras/": "Lorem ipsum dolor sit amet"}) == ("low", ["low"])
    # egy közepes oldal a megállapítást közepessé teszi
    assert severity(pages={**body, "/kep/": ("Kép", '<img src="a.png" alt="Lorem ipsum">')}) == (
        "medium", ["medium", "low"])


def test_no_finding_without_a_pattern():
    code = ("<pre>{{ nev }} [IDE_JÖN_A_LINK] Lorem ipsum dolor sit amet consectetur</pre>"
            "<p>TODO: megírni</p>")
    con = placeholder_site({"/kod/": ("Kód", code)})
    assert found(con) == {}
