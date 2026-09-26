"""Az LLM-kliens a három adapterrel, hálózat nélkül: egy helyi HTTP-szerver (127.0.0.1) a három
API drótformátumában válaszol, és minden kérést rögzít. A kliensek a valódi SDK-kon át hívják.
"""
import json
import random
import threading
from dataclasses import replace
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic
import duckdb
import httpx
import openai
import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel
from typer.testing import CliRunner

import aaa2.db.connect as connect_module
from aaa2.cli.main import app
from aaa2.db.connect import connect
from aaa2.llm import ledger
from aaa2.llm.adapters import is_transient
from aaa2.llm.client import (
    BudgetExceeded,
    LLMError,
    Retry,
    SchemaMismatch,
    api_key,
    check_models,
    open_clients,
)
from aaa2.llm.config import PriceError
from aaa2.llm.schemas import EntityType

PROVIDERS = ("anthropic", "openai", "gemini")
KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")
NOON = datetime(2026, 9, 26, 12, 0, tzinfo=UTC).replace(tzinfo=None)


class Mention(BaseModel):
    name: str
    type: EntityType
    evidence: str


class PageEntities(BaseModel):
    entities: list[Mention]


EXPECTED = PageEntities(entities=[Mention(name="Materia", type="brand", evidence="a Materia")])
PAGE_JSON = EXPECTED.model_dump_json()


def anthropic_reply(text=PAGE_JSON, stop="end_turn", **usage):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "thinking", "thinking": "…", "signature": "sig"},
                    {"type": "text", "text": text}],
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0, **usage},
    }


def openai_reply(text=PAGE_JSON, status="completed", incomplete=None):
    return {
        "id": "resp_1", "object": "response", "created_at": 1790000000, "model": "gpt-6-luna",
        "status": status, "incomplete_details": incomplete,
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
             "content": [{"type": "output_text", "text": text, "annotations": []}]},
        ],
        "usage": {"input_tokens": 2000,
                  "input_tokens_details": {"cached_tokens": 500, "cache_write_tokens": 0},
                  "output_tokens": 800, "output_tokens_details": {"reasoning_tokens": 300},
                  "total_tokens": 2800},
    }


def gemini_reply(text=PAGE_JSON, finish="STOP"):
    return {
        "candidates": [{"content": {"parts": [{"text": text}], "role": "model"},
                        "finishReason": finish, "index": 0}],
        "usageMetadata": {"promptTokenCount": 3000, "cachedContentTokenCount": 1000,
                          "candidatesTokenCount": 400, "thoughtsTokenCount": 600,
                          "totalTokenCount": 4000},
        "modelVersion": "gemini-3.8-flash",
    }


MODEL_LISTS = {
    "anthropic-models": {
        "data": [{"type": "model", "id": i, "display_name": i, "created_at": "2026-09-01T00:00:00Z"}
                 for i in ("claude-opus-5-5", "claude-sonnet-5")],
        "has_more": False, "first_id": "claude-opus-5-5", "last_id": "claude-sonnet-5"},
    "openai-models": {
        "object": "list",
        "data": [{"id": i, "object": "model", "created": 0, "owned_by": "openai"}
                 for i in ("gpt-6-luna", "gpt-6-astra")]},
    "gemini-models": {"models": [{"name": "models/gemini-3.8-flash"},
                                 {"name": "models/gemini-3.8-flash-lite"}]},
}
OVERLOADED = {"type": "error", "error": {"code": 503, "type": "overloaded_error",
                                        "message": "túlterhelt", "status": "UNAVAILABLE"}}


class FakeAPI:
    def __init__(self):
        self.replies = {"anthropic": (200, anthropic_reply()), "openai": (200, openai_reply()),
                        "gemini": (200, gemini_reply()),
                        **{k: (200, v) for k, v in MODEL_LISTS.items()}}
        self.requests: list[tuple[str, str, dict]] = []
        self.busy: dict[str, list[int]] = {}    # útvonalanként ezek a hibakódok a rendes válasz előtt
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self._answer({"/v1/models": "anthropic-models", "/models": "openai-models",
                              "/v1beta/models": "gemini-models"}.get(self.path.split("?")[0]), {})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                path = self.path.split("?")[0]
                route = ("anthropic" if path == "/v1/messages" else "openai" if path == "/responses"
                         else "gemini" if path.endswith(":generateContent") else None)
                self._answer(route, body)

            def _answer(self, route, body):
                api.requests.append((route, self.path, body))
                status, payload = api.replies.get(route, (404, {"error": {"message": "nincs"}}))
                if api.busy.get(route):
                    status, payload = api.busy[route].pop(0), OVERLOADED
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"


