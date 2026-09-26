"""URL-normalizálás és scope egy site-on belül.

Normalizálási szabályok, ebben a sorrendben (a 8. a path-on a 6. előtt fut):

1. host kisbetűre, a `www.`-változat a seed formájára;
2. séma https-re, ha a site http→https 301-et ad (csak belső URL-en);
3. fragment eldobva;
4. tracking-paraméterek eldobva: `utm_*`, `gclid`, `fbclid`, `mc_*`, `ref`;
5. a megmaradó query-paraméterek kulcs szerint ábécérendben, azonos kulcsnál eredeti sorrendben;
6. trailing slash a site domináns formájára (`site.trailing_slash`, csak belső URL-en);
   fájl az, aminek az utolsó szegmense ismert kiterjesztésre végződik, az érintetlen;
7. a canonical nem írja felül az URL-t: ez a modul nem is látja;
8. a path kódolása egységes: a nem fenntartott ASCII (betű, szám, `-._~`) nyersen, a nem-ASCII
   és a path-ban nem megengedett karakter UTF-8 %XX-kódolva, a hex nagybetűvel. A fenntartott
   karakter (`/`, `:`, `@`, `+`, ...) abban a formában marad, ahogy jött: a kódolt `%2F` nem
   válik szegmenshatárrá, a nyers `+` nem válik `%2B`-vé.

Ezen felül csak szintaktikai azonosság: üres path helyett `/`, az alapértelmezett port eldobva.
A query kódolása érintetlen marad.

Belső az az URL, amelynek a registrable domainje a seedé, nem kizárt aldomainen van, és nem a
CDN saját útvonala (`/cdn-cgi/`): az a proxy infrastruktúrája, nem a site tartalma.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from itertools import islice
from urllib.parse import unquote_plus, urlsplit, urlunsplit

import tldextract

EXCLUDED_SUBDOMAINS = ("cdn", "static", "img")
INFRASTRUCTURE_PATHS = ("/cdn-cgi/",)
TRAILING_SLASH_SAMPLE = 50

_DEFAULT_PORTS = {"http": 80, "https": 443}
_TRACKING_PREFIXES = ("utm_", "mc_")
_TRACKING_NAMES = frozenset({"gclid", "fbclid", "ref"})
_FILE_EXTENSIONS = frozenset({
    "html", "htm", "php", "pdf", "xml", "txt", "csv", "json",
    "jpg", "jpeg", "png", "gif", "webp", "svg", "ico",
    "css", "js", "woff", "woff2", "mp4", "zip",
})
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
# Egy érvényes %XX-escape, vagy egy karakter, ami nyersen nem állhat a path-ban.
_PATH_TOKEN = re.compile(r"%[0-9A-Fa-f]{2}|[^A-Za-z0-9\-._~!$&'()*+,;=:@/]")

# A csomagba épített PSL-pillanatkép, hálózat és lemez-cache nélkül; a privát utótagok
# (github.io, ...) külön site-ot jelentenek.
_psl = tldextract.TLDExtract(
    suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True
)


@dataclass(frozen=True)
class UrlPolicy:
    """Egy site normalizálási és scope-döntései. A crawl elején rögzül, közben nem változik."""

    seed_host: str
    https_redirect: bool = False
    trailing_slash: bool | None = None
    excluded_subdomains: tuple[str, ...] = EXCLUDED_SUBDOMAINS

    @classmethod
    def from_seed(
        cls,
        seed_url: str,
        *,
        https_redirect: bool = False,
        trailing_slash: bool | None = None,
    ) -> UrlPolicy:
        """A seed hostja kisbetűvel, úgy, ahogy megadták (www-vel vagy anélkül)."""
        parts = urlsplit(seed_url.strip())
        if parts.scheme.lower() not in _DEFAULT_PORTS or not parts.hostname:
            raise ValueError(f"nem http(s) seed URL: {seed_url!r}")
        return cls(
            seed_host=parts.hostname,
            https_redirect=https_redirect,
            trailing_slash=trailing_slash,
        )

    @property
    def domain(self) -> str:
        """A site registrable domainje; ez a `site.domain` és a DuckDB-fájl neve."""
        return registrable_domain(self.seed_host)


@lru_cache(maxsize=4096)
def registrable_domain(host: str) -> str:
    """A host registrable domainje; IP-címnél és localhostnál maga a host."""
    host = host.lower()
    return _psl(host).top_domain_under_public_suffix or host


def public_suffix(host: str) -> str:
    """A host public suffixe (`co.uk`, `hu`, `github.io`); IP-címnél és localhostnál üres."""
    return _psl(host.lower()).suffix


def is_internal(url: str, policy: UrlPolicy) -> bool:
    """Abszolút http(s) URL a seed registrable domainjén, nem kizárt aldomainen, nem a CDN
    infrastruktúra-útvonalán."""
    parts = urlsplit(url.strip())
    host = parts.hostname
    if parts.scheme.lower() not in _DEFAULT_PORTS or not host:
        return False
    if is_infrastructure(url):
        return False
    domain = policy.domain
    if registrable_domain(host) != domain:
        return False
    if host in (policy.seed_host, domain):
        return True
    return host.split(".", 1)[0] not in policy.excluded_subdomains


def is_infrastructure(url: str) -> bool:
    """A CDN vagy proxy saját útvonala a site hostján (`/cdn-cgi/`), nem a site tartalma."""
    return urlsplit(url.strip()).path.startswith(INFRASTRUCTURE_PATHS)


def normalize(url: str, policy: UrlPolicy) -> str | None:
    """Az URL normalizált alakja; None, ha nem abszolút http(s) URL érvényes hosttal."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = parts.hostname
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    internal = is_internal(url, policy)

    host = _unify_www(host, policy.seed_host)
    if port == _DEFAULT_PORTS[scheme]:
        port = None
    if internal and policy.https_redirect and scheme == "http":
        scheme = "https"
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"

    path = normalize_path_encoding(parts.path) or "/"
    if internal:
        path = _apply_trailing_slash(path, policy.trailing_slash)
    return urlunsplit((scheme, netloc, path, _clean_query(parts.query), ""))


