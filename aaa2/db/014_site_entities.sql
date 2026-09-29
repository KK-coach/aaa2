-- 014: site-szintű entitások (M2 spec, „M2/6 — Site-szintű entitások”, 11. pont).
--
-- entities.anchor_page_id: az oldalhoz kötött entitás oldala (ajánlat, komponens, cikk, termék;
--   hreflang-párnál az elsődleges nyelvű oldal). tier: core (fő ajánlat) / package (csomag) /
--   step (módszertani lépés). flags: demo (demótartalom, nem kerül a gráfba), template (minden
--   tartalmi említése sablonismétlés), navigational (navigációs címke, nem entitás).
--   wikidata_status: confident / probable / none.
-- page_entities.flags: az említés jelölése (template: ugyanaz a szöveg ugyanabban a
--   blokkfajtában az oldalcsoportok jelentős részén).
-- entity_aliases: az entitás nevei a forrásukkal (h1, title, nav, anchor, schema, llm, hreflang,
--   merge) és nyelvükkel; az összevonás és a nevek visszakövetéséhez.
-- entity_relations: from → to kapcsolat: offers (ajánlat → fogalom), part_of (csomag → fő
--   ajánlat), brand_of (márka → termék); a forrással és a bizonyítékkal.
-- merge_log: mely entitás olvadt melyikbe (vagy vált le róla), milyen szabállyal és
--   bizonyítékkal; a site-kör futásához kötve.

ALTER TABLE entities ADD COLUMN IF NOT EXISTS anchor_page_id INTEGER;
ALTER TABLE entities ADD COLUMN IF NOT EXISTS tier VARCHAR;
ALTER TABLE entities ADD COLUMN IF NOT EXISTS flags VARCHAR[];
ALTER TABLE entities ADD COLUMN IF NOT EXISTS wikidata_status VARCHAR;
ALTER TABLE page_entities ADD COLUMN IF NOT EXISTS flags VARCHAR[];

CREATE TABLE IF NOT EXISTS entity_aliases (
    entity_id       INTEGER NOT NULL,
    alias           VARCHAR NOT NULL,
    lang            VARCHAR,
    source          VARCHAR NOT NULL CHECK (source IN (
                        'h1', 'title', 'nav', 'anchor', 'schema', 'llm', 'hreflang', 'merge')),
    PRIMARY KEY (entity_id, alias, source)
);

CREATE TABLE IF NOT EXISTS entity_relations (
    from_id         INTEGER NOT NULL,
    to_id           INTEGER NOT NULL,
    type            VARCHAR NOT NULL CHECK (type IN ('offers', 'part_of', 'brand_of')),
    source          VARCHAR NOT NULL,
    evidence        JSON,
    PRIMARY KEY (from_id, to_id, type)
);

CREATE SEQUENCE IF NOT EXISTS seq_merge_id START 1;

CREATE TABLE IF NOT EXISTS merge_log (
    merge_id        INTEGER PRIMARY KEY DEFAULT nextval('seq_merge_id'),
    run_id          INTEGER NOT NULL,
    kept_id         INTEGER NOT NULL,
    removed_id      INTEGER,                       -- NULL: leválasztás (az új entitás a kept_id)
    kept_name       VARCHAR NOT NULL,
    removed_name    VARCHAR NOT NULL,
    rule            VARCHAR NOT NULL,
    evidence        JSON,
    merged_at       TIMESTAMP NOT NULL
);
