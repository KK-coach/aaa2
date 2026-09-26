"""Entitás-validálás (aaa2/entities/validate.py): a KG-osztályozó, a típusdöntés, a
Wikipedia-egyezés, a cache, a napló és az újrapróba hálózat nélkül; a három készlet rögzített
KG- és Wikipedia-válaszokkal (felvétel: `pytest -m live -k record_validation`)."""
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import aaa2.cli.main as cli
import aaa2.db.connect as connect_module
from aaa2.cli.main import app
from aaa2.db.connect import connect, db_path
from aaa2.entities.rules import run_rules
from aaa2.entities.validate import (
    KG_TYPES_FILE,
    WIKI_MIN_INTERVAL,
    KGMatch,
    canonical_key,
    classify_kg,
    decide_type,
    load_kg_types,
    validate_entities,
    wikipedia_match,
)
from aaa2.llm.client import Retry
from tests.recorded import FIXTURES_DIR
from tests.test_entities_rules import html, site

NOON = datetime(2026, 9, 26, 12, 0, tzinfo=UTC).replace(tzinfo=None)
MAPPING = load_kg_types()
VALIDATION_DIR = FIXTURES_DIR / "validation"


def kg_item(name, types, description=None, article=None, score=100.0, kg_id=None):
    result = {"@id": kg_id or f"kg:/m/{abs(hash(name)) % 10000}", "name": name, "@type": types}
    if description:
        result["description"] = description
    if article:
        result["detailedDescription"] = {"articleBody": article,
                                         "url": f"https://en.wikipedia.org/wiki/{name}"}
    return {"@type": "EntitySearchResult", "result": result, "resultScore": score}


def kg_body(*items):
    return {"@type": "ItemList", "itemListElement": list(items)}


def wiki_body(title, lang="en", disambiguation=False):
    page = {"pageid": 1, "ns": 0, "title": title, "index": 1,
            "fullurl": f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}"}
    if disambiguation:
        page["pageprops"] = {"disambiguation": ""}
    return {"batchcomplete": True, "query": {"pages": [page]}}


# ---------------------------------------------------------------------------
# osztályozó, típusdöntés, Wikipedia-egyezés
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("items", "status"), [
    ([kg_item("Budapest", ["City", "Place", "Thing"], "Capital of Hungary", "Budapest is…")],
     "high"),
    ([kg_item("Budapest", ["City", "Thing"], "Capital of Hungary")], "medium"),
    ([kg_item("Budapest", ["City", "Thing"])], "stub"),
    ([kg_item("Budapest", ["City", "Thing"]), kg_item("Budapest", ["City", "Thing"])],
     "ambiguous"),
    # A típusban egyező találat előnyt élvez: a Movie nem számít bele.
    ([kg_item("Budapest", ["City", "Thing"]), kg_item("Budapest", ["Movie", "Thing"])], "stub"),
    # Egyik sem egyező típusú: nincs név- és típus-egyezés.
    ([kg_item("Budapest", ["Book"]), kg_item("Budapest", ["Movie", "Thing"])], "no_match"),
    ([kg_item("Budapesti Kávé", ["Corporation"], "Company")], "no_match"),
    ([], "no_match"),
])
def test_five_categories(items, status):
    assert classify_kg(kg_body(*items), {"budapest"}, {"place"}, MAPPING).status == status


def test_name_matches_exactly_by_key_or_alias():
    body = kg_body(kg_item("KK Coach", ["Organization"], "Consulting"),
                   kg_item("Kiss Krisztián", ["Person"], "Consultant"))
    assert classify_kg(body, {"kiss krisztian"}, {"person"}, MAPPING).name == "Kiss Krisztián"
    assert classify_kg(body, {"kk.coach"}, {"org"}, MAPPING).status == "no_match"
    assert classify_kg(body, {"kk.coach", "kk coach"}, {"org"}, MAPPING).kg_type == "org"


