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

## M1 — kész (2026-09-26)

Első függőleges szelet, ebben a sorrendben, mindegyik tesztekkel:

1. `aaa2/engine/normalize.py` — URL-normalizálás az M1 spec 7 szabálya szerint. Táblás unit tesztek (`tests/test_normalize.py`), ez az első PR.
2. `aaa2/engine/frontier.py` — seed + sitemap → `crawl_queue`; prioritás sitemap < nav < body < footer; `--resume`.
3. `aaa2/engine/render.py` — Playwright: perzisztens böngésző, N context, route-abort a `config/route_abort_domains.txt` alapján, consent a `config/consent_texts.txt`-ből, stabilizálás (a `legacy/renderer.py` HALT-26Y blokkjának logikája), sosem dob kivételt.
4. `aaa2/engine/parse.py` — renderelt DOM → `pages`, `links` (pozícióval), `headings`, `schema_blocks`. Main content: `legacy/crawler.py` readability-scoring.
5. `aaa2/engine/crawl.py` — összefűzés, oldalanként egy tranzakció, `crawl_runs` naplózás.
6. `aaa2/engine/site_profile.py` — célország (a v1 súlyozott szavazása, sitewide), piaci hatókör, nyelvek, nyers tech-jelek a crawl végén.
7. `tests/acceptance/` — elfogadás: Screaming Frog és `aaa crawl` egy ülésben, összevetés (`compare.py`), `--resume` élő teszt. A Screaming Frog-konfiguráció tudása: `tests/acceptance/screamingfrog.md`.

Kész, ha a három referencia-site (kk.coach, Materia Trattoria, ngx-bootstrap) végigmegy, a linkgráf egyezik a Screaming Frog JS-render baseline-nal (egy ülésben felvéve), és a `--resume` egy megszakított crawlt befejez. Stop-feltétel: kezdéstől két hét.

### Elfogadás (2026-09-26)

SF 24.3 és `aaa crawl` egy ülésben, site-onként egymás után. A szabály:

- magyarázatlan URL-eltérés 0; az összes eltérés kategóriánként jelentve, küszöb nélkül;
- a státuszkódok egyeznek;
- a belső linkek linkszinten, a renderelt DOM-on ±5%-on belül vannak; az SF saját száma csak referencia.

Mindhárom site elfogadva, magyarázatlan eltérés, státusz-eltérés és linkszintű eltérés nélkül:

- **kk.coach:** URL-eltérés 4,8% (2/42; a Cloudflare `/cdn-cgi/` linkje és egy valódi 404, a `/hu/` canonical-ja); 0,87 oldal/mp.
- **Materia:** 6,7% (1/15; `/cdn-cgi/`); 0,70 oldal/mp. A `--resume` 5 oldal után megszakítva, a megszakítás nélküli adatbázistól 0 eltéréssel fejezte be.
- **ngx-bootstrap:** 0,0% (0/88); 0,46 oldal/mp.
- **Tanulság:** az SF alapból mobilként renderel (Googlebot Smartphone, 411 × 731), és mobilon az ngx menüje a DOM-ba sem kerül; az SF-et az aaa-val azonos asztali ablakra (1920 × 1080) kell állítani.

### Referencia-site-ok

| Site | Seed | Include | Mit fed le |
| --- | --- | --- | --- |
| kk.coach | `https://kk.coach/` | — | Astro, kétnyelvű, Zaraz-consent |
| Materia Trattoria | `https://materia-tm.com/` | — | WP (Divi), WPML 4.7.4 nyelvi almappákkal (`/hu/`, `/it/`), consent, részleges JS-linkek |
| ngx-bootstrap | `https://valor-software.com/ngx-bootstrap/components` | `/ngx-bootstrap/` | tiszta CSR (Angular), valódi path-okkal |

