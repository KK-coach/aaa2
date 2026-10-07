-- 031: a tárolt blokkok és a tárolt kinyerés érvényességének ellenőrzéséhez.
--
-- blocks_built.dom_hash: a renderelt DOM (a tárolt, tömörített `pages.rendered_html`) hash-e,
--   amelyből a blokkok épültek. Ha az oldalt a crawl újra renderelte, a blokképítő a mostani
--   DOM-ból épített blokkokat összeveti a tároltakkal (a nyers HTML és a lapos szöveg
--   változatlansága mellett is változhat a szerkezet, pl. heading helyett bekezdés). A korábbi
--   soroknál NULL: az első futás a mostani értéket veszi fel, újraépítés nélkül.
-- entity_run_pages.blocks_hash: a kinyerés bemenetének blokkjai (azonosító, fajta,
--   heading-útvonal, szöveg, cellák) hash-e. A tárolt rekord akkor írható vissza, ha az oldal
--   mostani blokkjai ugyanezek. A korábbi rekordoknál NULL: ott a visszaírhatóságot az dönti
--   el, hogy a rekord említései megtalálhatók-e a mostani blokkokban.

ALTER TABLE blocks_built ADD COLUMN IF NOT EXISTS dom_hash VARCHAR;
ALTER TABLE entity_run_pages ADD COLUMN IF NOT EXISTS blocks_hash VARCHAR;
