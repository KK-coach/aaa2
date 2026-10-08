"""A heading-fa és a szerkezeti megállapítások (aaa2/functions/headings.py) szintetikus site-on,
hálózat és LLM nélkül."""
import csv
import json

from aaa2 import contracts
from aaa2.entities.dom import outside_h1
from aaa2.entities.rules import run_rules
from aaa2.functions import headings as heading_tree
from aaa2.functions.findings import _Site, build_findings, export_views, site_views
from aaa2.functions.graph import build_graph
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, site
from tests.test_entities_site import NOON, llm_entity
from tests.test_graph import primary

BASE = "https://pelda.hu"
WORDS = " ".join(f"szó{i}" for i in range(60))             # érdemi tartalom (60 szó)
CONTACT = "<h2>Beszéljünk róla</h2><p>Írj nekünk.</p>"      # sablon-heading négy oldalon


def article(title, body):
    return html(f"{title} · Pelda", f"<main>{body}</main>")


def heading_site():
    return site({
        "/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"),
        "/jo/": article("Jó oldal", f"<h1>Mérés</h1><p>{WORDS}</p><h2>Adatarchitektúra</h2>"
                                 f"<p>{WORDS}</p><h3>Riportolás</h3><p>Szöveg.</p>"
                                 f"<h2>Szlogen a végén</h2><p>Szöveg.</p>{CONTACT}"),
        "/nincs-h1/": article("Nincs", f"<h2>Csak alcím</h2><p>{WORDS}</p>{CONTACT}"),
        "/kivul/": html("Kívül · Pelda",
                        "<header><h1>Pelda logó</h1></header>"
                        "<div class='cookie-banner' style='display:none'><h1>Sütik</h1></div>"
                        "<div role='dialog'><h1>Felugró ajánlat</h1></div>"
                        f"<main><h2>Tartalom</h2><p>{WORDS}</p>{CONTACT}</main>"),
        "/cikkfej/": article("Cikkfej", f"<article><header><h1>Cikk címe</h1></header>"
                                        f"<p>{WORDS}</p></article>"),
        "/dupla-h1/": article("Dupla", f"<h1>Mérés</h1><h1>Mérés</h1><p>{WORDS}</p>{CONTACT}"),
        "/ket-tema/": article("Két téma", f"<h1>Mérés</h1><p>{WORDS}</p>"
                                          f"<h1>Adatarchitektúra</h1><h2>Rétegek</h2><p>x</p>"),
        "/ures/": article("Üres", f"<h1>Üres szakasz</h1><h2>Első</h2><h2>Második</h2>"
                                  f"<p>{WORDS}</p>"),
        "/ugras/": article("Ugrás", "<h1>Ugrás</h1><h2>Kettes</h2><h4>Négyes</h4><p>x</p>"
                                    "<h3>Hármas</h3><p>x</p>"),
        "/hosszu/": article("Hosszú", "<h1>Hosszú</h1>" + f"<p>{WORDS}</p>" * 11),
    })


def built():
    con = heading_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    for path in ("/jo/", "/dupla-h1/", "/ket-tema/"):
        llm_entity(con, f"{BASE}{path}", "Mérés", "Mérés", "concept")
    for path in ("/jo/", "/ket-tema/"):
        llm_entity(con, f"{BASE}{path}", "Adatarchitektúra", "Adatarchitektúra", "concept")
    llm_entity(con, f"{BASE}/jo/", "Riportolás", "Riportolás", "concept")
    con.execute("UPDATE page_entities SET position = CASE WHEN b.level = 1 THEN 'h1' ELSE "
                "'heading' END FROM blocks b WHERE b.block_id = page_entities.block_id "
                "AND b.kind = 'heading'")
    ids = dict(con.execute("SELECT name, entity_id FROM entities").fetchall())
    con.execute("INSERT INTO entity_relations (from_id, to_id, type, source) VALUES "
                "(?, ?, 'part_of', 'shop')", [ids["Adatarchitektúra"], ids["Mérés"]])
    primary(con, f"{BASE}/jo/", ["Mérés"])
    primary(con, f"{BASE}/dupla-h1/", ["Mérés"])
    primary(con, f"{BASE}/ket-tema/", ["Mérés", "Adatarchitektúra"])
    build_graph(con)
    return con, build_findings(con)


