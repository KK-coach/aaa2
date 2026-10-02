"""Feloldás (architektúra-spec, `resolve` modul): az oldalankénti kinyerés után, LLM nélkül.

- `pages`: oldalcsoportok és oldalszerepek;
- `site`: a site-kör (`run_site`) a lépések sorrendjével; a lépések: `offers` (oldalhoz kötött
  entitások, csomagok, lépések, fogalom és ajánlat, felülbírálat), `merge` (összevonás),
  `navigation` (anchorok és kártyacímek), `flags` (demó- és sablonjelölés), `shop`
  (webshop-szintek); közös: `names` (nevek és szövegek), `context` (a futás állapota);
- `overrides`: a site-fájl beállításai és felülbírálatai;
- `knowledge`, `validate`: tudásbázis-kapcsolás (Wikidata, Wikipedia, Knowledge Graph).
"""
