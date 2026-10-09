"""A robots.txt botonkénti kiértékelése (frontier.Robots), a tiltott linkcél nyoma a crawl-sorban,
a „belső link a robots.txt által tiltott címre mutat” és a „robots.txt kitilt egy botot”
megállapítás, valamint a robots.txt tényei a site-áttekintőben; szintetikus site-on, hálózat és
LLM nélkül."""
from aaa2.db.connect import connect
from aaa2.engine.frontier import CHECKED_BOTS, Frontier, QueueItem, Robots
from aaa2.engine.normalize import UrlPolicy
from aaa2.engine.queries import queue_size, robots_blocked_urls
from aaa2.entities.rules import run_rules
from aaa2.functions.findings import (
    _finding_html,
    build_findings,
    export_views,
    robots_intended,
    site_views,
    stored_findings,
)
from aaa2.functions.graph import build_graph
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, site
from tests.test_entities_site import NOON

BASE = "https://pelda.hu"
ROBOTS = """
User-agent: *
Disallow: /kosar
Disallow: /belso/
Allow: /belso/nyilt
Disallow: /egyenlo
Allow: /egyenlo

User-agent: Googlebot
Disallow: /titkos/

User-agent: GPTBot
User-agent: ClaudeBot
Disallow: /

User-agent: MJ12bot
Disallow: /

User-agent: Bingbot
Disallow:
"""


def test_a_bot_follows_its_own_group_otherwise_the_star_group():
    robots = Robots.parse(ROBOTS)
    assert CHECKED_BOTS == ("Googlebot", "Bingbot", "GPTBot", "ClaudeBot", "PerplexityBot",
                            "Google-Extended")
    # a `*` csoport viselkedése változatlan: a leghosszabb szabály dönt, egyenlő hossznál az Allow
    assert not robots.allowed(f"{BASE}/kosar/") and robots.allowed(f"{BASE}/belso/nyilt/x")
    assert not robots.allowed(f"{BASE}/belso/zart") and robots.allowed(f"{BASE}/egyenlo")
    assert robots.blocking_rule(f"{BASE}/belso/zart") == "Disallow: /belso/"
    assert robots.blocking_rule(f"{BASE}/egyenlo") is None
    assert robots.sources == ("Disallow: /kosar", "Disallow: /belso/", "Allow: /belso/nyilt",
                              "Disallow: /egyenlo", "Allow: /egyenlo")
    # saját csoport felülírja a `*`-ot: a Googlebotra a /kosar nem tilos, a /titkos/ igen
    google = robots.for_bot("Googlebot")
    assert robots.group_of("googlebot") == "googlebot"
    assert google.allowed(f"{BASE}/kosar/") and not google.allowed(f"{BASE}/titkos/x")
    assert google.blocking_rule(f"{BASE}/titkos/x") == "Disallow: /titkos/"
    # az üres Disallow csoport is csoport: a Bingbotra semmi sem tilos
    assert robots.group_of("Bingbot") == "bingbot"
    assert robots.for_bot("Bingbot").allowed(f"{BASE}/kosar/")
    # saját csoport nélkül a `*` csoport érvényes
    assert robots.group_of("PerplexityBot") == "*"
    assert not robots.for_bot("PerplexityBot").allowed(f"{BASE}/kosar/")
    # közös csoport két botnak; teljes tiltás
    assert robots.for_bot("GPTBot").blocks_everything()
    assert robots.for_bot("ClaudeBot").blocks_everything()
    assert not robots.blocks_everything() and not google.blocks_everything()
    # csoportok nélkül nincs tiltás
    empty = Robots.parse("Sitemap: https://pelda.hu/sitemap.xml\n")
    assert empty.group_of("Googlebot") is None and empty.for_bot("Googlebot").allowed(BASE)


def test_the_crawl_queue_keeps_a_trace_of_blocked_link_targets():
    con = connect(":memory:")
    policy = UrlPolicy.from_seed(f"{BASE}/")
    frontier = Frontier(con, policy, robots=Robots.parse(ROBOTS), max_pages=3)
    source = QueueItem(f"{BASE}/", 0, 0)
    links = [(f"{BASE}/kosar", "nav"), (f"{BASE}/a/", "body"), (f"{BASE}/belso/zart", "body"),
             (f"{BASE}/kosar", "footer"), ("https://masik.hu/kosar", "body"),
             (f"{BASE}/b/", "body"), (f"{BASE}/c/", "body"), (f"{BASE}/d/", "body")]
    assert frontier.add_links(source, links) == 3               # a tiltott cím nem kerül a sorba
    assert robots_blocked_urls(con) == {f"{BASE}/belso/zart": "*: Disallow: /belso/",
                                        f"{BASE}/kosar": "*: Disallow: /kosar"}
    assert con.execute("SELECT status, discovered_from FROM crawl_queue WHERE url = ?",
                       [f"{BASE}/kosar"]).fetchone() == ("robots_blocked", f"{BASE}/")
    # a tiltott sor nem várakozik, és nem számít a korlátba
    assert [item.url for item in frontier.next_batch(10)] == [f"{BASE}/a/", f"{BASE}/b/",
                                                               f"{BASE}/c/"]
    assert queue_size(con) == 3 and frontier.limit_skipped == {f"{BASE}/d/"}
    # új példány sem számolja; sitemap-módban (a linkek nem kerülnek a sorba) is marad nyom
    again = Frontier(con, policy, robots=Robots.parse(ROBOTS), max_pages=3, follow_links=False)
    assert again.add_links(source, [(f"{BASE}/kosar/penztar", "body")]) == 0
    assert f"{BASE}/kosar/penztar" in robots_blocked_urls(con)
    assert queue_size(con) == 3