def test_multilingual_values_are_read():
    body = kg_body({"result": {"@id": "kg:/m/1", "@type": ["Place"],
                               "name": [{"@language": "hu", "@value": "Duna"},
                                        {"@language": "en", "@value": "Danube"}],
                               "description": [{"@language": "en", "@value": "River"}]},
                    "resultScore": 5})
    match = classify_kg(body, {"danube"}, {"place"}, MAPPING)
    assert (match.status, match.kg_type, match.name) == ("medium", "place", "Duna")


def test_only_type_consistent_matches_count():
    """A státusz csak név- és típus-egyezésből jön; más típusú találatnál no_match, a típusa
    a kg_type-ba kerül (a legjobb pontszámú leképezhetőé)."""
    body = kg_body(kg_item("Mercury", ["Planet", "Thing"], "Planet", "…", score=900),
                   kg_item("Mercury", ["Corporation", "Organization"], "Company", score=10),
                   kg_item("Mercury", ["Person"], "Singer", score=50))
    assert (classify_kg(body, {"mercury"}, {"org"}, MAPPING).status,
            classify_kg(body, {"mercury"}, {"org"}, MAPPING).score) == ("medium", 10)
    other = classify_kg(body, {"mercury"}, {"event"}, MAPPING)
    assert (other.status, other.kg_type, other.kg_id) == ("no_match", "person", None)


def test_thing_only_match_counts_for_a_concept_only():
    body = kg_body(kg_item("Execution", ["Thing"], article="Capital punishment is…"))
    concept = classify_kg(body, {"execution"}, {"concept"}, MAPPING)
    service = classify_kg(body, {"execution"}, {"service"}, MAPPING)
    assert (concept.status, concept.kg_type) == ("high", None)
    assert (service.status, service.kg_type) == ("no_match", None)
    assert decide_type("service", None, service) == ("service", None, False)


@pytest.mark.parametrize(("types", "ours"), [
    (["Corporation", "Organization", "Thing"], "org"),
    (["LocalBusiness", "Place", "Organization"], "org"),
    (["City", "AdministrativeArea", "Place"], "place"),
    (["SoftwareApplication", "Thing"], "tech"),
    (["Book", "CreativeWork"], "work"),
    (["http://schema.org/Person"], "person"),
    (["Thing"], None),
])
def test_kg_type_mapping(types, ours):
    match = classify_kg(kg_body(kg_item("X", types, "d")), {"x"}, set(), MAPPING)
    assert match.kg_type == ours


@pytest.mark.parametrize(("current", "suggested", "kg", "expected"), [
    ("concept", "tech", KGMatch("high", kg_type="tech"), ("tech", "concept", False)),
    ("concept", "tech", KGMatch("medium", kg_type="tech"), ("tech", "concept", False)),
    ("concept", "tech", KGMatch("stub", kg_type="tech"), ("concept", None, True)),
    ("concept", "tech", KGMatch("ambiguous", kg_type="tech"), ("concept", None, True)),
    ("concept", None, KGMatch("high", kg_type="tech"), ("concept", None, True)),
    ("concept", "product", KGMatch("high", kg_type="tech"), ("concept", None, True)),
    ("org", "tech", KGMatch("high", kg_type="org"), ("org", None, False)),
    ("org", None, KGMatch("high", kg_type=None), ("org", None, False)),
    ("org", None, KGMatch("no_match"), ("org", None, False)),
    ("org", None, KGMatch("no_match", kg_type="event"), ("org", None, True)),
    ("concept", "tech", KGMatch("no_match", kg_type="tech"), ("concept", None, True)),
    ("org", None, None, ("org", None, False)),
])
def test_type_changes_only_to_the_suggestion_on_a_recognized_match(current, suggested, kg,
                                                                    expected):
    assert decide_type(current, suggested, kg) == expected


