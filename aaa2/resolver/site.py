"""Site-szintű entitások (M2 spec, „M2/6 — Site-szintű entitások”): az oldalankénti kinyerés
(szabálykör és LLM-kör) után, LLM-hívás nélkül.

- Oldalhoz kötött entitás (3. pont): minden entitásoldal-csoporthoz (`pages.entity_groups`)
  egy entitás. Nevei: a tagoldalak H1-e és title-je (a site-név nélkül, és az első elválasztó
  előtti része), az oldal saját JSON-LD csomópontjának neve (`name`, `headline`,
  `alternateName`), a más csoportból rá mutató anchorok (nem triviális, az anchor szövege az
  egész site-on csak ide mutat, legfeljebb `ANCHOR_MAX_WORDS` szó vagy egy tagoldal H1-e vagy
  title-je), a kártyacímek (az anchor
  blokkja előtti rövid heading, `_card_headings`); a nem elsődleges nyelvű
  tagoldal H1-e és title-je `hreflang` forrással. Kanonikus név: ajánlatnál és terméknél a
  leggyakoribb navigációs (nav, aside, footer) anchor az elsődleges nyelvű oldalra (azonos
  számnál a több szavú), különben a JSON-LD név, a title első része, a H1; komponensnél és
  cikknél a H1. Típus a szerepből (`pages.ROLE_TYPE`), ajánlatnál `tier = core`. Az azonos nevű
  (név vagy alias kulcsa) meglévő, kompatibilis típusú entitások ebbe olvadnak
  (`page_identity`); komponensnél a concept is kompatibilis (a típus ingadozik), ajánlatnál
  nem (a fogalom külön entitás). A JSON-LD név bármelyik oldal olyan csomópontjából jön, amely
  a tagoldalra mutat (`url` vagy `@id`). Említés: a tagoldalak title- és H1-blokkja, az
  anchorok blokkjai és a kártyacímek. Az a más típusú, azonos nevű entitás, amelynek minden
  blokkos említése ezekben az azonosító blokkokban áll, beolvad (`page_identity_position`).
- Csomag (4. pont): egy ajánlatoldal árazási sora (táblázatsor vagy kártya, ahol az árat
  tartalmazó blokk előtt legfeljebb `PRICE_LOOKBACK` blokkal rövid név áll; az óradíj-sorok
  kimaradnak) és az oldal saját `Service` csomópontjának `hasOfferCatalog` elemei:
  `tier = package`, `part_of` a fő ajánlathoz. A hreflang-pár oldalak azonos sorszámú árazási
  sora ugyanaz a csomag, ha a két oldal árazási sorainak száma egyezik
  (`hreflang_pricing_row`).
- Módszertani lépés (4. pont): az LLM-ből jött, fő ajánlathoz és csomaghoz nem kötött service
  → concept / method, `tier = step` (`type_changed_from = service`).
- Demótartalom (6. pont): nem-tech típusú entitás (`DEMO_TYPES`), amelynek blokkos említései
  legalább `DEMO_SHARE` részben demó-környezetben állnak: kódblokk, „lorem ipsum” szöveg,
  kitöltőszöveg-oldal (`placeholder.placeholder_pages`: a szövege döntően lorem ipsum), vagy
  olyan oldal, ahol ugyanennek az entitásnak kódblokkos említése is van (a példa kimenete).
  Jelölés: `flags` demo. A kitöltőszöveg-oldal oldalhoz kötött entitást sem hoz létre.
- Sablonismétlés (6. pont): az említés egysége (kódblokkban a sora, máshol a blokk szövege,
  kulcs szerint, blokkfajtánként) legalább `TEMPLATE_MIN_GROUPS` és az oldalcsoportok
  `TEMPLATE_MIN_SHARE` részén áll → `page_entities.flags` template; az entitás template, ha
  minden blokkos említése az. A title-blokk kimarad.
- Összevonás (7. pont), fuzzy nélkül: a hreflang-pár oldalak azonos helyű headingjei (a
  H2-szakaszok elölről és hátulról párban, amíg a H3-ak száma egyezik; a headinget egészében
  lefedő egyetlen említés entitása, azonos típus, vagy service és csak headingben álló
  concept: `hreflang_place`), és az írásmód-normalizált név (kis-nagybetű, ékezet, szóköz,
  aláhúzás, kötőjel, perjel, gondolatjel, zárójel nélkül, „&” = „és” = „and”; a zárójeles
  rövidítésből csak a hosszú kifejtés; „/” vagy „@” tartalmú névnél elválasztó-normalizálás
  nincs; szervezetnél a név végi jogi forma nélkül is, `LEGAL_FORMS`; azonos típus:
  `normalized_name`). Két különböző oldalhoz kötött entitás, két eltérő
  szint (core, package) és két nem kompatibilis altípus (package kontra component) nem olvad
  össze; a hreflang-párban a kanonikus nyelvű oldal entitása marad.
- Fogalom és ajánlat (9. pont): a service típusú entitás LLM-említései, amelyeket az LLM
  fogalomként talált (a tárolt rekordok szerint), fogalom-entitáshoz kerülnek (`type_split`),
  kivéve a heading-, title- vagy kártyablokkot egészében lefedő említést és az ajánlat
  valamelyik több szavas nevére szóló említést;
  a fő ajánlat és a csomag `offers` kapcsolattal kötődik ezekhez, és az olyan fogalomhoz,
  amelynek normalizált kulcsa a nevének vagy összetett címkéje egy részének kulcsa
  (`label_parts`: „UX & Konverzióoptimalizálás” → UX, Konverzióoptimalizálás).
- Webshop-szintek (9a pont, `shop.py`): kategória, márka, termékcsalád, a termék tulajdonságai
  és kapcsolatai, a csomagok után.
- Site-szintű felülbírálat (4. pont, `overrides.py`, `core/sites/<domain>.toml`): az
  ajánlat szintje (core, package, work_mode) név vagy URL szerint, a szintszabály után
  (`apply_overrides`). A kanonikus név nyelve a beállításé, különben a site gyökér-URL-jéé
  (`overrides.canonical_language`).
- Minden összevonás, leválasztás és felülbírálat a `merge_log`-ba kerül; futásonként egy
  `entity_runs` sor (method = site).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime

import duckdb

from aaa2.db.stable_json import dumps
from aaa2.entities import store
from aaa2.resolver.context import SiteRun, _Context
from aaa2.resolver.flags import _demo, _template
from aaa2.resolver.merge import Merger, _abbreviation_merges, _hreflang_place, _normalized_merges
from aaa2.resolver.names import _now
from aaa2.resolver.offers import (
    _offers,
    _packages,
    _page_entities,
    _steps,
    _type_split,
    apply_overrides,
)
from aaa2.resolver.overrides import canonical_language, load_site_config, site_domain
from aaa2.resolver.pages import (
    page_roles,
)
from aaa2.resolver.shop import run_shop


def run_site(con: duckdb.DuckDBPyConnection,
             clock: Callable[[], datetime] | None = None) -> SiteRun:
    clock = clock or _now
    started = clock()
    roles = page_roles(con)
    config = load_site_config(site_domain(con))
    site_lang = canonical_language(con, config)
    con.begin()
    try:
        (run_id,) = store.insert_entity_runs_in_run_site(con, started)
        run = SiteRun(run_id, dict(Counter(info.role for info in roles.values())))
        # a korábbi körök óta törölt entitásokra mutató kapcsolatok (újrafuttatáskor a
        # szabálykör az említés nélküli szabály-entitásokat törli)
        known = store.entity_ids(con)
        con.execute("DELETE FROM entity_relations WHERE NOT list_contains(CAST(? AS INTEGER[]), "
                    "from_id) OR NOT list_contains(CAST(? AS INTEGER[]), to_id)", [known, known])
        merger = Merger(con, run_id, clock)
        context = _Context(con, roles, site_lang, run_id)
        anchored = _page_entities(context, merger, run)
        _packages(context, merger, run, anchored)
        run.shop = run_shop(context, merger, config).as_dict()
        _hreflang_place(context, merger)
        _normalized_merges(con, merger)
        _abbreviation_merges(con, merger)
        split = _type_split(con, merger)
        _steps(con, run)
        _normalized_merges(con, merger)
        _abbreviation_merges(con, merger)
        run.overrides = apply_overrides(context, merger, config)
        run.offers = _offers(con, split)
        run.placeholder_pages = sorted(roles[p].url for p in context.placeholder if p in roles)
        _demo(con, run, context.placeholder)
        _template(context, run)
        run.merges = merger.counts
        store.update_entity_runs_in_run_site(con, clock(), len(roles), run.page_entities + run.packages, run.anchor_mentions, dumps({"roles": run.roles, "merges": dict(run.merges),
                         "packages": run.packages, "steps": run.steps, "offers": run.offers,
                         "overrides": run.overrides, "shop": run.shop,
                         "demo": run.demo, "placeholder_pages": run.placeholder_pages,
                         "template_mentions": run.template_mentions,
                         "template_entities": run.template_entities,
                         "thresholds": run.thresholds}, ensure_ascii=False), run_id)
        con.commit()
    except Exception:
        con.rollback()
        raise
    return run
