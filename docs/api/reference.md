# aaa2 API-referencia

Az `aaa2.api` nyilvános nevei. A fájl a docstringekből készül (`python -m aaa2.api.reference docs/api`), kézzel ne szerkeszd.

Az aaa2 API-ja: a motor minden képessége itt érhető el Pythonból; a parancssor (és később a
riport, a HTTP API) csak ezt használja.

Egy site megnyitása: `open_site(domain, db=None)` → `Site`. A lépések site-onként hívhatók
(`crawl`, `extract`, `resolve`, `build_graph`, `find`), a lekérdezések szerződést adnak vissza
(`aaa2/contracts`: `pages`, `entities`, `edges`, `main_entities`, `weights`, `findings`, …);
a riport bemenete a `views` (verziózott JSON: `views_json`, `export_views_json`).

    from aaa2 import api

    site = api.open_site("kk.coach")
    api.extract(site, llm=False)
    api.resolve(site)
    api.build_graph(site)
    api.find(site)
    for finding in api.findings(site):
        print(finding.severity, finding.summary)

A referencia a docstringekből készül: `python -m aaa2.api.reference docs/api`.

## Site

### `open_site`

```python
open_site(domain: 'str', db: 'Path | None' = None) -> 'Site'
```

A site megnyitása: `db` az adatbázis útvonala, alapból `data/<domain>.duckdb`. Ha a fájl
nincs meg: `SiteNotFound`.

### `Site`

Egy megnyitott site: az adatbázis-kapcsolat (`con`), az adatbázis útvonala (`path`) és a
kimeneti fájlok neve (`name`: az adatbázisfájl neve, ha útvonallal nyílt, különben a domain).

Mezők:

- `con`: `duckdb.DuckDBPyConnection`
- `path`: `Path`
- `name`: `str`

### `domain_of`

```python
domain_of(value: 'str') -> 'str'
```

A registrable domain egy URL-ből; a domainként adott érték kisbetűsítve.

### `ApiError`

Az API hívója által kezelhető hiba; `code`: a parancssor kilépési kódja ehhez a hibához.

### `SiteNotFound`

A site-nak nincs adatbázisa a megadott helyen.

### `GraphMissing`

A megállapításokhoz előbb a gráf kell (`build_graph`).

## Lépések

### `crawl`

```python
crawl(url: 'str', *, sitemap: 'str | None' = None, max_pages: 'int' = 5000, concurrency: 'int | None' = None, render_timeout: 'float | None' = None, respect_robots: 'bool' = True, resume: 'bool' = False, include: 'str | None' = None, exclude: 'str | None' = None, progress: 'Callable[[str, int | None, str | None], None] | None' = None, on_options: 'Callable[[CrawlOptions, bool], None] | None' = None) -> 'CrawlResult'
```

Egy site sitewide crawlja Playwright-renderrel a `data/<domain>.duckdb`-be. Az include,
az exclude, a párhuzamosság és a render-időkorlát alapja a site-fájl `[crawl]` része
(`aaa2/core/sites/<domain>.toml`), ha van; a megadott paraméter felülírja. `progress`:
oldalanként (URL, státusz, hiba); `on_options`: a crawl indulása előtt a tényleges beállítás.
Hibás site-fájl vagy seed: `ApiError`.

### `extract`

```python
extract(site: 'Site', *, llm: 'bool | None' = None, knowledge: 'bool | None' = None, extraction_model: 'str | None' = None, verify_model: 'str | None' = None, limit: 'int | None' = None, resume: 'bool' = False, estimate: 'bool' = False, max_usd: 'float | None' = None, workers: 'int | None' = None, fresh: 'bool' = False, notify: 'Notify | None' = None) -> 'ExtractResult'
```

Kinyerés a `pipeline.toml` lépéseivel: a hiányzó blokkok, a determinisztikus szabálykör,
és ha be van kapcsolva (`llm`, alapból a `pipeline.toml` `extraction`), oldalanként az
LLM-kinyerés a saját ajánlatok ellenőrzésével és a fogalmak bizonyítékaival. Az LLM-kör
előtt költségbecslés megy a `notify`-nak; `estimate`: csak a becslés készül el. A becslés a
költséghatár (`max_usd`, alapból a `pipeline.toml` `max_usd`) fölött: `ApiError` 2-es kóddal.
`knowledge`: a tudásbázis-egyezés a fogalmak bizonyítékaihoz (alapból a `pipeline.toml`
`knowledge`).

