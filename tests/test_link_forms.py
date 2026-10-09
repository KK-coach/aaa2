"""A linkek eredeti alakja (`links.raw_url`), a nem navigációs hrefek, a visszatöltés a tárolt
DOM-ból, az átirányítási lánc és a linkelt alak mérése, valamint a „belső link nem a végleges
címre mutat” megállapítás; szintetikus site-on, hálózat és LLM nélkül."""
import asyncio
import json

import httpx

from aaa2.engine.crawl import _redirect_columns, probe_link_variants
from aaa2.engine.normalize import UrlPolicy, form_differences
from aaa2.engine.parse import parse_page
from aaa2.engine.queries import link_variants
from aaa2.engine.relink import relink
from aaa2.engine.render import RenderResult
from aaa2.entities.rules import run_rules
from aaa2.functions.findings import build_findings, stored_findings
from aaa2.functions.graph import build_graph
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, site
from tests.test_entities_site import NOON

BASE = "https://pelda.hu"
POLICY = UrlPolicy.from_seed(f"{BASE}/")


def links_of(body, url=f"{BASE}/oldal/", head=""):
    return [(link.raw_url, link.to_url, link.ordinal)
            for link in parse_page(html("Oldal", body, head=head), url, POLICY).links]


def test_the_parser_keeps_the_original_address_and_skips_non_navigational_hrefs():
    body = ('<a href="/szolg">Szolgáltatások</a><a href="#">Fel</a><a href="">Üres</a>'
            '<a href="#szakasz">Szakasz</a><a href="javascript:void(0)">Gomb</a>'
            '<a href="HTTP://WWW.Pelda.hu/Blog/?utm_source=x#top">Blog</a>'
            '<a href="/oldal/#lent">Ugyanez az oldal</a>')
    assert links_of(body) == [
        (f"{BASE}/szolg", f"{BASE}/szolg", 1),
        # a host a seed formájára, a követőparaméter eldobva; a protokoll marad, ha a site
        # nem irányít át https-re
        ("HTTP://WWW.Pelda.hu/Blog/?utm_source=x", "http://pelda.hu/Blog/", 2),
        # a töredékes link másik href-fel link marad, a címe töredék nélkül áll
        (f"{BASE}/oldal/", f"{BASE}/oldal/", 3)]
    # a `<base href>` mellett sem oldódik fel a `#` a base címre
    based = links_of('<a href="#">Fül</a><a href="a/">A</a>', head='<base href="/app/">')
    assert based == [(f"{BASE}/app/a/", f"{BASE}/app/a/", 1)]


def test_form_differences_on_the_same_host():
    assert form_differences(f"{BASE}/a", f"{BASE}/a/") == ("záró perjel",)
    assert form_differences("http://www.pelda.hu/a/", f"{BASE}/a/") == ("protokoll", "www")
    assert form_differences(f"{BASE}/Cikk", f"{BASE}/cikk/") == ("záró perjel", "kis/nagybetű")
    assert form_differences(f"{BASE}/Cikk/", f"{BASE}/cikk/") == ("kis/nagybetű",)
    # nem eltérés: a gyökér üres útvonala, a lekérdezés, az útvonal kódolása, más host
    assert form_differences(BASE, f"{BASE}/") == ()
    assert form_differences(f"{BASE}/a/?utm_source=x", f"{BASE}/a/") == ()
    assert form_differences(f"{BASE}/sérült/", f"{BASE}/s%C3%A9r%C3%BClt/") == ()
    assert form_differences("http://masik.hu/a", f"{BASE}/a/") == ()
    assert form_differences("http://bolt.pelda.hu/a", f"{BASE}/a/") == ()


