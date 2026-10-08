-- 035: a szomszéd blokkra javított említés jelölése.
--
-- page_entities.block_corrected: a kinyerés a modell blokkazonosítóját a közvetlen szomszédra
--   javította ('-1': az előző, '+1': a következő tartalmi blokk); NULL: nem volt javítás.
-- page_entities.block_given: a modell eredeti blokkazonosítója a javított említésnél (pl.
--   'b103'); NULL: nem volt javítás.

ALTER TABLE page_entities ADD COLUMN IF NOT EXISTS block_corrected VARCHAR;
ALTER TABLE page_entities ADD COLUMN IF NOT EXISTS block_given VARCHAR;
