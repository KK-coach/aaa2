-- A seed http-változatára kapott válasz. A frontier a crawl elején méri.
-- https_redirect: 301/302/307/308 https-re ugyanazon a registrable domainen; a --resume innen
--   olvassa vissza, hogy a sor URL-jei ugyanazzal a szabállyal normalizálódjanak.
-- https_redirect_status: a kapott HTTP-státusz (átirányításnál a kódja, különben pl. 200);
--   NULL, ha a http-változat nem volt elérhető.
ALTER TABLE site ADD COLUMN IF NOT EXISTS https_redirect BOOLEAN;
ALTER TABLE site ADD COLUMN IF NOT EXISTS https_redirect_status INTEGER;
