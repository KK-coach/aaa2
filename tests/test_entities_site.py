"""Site-szintű entitások (aaa2/entities/pages.py, site.py; M2/6 PR A) szintetikus site-okon,
hálózat és LLM nélkül: oldalszerepek, oldalhoz kötött entitás, csomagok, lépések, demó- és
sablonjelölés, a módosított anchor-szabály."""
import json
from datetime import UTC, datetime

from aaa2.entities.knowledge import clear_person_links
from aaa2.entities.pages import entity_groups, page_roles, representative
from aaa2.entities.rules import run_rules
from aaa2.entities.site import (
    aligned_headings,
    expansions,
    label_parts,
    long_form,
    normal_key,
    pricing_rows,
    run_site,
)
from tests.test_entities_rules import html, ld, site

NOON = datetime(2026, 9, 29, 12, 0, tzinfo=UTC).replace(tzinfo=None)

NAV = ("<nav><a href='/hu/meres/'>Mérés</a> <a href='/kapcsolat/'>Kapcsolat</a> "
       "<a href='/blog/cikk/'>Blog</a></nav>")
NAV_EN = ("<nav><a href='/en/measurement/'>Measurement &amp; Data Architecture</a> "
          "<a href='/kapcsolat/'>Contact</a></nav>")
HU_OFFER = (NAV + "<main><h1>Mérés nélkül csak találgatás.</h1>"
            "<h2>Négy fázis</h2><h3>Diagnózis</h3><p>A jelenlegi állapot felmérése.</p>"
            "<h2>Árazás</h2><table><tr><th>Csomag</th><th>Óra</th><th>Díj</th></tr>"
            "<tr><td>Szerver oldali mérés · GTM konténer</td><td>8-14 h</td>"
            "<td>100 000 Ft</td></tr>"
            "<tr><td>BigQuery integráció · export</td><td>10-18 h</td><td>160 000 Ft</td></tr>"
            "</table><p>Óradíj: 16000 Ft</p></main>")
EN_SERVICE = ld({"@type": "Service", "name": "Measurement &amp; Data Architecture",
                 "url": "https://pelda.hu/en/measurement/",
                 "hasOfferCatalog": {"@type": "OfferCatalog", "itemListElement": [
                     {"@type": "Offer", "itemOffered": {"@type": "Service",
                                                        "name": "Server-Side Tracking Setup"}},
                     {"@type": "Offer", "itemOffered": {"@type": "Service",
                                                        "name": "BigQuery Integration"}}]}})
EN_OFFER = (NAV_EN + "<main><h1>Marketing without measurement is guessing.</h1>"
            "<h2>Pricing</h2><table><tr><th>Project</th><th>Hours</th><th>Cost</th></tr>"
            "<tr><td>Server-side tracking setup · GTM</td><td>8-14 h</td><td>€320</td></tr>"
            "<tr><td>BigQuery integration · export</td><td>12-20 h</td><td>€480</td></tr>"
            "</table></main>")
ARTICLE = (NAV + "<main><h1>Hogyan mérj jól</h1><p>Bevezető a méréshez.</p>"
           "<h3>Mérés és adatarchitektúra</h3><p>A rendszer alapja.</p>"
           "<p><a href='/hu/meres/'>Részletek</a></p>"
           "<h3>Beszéljünk</h3><p><a href='/kapcsolat/'>Részletek</a></p></main>")