def structure(con):
    found = {}
    for kind, severity, summary, evidence in con.execute(
            "SELECT type, severity, summary, evidence FROM findings WHERE "
            "list_contains(?, type) ORDER BY finding_id",
            [list(heading_tree.STRUCTURE_TYPES)]).fetchall():
        data = json.loads(evidence)
        found[(kind, data.get("url") or data.get("group"))] = (severity, summary, data)
    return found


def test_structure_findings_by_cause():
    con, run = built()
    found = structure(con)
    assert {(kind, where.removeprefix(BASE)) for kind, where in found} == {
        ("missing_h1", "/nincs-h1/"), ("h1_outside_content", "/kivul/"),
        ("multiple_h1", "/dupla-h1/"), ("empty_section", "/ures/"),
        ("empty_section", "/dupla-h1/"), ("skipped_level", "/ugras/"),
        ("missing_h2", "/hosszu/")}
    severity, summary, _ = found[("missing_h1", f"{BASE}/nincs-h1/")]
    assert (severity, summary) == ("high", "nincs H1")
    severity, summary, data = found[("h1_outside_content", f"{BASE}/kivul/")]
    assert severity == "medium"
    assert data["outside"] == [{"where": "chrome", "text": "Pelda logó"},
                               {"where": "cookie", "text": "Sütik"},
                               {"where": "dialog", "text": "Felugró ajánlat"}]
    assert summary == ("H1 a fő tartalmon kívül: cookie- vagy consent-elem; fejléc, menü, "
                       "lábléc vagy oldalsáv; popup vagy modális ablak")
    severity, summary, data = found[("multiple_h1", f"{BASE}/dupla-h1/")]
    assert severity == "low" and data["reasons"] == [
        "valamelyik H1-ben nincs entitás",
        "valamelyik H1-szakasz üres vagy nincs alatta érdemi tartalom"]
    # a H1 után H1 üres szakasz is
    assert found[("empty_section", f"{BASE}/dupla-h1/")][2]["headings"] == ["H1: Mérés"]
    assert found[("empty_section", f"{BASE}/ures/")][2]["headings"] == ["H2: Első"]
    assert found[("skipped_level", f"{BASE}/ugras/")][2]["headings"] == ["H4: Négyes"]
    severity, summary, data = found[("missing_h2", f"{BASE}/hosszu/")]
    assert severity == "low" and data["words"] == 660 and summary == "660 szó H2 nélkül"
    # a H1 nélküli oldal nincs a H1/title-eltérések között; a cikkfej H1-e a tartalom része
    mismatch = con.execute("SELECT evidence FROM findings WHERE type = 'h1_title_mismatch'"
                           ).fetchall()
    assert not any("nincs H1" in row[0] for row in mismatch)
    assert run.by_type().get("missing_h1") == 1


def test_the_tree_sections_entities_and_relations():
    con, _ = built()
    site_data = _Site(con)
    by_url = {page["url"]: site_data.headings[page_id]
              for page_id, page in site_data.pages.items()}
    good = by_url[f"{BASE}/jo/"]
    (h1,) = good["tree"]
    assert (h1["level"], h1["text"], h1["relation"], h1["words"]) == (1, "Mérés", "main", 60)
    assert [(c["level"], c["text"], c["relation"], c["template"]) for c in h1["children"]] == [
        (2, "Adatarchitektúra", "related", False),     # él a fő entitáshoz (part_of)
        (2, "Szlogen a végén", "no_entity", False),
        (2, "Beszéljünk róla", "no_entity", True)]     # sablon-heading: a fában marad
    (h3,) = h1["children"][0]["children"]
    assert (h3["text"], h3["relation"]) == ("Riportolás", "unrelated")
    assert h1["children"][0]["total_words"] == 61 and h1["total_words"] == 60 + 61 + 1 + 2
    assert good["template"] == 1                        # a „Beszéljünk róla” sablon-heading
    # a sablon-heading a szintjét tartja: a /nincs-h1/ oldalnak van H2-je, a tartalom nélküli
    # sablon-heading maga nem üres szakasz
    assert by_url[f"{BASE}/nincs-h1/"]["has_h2"]
    assert not any(node["empty"] or node["skipped_level"]
                   for data in by_url.values() for node in data["sequence"] if node["template"])
    # a cikk saját fejlécének H1-e a tartalom része, nincs kívül lévő H1
    head = by_url[f"{BASE}/cikkfej/"]
    assert [n["text"] for n in head["h1"]] == ["Cikk címe"] and head["outside_h1"] == []
    # két H1 két külön (fő és másodlagos) entitással, érdemi tartalommal vagy alcímmel: indokolt
    two = by_url[f"{BASE}/ket-tema/"]
    assert two["h1_justified"] is True and two["h1_reasons"] == []
    assert by_url[f"{BASE}/dupla-h1/"]["h1_justified"] is False
    assert by_url[f"{BASE}/jo/"]["h1_justified"] is None


