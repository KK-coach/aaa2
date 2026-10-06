-- 029: az aktuális audit oldalkészlete kifejezett jelöléssel, a történeti tárolástól külön.
--
-- crawl_runs.crawl_id: melyik crawlhoz tartozik a futás: új crawlnál a saját `run_id`-je, a
--   folytatásnál (resume) a folytatott crawlé. A folytatott crawl így ugyanaz a crawl marad.
-- pages.seen_crawl_id: melyik crawl látta utoljára az oldalt (feldolgozta, vagy a változatlan
--   tartalom miatt kihagyta). Az aktuális készlet: amit a legutóbbi crawl látott. A többi sor
--   történet: megmarad (a blokkjaival és a tárolt kinyerésével együtt), de az elemzés nem
--   olvassa.
-- pages.x_robots_tag: a válasz `X-Robots-Tag` fejléce (kisbetűvel, a sorok vesszővel
--   összefűzve; '' ha nem volt). A kihagyás ezt is összeveti, hogy a változatlan HTML mellett
--   megjelenő `noindex` fejléc ne maradjon észrevétlen. A korábbi soroknál NULL (nem ismert).
--
-- A meglévő adatbázisok kitöltése: a folytató futás (a megjegyzése `resume:` kezdetű) az előtte
--   álló legutóbbi nem folytató futás crawlja; az aktuális készlet a crawl-sorban álló címek
--   oldalai (a sor minden új crawlnál kiürül); a sorban nem álló oldalé az a crawl, amelyik
--   utoljára írta.

ALTER TABLE crawl_runs ADD COLUMN IF NOT EXISTS crawl_id INTEGER;
ALTER TABLE pages ADD COLUMN IF NOT EXISTS seen_crawl_id INTEGER;
ALTER TABLE pages ADD COLUMN IF NOT EXISTS x_robots_tag VARCHAR;

UPDATE crawl_runs SET crawl_id = coalesce(
    (SELECT max(o.run_id) FROM crawl_runs o
      WHERE o.run_id < crawl_runs.run_id AND coalesce(o.notes, '') NOT LIKE 'resume:%'),
    run_id)
 WHERE crawl_id IS NULL AND coalesce(notes, '') LIKE 'resume:%';
UPDATE crawl_runs SET crawl_id = run_id WHERE crawl_id IS NULL;

UPDATE pages SET seen_crawl_id = (SELECT crawl_id FROM crawl_runs ORDER BY run_id DESC LIMIT 1)
 WHERE seen_crawl_id IS NULL AND url IN (SELECT url FROM crawl_queue);
UPDATE pages SET seen_crawl_id = (SELECT r.crawl_id FROM crawl_runs r WHERE r.run_id = pages.run_id)
 WHERE seen_crawl_id IS NULL AND run_id IS NOT NULL;
