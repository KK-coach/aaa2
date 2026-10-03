-- 026: a blokképítő verziója oldalanként.
--
-- blocks_built.builder_version: melyik blokképítő-verzióval (`dom.BLOCKS_VERSION`) épültek az
--   oldal blokkjai. NULL: a verziózás előtti (1-es) építő. Ha a tárolt verzió régebbi, a
--   `build_blocks` az oldal blokkjait memóriában újraépíti, és összeveti a tároltakkal: ha
--   azonosak, csak a verzió frissül (az említések és a tárolt kinyerés megmarad); ha eltérnek,
--   az oldal a megváltozott oldal útján épül újra (az említései és a bizonyítékai törlődnek).

ALTER TABLE blocks_built ADD COLUMN IF NOT EXISTS builder_version INTEGER;
