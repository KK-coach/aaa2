"""A párhuzamos modellteszt (tests/acceptance/llm_compare.py): a minta, a mérés és a két
Markdown-kimenet, hálózat nélkül, három szkriptelt modellel. Az 1. kör állapotát (a nyers
kimenet és a `page_entities_v1` sorai) a `legacy_round` állítja elő a v1 prompttal."""
import json
from datetime import UTC, datetime

import pytest

import tests.acceptance.llm_compare as compare
from aaa2.entities.llm import PROMPT, check_evidence, normalize_text, page_input
from aaa2.entities.rules import alias_key, run_rules
from aaa2.llm.adapters import Reply, genai_errors
from aaa2.llm.client import LLMClient, LLMError, Retry, SchemaMismatch
from aaa2.llm.config import Usage, load_config
from aaa2.llm.schemas import PageExtraction
from tests.test_entities_rules import html, site

NOON = datetime(2026, 9, 26, 12, 0, tzinfo=UTC).replace(tzinfo=None)
CONFIG = load_config()
KINDS = {"home", "service", "product", "article", "other"}


class Scripted:
    """Oldalanként egy előre adott válasz (dict → JSON, vagy kivétel) a szolgáltató modelljével."""

    def __init__(self, provider, replies):
        self.config = CONFIG.providers[provider]
        self.replies = list(replies)

    def call(self, model, schema, prompt, input):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Reply(text=json.dumps(reply, ensure_ascii=False),
                     usage=Usage(input=1000, output=200))


def legacy_round(con, provider, replies, tmp_path):
    """Az 1. kör egy modellje: oldalanként egy v1 hívás; a nyers kimenet rekordként, az
    elfogadott (nem fabrikált) sorok a `page_entities_v1`-be, kulcs szerint közös entitással."""
    client = LLMClient(con, Scripted(provider, replies), CONFIG, tmp_path / "ledger.jsonl",
                       lambda: NOON, Retry(sleep=lambda _: None))
    records = []
    for page_id, title, main_content in con.execute(
            "SELECT page_id, title, main_content FROM pages ORDER BY page_id").fetchall():
        record = {"provider": provider, "model": client.model, "page_id": page_id,
                  "call_id": None, "entities": None, "error": None}
        records.append(record)
        try:
            result = client.extract(PageExtraction, PROMPT, page_input(title, [], main_content)[0],
                                    domain="entity", page_id=page_id)
        except SchemaMismatch as exc:
            record.update(call_id=exc.call_id, error=f"schema_mismatch: {exc}"[:500])
            continue
        except LLMError as exc:
            record.update(error=f"call_error: {exc}"[:500])
            continue
        record.update(call_id=result.call_id,
                      entities=[e.model_dump() for e in result.parsed.entities])
        sources = [normalize_text(main_content), normalize_text(title)]
        fabricated = 0
        for entity in result.parsed.entities:
            if check_evidence(entity, sources):
                fabricated += 1
                continue
            found = [i for i, name in con.execute("SELECT entity_id, name FROM entities"
                                                  ).fetchall()
                     if alias_key(name) == alias_key(entity.name)]
            entity_id = found[0] if found else con.execute(
                "INSERT INTO entities (name, type, source) VALUES (?, ?, 'llm') "
                "RETURNING entity_id", [entity.name, entity.type]).fetchone()[0]
            con.execute("INSERT INTO page_entities_v1 (page_id, entity_id, position, evidence, "
                        "source, llm_call_id) VALUES (?, ?, 'body', ?, 'llm', ?)",
                        [page_id, entity_id, entity.evidence, result.call_id])
        con.execute("UPDATE llm_calls SET fabricated_count = ? WHERE call_id = ?",
                    [fabricated, result.call_id])
    return records


def entity(name, kind, evidence):
    return {"name": name, "type": kind, "description": "leírás", "evidence": evidence,
            "context": evidence}


PAGES = {
    "/a/": html("Kávé", "<p>A Példa Kávézó Budapest belvárosában működik 2016 óta.</p>"
                        "<p>A pörkölést Kiss Anna vezeti a csapatban.</p>"),
    "/b/": html("Fesztivál", "<p>A Budapest Coffee Fest idén is lesz a Bálnában.</p>"),
}
CAFE = entity("Példa Kávézó", "org", "A Példa Kávézó Budapest belvárosában")
ANNA = entity("Kiss Anna", "person", "A pörkölést Kiss Anna vezeti")
FEST = entity("Budapest Coffee Fest", "event", "A Budapest Coffee Fest idén")
FAKE = entity("Kitalált Kft.", "org", "a Kitalált Kft. szervezi a fesztivált")


