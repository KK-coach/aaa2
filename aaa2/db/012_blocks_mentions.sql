-- 012: blokkmodell és említéstábla (M2 spec A2).
--
-- blocks: az oldal látható szövegblokkjai a renderelt DOM-ból (entities.dom), sorszámmal és
-- heading-útvonallal; a 0. sorszám a title. A chrome-régió (header, nav, footer, aside …) is
-- tárolódik, jelölve; az LLM-bemenetbe csak a content régió kerül.
--
-- page_entities: említéstábla. Egy sor egy előfordulás: blokk, karakterpozíció (a blokk
-- szövegében, [char_start, char_end)), a szöveg szerinti alak. Egy említés egyszer szerepel,
-- akárhány forrás találta meg; a források a mention_sources táblában. A schema-említésnek
-- (JSON-LD, nem látható szöveg) nincs blokkja és pozíciója.
--
-- A 011-es állapot sorai (evidence / context / section_ordinal) a page_entities_v1 táblában
-- maradnak; blokkhoz és pozícióhoz nem köthetők.

CREATE SEQUENCE IF NOT EXISTS seq_block_id START 1;

CREATE TABLE IF NOT EXISTS blocks (
    block_id        INTEGER PRIMARY KEY DEFAULT nextval('seq_block_id'),
    page_id         INTEGER NOT NULL,
    ordinal         INTEGER NOT NULL,              -- 0 = title, utána dokumentum-sorrend
    kind            VARCHAR NOT NULL CHECK (kind IN (
                        'title', 'heading', 'paragraph', 'list_item', 'table_row', 'code',
                        'card', 'other')),
    region          VARCHAR NOT NULL CHECK (region IN ('content', 'chrome')),
    level           INTEGER,                       -- headingnél 1–6
    heading_path    VARCHAR[] NOT NULL,
    text            VARCHAR NOT NULL,
    cells           JSON,                          -- table_row: [{"header": …, "value": …}]
    UNIQUE (page_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_blocks_page ON blocks(page_id);

CREATE TABLE page_entities_v1 AS SELECT * FROM page_entities;
DROP TABLE page_entities;

CREATE SEQUENCE IF NOT EXISTS seq_mention_id START 1;

CREATE TABLE page_entities (
    mention_id      INTEGER PRIMARY KEY DEFAULT nextval('seq_mention_id'),
    page_id         INTEGER NOT NULL,
    entity_id       INTEGER NOT NULL,
    block_id        INTEGER,                       -- NULL: schema
    char_start      INTEGER,
    char_end        INTEGER,
    surface_form    VARCHAR NOT NULL,
    position        VARCHAR NOT NULL,              -- title | h1 | heading | body | anchor | schema
    description     VARCHAR,                       -- LLM: mit jelent ez az előfordulás
    context         VARCHAR,                       -- schema: a JSON-LD csomópont
    CHECK ((block_id IS NULL) = (position = 'schema')),
    CHECK (block_id IS NULL OR (char_start >= 0 AND char_end > char_start)),
    UNIQUE (page_id, block_id, char_start, char_end, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_pe_page ON page_entities(page_id);
CREATE INDEX IF NOT EXISTS idx_pe_entity ON page_entities(entity_id);

CREATE TABLE IF NOT EXISTS mention_sources (
    mention_id      INTEGER NOT NULL,
    source          VARCHAR NOT NULL CHECK (source IN ('schema', 'rule', 'llm')),
    run_id          INTEGER NOT NULL,              -- entity_runs
    llm_call_id     INTEGER REFERENCES llm_calls(call_id),  -- llm: a kinyerő hívás
    count           INTEGER NOT NULL DEFAULT 1,    -- schema: hány JSON-LD csomópont
    PRIMARY KEY (mention_id, source, run_id)
);

ALTER TABLE entities ADD COLUMN IF NOT EXISTS subtype VARCHAR;
ALTER TABLE entities ADD COLUMN IF NOT EXISTS role VARCHAR;       -- org: brand is (site-név)
ALTER TABLE entities ADD COLUMN IF NOT EXISTS namespace VARCHAR;  -- API-elem: könyvtár, modul
ALTER TABLE entities ADD COLUMN IF NOT EXISTS page_id INTEGER;    -- a site saját oldala (work)
