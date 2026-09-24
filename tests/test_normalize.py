"""URL-normalizálás és scope: táblás esetek szabályonként."""
import pytest

from aaa2.engine.normalize import (
    UrlPolicy,
    decide_trailing_slash,
    is_internal,
    normalize,
    registrable_domain,
)

BARE = UrlPolicy.from_seed("https://kk.coach/")
WWW = UrlPolicy.from_seed("https://www.kk.coach/")
HTTPS = UrlPolicy.from_seed("https://kk.coach/", https_redirect=True)
SLASH = UrlPolicy.from_seed("https://kk.coach/", trailing_slash=True)
NO_SLASH = UrlPolicy.from_seed("https://kk.coach/", trailing_slash=False)
FULL = UrlPolicy.from_seed("https://kk.coach/", https_redirect=True, trailing_slash=True)

CASES = [
    # 1. host kisbetűre, www a seed formájára
    ("host kisbetű, path érintetlen", BARE, "https://KK.Coach/Rolam", "https://kk.coach/Rolam"),
    ("www le, ha a seed www nélküli", BARE, "https://www.kk.coach/a", "https://kk.coach/a"),
    ("www nagybetűvel", BARE, "https://WWW.KK.COACH/", "https://kk.coach/"),
    ("www fel, ha a seed www-s", WWW, "https://kk.coach/a", "https://www.kk.coach/a"),
    ("www-s seed, www-s URL", WWW, "https://www.kk.coach/a", "https://www.kk.coach/a"),
    ("más aldomain érintetlen", BARE, "https://en.kk.coach/a", "https://en.kk.coach/a"),
    ("külső www érintetlen", BARE, "https://www.example.com/a", "https://www.example.com/a"),
    # 2. séma https-re, ha a site http→https 301-et ad
    ("http → https 301 mellett", HTTPS, "http://kk.coach/a", "https://kk.coach/a"),
    ("http marad 301 nélkül", BARE, "http://kk.coach/a", "http://kk.coach/a"),
    ("http www → https seed", HTTPS, "http://www.kk.coach/a", "https://kk.coach/a"),
    ("http belső aldomain → https", HTTPS, "http://en.kk.coach/a", "https://en.kk.coach/a"),
    ("külső http érintetlen", HTTPS, "http://example.com/a", "http://example.com/a"),
    ("séma kisbetűre", BARE, "HTTPS://kk.coach/a", "https://kk.coach/a"),
    # 3. fragment eldobva
    ("fragment", BARE, "https://kk.coach/a#szekcio", "https://kk.coach/a"),
    ("üres fragment", BARE, "https://kk.coach/#", "https://kk.coach/"),
    ("fragment query után", BARE, "https://kk.coach/a?x=1#y", "https://kk.coach/a?x=1"),
    # 4. tracking-paraméterek eldobva
    ("utm_*", BARE, "https://kk.coach/a?utm_source=fb&utm_medium=cpc", "https://kk.coach/a"),
    ("gclid", BARE, "https://kk.coach/a?gclid=abc", "https://kk.coach/a"),
    ("fbclid", BARE, "https://kk.coach/a?fbclid=abc", "https://kk.coach/a"),
    ("mc_*", BARE, "https://kk.coach/a?mc_cid=1&mc_eid=2", "https://kk.coach/a"),
    ("ref", BARE, "https://kk.coach/a?ref=home", "https://kk.coach/a"),
    ("tracking kis-nagybetűtől függetlenül", BARE, "https://kk.coach/a?UTM_Source=x&GCLID=1",
     "https://kk.coach/a"),
    ("tracking ki, a többi marad", BARE, "https://kk.coach/a?page=2&utm_source=x",
     "https://kk.coach/a?page=2"),
    ("ref csak pontos egyezés", BARE, "https://kk.coach/a?referrer=x", "https://kk.coach/a?referrer=x"),
    ("utm_ csak aláhúzással", BARE, "https://kk.coach/a?utmx=1", "https://kk.coach/a?utmx=1"),
    ("érték nélküli tracking-kulcs", BARE, "https://kk.coach/a?fbclid&b=1", "https://kk.coach/a?b=1"),
    # 5. megmaradó query-paraméterek ábécérendben
    ("kulcsok rendezve", BARE, "https://kk.coach/a?b=2&a=1", "https://kk.coach/a?a=1&b=2"),
    ("azonos kulcs sorrendje marad", BARE, "https://kk.coach/a?a=2&a=1", "https://kk.coach/a?a=2&a=1"),
    ("kódolás érintetlen", BARE, "https://kk.coach/a?b=%20&a=x+y", "https://kk.coach/a?a=x+y&b=%20"),
    ("üres érték és kulcs-flag marad", BARE, "https://kk.coach/a?b&a=", "https://kk.coach/a?a=&b"),
    ("üres darabok eldobva", BARE, "https://kk.coach/a?&a=1&", "https://kk.coach/a?a=1"),
    ("üres query eldobva", BARE, "https://kk.coach/?", "https://kk.coach/"),
    # 6. trailing slash a site domináns formájára
    ("slash hozzá", SLASH, "https://kk.coach/a", "https://kk.coach/a/"),
    ("slash marad", SLASH, "https://kk.coach/a/", "https://kk.coach/a/"),
    ("slash hozzá query előtt", SLASH, "https://kk.coach/a?x=1", "https://kk.coach/a/?x=1"),
    ("slash le", NO_SLASH, "https://kk.coach/a/", "https://kk.coach/a"),
    ("többszörös slash le", NO_SLASH, "https://kk.coach/a//", "https://kk.coach/a"),
    ("slash nélkül marad", NO_SLASH, "https://kk.coach/a", "https://kk.coach/a"),
    ("gyökér marad, slash-es site", SLASH, "https://kk.coach/", "https://kk.coach/"),
    ("gyökér marad, slash nélküli site", NO_SLASH, "https://kk.coach/", "https://kk.coach/"),
    ("fájl nem kap slasht", SLASH, "https://kk.coach/cv.pdf", "https://kk.coach/cv.pdf"),
    ("döntés nélkül érintetlen /a", BARE, "https://kk.coach/a", "https://kk.coach/a"),
    ("döntés nélkül érintetlen /a/", BARE, "https://kk.coach/a/", "https://kk.coach/a/"),
    ("külső path érintetlen", SLASH, "https://example.com/a", "https://example.com/a"),
    # szintaktikai azonosság
    ("üres path → /", BARE, "https://kk.coach", "https://kk.coach/"),
    ("üres path query-vel", BARE, "https://kk.coach?x=1", "https://kk.coach/?x=1"),
    ("alapport le https", BARE, "https://kk.coach:443/a", "https://kk.coach/a"),
    ("alapport le http, aztán https", HTTPS, "http://kk.coach:80/a", "https://kk.coach/a"),
    ("nem alapport marad", BARE, "https://kk.coach:8443/a", "https://kk.coach:8443/a"),
    ("körülvevő szóköz", BARE, "  https://kk.coach/a \n", "https://kk.coach/a"),
    # együtt, a teljes lánc
    ("minden szabály egyszerre", FULL, "HTTP://WWW.KK.COACH:80/Blog?utm_source=fb&b=2&a=1#top",
     "https://kk.coach/Blog/?a=1&b=2"),
    # nem crawlolható
    ("mailto", BARE, "mailto:hello@kk.coach", None),
    ("tel", BARE, "tel:+36301234567", None),
    ("javascript", BARE, "javascript:void(0)", None),
    ("relatív", BARE, "/rolam", None),
    ("host nélkül", BARE, "https:///rolam", None),
    ("hibás port", BARE, "https://kk.coach:abc/", None),
]