def business_site():
    blog = ld({"@type": "BlogPosting", "headline": "Hogyan mérj jól"})
    contact = ld({"@type": "ContactPage", "name": "Kapcsolat"})
    con = site({
        "/": html("Pelda · Kezdőlap", NAV + "<main><h1>Pelda</h1><p>Üdv.</p></main>"),
        "/hu/meres/": html("Mérés és Adatarchitektúra, Döntésekhez · Pelda", HU_OFFER),
        "/en/measurement/": html("Measurement & Data Architecture — Growth · Pelda", EN_OFFER,
                                 head=EN_SERVICE, lang="en"),
        "/kapcsolat/": html("Kapcsolat · Pelda", NAV + "<main><h1>Írj nekem</h1></main>",
                            head=contact),
        "/blog/cikk/": html("Hogyan mérj jól · Pelda", ARTICLE, head=blog),
        "/adatvedelem/": html("Adatvédelem · Pelda", NAV + "<main><h1>Adatvédelem</h1></main>"),
    }, languages=("hu", "en"))
    pair = ["hu|https://pelda.hu/hu/meres/", "en|https://pelda.hu/en/measurement/"]
    con.execute("UPDATE pages SET hreflang = ? WHERE url IN ('https://pelda.hu/hu/meres/', "
                "'https://pelda.hu/en/measurement/')", [pair])
    return con


def llm_entity(con, url, text, name, kind, subtype=None):
    """Egy LLM-forrású említés a `text`-et tartalmazó blokkban (a név első előfordulásán)."""
    page_id, block_id, block_text = con.execute(
        "SELECT b.page_id, b.block_id, b.text FROM blocks b JOIN pages p USING (page_id) "
        "WHERE p.url = ? AND b.text LIKE ? ORDER BY b.ordinal LIMIT 1",
        [url, f"%{text}%"]).fetchone()
    start = block_text.index(text)
    row = con.execute("SELECT entity_id FROM entities WHERE name = ? AND type = ?",
                      [name, kind]).fetchone()
    entity_id = row[0] if row else con.execute(
        "INSERT INTO entities (name, type, subtype, aliases, source, created_at) "
        "VALUES (?, ?, ?, [], 'llm', ?) RETURNING entity_id",
        [name, kind, subtype, NOON]).fetchone()[0]
    (mention_id,) = con.execute(
        "INSERT INTO page_entities (page_id, entity_id, block_id, char_start, char_end, "
        "surface_form, position) VALUES (?, ?, ?, ?, ?, ?, 'body') RETURNING mention_id",
        [page_id, entity_id, block_id, start, start + len(text), text]).fetchone()
    con.execute("INSERT INTO mention_sources (mention_id, source, run_id) VALUES (?, 'llm', 99)",
                [mention_id])
    return entity_id


def by_name(con, name):
    return con.execute("SELECT entity_id, name, type, subtype, tier, anchor_page_id, flags "
                       "FROM entities WHERE name = ?", [name]).fetchall()


def roles_by_path(con):
    return {info.url.replace("https://pelda.hu", ""): (info.role, info.reason)
            for info in page_roles(con).values()}


# ---------------------------------------------------------------------------
# oldalszerepek
# ---------------------------------------------------------------------------


def test_page_roles_from_schema_hreflang_and_urls():
    con = business_site()
    run_rules(con)
    assert roles_by_path(con) == {
        "/": ("support", "home"),
        "/hu/meres/": ("offer", "group:offer"),
        "/en/measurement/": ("offer", "schema_service"),
        "/kapcsolat/": ("support", "schema_support"),
        "/blog/cikk/": ("article", "schema_article"),
        "/adatvedelem/": ("support", "support_url"),
    }
    groups = entity_groups(page_roles(con))
    offer = next(m for m in groups.values() if m[0].role == "offer")
    assert representative(offer, "hu").url == "https://pelda.hu/hu/meres/"


def docs_site(demo=False):
    aside = ("<aside><a href='/docs/button?tab=api'>Button</a> "
             "<a href='/docs/card?tab=api'>Card</a> <a href='/docs/modal'>Modal</a></aside>")
    code = "<pre><code>import { Component } from '@angular/core';\nlet x = 1;</code></pre>"
    states = ("<pre><code>states = ['Alabama', 'Alaska'];</code></pre><p>Try Alabama.</p>"
              if demo else "")
    pages = {"/": html("Docs", aside + "<main><h1>All components</h1></main>", lang="en")}
    for name in ("button", "card", "modal"):
        title = name.capitalize()
        body = (aside + f"<main><h1>{title}</h1><h2>Installation</h2>{code}"
                f"<p>The {title} component.</p>{states if name == 'button' else ''}</main>")
        pages[f"/docs/{name}"] = html(title, body, lang="en")
        pages[f"/docs/{name}?tab=api"] = html(title, body, lang="en")
    return site(pages, languages=("en",))