A Materia seedje a gyökér, nem a `/hu/`: a gyökér a canonical, a `/hu/` a magyar ág. Hibái szándékosan maradnak, **ne javíts rajtuk, ezek a teszt**:
- nincs H1;
- nincs meta description;
- több generator meta van (Divi-alapú téma, WordPress, WPML).

Mérve 2026-09-25-én a seeden:
- **Nyersen:** 14 `<a href>`, ebből 11 belső előfordulás és 7 különböző belső URL. Közte két Cloudflare `/cdn-cgi/l/email-protection` link.
- **Renderelve:** 21 `<a href>`, 15 belső előfordulás, 6 különböző. A JS megduplázza a menüt, és az e-mail-védelmi linkeket `mailto:`-ra cseréli.
- **Nyelv:** `html[lang]="en-US"`, 4 hreflang.

Az ngx-bootstrap mérése (2026-09-25, `Renderer` + `Frontier`, 300-as felső korláttal):
- **A seeden** 1 belső `<a href>` van a nyers HTML-ben és 53 a renderelt DOM-ban. Hash-link (`#/`) egy sincs.
- **Az include-dal** a crawl 88 URL-nél magától leállt. Ebből 69 rendben van, 19 pedig 404: ezek a site saját `href="['']"` kötéshibájának célpontjai, valódi törött linkek.
- **Oldalanként:** a 68 sikeres nem-seed oldal mindegyikén a nyers belső linkek száma legfeljebb a renderelt ötöde. Összesen ez 368 nyers a 3723 renderelthez.
- **Ne a nyitóoldal legyen a seed:** a `/ngx-bootstrap/` előrenderelt (nyersen és renderelve is 5 link, 319 szó).

A MicroStore-boltok kiestek: a kirakatukban nincs `<a>`, a navigáció JS-kezelőkön megy. A victoria.microstore.app negatív esetként maradt: a render sikeres, 0 belső link, 59 szó. A `tests/test_fixture_sites.py` őrzi, visszajátszva a `tests/fixtures/` alatti felvételből.

## M2 — folyamatban

1. `aaa2/llm/` — modellfüggetlen kliens: `extract(schema, prompt, input) → parsed + call_id`. Három adapter az API-k natív strukturált kimenetével (Anthropic `claude-opus-5-5`, OpenAI `gpt-6-luna` / fallback `gpt-5.6-terra`, Gemini `gemini-3.8-flash` `low` thinkinggel). Modellek, dátumozott árak, keretek és leállási küszöbök: `aaa2/llm/models.toml`. Minden hívás `llm_calls`-sort és főkönyvsort (`data/llm_ledger.jsonl`, minden site, a nyers usage-mezőkkel) ír; a keret-őr a főkönyvből számol. Átmeneti hibánál (408, 429, 5xx, kapcsolat) legfeljebb 3 újrapróba ≈ 1, 3, 9 mp várakozással (±25% jitter); a kísérletek száma az `llm_calls.attempts`-ben. `aaa status` modellenkénti költség, `aaa models` a modell-listán. A 005-ös migráció: `entities.type` tíz értéke, `entities.lang`, `page_entities.llm_call_id`. Élő próba: `pytest -m live -s tests/test_llm_live.py` (kulcsok a `.env`-ben).

## Stack

Python 3.12, asyncio, Playwright (Chromium), selectolax, DuckDB, Typer, pydantic, zstandard, pytest; az LLM-hez anthropic, openai, google-genai, python-dotenv. `pip install -e ".[dev]"`, `playwright install chromium`, `pytest`, `aaa --help`.

## Amit ne csinálj

- Ne hozz létre riport-, memory-, RAG- vagy agent-réteget. Ezek a v1-et vitték el.
- Ne rakj cloud infrát alá (Cloud Run, Tasks, Firestore). Minden lokális, amíg nincs kinek kiszolgálni.
- Ne bővítsd a sémát "majd jól jön" alapon. Oszlop csak akkor, ha egy modul most használja.