@pytest.mark.parametrize(
    ("policy", "url", "expected"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_normalize(policy, url, expected):
    assert normalize(url, policy) == expected


@pytest.mark.parametrize(
    ("policy", "url"),
    [(case[1], case[3]) for case in CASES if case[3] is not None],
    ids=[case[0] for case in CASES if case[3] is not None],
)
def test_normalize_is_idempotent(policy, url):
    assert normalize(url, policy) == url


def test_variants_dedup_to_one_url():
    variants = [
        "http://kk.coach/szolgaltatasok",
        "https://www.kk.coach/szolgaltatasok/",
        "https://KK.coach/szolgaltatasok#arak",
        "https://kk.coach/szolgaltatasok?utm_source=newsletter",
        "https://kk.coach:443/szolgaltatasok/?fbclid=x#top",
    ]
    assert {normalize(v, FULL) for v in variants} == {"https://kk.coach/szolgaltatasok/"}


SCOPE = [
    ("seed host", BARE, "https://kk.coach/a", True),
    ("www-változat", BARE, "https://www.kk.coach/", True),
    ("nyelvi aldomain", BARE, "https://en.kk.coach/", True),
    ("http is belső", BARE, "http://kk.coach/a", True),
    ("cdn kizárva", BARE, "https://cdn.kk.coach/app.js", False),
    ("static kizárva", BARE, "https://static.kk.coach/", False),
    ("img kizárva", BARE, "https://img.kk.coach/logo.png", False),
    ("külső", BARE, "https://example.com/", False),
    ("domain mint aldomain-előtag", BARE, "https://kk.coach.example.com/", False),
    ("mailto", BARE, "mailto:hello@kk.coach", False),
    ("relatív", BARE, "/rolam", False),
    ("többtagú utótag", UrlPolicy.from_seed("https://shop.x.co.uk/"), "https://x.co.uk/", True),
    ("többtagú utótag, más domain", UrlPolicy.from_seed("https://shop.x.co.uk/"),
     "https://y.co.uk/", False),
    ("privát utótag külön site", UrlPolicy.from_seed("https://a.github.io/"),
     "https://b.github.io/", False),
    ("kizárt előtagú seed maga belső", UrlPolicy.from_seed("https://static.x.hu/"),
     "https://static.x.hu/a", True),
    ("saját kizárási lista", UrlPolicy("kk.coach", excluded_subdomains=("en",)),
     "https://en.kk.coach/", False),
]


@pytest.mark.parametrize(
    ("policy", "url", "expected"),
    [case[1:] for case in SCOPE],
    ids=[case[0] for case in SCOPE],
)
def test_is_internal(policy, url, expected):
    assert is_internal(url, policy) is expected


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("kk.coach", "kk.coach"),
        ("www.kk.coach", "kk.coach"),
        ("en.kk.coach", "kk.coach"),
        ("WWW.Vestino.HU", "vestino.hu"),
        ("shop.x.co.uk", "x.co.uk"),
        ("a.github.io", "a.github.io"),
        ("127.0.0.1", "127.0.0.1"),
        ("localhost", "localhost"),
    ],
)
def test_registrable_domain(host, expected):
    assert registrable_domain(host) == expected