def test_docs_components_group_their_tab_pages():
    con = docs_site()
    run_rules(con)                                   # a blokkokat (kódblokk) a szabálykör építi
    roles = roles_by_path(con)
    assert roles["/"] == ("support", "home")
    assert {roles[f"/docs/{n}"][0] for n in ("button", "card", "modal")} == {"component"}
    assert roles["/docs/button?tab=api"] == ("component", "docs_component")
    assert len(entity_groups(page_roles(con))) == 3


# ---------------------------------------------------------------------------
# oldalhoz kötött entitás, csomagok, lépések
# ---------------------------------------------------------------------------


def test_offer_page_entity_with_aliases_packages_and_steps():
    con = business_site()
    run_rules(con)
    hu = "https://pelda.hu/hu/meres/"
    llm_entity(con, hu, "Diagnózis", "Diagnózis", "service")
    llm_entity(con, hu, "BigQuery integráció", "BigQuery integráció", "service")
    concept = llm_entity(con, "https://pelda.hu/blog/cikk/", "Bevezető a méréshez",
                         "Mérés", "concept")
    run = run_site(con, clock=lambda: NOON)
    (core,) = [r for r in by_name(con, "Mérés") if r[2] == "service"]
    entity_id, _, kind, _, tier, anchor, _ = core
    assert (kind, tier) == ("service", "core")
    assert anchor == con.execute("SELECT page_id FROM pages WHERE url = ?", [hu]).fetchone()[0]
    aliases = {row for row in con.execute(
        "SELECT alias, source FROM entity_aliases WHERE entity_id = ?", [entity_id]).fetchall()}
    assert {("Mérés nélkül csak találgatás.", "h1"), ("Mérés és Adatarchitektúra, Döntésekhez",
            "title"), ("Measurement & Data Architecture", "schema"), ("Mérés", "nav"),
            ("Mérés és adatarchitektúra", "anchor"), ("Growth", "hreflang")} <= aliases
    # A szövegben használt „Mérés” fogalom külön entitás marad (spec 9. pont).
    assert (concept, "concept") in {(r[0], r[2]) for r in by_name(con, "Mérés")}
    packages = dict(con.execute(
        "SELECT e.name, r.to_id FROM entity_relations r JOIN entities e ON e.entity_id = "
        "r.from_id WHERE r.type = 'part_of' ORDER BY e.name").fetchall())
    assert packages == {"BigQuery integráció": entity_id, "Szerver oldali mérés": entity_id}
    assert con.execute("SELECT count(*) FROM entities WHERE tier = 'package'").fetchone() == (2,)
    assert by_name(con, "Diagnózis")[0][2:5] == ("concept", "method", "step")
    rules = dict(con.execute("SELECT rule, count(*) FROM merge_log GROUP BY rule").fetchall())
    assert rules["hreflang_pricing_row"] == 2
    assert run.page_entities == 2 and run.steps == 1


def test_unequal_pricing_rows_are_not_paired():
    con = business_site()
    con.execute("UPDATE pages SET rendered_html = ? WHERE url = 'https://pelda.hu/en/measurement/'",
                [__import__("zstandard").ZstdCompressor().compress(html(
                    "Measurement & Data Architecture — Growth · Pelda",
                    EN_OFFER.replace("<tr><td>BigQuery integration · export</td><td>12-20 h</td>"
                                     "<td>€480</td></tr>", ""), head=EN_SERVICE,
                    lang="en").encode())])
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    assert con.execute("SELECT count(*) FROM merge_log WHERE rule = 'hreflang_pricing_row'"
                       ).fetchone() == (0,)


