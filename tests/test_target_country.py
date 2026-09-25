"""Célország: a v1 jelei táblás esetekkel, a sitewide szavazás és a konfidencia-sávok."""
import pytest

from aaa2.engine.target_country import (
    Candidate,
    TargetCountry,
    currencies,
    page_country_signals,
    phone_countries,
    tld_country,
    vote,
)

# ---------------------------------------------------------------------------
# jelek
# ---------------------------------------------------------------------------

TLDS = [
    ("bolt.hu", "HU"),
    ("shop.co.uk", "GB"),
    ("bolt.com.au", "AU"),
    ("kk.coach", None),
    ("materia-tm.com", None),
    ("app.io", None),
    ("user.github.io", None),
]


@pytest.mark.parametrize(("domain", "expected"), TLDS, ids=[t[0] for t in TLDS])
def test_tld_country(domain, expected):
    assert tld_country(domain) == expected


PHONES = [
    ("nemzetközi alak", "Tel.: +36 20 420 4461", {"HU"}),
    ("00-s alak", "0036 1 234 5678", {"HU"}),
    ("tagolás nélkül", "+3612345678", {"HU"}),
    ("több ország", "+44 20 7946 0958, +1 347 897 0218", {"GB", "US"}),
    ("háromjegyű előhívó", "+420 777 123 456", {"CZ"}),
    ("belföldi alak", "06 20 420 4461", set()),
    ("túl rövid", "+36 20", set()),
    ("számon belüli 00", "rendelésszám 1200361234567", set()),
    ("árak kötőjellel", "128000-224000 Ft", set()),
    ("ismeretlen előhívó", "+81 3 1234 5678", set()),
]


@pytest.mark.parametrize(("text", "expected"), [p[1:] for p in PHONES], ids=[p[0] for p in PHONES])
def test_phone_countries(text, expected):
    assert phone_countries(text) == expected


CURRENCIES = [
    ("ISO-kód", "Ár: 12 000 HUF", {"HUF"}),
    ("ISO-kód írásjel előtt", "EUR-ban fizethető", {"EUR"}),
    ("szimbólum számhoz tapadva", "160000-320000Ft/hó", {"HUF"}),
    ("euró és font", "€49 or £42", {"EUR", "GBP"}),
    ("korona és zloty", "1 200 Kč, 99 zł", {"CZK", "PLN"}),
    ("kisbetűs ISO-kód", "huf, eur, hrk", set()),
    ("ISO-kód szó belsejében", "EURO, HUFFINGTON, SHRKX", set()),
    ("angol szöveg", "We're not sure what we're known for, online. Knowledge first.", set()),
]


@pytest.mark.parametrize(("text", "expected"), [c[1:] for c in CURRENCIES],
                         ids=[c[0] for c in CURRENCIES])
def test_currencies(text, expected):
    assert currencies(text) == expected


ADDRESS_HU = {"@type": "PostalAddress", "addressCountry": "HU"}
PAGE_SIGNALS = [
    ("semmi", "https://x.com/", "", None, [], [], {}),
    ("hreflang régiókód", "https://x.com/", "", None, ["en-GB", "hu", "x-default", "de_AT"], [],
     {"hreflang": {"GB", "AT"}}),
    ("path: nyelvkód, ami országkód is", "https://x.com/hu/rolam/", "", None, [], [],
     {"path_prefix": {"HU"}}),
    ("path: régió", "https://x.com/en-gb/about/", "", None, [], [], {"path_prefix": {"GB"}}),
    ("path: nagybetűs", "https://x.com/IT/menu/", "", None, [], [], {"path_prefix": {"IT"}}),
    ("path: nyelvkód, ami nem ország", "https://x.com/en/about/", "", None, [], [], {}),
    ("path: nem kód", "https://x.com/blog/", "", None, [], [], {}),
    ("path: táblán kívüli régió", "https://x.com/pt-br/", "", None, [], [], {}),
    ("og:locale", "https://x.com/", "", "hu_HU", [], [], {"og_locale": {"HU"}}),
    ("og:locale régió nélkül", "https://x.com/", "", "en", [], [], {}),
    ("telefon és pénznem", "https://x.com/", "+36 1 432 3232, 16 000 Ft", None, [], [],
     {"phone": {"HU"}, "currency": {"HU"}}),
    ("országot nem adó pénznem", "https://x.com/", "49 EUR, 10 USD", None, [], [], {}),
    ("schema: kód", "https://x.com/", "", None, [],
     [{"@type": "Organization", "address": ADDRESS_HU}], {"schema": {"HU"}}),
    ("schema: országnév", "https://x.com/", "", None, [],
     [{"@type": "LocalBusiness", "address": {"addressCountry": "Hungary"}}], {"schema": {"HU"}}),
    ("schema: Country objektum", "https://x.com/", "", None, [],
     [{"address": {"addressCountry": {"@type": "Country", "name": "United Kingdom"}}}],
     {"schema": {"GB"}}),
    ("schema: UK a címlistában", "https://x.com/", "", None, [],
     [{"address": [{"streetAddress": "1 High St"}, {"addressCountry": "UK"}]}], {"schema": {"GB"}}),
    ("schema: beágyazva", "https://x.com/", "", None, [],
     [{"@type": "WebPage", "publisher": {"@type": "Organization", "address": ADDRESS_HU}}],
     {"schema": {"HU"}}),
    ("schema: táblán kívüli ország és szöveges cím", "https://x.com/", "", None, [],
     [{"address": {"addressCountry": "SE"}}, {"address": "1073 Budapest"}], {}),
]


