"""Élő LLM-próba (`pytest -m live -s tests/test_llm_live.py`): fizetős API-kat hív.

- A konfigurált modell-azonosítók a szolgáltatók modell-listáján.
- Modellenként egy oldal: azonos séma, azonos bemenet; mindhárom válasz a sémára validálva.
- Gyorsítótár: egy legalább 1024 tokenes prompt kétszer; a második hívás nyers usage-mezői a
  főkönyv és az `llm_calls` soraival összevetve.

A hívások a data/llm_ledger.jsonl főkönyvbe kerülnek és beszámítanak a keretbe. Kulcs nélkül az
adott szolgáltató kimarad (skip).
"""
import json
from itertools import product

import pytest
from pydantic import BaseModel

from aaa2.db.connect import connect
from aaa2.llm import ledger
from aaa2.llm.client import check_models, open_clients
from aaa2.llm.config import Usage
from aaa2.llm.schemas import EntityType

pytestmark = pytest.mark.live


class Mention(BaseModel):
    name: str
    type: EntityType
    evidence: str


class PageEntities(BaseModel):
    entities: list[Mention]


PROMPT = (
    "Az alábbi weboldal-szövegből sorold fel a megnevezett entitásokat. Minden entitáshoz: a "
    "kanonikus név, a típus, és egy szó szerinti idézet a szövegből (evidence), amelyben "
    "szerepel. Csak olyat adj, ami a szövegben ténylegesen szerepel."
)
PAGE = """Materia Kávépörkölő – Budapest, Kazinczy utca 10.

A Materia 2016 óta pörköl specialty kávét a VII. kerületben. Kínálatunkban etióp Yirgacheffe
és kolumbiai Huila szemes kávé szerepel, valamint Chemex és AeroPress főzőeszközök.
Szombatonként barista kurzust tartunk; a jegyek a Shopify-alapú webshopban vehetők meg.
A pörkölést Varga Anna vezeti, aki a 2023-as Budapest Coffee Festen is bemutatót tartott.
"""

ORIGINS = [("Etiópia", "Yirgacheffe"), ("Etiópia", "Guji"), ("Kenya", "Nyeri"),
           ("Kolumbia", "Huila"), ("Brazília", "Cerrado Mineiro"), ("Guatemala", "Antigua"),
           ("Costa Rica", "Tarrazú"), ("Ruanda", "Nyamasheke"), ("Burundi", "Kayanza"),
           ("Panama", "Boquete"), ("Peru", "Cajamarca"), ("Honduras", "Santa Bárbara")]
PROCESSES = [("mosott", "citrus, jázmin és fekete tea", "V60"),
             ("natúr", "érett eper, kakaó és vörösbor", "AeroPress"),
             ("mézes", "barackos, karamellás és mogyorós", "Chemex")]
LONG_PAGE = PAGE + "\nKávéink részletesen:\n" + "\n".join(
    f"{region} ({country}), {process} feldolgozás: {notes} jegyek; ajánlott főzés: {method}, "
    f"18 gramm kávé 300 gramm vízhez; 250 grammos kiszerelés {3900 + 150 * i} forint."
    for i, ((country, region), (process, notes, method))
    in enumerate(product(ORIGINS, PROCESSES))
)


class PageSummary(BaseModel):
    title: str
    language: str
    topic: str


SUMMARY_PROMPT = "Add meg az alábbi weboldal címét, nyelvét és fő témáját, egy-egy rövid sorban."


@pytest.fixture(scope="module")
def session():
    con = connect(":memory:")
    clients, skipped = open_clients(con)
    return con, clients, skipped


