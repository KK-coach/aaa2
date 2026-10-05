"""A riport első körének ténykimenete (aaa2/functions/facts.py, az oldalnézet crawl-tényei)
szintetikus site-on, hálózat és LLM nélkül."""
import csv

from aaa2.entities.rules import run_rules
from aaa2.functions.facts import export_facts, link_rows, site_fact_rows, structured_rows
from aaa2.functions.findings import PAGE_FACT_COLUMNS, build_findings, export_views, page_facts
from aaa2.functions.graph import build_graph
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, ld, site
from tests.test_entities_site import NOON

BASE = "https://pelda.hu"


def facts_site():
    con = site({
        "/": html("Pelda", "<nav><a href='/meres/'>Mérés</a></nav><main><h1>Pelda</h1>"
                  "<p>Üdv. <a href='/meres/' rel='nofollow'>A mérésről</a> és "
                  "<a href='/nincs/'>egy hiányzó oldal</a>.</p></main>",
                  head=ld({"@type": "Organization", "name": "Pelda", "url": f"{BASE}/"})),
        "/meres/": html("Mérés · Pelda", "<main><h1>Mérés</h1><p>Szöveg. <a href='/'>Vissza</a>"
                        " és <a href='/blog/szappan-100g'>a szappan</a>, <a "
                        "href='/akcio/szappan-100g'>akcióban</a>.</p></main>",
                        head=ld({"@type": "Service", "name": "Mérés", "url": f"{BASE}/meres/"})),
        "/szappan-100g": html("Szappan", "<main><h1>Szappan</h1><p>Leírás.</p></main>")})
    con.execute("UPDATE pages SET meta_description = 'A mérésről szól.', word_count = 42, "
                "external_link_count = 3, noindex = false, final_url = url, "
                "hreflang = ['hu|https://pelda.hu/meres/', 'en|https://pelda.hu/en/measurement/'] "
                "WHERE url LIKE '%/meres/'")
    con.execute("UPDATE site SET target_country = 'HU', target_country_confidence = 'medium', "
                "market_scope = 'local', market_scope_city = 'Budapest', "
                "tech_signals = ['generator:WordPress 7'], trailing_slash = true, "
                "https_redirect = true, https_redirect_status = 301, robots_status = 200, "
                "robots_txt = 'User-agent: *'")
    return con


def test_link_rows_carry_the_anchor_position_and_nofollow():
    rows = link_rows(facts_site())
    assert list(rows[0]) == ["honnan", "hová", "feloldott céloldal", "feloldás módja",
                             "horgonyszöveg", "pozíció", "nofollow"]
    target = f"{BASE}/meres/"
    assert {"honnan": f"{BASE}/", "hová": target, "feloldott céloldal": target,
            "feloldás módja": "tárolt", "horgonyszöveg": "A mérésről", "pozíció": "body",
            "nofollow": "igen"} in rows
    assert {"honnan": f"{BASE}/", "hová": target, "feloldott céloldal": target,
            "feloldás módja": "tárolt", "horgonyszöveg": "Mérés", "pozíció": "nav",
            "nofollow": "nem"} in rows
    # a készleten kívüli belső cél is sor, feloldott céloldal és feloldási mód nélkül
    missing = next(row for row in rows if row["hová"] == f"{BASE}/nincs/")
    assert (missing["feloldott céloldal"], missing["feloldás módja"]) == ("", "")
    # a kategóriaúttal bővített cím a készletbeli oldalra oldódik fel; a „hová” az eredeti cél,
    # és a mód jelzi, hogy a cél következtetett, nem tárolt
    variants = {row["hová"]: (row["feloldott céloldal"], row["feloldás módja"]) for row in rows
                if row["hová"].endswith("/szappan-100g")}
    assert variants == {
        f"{BASE}/blog/szappan-100g": (f"{BASE}/szappan-100g", "következtetett"),
        f"{BASE}/akcio/szappan-100g": (f"{BASE}/szappan-100g", "következtetett")}
    assert [row["honnan"] for row in rows] == sorted(row["honnan"] for row in rows)


def test_a_language_prefix_is_not_a_category_path():
    # az /en/<slug> más nyelvű oldal, nem a /<slug> változata: nem oldódik fel
    from aaa2.engine import queries as crawl

    con = facts_site()
    for url in (f"{BASE}/en/szappan-100g", f"{BASE}/hu/szappan-100g",
                f"{BASE}/de/szappan-100g"):
        con.execute("INSERT INTO links (from_page_id, to_url, to_page_id, anchor, position, "
                    "ordinal) VALUES (1, ?, NULL, 'x', 'body', 90)", [url])
    # a site nyelve hu, a hreflang nyelvei hu és en; a „de” nem a site nyelve
    resolved = crawl.link_targets(con)
    assert f"{BASE}/en/szappan-100g" not in resolved
    assert f"{BASE}/hu/szappan-100g" not in resolved
    assert f"{BASE}/de/szappan-100g" in resolved
    by_url = {link.to_url: link for link in crawl.links(con)}
    assert (by_url[f"{BASE}/en/szappan-100g"].to_page_id,
            by_url[f"{BASE}/en/szappan-100g"].resolution) == (None, None)
    assert by_url[f"{BASE}/blog/szappan-100g"].resolution == "inferred"
    assert by_url[f"{BASE}/meres/"].resolution == "stored"


