# CLAUDE.md — aaa2

Sitewide SEO/GEO elemzőmotor. A mag: oldalak × entitások × belső linkek gráfja, DuckDB-ben, site-onként egy fájl. Lokálban fut, CLI-ből. A teljes terv a Claude "AAA v2" projekt projektindítójában és az M1 spec tabban van; ez a fájl a kódolás közben betartandó szabályokat rögzíti.

## Amit mindig tarts be

- **Alulról felfelé, modulonként.** M1 (crawl) → M2 (entity, buta verzió) → M3 (gráf) → M4 (resolver) → M5 (konkurencia). Nem kezdünk bele a következőbe, amíg az előző kész-feltétele nem teljesül. Ne írj M2+ kódot M1 közben "ha már itt vagyok" alapon.
- **A DuckDB-séma a szerződés.** `aaa2/db/001_init.sql`. Modulok csak a sémán keresztül beszélnek. Új oszlop mehet új migrációban (`002_*.sql`), meglévő oszlop jelentése nem változik. Az `engine` ír, a `functions` csak olvas, a `resolver` címkéz.
- **JS-render mindenre.** Nincs HTTP-only útvonal, nincs "csak ha kell" render. A sebesség mért szám, nem feltétel.
- **Az LLM sosem talál ki számot**, és minden entitáshoz szó szerinti bizonyíték kell (`page_entities.evidence NOT NULL`). Bizonyíték nélkül nincs sor.
- **Minden LLM-hívás az `aaa2/llm/` csomagon megy át** és sort ír az `llm_calls` táblába. Modellfüggetlen interfész; a modell (Gemini / GPT / Claude) nincs eldöntve, M2-ben párhuzamosan tesztelve.
- **Nincs ticket-hivatkozás a kommentekben.** A kód nem changelog. Docstring azt mondja, mit csinál, nem azt, mikor és miért döntöttünk így.
- **`legacy/` nem importálható.** Referencia. Darabokat átemelünk, ADK- és dict-lánc-függőség nélkül, a v2 sémára írva.
- **Tesztek a mérésre, nem a riportra.** Élő site-ot csak az elfogadási teszt hív; a regressziós tesztek a `tests/fixtures/<domain>/` alól szolgálják ki a rögzített válaszokat Playwright `route`-tal.

## M1 — a következő lépés

Első függőleges szelet, ebben a sorrendben, mindegyik tesztekkel:

1. `aaa2/engine/normalize.py` — URL-normalizálás az M1 spec 7 szabálya szerint. Táblás unit tesztek (`tests/test_normalize.py`), ez az első PR.
2. `aaa2/engine/frontier.py` — seed + sitemap → `crawl_queue`; prioritás sitemap < nav < body < footer; `--resume`.
3. `aaa2/engine/render.py` — Playwright: perzisztens böngésző, N context, route-abort a `config/route_abort_domains.txt` alapján, consent a `config/consent_texts.txt`-ből, stabilizálás (a `legacy/renderer.py` HALT-26Y blokkjának logikája), sosem dob kivételt.
4. `aaa2/engine/parse.py` — renderelt DOM → `pages`, `links` (pozícióval), `headings`, `schema_blocks`. Main content: `legacy/crawler.py` readability-scoring.
5. `aaa2/engine/crawl.py` — összefűzés, oldalanként egy tranzakció, `crawl_runs` naplózás.
6. `aaa2/engine/site_profile.py` — célország, nyelvek, nyers tech-jelek a crawl végén.

Kész, ha a három referencia-site (kk.coach, Materia Trattoria, vestino.hu vagy másik microstore.app-bolt) végigmegy, a linkgráf egyezik a Screaming Frog JS-render baseline-nal (egy ülésben felvéve), és a `--resume` egy megszakított crawlt befejez. Stop-feltétel: kezdéstől két hét.

## Stack

Python 3.12, asyncio, Playwright (Chromium), selectolax, DuckDB, Typer, pydantic, zstandard, pytest. `pip install -e ".[dev]"`, `playwright install chromium`, `pytest`, `aaa --help`.

## Amit ne csinálj

- Ne hozz létre riport-, memory-, RAG- vagy agent-réteget. Ezek a v1-et vitték el.
- Ne rakj cloud infrát alá (Cloud Run, Tasks, Firestore). Minden lokális, amíg nincs kinek kiszolgálni.
- Ne bővítsd a sémát "majd jól jön" alapon. Oszlop csak akkor, ha egy modul most használja.