def test_multiple_h1_is_justified_only_for_distinct_page_entities_with_content():
    def h1(own, entities=1, words=60, children=0, empty=False):
        return {"entities": [{}] * entities, "own": own, "total_words": words,
                "children": [{}] * children, "empty": empty}

    judge = heading_tree._multiple_h1
    assert judge([h1([1])]) == (None, [])
    assert judge([h1([1]), h1([2])]) == (True, [])
    assert judge([h1([1]), h1([2], words=3, children=2)]) == (True, [])     # saját alcímek
    assert judge([h1([1]), h1([1])]) == (False, ["a H1-ek ugyanarra az entitásra mutatnak"])
    assert judge([h1([1]), h1([], entities=0)])[1] == ["valamelyik H1-ben nincs entitás"]
    assert judge([h1([1]), h1([])])[1] == [
        "valamelyik H1 nem az oldal fő vagy másodlagos entitását nevezi meg"]
    assert judge([h1([1]), h1([2], words=heading_tree.SUBSTANTIVE_WORDS - 1)])[1] == [
        "valamelyik H1-szakasz üres vagy nincs alatta érdemi tartalom"]
    assert judge([h1([1], empty=True), h1([2])])[0] is False


def test_headings_inside_links_count_for_missing_h1_and_h2():
    from aaa2.entities.dom import heading_scan
    card = "<a href='/x'><h2>Termék</h2></a>"
    assert heading_scan(f"<body><main><a href='/'><h1>Cím</h1></a>{card}</main></body>") == (
        [("content", "Cím")], {1, 2})
    assert heading_scan("<body><nav><h2>Menü</h2></nav><main><p>x</p></main></body>") == (
        [], set())


def test_outside_h1_reads_the_dom():
    assert outside_h1("<body><main><h1>Cím</h1></main></body>") == []
    assert outside_h1("<body><main><header><h1>Cím</h1></header></main></body>") == []
    assert outside_h1("<body><header><h1>Logó</h1></header><main><h1>Cím</h1></main></body>") \
        == [("chrome", "Logó")]
    assert outside_h1("<body><header class='site-header'><h1>Logó</h1></header></body>") \
        == [("chrome", "Logó")]
    assert outside_h1("<body><main><nav><h1>Menü</h1></nav></main></body>") \
        == [("chrome", "Menü")]
    assert outside_h1("<body><div id='cookie-consent' hidden><h1>Sütik</h1></div></body>") \
        == [("cookie", "Sütik")]
    assert outside_h1("<body><dialog><h1>Hírlevél</h1></dialog></body>") \
        == [("dialog", "Hírlevél")]
    assert outside_h1("<body><div aria-modal='true'><h1>Modális</h1></div></body>") \
        == [("dialog", "Modális")]
    assert outside_h1("<body><main><div style='display:none'><h1>Rejtett</h1></div><h1> </h1>"
                      "</main></body>") == []


