"""Párhuzamosság és időkorlátok: a tudásbázis-gyorsítótár közös kulcsa több szálról, a 403 utáni
render határideje, és a szál eredményére várás határideje."""
import asyncio
import threading
import time

import httpx
import pytest

import aaa2.engine.render as render_module
from aaa2.db.connect import connect
from aaa2.entities.extract import Worker, run_llm
from aaa2.llm.client import Retry
from aaa2.resolver.validate import _Api
from tests.test_entities_pipeline import NOON, Scripted, client_for, mention, reply
from tests.test_entities_rules import html, site
from tests.test_render import make_renderer  # noqa: F401  (fixture)

WORKERS = 6


def test_six_workers_storing_the_same_cache_keys_do_not_conflict(tmp_path):
    con = connect(tmp_path / "x.duckdb")
    barrier = threading.Barrier(WORKERS)
    errors, served = [], []

    def handler(request):
        served.append(str(request.url))
        time.sleep(0.002)                       # a szálak ugyanazon a kulcson találkoznak
        return httpx.Response(200, json={"search": [{"id": "Q1"}]})

    def worker():
        cursor = con.cursor()
        api = _Api(cursor, None, httpx.Client(transport=httpx.MockTransport(handler)),
                   Retry(sleep=lambda _: None), lambda: NOON, time.monotonic)
        try:
            barrier.wait(timeout=10)
            for number in range(40):
                _, body = api.get("kg", "https://kg.example/search", [("query", f"n{number}")])
                assert body == {"search": [{"id": "Q1"}]}
        except Exception as exc:  # noqa: BLE001  (a szál hibája a fő szálon látszik)
            errors.append(f"{type(exc).__name__}: {exc}"[:200])

    threads = [threading.Thread(target=worker) for _ in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert errors == []
    assert con.execute("SELECT count(*) FROM validation_cache").fetchone() == (40,)
    # egy kulcsot egy szál kér le, a többi a gyorsítótárból kapja
    assert len(served) == 40


async def test_the_retry_after_a_403_has_the_same_hard_limit(make_renderer, monkeypatch):  # noqa: F811
    # a határidő elég tág a rendes kísérletnek lassú gépen is (2 × 2 + 1 = 5 mp)
    monkeypatch.setattr(render_module, "HARD_TIMEOUT_MARGIN_S", 1.0)
    renderer, _ = await make_renderer({"https://kk.test/": 403}, render_timeout=2.0)
    original, attempts = renderer._render_page, []

    async def hang_on_the_fallback(context, url, retryable):
        attempts.append(url)
        if len(attempts) > 1:                   # a másik user-agenttel indított kísérlet beragad
            await asyncio.Event().wait()
        return await original(context, url, retryable)

    renderer._render_page = hang_on_the_fallback
    result = await asyncio.wait_for(renderer.render("https://kk.test/"), 40)
    assert result.error == "hard_timeout" and len(attempts) == 2


def test_waiting_for_a_worker_has_a_deadline(tmp_path):
    con = site({"/a/": html("A", "<p>Az Alfa Fesztivál idén is lesz</p>"),
                "/b/": html("B", "<p>A Béta Fesztivál idén is lesz</p>")})
    release = threading.Event()

    class Hanging(Scripted):
        def call(self, model, schema, prompt, input, **options):
            if "Alfa" in input:                 # az egyik oldal hívása nem tér vissza
                release.wait(timeout=30)
            return super().call(model, schema, prompt, input, **options)

    adapter = Hanging([reply(mention("b1", "Béta Fesztivál", "Béta Fesztivál", "event"))] * 2)
    client = client_for(con, adapter, tmp_path)
    began = time.monotonic()
    try:
        run = run_llm(con, client, workers=2, fork=lambda cursor: Worker(client.bind(cursor)),
                      clock=lambda: NOON, page_timeout=1.0)
    finally:
        release.set()
    assert time.monotonic() - began < 20
    statuses = dict(con.execute(
        "SELECT p.url, r.status FROM entity_run_pages r JOIN pages p USING (page_id) "
        "WHERE r.run_id = ?", [run.run_id]).fetchall())
    assert statuses == {"https://pelda.hu/a/": "failed", "https://pelda.hu/b/": "done"}
    reasons = con.execute(
        "SELECT r.reasons FROM entity_run_pages r JOIN pages p USING (page_id) WHERE "
        "p.url LIKE '%/a/'").fetchone()[0]
    assert "worker_timeout" in reasons


@pytest.mark.parametrize("field", ["timeout"])
def test_the_gemini_client_has_a_finite_timeout(field):
    from aaa2.llm.adapters import GEMINI_TIMEOUT_MS, GeminiAdapter
    from aaa2.llm.config import load_config

    adapter = GeminiAdapter(load_config().providers["gemini"], "kulcs-helyett")
    options = adapter.client._api_client._http_options
    assert getattr(options, field) == GEMINI_TIMEOUT_MS
