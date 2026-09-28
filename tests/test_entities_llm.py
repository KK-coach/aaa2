"""Az oldalszintű (v1) prompt és ellenőrzései (aaa2/entities/llm.py), a mérőeszközöknek; és
a site-leíró mondat. A blokkos kör tesztjei: tests/test_entities_extract.py."""
import re

import pytest

from aaa2.entities.llm import (
    MAX_INPUT_CHARS,
    PROMPT,
    TYPE_DEFINITIONS,
    check_evidence,
    name_in_evidence,
    normalize_text,
    page_input,
    site_line,
)
from aaa2.llm.schemas import ENTITY_TYPES, ExtractedEntity
from tests.test_entities_rules import html, site

BODY = ("<h1>Példa Kávézó</h1><p>A Példa Kávézó Budapest belvárosában működik 2016 óta, "
        "specialty kávéval.</p><h2>Csapat</h2><p>A pörkölést Kiss Anna vezeti, aki a "
        "Budapest Coffee Festen is bemutatót tartott.</p>")


# ---------------------------------------------------------------------------
# prompt és bemenet
# ---------------------------------------------------------------------------


def test_prompt_is_constant_and_defines_the_ten_types():
    assert set(TYPE_DEFINITIONS) == set(ENTITY_TYPES)
    for kind, definition in TYPE_DEFINITIONS.items():
        assert f"- {kind}: {definition}" in PROMPT


def test_page_input_cuts_the_main_content():
    text, truncated = page_input("T", [(1, "H"), (2, "")], "x" * (MAX_INPUT_CHARS + 5))
    assert text.split("\n\n") == ["T", "H", "x" * MAX_INPUT_CHARS]
    assert truncated


def test_page_input_carries_no_structural_label():
    text, _ = page_input("Angular Bootstrap", [(1, "Accordion"), (3, "Usage")], "Szöveg.")
    assert text == "Angular Bootstrap\n\nAccordion\n\nUsage\n\nSzöveg."
    assert not re.search(r"(?i)\b(title|headings|text|h[1-6])\s*:", text)
    with_site, _ = page_input("T", [], "x", "This page belongs to the website pelda.hu.")
    assert with_site.split("\n\n") == ["This page belongs to the website pelda.hu.", "T", "x"]


def test_site_line_names_the_domain_and_the_home_title():
    con = site({"/": html("Példa   Kávézó\n| Kávé", BODY), "/b/": html("B", "<p>b</p>")})
    assert site_line(con) == ("This page belongs to the website pelda.hu, whose home page is "
                              "titled “Példa Kávézó | Kávé”.")
    con.execute("UPDATE site SET home_urls = ['https://pelda.hu/b/']")
    assert site_line(con).endswith("titled “B”.")
    con.execute("UPDATE site SET home_urls = []")
    assert site_line(con) == "This page belongs to the website pelda.hu."


def test_prompt_v2_asks_for_every_entity_and_the_primary_one():
    assert PROMPT.startswith("List every entity the web page below is about or mentions: "
                             "named things and concepts.")
    for phrase in ("primary_entity", "description", "contains the name",
                   "The titles of this site's own pages are not works"):
        assert phrase in PROMPT
    for gone in ("generic noun", "3 to 15", "named entities mentioned"):
        assert gone not in PROMPT
    assert "a food, a dish, a drink, a wine" in TYPE_DEFINITIONS["product"]
    assert "a company is org, not brand" in TYPE_DEFINITIONS["org"]


# ---------------------------------------------------------------------------
# fabrikáció-szűrő
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("evidence", "reason"), [
    ("a Példa Kávézó Budapest belvárosában", None),
    ("A  PÉLDA kávézó\nbudapest belvárosában", None),
    ("Példa Kávézó | Kávé", None),
    ("Példa Kávézó Szegeden működik", "fabricated"),
    # hosszkorlát nincs: az egyszavas és a teljes mondatnyi idézet is megmarad
    ("Példa Kávézó", None),
    ("Kávézó", None),
    (("A Példa Kávézó Budapest belvárosában működik 2016 óta, specialty kávéval. A pörkölést "
      "Kiss Anna vezeti, aki a Budapest Coffee Festen is bemutatót tartott."), None),
    ("", "fabricated"),
    # a korábbi bemenet címke-előtagja leválik az összevetés előtt
    ("TITLE: Példa Kávézó | Kávé", None),
    ("h2:  a Példa Kávézó Budapest belvárosában", None),
    ("TEXT: H1: a Példa Kávézó Budapest belvárosában", None),
    ("TITLE: Példa Kávézó", None),
    ("TITLE: Példa Kávézó Szegeden működik", "fabricated"),
    ("TITLE: Példa Kávézó | Kávé HEADINGS:", None),
    ("Példa Kávézó | Kávé TEXT: A Példa Kávézó", "fabricated"),
])
def test_check_evidence(evidence, reason):
    """Csak a szó szerinti egyezés számít (a címke nélkül); hosszkorlát nincs."""
    sources = [normalize_text(s) for s in (
        ("A Példa Kávézó Budapest belvárosában működik 2016 óta, specialty kávéval. A pörkölést "
         "Kiss Anna vezeti, aki a Budapest Coffee Festen is bemutatót tartott."),
        "Példa Kávézó | Kávé")]
    assert check_evidence(ExtractedEntity(name="X", type="org", description="d", evidence=evidence,
                                          context="c"), sources) == reason


@pytest.mark.parametrize(("name", "evidence", "inside"), [
    ("Kiss Anna", "A pörkölést Kiss Anna vezeti", True),
    ("Kiss Anna", "A pörkölést KISS  ANNA vezeti", True),
    ("Példa Kávézó", "TITLE: Pelda Kavezo | Kávé", True),
    ("Kiss Anna", "A pörkölést a vezető végzi", False),
])
def test_name_in_evidence_is_a_measure(name, evidence, inside):
    assert name_in_evidence(ExtractedEntity(name=name, type="person", description="d",
                                            evidence=evidence, context="c")) is inside


def test_title_label_quote_is_not_fabricated():
    """A felvett ngx-kimenet esete: a címke nélkül a title (hosszkorlát nincs)."""
    quote = ExtractedEntity(name="Angular Bootstrap", type="brand", description="d",
                            evidence="TITLE: Angular Bootstrap", context="c")
    assert check_evidence(quote, [normalize_text("Angular Bootstrap")]) is None