@pytest.mark.parametrize(("body", "url"), [
    (wiki_body("Budapest", "hu"), "https://hu.wikipedia.org/wiki/Budapest"),
    (wiki_body("Kiss Krisztián"), "https://en.wikipedia.org/wiki/Kiss_Krisztián"),
    (wiki_body("KK Coach"), "https://en.wikipedia.org/wiki/KK_Coach"),
    (wiki_body("Budapest", disambiguation=True), None),
    (wiki_body("Budapest (film)"), None),
    (wiki_body("Budapesti"), None),
    ({"batchcomplete": True}, None),
    ({"query": {"pages": {"12": {"title": "Budapest", "index": 1,
                                 "fullurl": "https://en.wikipedia.org/wiki/Budapest"}}}},
     "https://en.wikipedia.org/wiki/Budapest"),
])
def test_wikipedia_match_is_exact_and_unambiguous(body, url):
    assert wikipedia_match(body, {"budapest", "kiss krisztian", "kk coach"}) == url


def test_canonical_key_drops_the_api_key_and_sorts():
    url = ("https://kgsearch.googleapis.com/v1/entities:search?query=Kiss%20Kriszti%C3%A1n"
           "&key=TITOK&limit=10&languages=hu&languages=en")
    key = canonical_key(url)
    assert "TITOK" not in key and "key=" not in key
    assert key == ("https://kgsearch.googleapis.com/v1/entities:search?languages=en&"
                   "languages=hu&limit=10&query=Kiss%20Kriszti%C3%A1n")


def test_kg_types_config(tmp_path):
    bad = tmp_path / "kg_types.toml"
    bad.write_text(KG_TYPES_FILE.read_text(encoding="utf-8").replace(
        '["Person", "person"]', '["Person", "ember"]'), encoding="utf-8")
    with pytest.raises(ValueError, match="ember"):
        load_kg_types(bad)


# ---------------------------------------------------------------------------
# a futás hálózat nélkül
# ---------------------------------------------------------------------------


class FakeApis:
    """KG és Wikipedia: név → válasz; ismeretlen név üres válasz. `fail`: szolgáltatásonként a
    rendes válasz előtti hibakódok."""

    def __init__(self):
        self.kg, self.wiki, self.fail, self.requests = {}, {}, {}, []

    def handler(self, request):
        self.requests.append(request)
        service = "kg" if request.url.host == "kgsearch.googleapis.com" else "wikipedia"
        if self.fail.get(service):
            return httpx.Response(self.fail[service].pop(0), json={"error": {"message": "x"}},
                                  headers={"retry-after": "2"})
        if service == "kg":
            body = self.kg.get(request.url.params["query"], kg_body())
        else:
            lang = request.url.host.split(".")[0]
            body = self.wiki.get((lang, request.url.params["gsrsearch"]), {"batchcomplete": True})
        return httpx.Response(200, json=body)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def services(self):
        return ["kg" if r.url.host.startswith("kg") else r.url.host.split(".")[0]
                for r in self.requests]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_KG_API_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("GOOGLE_KG_API_KEY=TITOK-KULCS\n", encoding="utf-8")
    return path


def entities_db(rows):
    con = connect(":memory:")
    for name, kind, lang, suggested in rows:
        con.execute("INSERT INTO entities (name, type, lang, type_suggested, source) "
                    "VALUES (?, ?, ?, ?, 'llm')", [name, kind, lang, suggested])
    return con


def run(con, shared, apis, env, sleeps=None, **kwargs):
    return validate_entities(con, shared, http=apis.client(), env_file=env,
                             retry=Retry(sleep=(sleeps.append if sleeps is not None
                                                else lambda _: None), jitter=0),
                             clock=lambda: NOON, **kwargs)


def standard_apis():
    apis = FakeApis()
    apis.kg["Budapest"] = kg_body(kg_item("Budapest", ["City", "Place"], "Capital", "Budapest is",
                                          kg_id="kg:/m/budapest"))
    apis.kg["BigQuery"] = kg_body(kg_item("BigQuery", ["SoftwareApplication"], "Data warehouse",
                                          kg_id="kg:/m/bq"))
    apis.kg["Példa Kft."] = kg_body(kg_item("Példa Kft.", ["Person"], "Someone"))
    apis.wiki[("hu", "Budapest")] = wiki_body("Budapest", "hu")
    apis.wiki[("en", "BigQuery")] = wiki_body("BigQuery")
    return apis