def test_article_entity_and_navigation_is_not_an_entity():
    con = business_site()
    run = run_rules(con)
    assert run.skipped.get("anchor_to_support_page") == 1           # „Kapcsolat” (4 oldalon)
    assert run.skipped.get("anchor_to_entity_page") == 2            # „Mérés”, „Blog”
    assert con.execute("SELECT count(*) FROM entities WHERE type = 'concept'").fetchone() == (0,)
    run_site(con, clock=lambda: NOON)
    (article,) = by_name(con, "Hogyan mérj jól")
    assert article[2:4] == ("work", "article")
    assert con.execute("SELECT alias FROM entity_aliases WHERE entity_id = ? AND source = 'nav'",
                       [article[0]]).fetchall() == [("Blog",)]


def test_rerun_is_stable():
    con = business_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    first = con.execute("SELECT name, type, tier, anchor_page_id FROM entities ORDER BY ALL"
                        ).fetchall()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    assert con.execute("SELECT name, type, tier, anchor_page_id FROM entities ORDER BY ALL"
                       ).fetchall() == first


def test_pricing_rows_skip_rates_and_find_card_names():
    blocks = [(1, "heading", "Árazás", None),
              (2, "paragraph", "Minden munka díja: 16000 Ft/óra.", None),
              (3, "heading", "Alap audit", None), (4, "paragraph", "8-14 h", None),
              (5, "paragraph", "128 000 Ft", None),
              (6, "paragraph", "Konverziós Sprint", None),
              (7, "paragraph", "Hosszú leírás, amely nem név, mert sok szóból áll össze most.", None),
              (8, "paragraph", "€880–1,440", None),
              (9, "table_row", "Conversion rate audit · mapping | 8h | €320",
               json.dumps([{"header": "Type", "value": "Conversion rate audit · mapping"}])),
              (10, "other", "Rate: €40 / hour", None)]
    assert pricing_rows(blocks) == [(3, "Alap audit"), (6, "Konverziós Sprint"),
                                    (9, "Conversion rate audit")]


def test_parenthetical_expansion_only_for_acronyms():
    assert expansions("Keresőoptimalizálás (SEO)") == ["Keresőoptimalizálás", "SEO"]
    assert expansions("SEO (Keresőoptimalizálás)") == ["SEO", "Keresőoptimalizálás"]
    assert expansions("Kéthetes Sprint (projektmenedzsment)") == []


# ---------------------------------------------------------------------------
# demó és sablon
# ---------------------------------------------------------------------------


def test_demo_entities_and_template_mentions():
    con = docs_site(demo=True)
    run_rules(con)
    button = "https://pelda.hu/docs/button"
    llm_entity(con, button, "Alabama", "Alabama", "place")
    llm_entity(con, button, "Try Alabama", "Alabama", "place")
    llm_entity(con, button, "Component", "Component", "tech")
    for name in ("button", "card", "modal"):
        llm_entity(con, f"https://pelda.hu/docs/{name}", "Installation", "Installation",
                   "concept")
    llm_entity(con, button, "The Button component", "The Button component", "concept")
    run = run_site(con, clock=lambda: NOON)
    assert run.demo == ["Alabama"]
    assert by_name(con, "Component")[0][6] == ["template"]          # import-sor 3 csoportban
    assert by_name(con, "Installation")[0][6] == ["template"]
    assert by_name(con, "The Button component")[0][6] is None
    assert run.thresholds["template_groups"] == 4                    # a fülek egy csoport
    components = con.execute("SELECT name, type, subtype FROM entities WHERE anchor_page_id "
                             "IS NOT NULL ORDER BY name").fetchall()
    assert components == [("Button", "tech", "component"), ("Card", "tech", "component"),
                          ("Modal", "tech", "component")]


