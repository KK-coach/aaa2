"""Entitás-validálás: Google Knowledge Graph Search API és Wikipedia, minden entitásra.

- KG: a `legacy/kg_validate.py` ötkategóriás osztályozója, név és típus szerint. Név-egyezés:
  az `alias_key` szerint, pontosan (nincs fuzzy), a névre vagy egy aliasra. Típus-egyezés: a
  találat típusa (`config/kg_types.toml` szerint leképezve) az entitás típusa vagy a
  `type_suggested`; conceptnél a csak "Thing" típusú találat is.
  - high: van `detailedDescription` (Wikipedia-alapú leírás); medium: csak `description`;
    stub: egyetlen név- és típus-egyezés leírás nélkül; ambiguous: több; no_match: nincs.
  - Más típusú, azonos nevű találat nem ad státuszt (no_match), a típusa a `kg_type`-ba kerül,
    és `kg_type_mismatch` jelöli.
  - Ha a KG-típus = `type_suggested`, és a találat high / medium, a típus átáll
    (`type_changed_from` a régi); ez az egyetlen út a típusváltásra. Más eltérés csak jelölés.
  - A kérés nyelvei: az entitás nyelve és az angol. Kulcs nélkül a KG kimarad, nem hiba; API-hiba
    esetén a `kg_status` marad (a hiba nem bizonyítéka a hiánynak).
- Wikipedia: a keresés top-1 találata az entitás nyelvén, majd angolul; csak akkor, ha a cím a
  kulcs szerint pontosan egyezik a névvel vagy egy aliasszal, és nem egyértelműsítő lap.
- Cache: a válasz a kérés kanonikus URL-je szerint (API-kulcs nélkül) a site-adatbázisba, a
  találatok a shared.duckdb-be is; ismételt futásnál onnan jön, nincs újrahívás.
- Minden kimenő kérés naplózva (`validation_calls`): státusz, kísérletek, késleltetés, hiba.
  429-nél, 5xx-nél és kapcsolati hibánál újrapróba (a `Retry-After` legalább annyi várakozás);
  a Wikipedia-kérések között legalább `WIKI_MIN_INTERVAL` másodperc.
"""
from __future__ import annotations

import json
import time
import tomllib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import duckdb
import httpx

from aaa2.entities.rules import alias_key
from aaa2.llm.client import ENV_PATH, Retry, api_key
from aaa2.llm.schemas import ENTITY_TYPES

KG_ENDPOINT = "https://kgsearch.googleapis.com/v1/entities:search"
WIKI_API = "https://{lang}.wikipedia.org/w/api.php"
KG_TYPES_FILE = Path(__file__).parent / "config" / "kg_types.toml"
KG_KEY_ENV = "GOOGLE_KG_API_KEY"
KG_LIMIT = 10
KG_DAILY_QUOTA = 100_000
WIKI_MIN_INTERVAL = 0.1
RECOGNIZED = ("high", "medium")
USER_AGENT = "aaa2/0.1 (+https://github.com/KK-coach/aaa2)"
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def load_kg_types(path: Path = KG_TYPES_FILE) -> list[tuple[str, str]]:
    pairs = [tuple(pair) for pair in tomllib.loads(path.read_text(encoding="utf-8"))["types"]]
    invalid = [pair for pair in pairs if pair[1] not in ENTITY_TYPES]
    if invalid:
        raise ValueError(f"{path.name}: ismeretlen entitás-típus: {invalid}")
    return pairs


def kg_type_of(kg_types: list[str], mapping: list[tuple[str, str]]) -> str | None:
    present = {_short(t) for t in kg_types}
    return next((ours for theirs, ours in mapping if theirs in present), None)


# ---------------------------------------------------------------------------
# KG-osztályozó és Wikipedia-egyezés
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KGMatch:
    status: str
    kg_id: str | None = None
    name: str | None = None
    types: tuple[str, ...] = ()
    kg_type: str | None = None
    score: float | None = None
    alternatives: int = 0


def classify_kg(body: dict, name_keys: set[str], prefer: set[str],
                mapping: list[tuple[str, str]]) -> KGMatch:
    matches = []
    for item in (body or {}).get("itemListElement") or []:
        result = item.get("result") or {}
        names = _values(result.get("name"))
        if not any(alias_key(name) in name_keys for name in names):
            continue
        types = [str(t) for t in _as_list(result.get("@type")) if t]
        matches.append((item.get("resultScore") or 0.0, result, types, kg_type_of(types, mapping)))
    pool = [m for m in matches
            if m[3] in prefer or (m[3] is None and "concept" in prefer)]
    if not pool:
        typed = sorted((m for m in matches if m[3] is not None), key=lambda m: m[0],
                       reverse=True)
        return KGMatch("no_match", types=tuple(typed[0][2]) if typed else (),
                       kg_type=typed[0][3] if typed else None)
    pool.sort(key=lambda m: m[0], reverse=True)
    score, result, types, kg_type = pool[0]
    if any(_values(d.get("articleBody")) for d in _dicts(result.get("detailedDescription"))):
        status = "high"
    elif _values(result.get("description")):
        status = "medium"
    else:
        status = "stub" if len(pool) == 1 else "ambiguous"
    return KGMatch(status, result.get("@id"), _values(result.get("name"))[0], tuple(types),
                   kg_type, score, len(pool))