def test_heading_views_in_the_csv_the_html_and_the_contract(tmp_path):
    con, _ = built()
    paths = export_views(con, tmp_path, "pelda")
    with paths["headings"].open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    good = [r for r in rows if r["url"] == f"{BASE}/jo/"]
    assert [(r["mélység"], r["szint"], r["heading"], r["kapcsolat a fő entitáshoz"])
            for r in good] == [
        ("0", "H1", "Mérés", "fő entitás"), ("1", "H2", "Adatarchitektúra", "kapcsolódik"),
        ("2", "H3", "Riportolás", "független"),
        ("1", "H2", "Szlogen a végén", "nincs benne entitás"),
        ("1", "H2", "Beszéljünk róla", "nincs benne entitás")]
    assert good[-1]["megjegyzés"] == "sablon-heading"
    assert good[1]["entitások"] == "Adatarchitektúra (kapcsolódik)"
    outside = [r for r in rows if r["url"] == f"{BASE}/kivul/"
               and r["megjegyzés"].startswith("a fő tartalmon kívül")]
    assert {r["megjegyzés"] for r in outside} == {
        "a fő tartalmon kívül: fejléc, menü, lábléc vagy oldalsáv",
        "a fő tartalmon kívül: cookie- vagy consent-elem",
        "a fő tartalmon kívül: popup vagy modális ablak"}
    notes = {(r["url"].removeprefix(BASE), r["heading"]): r["megjegyzés"] for r in rows}
    assert notes[("/ures/", "Első")] == "üres szakasz"
    assert notes[("/ugras/", "Négyes")] == "kihagyott szint"
    assert notes[("/ket-tema/", "Adatarchitektúra")] == "indokolt több H1"
    assert "indokolatlan több H1" in notes[("/dupla-h1/", "Mérés")]
    page = paths["html"].read_text(encoding="utf-8")
    assert "heading-fa" in page and "Kihagyott heading-szint" in page and "Hiányzó H1" in page
    views = site_views(con, "pelda", "pelda.hu")
    assert views.schema_version == contracts.SCHEMA_VERSION == "1.17"
    by_url = {p.url: p for p in views.pages}
    tree = by_url[f"{BASE}/jo/"].headings
    assert isinstance(tree[0], contracts.HeadingView)
    assert tree[0].children[0].children[0].text == "Riportolás"
    assert tree[0].children[0].entities[0].relation == "related"
    assert [o.where for o in by_url[f"{BASE}/kivul/"].h1_outside] == ["chrome", "cookie",
                                                                      "dialog"]
    assert by_url[f"{BASE}/ket-tema/"].h1_justified is True
    restored = contracts.SiteViews.model_validate(json.loads(views.model_dump_json()))
    assert restored == views


def section_site():
    """Üres és csak szöveg nélküli elemet tartalmazó szakaszok, és azonos mintájú oldalak."""
    def empty(mark):                       # oldalanként más szöveg: nem sablon-heading
        return f"<h2>Első {mark}</h2><h2>Második {mark}</h2><p>Szöveg.</p>"

    jump = "<h2>Kettes</h2><h4>Négyes</h4><p>x</p>"
    return site({
        "/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"),
        "/kep/": article("Kép", "<h1>Kép</h1><h2>Galéria</h2><img src='/a.png' alt=''>"
                                "<h2>Kártya <b>egy</b>, kettő</h2><a href='/x/'><img src='/b.png' alt=''></a>"
                                "<h2>Űrlap</h2><form><input name='q'></form>"
                                "<h2>Vége</h2><p>Szöveg.</p>"),
        "/rejtett/": article("Rejtett", "<h1>Rejtett</h1><h2>Első lépés</h2>"
                                        "<div hidden><p>Lenyíló szöveg a lépésről.</p></div>"
                                        "<h2>Második lépés</h2>"
                                        "<div style='display: none'><p>Másik szöveg.</p></div>"
                                        "<h2>Vége</h2><p>Szöveg.</p>"),
        "/ures-a/": article("Üres A", f"<h1>Üres A</h1>{empty('A')}"),
        "/ures-b/": article("Üres B", f"<h1>Üres B</h1>{empty('B')}<h3>Alcím</h3><h3>Másik</h3>"
                                    "<p>x</p>"),
        "/ures-c/": article("Üres C", f"<h1>Üres C</h1>{empty('C')}"),
        "/ugras-a/": article("Ugrás A", f"<h1>Ugrás A</h1>{jump}"),
        "/ugras-b/": article("Ugrás B", f"<h1>Ugrás B</h1>{jump}"),
        "/utolso/": article("Utolsó", "<h1>Utolsó</h1><p>Szöveg.</p><h2>Záró heading</h2>"),
    })


