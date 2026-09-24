-- A site http→https 301/308-at ad a seedre. A frontier a crawl elején méri, és --resume-nál
-- innen olvassa vissza, hogy a sor URL-jei ugyanazzal a szabállyal normalizálódjanak.
ALTER TABLE site ADD COLUMN IF NOT EXISTS https_redirect BOOLEAN;
