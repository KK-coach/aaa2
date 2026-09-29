"""Site-szintű entitások (aaa2/entities/pages.py, site.py; M2/6 PR A) szintetikus site-okon,
hálózat és LLM nélkül: oldalszerepek, oldalhoz kötött entitás, csomagok, lépések, demó- és
sablonjelölés, a módosított anchor-szabály."""
import json
from datetime import UTC, datetime

from aaa2.entities.pages import entity_groups, page_roles, representative
from aaa2.entities.rules import run_rules
from aaa2.entities.site import expansions, pricing_rows, run_site
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
