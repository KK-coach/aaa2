-- Entitás-típusok, a kanonikus név nyelve, az LLM-eredetű entitássor hívása, és az LLM-hívás
-- kísérleteinek száma.
-- entities.type: pontosan tíz érték, mindegyiknek van Schema.org-megfelelője:
--   brand → Brand, product → Product, service → Service, work → CreativeWork,
--   event → Event, person → Person, org → Organization, place → Place,
--   tech → SoftwareApplication, concept → DefinedTerm.
-- entities.lang: a kanonikus név nyelve (BCP 47 elsődleges címke, pl. "hu"): azoknak az
--   oldalaknak a leggyakoribb nyelve, ahol a kanonikus alak előfordul.
-- page_entities.llm_call_id: az a hívás, amelyik a sort adta (source = 'llm'); a párhuzamos
--   modellteszt ezen választja szét a modellek sorait. Szabályból jött sornál NULL.
-- llm_calls.attempts: hány API-kérés ment ki a hívásért (1 = nem kellett újrapróba); a
--   latency_ms a sikeres kísérleté.
--
-- A DuckDB ALTER-rel nem ad CHECK- és idegenkulcs-megszorítást, és idegenkulcsos táblát nem
-- nevez át: mindkét tábla adata átmeneti táblába kerül, a tábla újra létrejön, az adat vissza.

ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS attempts INTEGER DEFAULT 1;

CREATE TABLE _entities_004 AS SELECT * FROM entities;
DROP TABLE entities;
CREATE TABLE entities (
    entity_id       INTEGER PRIMARY KEY DEFAULT nextval('seq_entity_id'),
    name            VARCHAR NOT NULL,              -- kanonikus név
    lang            VARCHAR,                       -- a kanonikus név nyelve (005)
    type            VARCHAR NOT NULL CHECK (type IN (
                        'brand', 'product', 'service', 'work', 'event',
                        'person', 'org', 'place', 'tech', 'concept')),
    aliases         VARCHAR[],
    kg_status       VARCHAR DEFAULT 'unchecked',   -- high | medium | stub | ambiguous | no_match | unchecked
    kg_id           VARCHAR,                       -- Google KG id
    wikipedia_url   VARCHAR,                       -- szócikk a site nyelvén, ha van
    source          VARCHAR,                       -- rule | llm | schema
    created_at      TIMESTAMP
);
INSERT INTO entities (entity_id, name, type, aliases, kg_status, kg_id, wikipedia_url, source,
                      created_at)
    SELECT entity_id, name, type, aliases, kg_status, kg_id, wikipedia_url, source, created_at
    FROM _entities_004;
DROP TABLE _entities_004;
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

CREATE TABLE _page_entities_004 AS SELECT * FROM page_entities;
DROP TABLE page_entities;
CREATE TABLE page_entities (
    page_id         INTEGER NOT NULL,
    entity_id       INTEGER NOT NULL,
    position        VARCHAR NOT NULL,              -- title | h1 | heading | body | anchor | schema
    evidence        VARCHAR NOT NULL,              -- szó szerinti idézet; nélküle nincs sor
    context         VARCHAR,                       -- a bekezdés / listaelem, amiben áll
    section_ordinal INTEGER,                       -- headings.ordinal, amelyik szekció alatt van
    count           INTEGER DEFAULT 1,
    source          VARCHAR NOT NULL,              -- rule | llm
    llm_call_id     INTEGER REFERENCES llm_calls(call_id)  -- (005)
);
INSERT INTO page_entities (page_id, entity_id, position, evidence, context, section_ordinal,
                           count, source)
    SELECT page_id, entity_id, position, evidence, context, section_ordinal, count, source
    FROM _page_entities_004;
DROP TABLE _page_entities_004;
CREATE INDEX IF NOT EXISTS idx_pe_page ON page_entities(page_id);
CREATE INDEX IF NOT EXISTS idx_pe_entity ON page_entities(entity_id);