def decide_type(current: str, suggested: str | None, kg: KGMatch | None
                ) -> tuple[str, str | None, bool]:
    """(új típus, a régi típus, ha átállt, eltérés-jelölés)."""
    if kg is None or kg.kg_type is None or kg.kg_type == current:
        return current, None, False
    if kg.kg_type == suggested and kg.status in RECOGNIZED:
        return kg.kg_type, current, False
    return current, None, True


def wikipedia_match(body: dict, name_keys: set[str]) -> str | None:
    """A top-1 lap URL-je, ha a címe pontosan egyezik és nem egyértelműsítő lap."""
    pages = ((body or {}).get("query") or {}).get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    if not pages:
        return None
    page = min(pages, key=lambda p: p.get("index", 0))
    if "disambiguation" in (page.get("pageprops") or {}):
        return None
    if alias_key(page.get("title") or "") not in name_keys:
        return None
    return page.get("fullurl") or page.get("canonicalurl")


def canonical_key(url: httpx.URL | str) -> str:
    """A kérés URL-je az API-kulcs nélkül, rendezett paraméterekkel."""
    url = httpx.URL(url)
    params = sorted((k, v) for k, v in url.params.multi_items() if k != "key")
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params)
    return f"{url.scheme}://{url.host}{url.path}?{query}"


# ---------------------------------------------------------------------------
# API-hívás cache-sel, naplóval, újrapróbával
# ---------------------------------------------------------------------------


@dataclass
class _Counts:
    calls: Counter = field(default_factory=Counter)        # kimenő kérés szolgáltatásonként
    cache_site: int = 0
    cache_shared: int = 0
    errors: Counter = field(default_factory=Counter)


class _Api:
    def __init__(self, con, shared, http: httpx.Client, retry: Retry,
                 clock: Callable[[], datetime], monotonic: Callable[[], float]):
        self.con, self.shared, self.http, self.retry = con, shared, http, retry
        self.clock, self.monotonic = clock, monotonic
        self.counts = _Counts()
        self.last_wiki = -WIKI_MIN_INTERVAL

    def get(self, service: str, url: str, params: list[tuple[str, str]]) -> tuple[str, dict | None]:
        request = self.http.build_request("GET", url, params=params,
                                          headers={"User-Agent": USER_AGENT})
        key = canonical_key(request.url)
        cached = self._cached(self.con, service, key)
        if cached is not None:
            self.counts.cache_site += 1
            return key, cached
        cached = self._cached(self.shared, service, key) if self.shared is not None else None
        if cached is not None:
            self.counts.cache_shared += 1
            self._store(self.con, service, key, cached)
            return key, cached
        body = self._call(service, key, request)
        if body is not None:
            self._store(self.con, service, key, body)
        return key, body

    def share(self, service: str, key: str) -> None:
        body = self._cached(self.con, service, key)
        if self.shared is not None and body is not None:
            self._store(self.shared, service, key, body)

    def _call(self, service: str, key: str, request: httpx.Request) -> dict | None:
        attempt, status, error = 0, None, None
        while True:
            attempt += 1
            if service == "wikipedia":
                wait = WIKI_MIN_INTERVAL - (self.monotonic() - self.last_wiki)
                if wait > 0:
                    self.retry.sleep(wait)
                self.last_wiki = self.monotonic()
            started = time.perf_counter()
            retry_after = None
            try:
                response = self.http.send(request)
                status = response.status_code
                body = response.json() if status == 200 else None
                if isinstance(body, dict) and body.get("error"):
                    code = (body["error"] or {}).get("code")
                    error = f"api_error: {code or ''} {(body['error'] or {}).get('info') or ''}"
                    status = 429 if code == "maxlag" else status
                    body = None
                elif status != 200:
                    error = f"HTTP {status}"
                retry_after = response.headers.get("retry-after")
            except (httpx.HTTPError, ValueError) as exc:
                status, body, error = None, None, f"{type(exc).__name__}: {exc}"[:200]
            latency = round((time.perf_counter() - started) * 1000)
            transient = status is None or status in _RETRY_STATUS
            if body is not None or not transient or attempt > len(self.retry.delays):
                break
            self.retry.wait(attempt - 1)
            if retry_after and retry_after.isdigit():
                self.retry.sleep(min(float(retry_after), 60.0))
        self.counts.calls[service] += 1
        if body is None:
            self.counts.errors[service] += 1
        self.con.execute(
            "INSERT INTO validation_calls (service, request_key, status, attempts, latency_ms, "
            "error, called_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [service, key, status, attempt, latency, None if body is not None else error,
             self.clock()])
        return body

    @staticmethod
    def _cached(con, service: str, key: str) -> dict | None:
        row = con.execute("SELECT response FROM validation_cache WHERE service = ? AND "
                          "request_key = ?", [service, key]).fetchone()
        return json.loads(row[0]) if row else None

    def _store(self, con, service: str, key: str, body: dict) -> None:
        con.execute("INSERT OR REPLACE INTO validation_cache VALUES (?, ?, ?, ?)",
                    [service, key, json.dumps(body, ensure_ascii=False), self.clock()])