### `resolve`

```python
resolve(site: 'Site', *, knowledge: 'bool | None' = None) -> 'ResolveResult'
```

Feloldás LLM nélkül: a site-szintű entitások (oldalhoz kötés, csomagok, lépések,
összevonás, demó- és sablonjelölés), utána a tudásbázis-kapcsolás (`knowledge`, alapból a
`pipeline.toml` `knowledge`).

### `rebuild_entities`

```python
rebuild_entities(site: 'Site', *, knowledge: 'bool | None' = None) -> 'RebuildResult'
```

Az entitások újraépítése a tárolt kinyerésből, LLM-hívás nélkül: ugyanaz a levezetés,
amellyel az `aaa entities` minden futása végződik (ürítés, szabálykör, a tárolt kinyerés
visszaírása), utána a site-kör és a tudásbázis-kapcsolás (`resolve`). A blokkok, a
futásnapló, a tárolt kinyerés és a hívásnapló marad; a gráfot és a megállapításokat utána
az `aaa graph` és az `aaa findings` építi fel.

### `build_graph`

```python
build_graph(site: 'Site', *, wikidata: 'bool' = True, out: 'Path | None' = None) -> 'GraphResult'
```

Entitásgráf az M2 tárolt kimenetéből, LLM nélkül: oldal-csomópontok, az oldalak fő
entitása a bizonyítékaival, élek, az entitások súlya. `wikidata`: `is_a`-élek a biztos
Wikidata-osztályokból (a tudásbázis gyorsítótárán át). `out`: ha meg van adva, CSV-kimenet
ide: `<név>-main-entity.csv`, `<név>-edges.csv`, `<név>-weights.csv`, és Wikidata mellett
`<név>-is-a-rejected.csv`.

### `find`

```python
find(site: 'Site', *, out: 'Path | None' = None) -> 'FindResult'
```

SEO-megállapítások a gráfból (a `build_graph` után), LLM nélkül. `out`: ha meg van adva,
kimenet ide: `<név>-findings.csv`, `<név>-view-site.csv`, `<név>-view-entities.csv`,
`<név>-view-pages.csv`, `<név>-views.html`. Gráf nélkül: `GraphMissing`.

### `entity_report`

```python
entity_report(site: 'Site', out: 'Path', baseline: 'Path | None' = None) -> 'tuple[Path, Path, int]'
```

Futásjelentés (Markdown) és site-szintű entitástábla (CSV) a legutóbbi futásról:
`<out>/<név>-run.md`, `<out>/<név>-entities.csv`; `baseline`: a korábbi állapot adatbázisa a
regressziós összevetéshez (csak olvasva). Visszaad: (jelentés, tábla, az entitások száma).

### `validate`

```python
validate(site: 'Site', *, limit: 'int | None' = None, wikipedia: 'bool' = True) -> 'ValidationRun'
```

Az entitások validálása: Google Knowledge Graph és Wikipedia-szócikk; a válaszok a
site-adatbázisban és a `shared.duckdb`-ben gyorsítótárazva.

### `check_models`

```python
check_models() -> 'list[ModelCheck]'
```

A konfigurált LLM-modellek azonosítói a szolgáltatók modell-listáján (kulcs kell).

## A lépések eredményei

### `CrawlResult`

A crawl eredménye: az összesítő, az adatbázis útvonala, a tényleges beállítás, és hogy a
site-fájl adott-e include- vagy exclude-mintát.

Mezők:

- `summary`: `CrawlSummary`
- `path`: `Path`
- `options`: `CrawlOptions`
- `site_file_scope`: `bool`

### `ExtractResult`

A kinyerés eredménye: a futások azonosítói (szabálykör, LLM-kör) sorrendben;
`estimate_only`: csak költségbecslés készült, a pipeline nem futott tovább.

Mezők:

- `run_ids`: `list[int]`
- `estimate_only`: `bool`
- `restored`: `Restored | None`

### `ResolveResult`

A feloldás eredménye: a site-kör (`site_run`; None, ha a lépés ki van kapcsolva) és a
tudásbázis-kapcsolás (`linked`; None, ha nem futott).

Mezők:

- `site_run`: `SiteRun | None`
- `linked`: `KnowledgeRun | None`

### `RebuildResult`

Az újraépítés eredménye: a szabálykör futása, a visszaírás LLM-futása (None, ha nincs
tárolt kinyerés) és a feloldás.

Mezők:

- `rules_run`: `int | None`
- `restored`: `Restored | None`
- `resolved`: `ResolveResult`

### `GraphResult`

A gráfépítés eredménye: a futás számai (`run`) és a kiírt fájlok (`paths`; üres, ha nem
volt kimeneti mappa).

Mezők:

- `run`: `GraphRun`
- `paths`: `dict[str, Path]`

### `FindResult`

A megállapítások futása (`run`) és a kiírt fájlok (`paths`; üres, ha nem volt kimeneti
mappa).

Mezők:

- `run`: `FindingsRun`
- `paths`: `dict[str, Path]`

## Lekérdezések (szerződések)

### `site_profile`

```python
site_profile(site: 'Site') -> 'SiteProfile | None'
```

A site profilja a crawlból; None, ha még nincs crawl.

### `pages`

```python
pages(site: 'Site') -> 'list[Page]'
```

Minden bejárt oldal, `page_id` szerint.

### `page_metas`

```python
page_metas(site: 'Site') -> 'list[PageMeta]'
```

Oldalanként a canonical és a hreflang.

### `links`

```python
links(site: 'Site') -> 'list[Link]'
```

A belső linkek a forrásoldal és a DOM-sorrend szerint.

### `structured_data`

```python
structured_data(site: 'Site') -> 'list[StructuredData]'
```

A strukturált adat minden eleme: előbb az érvényes JSON-LD blokkok, utána a microdata-,
RDFa- és Open Graph-elemek, az oldal és a sorszám szerint.

### `latest_crawl_run`

```python
latest_crawl_run(site: 'Site') -> 'CrawlRun | None'
```

—

### `entities`

```python
entities(site: 'Site') -> 'list[Entity]'
```

A site entitásai, `entity_id` szerint.

### `kb_links`

```python
kb_links(site: 'Site', *, confident_only: 'bool' = True) -> 'list[KbLink]'
```

Az entitások tudásbázis-kapcsolatai (Wikidata, Wikipedia, Knowledge Graph). Alapból csak
a biztos (`wikidata_status = confident`) Wikidata- és Wikipedia-kapcsolás látszik: a riport
és minden javaslat (sameAs, tudásbázis-lehetőség, `is_a`, nyelvi összevonás) csak ezzel
számol; a valószínű kapcsolás csak tárolva van, a mezői itt üresek. `confident_only=False`:
a tárolt állapot, a valószínűvel együtt (ellenőrzéshez).

### `page_nodes`

```python
page_nodes(site: 'Site') -> 'list[PageNode]'
```

Az oldal-csomópontok a szerepükkel és a canonical-döntéssel.

### `edges`

```python
edges(site: 'Site', kind: 'str | None' = None) -> 'list[Edge]'
```

Az élek; `kind`: csak ez az éltípus (pl. `mentions`, `offers`, `is_a`).

### `main_entities`

```python
main_entities(site: 'Site') -> 'list[MainEntity]'
```

Az oldalak fő és másodlagos entitásai a bizonyítékokkal, az oldal és a rangsor szerint.

### `weights`

```python
weights(site: 'Site') -> 'list[EntityWeight]'
```

Az entitások súlya a site-on.

### `findings`

```python
findings(site: 'Site') -> 'list[Finding]'
```

A tárolt SEO-megállapítások, az azonosítójuk szerint.

### `llm_calls_of`

```python
llm_calls_of(site: 'Site') -> 'list[LLMCall]'
```

A site-adatbázisban könyvelt LLM-hívások.

## A riport bemenete

### `views`

```python
views(site: 'Site') -> 'SiteViews'
```

A riport bemenete: a megállapítások (típuscímkével, az entitás nevével, az érintett
oldalakkal), az entitások nézete (rang, súly, kapcsolatok, fő és csak említő oldalak) és
az oldalak nézete (szerep, fő entitás a bizonyítékokkal, a H1 és a title megnevezése,
további említések, az oldal megállapításai), egy verziózott szerződésben. A `find` után
hívható.

### `views_json`

