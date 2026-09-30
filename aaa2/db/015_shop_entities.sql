-- 015: webshop-entitásszintek (M2 spec, „M2/6 — Site-szintű entitások”, 9a pont; M2/7 B).
--
-- entities.attributes: a termék tulajdonságai, amelyek nem entitások (teljesítmény, fázisszám,
--   energiaosztály), a terméknévből; JSON-objektum.
-- entity_relations.type: új érték az in_category (termék → kategória, a morzsamenüből). A DuckDB
--   ALTER-rel nem módosít CHECK-megszorítást: a tábla adata átmeneti táblába kerül, a tábla
--   újra létrejön, az adat vissza.

ALTER TABLE entities ADD COLUMN IF NOT EXISTS attributes JSON;

CREATE TABLE _entity_relations_014 AS SELECT * FROM entity_relations;
DROP TABLE entity_relations;
CREATE TABLE entity_relations (
    from_id         INTEGER NOT NULL,
    to_id           INTEGER NOT NULL,
    type            VARCHAR NOT NULL CHECK (type IN (
                        'offers', 'part_of', 'brand_of', 'in_category')),
    source          VARCHAR NOT NULL,
    evidence        JSON,
    PRIMARY KEY (from_id, to_id, type)
);
INSERT INTO entity_relations SELECT from_id, to_id, type, source, evidence
    FROM _entity_relations_014;
DROP TABLE _entity_relations_014;
