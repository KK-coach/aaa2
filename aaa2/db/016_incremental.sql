-- 016: inkrementális kinyerés (M2/7, A rész 2. pont).
--
-- entity_run_pages.raw_html_hash: az oldal M1-es stabil hash-e (`pages.raw_html_hash`) a
--   kinyeréskor; input_hash: a kinyerés bemenetének hash-e (prompt, site-leíró mondat, a
--   content-blokkok szövege). Egy új futás a változatlan oldal (azonos modell, azonos
--   input_hash) tárolt kinyerését használja, hívás nélkül.
-- blocks_built: melyik nyers hash-ből épültek az oldal blokkjai. Ha a crawl után a hash más, a
--   blokkok (és a rájuk mutató említések) újraépülnek. A tábla előtti blokkoknak nincs sora:
--   azokat a `build_blocks` a jelenlegi hash-sel veszi fel, újraépítés nélkül.

ALTER TABLE entity_run_pages ADD COLUMN IF NOT EXISTS raw_html_hash VARCHAR;
ALTER TABLE entity_run_pages ADD COLUMN IF NOT EXISTS input_hash VARCHAR;

CREATE TABLE IF NOT EXISTS blocks_built (
    page_id         INTEGER PRIMARY KEY,
    raw_html_hash   VARCHAR,
    built_at        TIMESTAMP
);