def robots_site(robots_txt, menu_extra="", body_extra="", sitemap=()):
    def page(title, body=""):
        menu = (f'<header><a class="logo" href="/">Pelda</a><nav><a href="/szolg/">Szolgáltatások'
                f'</a><a href="/blog/">Blog</a>{menu_extra}</nav></header>')
        return html(f"{title} · Pelda", f"{menu}<main><h1>{title}</h1><p>Szöveg.</p>{body}</main>"
                    '<footer><a href="/adat/">Adatvédelem</a></footer>')
    con = site({"/": page("Pelda", body_extra), "/szolg/": page("Szolgáltatások"),
                "/blog/": page("Blog"), "/adat/": page("Adatvédelem")})
    con.execute("UPDATE site SET robots_txt = ?, robots_status = 200", [robots_txt])
    if sitemap:
        con.execute("INSERT INTO sitemap_files (snapshot, ordinal, url, source, found, urls, "
                    "fetched_at) VALUES ('crawl', 0, ?, 'robots', true, ?, ?)",
                    [f"{BASE}/sitemap.xml", len(sitemap), NOON])
        for ordinal, path in enumerate(sitemap):
            con.execute("INSERT INTO sitemap_urls (snapshot, ordinal, raw_url, url, lastmod, "
                        "sitemap_file, source, fetched_at) VALUES ('crawl', ?, ?, ?, NULL, ?, "
                        "'robots', ?)", [ordinal, f"{BASE}{path}", f"{BASE}{path}",
                                         f"{BASE}/sitemap.xml", NOON])
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    return con


def of_type(con, kind):
    return [finding for finding in stored_findings(con) if finding.type == kind]


def test_links_to_blocked_addresses_by_rule_with_severity():
    assert robots_intended(f"{BASE}/kosar/") == "kosár / pénztár"
    assert robots_intended(f"{BASE}/termekek/?sort=ar") == "szűrő / rendezés / lapozás"
    assert robots_intended(f"{BASE}/arlista/") is None
    con = robots_site(
        "User-agent: *\nDisallow: /kosar\nDisallow: /arlista\nDisallow: /akcio\n"
        "Disallow: /regi\n\nUser-agent: Googlebot\nDisallow: /regi\n",
        menu_extra='<a href="/kosar/">Kosár</a><a href="/arlista/">Árlista</a>',
        body_extra='<p><a href="/akcio/">Akció</a> <a href="/regi/">Régi</a></p>',
        sitemap=("/akcio/",))
    found = {finding.evidence["rule"]: finding for finding in of_type(con, "robots_blocked_link")}
    assert set(found) == {"Disallow: /kosar", "Disallow: /arlista", "Disallow: /akcio",
                          "Disallow: /regi"}
    cart = found["Disallow: /kosar"]                    # szándékos tiltás: low, a menüből is
    assert cart.severity == "low"
    assert cart.evidence["urls"][0]["intended"] == "kosár / pénztár"
    assert "a robots.txt tiltja a bejárását: a keresők nem látják a tartalmát" in cart.summary
    assert "1 cím, 4 forrásoldalról" in cart.summary
    menu = found["Disallow: /arlista"]                  # nem szándékos, menüből linkelt: high
    assert menu.severity == "high"
    item = menu.evidence["urls"][0]
    assert (item["url"], item["source_pages"], item["by_area"]) == (
        f"{BASE}/arlista/", 4, {"menü": 4})
    assert item["in_sitemap"] is False and item["intended"] is None
    # botonként: a `*` szerint tiltott; a Googlebotnak saját csoportja van, rá nem vonatkozik
    assert item["bots"]["saját bejáró (*)"] is True and item["bots"]["Googlebot"] is False
    assert item["bots"]["Bingbot"] is True and item["bots"]["GPTBot"] is True
    listed = found["Disallow: /akcio"]                  # nem szándékos, a sitemapben: high
    assert listed.severity == "high" and listed.evidence["urls"][0]["in_sitemap"] is True
    assert listed.evidence["urls"][0]["by_area"] == {"tartalom": 1}
    plain = found["Disallow: /regi"]                    # nem szándékos, csak tartalmi link: medium
    assert plain.severity == "medium"
    assert plain.evidence["urls"][0]["bots"]["Googlebot"] is True
    assert [page["url"] for page in plain.evidence["pages"]] == [f"{BASE}/"]
    # tiltás nélkül nincs megállapítás
    assert of_type(robots_site("User-agent: *\nAllow: /\n"), "robots_blocked_link") == []