def test_a_long_anchor_is_an_alias_when_it_is_the_article_title():
    title = "Hogyan mérj jól egy kis cég weboldalán lépésről lépésre"
    blog = ld({"@type": "BlogPosting", "headline": title})
    link = f"<p><a href='/blog/cikk/'>{title}</a></p>"
    con = site({
        "/": html("Pelda", f"<main><h1>Pelda</h1>{link}</main>"),
        "/lista/": html("Lista", f"<main><h1>Cikkek</h1>{link}</main>"),
        "/blog/cikk/": html(f"{title} · Pelda", f"<main><h1>{title}</h1><p>Szöveg.</p></main>",
                            head=blog),
    })
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    (article,) = by_name(con, title)
    assert con.execute("SELECT count(*) FROM entity_aliases WHERE entity_id = ? AND "
                       "source = 'anchor' AND alias = ?", [article[0], title]).fetchone() == (1,)
    assert con.execute("SELECT count(*) FROM page_entities WHERE entity_id = ? AND "
                       "position = 'anchor'", [article[0]]).fetchone() == (2,)


# ---------------------------------------------------------------------------
# összevonás, fogalom és ajánlat (PR B)
# ---------------------------------------------------------------------------


def test_normal_key_and_label_parts():
    assert normal_key("UX & Konverzió optimalizálás") == normal_key("UX & Konverzióoptimalizálás")
    assert normal_key("Two-Week Sprint (Structured Oversight)") == \
        normal_key("Two-Week Sprint — Structured Oversight")
    assert normal_key("page_load_time") == normal_key("Page load time")
    assert normal_key("UX & CRO") == normal_key("UX és CRO") == normal_key("UX and CRO")
    assert normal_key("SEO & Technikai alapok felmérése") == \
        normal_key("SEO és technikai alapok felmérése")
    assert normal_key("fejlesztés") == "fejlesztes"                  # az „és” csak szóként
    assert normal_key("ngx-bootstrap/datepicker") != normal_key("ngx-bootstrap Datepicker")
    assert normal_key("@angular/core") == "@angular/core"
    assert long_form("Google Eats Own Citation (GEO)") == "Google Eats Own Citation"
    assert long_form("GEO (Generative Engine Optimization)") == "Generative Engine Optimization"
    assert long_form("Kéthetes Sprint (projektmenedzsment)") is None
    assert label_parts("UX & Konverzióoptimalizálás") == ["UX", "Konverzióoptimalizálás"]
    assert label_parts("GEO – AI láthatóság") == ["GEO", "AI láthatóság"]
    assert label_parts("Keresőoptimalizálás (SEO)") == [
        "Keresőoptimalizálás (SEO)", "Keresőoptimalizálás", "SEO"]


def test_aligned_headings_pair_sections_from_both_ends_while_the_h3_counts_match():
    left = [(1, [2, 3]), (4, [5]), (6, []), (7, [8, 9])]
    right = [(11, [12, 13]), (14, [15, 16]), (17, [18, 19])]
    assert aligned_headings(left, right) == [(1, 11), (2, 12), (3, 13), (7, 17), (8, 18),
                                             (9, 19)]


def test_normalized_names_merge_within_a_type_and_log_it():
    con = business_site()
    run_rules(con)
    article = "https://pelda.hu/blog/cikk/"
    a = llm_entity(con, article, "Bevezető", "Entitás-architektúra", "concept")
    b = llm_entity(con, article, "méréshez", "Entitásarchitektúra", "concept")
    llm_entity(con, article, "A rendszer", "Entitásarchitektúra", "tech")
    run_site(con, clock=lambda: NOON)
    assert {r[2] for r in by_name(con, "Entitás-architektúra")} == {"concept"}
    assert by_name(con, "Entitásarchitektúra")[0][2] == "tech"
    assert con.execute("SELECT kept_id, removed_id, rule FROM merge_log WHERE rule = "
                       "'normalized_name'").fetchall() == [(a, b, "normalized_name")]


