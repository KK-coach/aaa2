# legacy/ — v1 referencia

A v1 (`aaa-dev`) azon részei, amiket logikaként vagy kódként átveszünk. **Nem importáljuk innen.** Amikor egy modul odaér, a szükséges darabot átemeljük az `aaa2/` alá, ADK- és dict-lánc-függőségek nélkül, a v2 sémára írva.

| Fájl | v1 eredeti | Mit érdemes belőle | Hova |
| --- | --- | --- | --- |
| `crawler.py` | `crawler/crawler.py` | httpx+selectolax parser, main-content readability-scoring, SPA-detektálás, boilerplate-szűrés, `_detect_i18n`, brand-kontextus JSON-LD-ből | M1 `engine/parse.py`, `engine/site_profile.py` |
| `renderer.py` | `playwright_service/renderer.py` | defenzív render, dinamikus stabilizálás (`HALT-26Y` blokk), `_CONSENT_TEXTS`, `_WALL_PHRASES`, sosem dob kivételt. A screenshot/tiling rész **nem kell** | M1 `engine/render.py` |
| `preflight.py` | `reverse_engineering_agent/preflight.py` | elérhetőségi lépcső 200/403/404/5xx | M1 `engine/crawl.py` |
| `site_structure.py` | `page_analysis/site_structure.py` | hub-detektálás, taxonómia belső linkekből, chrome-slug szűrés | M3 `functions/graph.py` |
| `site_profile/heuristics.py`, `site_profile/constants.py` | `site_profile/` | célország-jelek API nélkül: ccTLD (két címkés is), hreflang-ország, path-prefix, telefon-előhívó, pénznem (ISO-kód csak szóhatárral, kis-nagybetű-érzékenyen), og:locale, JSON-LD cím; a táblák | M1 `engine/target_country.py` |
| `site_profile/profile.py` | `site_profile/profile.py` | `_location_scores` / `_fuse_location`: súlyozott szavazás, top-3 jelölt, konfidencia-sáv az egyező erős jelekből, ütközés. A Gemini-ágak **nem kellenek** | M1 `engine/target_country.py` |
| `audit_verdict_scope.py` | `report/audit_verdict.py` (kivonat) | csak `scope_signals()` a `_CITY_TOKENS`, `_TLD_COUNTRY`, `_INTL_RE` konstansokkal: piaci hatókör local / country_specific / international_global / mixed | M1 `engine/market_scope.py` |
| `language_detect.py` | `page_analysis/language_detect.py` | nyelvdetekció main contentből | M1 `engine/site_profile.py` |
| `kg_validate.py` | `page_analysis/kg_validate.py` | 5-kategóriás KG-osztályozó | M2, M4 |
| `ranking.py`, `competitor_filter.py` | `reverse_engineering_agent/` | registrable domain, platform-owned kizárás, serp_type-összehasonlíthatóság, top-3 | M5 |
| `re_tools_serp.py` | `reverse_engineering_agent/tools.py` | csak `_run_serp` és `_parse_serp_payload`; a többi ADK-tool, nem kell | M5 |
| `pagespeed_client.py` | `pagespeed/client.py` | PageSpeed / CrUX kliens | későbbi ráépítés |

Ami a v1-ből tudatosan **nem** jön át: `report/` (a `scope_signals()` kivonatán kívül), `site_profile/gemini_analyzer.py`, `memory/`, ADK agentek, Cloud Run / Cloud Tasks / Firestore infra, `validation_ui/`, OpenAI citation, AI Overview check.