def normalize_path_encoding(path: str) -> str:
    """A 8. szabály egy path-ra: egységes %XX-kódolás, a fenntartott karakter formája marad."""
    return _PATH_TOKEN.sub(_normalize_path_token, path)


def decide_trailing_slash(
    urls: Iterable[str], sample: int = TRAILING_SLASH_SAMPLE
) -> bool | None:
    """A domináns path-forma az első `sample` URL-ből; None, ha egyik forma sincs többségben.

    A gyökér és a fájl (ismert kiterjesztés az utolsó szegmensben) nem számít bele.
    """
    with_slash = without_slash = 0
    for url in islice(urls, sample):
        path = urlsplit(url.strip()).path
        if not path or path == "/" or _is_file(path):
            continue
        if path.endswith("/"):
            with_slash += 1
        else:
            without_slash += 1
    if with_slash == without_slash:
        return None
    return with_slash > without_slash


def slash_alternate(url: str) -> str | None:
    """Az URL a trailing slash másik alakjával; None a gyökérnél és a fájlnál, ahol a 6. szabály
    nem ír át."""
    parts = urlsplit(url)
    path = parts.path
    if not path or path == "/" or _is_file(path):
        return None
    toggled = path.rstrip("/") if path.endswith("/") else f"{path}/"
    return urlunsplit((parts.scheme, parts.netloc, toggled, parts.query, parts.fragment))


def _unify_www(host: str, seed_host: str) -> str:
    bare = seed_host.removeprefix("www.")
    return seed_host if host in (bare, f"www.{bare}") else host


def _clean_query(query: str) -> str:
    kept: list[tuple[str, str]] = []
    for piece in query.split("&"):
        if not piece:
            continue
        key = unquote_plus(piece.split("=", 1)[0])
        name = key.lower()
        if name.startswith(_TRACKING_PREFIXES) or name in _TRACKING_NAMES:
            continue
        kept.append((key, piece))
    kept.sort(key=lambda pair: pair[0])
    return "&".join(piece for _, piece in kept)


def _apply_trailing_slash(path: str, trailing_slash: bool | None) -> str:
    if trailing_slash is None or path == "/" or _is_file(path):
        return path
    if trailing_slash:
        return path if path.endswith("/") else f"{path}/"
    return path.rstrip("/") or "/"


def _normalize_path_token(match: re.Match[str]) -> str:
    token = match.group()
    if len(token) == 3:
        char = chr(int(token[1:], 16))
        return char if char in _UNRESERVED else token.upper()
    return "".join(f"%{byte:02X}" for byte in token.encode("utf-8"))


def _is_file(path: str) -> bool:
    last = path.rstrip("/").rsplit("/", 1)[-1]
    _, dot, extension = last.rpartition(".")
    return bool(dot) and extension.lower() in _FILE_EXTENSIONS