def test_validation_writes_kg_and_wikipedia_fields(env):
    con = entities_db([("Budapest", "place", "hu", None), ("BigQuery", "concept", "en", "tech"),
                       ("Példa Kft.", "org", "hu", None), ("Ismeretlen", "concept", "hu", None)])
    apis = standard_apis()
    result = run(con, connect(":memory:"), apis, env)
    rows = con.execute("SELECT name, type, type_changed_from, kg_status, kg_id, kg_type, "
                       "kg_type_mismatch, wikipedia_url, validated_at FROM entities "
                       "ORDER BY entity_id").fetchall()
    assert [r[:8] for r in rows] == [
        ("Budapest", "place", None, "high", "kg:/m/budapest", "place", False,
         "https://hu.wikipedia.org/wiki/Budapest"),
        ("BigQuery", "tech", "concept", "medium", "kg:/m/bq", "tech", False,
         "https://en.wikipedia.org/wiki/BigQuery"),
        ("Példa Kft.", "org", None, "no_match", None, "person", True, None),
        ("Ismeretlen", "concept", None, "no_match", None, None, False, None),
    ]
    assert all(r[8] == NOON for r in rows)
    assert result.type_changes == [("BigQuery", "concept", "tech")]
    assert (result.mismatches, result.wikipedia, result.statuses) == (
        1, 2, {"high": 1, "medium": 1, "no_match": 2})
    # hu entitás: KG egyszer (hu, en), Wikipedia hu, majd en, ha a hu nem talált
    assert apis.services() == ["kg", "hu", "kg", "en", "kg", "hu", "en", "kg", "hu", "en"]
    kg_request = apis.requests[0]
    assert kg_request.url.params.get_list("languages") == ["hu", "en"]
    assert kg_request.headers["user-agent"].startswith("aaa2/")


def test_api_key_is_never_stored(env):
    con = entities_db([("Budapest", "place", "hu", None)])
    run(con, connect(":memory:"), standard_apis(), env)
    stored = json.dumps(con.execute("SELECT * FROM validation_cache").fetchall(), default=str) \
        + json.dumps(con.execute("SELECT * FROM validation_calls").fetchall(), default=str)
    assert "TITOK-KULCS" not in stored and "Budapest" in stored


def test_rerun_uses_the_site_cache_and_other_sites_the_shared_hits(env):
    shared = connect(":memory:")
    con = entities_db([("Budapest", "place", "hu", None), ("Ismeretlen", "concept", "hu", None)])
    apis = standard_apis()
    run(con, shared, apis, env)
    first = len(apis.requests)
    again = run(con, shared, apis, env)
    assert (len(apis.requests), again.calls, again.cache_site) == (first, {}, 5)
    other = entities_db([("Budapest", "place", "hu", None), ("Ismeretlen", "concept", "hu", None)])
    third = run(other, shared, apis, env)
    # a Budapest két találata (KG, hu Wikipedia) a shared-ből; az Ismeretlen három kérése újra
    assert (third.cache_shared, third.calls) == (2, {"kg": 1, "wikipedia": 2})
    assert other.execute("SELECT kg_status, wikipedia_url FROM entities WHERE name = 'Budapest'"
                         ).fetchone() == ("high", "https://hu.wikipedia.org/wiki/Budapest")


