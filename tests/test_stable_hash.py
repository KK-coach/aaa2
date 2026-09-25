"""Stabil hash: a csak kérésenkénti tokenben eltérő két HTML azonos hash-t ad, a valódi
változás (asset-verzió, inline JSON, JSON-LD) eltérőt."""
import hashlib

import pytest

from aaa2.engine.stable_hash import decode_raw, stable_hash, strip_volatile, volatile_patterns


def page(fragment):
    return f"<html><head><title>T</title></head><body><p>tartalom</p>{fragment}</body></html>"


SAME = [
    ("Cloudflare data-cfemail",
     '<a href="/cdn-cgi/l/email-protection" class="__cf_email__" data-cfemail="462f2820290">[email]</a>',
     '<a href="/cdn-cgi/l/email-protection" class="__cf_email__" data-cfemail="4d24232b220d">[email]</a>'),
    ("Cloudflare data-cfemail aposztróffal",
     "<a href='/cdn-cgi/l/email-protection' data-cfemail='0a0b'>x</a>",
     "<a href='/cdn-cgi/l/email-protection' data-cfemail='7f7e'>x</a>"),
    ("Cloudflare email-protection href",
     '<a href="/cdn-cgi/l/email-protection#b3dad2c1f3dcd6">írjon</a>',
     '<a href="/cdn-cgi/l/email-protection#5e373f2c1e313b">írjon</a>'),
    ("WordPress inline CSS helyőrző",
     "<style>/*wp_block_styles_on_demand_placeholder:6ab6626e8c9f1*/</style>",
     "<style>/*wp_block_styles_on_demand_placeholder:6ab662b99f32c*/</style>"),
    ("WordPress _wpnonce query",
     '<a href="/wp-admin/admin-ajax.php?action=x&_wpnonce=3f2a9b1c0d">x</a>',
     '<a href="/wp-admin/admin-ajax.php?action=x&_wpnonce=a1b2c3d4e5">x</a>'),
    ("WordPress _wpnonce &amp; alakban",
     '<a href="/?action=logout&amp;_wpnonce=9f8e7d6c5b">ki</a>',
     '<a href="/?action=logout&amp;_wpnonce=0a1b2c3d4e">ki</a>'),
    ("CSP nonce script-en",
     '<script nonce="r4nd0mB4s364==">init()</script>',
     '<script nonce="Z2l2ZW5vbmNl">init()</script>'),
    ("CSP nonce style-on, aposztróffal",
     "<style nonce='abc123'>p{}</style>",
     "<style nonce='xyz789'>p{}</style>"),
    ("WordPress data-nonce",
     '<button data-nonce="4c3b2a1f0e" data-action="like">tetszik</button>',
     '<button data-nonce="9e8d7c6b5a" data-action="like">tetszik</button>'),
    ("CSRF meta",
     '<meta name="csrf-token" content="aB3dE5fG7hJ9">',
     '<meta name="csrf-token" content="Zy8Xw6Vu4Ts2">'),
    ("Laravel _token meta, fordított attribútumsorrend",
     "<meta content='tok1' name='_token'>",
     "<meta content='tok2' name='_token'>"),
    ("WP Rocket komment",
     "<!-- This website is like a Rocket. Performance optimized by WP Rocket. - Debug: cached@1727258400 -->",
     "<!-- This website is like a Rocket. Performance optimized by WP Rocket. - Debug: cached@1727262000 -->"),
    ("W3 Total Cache komment",
     "<!--\nPerformance optimized by W3 Total Cache.\nServed from: kk.coach @ 2026-09-25 10:00:01 by W3 Total Cache\n-->",
     "<!--\nPerformance optimized by W3 Total Cache.\nServed from: kk.coach @ 2026-09-25 11:42:17 by W3 Total Cache\n-->"),
    ("LiteSpeed komment",
     "<!-- Page optimized by LiteSpeed Cache @2026-09-25 10:00:00 -->",
     "<!-- Page optimized by LiteSpeed Cache @2026-09-25 12:30:45 -->"),
    ("WP Super Cache generated in",
     "<!-- Dynamic page generated in 0.412 seconds. -->",
     "<!-- Dynamic page generated in 1.207 seconds. -->"),
    ("időbélyeges komment",
     "<!-- page built 2026-09-25T10:00:00Z -->",
     "<!-- page built 2026-09-25T10:05:31Z -->"),
    ("futásidős komment",
     "<!-- 42 queries in 0.318 seconds -->",
     "<!-- 42 queries in 0.502 seconds -->"),
    ("ms futásidő",
     "<!-- render: 83 ms -->",
     "<!-- render: 127 ms -->"),
    ("epoch időbélyeg kommentben",
     "<!-- cache-key 1727258400 -->",
     "<!-- cache-key 1727262911 -->"),
]


@pytest.mark.parametrize(("first", "second"), [s[1:] for s in SAME], ids=[s[0] for s in SAME])
def test_volatile_token_does_not_change_hash(first, second):
    assert first != second
    assert stable_hash(page(first)) == stable_hash(page(second))


DIFFERENT = [
    ("asset ?ver=",
     '<script src="/wp-includes/js/jquery.min.js?ver=3.7.1"></script>',
     '<script src="/wp-includes/js/jquery.min.js?ver=3.7.2"></script>'),
    ("inline JSON nonce", '<script>var ajax = {"nonce":"3f2a9b1c0d"};</script>',
     '<script>var ajax = {"nonce":"a1b2c3d4e5"};</script>'),
    ("JSON-LD", '<script type="application/ld+json">{"@type":"Event","startDate":"2026-10-01"}</script>',
     '<script type="application/ld+json">{"@type":"Event","startDate":"2026-10-02"}</script>'),
    ("szöveg", "<p>Nyitva 10-től</p>", "<p>Nyitva 11-től</p>"),
    ("komment időbélyeg nélkül", "<!-- fejléc eleje -->", "<!-- fejléc vége -->"),
    ("dátum kommenten kívül", "<time>2026-09-25 10:00</time>", "<time>2026-09-25 11:00</time>"),
    ("nonce szó szövegben", "<p>nonce=\"a\" példa</p>", "<p>nonce=\"b\" példa</p>"),
]


@pytest.mark.parametrize(("first", "second"), [d[1:] for d in DIFFERENT], ids=[d[0] for d in DIFFERENT])
def test_real_change_changes_hash(first, second):
    assert stable_hash(page(first)) != stable_hash(page(second))


def test_comment_removal_stays_inside_one_comment():
    html = "<!-- elején 2026-09-25 10:00 --><p>megmarad</p><!-- végén -->"
    assert strip_volatile(html) == "<p>megmarad</p><!-- végén -->"


def test_hash_without_tokens_is_plain_sha256():
    html = page("<p>semmi változó</p>")
    assert stable_hash(html) == hashlib.sha256(html.encode("utf-8")).hexdigest()


def test_patterns_file_loaded_without_comments():
    patterns = volatile_patterns()
    assert len(patterns) == 13
    assert all(not p.pattern.startswith("#") for p in patterns)


def test_decode_raw_is_lossless_for_utf8_and_tolerant_otherwise():
    assert decode_raw("ékezet".encode()) == "ékezet"
    assert decode_raw(b"\xe9kezet") == "�kezet"
