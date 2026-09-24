-- AAA v2 — 001_init.sql
-- DuckDB séma: egy fájl site-onként (data/<domain>.duckdb).
-- A rules és az általános entitás-szótár a data/shared.duckdb-ben él (lásd lent, SHARED szekció).
-- Ez a séma a szerződés a motor, a resolver és a funkciók között. Oszlop hozzáadható,
-- meglévő oszlop jelentése nem változik.

-- ---------------------------------------------------------------------------
-- SITE
-- ---------------------------------------------------------------------------

CREATE SEQUENCE IF NOT EXISTS seq_page_id START 1;
CREATE SEQUENCE IF NOT EXISTS seq_entity_id START 1;
CREATE SEQUENCE IF NOT EXISTS seq_call_id START 1;
CREATE SEQUENCE IF NOT EXISTS seq_run_id START 1;

CREATE TABLE IF NOT EXISTS site (
    domain          VARCHAR PRIMARY KEY,          -- registrable domain
    seed_url        VARCHAR NOT NULL,
    crawled_at      TIMESTAMP,                     -- utolsó teljes crawl kezdete
    target_country  VARCHAR,                       -- hreflang / TLD / nyelv alapján levezetve
    languages       VARCHAR[],                     -- talált nyelvek
    tech            VARCHAR[],                     -- resolver `tech` domain kimenete (M4)
    tech_signals    VARCHAR[],                     -- M1 nyers jelek: generator meta, script-src domainek, útvonalak
    trailing_slash  BOOLEAN,                       -- a site domináns URL-formája, crawl alatt rögzített
    page_count      INTEGER DEFAULT 0,
    render_mode     VARCHAR DEFAULT 'playwright'
);

-- ---------------------------------------------------------------------------
-- CRAWL FUTÁSOK ÉS FRONTIER (M1 belső, folytatható crawlhoz)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS crawl_runs (
    run_id          INTEGER PRIMARY KEY DEFAULT nextval('seq_run_id'),
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    max_pages       INTEGER,
    concurrency     INTEGER,
    pages_done      INTEGER DEFAULT 0,
    pages_failed    INTEGER DEFAULT 0,
    pages_skipped   INTEGER DEFAULT 0,             -- hash egyezett, render kimaradt
    pages_per_sec   DOUBLE,                        -- mért, nem feltétel
    bytes_stored    BIGINT,
    notes           VARCHAR
);

CREATE TABLE IF NOT EXISTS crawl_queue (
    url             VARCHAR PRIMARY KEY,           -- normalizált URL
    depth           INTEGER NOT NULL DEFAULT 0,
    priority        INTEGER NOT NULL DEFAULT 50,   -- kisebb = előbb; sitemap < nav < body < footer
    status          VARCHAR NOT NULL DEFAULT 'queued', -- queued | done | failed
    discovered_from VARCHAR,                       -- melyik oldalon találtuk
    error           VARCHAR,
    updated_at      TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- PAGES
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pages (
    page_id             INTEGER PRIMARY KEY DEFAULT nextval('seq_page_id'),
    url                 VARCHAR NOT NULL UNIQUE,   -- normalizált URL
    status              INTEGER,                   -- HTTP státusz a fő navigációból
    error               VARCHAR,                   -- render / fetch hiba, ha volt; a sor akkor is létezik
    canonical           VARCHAR,                   -- nem írja felül az url-t
    noindex             BOOLEAN DEFAULT FALSE,     -- meta robots / X-Robots-Tag
    title               VARCHAR,
    meta_description    VARCHAR,
    h1                  VARCHAR,                   -- első H1
    lang                VARCHAR,                   -- html[lang], különben detektált
    hreflang            VARCHAR[],                 -- "lang|url" párok
    page_type           VARCHAR,                   -- resolver `page_type` (M4); M1-ben NULL
    word_count          INTEGER,
    main_content        VARCHAR,                   -- kinyert főszöveg
    external_link_count INTEGER DEFAULT 0,         -- külső linkek csak számolva
    raw_html_hash       VARCHAR,                   -- sha256 a nyers válaszon, skip-alap
    rendered_html       BLOB,                      -- zstd-tömörített Playwright-kimenet
    render_ms           INTEGER,
    fetched_at          TIMESTAMP,
    run_id              INTEGER
);

CREATE INDEX IF NOT EXISTS idx_pages_url ON pages(url);
CREATE INDEX IF NOT EXISTS idx_pages_type ON pages(page_type);

-- ---------------------------------------------------------------------------
-- LINKS (csak belső)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS links (
    from_page_id    INTEGER NOT NULL,
    to_url          VARCHAR NOT NULL,              -- normalizált cél
    to_page_id      INTEGER,                       -- NULL, ha a cél nincs crawlolva
    anchor          VARCHAR,                       -- szöveg | img alt | aria-label
    position        VARCHAR NOT NULL,              -- nav | body | footer | aside
    nofollow        BOOLEAN DEFAULT FALSE,
    ordinal         INTEGER                        -- sorrend az oldalon
);

CREATE INDEX IF NOT EXISTS idx_links_from ON links(from_page_id);
CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_page_id);
CREATE INDEX IF NOT EXISTS idx_links_to_url ON links(to_url);