def test_hreflang_place_merges_the_same_heading_across_languages():
    con = business_site()
    run_rules(con)
    hu, en = "https://pelda.hu/hu/meres/", "https://pelda.hu/en/measurement/"
    diag = llm_entity(con, hu, "Diagnózis", "Diagnózis", "service")
    for url, text in ((hu, "Négy fázis"), (en, "Pricing")):
        llm_entity(con, url, text, text, "concept")
    run_site(con, clock=lambda: NOON)
    # HU: „Négy fázis” (1 H3) és „Árazás” (0 H3); EN: „Pricing” (0 H3): az első szakasz H3-száma
    # eltér, hátulról az „Árazás” ↔ „Pricing” párba áll, de az „Árazás”-nak nincs entitása.
    assert con.execute("SELECT count(*) FROM merge_log WHERE rule = 'hreflang_place'"
                       ).fetchone() == (0,)
    assert by_name(con, "Diagnózis")[0][0] == diag


def plant_llm_run(con, url, raw):
    """Egy tárolt LLM-futás a megadott oldal nyers rekordjával (a típusleválasztáshoz)."""
    page_id = con.execute("SELECT page_id FROM pages WHERE url = ?", [url]).fetchone()[0]
    (run_id,) = con.execute("INSERT INTO entity_runs (started_at, method, model, llm_calls) "
                            "VALUES (?, 'llm', 'x', 0) RETURNING run_id", [NOON]).fetchone()
    con.execute("INSERT INTO entity_run_pages (run_id, page_id, status, refined) "
                "VALUES (?, ?, 'done', ?)", [run_id, page_id, json.dumps({"entities": raw})])


def test_concept_mentions_leave_the_offer_and_the_offer_offers_them():
    con = business_site()
    run_rules(con)
    article = "https://pelda.hu/blog/cikk/"
    ordinal = con.execute("SELECT b.ordinal FROM blocks b JOIN pages p USING (page_id) WHERE "
                          "p.url = ? AND b.text LIKE 'Bevezető%'", [article]).fetchone()[0]
    llm_entity(con, article, "méréshez", "Mérés", "service")
    plant_llm_run(con, article, [{"block_id": f"b{ordinal}", "surface_form": "méréshez",
                                  "canonical_name": "mérés", "type": "concept"}])
    run_site(con, clock=lambda: NOON)
    (core,) = [r for r in by_name(con, "Mérés") if r[2] == "service"]
    concept = con.execute("SELECT entity_id FROM entities WHERE type = 'concept' AND "
                          "lower(name) = 'mérés'").fetchone()[0]
    assert con.execute("SELECT count(*) FROM page_entities WHERE entity_id = ? AND position = "
                       "'body'", [concept]).fetchone() == (1,)
    assert con.execute("SELECT rule, kept_id FROM merge_log WHERE rule = 'type_split'"
                       ).fetchall() == [("type_split", concept)]
    assert con.execute("SELECT from_id, to_id FROM entity_relations WHERE type = 'offers'"
                       ).fetchall() == [(core[0], concept)]


def test_person_links_are_cleared():
    con = business_site()
    con.execute("INSERT INTO entities (name, type, aliases, source, created_at, wikidata_id, "
                "wikipedia) VALUES ('Kiss Anna', 'person', [], 'schema', ?, 'Q1', 'hu:Anna')",
                [NOON])
    assert clear_person_links(con, lambda: NOON) == 1
    assert con.execute("SELECT wikidata_id, wikipedia, wikidata_status FROM entities").fetchall(
    ) == [(None, None, "none")]