# ---------------------------------------------------------------------------
# a futás
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationRun:
    entities: int
    kg_skipped: bool
    statuses: dict[str, int]
    type_changes: list[tuple[str, str, str]]
    mismatches: int
    wikipedia: int
    calls: dict[str, int]
    cache_site: int
    cache_shared: int
    errors: dict[str, int]


def validate_entities(con: duckdb.DuckDBPyConnection,
                      shared: duckdb.DuckDBPyConnection | None = None, *,
                      http: httpx.Client | None = None, env_file: Path = ENV_PATH,
                      limit: int | None = None, wikipedia: bool = True,
                      retry: Retry | None = None, clock: Callable[[], datetime] | None = None,
                      monotonic: Callable[[], float] = time.monotonic) -> ValidationRun:
    clock = clock or _now
    kg_key = api_key(KG_KEY_ENV, env_file)
    mapping = load_kg_types()
    own_http = http is None
    http = http or httpx.Client(timeout=20.0)
    api = _Api(con, shared, http, retry or Retry(), clock, monotonic)
    statuses: Counter[str] = Counter()
    changes: list[tuple[str, str, str]] = []
    mismatches = found = 0
    rows = con.execute(
        "SELECT entity_id, name, type, type_suggested, aliases, lang FROM entities "
        "ORDER BY entity_id" + (" LIMIT ?" if limit else ""), [limit] if limit else [],
    ).fetchall()
    try:
        for entity_id, name, kind, suggested, aliases, lang in rows:
            keys = {alias_key(n) for n in [name, *(aliases or [])]}
            langs = [lang, "en"] if lang and lang != "en" else ["en"]
            kg = None
            if kg_key:
                params = [("query", name), ("limit", str(KG_LIMIT)),
                          *[("languages", code) for code in langs], ("key", kg_key)]
                key, body = api.get("kg", KG_ENDPOINT, params)
                if body is not None:
                    kg = classify_kg(body, keys, {kind, suggested} - {None}, mapping)
                    if kg.status != "no_match":
                        api.share("kg", key)
            url, wiki_ok = None, wikipedia
            if wikipedia:
                for code in langs:
                    key, body = api.get("wikipedia", WIKI_API.format(lang=code), _wiki_params(name))
                    if body is None:
                        wiki_ok = False
                        continue
                    url = wikipedia_match(body, keys)
                    if url:
                        api.share("wikipedia", key)
                        break
            new_type, changed_from, mismatch = decide_type(kind, suggested, kg)
            if kg is not None:
                statuses[kg.status] += 1
                mismatches += mismatch
                if changed_from:
                    changes.append((name, changed_from, new_type))
                con.execute(
                    "UPDATE entities SET kg_status = ?, kg_id = ?, kg_type = ?, "
                    "kg_type_mismatch = ?, type = ?, type_changed_from = coalesce(?, "
                    "type_changed_from) WHERE entity_id = ?",
                    [kg.status, kg.kg_id, kg.kg_type, mismatch, new_type, changed_from,
                     entity_id])
            if wiki_ok or url:
                found += url is not None
                con.execute("UPDATE entities SET wikipedia_url = ? WHERE entity_id = ?",
                            [url, entity_id])
            con.execute("UPDATE entities SET validated_at = ? WHERE entity_id = ?",
                        [clock(), entity_id])
    finally:
        if own_http:
            http.close()
    return ValidationRun(len(rows), kg_key is None, dict(statuses), changes, mismatches, found,
                         dict(api.counts.calls), api.counts.cache_site, api.counts.cache_shared,
                         dict(api.counts.errors))


def _wiki_params(name: str) -> list[tuple[str, str]]:
    return [("action", "query"), ("format", "json"), ("formatversion", "2"),
            ("generator", "search"), ("gsrsearch", name), ("gsrlimit", "1"),
            ("gsrnamespace", "0"), ("prop", "pageprops|info"), ("ppprop", "disambiguation"),
            ("inprop", "url"), ("maxlag", "5")]


def _values(value: object) -> list[str]:
    """Szöveg, `{"@value": …}`, vagy ezek listája (többnyelvű KG-válasz) → szövegek."""
    out = []
    for item in _as_list(value):
        if isinstance(item, dict):
            item = item.get("@value")
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def _dicts(value: object) -> list[dict]:
    return [item for item in _as_list(value) if isinstance(item, dict)]


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else [value] if value is not None else []


def _short(kg_type: str) -> str:
    return str(kg_type).rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