@pytest.fixture
def api():
    fake = FakeAPI()
    yield fake
    fake.server.shutdown()


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    path.write_text("".join(f"{key}=teszt-{key.lower()}\n" for key in KEYS), encoding="utf-8")
    return path


@pytest.fixture
def ledger_path(tmp_path):
    return tmp_path / "llm_ledger.jsonl"


@pytest.fixture
def con():
    return connect(":memory:")


def clients_for(con, api, env, ledger_path, clock=lambda: NOON, retry=None):
    clients, _ = open_clients(con, env_file=env, ledger_path=ledger_path,
                              base_urls=dict.fromkeys(PROVIDERS, api.base), clock=clock,
                              retry=retry or Retry(sleep=lambda _: None))
    return clients


def last_entry(path):
    return json.loads(path.read_text(encoding="utf-8").splitlines()[-1])


def test_requests_use_native_structured_output(con, api, env, ledger_path):
    clients = clients_for(con, api, env, ledger_path)
    for name in PROVIDERS:
        clients[name].extract(PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")
    bodies = {route: body for route, _, body in api.requests}

    anthropic = bodies["anthropic"]
    assert anthropic["model"] == "claude-opus-5-5"
    assert anthropic["system"] == "UTASÍTÁS"
    assert anthropic["messages"] == [{"role": "user", "content": "OLDAL"}]
    fmt = anthropic["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["$defs"]["Mention"]["properties"]["type"]["enum"][0] == "brand"

    openai = bodies["openai"]
    assert (openai["model"], openai["instructions"], openai["input"], openai["store"]) == (
        "gpt-6-luna", "UTASÍTÁS", "OLDAL", False)
    assert openai["text"]["format"]["type"] == "json_schema"
    assert openai["text"]["format"]["strict"] is True
    assert openai["text"]["format"]["name"] == "PageEntities"

    gemini = bodies["gemini"]
    path = next(p for route, p, _ in api.requests if route == "gemini")
    assert path.startswith("/v1beta/models/gemini-3.8-flash:generateContent")
    config = gemini["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["required"] == ["entities"]
    assert config["thinkingConfig"] == {"thinking_level": "LOW"}
    assert gemini["systemInstruction"]["parts"] == [{"text": "UTASÍTÁS"}]


@pytest.mark.parametrize(("name", "model", "tokens_in", "tokens_out", "cost"), [
    # 1000 × 4 + 500 × 20 (a gondolkodás a kimenetben)
    ("anthropic", "claude-opus-5-5", 1000, 500, 0.014),
    # 1500 × 0,10 + 500 × 0,01 (gyorsítótárból) + 800 × 0,50 (a reasoning a kimenetben)
    ("openai", "gpt-6-luna", 2000, 800, 0.000555),
    # 2000 × 0,75 + 1000 × 0,075 (gyorsítótárból) + (400 + 600 gondolkodás) × 3,75
    ("gemini", "gemini-3.8-flash", 3000, 1000, 0.005325),
])
def test_extract_books_the_call(con, api, env, ledger_path, name, model, tokens_in, tokens_out,
                                cost):
    con.execute("INSERT INTO pages (url) VALUES ('https://materia-tm.com/')")
    result = clients_for(con, api, env, ledger_path)[name].extract(
        PageEntities, "UTASÍTÁS", "OLDAL", domain="entity", page_id=1)
    assert result.parsed == EXPECTED
    row = con.execute(
        "SELECT domain, page_id, model, tokens_in, tokens_out, cost_usd, purpose, latency_ms, "
        "called_at FROM llm_calls WHERE call_id = ?", [result.call_id]).fetchone()
    assert row[:5] == ("entity", 1, model, tokens_in, tokens_out)
    assert row[5] == pytest.approx(cost)
    assert row[6] == "extract" and row[7] >= 0 and row[8] == NOON
    (entry,) = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    assert (entry["provider"], entry["model"], entry["call_id"]) == (name, model, result.call_id)
    assert entry["cost_usd"] == pytest.approx(cost)
    raw_in = {"anthropic": "input_tokens", "openai": "input_tokens", "gemini": "prompt_token_count"}
    assert entry["usage"][raw_in[name]] == {"anthropic": 1000, "openai": 2000, "gemini": 3000}[name]


def test_anthropic_cache_tokens_are_input_at_their_own_price(con, api, env, ledger_path):
    api.replies["anthropic"] = (200, anthropic_reply(cache_read_input_tokens=2000,
                                                     cache_creation_input_tokens=400))
    result = clients_for(con, api, env, ledger_path)["anthropic"].extract(
        PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")
    tokens_in, cost = con.execute("SELECT tokens_in, cost_usd FROM llm_calls WHERE call_id = ?",
                                  [result.call_id]).fetchone()
    assert tokens_in == 3400
    assert cost == pytest.approx((1000 * 4 + 2000 * 0.20 + 400 * 5 + 500 * 20) / 1e6)


def test_call_id_links_page_entities(con, api, env, ledger_path):
    result = clients_for(con, api, env, ledger_path)["gemini"].extract(
        PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")
    con.execute("INSERT INTO pages (url) VALUES ('https://materia-tm.com/')")
    con.execute("INSERT INTO entities (name, lang, type) VALUES ('Materia', 'hu', 'brand')")
    con.execute(
        "INSERT INTO page_entities (page_id, entity_id, position, evidence, source, llm_call_id) "
        "VALUES (1, 1, 'body', 'a Materia', 'llm', ?)", [result.call_id])
    assert con.execute(
        "SELECT c.model FROM page_entities pe JOIN llm_calls c ON c.call_id = pe.llm_call_id"
    ).fetchall() == [("gemini-3.8-flash",)]


@pytest.mark.parametrize(("name", "reply", "stop"), [
    ("gemini", gemini_reply(text='{"entities": [{"name": "X", "type": "software", '
                                 '"evidence": "X"}]}'), "rendes"),
    ("anthropic", anthropic_reply(text='{"entities": [{"name": "Mat', stop="max_tokens"),
     "max_tokens"),
    ("openai", openai_reply(text='{"entities": [', status="incomplete",
                            incomplete={"reason": "max_output_tokens"}), "max_output_tokens"),
])
def test_schema_mismatch_is_still_booked(con, api, env, ledger_path, name, reply, stop):
    api.replies[name] = (200, reply)
    with pytest.raises(SchemaMismatch, match=f"leállás: {stop}") as info:
        clients_for(con, api, env, ledger_path)[name].extract(
            PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")
    assert con.execute("SELECT count(*), sum(cost_usd) > 0 FROM llm_calls WHERE call_id = ?",
                       [info.value.call_id]).fetchone() == (1, True)
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.parametrize(("name", "status"), [("anthropic", 529), ("openai", 429),
                                            ("gemini", 503), ("gemini", 408)])
def test_transient_error_is_retried_and_counted(con, api, env, ledger_path, name, status):
    api.busy[name] = [status]
    waits = []
    result = clients_for(con, api, env, ledger_path, retry=Retry(sleep=waits.append, jitter=0))[
        name].extract(PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")
    assert result.parsed == EXPECTED
    assert [route for route, _, _ in api.requests] == [name, name]
    assert waits == [1.0]
    assert con.execute("SELECT attempts FROM llm_calls WHERE call_id = ?",
                       [result.call_id]).fetchone() == (2,)
    assert last_entry(ledger_path)["attempts"] == 2


def test_first_try_is_one_attempt(con, api, env, ledger_path):
    result = clients_for(con, api, env, ledger_path)["openai"].extract(
        PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")
    assert con.execute("SELECT attempts FROM llm_calls WHERE call_id = ?",
                       [result.call_id]).fetchone() == (1,)


def test_backoff_is_exponential_with_jitter(con, api, env, ledger_path):
    api.busy["gemini"] = [503, 503, 503]
    waits = []
    retry = Retry(sleep=waits.append, rng=random.Random(20260926))
    result = clients_for(con, api, env, ledger_path, retry=retry)["gemini"].extract(
        PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")
    factors = [wait / base for wait, base in zip(waits, (1.0, 3.0, 9.0), strict=True)]
    assert all(0.75 <= factor <= 1.25 for factor in factors)
    assert len({round(factor, 6) for factor in factors}) == 3
    assert con.execute("SELECT attempts FROM llm_calls WHERE call_id = ?",
                       [result.call_id]).fetchone() == (4,)


def test_retries_give_up_after_four_attempts(con, api, env, ledger_path):
    api.busy["gemini"] = [503] * 4
    waits = []
    with pytest.raises(LLMError, match="gemini/gemini-3.8-flash: 4 kísérlet után: 503"):
        clients_for(con, api, env, ledger_path, retry=Retry(sleep=waits.append, jitter=0))[
            "gemini"].extract(PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")
    assert [route for route, _, _ in api.requests] == ["gemini"] * 4
    assert waits == [1.0, 3.0, 9.0]
    assert con.execute("SELECT count(*) FROM llm_calls").fetchone() == (0,)
    assert not ledger_path.exists()


def test_transient_classification():
    request = httpx.Request("POST", "http://127.0.0.1/")
    assert is_transient(anthropic.APIConnectionError(request=request))
    assert is_transient(openai.APIConnectionError(request=request))
    assert is_transient(httpx.ConnectError("elutasítva", request=request))
    assert is_transient(genai_errors.ServerError(503, {"error": {"message": "túlterhelt"}}))
    assert is_transient(genai_errors.ClientError(429, {"error": {"message": "kvóta"}}))
    assert not is_transient(genai_errors.ClientError(400, {"error": {"message": "rossz"}}))
    assert not is_transient(ValueError("nem API-hiba"))


def test_api_error_writes_no_row(con, api, env, ledger_path):
    api.replies["anthropic"] = (400, {"type": "error", "error": {"type": "invalid_request_error",
                                                                 "message": "rossz kérés"}})
    waits = []
    with pytest.raises(LLMError, match="anthropic/claude-opus-5-5: Error code: 400"):
        clients_for(con, api, env, ledger_path, retry=Retry(sleep=waits.append))[
            "anthropic"].extract(PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")
    assert [route for route, _, _ in api.requests] == ["anthropic"]
    assert waits == []
    assert con.execute("SELECT count(*) FROM llm_calls").fetchone() == (0,)
    assert not ledger_path.exists()


def spend(path, model, usd):
    ledger.append({"model": model, "cost_usd": usd}, path)


def test_budget_stops_above_threshold_without_calling(con, api, env, ledger_path):
    spend(ledger_path, "claude-opus-5-5", 4.0)
    client = clients_for(con, api, env, ledger_path)["anthropic"]
    client.extract(PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")   # 4,0: még nem „felett”
    assert client.spent_usd() == pytest.approx(4.014)
    with pytest.raises(BudgetExceeded, match="4.0140 USD a 4.0 USD leállási küszöb fölött"):
        client.extract(PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")
    assert [route for route, _, _ in api.requests] == ["anthropic"]


def test_budget_counts_every_model_of_the_provider(con, api, env, ledger_path):
    spend(ledger_path, "gpt-5.6-terra", 2.4)
    spend(ledger_path, "gpt-6-luna", 0.11)
    spend(ledger_path, "gemini-3.8-flash", 3.9)
    clients = clients_for(con, api, env, ledger_path)
    with pytest.raises(BudgetExceeded, match="openai: 2.5100 USD"):
        clients["openai"].extract(PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")
    clients["gemini"].extract(PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")
    assert [route for route, _, _ in api.requests] == ["gemini"]


def test_missing_price_stops_before_calling(con, api, env, ledger_path):
    client = clients_for(con, api, env, ledger_path)["openai"]
    client.config = replace(client.config, prices=())
    with pytest.raises(PriceError, match="gpt-6-luna: 0 ársor érvényes 2026-09-26-n"):
        client.extract(PageEntities, "UTASÍTÁS", "OLDAL", domain="entity")
    assert api.requests == []


def test_missing_key_skips_the_adapter(con, tmp_path, monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=teszt\nOPENAI_API_KEY=\n", encoding="utf-8")
    clients, skipped = open_clients(con, env_file=env)
    assert list(clients) == ["gemini"]
    assert skipped == {"anthropic": "nincs ANTHROPIC_API_KEY", "openai": "nincs OPENAI_API_KEY"}
    clients, skipped = open_clients(con, env_file=tmp_path / "nincs.env")
    assert clients == {} and set(skipped) == set(PROVIDERS)


def test_environment_key_wins_over_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=fajlbol\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "kornyezetbol")
    assert api_key("OPENAI_API_KEY", env) == "kornyezetbol"
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert api_key("OPENAI_API_KEY", env) == "fajlbol"


def test_check_models_against_provider_lists(api, env, tmp_path, monkeypatch):
    checks = {c.model: c for c in check_models(env_file=env,
                                               base_urls=dict.fromkeys(PROVIDERS, api.base))}
    assert {m: c.found for m, c in checks.items()} == {
        "claude-opus-5-5": True, "gpt-6-luna": True, "gpt-5.6-terra": False,
        "gemini-3.8-flash": True}
    assert checks["gemini-3.8-flash"].note == "2 modell a listán"
    api.replies["openai-models"] = (200, {"object": "list", "data": [
        {"id": "gpt-6-luna-2026-08-01", "object": "model", "created": 0, "owned_by": "openai"}]})
    checks = {c.model: c for c in check_models(env_file=env,
                                               base_urls=dict.fromkeys(PROVIDERS, api.base))}
    assert checks["gpt-6-luna"].found is False
    assert checks["gpt-6-luna"].similar == ("gpt-6-luna-2026-08-01",)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    no_keys = check_models(env_file=tmp_path / "nincs.env")
    assert {c.found for c in no_keys} == {None}
    assert no_keys[0].note == "nincs ANTHROPIC_API_KEY"


def test_status_prints_cumulative_usd_per_model(tmp_path, monkeypatch):
    monkeypatch.setattr(connect_module, "DATA_DIR", tmp_path)
    spend(ledger.default_path(), "claude-opus-5-5", 1.25)
    spend(ledger.default_path(), "claude-opus-5-5", 0.5)
    spend(ledger.default_path(), "gpt-5.6-terra", 2.6)
    spend(ledger.default_path(), "gpt-6-astra", 0.01)
    result = CliRunner().invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    lines = [line.strip() for line in result.output.splitlines()]
    assert "claude-opus-5-5: 1.7500 USD" in lines
    assert ("anthropic összesen 1.7500 USD; leállás 4.00 USD felett, keret 5.00 USD" in lines)
    assert "gpt-6-luna (aktív): 0.0000 USD" in lines
    assert "gpt-5.6-terra: 2.6000 USD" in lines
    assert ("openai összesen 2.6000 USD; leállás 2.50 USD felett, keret 3.00 USD; LEÁLLVA"
            in lines)
    assert "gemini-3.8-flash: 0.0000 USD" in lines
    assert "gpt-6-astra (nincs a konfigurációban): 0.0100 USD" in lines


def test_status_of_a_site_adds_its_own_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(connect_module, "DATA_DIR", tmp_path)
    site = connect(connect_module.db_path("materia-tm.com"))
    site.execute("INSERT INTO llm_calls (domain, model, cost_usd, purpose, called_at, attempts) "
                 "VALUES ('entity', 'gemini-3.8-flash', 0.002, 'extract', now(), 1), "
                 "('entity', 'gemini-3.8-flash', 0.003, 'extract', now(), 3), "
                 "('entity', 'gpt-6-luna', 0.001, 'extract', now(), 1)")
    site.close()
    result = CliRunner().invoke(app, ["status", "materia-tm.com"])
    assert result.exit_code == 0, result.output
    assert ("LLM ezen a site-on: gemini-3.8-flash 2 hívás 0.0050 USD (2 újrapróba), "
            "gpt-6-luna 1 hívás 0.0010 USD") in result.output.splitlines()[-1]


def test_cli_models_exit_code(api, env, monkeypatch):
    import aaa2.cli.main as cli
    monkeypatch.setattr(cli, "check_models", lambda: check_models(
        env_file=env, base_urls=dict.fromkeys(PROVIDERS, api.base)))
    result = CliRunner().invoke(app, ["models"])
    assert result.exit_code == 1
    assert "openai gpt-5.6-terra: NINCS a listán (2 modell a listán)" in result.output
    assert "anthropic claude-opus-5-5: a listán (2 modell a listán)" in result.output


def test_duckdb_rejects_unknown_call_id(con):
    con.execute("INSERT INTO pages (url) VALUES ('https://x.hu/')")
    con.execute("INSERT INTO entities (name, type) VALUES ('X', 'brand')")
    with pytest.raises(duckdb.ConstraintException, match="foreign key"):
        con.execute("INSERT INTO page_entities (page_id, entity_id, position, evidence, source, "
                    "llm_call_id) VALUES (1, 1, 'body', 'X', 'llm', 99)")