def test_hreflang_place_merges_parallel_headings():
    service = ld({"@type": "Service", "name": "Execution", "url": "https://pelda.hu/en/exec/"})
    hu_body = ("<main><h1>Megvalósítás</h1><h2>Két munkamód</h2><h3>Közvetlen implementálás</h3>"
               "<p>A.</p><h3>Strukturált felügyelet</h3><p>B.</p></main>")
    en_body = ("<main><h1>Execution</h1><h2>Two ways to work</h2><h3>Direct Implementation</h3>"
               "<p>A.</p><h3>Structured Oversight</h3><p>B.</p></main>")
    con = site({"/": html("Pelda", "<main><h1>Pelda</h1></main>"),
                "/hu/exec/": html("Megvalósítás · Pelda", hu_body),
                "/en/exec/": html("Execution · Pelda", en_body, head=service, lang="en")},
               languages=("hu", "en"))
    con.execute("UPDATE pages SET hreflang = ? WHERE url LIKE '%/exec/'",
                [["hu|https://pelda.hu/hu/exec/", "en|https://pelda.hu/en/exec/"]])
    run_rules(con)
    hu, en = "https://pelda.hu/hu/exec/", "https://pelda.hu/en/exec/"
    direct_hu = llm_entity(con, hu, "Közvetlen implementálás", "Közvetlen implementálás",
                           "service")
    llm_entity(con, en, "Direct Implementation", "Direct Implementation", "service")
    oversight = llm_entity(con, hu, "Strukturált felügyelet", "Strukturált felügyelet", "service")
    llm_entity(con, en, "Structured Oversight", "Structured Oversight", "concept")
    run_site(con, clock=lambda: NOON)
    merged = con.execute("SELECT kept_id, kept_name, removed_name FROM merge_log "
                         "WHERE rule = 'hreflang_place' ORDER BY merge_id").fetchall()
    assert merged == [(direct_hu, "Közvetlen implementálás", "Direct Implementation"),
                      (oversight, "Strukturált felügyelet", "Structured Oversight")]


def test_confident_wikidata_merges_language_pairs_and_excluded_entities_get_no_link():
    from aaa2.entities.gate import KnowledgeBase
    from aaa2.entities.knowledge import link_entities, status_of
    from tests.test_entities_pipeline import Knowledge
    con = business_site()
    run_rules(con)
    article = "https://pelda.hu/blog/cikk/"
    hu = llm_entity(con, article, "méréshez", "Mérés", "concept")
    en = llm_entity(con, article, "A rendszer", "Measurement", "concept")
    api = llm_entity(con, article, "alapja", "show", "tech", "api_symbol")
    short = llm_entity(con, article, "Bevezető", "AB", "concept")
    ambiguous = llm_entity(con, article, "Hogyan", "Mérték", "concept")
    rival_name = llm_entity(con, article, "alapja", "Mértékegység", "concept")
    person = llm_entity(con, article, "Hogyan", "Kiss Anna", "person")
    run_site(con, clock=lambda: NOON)
    source = Knowledge(wikidata={"Mérés": "Q12453", "Measurement": "Q12453", "show": "Q9",
                                 "AB": "Q8", "Kiss Anna": "Q7",
                                 "Mérték": [("Q12453", "alias"), ("Q5", "label")],
                                 "Mértékegység": [("Q12453", "alias"), ("Q6", "label")]},
                       classes={"Q12453": (["academic major"], "process of assigning"),
                                "Q6": (["desa"], "village in Indonesia")})
    run = link_entities(con, KnowledgeBase(source), lambda: NOON, "hu")
    assert run.merged == 2
    assert con.execute("SELECT kept_id, removed_id, rule FROM merge_log WHERE rule = "
                       "'wikidata_confident' ORDER BY merge_id").fetchall() == [
        (hu, en, "wikidata_confident"), (hu, rival_name, "wikidata_confident")]
    rows = dict(con.execute("SELECT entity_id, wikidata_status FROM entities").fetchall())
    assert (rows[hu], rows[api], rows[short], rows[person]) == ("confident", "none", "none",
                                                                "none")
    assert rows[ambiguous] == "probable"
    # a rövid név csak a megerősítéshez kerül keresésre; a személy és az api_symbol soha
    assert {name for _, name in source.requests} == {"Mérés", "Measurement", "AB", "Mérték",
                                                        "Mértékegység"}
    assert status_of("concept", ["organization"], "") == "none"
    assert status_of("concept", [], "discipline focused on experience") == "confident"
    assert status_of("tech", ["type of object"], "") == "probable"


