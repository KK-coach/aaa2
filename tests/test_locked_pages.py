"""A zárolt tesztoldalak kiválasztási szabálya (tests/acceptance/locked_pages.py), hálózat és
adatbázis nélkül."""
import json

from tests.acceptance.locked_pages import (
    DEVELOPMENT,
    M27_SITES,
    OUT,
    PER_SITE,
    REQUIRED,
    select,
    sha256,
)

DEV = "https://x.test/hu/fejlesztes/"


def row(url, status=200, final_url=None, canonical=None, hreflang=None, word_count=500):
    return {"url": url, "status": status, "final_url": final_url or url,
            "canonical": canonical or url, "hreflang": hreflang, "word_count": word_count}


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


def test_pages_under_150_words_are_dropped():
    rows = [row("https://x.test/rovid/", word_count=149), row("https://x.test/eleg/", word_count=150),
            row("https://x.test/ures/", word_count=None)]
    assert select(rows, DEV, n=10) == ["https://x.test/eleg/"]


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
    assert set(data["sites"]) == set(DEVELOPMENT) | set(M27_SITES)
    for name, site in data["sites"].items():
        assert len(site["urls"]) == PER_SITE
        if name in M27_SITES:
            assert site["development"] is None
            required = list(REQUIRED.get(name, ()))
            assert site["types"][:len(required)] == required
            rest = site["urls"][len(required):]
            assert rest == sorted(rest, key=sha256)
            continue
        assert site["development"] == DEVELOPMENT[name]
        assert site["urls"] == sorted(site["urls"], key=sha256)
        assert DEVELOPMENT[name] not in site["urls"]


def test_required_page_types_come_first_then_sha256_order():
    rows = [{**row(f"https://x.test/{i}/"), "type": kind}
            for i, kind in enumerate(["other", "other", "product", "product", "category"])]
    chosen = select(rows, None, required=("product", "category"))
    products = sorted((r["url"] for r in rows if r["type"] == "product"), key=sha256)
    assert chosen[:2] == [products[0], "https://x.test/4/"]
    rest = sorted((r["url"] for r in rows if r["url"] not in chosen[:2]), key=sha256)
    assert chosen[2:] == rest[:PER_SITE - 2]
    assert select(rows, None) == sorted((r["url"] for r in rows), key=sha256)[:PER_SITE]