@pytest.mark.parametrize(("url", "text", "og", "hreflang", "schema", "expected"),
                         [p[1:] for p in PAGE_SIGNALS], ids=[p[0] for p in PAGE_SIGNALS])
def test_page_country_signals(url, text, og, hreflang, schema, expected):
    assert page_country_signals(url, text, og, hreflang, schema) == expected


def test_english_known_is_not_croatia():
    """A v1-ben a kk.coach a "known"-ból lett horvát; az angol szöveg nem ad pénznemet."""
    text = "We're not sure what we're known for, online. Knowledge, KN, kn."
    assert page_country_signals("https://kk.coach/solutions/", text, None, [], []) == {}


# ---------------------------------------------------------------------------
# szavazás
# ---------------------------------------------------------------------------


def test_vote_without_signals():
    assert vote(None, []) == TargetCountry(None, None, ())


def test_vote_splits_kind_weight_by_page_share():
    """Materia-szerű: /hu/ és /it/ ág, magyar telefon a kezdőoldalakon, az adatkezelési
    tájékoztatóban amerikai és olasz is. Oldalanként egyszer számító jelekkel az olasz nem
    kerülhet holtversenybe a magyarral."""
    pages = [
        {"phone": {"HU"}},
        {"path_prefix": {"HU"}, "phone": {"HU"}},
        {"path_prefix": {"IT"}, "phone": {"HU"}},
        {"phone": {"HU", "US", "IT"}},
        {"path_prefix": {"HU"}},
        {"path_prefix": {"IT"}},
    ]
    assert vote(None, pages) == TargetCountry("HU", "medium", (
        Candidate("HU", 0.6, ("path_prefix", "phone")),
        Candidate("IT", 0.31, ("path_prefix", "phone")),
        Candidate("US", 0.1, ("phone",)),
    ))


def test_contact_page_phone_outweighs_template_signal():
    pages = [{"currency": {"GB"}}] * 20 + [{"phone": {"HU"}}]
    result = vote(None, pages)
    assert (result.country, result.candidates[0].score) == ("HU", 0.67)


def test_isolated_og_locale_is_a_soft_hint():
    """Egy elszigetelt og:locale (1) alulmarad a path-prefixnek (1,5); megerősítve (2) nyer."""
    isolated = [{"og_locale": {"GB"}}] * 3 + [{"path_prefix": {"HU"}}]
    assert vote(None, isolated).country == "HU"
    corroborated = isolated + [{"hreflang": {"GB"}}]
    assert vote(None, corroborated) == TargetCountry("GB", "medium", (
        Candidate("GB", 0.67, ("hreflang", "og_locale")),
        Candidate("HU", 0.33, ("path_prefix",)),
    ))


def test_tie_at_the_top_has_no_winner():
    result = vote(None, [{"phone": {"HU"}}, {"phone": {"IT"}}])
    assert result == TargetCountry(None, None, (
        Candidate("HU", 0.5, ("phone",)), Candidate("IT", 0.5, ("phone",))))


def test_at_most_three_candidates():
    pages = [{"phone": {"HU"}}, {"phone": {"HU"}}, {"phone": {"DE"}}, {"phone": {"AT"}},
             {"phone": {"CH"}}]
    assert [c.country for c in vote(None, pages).candidates] == ["HU", "AT", "CH"]


CONFIDENCE = [
    ("három erős jel", "HU",
     [{"phone": {"HU"}, "schema": {"HU"}}], ("HU", "high")),
    ("csak ccTLD", "HU", [], ("HU", "medium")),
    ("ccTLD és telefon", "HU", [{"phone": {"HU"}}], ("HU", "medium")),
    ("csak gyenge jelek", None, [{"path_prefix": {"HU"}, "currency": {"HU"}}], ("HU", "low")),
    ("csak elszigetelt og:locale", None, [{"og_locale": {"GB"}}], ("GB", "low")),
    ("ütközés: ccTLD és más ország telefonja", "GB", [{"phone": {"HU"}}], ("GB", "low")),
    ("három egyező erős jel felülírja az ütközést", "GB",
     [{"hreflang": {"GB"}, "schema": {"GB"}, "phone": {"HU"}}], ("GB", "high")),
    ("megerősített og:locale: két erős jel", None,
     [{"phone": {"GB"}, "og_locale": {"GB"}}], ("GB", "medium")),
    ("megerősített og:locale harmadik erős jelként", None,
     [{"phone": {"GB"}, "og_locale": {"GB"}, "hreflang": {"GB"}}], ("GB", "high")),
    ("megerősített og:locale, de ütközéssel", "HU",
     [{"phone": {"GB"}, "og_locale": {"GB"}}], ("GB", "low")),
    ("holtversenyes jelfajtának nincs vezetője", "DE",
     [{"hreflang": {"GB", "AT"}}] * 4, ("DE", "medium")),
    ("a path-prefix nem erősíti meg az og:locale-t", None,
     [{"og_locale": {"HU"}, "path_prefix": {"HU"}}], ("HU", "low")),
]


@pytest.mark.parametrize(("tld", "pages", "expected"), [c[1:] for c in CONFIDENCE],
                         ids=[c[0] for c in CONFIDENCE])
def test_confidence(tld, pages, expected):
    result = vote(tld, pages)
    assert (result.country, result.confidence) == expected