def link_site():
    def page(title, body):
        menu = ('<header><nav><a href="/szolg/">Szolgáltatások</a><a href="/blog/">Blog</a></nav>'
                "</header>")
        return html(f"{title} · Pelda", f"{menu}<main><h1>{title}</h1>{body}</main>"
                    '<footer><a href="/adat">Adatvédelem</a><a href="#">Fel</a></footer>')
    pages = {
        "/": page("Pelda", '<p><a href="/szolg/">Szolgáltatások</a></p>'),
        "/szolg/": page("Szolgáltatások", '<p><a href="/allas">Állás</a> és a '
                        '<a href="http://kulso.pelda.hu/x">másik host</a>.</p>'),
        "/blog/": page("Blog", '<p><a href="/regi/">Régi cikk</a> <a href="#">Fel</a></p>'),
        "/adat/": page("Adatvédelem", "<p>Szöveg.</p>"),
        "/allas/": page("Állás", "<p>Szöveg.</p>"),
        "/regi/": page("Régi", "<p>Szöveg.</p>"),
        "/uj/": page("Új", "<p>Szöveg.</p>")}
    con = site(pages)
    # a site perjeles formára normalizál: a perjel nélküli linkcél a perjeles oldalé
    con.execute("UPDATE site SET trailing_slash = true")
    con.execute("UPDATE links SET to_url = to_url || '/' WHERE NOT ends_with(to_url, '/')")
    con.execute("UPDATE links SET to_page_id = pages.page_id FROM pages "
                "WHERE links.to_url = pages.url")
    return con


def test_relink_from_the_stored_dom_fills_the_original_address_and_drops_fragment_links():
    con = link_site()
    # a korábbi parser sorai: eredeti cím nélkül, a `#` link az oldal saját címére oldva
    con.execute("UPDATE links SET raw_url = NULL")
    targets = dict(con.execute("SELECT to_url, to_page_id FROM links WHERE to_page_id IS NOT NULL"
                               ).fetchall())
    for page_id, url in con.execute("SELECT page_id, url FROM pages").fetchall():
        con.execute("INSERT INTO links (from_page_id, to_url, to_page_id, anchor, position, "
                    "nofollow, ordinal) VALUES (?, ?, ?, 'Fel', 'footer', false, 99)",
                    [page_id, url, page_id])
    before = con.execute("SELECT count(*) FROM links").fetchone()[0]
    run = relink(con)
    assert (run.pages, run.before, run.dropped, run.mismatched) == (7, before, 7, [])
    assert run.after == run.with_raw_url == before - 7
    assert con.execute("SELECT count(*) FROM links WHERE raw_url IS NULL").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM links WHERE anchor = 'Fel'").fetchone()[0] == 0
    # a céloldal a régi sorból öröklődik, a sorszám újraszámolódik
    assert dict(con.execute("SELECT to_url, to_page_id FROM links WHERE to_page_id IS NOT NULL"
                            ).fetchall()) == targets
    assert con.execute("SELECT max(ordinal) FROM links").fetchone()[0] < 99
    assert con.execute("SELECT raw_url, to_url FROM links WHERE anchor = 'Állás'").fetchall() == [
        (f"{BASE}/allas", f"{BASE}/allas/")]
    # ahol a tárolt sorok nem a mai parser kimenetének felelnek meg, az oldal érintetlen marad
    con.execute("UPDATE links SET anchor = 'átírt' WHERE anchor = 'Állás'")
    again = relink(con)
    assert again.mismatched == [f"{BASE}/szolg/"] and again.dropped == 0
    assert con.execute("SELECT count(*) FROM links WHERE anchor = 'átírt'").fetchone()[0] == 1


def test_the_redirect_chain_is_stored_with_the_page():
    plain = RenderResult(url=f"{BASE}/a/", status=200)
    assert _redirect_columns(plain) == {"redirect_hops": 0, "redirect_chain": None}
    moved = RenderResult(url=f"{BASE}/a", status=200, final_url=f"{BASE}/b/",
                         redirects=((301, f"{BASE}/a"), (302, f"{BASE}/a/")))
    columns = _redirect_columns(moved)
    assert columns["redirect_hops"] == 2
    assert json.loads(columns["redirect_chain"]) == [{"status": 301, "url": f"{BASE}/a"},
                                                     {"status": 302, "url": f"{BASE}/a/"}]


def probe(con, handler, **options):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await probe_link_variants(con, client, **options)
    return asyncio.run(run())


