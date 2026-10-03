-- 024: a blokkok újraépítése a renderelt tartalom változására is.
--
-- blocks_built.content_hash: az oldal renderelt tartalmának hash-e (title, H1, fő tartalom) a
--   blokkok építésekor. A nyers HTML hash-e (raw_html_hash) nem változik, ha a tartalmat a
--   JavaScript tölti be; ilyenkor a tartalom-hash eltérése indítja az újraépítést. A 024 előtti
--   soroknál üres: azokat a `build_blocks` a jelenlegi tartalom-hash-sel veszi fel,
--   újraépítés nélkül.

ALTER TABLE blocks_built ADD COLUMN IF NOT EXISTS content_hash VARCHAR;