def test_configured_models_are_on_the_provider_lists():
    """Kulcs nélküli szolgáltató kimarad; kulccsal a lista-hiba is bukás, nem kihagyás."""
    checks = [c for c in check_models() if not c.note.startswith("nincs ")]
    if not checks:
        pytest.skip("egyik szolgáltatóhoz sincs kulcs")
    for check in checks:
        state = {True: "a listán", False: "NINCS", None: "nem ellenőrizhető"}[check.found]
        print(f"{check.provider} {check.model}: {state} ({check.note})"
              f"{'; hasonló: ' + ', '.join(check.similar) if check.similar else ''}")
    assert [c.model for c in checks if not c.found] == []


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_one_page_per_model(session, provider):
    con, clients, skipped = session
    if provider not in clients:
        pytest.skip(skipped[provider])
    client = clients[provider]
    result = client.extract(PageEntities, PROMPT, PAGE, domain="smoke")
    assert isinstance(result.parsed, PageEntities) and result.parsed.entities
    assert PageEntities.model_validate_json(result.parsed.model_dump_json()) == result.parsed
    model, tokens_in, tokens_out, cost, latency = con.execute(
        "SELECT model, tokens_in, tokens_out, cost_usd, latency_ms FROM llm_calls "
        "WHERE call_id = ?", [result.call_id]).fetchone()
    assert model == client.model and tokens_in > 0 and tokens_out > 0 and cost > 0
    entities = result.parsed.entities
    verbatim = sum(mention.evidence in PAGE for mention in entities)
    print(f"\n{model}: {len(entities)} entitás, {verbatim} szó szerinti bizonyíték; "
          f"{tokens_in} be / {tokens_out} ki token, {cost:.6f} USD, {latency} ms")
    for mention in entities:
        print(f"  {mention.type:8} {mention.name}  «{mention.evidence}»")


def usage_from_raw(provider: str, raw: dict) -> Usage:
    """A szolgáltató nyers usage-mezőiből, a review-ban rögzített értelmezéssel."""
    if provider == "anthropic":
        return Usage(input=raw["input_tokens"], output=raw["output_tokens"],
                     cached_input=raw.get("cache_read_input_tokens") or 0,
                     cache_write=raw.get("cache_creation_input_tokens") or 0)
    if provider == "openai":
        details = raw.get("input_tokens_details") or {}
        cached = details.get("cached_tokens") or 0
        written = details.get("cache_write_tokens") or 0
        return Usage(input=raw["input_tokens"] - cached - written, output=raw["output_tokens"],
                     cached_input=cached, cache_write=written)
    cached = raw.get("cached_content_token_count") or 0
    return Usage(input=raw["prompt_token_count"] - cached,
                 output=(raw.get("candidates_token_count") or 0)
                 + (raw.get("thoughts_token_count") or 0),
                 cached_input=cached)


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_second_call_usage_matches_ledger(session, provider):
    con, clients, skipped = session
    if provider not in clients:
        pytest.skip(skipped[provider])
    client = clients[provider]
    calls = [client.extract(PageSummary, SUMMARY_PROMPT, LONG_PAGE, domain="smoke-cache")
             for _ in range(2)]
    lines = ledger.default_path().read_text(encoding="utf-8").splitlines()
    entries = [next(e for e in map(json.loads, reversed(lines))
                    if e["call_id"] == call.call_id and e["model"] == client.model)
               for call in calls]
    print()
    for n, entry in enumerate(entries, 1):
        print(f"{client.model} {n}. hívás usage: {json.dumps(entry['usage'], ensure_ascii=False)}")
    second = entries[1]
    usage = usage_from_raw(provider, second["usage"])
    tokens_in, tokens_out, cost, attempts = con.execute(
        "SELECT tokens_in, tokens_out, cost_usd, attempts FROM llm_calls WHERE call_id = ?",
        [calls[1].call_id]).fetchone()
    expected = client.config.price(client.model, client.clock().date()).cost_usd(usage)
    print(f"{client.model} 2. hívás: {usage.tokens_in} be ({usage.cached_input} gyorsítótárból, "
          f"{usage.cache_write} írás), {usage.output} ki; főkönyv {second['tokens_in']} / "
          f"{second['tokens_out']} / {second['cost_usd']:.6f} USD; llm_calls {tokens_in} / "
          f"{tokens_out} / {cost:.6f} USD; számolt {expected:.6f} USD; {attempts} kísérlet")
    assert usage.tokens_in >= 1024
    assert (tokens_in, tokens_out) == (second["tokens_in"], second["tokens_out"]) == (
        usage.tokens_in, usage.output)
    assert cost == pytest.approx(second["cost_usd"]) == pytest.approx(expected)