def test_link_variants_are_probed_only_for_differing_forms_without_the_body():
    con = link_site()
    asked = []

    def handler(request):
        asked.append((request.method, str(request.url)))
        url = str(request.url)
        if url == f"{BASE}/adat":
            return httpx.Response(301, headers={"location": f"{BASE}/adat/"})
        if url == f"{BASE}/allas":
            raise httpx.ConnectError("nincs válasz", request=request)
        if url == "http://kulso.pelda.hu/x" and request.method == "HEAD":
            return httpx.Response(405)                  # a HEAD nem támogatott: GET, törzs nélkül
        return httpx.Response(200, text="ok")

    run = probe(con, handler, concurrency=1)            # az azonos alakú linkcím nem megy
    assert (run.probed, run.over_limit, run.blocked) == (3, 0, 0)
    assert asked == [("HEAD", "http://kulso.pelda.hu/x"), ("GET", "http://kulso.pelda.hu/x"),
                     ("HEAD", f"{BASE}/adat"), ("HEAD", f"{BASE}/adat/"),
                     ("HEAD", f"{BASE}/allas")]
    found = {variant.raw_url: variant for variant in link_variants(con)}
    moved = found[f"{BASE}/adat"]
    assert (moved.status, moved.final_url, moved.hops) == (200, f"{BASE}/adat/", 1)
    assert moved.chain == [{"status": 301, "url": f"{BASE}/adat"}]
    failed = found[f"{BASE}/allas"]
    assert failed.status is None and failed.hops is None and "ConnectError" in failed.error
    assert found["http://kulso.pelda.hu/x"].status == 200


def test_the_probe_respects_robots_and_the_address_limit(monkeypatch):
    from aaa2.engine import crawl as crawl_module
    from aaa2.engine.frontier import Robots

    con = link_site()
    asked = []

    def handler(request):
        asked.append(str(request.url))
        return httpx.Response(200)

    # a robots.txt által tiltott címre nincs kérés, és a sor sem keletkezik
    run = probe(con, handler, robots=Robots.parse("User-agent: *\nDisallow: /adat\n"))
    assert (run.probed, run.blocked, run.over_limit) == (2, 1, 0)
    assert f"{BASE}/adat" not in asked
    assert f"{BASE}/adat" not in {variant.raw_url for variant in link_variants(con)}
    # a korlát fölött nincs kérés: cím szerinti sorrendben az elsők mennek
    asked.clear()
    monkeypatch.setattr(crawl_module, "VARIANT_PROBE_LIMIT", 1)
    run = probe(con, handler)
    assert (run.probed, run.over_limit, run.blocked) == (1, 2, 0)
    assert asked == ["http://kulso.pelda.hu/x"]
    assert [variant.raw_url for variant in link_variants(con)] == ["http://kulso.pelda.hu/x"]


def link_findings(con):
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    return {finding.evidence["raw_url"].removeprefix(BASE): finding
            for finding in stored_findings(con) if finding.type == "link_not_final_url"}


