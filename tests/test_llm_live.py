"""Élő LLM-próba (`pytest -m live -s tests/test_llm_live.py`): fizetős API-kat hív.

- A konfigurált modell-azonosítók a szolgáltatók modell-listáján.
- Modellenként egy oldal: azonos séma, azonos bemenet; mindhárom válasz a sémára validálva.

A hívások a data/llm_ledger.jsonl főkönyvbe kerülnek és beszámítanak a keretbe. Kulcs nélkül az
adott szolgáltató kimarad (skip).
"""
import pytest
from pydantic import BaseModel

from aaa2.db.connect import connect
from aaa2.llm.client import check_models, open_clients
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
