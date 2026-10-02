"""Microdata és RDFa kinyerése (aaa2/engine/structured.py) és tárolása a `structured_data`
táblában; a `StructuredData` szerződés a JSON-LD-vel együtt. Hálózat nélkül."""
import json
from types import SimpleNamespace

from selectolax.parser import HTMLParser

from aaa2 import contracts
from aaa2.db.connect import connect
from aaa2.engine.crawl import _Run
from aaa2.engine.normalize import UrlPolicy
from aaa2.engine.parse import parse_page
from aaa2.engine.structured import microdata_items, rdfa_items, structured_items
from tests.contract_rows import build_all

MICRODATA = """
<div itemscope itemtype="https://schema.org/Product" itemid="#p1">
  <h1 itemprop="name">Nordic  12 kW</h1>
  <img itemprop="image" src="/kep.jpg" alt="x">
  <a itemprop="url" href="/termek/nordic-12/">részletek</a>
  <meta itemprop="sku" content="N-12">
  <time itemprop="releaseDate" datetime="2026-01-05">január 5.</time>
  <data itemprop="weight" value="42">42 kg</data>
  <span itemprop="category">Hőszivattyú</span><span itemprop="category">Split</span>
  <div itemprop="brand" itemscope itemtype="https://schema.org/Brand">
    <span itemprop="name">ACME</span>
  </div>
  <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
    <meta itemprop="priceCurrency" content="HUF"><span itemprop="price">1 200 000</span>
  </div>
</div>
<template><div itemscope itemtype="https://schema.org/Thing"><span itemprop="name">sablon</span></div></template>
<p itemscope><span itemprop="name alternateName">Típus nélküli</span></p>
"""

RDFA = """
<div vocab="https://schema.org/" typeof="Person" resource="#anna">
  <span property="name">Kiss Anna</span>
  <a property="url" href="https://pelda.hu/anna/">profil</a>
  <meta property="jobTitle" content="tanácsadó">
  <div property="worksFor" typeof="Organization"><span property="name">Pelda Kft.</span></div>
  <span property="knowsAbout">SEO</span><span property="knowsAbout">GEO</span>
</div>
<p typeof="schema:Article foaf:Document"><span property="schema:headline">Cím</span></p>
"""

OPEN_GRAPH = '<head><meta property="og:title" content="Cím"><meta property="og:type" content="article"></head>'


def test_microdata_items_with_nested_items_and_typed_values():
    items = microdata_items(HTMLParser(f"<html><body>{MICRODATA}</body></html>"))
    assert items == [
        {"@type": "https://schema.org/Product", "@id": "#p1", "name": "Nordic 12 kW",
         "image": "/kep.jpg", "url": "/termek/nordic-12/", "sku": "N-12",
         "releaseDate": "2026-01-05", "weight": "42", "category": ["Hőszivattyú", "Split"],
         "brand": {"@type": "https://schema.org/Brand", "name": "ACME"},
         "offers": {"@type": "https://schema.org/Offer", "priceCurrency": "HUF",
                    "price": "1 200 000"}},
        {"name": "Típus nélküli", "alternateName": "Típus nélküli"},
    ]


def test_rdfa_items_with_vocab_nested_types_and_prefixes():
    items = rdfa_items(HTMLParser(f"<html>{OPEN_GRAPH}<body>{RDFA}</body></html>"))
    assert items == [
        {"@context": "https://schema.org/", "@type": "Person", "@id": "#anna",
         "name": "Kiss Anna", "url": "https://pelda.hu/anna/", "jobTitle": "tanácsadó",
         "worksFor": {"@context": "https://schema.org/", "@type": "Organization",
                      "name": "Pelda Kft."},
         "knowsAbout": ["SEO", "GEO"]},
        {"@type": ["schema:Article", "foaf:Document"], "schema:headline": "Cím"},
    ]


def test_structured_items_are_ordered_typed_and_json_ld_is_not_repeated():
    ld = '<script type="application/ld+json">{"@type": "Service", "name": "Mérés"}</script>'
    html = f"<html><head>{ld}</head><body>{MICRODATA}{RDFA}</body></html>"
    items = structured_items(HTMLParser(html))
    assert [(i.syntax, i.type, i.ordinal) for i in items] == [
        ("microdata", "Product", 1), ("microdata", None, 2),
        ("rdfa", "Person", 3), ("rdfa", "Article,Document", 4)]
    assert all(json.loads(i.json) for i in items)
    parsed = parse_page(html, "https://pelda.hu/", UrlPolicy.from_seed("https://pelda.hu/"))
    assert parsed.structured_data == items
    assert [b.type for b in parsed.schema_blocks] == ["Service"]        # a JSON-LD külön marad
    # az Open Graph (típusos ős nélküli property) és a jelölés nélküli oldal nem ad elemet
    assert structured_items(HTMLParser(f"<html>{OPEN_GRAPH}<body><p>x</p></body></html>")) == ()


def test_the_crawl_stores_structured_data_and_the_contract_reads_both_tables():
    con = connect(":memory:")
    con.execute("INSERT INTO site (domain, seed_url) VALUES ('pelda.hu', 'https://pelda.hu/')")
    (page_id,) = con.execute("INSERT INTO pages (url, status) VALUES ('https://pelda.hu/', 200) "
                             "RETURNING page_id").fetchone()
    ld = '<script type="application/ld+json">{"@type": "Service", "name": "Mérés"}</script>'
    html = f"<html><head>{ld}</head><body>{MICRODATA}{RDFA}</body></html>"
    parsed = parse_page(html, "https://pelda.hu/", UrlPolicy.from_seed("https://pelda.hu/"))
    writer = SimpleNamespace(con=con)
    _Run._write_children(writer, page_id, parsed)
    assert con.execute("SELECT syntax, type, ordinal FROM structured_data ORDER BY ordinal"
                       ).fetchall() == [("microdata", "Product", 1), ("microdata", None, 2),
                                        ("rdfa", "Person", 3), ("rdfa", "Article,Document", 4)]
    built = build_all(con)
    rows, items = built["StructuredData"]
    assert rows == len(items) == 5
    assert sorted({item.syntax for item in items}) == ["json-ld", "microdata", "rdfa"]
    assert all(isinstance(item, contracts.StructuredData) for item in items)
    product = next(item for item in items if item.type == "Product")
    assert product.data["brand"] == {"@type": "https://schema.org/Brand", "name": "ACME"}
    assert len(built["PageMeta"][1][0].structured_data) == 5
    # újracrawlnál az oldal régi elemei törlődnek
    _Run._clear_children(writer, page_id)
    assert con.execute("SELECT count(*) FROM structured_data").fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM schema_blocks").fetchone() == (0,)
