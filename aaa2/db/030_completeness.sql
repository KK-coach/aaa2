-- 030: a crawl teljességi állapotának rögzítése a futáson.
--
-- crawl_runs.skipped_by_limit: hány cím maradt ki a sorból az oldalkorlát (`max_pages`) miatt;
--   a korábbi futásoknál NULL (nem ismert).
-- crawl_runs.include, exclude: a futás hatókör-szűkítése (regex a normalizált URL-re); '' ha
--   nem volt; a korábbi futásoknál NULL (nem ismert).
-- crawl_runs.stopped: a megállás oka (oldalkorlát, a várt oldalszám vagy idő túllépése); NULL,
--   ha a futás nem állt meg idő előtt. A korábbi futásoknál a megjegyzésből (`(megállt: …)`).

ALTER TABLE crawl_runs ADD COLUMN IF NOT EXISTS skipped_by_limit INTEGER;
ALTER TABLE crawl_runs ADD COLUMN IF NOT EXISTS include VARCHAR;
ALTER TABLE crawl_runs ADD COLUMN IF NOT EXISTS exclude VARCHAR;
ALTER TABLE crawl_runs ADD COLUMN IF NOT EXISTS stopped VARCHAR;

UPDATE crawl_runs SET stopped = nullif(regexp_extract(notes, '\(megállt: (.*)\)$', 1), '')
 WHERE stopped IS NULL AND notes LIKE '%(megállt: %';