def test_links_that_do_not_point_at_the_final_address():
    con = link_site()
    # a /regi/ átirányít az /uj/ oldalra (két lépésben); a blog csak lekérdezésben tér el
    con.execute("UPDATE pages SET status = 301, final_url = ?, redirect_hops = 2 WHERE url = ?",
                [f"{BASE}/uj/", f"{BASE}/regi/"])
    con.execute("UPDATE pages SET final_url = ? WHERE url = ?",
                [f"{BASE}/blog/?oldal=2", f"{BASE}/blog/"])
    found = link_findings(con)
    assert set(found) == {"/adat", "/allas", "/regi/"}
    footer = found["/adat"]                             # a láblécben, minden oldalon: közepes
    assert footer.severity == "medium"
    assert footer.evidence["differences"] == ["záró perjel"]
    # a hét oldalból hat forrás: az átirányító /regi/ nem oldal-csomópont
    assert (footer.evidence["source_pages"], footer.evidence["by_area"]) == (6, {"lábléc": 6})
    assert footer.evidence["stored_url"] == f"{BASE}/adat/"
    # mérés nélkül: a végleges cím helyén a tárolt oldal címe, a státusz és az ugrásszám nem mért
    assert footer.evidence["final_url"] is None
    assert footer.evidence["final_note"] == "a tárolt oldal címe"
    assert (footer.evidence["linked_status"], footer.evidence["hops"]) == ("nem mért", "nem mért")
    assert "a tárolt oldal címe" in footer.summary and "záró perjel" in footer.summary
    assert footer.evidence["pages"][0] == {"url": f"{BASE}/", "areas": ["lábléc"],
                                           "anchors": ["Adatvédelem"]}
    single = found["/allas"]                            # egyedi tartalmi link: alacsony
    assert single.severity == "low" and single.evidence["by_area"] == {"tartalom": 1}
    assert single.evidence["pages"] == [{"url": f"{BASE}/szolg/", "areas": ["tartalom"],
                                         "anchors": ["Állás"]}]
    moved = found["/regi/"]                             # átirányítás: a tárolt lánc szerint
    assert moved.severity == "low" and moved.evidence["differences"] == ["átirányítás"]
    assert moved.evidence["final_url"] == f"{BASE}/uj/"
    assert (moved.evidence["linked_status"], moved.evidence["hops"]) == (301, 2)
    assert f"a végleges cím: {BASE}/uj/" in moved.summary
    # a linkelt alak mért válaszával a státusz, az ugrásszám és a végleges cím a mérésé
    con.execute("INSERT INTO link_variants (raw_url, normalized_url, status, final_url, hops, "
                "fetched_at) VALUES (?, ?, 200, ?, 1, ?)",
                [f"{BASE}/adat", f"{BASE}/adat/", f"{BASE}/adat/", NOON])
    build_findings(con)
    measured = next(finding for finding in stored_findings(con)
                    if finding.type == "link_not_final_url"
                    and finding.evidence["raw_url"] == f"{BASE}/adat")
    assert measured.evidence["measured"] is True
    assert (measured.evidence["linked_status"], measured.evidence["hops"],
            measured.evidence["final_url"]) == (200, 1, f"{BASE}/adat/")
    assert "note" not in measured.evidence
    assert measured.evidence["differences"] == ["záró perjel"]


def measured_finding(con, raw_url):
    build_findings(con)
    return next(finding for finding in stored_findings(con)
                if finding.type == "link_not_final_url"
                and finding.evidence["raw_url"] == raw_url)


def test_a_linked_form_that_answers_itself_is_served_at_two_addresses():
    con = link_site()
    link_findings(con)
    # a linkelt alak is 200-at ad, átirányítás nélkül: a végleges cím a tárolt cél
    con.execute("INSERT INTO link_variants (raw_url, normalized_url, status, final_url, hops, "
                "fetched_at) VALUES (?, ?, 200, ?, 0, ?)",
                [f"{BASE}/adat", f"{BASE}/adat/", f"{BASE}/adat", NOON])
    both = measured_finding(con, f"{BASE}/adat")
    assert both.evidence["final_url"] == f"{BASE}/adat/"
    assert both.evidence["note"] == (
        "a linkelt alak is 200-at ad, átirányítás nélkül (két címen elérhető)")
    assert both.evidence["differences"] == ["záró perjel", "két címen elérhető"]
    assert (both.evidence["linked_status"], both.evidence["hops"]) == (200, 0)
    assert f"a végleges cím: {BASE}/adat/" in both.summary
    assert "két címen elérhető" in both.summary
    # hibával visszatért mérés: nem mért, mint a rögzített készleten
    con.execute("UPDATE link_variants SET status = NULL, final_url = NULL, hops = NULL, "
                "error = 'ConnectError: nincs válasz'")
    failed = measured_finding(con, f"{BASE}/adat")
    assert failed.evidence["measured"] is False and "note" not in failed.evidence
    assert failed.evidence["final_url"] is None
    assert failed.evidence["final_note"] == "a tárolt oldal címe"
    assert (failed.evidence["linked_status"], failed.evidence["hops"]) == ("nem mért", "nem mért")
    assert failed.evidence["differences"] == ["záró perjel"]


def test_a_menu_redirect_already_reported_is_not_repeated():
    con = link_site()
    con.execute("UPDATE pages SET status = 301, final_url = ? WHERE url = ?",
                [f"{BASE}/uj/", f"{BASE}/blog/"])
    found = link_findings(con)
    assert "/blog/" not in found                        # a menülinket a menu_broken_target jelzi
    assert [finding.evidence["url"] for finding in stored_findings(con)
            if finding.type == "menu_broken_target"] == [f"{BASE}/blog/"]
