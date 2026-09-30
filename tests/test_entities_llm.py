"""A típusdefiníciók, a site-leíró mondat és a szövegnormalizálás (aaa2/entities/llm.py). A
blokkos kör tesztjei: tests/test_entities_extract.py."""
from aaa2.entities.llm import TYPE_DEFINITIONS, normalize_text, site_line
from aaa2.llm.schemas import ENTITY_TYPES
from tests.test_entities_rules import html, site

BODY = ("<h1>Példa Kávézó</h1><p>A Példa Kávézó Budapest belvárosában működik 2016 óta, "
        "specialty kávéval.</p>")


def test_type_definitions_cover_the_ten_types():
    assert set(TYPE_DEFINITIONS) == set(ENTITY_TYPES)
    assert "a food, a dish, a drink, a wine" in TYPE_DEFINITIONS["product"]
    assert "a company is org, not brand" in TYPE_DEFINITIONS["org"]


def test_site_line_names_the_domain_and_the_home_title():
    con = site({"/": html("Példa   Kávézó\n| Kávé", BODY), "/b/": html("B", "<p>b</p>")})
    assert site_line(con) == ("This page belongs to the website pelda.hu, whose home page is "
                              "titled “Példa Kávézó | Kávé”.")
    con.execute("UPDATE site SET home_urls = ['https://pelda.hu/b/']")
    assert site_line(con).endswith("titled “B”.")
    con.execute("UPDATE site SET home_urls = []")
    assert site_line(con) == "This page belongs to the website pelda.hu."


def test_normalize_text():
    assert normalize_text("  A  Kávé\n\tBolt ") == "a kávé bolt"
    assert normalize_text(None) == ""