def test_empty_sections_follow_the_dom_and_patterns_merge_by_role(tmp_path):
    con = section_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    found = structure(con)
    kinds = {(kind, where.removeprefix(BASE)) for kind, where in found}
    # csak kép, linkbe ágyazott kép, űrlap vagy rejtett szöveg: nem megállapítás; az utolsó
    # heading nem vizsgált
    assert not any(path in where for _, where in kinds
                   for path in ("/kep/", "/rejtett/", "/utolso/"))
    role = _Site(con).pages[1]["role"]
    by_kind = {kind: (where, *found[(kind, where)]) for kind, where in found}
    # az azonos szerepű és szintmintájú oldalak egy tétel; az /ures-b/ mintája más (H2, H3)
    where, severity, summary, data = by_kind["skipped_level"]
    assert where == f"skipped_level | {data['role']} | H2→H4" and severity == "low"
    assert [page["url"].removeprefix(BASE) for page in data["pages"]] == ["/ugras-a/",
                                                                          "/ugras-b/"]
    assert data["pattern"] == ["H2→H4"] and data["pages"][0]["headings"] == ["H4: Négyes"]
    assert summary.startswith("kihagyott heading-szint (H2→H4): 2 ")
    empty = {where: found[(kind, where)] for kind, where in found if kind == "empty_section"}
    merged = next(data for where, (_, _, data) in empty.items() if "pages" in data)
    assert merged["pattern"] == ["H2"] and [
        page["url"].removeprefix(BASE) for page in merged["pages"]] == ["/ures-a/", "/ures-c/"]
    single = empty[f"{BASE}/ures-b/"][2]
    assert single["pattern"] == ["H2", "H3"] and single["headings"] == ["H2: Első B", "H3: Alcím"]
    assert len(empty) == 2 and role
    # a nézetben: megjegyzés a szöveg nélküli elemre, a rejtett szövegre nincs
    paths = export_views(con, tmp_path, "pelda")
    with paths["headings"].open(encoding="utf-8-sig", newline="") as handle:
        notes = {(r["url"].removeprefix(BASE), r["heading"]): r["megjegyzés"]
                 for r in csv.DictReader(handle)}
    # a soron belüli elem körüli szóköz eltérhet a blokk és a DOM szövegében
    notes[("/kep/", "Kártya")] = next(note for (path, heading), note in notes.items()
                                      if path == "/kep/" and heading.startswith("Kártya"))
    assert [notes[("/kep/", name)] for name in ("Galéria", "Kártya", "Űrlap", "Vége")] == [
        "csak kép / űrlap", "csak kép / űrlap", "csak kép / űrlap", ""]
    assert notes[("/rejtett/", "Első lépés")] == notes[("/rejtett/", "Második lépés")] == ""
    assert notes[("/ures-a/", "Első A")] == "üres szakasz"
    assert notes[("/utolso/", "Záró heading")] == ""
    assert "csak kép / űrlap" in paths["html"].read_text(encoding="utf-8")
    views = site_views(con, "pelda", "pelda.hu")
    gallery = next(p for p in views.pages if p.url == f"{BASE}/kep/").headings[0].children[0]
    assert (gallery.text, gallery.media_only, gallery.empty) == ("Galéria", True, False)


def test_heading_gaps_read_text_and_content_elements_from_the_dom():
    from aaa2.entities.dom import heading_gaps
    assert heading_gaps(
        "<body><main><h1>A <a href='#a'>#</a></h1><h2>B</h2><img src='x'><h2>C</h2>"
        "<div hidden><p>rejtett szó</p></div><h2>D</h2><a href='/x'><img src='y'></a>"
        "<h2>E</h2><table><tr><td>1</td></tr></table><h2>F</h2><script>var x</script>"
        "<input type='hidden'><a href='/y'></a><h2>G</h2></main></body>") == [
        (1, "A #", 0, ()), (2, "B", 0, ("img",)), (2, "C", 2, ()), (2, "D", 0, ("a", "img")),
        (2, "E", 1, ("table",)), (2, "F", 0, ()), (2, "G", 0, ())]
