# legacy/ — v1 referencia

A v1 (`aaa-dev`) azon részei, amiket logikaként vagy kódként átveszünk. **Nem importáljuk innen.** Amikor egy modul odaér, a szükséges darabot átemeljük az `aaa2/` alá, ADK- és dict-lánc-függőségek nélkül, a v2 sémára írva.

| Fájl | v1 eredeti | Mit érdemes belőle | Hova |
| --- | --- | --- | --- |
| `crawler.py` | `crawler/crawler.py` | httpx+selectolax parser, main-content readability-scoring, SPA-detektálás, boilerplate-szűrés, `_detect_i18n`, brand-kontextus JSON-LD-ből | M1 `engine/parse.py`, `engine/site_profile.py` |
| `renderer.py` | `playwright_service/renderer.py` | defenzív render, dinamikus stabilizálás (`HALT-26Y` blokk), `_CONSENT_TEXTS`, `_WALL_PHRASES`, sosem dob kivételt. A screenshot/tiling rész **nem kell** | M1 `engine/render.py` |
| `preflight.py` | `reverse_engineering_agent/preflight.py` | elérhetőségi lépcső 200/403/404/5xx | M1 `engine/crawl.py` |
| `site_structure.py` | `page_analysis/site_structure.py` | hub-detektálás, taxonómia belső linkekből, chrome-slug szűrés | M3 `functions/graph.py` |
| `language_detect.py` | `page_analysis/language_detect.py` | nyelvdetekció main contentből | M1 `engine/site_profile.py` |
| `kg_validate.py` | `page_analysis/kg_validate.py` | 5-kategóriás KG-osztályozó | M2, M4 |
| `ranking.py`, `competitor_filter.py` | `reverse_engineering_agent/` | registrable domain, platform-owned kizárás, serp_type-összehasonlíthatóság, top-3 | M5 |
| `re_tools_serp.py` | `reverse_engineering_agent/tools.py` | csak `_run_serp` és `_parse_serp_payload`; a többi ADK-tool, nem kell | M5 |
| `pagespeed_client.py` | `pagespeed/client.py` | PageSpeed / CrUX kliens | későbbi ráépítés |

Ami a v1-ből tudatosan **nem** jön át: `report/`, `memory/`, ADK agentek, Cloud Run / Cloud Tasks / Firestore infra, `validation_ui/`, OpenAI citation, AI Overview check.