def three_models(tmp_path):
    """Három modell ugyanazon a két oldalon: a Gemini fabrikál egy sort, az OpenAI első hívása
    503 után sikerül, az Anthropic a második oldalon séma-eltérést ad."""
    con = site(PAGES)
    run_rules(con)
    replies = {
        "gemini": [{"primary_entity": "x", "entities": [CAFE, ANNA]}, {"primary_entity": "x", "entities": [FEST, FAKE]}],
        "openai": [genai_errors.ServerError(503, {"error": {"message": "túlterhelt"}}),
                   {"primary_entity": "x", "entities": [CAFE]}, {"primary_entity": "x", "entities": [FEST]}],
        "anthropic": [{"primary_entity": "x", "entities": [CAFE, ANNA]}, {"primary_entity": "x", "entities": "nem lista"}],
    }
    records = {provider: legacy_round(con, provider, answers, tmp_path)
               for provider, answers in replies.items()}
    con.execute("UPDATE entities SET kg_status = 'high' WHERE name = 'Budapest Coffee Fest'")
    return con, records


def test_sample_is_ten_mixed_pages_per_set():
    urls = [url for pages in compare.SAMPLE.values() for url, _ in pages]
    assert [len(pages) for pages in compare.SAMPLE.values()] == [10, 10, 10]
    assert len(set(urls)) == 30
    assert {kind for pages in compare.SAMPLE.values() for _, kind in pages} == KINDS
    for pages in compare.SAMPLE.values():
        assert len({kind for _, kind in pages}) >= 3
    assert len(compare.SPOTCHECK) == 5
    assert all(url in dict(compare.SAMPLE[name]) for name, url in compare.SPOTCHECK)


@pytest.mark.parametrize("name", list(compare.SAMPLE))
def test_sample_pages_are_llm_pages_of_the_recorded_sets(name, reference_crawl):
    con = reference_crawl(name)
    if con is None:
        pytest.skip(f"nincs felvétel: {name}")
    ids = compare.sample_page_ids(con, name)
    eligible = {page_id for (page_id,) in con.execute(
        "SELECT page_id FROM pages WHERE status BETWEEN 200 AND 299 AND error IS NULL AND "
        "rendered_html IS NOT NULL AND trim(coalesce(main_content, '')) <> ''").fetchall()}
    assert set(ids.values()) <= eligible


def test_missing_sample_url_stops(monkeypatch):
    con = site(PAGES)
    monkeypatch.setitem(compare.SAMPLE, "x", [("https://nincs.hu/", "home")])
    with pytest.raises(SystemExit, match="nincs.hu"):
        compare.sample_page_ids(con, "x")


def test_measure_per_model(tmp_path):
    con, records = three_models(tmp_path)
    stats = {p: compare.ModelStats(p) for p in compare.PROVIDERS}
    for provider in compare.PROVIDERS:
        compare.collect(con, "set", records[provider], stats[provider])
    gemini, openai, anthropic = (stats[p] for p in compare.PROVIDERS)
    assert (gemini.model, openai.model, anthropic.model) == (
        "gemini-3.8-flash", "gpt-6-luna", "claude-opus-5-5")
    assert (gemini.pages, len(gemini.answered), gemini.counts(), gemini.fabricated_logged) == (
        2, 2, (4, 1, 0), 1)
    assert gemini.fabrication_rate() == 0.25
    assert (anthropic.pages, len(anthropic.answered), anthropic.failures) == (
        2, 1, {"schema_mismatch": 1})
    page_a = anthropic.answered
    assert (gemini.counts(page_a), gemini.fabrication_rate(page_a)) == ((2, 0, 0), 0.0)
    assert openai.retried == 1
    assert list(openai.retry_errors) == ["503: 503 None. {'error': {'message': 'túlterhelt'}}"]
    assert (len(gemini.pairs), len(openai.pairs), len(anthropic.pairs)) == (3, 2, 2)
    assert compare.jaccard(gemini.pairs, openai.pairs) == pytest.approx(2 / 3)
    assert compare.jaccard(openai.pairs, anthropic.pairs) == pytest.approx(1 / 3)
    assert (len(gemini.recognized_on()), gemini.kg_rate()) == (1, pytest.approx(1 / 3))
    assert (len(gemini.pairs_on(page_a)), gemini.kg_rate(page_a)) == (2, 0.0)
    price = CONFIG.price("gemini-3.8-flash", NOON.date())
    assert gemini.cost_usd == pytest.approx(2 * price.cost_usd(Usage(input=1000, output=200)))
    assert (gemini.tokens_in, gemini.tokens_out, len(gemini.latencies)) == (2000, 400, 2)
    assert anthropic.cost_usd > 0 and len(anthropic.latencies) == 2      # a séma-eltérés is fizetős