def test_the_graph_counts_an_inferred_link_as_an_inbound_link():
    # a gráf ugyanazt a feloldott célt használja, mint a kimenet
    from aaa2.functions.graph import _Graph

    con = facts_site()
    ids = dict(con.execute("SELECT url, page_id FROM pages").fetchall())
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    graph = _Graph(con, {})
    inbound = graph.inbound[ids[f"{BASE}/szappan-100g"]]
    assert sorted(anchor for _, anchor, _ in inbound) == ["a szappan", "akcióban"]


def test_a_nofollow_that_was_not_stored_stays_empty():
    con = facts_site()
    con.execute("UPDATE links SET nofollow = NULL WHERE anchor = 'Vissza'")
    assert next(row for row in link_rows(con) if row["horgonyszöveg"] == "Vissza")[
        "nofollow"] == ""


def test_link_targets_resolve_only_unambiguous_root_level_slugs():
    from aaa2.engine import queries as crawl

    con = facts_site()
    ids = dict(con.execute("SELECT url, page_id FROM pages").fetchall())
    assert crawl.link_targets(con) == {
        f"{BASE}/blog/szappan-100g": ids[f"{BASE}/szappan-100g"],
        f"{BASE}/akcio/szappan-100g": ids[f"{BASE}/szappan-100g"]}
    # dupla perjeles cím, lekérdezéses cím és több szegmensű készletbeli oldal nem oldódik fel
    for url in (f"{BASE}//szappan-100g", f"{BASE}/blog/szappan-100g?szin=kek",
                f"{BASE}/hu/valami/meres"):
        con.execute("INSERT INTO links (from_page_id, to_url, to_page_id, anchor, position, "
                    "ordinal) VALUES (1, ?, NULL, 'x', 'body', 90)", [url])
    assert len(crawl.link_targets(con)) == 2


def test_structured_rows_list_the_stored_items_without_judging_them():
    con = facts_site()
    con.execute("INSERT INTO structured_data (page_id, syntax, type, json, ordinal) SELECT "
                "page_id, 'opengraph', 'website', '{}', 1 FROM pages WHERE url = ?", [f"{BASE}/"])
    assert structured_rows(con) == [
        {"url": f"{BASE}/", "formátum": "JSON-LD", "típus": "Organization"},
        {"url": f"{BASE}/", "formátum": "Open Graph", "típus": "website"},
        {"url": f"{BASE}/meres/", "formátum": "JSON-LD", "típus": "Service"}]


def test_site_facts_are_key_value_rows_and_missing_values_stay_empty():
    facts = {row["tény"]: row["érték"] for row in site_fact_rows(facts_site())}
    assert facts["nyelvek"] == "hu" and facts["célország"] == "HU"
    assert facts["célország megbízhatósága"] == "medium"
    assert facts["piaci hatókör"] == "local (Budapest)"
    assert facts["technológia"] == "" and facts["technológiai jelek"] == "generator:WordPress 7"
    assert facts["HTTPS-átirányítás"] == "igen (301)" and facts["záró perjel"] == "igen"
    assert facts["robots.txt státusz"] == 200 and facts["robots.txt"] == "User-agent: *"
    assert facts["oldalak száma"] == 3 and facts["oldalak státusz szerint: 200"] == 3


def test_page_facts_and_the_page_view_columns(tmp_path):
    con = facts_site()
    ids = dict(con.execute("SELECT url, page_id FROM pages").fetchall())
    facts = page_facts(con)
    assert facts[ids[f"{BASE}/meres/"]] == {
        "státusz": 200, "végső URL": f"{BASE}/meres/", "meta description": "A mérésről szól.",
        "noindex": "nem", "szószám": 42, "külső linkek": 3,
        "hreflang": "hu|https://pelda.hu/meres/ | en|https://pelda.hu/en/measurement/",
        "bejövő belső linkek": 2, "hivatkozó oldalak": 1, "kimenő belső linkek": 3}
    # a kezdőoldalról három link megy ki (kettő a mérésre, egy a hiányzó oldalra)
    assert facts[ids[f"{BASE}/"]]["kimenő belső linkek"] == 3
    # a kategóriaúttal bővített címen érkező két link a készletbeli oldalé, egy forrásoldalról
    szappan = facts[ids[f"{BASE}/szappan-100g"]]
    assert (szappan["bejövő belső linkek"], szappan["hivatkozó oldalak"]) == (2, 1)
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    paths = {**export_views(con, tmp_path, "pelda"), **export_facts(con, tmp_path, "pelda")}
    with paths["pages"].open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0])[-len(PAGE_FACT_COLUMNS):] == list(PAGE_FACT_COLUMNS)
    meres = next(row for row in rows if row["url"].endswith("/meres/"))
    assert (meres["státusz"], meres["szószám"], meres["bejövő belső linkek"]) == ("200", "42", "2")
    assert sorted(path.name for key, path in paths.items()
                  if key in ("links", "structured", "site_facts")) == [
        "pelda-view-links.csv", "pelda-view-site-facts.csv", "pelda-view-structured-data.csv"]
    with paths["site_facts"].open(encoding="utf-8-sig", newline="") as handle:
        assert next(csv.reader(handle)) == ["tény", "érték"]
