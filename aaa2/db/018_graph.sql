-- 018: entitásgráf (M3 spec, „M3 — Entitásgráf”, 2–3. és 5–6. pont; M3/1).
--
-- page_nodes: az alkalmas HTML-oldalak csomópontja: URL, oldalszerep (offer, article,
--   component, product, category, listing, home, profile, support), a segédoldal fajtája (legal, contact,
--   list, checkout, placeholder), title, H1, nyelv, az oldalcsoport és a hreflang-pár URL-jei;
--   main_status: main (van fő entitása), support (segédoldal, nincs), none (nincs elég
--   bizonyíték); decision: a döntés az összes jelölttel és bizonyítékkal (visszakövetéshez).
-- edges: from → to él (page vagy entity végpontokkal): mentions (oldal → entitás, az említések
--   súlyozva), main_entity (oldal → entitás), part_of, brand_of, offers (az M2/6 kapcsolatai),
--   is_a (termék → kategória a kategóriaoldalból; entitás → entitás a biztos Wikidata-osztályból),
--   about (a cikk entitása → a témája, a cikkoldal fő entitása); a forrással, a bizonyítékkal és
--   a súllyal.
-- page_main_entity: az oldal fő (main) és legfeljebb két másodlagos (secondary) entitása, a
--   megbízhatósággal (strong / medium / weak) és a bizonyítékok listájával;
--   external_confirmation: a külső megerősítés helye (M3b), üres.
-- entity_weights: az entitás site-szintű súlya és az összetevői (a sablon- és demó-említések
--   nélkül).

CREATE TABLE IF NOT EXISTS page_nodes (
    page_id         INTEGER PRIMARY KEY,
    url             VARCHAR NOT NULL,
    role            VARCHAR NOT NULL,
    support_kind    VARCHAR,
    title           VARCHAR,
    h1              VARCHAR,
    lang            VARCHAR,
    group_key       VARCHAR NOT NULL,
    hreflang_pages  VARCHAR[],
    main_status     VARCHAR NOT NULL CHECK (main_status IN ('main', 'support', 'none')),
    decision        JSON
);

CREATE SEQUENCE IF NOT EXISTS seq_edge_id START 1;

CREATE TABLE IF NOT EXISTS edges (
    edge_id         INTEGER PRIMARY KEY DEFAULT nextval('seq_edge_id'),
    from_kind       VARCHAR NOT NULL CHECK (from_kind IN ('page', 'entity')),
    from_id         INTEGER NOT NULL,
    to_kind         VARCHAR NOT NULL CHECK (to_kind IN ('page', 'entity')),
    to_id           INTEGER NOT NULL,
    type            VARCHAR NOT NULL CHECK (type IN (
                        'mentions', 'main_entity', 'part_of', 'brand_of', 'offers', 'is_a',
                        'about')),
    source          VARCHAR NOT NULL,
    evidence        JSON,
    weight          DOUBLE
);

CREATE TABLE IF NOT EXISTS page_main_entity (
    page_id                 INTEGER NOT NULL,
    entity_id               INTEGER NOT NULL,
    role                    VARCHAR NOT NULL CHECK (role IN ('main', 'secondary')),
    rank                    INTEGER NOT NULL,
    confidence              VARCHAR NOT NULL CHECK (confidence IN ('strong', 'medium', 'weak')),
    evidence                JSON NOT NULL,
    external_confirmation   JSON,
    PRIMARY KEY (page_id, entity_id)
);

CREATE TABLE IF NOT EXISTS entity_weights (
    entity_id       INTEGER PRIMARY KEY,
    pages           INTEGER NOT NULL,
    mentions        INTEGER NOT NULL,
    structural      INTEGER NOT NULL,
    main_pages      INTEGER NOT NULL,
    secondary_pages INTEGER NOT NULL,
    inbound_anchors INTEGER NOT NULL,
    single_mention  BOOLEAN NOT NULL,
    weight          DOUBLE NOT NULL
);
