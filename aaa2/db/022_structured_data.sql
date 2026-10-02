-- 022: strukturált adat a JSON-LD mellett: az oldal microdata- és RDFa-elemei (a crawl írja,
-- `engine/structured.py`). A JSON-LD marad a `schema_blocks` táblában; a két tábla együtt adja a
-- `StructuredData` szerződést. Az elem a JSON-LD-hez hasonló alakú JSON-objektum.
-- A szabálykör és a feloldás ezt a táblát még nem olvassa.

CREATE TABLE IF NOT EXISTS structured_data (
    page_id     INTEGER NOT NULL,
    syntax      VARCHAR NOT NULL CHECK (syntax IN ('json-ld', 'microdata', 'rdfa', 'opengraph')),
    type        VARCHAR,                           -- a típus rövid neve; több típus vesszővel
    json        VARCHAR NOT NULL,                  -- az elem JSON-objektumként
    ordinal     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_structured_page ON structured_data(page_id);