def test_a_bot_blocked_from_the_site_or_a_main_section():
    con = robots_site(
        "User-agent: *\nAllow: /\n\nUser-agent: Googlebot\nDisallow: /szolg/\n\n"
        "User-agent: GPTBot\nUser-agent: Google-Extended\nDisallow: /\n\n"
        "User-agent: MJ12bot\nDisallow: /\n")
    found = {finding.evidence["kind"]: finding for finding in of_type(con, "robots_bot_blocked")}
    search = found["search"]                            # keresőbot, fő szekció: high
    assert search.severity == "high" and "Googlebot" in search.summary
    assert "egy fő szekcióról" in search.summary
    assert search.evidence["bots"] == [{
        "bot": "Googlebot", "group": "googlebot", "scope": "fő szekció",
        "rule": "Disallow: /szolg/",
        "sections": [{"anchor": "Szolgáltatások", "url": f"{BASE}/szolg/",
                      "rule": "Disallow: /szolg/"}]}]
    ai = found["ai"]                                    # AI-bot, egész site: low, semleges szöveg
    assert ai.severity == "low" and "üzleti döntés lehet" in ai.summary
    assert [item["bot"] for item in ai.evidence["bots"]] == ["GPTBot", "Google-Extended"]
    assert all(item["scope"] == "az egész site" for item in ai.evidence["bots"])
    # a listán kívüli bot teljes tiltása nem megállapítás: tény a site-áttekintőben
    assert all("MJ12bot" not in finding.summary
               for finding in of_type(con, "robots_bot_blocked"))
    robots = site_views(con, "pelda").structure.robots
    assert robots.other_blocked == ["mj12bot"]
    google = next(bot for bot in robots.bots if bot.bot == "Googlebot")
    assert (google.group, google.own_group, google.rules) == ("googlebot", True,
                                                               ["Disallow: /szolg/"])
    assert next(bot for bot in robots.bots if bot.bot == "Bingbot").group == "*"


def test_robots_facts_in_the_site_overview_only_when_there_is_something_to_show(tmp_path):
    plain = robots_site("User-agent: *\nDisallow: /kosar\n")
    page = export_views(plain, tmp_path / "a", "pelda")["html"].read_text(encoding="utf-8")
    assert "<summary>robots.txt" not in page
    shown = robots_site("User-agent: *\nDisallow: /kosar\n\nUser-agent: Googlebot\nDisallow:\n\n"
                        "User-agent: MJ12bot\nDisallow: /\n")
    page = export_views(shown, tmp_path / "b", "pelda")["html"].read_text(encoding="utf-8")
    assert "<summary>robots.txt" in page
    assert "Googlebot: saját csoport (googlebot) — nincs tiltás" in page
    assert "az egész site-ról kitiltott egyéb botok: mj12bot" in page


def test_the_final_address_row_of_a_broken_linked_form():
    evidence = {"raw_url": f"{BASE}/allas", "stored_url": f"{BASE}/allas/", "final_url": None,
                "final_note": "a tárolt oldal címe", "note": "a linkelt alak hibát ad (404)",
                "differences": ["záró perjel", "a linkelt alak hibát ad"], "linked_status": 404,
                "hops": 0, "by_area": {"tartalom": 1}, "source_pages": 1,
                "pages": [{"url": f"{BASE}/szolg/", "areas": ["tartalom"], "anchors": ["Állás"]}]}
    shown = _finding_html({"type": "link_not_final_url", "severity": "high", "summary": "x",
                           "evidence": evidence})
    assert "<th>végleges cím</th><td>nincs: a linkelt alak hibát ad (404)</td>" in shown
    assert "nem mért (a tárolt oldal címe" not in shown
    # mérés nélkül a sor változatlan
    unmeasured = {**evidence, "note": None, "linked_status": "nem mért", "hops": "nem mért",
                  "differences": ["záró perjel"]}
    del unmeasured["note"]
    shown = _finding_html({"type": "link_not_final_url", "severity": "low", "summary": "x",
                           "evidence": unmeasured})
    assert "nem mért (a tárolt oldal címe:" in shown
