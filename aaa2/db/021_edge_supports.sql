-- 021: új éltípus: supports (a cikk entitása vagy a cikkoldal → a site saját ajánlata, amelyre a
-- cikk JSON-LD about / mainEntity-je mutat). Az éltábla minden gráffutásban újraépül, ezért a
-- típuslista bővítéséhez a tábla újra létrejön.

DROP TABLE IF EXISTS edges;

CREATE TABLE edges (
    edge_id         INTEGER PRIMARY KEY DEFAULT nextval('seq_edge_id'),
    from_kind       VARCHAR NOT NULL CHECK (from_kind IN ('page', 'entity')),
    from_id         INTEGER NOT NULL,
    to_kind         VARCHAR NOT NULL CHECK (to_kind IN ('page', 'entity')),
    to_id           INTEGER NOT NULL,
    type            VARCHAR NOT NULL CHECK (type IN (
                        'mentions', 'main_entity', 'part_of', 'brand_of', 'offers', 'is_a',
                        'about', 'duplicate_of', 'supports')),
    source          VARCHAR NOT NULL,
    evidence        JSON,
    weight          DOUBLE
);
