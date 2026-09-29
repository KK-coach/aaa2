-- 013: az entitás-pipeline a megközelítés v3 szerint (M2 spec, „Megközelítés v3”).
--
-- entity_run_pages: az LLM-kör oldalankénti naplója. Egy sor egy oldal egy futásban: az
--   állapota (done: mentve; extracted: kinyerve, mentés nélkül; failed: minden kinyerő hívás
--   hibás; verify_error: az ellenőrző hívás hibás, nincs mentés; skipped: nincs content-blokk;
--   stopped: a keret vagy a költséghatár miatt nem futott), a kimaradás okai, a hívások, a
--   kitalált említések, az időtartam, a kinyerés rekordja (a v3 szabály előtt) és a szabály
--   utáni rekord. A folytatás (`--resume`) ebből dönt: a done oldal kimarad, a meglévő rekord
--   nem hív újra.
-- soft_checks: a puha típusok (service, concept) oldalankénti bizonyítéka a legutóbbi
--   feldolgozásból: szerkezeti hely, blokkszám, említésszám, title- vagy heading-hely,
--   fontossági sorszám (concept), tudásbázis-egyezés, az ellenőrző hívás döntése (service), és
--   hogy megmaradt-e. A kiesett service-nek nincs entitása.
-- entities.wikidata_id / wikipedia: a név pontos egyezése egy Wikidata-címkével vagy
--   -aliasszal, illetve egy Wikipedia-címmel vagy átirányítással (az entitás nyelvén vagy
--   angolul). Névegyezés, nem azonosítás. knowledge_checked_at: az utolsó sikeres lekérdezés.
-- entity_runs.seconds: a futás időtartama; folytatásnál a szakaszok összege.

CREATE TABLE IF NOT EXISTS entity_run_pages (
    run_id          INTEGER NOT NULL,
    page_id         INTEGER NOT NULL,
    status          VARCHAR NOT NULL CHECK (status IN (
                        'done', 'extracted', 'failed', 'verify_error', 'skipped', 'stopped')),
    reasons         JSON,                          -- ok → darab
    call_ids        INTEGER[],
    chunks          INTEGER,
    fabricated      INTEGER,
    seconds         DOUBLE,
    error           VARCHAR,
    extraction      JSON,
    refined         JSON,
    finished_at     TIMESTAMP,
    PRIMARY KEY (run_id, page_id)
);

CREATE TABLE IF NOT EXISTS soft_checks (
    run_id          INTEGER NOT NULL,
    page_id         INTEGER NOT NULL,
    entity_id       INTEGER,                       -- NULL: kiesett (service)
    canonical       VARCHAR NOT NULL,
    type            VARCHAR NOT NULL CHECK (type IN ('service', 'concept')),
    structure       VARCHAR,                       -- title:b0 | heading:b3 | card:… | nav | anchor
    blocks          INTEGER,
    mentions        INTEGER,
    prominent       BOOLEAN,
    rank            INTEGER,
    knowledge       VARCHAR,                       -- wikidata:hu:Q… (név) | wikipedia:en:… (név)
    sol             BOOLEAN,                       -- service: az ellenőrzés döntése, NULL: nem ment
    kept            BOOLEAN NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_soft_checks_page ON soft_checks(page_id);

ALTER TABLE entities ADD COLUMN IF NOT EXISTS wikidata_id VARCHAR;
ALTER TABLE entities ADD COLUMN IF NOT EXISTS wikipedia VARCHAR;         -- nyelv:cím
ALTER TABLE entities ADD COLUMN IF NOT EXISTS knowledge_checked_at TIMESTAMP;
ALTER TABLE entity_runs ADD COLUMN IF NOT EXISTS seconds DOUBLE;
