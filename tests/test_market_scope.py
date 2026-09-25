"""Piaci hatókör: a v1 `scope_signals()` döntési sorrendje, a postai cím városa és a sitewide
strukturált tények."""
import pytest

from aaa2.engine.market_scope import MarketScope, ScopePage, market_scope


def home(head="", main="", schema=()):
    return ScopePage(head_text=head, main_content=main, schema_items=tuple(schema))


DECISIONS = [
    ("város a H1-ben + ccTLD-ország", [home(head="Plumber | Emergency plumber in London")], [],
     "GB", MarketScope("mixed", "London")),
    ("város a címben + nemzetközi a main contentben",
     [home(head="SEO Budapest", main="We work with clients worldwide.")], [], None,
     MarketScope("mixed", "Budapest")),
    ("csak ország", [home(head="Vízszerelés")], [], "HU", MarketScope("country_specific", None)),
    ("csak nemzetközi, a fejrészben", [home(head="Remote SEO consulting")], [], None,
     MarketScope("international_global", None)),
    ("ország és nemzetközi, város nélkül", [home(main="We ship internationally.")], [], "HU",
     MarketScope("country_specific", None)),
    ("csak város a meta descriptionben", [home(head="Trattoria | Italian food in Budapest")], [],
     None, MarketScope("local", "Budapest")),
    ("semmi", [home(head="Components", main="Alerts, buttons, carousel.")], [], None,
     MarketScope("not_country_specific", None)),
    ("nincs kezdőoldal", [], [], None, MarketScope("not_country_specific", None)),
    ("nagybetűs város", [home(head="LONDON PLUMBERS")], [], None, MarketScope("local", "London")),
    ("több szavas város", [home(head="Pizza in New York")], [], None,
     MarketScope("local", "New York")),
    ("a lista sorrendje dönt", [home(head="Paris and London")], [], None,
     MarketScope("local", "London")),
    ("nemzetközi a kezdőoldal schemájában",
     [home(schema=[{"@type": "Organization", "description": "Shipping worldwide"}])], [], None,
     MarketScope("international_global", None)),
]


@pytest.mark.parametrize(("home_pages", "site_schema", "country", "expected"),
                         [d[1:] for d in DECISIONS], ids=[d[0] for d in DECISIONS])
def test_market_scope_decisions(home_pages, site_schema, country, expected):
    assert market_scope(home_pages, site_schema, country) == expected


ADDRESSES = [
    ("magyar irányítószám", "Materia – Trattoria Moderna 1073 Budapest, Dob u. 56-58", "Budapest"),
    ("országjeles irányítószám", "DotRoll Kft. H-1148 Budapest, Fogarasi út 3-5.", "Budapest"),
    ("német irányítószám", "Friedrichstraße 1, D-10115 Berlin", "Berlin"),
    ("holland irányítószám", "Damrak 1, 1012 LG Amsterdam", "Amsterdam"),
    ("UK postcode", "10 Downing Street, London SW1A 2AA", "London"),
    ("US ZIP", "350 5th Ave, New York, NY 10118", "New York"),
    ("vessző az irányítószám előtt", "Dob u. 56-58, 1073 Budapest Tel.: +36 1 234 5678", "Budapest"),
    ("cím a mondat végén", "Irodánk: 1073 Budapest.", "Budapest"),
    ("csak említés", "We love Budapest and London.", None),
    ("szám a szó végén", "ID1073 Budapest", None),
    ("kisbetűs UK postcode", "london sw1a 2aa", None),
    ("magában álló négyjegyű szám", "Nyitva 1073 napja, rendelés: 2024", None),
    ("termékkód", "Cikkszám: HU-1073", None),
    ("szám kettőspont után, a város után szöveg", "Cikkszám: 1073 Budapest Blend", None),
    ("évszám a város előtt", "In 2024 London hosted the finals.", None),
    ("szám a város előtt, cím nélkül", "Over 1500 London businesses trust us.", None),
    ("termékkód a város előtt", "HU-1073 Budapest Blend, 250 g", None),
    ("termékkód a város előtt, a szöveg végén", "Rendelés: HU-1073 Budapest", None),
]


@pytest.mark.parametrize(("main", "expected"), [a[1:] for a in ADDRESSES], ids=[a[0] for a in ADDRESSES])
def test_city_from_postal_address_in_main_content(main, expected):
    result = market_scope([home(main=main)], [], None)
    assert result.city == expected
    assert result.scope == ("local" if expected else "not_country_specific")


def test_sitewide_schema_text_is_not_an_address():
    """A postai cím a kezdőoldalak main contentjéből jön; a sitewide schema csak strukturáltan
    (`addressLocality`) ad várost."""
    site_schema = [{"@type": "WebPage", "description": "Headquarters: 1092 Budapest, Ráday utca 57."}]
    assert market_scope([home()], site_schema, None) == MarketScope("not_country_specific", None)


SITE_SCHEMA = [
    ("schema-cím városa", [{"@type": "LocalBusiness", "address": {
        "@type": "PostalAddress", "addressLocality": "Budapest", "addressCountry": "HU"}}],
     MarketScope("local", "Budapest")),
    ("schema-cím városa listában", [{"address": [{"addressLocality": "Wien"},
                                                 {"addressLocality": "London"}]}],
     MarketScope("local", "London")),
    ("areaServed Place", [{"@type": "Service", "areaServed": {"@type": "Place", "name": "Worldwide"}}],
     MarketScope("international_global", None)),
    ("areaServed lista szöveggel", [{"areaServed": ["Hungary", "International"]}],
     MarketScope("international_global", None)),
    ("serviceArea", [{"serviceArea": {"name": "Global"}}], MarketScope("international_global", None)),
    ("areaServed ország", [{"areaServed": {"@type": "Country", "name": "Magyarország"}}],
     MarketScope("not_country_specific", None)),
    ("nemzetközi szó a schema más mezőjében", [{"@type": "Article",
                                                "description": "An international B2B SaaS company"}],
     MarketScope("not_country_specific", None)),
]


@pytest.mark.parametrize(("site_schema", "expected"), [s[1:] for s in SITE_SCHEMA],
                         ids=[s[0] for s in SITE_SCHEMA])
def test_sitewide_structured_facts(site_schema, expected):
    assert market_scope([home()], site_schema, None) == expected