def test_policy_from_seed():
    policy = UrlPolicy.from_seed("https://WWW.KK.coach/rolam")
    assert policy.seed_host == "www.kk.coach"
    assert policy.domain == "kk.coach"
    assert policy.https_redirect is False
    assert policy.trailing_slash is None


@pytest.mark.parametrize("seed", ["kk.coach", "ftp://kk.coach/", "https:///a", ""])
def test_policy_rejects_non_http_seed(seed):
    with pytest.raises(ValueError):
        UrlPolicy.from_seed(seed)


def _urls(*paths):
    return [f"https://kk.coach{p}" for p in paths]


TRAILING = [
    ("többség slash-es", _urls("/", "/a/", "/b/", "/c"), True),
    ("többség slash nélküli", _urls("/a", "/b", "/c/"), False),
    ("döntetlen", _urls("/a/", "/b"), None),
    ("csak gyökér és fájl", _urls("/", "/cv.pdf", "/sitemap.xml"), None),
    ("üres", [], None),
    ("query és fragment nem számít", _urls("/a/?x=1", "/b/#y", "/c"), True),
    ("fájl nem szavaz", _urls("/a", "/x.pdf", "/y.html", "/z.jpg", "/b/"), None),
    ("csak az első 50", _urls(*[f"/n{i}" for i in range(50)], *[f"/s{i}/" for i in range(100)]),
     False),
]


@pytest.mark.parametrize(
    ("urls", "expected"),
    [case[1:] for case in TRAILING],
    ids=[case[0] for case in TRAILING],
)
def test_decide_trailing_slash(urls, expected):
    assert decide_trailing_slash(urls) is expected


def test_decide_trailing_slash_reads_only_the_sample():
    def urls():
        yield from _urls("/a/", "/b/")
        raise AssertionError("a mintán túl nem olvas")

    assert decide_trailing_slash(urls(), sample=2) is True