def test_report_markdown(tmp_path):
    con, records = three_models(tmp_path)
    stats = {p: compare.ModelStats(p) for p in compare.PROVIDERS}
    for provider in compare.PROVIDERS:
        compare.collect(con, "set", records[provider], stats[provider])
    text = compare.report_markdown(stats)
    own, common = text.split("## Összevetés a közös oldalakon (1 oldal: mindegyik modell adott "
                             "kimenetet)")
    assert "| oldal (hívás / kimenet) | 2 / 2 | 2 / 2 | 2 / 1 |" in own
    assert "| **fabrikáció** | **25.0%** (1) | **0.0%** (0) | **0.0%** (0) |" in own
    assert "| KG high + medium | 33.3% (1) | 50.0% (1) | 0.0% (0) |" in own
    assert "| sikertelen oldal | 0 | 0 | schema_mismatch 1 |" in own
    assert "| token be / ki hívásonként | 1000 / 200 | 1000 / 200 | 1000 / 200 |" in own
    call = {model: CONFIG.price(model, NOON.date()).cost_usd(Usage(input=1000, output=200))
            for model in ("gemini-3.8-flash", "gpt-6-luna", "claude-opus-5-5")}
    # az Anthropic két fizetett hívása (a séma-eltérés is) egy megválaszolt oldalra jut
    assert (f"| USD / megválaszolt oldal | {call['gemini-3.8-flash']:.4f} | "
            f"{call['gpt-6-luna']:.4f} | {2 * call['claude-opus-5-5']:.4f} |") in own
    assert "| **fabrikáció** | **0.0%** (0) | **0.0%** (0) | **0.0%** (0) |" in common
    assert "| KG high + medium | 0.0% (0) | 0.0% (0) | 0.0% (0) |" in common
    assert "- Gemini – OpenAI, 2 oldal: 0.667 (közös 2, együtt 3)" in common
    assert "- Gemini – Anthropic, 1 oldal: 1.000 (közös 2, együtt 2)" in common
    assert "- OpenAI – Anthropic, 1 oldal: 0.500 (közös 1, együtt 2)" in common
    assert "- mindhárom, 1 oldal: közös 1, együtt 2 (50.0%)" in common
    assert "A költség mért adat, nem döntési feltétel." in text
    assert "- OpenAI: 503: 503 None." in text
    assert text.count("  - home: ") == 6


def test_spotcheck_puts_the_three_outputs_side_by_side(tmp_path, monkeypatch):
    con, records = three_models(tmp_path)
    url = con.execute("SELECT url FROM pages WHERE url LIKE '%/b/'").fetchone()[0]
    con.execute(f"ATTACH '{(tmp_path / 'set.duckdb').as_posix()}' AS disk")
    for table in ("pages", "headings"):                # a szúrópróba csak ezeket olvassa
        con.execute(f"CREATE TABLE disk.{table} AS SELECT * FROM memory.{table}")
    con.execute("DETACH disk")
    for provider, recs in records.items():
        compare.write_jsonl(tmp_path / f"set.{provider}.jsonl", recs)
    monkeypatch.setattr(compare, "SPOTCHECK", [("set", url)])
    text = compare.spotcheck_markdown(tmp_path)
    assert f"## {url}" in text
    assert "- Anthropic: hiba — schema_mismatch: " in text
    assert "| név | Gemini | OpenAI | evidence |" in text
    assert "| Budapest Coffee Fest | event | event | A Budapest Coffee Fest idén |" in text
    assert "| Kitalált Kft. | ~~org~~ | — | a Kitalált Kft. szervezi a fesztivált |" in text
    assert "A Budapest Coffee Fest idén is lesz a Bálnában." in text
    assert text.count("Hiányzik: ") == 1


def test_gold_draft_lists_the_page_and_the_union(tmp_path, monkeypatch):
    import tests.acceptance.gold_draft as gold

    con, records = three_models(tmp_path)
    url = con.execute("SELECT url FROM pages WHERE url LIKE '%/b/'").fetchone()[0]
    con.execute(f"ATTACH '{(tmp_path / 'set.duckdb').as_posix()}' AS disk")
    for table in ("pages", "headings"):
        con.execute(f"CREATE TABLE disk.{table} AS SELECT * FROM memory.{table}")
    con.execute("DETACH disk")
    for provider, recs in records.items():
        compare.write_jsonl(tmp_path / f"set.{provider}.jsonl", recs)
    monkeypatch.setattr(gold, "GOLD_PAGES", [("b", "set", url)])
    text = gold.draft_markdown(tmp_path)
    assert f"## b: {url}" in text
    assert "Modellek: Gemini 2 sor, OpenAI 1 sor, Anthropic nincs kimenet." in text
    assert "| Budapest Coffee Fest | event | event | A Budapest Coffee Fest idén |" in text
    assert "| Kitalált Kft. | ~~org~~ | — | ~~a Kitalált Kft. szervezi a fesztivált~~ |" in text
    assert "A Budapest Coffee Fest idén is lesz a Bálnában." in text