def exec_site(root_lang="hu"):
    service = ld({"@type": "Service", "name": "Execution", "url": "https://pelda.hu/en/exec/"})
    hu_body = ("<main><h1>Megvalósítás</h1><h2>Két munkamód</h2><h3>Közvetlen implementálás</h3>"
               "<p>A.</p><h3>Strukturált felügyelet</h3><p>B.</p></main>")
    en_body = ("<main><h1>Execution</h1><h2>Two ways to work</h2><h3>Direct Implementation</h3>"
               "<p>A.</p><h3>Structured Oversight</h3><p>B.</p></main>")
    con = site({"/": html("Pelda", "<main><h1>Pelda</h1></main>", lang=root_lang),
                "/hu/exec/": html("Megvalósítás · Pelda", hu_body),
                "/en/exec/": html("Execution · Pelda", en_body, head=service, lang="en")},
               languages=("hu", "en"))
    con.execute("UPDATE pages SET hreflang = ? WHERE url LIKE '%/exec/'",
                [["hu|https://pelda.hu/hu/exec/", "en|https://pelda.hu/en/exec/"]])
    return con


def test_overrides_set_the_tier_and_part_of_and_are_logged(tmp_path, monkeypatch):
    from aaa2.entities import overrides
    (tmp_path / "pelda.hu.toml").write_text(
        '[[offers]]\nnames = ["Strukturált felügyelet", "Structured Oversight"]\n'
        'tier = "work_mode"\npart_of = "Execution"\n', encoding="utf-8")
    monkeypatch.setattr(overrides, "SITES_DIR", tmp_path)
    con = exec_site()
    run_rules(con)
    step = llm_entity(con, "https://pelda.hu/hu/exec/", "Strukturált felügyelet",
                      "Strukturált felügyelet", "service")
    run = run_site(con, clock=lambda: NOON)
    assert run.overrides == 1
    assert con.execute("SELECT type, tier, type_changed_from FROM entities WHERE entity_id = ?",
                       [step]).fetchone() == ("service", "work_mode", "concept")
    core = con.execute("SELECT entity_id FROM entities WHERE tier = 'core'").fetchone()[0]
    assert con.execute("SELECT to_id, source FROM entity_relations WHERE from_id = ? AND "
                       "type = 'part_of'", [step]).fetchall() == [(core, "override")]
    evidence = json.loads(con.execute("SELECT evidence FROM merge_log WHERE rule = 'override'"
                                      ).fetchone()[0])
    assert (evidence["tier"], evidence["previous_tier"]) == ("work_mode", "step")


def test_canonical_language_is_the_root_page_language_unless_configured():
    from aaa2.entities.overrides import SiteConfig, canonical_language
    con = exec_site(root_lang="en")
    assert canonical_language(con) == "en"
    assert canonical_language(con, SiteConfig(canonical_lang="hu")) == "hu"
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    assert con.execute("SELECT name FROM entities WHERE tier = 'core'").fetchone() == (
        "Execution",)


def test_merge_keeps_one_mention_per_span_whatever_its_position():
    """Ugyanaz a szövegrész két entitáson, eltérő pozícióval (h1 és heading): az összevonás
    után egy említés marad, a források egyesítve."""
    from aaa2.entities.site import Merger
    con = business_site()
    run_rules(con)
    url = "https://pelda.hu/blog/cikk/"
    first = llm_entity(con, url, "Hogyan", "Hogyan", "concept")
    second = llm_entity(con, url, "Hogyan", "Hogyan mérj", "concept")
    con.execute("UPDATE page_entities SET position = 'heading' WHERE entity_id = ?", [second])
    Merger(con, 1, lambda: NOON).merge(first, second, "test")
    assert con.execute("SELECT count(*) FROM page_entities WHERE entity_id = ?",
                       [first]).fetchone() == (1,)
    assert con.execute("SELECT count(*) FROM entities WHERE entity_id = ?",
                       [second]).fetchone() == (0,)