def test_missing_kg_key_skips_kg_only(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_KG_API_KEY", raising=False)
    con = entities_db([("Budapest", "place", "hu", None)])
    apis = standard_apis()
    result = run(con, connect(":memory:"), apis, tmp_path / "nincs.env")
    assert result.kg_skipped and apis.services() == ["hu"]
    assert con.execute("SELECT kg_status, wikipedia_url FROM entities").fetchone() == (
        "unchecked", "https://hu.wikipedia.org/wiki/Budapest")


def test_rate_limit_is_retried_and_logged(env):
    con = entities_db([("Budapest", "place", "en", None)])
    apis = standard_apis()
    apis.fail["kg"] = [429]
    sleeps = []
    run(con, connect(":memory:"), apis, env, sleeps=sleeps, wikipedia=False)
    assert con.execute("SELECT service, status, attempts, error FROM validation_calls"
                       ).fetchall() == [("kg", 200, 2, None)]
    assert sleeps == [1.0, 2.0]          # az újrapróba várakozása, utána a Retry-After


def test_persistent_error_keeps_the_status_and_is_counted(env):
    con = entities_db([("Budapest", "place", "en", "tech")])
    con.execute("UPDATE entities SET kg_status = 'high'")
    apis = standard_apis()
    apis.fail["kg"] = [503] * 4
    result = run(con, connect(":memory:"), apis, env, wikipedia=False)
    assert result.errors == {"kg": 1}
    assert con.execute("SELECT type, kg_status FROM entities").fetchone() == ("place", "high")
    assert con.execute("SELECT status, attempts, error FROM validation_calls").fetchall() == [
        (503, 4, "HTTP 503")]
    assert con.execute("SELECT count(*) FROM validation_cache").fetchone() == (0,)


def test_wikipedia_requests_keep_their_distance(env):
    con = entities_db([("Budapest", "place", "hu", None)])
    apis = standard_apis()
    apis.wiki.pop(("hu", "Budapest"))
    clock = iter([0.0, 0.0, 0.03, 0.03])
    sleeps = []
    validate_entities(con, connect(":memory:"), http=apis.client(), env_file=env,
                      retry=Retry(sleep=sleeps.append), clock=lambda: NOON,
                      monotonic=lambda: next(clock))
    assert sleeps == [pytest.approx(WIKI_MIN_INTERVAL - 0.03)]


def test_rules_rerun_finds_an_entity_the_kg_retyped(env):
    pages = {f"/{i}/": html(f"P{i}", "<a href='/bq/'>BigQuery</a>") for i in range(3)}
    pages["/bq/"] = html("BQ", "")
    con = site(pages)
    run_rules(con)
    con.execute("UPDATE entities SET type_suggested = 'tech'")
    run(con, connect(":memory:"), standard_apis(), env)
    assert con.execute("SELECT type, type_changed_from FROM entities").fetchall() == [
        ("tech", "concept")]
    run_rules(con)
    assert con.execute("SELECT name, type, source FROM entities").fetchall() == [
        ("BigQuery", "tech", "rule")]


def test_cli_validate(env, tmp_path, monkeypatch):
    monkeypatch.setattr(connect_module, "DATA_DIR", tmp_path)
    con = connect(db_path("pelda.hu"))
    con.execute("INSERT INTO entities (name, type, lang, type_suggested, source) VALUES "
                "('Budapest', 'place', 'hu', NULL, 'rule'), ('BigQuery', 'concept', 'en', "
                "'tech', 'llm')")
    con.close()
    apis = standard_apis()
    monkeypatch.setattr(cli, "validate_entities", lambda c, s, **kw: validate_entities(
        c, s, http=apis.client(), env_file=env, retry=Retry(sleep=lambda _: None), **kw))
    result = CliRunner().invoke(app, ["validate", "pelda.hu"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "validálás: 2 entitás; KG: high 1, medium 1; Wikipedia-szócikk: 2"
    assert lines[1:3] == ["  típusváltás a KG szerint: 1, KG-típuseltérés jelölve: 0",
                          "    BigQuery: concept → tech"]
    assert lines[3].startswith("  API-hívás: KG 2, Wikipedia 2; cache: site 0, shared 0; "
                               "hiba: 0; KG ma ezen a site-on ")
    assert Path(tmp_path / "shared.duckdb").exists()


# ---------------------------------------------------------------------------
# a három készlet rögzített válaszokkal
# ---------------------------------------------------------------------------


def clone(con, path):
    """A visszajátszott crawl másolata fájlba, hogy a validálás ne a közös kapcsolaton fusson."""
    con.execute(f"ATTACH '{path}' AS clone")
    con.execute("COPY FROM DATABASE memory TO clone")
    con.execute("DETACH clone")
    return connect(path)


def outcome(con):
    return {f"{name}|{kind}": [status, kg_type, url] for name, kind, status, kg_type, url in
            con.execute("SELECT name, type, kg_status, kg_type, wikipedia_url FROM entities "
                        "ORDER BY entity_id").fetchall()}


REFERENCE_NAMES = ("kk-coach-crawl", "materia-crawl", "ngx-bootstrap-crawl")


@pytest.mark.parametrize("name", REFERENCE_NAMES)
def test_reference_validation_replays_the_recorded_answers(name, reference_crawl, tmp_path,
                                                           env):
    fixture = VALIDATION_DIR / f"{name}.json"
    if not fixture.exists():
        pytest.skip(f"nincs felvétel: pytest -m live -k record_validation ({name})")
    source = reference_crawl(name)
    if source is None:
        pytest.skip(f"nincs crawl-felvétel: {name}")
    recorded = json.loads(fixture.read_text(encoding="utf-8"))
    misses = []

    def handler(request):
        entry = recorded["responses"].get(canonical_key(request.url))
        if entry is None:
            misses.append(canonical_key(request.url))
            return httpx.Response(404, json={"error": {"message": "nincs felvéve"}})
        return httpx.Response(entry["status"], json=entry["body"])

    con = clone(source, tmp_path / f"{name}.duckdb")
    run_rules(con)
    validate_entities(con, connect(":memory:"),
                      http=httpx.Client(transport=httpx.MockTransport(handler)), env_file=env,
                      retry=Retry(sleep=lambda _: None), clock=lambda: NOON)
    assert misses == []
    assert outcome(con) == recorded["measured"]


@pytest.mark.live
@pytest.mark.parametrize("name", REFERENCE_NAMES)
def test_live_record_validation(name, reference_crawl, tmp_path):
    """Élő felvétel: a készlet szabály-entitásai KG-vel és Wikipediával; a válaszok (API-kulcs
    nélkül) és az eredmény a tests/fixtures/validation/ alá."""
    from aaa2.llm.client import api_key
    if api_key("GOOGLE_KG_API_KEY") is None:
        pytest.skip("nincs GOOGLE_KG_API_KEY")
    source = reference_crawl(name)
    if source is None:
        pytest.skip(f"nincs crawl-felvétel: {name}")
    responses = {}
    live = httpx.HTTPTransport()

    def record(request):
        response = live.handle_request(request)
        response.read()
        if response.status_code == 200:
            responses[canonical_key(request.url)] = {"status": 200, "body": response.json()}
        return response

    con = clone(source, tmp_path / f"{name}.duckdb")
    run_rules(con)
    result = validate_entities(con, connect(":memory:"),
                               http=httpx.Client(transport=httpx.MockTransport(record),
                                                 timeout=20.0))
    print(f"\n{name}: {result}")
    assert result.errors == {}
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    (VALIDATION_DIR / f"{name}.json").write_text(json.dumps(
        {"measured": outcome(con), "responses": responses}, ensure_ascii=False, indent=1),
        encoding="utf-8")


@pytest.mark.live
def test_live_smoke_ten_entities(reference_crawl, tmp_path):
    """Élő smoke: a kk.coach-készlet első 10 entitása KG-vel és Wikipediával."""
    from aaa2.llm.client import api_key
    if api_key("GOOGLE_KG_API_KEY") is None:
        pytest.skip("nincs GOOGLE_KG_API_KEY")
    source = reference_crawl("kk-coach-crawl")
    if source is None:
        pytest.skip("nincs crawl-felvétel: kk-coach-crawl")
    con = clone(source, tmp_path / "kk.duckdb")
    run_rules(con)
    result = validate_entities(con, connect(":memory:"), limit=10)
    print(f"\n{result}")
    for row in con.execute("SELECT name, type, kg_status, kg_type, kg_type_mismatch, "
                           "wikipedia_url FROM entities WHERE validated_at IS NOT NULL "
                           "ORDER BY entity_id").fetchall():
        print("  ", row)
    assert result.entities == 10 and result.errors == {}
