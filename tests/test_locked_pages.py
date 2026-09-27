"""A zárolt tesztoldalak kiválasztási szabálya (tests/acceptance/locked_pages.py), hálózat és
adatbázis nélkül."""
import json

from tests.acceptance.locked_pages import DEVELOPMENT, OUT, PER_SITE, select, sha256

DEV = "https://x.test/hu/fejlesztes/"


def row(url, status=200, final_url=None, canonical=None, hreflang=None):
    return {"url": url, "status": status, "final_url": final_url or url,
            "canonical": canonical or url, "hreflang": hreflang}


def test_development_page_its_translations_and_variants_are_excluded():
    rows = [
        row(DEV, hreflang=[f"hu|{DEV}", "en|https://x.test/dev/"]),
        row("https://x.test/dev/"),
        row("https://x.test/it/sviluppo/", hreflang=[f"hu|{DEV}"]),
        row(DEV + "?tab=api"),
        row("https://x.test/regi/", final_url=DEV),
        row("https://x.test/a/"), row("https://x.test/b/"),
    ]
    assert set(select(rows, DEV, n=10)) == {"https://x.test/a/", "https://x.test/b/"}


def test_non_2xx_and_duplicates_are_dropped_and_order_is_sha256():
    urls = [f"https://x.test/{i}/" for i in range(8)]
    rows = [row(u) for u in urls] + [
        row("https://x.test/404/", status=404),
        row("https://x.test/masolat/", canonical=urls[0]),
        row("https://x.test/iranyit", final_url=urls[1]),
        row("https://x.test/kulso-canonical/", canonical="https://x.test/nincs-a-crawlban/"),
    ]
    chosen = select(rows, DEV)
    expected = sorted([*urls, "https://x.test/kulso-canonical/"], key=sha256)[:PER_SITE]
    assert chosen == expected
    assert select(list(reversed(rows)), DEV) == chosen


def test_the_committed_list_follows_the_rule_shape():
    data = json.loads(OUT.read_text(encoding="utf-8"))
    assert set(data["sites"]) == set(DEVELOPMENT)
    for name, site in data["sites"].items():
        assert site["development"] == DEVELOPMENT[name]
        assert len(site["urls"]) == PER_SITE
        assert site["urls"] == sorted(site["urls"], key=sha256)
        assert DEVELOPMENT[name] not in site["urls"]