-- ---------------------------------------------------------------------------
-- HEADINGS
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS headings (
    page_id     INTEGER NOT NULL,
    level       INTEGER NOT NULL,                  -- 1..6
    text        VARCHAR,
    ordinal     INTEGER NOT NULL                   -- sorrend az oldalon, 1-től
);

CREATE INDEX IF NOT EXISTS idx_headings_page ON headings(page_id);

-- ---------------------------------------------------------------------------
-- SCHEMA BLOCKS (JSON-LD)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_blocks (
    page_id     INTEGER NOT NULL,
    type        VARCHAR,                           -- @type; 'invalid', ha a JSON hibás
    json        VARCHAR NOT NULL,                  -- nyers blokk; @graph elemenként szétbontva
    ordinal     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_schema_page ON schema_blocks(page_id);

-- ---------------------------------------------------------------------------
-- ENTITIES (M2-től töltődik; M1 csak létrehozza)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS entities (
    entity_id       INTEGER PRIMARY KEY DEFAULT nextval('seq_entity_id'),
    name            VARCHAR NOT NULL,              -- kanonikus név
    type            VARCHAR,                       -- brand | product | person | org | place | tech | concept
    aliases         VARCHAR[],
    kg_status       VARCHAR DEFAULT 'unchecked',   -- high | medium | stub | ambiguous | no_match | unchecked
    kg_id           VARCHAR,                       -- Google KG id
    wikipedia_url   VARCHAR,                       -- szócikk a site nyelvén, ha van
    source          VARCHAR,                       -- rule | llm | schema
    created_at      TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

CREATE TABLE IF NOT EXISTS page_entities (
    page_id         INTEGER NOT NULL,
    entity_id       INTEGER NOT NULL,
    position        VARCHAR NOT NULL,              -- title | h1 | heading | body | anchor | schema
    evidence        VARCHAR NOT NULL,              -- szó szerinti idézet; nélküle nincs sor
    context         VARCHAR,                       -- a bekezdés / listaelem, amiben áll
    section_ordinal INTEGER,                       -- headings.ordinal, amelyik szekció alatt van
    count           INTEGER DEFAULT 1,
    source          VARCHAR NOT NULL               -- rule | llm
);

CREATE INDEX IF NOT EXISTS idx_pe_page ON page_entities(page_id);
CREATE INDEX IF NOT EXISTS idx_pe_entity ON page_entities(entity_id);

-- ---------------------------------------------------------------------------
-- LLM CALLS (minden hívás ide ír, modultól függetlenül)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS llm_calls (
    call_id     INTEGER PRIMARY KEY DEFAULT nextval('seq_call_id'),
    domain      VARCHAR NOT NULL,                  -- resolver-domain vagy modulnév
    page_id     INTEGER,                           -- NULL, ha nem oldalhoz kötött
    model       VARCHAR NOT NULL,                  -- gemini-* | gpt-* | claude-*
    tokens_in   INTEGER,
    tokens_out  INTEGER,
    cost_usd    DOUBLE,
    purpose     VARCHAR NOT NULL,                  -- extract | verify | propose_rule
    latency_ms  INTEGER,
    called_at   TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_domain ON llm_calls(domain, called_at);

-- ===========================================================================
-- SHARED (data/shared.duckdb) — külön fájl, ugyanez a migráció fut rá,
-- de csak az alábbi táblák relevánsak benne. Az általános entitás-szótár
-- ugyanazt az `entities` sémát használja, `scope = 'global'` nélkül,
-- mert a fájl maga a scope.
-- ===========================================================================

CREATE SEQUENCE IF NOT EXISTS seq_rule_id START 1;

CREATE TABLE IF NOT EXISTS rules (
    rule_id          INTEGER PRIMARY KEY DEFAULT nextval('seq_rule_id'),
    domain           VARCHAR NOT NULL,             -- entity | tech | page_type | ...
    scope            VARCHAR NOT NULL DEFAULT 'global', -- 'global' vagy site-domain
    kind             VARCHAR NOT NULL,             -- dict | regex | selector | field
    pattern          VARCHAR NOT NULL,             -- a szabály maga
    output           VARCHAR NOT NULL,             -- entitás-id | tech-név | page_type
    status           VARCHAR NOT NULL DEFAULT 'proposed', -- proposed | active | review | retired
    hits             INTEGER DEFAULT 0,
    misses           INTEGER DEFAULT 0,
    verify_rate      DOUBLE DEFAULT 0.2,           -- LLM-ellenőrzés aránya, adaptív
    agreement_rate   DOUBLE,                       -- ellenőrzött találatok egyezése
    last_verified_at TIMESTAMP,
    created_from     VARCHAR,                      -- llm_calls.call_id vagy 'manual'
    created_at       TIMESTAMP,
    updated_at       TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rules_lookup ON rules(domain, scope, status);

-- Szabály-találatok és ellenőrzések naplója: ebből számolódik a hits/misses/agreement_rate,
-- és ez a címkézett adathalmaz a későbbi szabály-finomításhoz.
CREATE TABLE IF NOT EXISTS rule_events (
    rule_id     INTEGER NOT NULL,
    site_domain VARCHAR,
    page_id     INTEGER,
    event       VARCHAR NOT NULL,                  -- hit | verified_ok | verified_wrong
    llm_call_id INTEGER,                           -- ha volt ellenőrzés
    occurred_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rule_events_rule ON rule_events(rule_id, occurred_at);