```python
views_json(site: 'Site') -> 'str'
```

A `views` JSON-szövegként: rendezett kulcsokkal, UTF-8-ban, a `schema_version`-nel.

### `export_views_json`

```python
export_views_json(site: 'Site', out: 'Path') -> 'Path'
```

A `views_json` fájlba: `<out>/<név>-views.json`.

## Állapot és költség

### `status`

```python
status(site: 'Site') -> 'SiteStatus'
```

—

### `SiteStatus`

A site állapota: oldalszám, hibás oldalak, státuszosztályok (rendezve), a crawl-sor
állapotonként, a profil, az utolsó crawl és módszerenként a legutóbbi entitás-futás sora.

Mezők:

- `pages`: `int`
- `errors`: `int`
- `by_class`: `list[tuple[str, int]]`
- `queue`: `dict[str, int]`
- `profile`: `SiteProfile | None`
- `last_crawl`: `CrawlRun | None`
- `entity_runs`: `list[tuple]`

### `entity_run`

```python
entity_run(site: 'Site', run_id: 'int') -> 'tuple | None'
```

Egy entitás-futás sora (`store.ENTITY_RUN_COLUMNS` oszlopai).

### `entity_run_skipped`

```python
entity_run_skipped(site: 'Site', run_id: 'int') -> 'dict'
```

A futás kimaradásai ok szerint.

### `entity_type_counts`

```python
entity_type_counts(site: 'Site') -> 'list[tuple[str, int, int]]'
```

Típusonként: (típus, entitások száma, említéssorok száma), az entitásszám szerint.

### `kg_calls_today`

```python
kg_calls_today(site: 'Site') -> 'int'
```

A mai Knowledge Graph-hívások száma ezen a site-on.

### `llm_spend`

```python
llm_spend(site: 'Site | None' = None) -> 'LLMSpend'
```

—

### `LLMSpend`

A halmozott LLM-költség a főkönyvből (minden site): szolgáltatónként, a konfigurációban
nem szereplő modellek külön; `site_rows`: az adott site-on könyvelt hívások modellenként
(modell, hívás, USD, újrapróba, hibakódok), None, ha nincs site megadva.

Mezők:

- `ledger_path`: `str`
- `providers`: `list[ProviderSpend]`
- `unconfigured`: `list[tuple[str, float]]`
- `site_rows`: `list[tuple] | None`

### `ProviderSpend`

Egy szolgáltató halmozott költsége: modellenként (modell, USD, aktív-e), az összeg, a
leállási küszöb és a keret.

Mezők:

- `name`: `str`
- `models`: `list[tuple[str, float, bool]]`
- `total`: `float`
- `stop_usd`: `float`
- `budget_usd`: `float`

### `table_rows`

```python
table_rows(site: 'Site', table: 'str') -> 'tuple[list[str], list[tuple]]'
```

Egy tábla minden sora (a BLOB-oszlopok nélkül) az oszlopnevekkel; `table` az
`EXPORT_TABLES` egyike, különben `ApiError`.

## Állandók

### `CONCURRENCY`

`6`

### `EXPORT_TABLES`

`('pages', 'links', 'headings', 'schema_blocks', 'crawl_queue', 'crawl_runs', 'site', 'entities', 'page_entities', 'mention_sources', 'blocks', 'llm_calls', 'entity_runs')`

### `FINDING_TYPE_LABELS`

`{'h1_title_mismatch': 'H1/title-eltérés', 'cannibalization': 'Kannibalizáció', 'shared_topic': 'Közös téma', 'missing_page': 'Hiányzó oldal', 'uncovered_topic': 'Lefedetlen téma', 'unclear_topic': 'Nem egyértelmű téma', 'missing_h1': 'Hiányzó H1', 'h1_outside_content': 'H1 a fő tartalmon kívül', 'multiple_h1': 'Több H1', 'empty_section': 'Üres szakasz', 'skipped_level': 'Kihagyott heading-szint', 'missing_h2': 'Hiányzó H2', 'paragraph_heading': 'Bekezdés headingként jelölve', 'canonical_issue': 'Hibás canonical', 'legal_page': 'Jogi oldal'}`

### `KG_DAILY_QUOTA`

`100000`

### `MAX_PAGES`

`5000`

### `RENDER_TIMEOUT`

`15.0`
