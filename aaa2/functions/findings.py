"""SEO-megállapítások és nézetek az entitásgráfból (M3 spec, „M3 — Entitásgráf”, 4. és 7. pont),
LLM nélkül, a gráf tábláiból (`graph.build_graph` után). Minden futás újraépíti a `findings`
táblát.

Megállapítások (`build_findings`), mindegyik bizonyítékkal és súlyossággal (high / medium / low);
a canonical-duplikátum oldal egyikben sem szerepel:

- `h1_title_mismatch`: az erős megbízhatóságú fő entitással bíró oldalon a fő entitás neve vagy
  aliasa nincs a H1-ben, illetve a title-ben (`names_in`: szókezdettől, a kötőszótól, az
  írásjelektől és az egybe- vagy különírástól függetlenül is, pl. „&” = „and”, „E-Privacy” =
  „Eprivacy”; a név rövid alakja is egyezés, ha a H1 vagy a title egy szelete legalább két
  szó, és minden szava a név szava; a title-ben a hosszkorlát miatt levágott név eleje is
  egyezés). Névnek nem számít az az alias, amely az oldal saját szövege: maga az oldal (vagy a
  hreflang-párja) H1-e, vagy csak H1- és title-forrása van (`entity_aliases.source`); így az
  összevetés nem körkörös (az M2 az oldalhoz kötött entitás aliasai közé az oldal H1-ét és
  title-szeleteit is felveszi). Személynél a kéttagú név mindkét sorrendje név. Az oldalhoz
  nem kötött fő entitásnál a gráf H1- és title-bizonyítéka (az M2 említése) is egyezés. Ha a
  H1-ben a fő entitás nincs meg, és más entitás említése sincs benne, a H1 általános. A
  kezdőoldalon, és ahol a fő entitás a site saját entitása (rólunk, karrier), csak a title
  számít (a H1-be nem kell a márkanév). Ha a H1 megnevezi a fő entitást, és a title a H1
  szövegét tartalmazza, a title is megnevezi. A fülek (pl. `?tab=api`) nem ismétlik. Az
  azonos okú (ugyanazok a hibák) és azonos szerepű oldalak egy megállapítást adnak, az
  érintett oldalak listájával. A H1 nélküli oldal itt csak a title miatt szerepelhet: a H1
  hiánya a `missing_h1` megállapítás. Súlyosság: high, ha a title-ből hiányzik; medium, ha
  csak a H1-ből.
- `cannibalization` és `shared_topic`: ugyanaz az erős megbízhatóságú fő entitás legalább két
  oldalcsoportban, egy nyelven belül. Egy csoport a hreflang-pár és a fül nélküli URL
  (`page_nodes.group_key`); különböző nyelvű oldalak nem alkotnak párt. Nem számít: a
  canonical-duplikátum, a lapozó oldal (`PAGINATION`), a nem indexelhető oldal (`noindex`), és
  a site saját entitása (a cégről több oldal is szólhat). Lehetséges kannibalizáció
  (`cannibalization`; jelölés, nem ítélet) akkor, ha a fő entitáson túl két oldal másodlagos
  entitásai is átfednek, vagy a title-jük nagyon hasonló (`title_similarity` ≥ `TITLE_SIMILAR`:
  a title szavainak Jaccard-hasonlósága; az utolsó szelet csak akkor marad el, ha a site
  azonos nyelvű oldalainak legalább `SUFFIX_SHARE` részén ismétlődik, `common_suffixes`). Minden
  oldalpárt összevetünk: a testvéroldalakat akkor is, ha van fölöttük témaközpont, és a
  témaközpontot a gyermekével is; az átfedés mellett áll a pár viszonya (`relation`:
  szülő–gyermek, testvér, más ág), a köztük lévő tartalmi (törzsbeli) linkek iránya
  (`content_links`), és hogy van-e köztük bármilyen belső link, a menüt és a láblécet is
  számítva (`any_link`). A megállapítás oldallistájában csak az
  átfedésben részt vevő oldalak állnak (a többi darabszáma: `other_pages`). Súlyosság: high, ha
  legalább `HIGH_GROUPS` oldal érintett, különben medium. Ha egy pár sem fed át: közös téma
  (`shared_topic`, low): az oldalak ugyanarról az entitásról szólnak, más-más szögből; itt a
  szülő–gyermek viszony nem számít (a gyermek kimarad, ha a szülője is a listában áll).
- `uncovered_topic` és `missing_page`: az entitás súlya a site felső tizedében van
  (`TOP_SHARE`), legalább `MIN_PAGES` oldalcsoport említi (a fülek, pl. `?tab=api`, és a
  hreflang-pár egy csoport), és egyik oldalnak sem fő entitása. További feltételek: a site
  maga kiemeli (legalább `MIN_STRUCTURAL`, `SMALL_SITE_GROUPS`-nál kevesebb oldalcsoportú
  site-on `SMALL_SITE_STRUCTURAL` oldalcsoportban áll title-ben, H1-ben vagy headingben, és
  összesen legalább `MIN_MENTIONS` említése van), és nincs lefedve: nem
  másodlagos entitása egy oldalnak sem, nem egy saját oldalú ajánlat fogalma (`offers` él), a
  neve nem egy oldal H1-e vagy URL-szakasza, és a nevének szavai nem egy fő entitás nevének
  szavai közül valók („Organic Growth” az „Organic Growth System” mellett; a szülő, pl. a
  termékcsalád a termékei mellett, ettől még nincs lefedve). Kimarad: a szervezet, a személy
  és a hely (`NO_PAGE_TYPES`), az API-szimbólum, a site saját entitása, az oldalhoz kötött
  entitás, és a kontextus-entitás (`is_context`): az említései legalább `CONTEXT_SHARE`
  részben sablon- vagy chrome-helyen állnak (title-sablon, menü, lábléc, oldalsáv), vagy a
  neve a site nevében szerepel (az Angular az „Angular Bootstrap” komponenstárban); a sok
  oldalon tárgyalt téma (pl. a GA4 egy mérési tanácsadó site-ján) nem kontextus. Ha egy
  hiányzó oldalú termékcsaládnak az alcsaládja is hiányzó oldal volna, az alcsalád a szülő
  megállapításában áll (`subfamilies`), nem külön. Nem lefedetlen téma az sem (`exclusion`),
  amit egy indexelhető oldal H1-e és title-je is megnevez (a szülő, pl. a termékcsalád
  kivételével; a rövid, más entitás nevében álló alias, pl. „AI”, nem megnevezés), és az
  egyszavas, kisbetűs köznévi fogalom („stratégia”, „reporting”). A termékcsalád (product / line, vagy termék, amelynek
  részei vannak) hiányzó oldal (`missing_page`: családoldal kell; high, ha legalább
  `HIGH_PAGES` oldalon szerepel, különben medium). Minden más lefedetlen téma
  (`uncovered_topic`): a teendő cikk, how-to vagy szakasz egy meglévő oldalon, nem
  feltétlenül új oldal; medium, ha legalább `HIGH_PAGES` oldalon szerepel, különben low. A
  további feltételek nélküli (szó szerinti) jelöltek száma: `FindingsRun.missing_literal`, a
  kontextus-entitások: `FindingsRun.context`.
- `unclear_topic`: az oldal fő entitása gyenge megbízhatóságú (medium), kivéve, ha a H1 és a
  title is megnevezi (ott a téma egyértelmű, csak más bizonyíték nincs); vagy az oldalnak
  egyetlen jelöltje sincs (high).
- `title_without_main_entity` (jelölt, low): a cím szövege nem nevezi meg az oldal fő
  entitását. A cím szövege tény, a fő entitás a gráf következtetése (`claim`); a megállapítás
  nem minősíti a címet. Az összevetés szigorú (`titles.names_whole`: teljes szó vagy
  kifejezés, az oldal nyelvén megengedett raggal; rövidebb név hosszabb névben nem egyezés), a
  title a site-utótag nélkül, a látható cím a tartalmi régió első címsora. A kinyerés tárolt
  title- és H1-említése is megnevezés; a látható címnél az oldal H1-e is számít, ha az nem a
  tartalmi régió első címsora. Esetek (`case`): ha sem a title, sem a látható cím nem nevezi
  meg a fő entitást: `no_entity` (a cím az oldalon említett entitások egyikét sem nevezi meg)
  vagy `other_entity` (más entitást nevez meg; `title_entities`: melyeket, az oldalhoz kötött
  és a site saját entitása nélkül; a hosszabb névben álló rövidebb helyett a hosszabb nevű
  áll; ha a megnevezettek mind mérőszámok, `metric` altípusúak, `title_metrics_only` igaz,
  és az összefoglaló ezt mondja: az eset ettől még `other_entity`); mindkettő csak cikk
  jellegű oldalon (`article_like`: `article` szerepű
  oldal; vagy segédfajta nélküli `support` oldal `og:type` = `article` jelöléssel és legalább
  `ARTICLE_MIN_WORDS` szóval); `title_only`: a title megnevezi, a látható cím nem, szereptől
  függetlenül; ha az oldalon már van `h1_title_mismatch`, nem külön megállapítás, hanem
  annak a bizonyítékában áll (`visible_title_note`). Kimarad a kezdőoldal, és ahol a fő
  entitás a site saját entitása. Az azonos
  esetű és szerepű oldalak egy megállapítást adnak.
- `article_markup_on_other_pages` (medium, egy megállapítás a site-ra): a site cikk-jelölést
  (`titles.ARTICLE_TYPES`: Article, BlogPosting, NewsArticle, TechArticle legfelső szintű
  csomópont) tesz olyan oldalakra is, amelyek nem cikkek. Feltétel: a HTML oldalak (az
  oldal-csomópontok a canonical-duplikátumok nélkül) legalább `ARTICLE_MARKUP_SHARE` részén
  áll cikk típusú jelölés (a típusok együtt számítanak), és a jelölt oldalak között van
  biztosan nem cikk: ajánlat-, termék- vagy kategória-szerepű oldal; rólunk- vagy
  karrieroldal (`ABOUT_URL_WORDS` az URL-ben, és a fő entitás a site saját entitása);
  kapcsolatoldal (a `contact` segédfajta); a kezdőoldal. A bizonyíték a teljes oldallista:
  `not_articles` (fajtánként; oldalanként az URL, a cikk típusú jelölés és a mai szerep) és
  `maybe_articles` (a többi cikk-jelölésű oldal), a számokkal (`html_pages`, `marked`,
  `share`, `by_type`, `counts`). A szerepet nem változtatja; a jelölési hibát mondja ki.
- `breadcrumb_foreign_home` (low): a morzsa első eleme egy másik nyelv kezdőoldalára mutat
  (`site.home_urls`: a kezdőoldal és a nyelvi párjai), miközben az oldal nyelvének is van
  kezdőoldala. Egy megállapítás az oldal nyelve és a megcélzott kezdőoldal szerint, a
  bizonyítékban oldalanként az URL, a morzsa első elemének címe és szövege.
- `menu_home_target` (medium): a nyelv fejléc-fájának „Home” / „Főoldal” / „Kezdőlap” horgonyú
  (`HOME_ANCHORS`) vagy kezdőoldal-ikonnal jelölt menüpontja nem kezdőoldalra mutat. A logólink
  külön áll a bizonyítékban: ha a logó a kezdőoldalra mutat, a menüpont pedig máshova, a
  site-nak két kezdőoldala van (`two_homes`).
- `menu_broken_target` (medium): a site-szintű menüfa (fejléc, lábléc, oldalsáv) egy
  menüpontja hibás oldalra mutat: 200-as státuszú „nem található” oldalra (`page_types`:
  not_found), hibás státuszú (4xx / 5xx, be nem töltött) oldalra, vagy átirányító címre (a
  végső cím hostja vagy útvonala más; a csak lekérdezésben eltérő végső cím, pl. a végtelen
  görgetés `?infinite_page=2`-je, nem átirányítás). A noindex cél nem megállapítás (gyakran
  szándékos: adatvédelmi központ, oldaltérkép, akciós lista); tényként áll a menü-nézetben
  („a cél noindex”). Egy megállapítás célonként; a bizonyítékban a menüpontok
  (horgonyszöveg, terület, nyelv, hány oldalon áll), a cél címe és a hiba fajtája. A készletben
  nem tárolt cél és a nem HTML válasz nem számít.
- `orphan_pages`: árva oldalak, két külön megállapításban. `sitemap_only`: az oldal a saját
  nyelvű kezdőoldalról belső linken nem érhető el, belső link nem mutat rá, és a sitemapben
  szerepel (az oldalnézet „csak sitemapből ismert” oszlopa). `linked_only_by_orphans`: van rá
  belső link, de csak más, szintén elérhetetlen oldalakról. Súlyosság a szerep szerint: medium,
  ha a csoportban van ajánlat-, termék-, kategória- vagy hub-oldal, különben low. A szöveg
  tényt mond (nem érhető el belső linken), nem szándékot. Részleges bejárásnál
  (`crawl.completeness`: megállt bejárás vagy sitemap-mód) a megállapítás részlegesként
  jelölt: a rá vezető oldal kimaradhatott a készletből. A bizonyítékban oldalanként az URL, a
  szerep, a fő entitás, az indexelhetőség és a szószám.
- `breadcrumb_ignores_menu` (low, site-szintű): a fejléc-fa almenüiben (1. vagy mélyebb
  szint) álló oldalak legalább `CRUMB_FLAT_SHARE` részénél, és legalább `CRUMB_FLAT_MIN`
  oldalnál a morzsa csak „kezdőoldal > oldal”, a menü-szülő nincs benne. A bizonyítékban
  oldalanként a menü-szülő és a morzsa lánca.
- `link_not_final_url`: belső link nem a végleges címre mutat. A link eredeti címe
  (`links.raw_url`) a saját hoston záró perjelben, kis/nagybetűben, protokollban vagy `www`-ben
  eltér a tárolt céltól (`normalize.form_differences`), vagy a cél átirányít, és a végső cím
  hostja vagy útvonala más (a `menu_broken_target` szabálya; a csak lekérdezésben eltérő végső
  cím nem számít). A site saját hostjától (a kiinduló cím hostja, a `www`-től eltekintve)
  eltérő hostra mutató link, a gyökér üres útvonala és az eredeti cím nélküli
  linksor nem tartozik ide; az a menülink, amelynek az átirányítását a `menu_broken_target`
  jelzi, nem ismétlődik. Egy megállapítás (eredeti cím → cél) mintánként, a forrásoldalak
  számával és területenként; a bizonyítékban a forrásoldalak a területtel és a horgonnyal, a
  linkelt alak státusza és ugrásszáma a `link_variants` mérése szerint (ahol nincs: nem mért,
  és a végleges cím helyén a tárolt oldal címe áll). Súlyosság: medium, ha a link menüben,
  láblécben vagy oldalsávban áll, vagy legalább `LINK_FORM_MANY_PAGES` forrásoldalon; különben
  low.
- A heading-fa szerkezeti megállapításai (`headings.structure_findings`): `missing_h1`,
  `h1_outside_content`, `multiple_h1`, `empty_section`, `skipped_level`, `missing_h2`; a
  szabályaik a `functions/headings.py` leírásában.

Hub-címke (`_Site.hubs`; címke, nem szerep és nem megállapítás), oldalanként legfeljebb egy:

- `menü-hub`: az oldal menüpontja alatt legalább `HUB_MIN_MENU_CHILDREN` gyerek-menüpont áll
  a tárolt site-szintű menüfa fejléc-területén (`structure.menu_items`), és az oldal
  tartalmából legalább `HUB_MIN_CONTENT_CHILDREN` tartalmi link mutat
  ezekre a gyerekekre. A kezdőoldal nem hub.
- `tartalmi hub`: nem menü-hub; a tartalmából legalább `HUB_MIN_CONTENT_TARGETS` tartalmi link
  mutat azonos szerepű oldalakra, amelyek a tartalmukból visszalinkelnek rá, és ezek szerepe
  más, mint az oldalé (az egymásra kölcsönösen linkelő testvéroldalak, pl. ajánlat ↔ ajánlat,
  termék ↔ termék, nem hubok).
- `kategória-hub`: kategória-szerepű oldal, amely alatt alkategóriák állnak (menügyerek, vagy
  a tartalmából linkelt kategóriaoldal), vagy a tartalmából legalább `HUB_MIN_PRODUCTS`
  termékoldalra mutat link.

A gyerekek (`children`): oldalanként az URL, a szerep, honnan ismert (menü / tartalom /
mindkettő), és visszalinkel-e a tartalmából a hubra; mellette, hány gyerekre mutat a hub
tartalma (`content_linked`) és hány linkel vissza (`linking_back`).

Belső struktúra (`_Site.structure`, `functions/structure.py`): oldalanként a kattintási mélység
a saját nyelvű kezdőoldaltól (minden link / csak menü / csak tartalom), a menüszint, a morzsa-
és az URL-szint, a szülő a menüfa, a morzsa és az URL szerint, és hogy egyeznek-e; site-szinten
a menüfa nyelvenként és területenként, a mélység-eloszlás és a kezdőoldalról elérhetetlen
oldalak (`structure_view`).

Nézetek (`export_views`), ellenőrzéshez: site-áttekintő (a legfontosabb entitások típus szerint,
a szülővel, a kategóriával és a fő oldalaikkal; mellette a belső struktúra), entitás környezete (az entitás élei egy
lépésnyire, a fő és a csak említő oldalai), oldalnézet (a fő entitás a bizonyítékaival, a H1 és a
title összevetése, a címmezők tényként (`titles.title_fields`: a title utótag nélkül, a
látható cím az elemével, a H1-ek száma, og:title, schema name / headline, az eltérő mezők; a
schema about külön, azzal, hogy mire mutat és azonos-e a fő entitással), a további említett
entitások, az oldal megállapításai, a heading-fa),
heading-fa (oldalanként a headingek a szintjükkel, az entitásaikkal, a fő entitáshoz fűződő
kapcsolatukkal és a szakaszuk szószámával); CSV-ben és egy lenyitható HTML-oldalon
(`<név>-views.html`).
"""
from __future__ import annotations

import csv
import html
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import duckdb

from aaa2.contracts import (
    EntityView,
    Finding,
    FindingView,
    PageView,
    SiteStructureView,
    SiteViews,
)
from aaa2.db.stable_json import dumps
from aaa2.engine import queries as crawl
from aaa2.engine.normalize import form_differences
from aaa2.entities import queries as extract_queries
from aaa2.entities import store
from aaa2.entities.gate import occurs
from aaa2.entities.rules import alias_key
from aaa2.functions import graph_queries, titles
from aaa2.functions import headings as heading_tree
from aaa2.functions import structure as site_structure
from aaa2.functions.graph import _walk, evidence_text, url_has_word
from aaa2.resolver import queries as resolver_queries
from aaa2.resolver.display import NAME_NOTE_COLUMN, OTHER_NAMES_COLUMN, DisplayNames
from aaa2.resolver.names import normal_key
from aaa2.resolver.overrides import load_site_config, site_domain
from aaa2.resolver.pages import LEGAL_KINDS, canonical_key, legal_kind, page_types

TYPES = ("h1_title_mismatch", "cannibalization", "shared_topic", "missing_page",
         "uncovered_topic", "unclear_topic", *heading_tree.STRUCTURE_TYPES,
         "canonical_issue", "legal_page", "soft_404", "schema_id_names",
         "title_without_main_entity", "article_markup_on_other_pages",
         "breadcrumb_foreign_home", "menu_home_target", "menu_broken_target",
         "orphan_pages", "breadcrumb_ignores_menu", "link_not_final_url")
TYPE_LABELS = {"h1_title_mismatch": "H1/title-eltérés",
               "cannibalization": "Lehetséges kannibalizáció",
               "shared_topic": "Közös téma", "missing_page": "Hiányzó oldal",
               "uncovered_topic": "Lefedetlen téma", "unclear_topic": "Nem egyértelmű téma",
               "missing_h1": "Hiányzó H1", "h1_outside_content": "H1 a fő tartalmon kívül",
               "multiple_h1": "Több H1", "empty_section": "Üres szakasz",
               "skipped_level": "Kihagyott heading-szint", "missing_h2": "Hiányzó H2",
               "paragraph_heading": "Bekezdés headingként jelölve",
               "schema_id_names": "Azonosító több névvel a strukturált adatban",
               "title_without_main_entity": "A cím nem nevezi meg a fő entitást",
               "article_markup_on_other_pages": "Cikk-jelölés nem cikk oldalakon",
               "breadcrumb_foreign_home": "A morzsa kezdőpontja más nyelvű kezdőoldalra mutat",
               "menu_home_target": "A menü Home pontja nem a kezdőoldalra mutat",
               "menu_broken_target": "A menü hibás oldalra mutat",
               "orphan_pages": "Árva oldal",
               "breadcrumb_ignores_menu": "A morzsa nem követi a menü hierarchiáját",
               "link_not_final_url": "Belső link nem a végleges címre mutat",
               "canonical_issue": "Hibás canonical", "legal_page": "Jogi oldal",
               "soft_404": "Nem található oldal 200-as státusszal"}
# többes szám jele a kulcsban (ékezet nélkül): a kategória neve és a H1 / title összevetéséhez
PLURAL_ENDINGS = frozenset({"k", "ok", "ek", "ak", "s", "es"})
CONTENT_WORD_CHARS = 3                  # ennél rövidebb szó nem tartalmas
FILLER_WORDS = frozenset({"the", "for", "egy", "csak", "meg", "nem", "mint"})
# a jogi fajták, amelyek jellemzően az ÁSZF részei: ha az ÁSZF szövege külső keretben
# (iframe) áll, a hiányuk nem állapítható meg
EMBEDDED_KINDS = frozenset({"withdrawal", "shipping_payment", "warranty"})
# a jogi oldalak fajtái a meglét-ellenőrzéshez: megnevezés, és a szavak, amelyekkel egy jogi
# oldal headingje a fajtát megnevezi (ha nincs külön oldala, de egy jogi oldalon belül megvan)
LEGAL_LABELS = {"terms": "ÁSZF", "privacy": "adatvédelem", "withdrawal": "elállás",
                "shipping_payment": "szállítás és fizetés", "warranty": "garancia",
                "contact": "kapcsolat / impresszum"}
LEGAL_HEADING_WORDS = {
    "terms": ("szerzodesi feltetel", "aszf", "terms"),
    "privacy": ("adatved", "adatkezel", "privacy"),
    "withdrawal": ("elallas", "withdrawal", "return"),
    "shipping_payment": ("szallitas", "fizetes", "shipping", "payment", "delivery"),
    "warranty": ("garancia", "jotallas", "szavatossag", "warranty"),
    "contact": ("kapcsolat", "elerhetoseg", "contact", "impresszum")}
LEGAL_TITLE_WORDS = 6                   # ennél nem hosszabb bekezdés címként álló sor lehet
CANONICAL_LABELS = {"not_crawled": "a cél nincs a készletben", "error_status": "a cél hibás",
                    "not_a_node": "a cél nem oldal-csomópont", "loop": "körbeérő lánc",
                    "other_type": "a cél más típusú oldal"}
QUANTITY = re.compile(r"\d+(?:[.,]\d+)?(?:[*x]\d+)?\s?(?:kg|ml|dl|cl|db|cm|mm|g|l|m)\b")
RELATION_LABELS = {"main": "fő entitás", "related": "kapcsolódik", "unrelated": "független",
                   "no_entity": "nincs benne entitás",
                   "no_main": "az oldalnak nincs fő entitása"}
ROLE_LABELS = {"offer": "ajánlatoldal", "article": "cikkoldal", "product": "termékoldal",
               "component": "komponensoldal", "category": "kategóriaoldal",
               "listing": "listaoldal",
               "profile": "profiloldal", "home": "kezdőoldal", "support": "egyéb oldal"}
ACTIONS = {"missing_page": "családoldal a termékcsaládnak",
           "uncovered_topic": "cikk, how-to vagy szakasz egy meglévő oldalon (topical "
                              "authority); nem feltétlenül új oldal"}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
OWN_TEXT_SOURCES = frozenset({"h1", "title"})
PAGINATION = re.compile(r"(?:[?&](?:page|paged|p|infinite_page|oldal)=\d+)|(?:/page/\d+/?$)",
                        re.IGNORECASE)
NO_PAGE_TYPES = frozenset({"org", "person", "place"})
NO_PAGE_SUBTYPES = frozenset({"api_symbol"})
TOP_SHARE = 0.1
MIN_PAGES = 3
MIN_STRUCTURAL = 3
SMALL_SITE_GROUPS = 50                  # ennél kevesebb oldalcsoportú site-on …
SMALL_SITE_STRUCTURAL = 2               # … ennyi csoportban elég a kiemelés
# ilyen szerepű oldalon a H1 vagy a title önmagában is megnevezés (az oldal egy entitásé)
NAMING_ALONE_ROLES = ("offer", "product", "component", "category")
SHORT_ALIAS_CHARS = 3                   # ennél nem hosszabb alias gyenge megnevezés
MIN_MENTIONS = 6
HIGH_PAGES = 10
HIGH_GROUPS = 3
CONTEXT_SHARE = 0.5                     # az említések ekkora része sablon vagy chrome
TITLE_SIMILAR = 0.6
SUFFIX_SHARE = 0.5              # a title utolsó szelete ennyi oldalon ismétlődve site-utótag
CONTENT_LINK = "body"           # a tartalmi link pozíciója (`Link.position`)
TITLE_SPLIT = re.compile(r"\s+[-–—|·:]\s+")
CONJUNCTIONS = frozenset({"and", "es"})
CUT_MIN_CHARS = 20                      # a levágott title-ben a névnek legalább ennyi jele áll
CUT_MIN_SHARE = 0.6
OVERVIEW_TOP = 100                      # a site-áttekintő ennyi legnagyobb súlyú entitást mutat
PAGE_MENTIONS = 10                      # az oldalnézet ennyi további említett entitást sorol
EVIDENCE_PAGES = 5
# ilyen szerepű oldal miatt közepes az árva oldalak megállapítása (a hub-címke is ilyen)
ORPHAN_KEY_ROLES = ("offer", "product", "category")
ORPHAN_GROUPS = {
    "sitemap_only": "belső link nem mutat rájuk, a sitemapben szerepelnek",
    "linked_only_by_orphans": "csak egymásra linkelnek (más, szintén elérhetetlen oldalakról "
                              "kapnak linket)"}
ORPHAN_PARTIAL = ("részleges bejárás: a rájuk vezető oldal kimaradhatott a készletből")
LINK_FORM_MANY_PAGES = 10               # ennyi forrásoldaltól közepes a linkforma-megállapítás
LINK_AREAS = {"nav": "menü", "body": "tartalom", "footer": "lábléc", "aside": "oldalsáv"}
LINK_REDIRECT = "átirányítás"
LINK_NOT_MEASURED = "nem mért"
LINK_STORED_NOTE = "a tárolt oldal címe"
CRUMB_FLAT_SHARE = 0.5                  # az almenüben álló oldalak ekkora részénél lapos a morzsa
CRUMB_FLAT_MIN = 3
MENU_TARGET_PROBLEMS = {"not_found": "200-as státuszú „nem található” oldal",
                        "error_status": "hibás státusz", "redirect": "átirányít"}
# a kezdőoldalt megnevező menüpont horgonyszövege (`alias_key` alakban)
HOME_ANCHORS = frozenset({"home", "homepage", "home page", "fooldal", "kezdolap", "kezdooldal",
                          "nyitolap", "nyitooldal", "cimlap"})
HUB_MIN_MENU_CHILDREN = 2
HUB_MIN_CONTENT_CHILDREN = 2
HUB_MIN_CONTENT_TARGETS = 3
HUB_MIN_PRODUCTS = 3
HUB_SOURCES = {(True, True): "mindkettő", (True, False): "menü", (False, True): "tartalom"}
ARTICLE_MARKUP_SHARE = 0.6              # a HTML oldalak ekkora részén áll cikk-jelölés
ABOUT_URL_WORDS = ("about", "rolunk", "rolam", "career", "karrier", "allas", "jobs")
NOT_ARTICLE_LABELS = {"offer": "ajánlat / termék / kategória", "about": "rólunk / karrier",
                      "contact": "kapcsolat", "home": "kezdőoldal"}
ARTICLE_MIN_WORDS = 300                 # a jelölés alapján cikknek vett egyéb oldal alsó hossza
TITLE_CASES = {"no_entity": "sem a title, sem a látható cím nem nevez meg entitást",
               "other_entity": "a cím más entitást nevez meg, mint a fő entitás",
               "title_only": "a title tartalmazza a fő entitás nevét, a látható cím nem"}
TITLE_METRICS = "a cím mérőszámot nevez meg, a fő entitás más"
VISIBLE_TITLE_NOTE = "a látható cím nem nevezi meg a fő entitást, a title igen"
TITLE_CLAIM = {"fact": "a title és a látható cím szövege (tárolt adat)",
               "inference": "az oldal fő entitása (a gráf döntése)"}


@dataclass
class FindingsRun:
    counts: Counter = field(default_factory=Counter)          # (típus, súlyosság) → darab
    missing_literal: int = 0             # lefedetlen-jelöltek a kiemelés és lefedés nélkül
    context: list[str] = field(default_factory=list)          # kizárt kontextus-entitások

    def by_type(self) -> dict[str, int]:
        found: Counter = Counter()
        for (kind, _), count in self.counts.items():
            found[kind] += count
        return dict(found)


# ---------------------------------------------------------------------------
# megállapítások
# ---------------------------------------------------------------------------


def clear_findings(con: duckdb.DuckDBPyConnection) -> None:
    """A megállapítások törlése (az entitások újraépítése után az azonosítók mások)."""
    con.execute("DELETE FROM findings")


def build_findings(con: duckdb.DuckDBPyConnection) -> FindingsRun:
    """A `findings` tábla újraépítése a gráf tábláiból (lásd a modul leírását)."""
    con.execute("DELETE FROM findings")
    site = _Site(con)
    run = FindingsRun()
    mismatches = _mismatches(site)
    rows = [*mismatches, *_shared_topics(site), *_uncovered(site, run),
            *_unclear_topics(site),
            *heading_tree.structure_findings(site.pages, site.headings, ROLE_LABELS),
            *_canonical_issues(site), *_legal_pages(site), *_soft_404(site),
            *_schema_id_names(site), *_title_naming(site, mismatches),
            *_article_markup(site), *_breadcrumb_home(site), *_menu_home(site)]
    menu_targets = _menu_targets(site)
    rows += [*menu_targets, *_orphans(site), *_breadcrumb_menu(site),
             *_link_forms(site, menu_targets)]
    rows.sort(key=lambda r: (TYPES.index(r[0]), SEVERITY_ORDER[r[1]], r[4]))
    for number, (kind, severity, page_id, entity_id, summary, evidence) in enumerate(rows, 1):
        con.execute("INSERT INTO findings (finding_id, type, severity, page_id, entity_id, "
                    "summary, evidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [number, kind, severity, page_id, entity_id, summary,
                     dumps(evidence, ensure_ascii=False)])
        run.counts[(kind, severity)] += 1
    return run


class _Site:
    """A gráf táblái a megállapításokhoz és a nézetekhez."""

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con
        noindex = {page.page_id: page.noindex for page in crawl.pages(con)}
        self.pages = {row[0]: dict(zip(
            ("page_id", "url", "role", "support", "title", "h1", "lang", "group", "status",
             "canonical", "issue", "signals", "issue_detail", "noindex"),
            (*row, bool(noindex[row[0]])), strict=True))
            for row in [(n.page_id, n.url, n.role, n.support_kind, n.title, n.h1, n.lang,
                         n.group_key, n.main_status, n.canonical_page, n.canonical_issue,
                         (n.decision or {}).get("signals") or {},
                         (n.decision or {}).get("canonical_issue") or {})
                        for n in sorted(graph_queries.page_nodes(con), key=lambda n: n.url)]
            if row[0] in noindex}
        self.lower_forms = {entity_id for (entity_id,) in store.lowercase_word_entities(con)}
        self.entities = {row[0]: dict(zip(
            ("entity_id", "name", "type", "subtype", "aliases", "anchor", "role", "source"),
            row, strict=True)) for row in store.entities_for_site___init__(con)}
        self.alias_sources: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for entity_id, alias, source in [(a.entity_id, a.alias, a.source)
                                         for a in resolver_queries.aliases(con)]:
            self.alias_sources[entity_id][alias].add(source)
        self.chosen: dict[int, list[tuple]] = defaultdict(list)     # oldal → (entitás, szerep, …)
        for chosen in graph_queries.main_entities(con):
            self.chosen[chosen.page_id].append(
                (chosen.entity_id, chosen.role, chosen.confidence, chosen.evidence))
        self.weights = {row[0]: dict(zip(
            ("entity_id", "pages", "mentions", "structural", "main_pages", "secondary_pages",
             "content_anchors", "nav_anchors", "weight"), row, strict=True))
            for row in [(w.entity_id, w.pages, w.mentions, w.structural, w.main_pages,
                         w.secondary_pages, w.content_anchors, w.nav_anchors, w.weight)
                        for w in graph_queries.entity_weights(con)]}
        ranked = sorted(self.weights.values(), key=lambda w: (-w["weight"], w["entity_id"]))
        self.rank = {w["entity_id"]: i for i, w in enumerate(ranked, start=1)}
        self.site_entities = {entity_id for (entity_id,) in store.site_entity_ids(
            con, resolver_queries.relation_from_ids(con, "brand_of"))}
        self.mention_edges: dict[int, list[tuple[int, float, dict]]] = defaultdict(list)
        for edge in sorted(graph_queries.edges(con, "mentions"),
                           key=lambda e: (e.from_id, e.weight is None, -(e.weight or 0.0),
                                          e.to_id)):
            self.mention_edges[edge.from_id].append(
                (edge.to_id, edge.weight or 0.0, edge.evidence))
        self._headings: dict[int, dict] | None = None
        self._titles: dict[int, titles.TitleFields] | None = None
        self._forms: dict[str, list[int]] | None = None
        self._by_key: dict[str, int] | None = None
        self._hubs: dict[int, dict] | None = None
        self._structure: site_structure.SiteStructure | None = None
        self.display = DisplayNames(con)

    @property
    def structure(self) -> site_structure.SiteStructure:
        """Az oldalak mélységei és szülői, a tárolt menüfával (`structure.page_structure`)."""
        if self._structure is None:
            self._structure = site_structure.page_structure(self.con, self.pages)
        return self._structure

    def page_of(self, url: str | None) -> int | None:
        """A cím oldala a készletben (a canonical-duplikátum helyett az eredeti), vagy None."""
        if not url:
            return None
        if self._by_key is None:
            self._by_key = {canonical_key(page["url"]): page["canonical"] or page["page_id"]
                            for page in self.pages.values()}
        return self._by_key.get(canonical_key(url))

    @property
    def headings(self) -> dict[int, dict]:
        """Oldalanként a heading-fa és a szerkezeti tények (`headings.page_headings`)."""
        if self._headings is None:
            self._headings = heading_tree.page_headings(
                self.con, self.pages, self.chosen,
                {entity_id: entity["name"] for entity_id, entity in self.entities.items()})
        return self._headings

    @property
    def title_fields(self) -> dict[int, titles.TitleFields]:
        """Oldalanként a címmezők (`titles.title_fields`); az `about` elemei feloldva: mire
        mutat (`entity_id`, `entity`, `type`), és azonos-e az oldal fő entitásával
        (`same_as_main`; None, ha nem oldható fel, vagy az oldalnak nincs fő entitása)."""
        if self._titles is None:
            suffixes = titles.suffixes_by_language(
                ((page["lang"], page["title"]) for page in self.nodes()), common_suffixes)
            counts = {page_id: len(data["h1"]) + len(data["outside_h1"])
                      for page_id, data in self.headings.items()}
            self._titles = titles.title_fields(self.con, self.pages, suffixes, TITLE_SPLIT,
                                               counts)
            for page_id, fields in self._titles.items():
                page, main = self.pages[page_id], self.main(page_id)
                for item in fields.about:
                    target = self.about_target(item, page)
                    item.update({
                        "entity_id": target,
                        "entity": self.shown(target, page) if target is not None else None,
                        "type": self.kind(target) if target is not None else None,
                        "same_as_main": None if target is None or main is None
                        else target == main[0]})
        return self._titles

    def about_target(self, item: Mapping, page: Mapping) -> int | None:
        """A schema `about` értékének entitása, ha egyértelmű: a hivatkozásnak egy neve van (a
        saját neve, vagy név nélküli `@id`-nél a csomópontjainak egyetlen neve; a kis- és
        nagybetű, az írásjelek eltérése nem számít), és ez a név pontosan egy entitás neve vagy
        aliasa. Különben None (nem oldható fel): a több nevű `@id` és a több entitáshoz illő
        név nem dől el sorrend alapján."""
        if self._forms is None:
            self._forms = defaultdict(list)
            for entity_id, entity in self.entities.items():
                for form in dict.fromkeys([entity["name"], *(entity["aliases"] or [])]):
                    if form and alias_key(form):
                        self._forms[alias_key(form)].append(entity_id)
        names = [item["name"]] if item.get("name") else item.get("names") or []
        keys = {alias_key(name) for name in names} - {""}
        if len(keys) != 1:
            return None
        pool = set(self._forms.get(next(iter(keys)), []))
        return next(iter(pool)) if len(pool) == 1 else None

    @property
    def hubs(self) -> dict[int, dict]:
        """Oldal → hub-címke a gyerekeivel (lásd a modul leírását): `label`, `children`
        ({`url`, `role`, `source`, `links_back`}, URL szerint), `content_linked`,
        `linking_back`. A hub nélküli oldal nincs benne."""
        if self._hubs is not None:
            return self._hubs
        nodes = {page["page_id"]: page for page in self.nodes()}
        items = {item.item_id: item for item in self.structure.menu}
        menu: dict[int, set[int]] = defaultdict(set)
        for item in items.values():
            parent = items.get(item.parent_id) if item.parent_id is not None else None
            if item.area == "header" and parent is not None and item.page_id in nodes \
                    and parent.page_id in nodes and item.page_id != parent.page_id:
                menu[parent.page_id].add(item.page_id)
        body: dict[int, set[int]] = defaultdict(set)
        for link in crawl.counted_links(self.con):
            target = link.to_page_id
            if link.position == CONTENT_LINK and target is not None:
                target = self.pages[target]["canonical"] or target if target in self.pages \
                    else target
                if link.from_page_id in nodes and target in nodes \
                        and target != link.from_page_id:
                    body[link.from_page_id].add(target)

        def role(page_id: int) -> str:
            page = nodes[page_id]
            return page["role"] + (f" ({page['support']})" if page["support"] else "")

        found: dict[int, dict] = {}
        for page_id, page in nodes.items():
            if page["role"] == "home":
                continue
            in_menu, linked = menu.get(page_id, set()), body.get(page_id, set())
            label, children = None, set()
            if page["role"] == "category":
                sub = in_menu | {t for t in linked if nodes[t]["role"] == "category"}
                products = {t for t in linked if nodes[t]["role"] == "product"}
                if sub or len(products) >= HUB_MIN_PRODUCTS:
                    label, children = "kategória-hub", sub | products
            elif len(in_menu) >= HUB_MIN_MENU_CHILDREN \
                    and len(in_menu & linked) >= HUB_MIN_CONTENT_CHILDREN:
                label, children = "menü-hub", set(in_menu)
            if label is None and page["role"] != "category":
                back: dict[str, set[int]] = defaultdict(set)
                for target in linked:
                    if page_id in body.get(target, set()) and role(target) != role(page_id):
                        back[role(target)].add(target)
                best = max(back.items(), key=lambda item: (len(item[1]), item[0]),
                           default=(None, set()))
                if len(best[1]) >= HUB_MIN_CONTENT_TARGETS:
                    label, children = "tartalmi hub", set(best[1])
            if label is None:
                continue
            rows = sorted(({
                "url": nodes[child]["url"], "role": role(child),
                "source": HUB_SOURCES[(child in in_menu, child in linked)],
                "links_back": page_id in body.get(child, set())} for child in children),
                key=lambda item: item["url"])
            found[page_id] = {
                "label": label, "children": rows,
                "content_linked": sum(1 for child in children if child in linked),
                "linking_back": sum(1 for item in rows if item["links_back"])}
        self._hubs = found
        return found

    def title_entities(self, page: Mapping, main_id: int, fields: titles.TitleFields
                       ) -> list[dict]:
        """Az oldalon említett entitások, amelyeket a title vagy a látható cím megnevez (a
        szigorú összevetéssel), a fő entitás, az oldalhoz vagy a nyelvi párjához kötött
        entitás és a site saját entitása nélkül. Rövidebb név hosszabb névben itt sem egyezés:
        a hosszabb nevű entitás áll a listán."""
        members = {p["page_id"] for p in self.pages.values() if p["group"] == page["group"]}
        forms: dict[int, list[str]] = {}
        for other, _, _ in self.mention_edges[page["page_id"]]:
            if other == main_id or other not in self.entities or other in self.site_entities \
                    or self.entities[other]["anchor"] in members:
                continue
            forms[other] = list(dict.fromkeys([*self.naming_forms(other, dict(page)),
                                               self.shown(other, page)]))
        found = []
        for other, own in forms.items():
            longer = [form for entity, names in forms.items() if entity != other
                      for form in names]
            where = [label for label, text in (("title", fields.title),
                                               ("látható cím", fields.visible))
                     if titles.names_whole(own, text, page["lang"], longer)]
            if where:
                # azonosító nélkül: az entitások azonosítói újraépítésenként mások
                found.append({"entity": self.shown(other, page), "type": self.kind(other),
                              "in": where})
        return sorted(found, key=lambda item: (item["entity"].lower(), item["entity"],
                                               item["type"]))

    def other_forms(self, page: Mapping, entity_id: int) -> list[str]:
        """Az oldalon említett többi entitás megnevezései (az oldalhoz vagy a nyelvi párjához
        kötött entitás nélkül): a szigorú összevetésben a hosszabb nevek."""
        members = {p["page_id"] for p in self.pages.values() if p["group"] == page["group"]}
        found: list[str] = []
        for other, _, _ in self.mention_edges[page["page_id"]]:
            if other != entity_id and other in self.entities \
                    and self.entities[other]["anchor"] not in members:
                found += self.naming_forms(other, dict(page))
        return list(dict.fromkeys(found))

    def name(self, entity_id: int) -> str:
        return self.entities[entity_id]["name"]

    def shown(self, entity_id: int, page: Mapping) -> str:
        """Az entitás neve az oldal kimenetében: az oldal nyelvén, ha van ilyen neve a nyelvi
        összevonásból (`display.DisplayNames.on_page`), különben a megtartott név."""
        return self.display.on_page(entity_id, self.name(entity_id), page["lang"])

    def kind(self, entity_id: int) -> str:
        entity = self.entities[entity_id]
        return "/".join(filter(None, (entity["type"], entity["subtype"])))

    def main(self, page_id: int) -> tuple | None:
        return next((c for c in self.chosen.get(page_id, []) if c[1] == "main"), None)

    def nodes(self) -> list[dict]:
        """Az oldalak a canonical-duplikátumok nélkül."""
        return [p for p in self.pages.values() if p["canonical"] is None]

    def group_h1(self, page: dict) -> set[str]:
        return {alias_key(p["h1"]) for p in self.pages.values()
                if p["group"] == page["group"] and p["h1"]} - {""}

    def named(self, page: dict, main: tuple) -> tuple[bool, bool, list[str]]:
        """(a H1 megnevezi-e a fő entitást, a title megnevezi-e, az elfogadott megnevezések);
        lásd a modul leírását."""
        forms = self.naming_forms(main[0], page)
        mentioned = self.entities[main[0]]["anchor"] is None
        in_h1 = names_in(forms, page["h1"]) or (mentioned and "h1" in main[3])
        in_title = names_in(forms, page["title"], cut=True) \
            or (mentioned and "title" in main[3]) \
            or (in_h1 and bool(page["h1"]) and names_in([page["h1"]], page["title"], cut=True))
        if not in_title and page["role"] == "product":
            in_title = product_title(page["h1"] or self.name(main[0]), page["title"])
        if page["role"] == "category":         # a többes számtól függetlenül
            in_h1 = in_h1 or loosely_named(forms, page["h1"])
            # a title-nek elég a név egy tartalmas szava (a title gyakran kulcsszavak sora)
            in_title = in_title or shares_word(forms, page["title"])
        return in_h1, in_title, forms

    def naming_forms(self, entity_id: int, page: dict) -> list[str]:
        """Az entitás megnevezései az oldal H1- és title-összevetéséhez: a név és az aliasok,
        az oldal saját szövegeiből lett aliasok nélkül (lásd a modul leírását)."""
        entity = self.entities[entity_id]
        own = self.group_h1(page)
        sources = self.alias_sources.get(entity_id, {})
        forms = [entity["name"]]
        for alias in dict.fromkeys([*(entity["aliases"] or []), *sources]):
            own_text = alias_key(alias) in own \
                or bool(sources.get(alias)) and sources[alias] <= OWN_TEXT_SOURCES
            if own_text and alias_key(alias) != alias_key(entity["name"]):
                continue
            forms.append(alias)
        if entity["type"] == "person":
            forms += [" ".join(reversed(f.split())) for f in list(forms) if len(f.split()) == 2]
        return [f for f in dict.fromkeys(forms) if f and alias_key(f)]


def names_in(forms: list[str], text: str | None, cut: bool = False) -> bool:
    """Valamelyik megnevezés áll-e a szövegben: szókezdettől (`gate.occurs`); a betűi egymás
    után, szóhatártól szóhatárig, az írásjelektől, a kötőszótól és az egybeírástól függetlenül
    (`_tokens`: „Tracking and Measurement” a „Tracking & Measurement”-ben, „Eprivacy” az
    „E-Privacy”-ben, de a „Mérés” nem a „Kimérés”-ben); a szöveg egy szelete (a title a
    `TITLE_SPLIT` mentén) a név rövid alakja: legalább két szó, mind a név szava; vagy `cut`
    esetén (title) a szöveg a megnevezés legalább `CUT_MIN_CHARS` jeles, a hosszának
    `CUT_MIN_SHARE` részét kitevő elejével kezdődik (a hosszkorlát miatt levágott név)."""
    if not text:
        return False
    words, plain = _tokens(text), _squash(text)
    joined = "".join(words)
    starts, ends, at = set(), set(), 0
    for word in words:
        starts.add(at)
        at += len(word)
        ends.add(at)
    pieces = [set(_tokens(piece)) for piece in TITLE_SPLIT.split(text)]
    for form in forms:
        name_words = _tokens(form)
        name = "".join(name_words)
        if occurs(form, text) or (name and any(
                joined.startswith(name, start) and start + len(name) in ends
                for start in starts)):
            return True
        if any(len(piece) >= 2 and piece <= set(name_words) for piece in pieces):
            return True
        if cut:
            name, shared = _squash(form), 0
            while shared < min(len(name), len(plain)) and name[shared] == plain[shared]:
                shared += 1
            if shared >= CUT_MIN_CHARS and shared >= CUT_MIN_SHARE * len(name):
                return True
    return False


def loosely_named(forms: list[str], text: str | None) -> bool:
    """A szöveg megnevezi-e valamelyik alakot a többes számtól és a kis-nagybetűtől függetlenül:
    az alak minden szava szerepel a szövegben, egyes vagy többes számban (`PLURAL_ENDINGS`:
    „Fürdőtejek” ↔ „fürdőtej”, „Szilárd Sampon” ↔ „Szilárd Samponok”)."""
    words = _tokens(text or "")

    def same(a: str, b: str) -> bool:
        short, long = sorted((a, b), key=len)
        return a == b or (long.startswith(short) and long[len(short):] in PLURAL_ENDINGS)

    return any(tokens and all(any(same(token, word) for word in words) for token in tokens)
               for tokens in (_tokens(form) for form in forms))


def shares_word(forms: list[str], text: str | None) -> bool:
    """A szöveg tartalmazza-e valamelyik alak legalább egy tartalmas szavát (legalább
    `CONTENT_WORD_CHARS` jel, nem töltelékszó) szótő szerint: egyes vagy többes számban
    (`PLURAL_ENDINGS`), vagy az egyik szó a másik eleje (összetett szó előtagja, ragozott
    alak: „kéz” ↔ „kézkrémek”, „illatú” ↔ „illat”)."""
    words = [w for w in _tokens(text or "") if len(w) >= CONTENT_WORD_CHARS]
    for form in forms:
        for token in _tokens(form):
            if len(token) < CONTENT_WORD_CHARS or token in FILLER_WORDS:
                continue
            if any(word.startswith(token) or token.startswith(word) for word in words):
                return True
    return False


def product_title(name: str | None, title: str | None) -> bool:
    """Termékoldalon a title megnevezi-e a terméket szó szerinti egyezés nélkül: benne áll a
    név fejszava (az első szó) és minden kiszerelése (`QUANTITY`: „90g”, „50 ml”, „2*25g”); a
    kiszerelés nélküli névnél az első két szó. A title gyakran a termék hosszabb, leíró neve
    („Fürdőgolyó kecsketejes 90g” ↔ „Fürdőgolyó organikus kecsketejjel … 90g”)."""
    if not name or not title:
        return False
    words, title_words = _tokens(name), set(_tokens(title))
    if not words or words[0] not in title_words:
        return False
    sizes = {size.replace(" ", "") for size in QUANTITY.findall(alias_key(name))}
    title_sizes = {size.replace(" ", "") for size in QUANTITY.findall(alias_key(title))}
    if sizes:
        return sizes <= title_sizes
    return set(words[:2]) <= title_words


def _tokens(text: str) -> list[str]:
    """A kulcs szavai az írásjelek és a kötőszó (és, and, &) nélkül."""
    return [w for w in re.split(r"[\W_]+", alias_key(text)) if w and w not in CONJUNCTIONS]


def _squash(text: str) -> str:
    """A kulcs betűi és számjegyei, elválasztók és írásjelek nélkül."""
    return re.sub(r"[\W_]+", "", alias_key(text))


def _mismatches(site: _Site) -> list[tuple]:
    records = []
    seen: set[tuple] = set()
    for page in site.nodes():
        main = site.main(page["page_id"])
        if page["status"] != "main" or main is None or main[2] != "strong":
            continue
        entity_id = main[0]
        key = (page["group"], entity_id, page["h1"], page["title"])
        if key in seen:
            continue
        seen.add(key)
        in_h1, in_title, forms = site.named(page, main)
        home = page["role"] == "home" or entity_id in site.site_entities
        h1_missing = not in_h1 and not home
        if not h1_missing and in_title:
            continue
        others = sorted({site.name(e) for e, _, counts in site.mention_edges[page["page_id"]]
                         if e != entity_id and counts.get("h1")})
        problems = [text for flag, text in (
            (bool(page["h1"]) and h1_missing and not others,
             "a H1 általános: nincs benne entitás"),
            (bool(page["h1"]) and h1_missing and bool(others), "a fő entitás nincs a H1-ben"),
            (not in_title, "a fő entitás nincs a title-ben")) if flag]
        if not problems:                       # a H1 hiánya a `missing_h1` megállapítás
            continue
        # kategóriaoldalon a név a morzsamenüből jön, az eltérés ott mindig közepes
        severity = "high" if not in_title and page["role"] != "category" else "medium"
        records.append((severity, page, entity_id,
                        {"url": page["url"], "main_entity": site.name(entity_id),
                         "confidence": main[2], "h1": page["h1"], "title": page["title"],
                         "in_h1": in_h1, "in_title": in_title, "h1_entities": others,
                         "names": forms[:8], "problems": problems}))
    groups: dict[tuple, list[tuple]] = defaultdict(list)
    for record in records:
        groups[(tuple(record[3]["problems"]), record[1]["role"])].append(record)
    found = []
    for (problems, role), members in sorted(groups.items()):
        severity, page, entity_id, evidence = members[0]
        if len(members) == 1:
            found.append(("h1_title_mismatch", severity, page["page_id"], entity_id,
                          f"{site.name(entity_id)}: {'; '.join(problems)}", evidence))
            continue
        label = f"{'; '.join(problems)} | {role}"
        found.append(("h1_title_mismatch", severity, None, None,
                      (f"{'; '.join(problems)}: {len(members)} "
                      f"{ROLE_LABELS.get(role, role)}"),
                      {"group": label, "role": role, "problems": list(problems),
                       "pages": [m[3] for m in sorted(members, key=lambda m: m[1]["url"])]}))
    return found


def _parent_child(a: str, b: str) -> bool:
    """Az egyik URL útvonala a másiké alatt áll (szülő–gyermek)."""
    first, second = (urlsplit(u).path.rstrip("/") + "/" for u in (a, b))
    return first != second and (first.startswith(second) or second.startswith(first))


def common_suffixes(titles: Iterable[str | None]) -> frozenset[str]:
    """A site-szerte ismétlődő title-utótagok (kulcs alakban): a title utolsó szelete
    (`TITLE_SPLIT`), ha a címmel bíró oldalak legalább `SUFFIX_SHARE` részén ugyanez áll ott.
    A hívó egy nyelv oldalainak címeit adja át: a más nyelvű oldalak utótagja más lehet."""
    known = [title for title in titles if title]
    counts = Counter(alias_key(pieces[-1]) for pieces in map(TITLE_SPLIT.split, known)
                     if len(pieces) > 1)
    return frozenset(suffix for suffix, count in counts.items()
                     if suffix and count >= SUFFIX_SHARE * len(known))


def title_similarity(first: str | None, second: str | None,
                     suffixes: frozenset[str] = frozenset()) -> float:
    """A két title szavainak Jaccard-hasonlósága. Az utolsó szelet csak akkor marad el, ha
    site-szerte ismétlődő utótag (`suffixes`, lásd `common_suffixes`): a „SEO – Kezdőknek” és a
    „SEO – Haladóknak” utolsó szelete maga a különbség."""
    sets = []
    for title in (first, second):
        pieces = TITLE_SPLIT.split(title or "")
        if len(pieces) > 1 and alias_key(pieces[-1]) in suffixes:
            pieces = pieces[:-1]
        sets.append(set(_tokens(" ".join(pieces))))
    union = sets[0] | sets[1]
    return len(sets[0] & sets[1]) / len(union) if union else 0.0


def _relation(a: str, b: str) -> str:
    """A két oldal viszonya az útvonaluk szerint."""
    if _parent_child(a, b):
        return "szülő–gyermek"
    first, second = (urlsplit(u).path.rstrip("/").rsplit("/", 1)[0] for u in (a, b))
    return "testvér" if first == second else "más ág"


def _shared_topics(site: _Site) -> list[tuple]:
    by_entity: dict[tuple[int, str | None], dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list))
    for page in site.nodes():
        main = site.main(page["page_id"])
        if page["status"] != "main" or main is None or main[2] != "strong" \
                or page["noindex"] or PAGINATION.search(page["url"]) \
                or main[0] in site.site_entities:
            continue
        by_entity[(main[0], page["lang"])][page["group"]].append({**page, "confidence": main[2]})
    titles: dict[str | None, list[str | None]] = defaultdict(list)
    for page in site.nodes():
        titles[page["lang"]].append(page["title"])
    suffixes = {lang: common_suffixes(found) for lang, found in titles.items()}
    linked: set[tuple[int, int]] = set()
    in_content: set[tuple[int, int]] = set()
    for link in crawl.links(site.con):
        if link.to_page_id is not None:
            linked.add((link.from_page_id, link.to_page_id))
            if link.position == CONTENT_LINK:
                in_content.add((link.from_page_id, link.to_page_id))
    found = []
    for (entity_id, lang), groups in sorted(by_entity.items(),
                                            key=lambda item: (item[0][0], item[0][1] or "")):
        reps = [min(members, key=lambda p: p["url"]) for _, members in sorted(groups.items())]
        if len(reps) < 2:
            continue
        reps.sort(key=lambda p: p["url"])
        secondary = {p["page_id"]: {c[0] for c in site.chosen.get(p["page_id"], [])
                                    if c[1] == "secondary"} for p in reps}
        overlaps = []
        for first, second in combinations(reps, 2):
            common = sorted(site.name(e) for e in
                            secondary[first["page_id"]] & secondary[second["page_id"]])
            similarity = round(title_similarity(first["title"], second["title"],
                                                suffixes[lang]), 2)
            if common or similarity >= TITLE_SIMILAR:
                pair, reverse = ((first["page_id"], second["page_id"]),
                                 (second["page_id"], first["page_id"]))
                there, back = pair in in_content, reverse in in_content
                overlaps.append({"pages": [first["url"], second["url"]], "secondary": common,
                                 "title_similarity": similarity,
                                 "relation": _relation(first["url"], second["url"]),
                                 "content_links": "kölcsönös" if there and back
                                 else "első → második" if there
                                 else "második → első" if back else "nincs",
                                 "any_link": pair in linked or reverse in linked})
        involved = {url for overlap in overlaps for url in overlap["pages"]}
        tail = f" ({lang})" if lang else ""
        # közös témánál a gyermek kimarad, ha a szülője is a listában áll
        kept = [p for p in reps if p["url"] in involved] if overlaps else [
            p for p in reps if not any(
                _parent_child(p["url"], other["url"]) and len(urlsplit(other["url"]).path)
                < len(urlsplit(p["url"]).path) for other in reps)]
        if len(kept) < 2:
            continue
        evidence = {"main_entity": site.name(entity_id), "lang": lang, "overlaps": overlaps,
                    "pages": [{"url": p["url"], "role": p["role"], "h1": p["h1"],
                               "title": p["title"], "confidence": p["confidence"],
                               "secondary": sorted(site.name(e)
                                                   for e in secondary[p["page_id"]])}
                              for p in kept]}
        if overlaps:
            evidence["other_pages"] = len(reps) - len(kept)
            found.append(("cannibalization",
                          "high" if len(involved) >= HIGH_GROUPS else "medium", None, entity_id,
                          (f"{site.name(entity_id)}: lehetséges kannibalizáció: {len(involved)} "
                           "oldal fő entitása, átfedő másodlagos entitással vagy hasonló "
                           f"title-lel{tail}"), evidence))
        else:
            found.append(("shared_topic", "low", None, entity_id,
                          (f"{site.name(entity_id)}: {len(kept)} oldal közös témája, átfedés "
                          f"nélkül{tail}"), evidence))
    return found


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^\w]+", alias_key(text)) if w}


def uncovered_candidates(site: _Site, run: FindingsRun | None = None,
                         min_structural: int | None = None) -> list[dict]:
    """A lefedetlen téma jelöltjei a kizárások (`exclusion`) előtt: a súly, az oldalszám, a
    típus, a kiemelés (`min_structural` oldalcsoportban title, H1 vagy heading; alapból
    `MIN_STRUCTURAL`, `SMALL_SITE_GROUPS`-nál kevesebb oldalcsoportú site-on
    `SMALL_SITE_STRUCTURAL`) és a lefedettség feltételein átment entitások, a mérőszámaikkal
    (`template_share`: az említések hányad része áll sablon- vagy chrome-helyen;
    `in_site_name`: a név szavai a site nevének szavai; `headline`: az első indexelhető oldal,
    amelynek a H1-e és a title-je is megnevezi, `NAMING_ALONE_ROLES` szerepű oldalon elég az
    egyik; `common_word`: egyszavas köznévi fogalom: a neve kisbetűs, vagy a szövegben
    kisbetűvel is áll (a név írásmódjától függetlenül: a „Stratégia” is, ha az oldalakon
    „stratégia” alakban is szerepel; a rövidítés és a tulajdonnév nem); `page_share`: az indexelhető oldalak hányad részén szerepel)."""
    if not site.weights:
        return []
    if min_structural is None:
        small = len({p["group"] for p in site.nodes()}) < SMALL_SITE_GROUPS
        min_structural = SMALL_SITE_STRUCTURAL if small else MIN_STRUCTURAL
    indexable = {p["page_id"] for p in site.nodes() if not p["noindex"]}
    headlines = [(p["url"], p["h1"], p["title"], p["role"] in NAMING_ALONE_ROLES)
                 for p in site.nodes() if not p["noindex"]]
    name_words: Counter = Counter()                  # szó → hány entitás nevében áll
    for other in site.entities.values():
        name_words.update(_words(other["name"]))
    on_pages: Counter = Counter()
    groups: dict[int, set[str]] = defaultdict(set)          # entitás → említő oldalcsoportok
    headed: dict[int, set[str]] = defaultdict(set)          # … ahol title, H1 vagy heading
    for page_id, edges in site.mention_edges.items():
        page = site.pages.get(page_id)
        if page is None or page["canonical"] is not None:
            continue
        for mentioned, edge_weight, counts in edges:
            if edge_weight <= 0:
                continue
            on_pages[mentioned] += page_id in indexable
            groups[mentioned].add(page["group"])
            if any(counts.get(position) for position in ("title", "h1", "heading")):
                headed[mentioned].add(page["group"])
    originals = sorted(node.page_id for node in graph_queries.page_nodes(site.con)
                       if node.canonical_page is None)
    placed = {entity_id: (total, fixed) for entity_id, total, fixed
              in store.mention_placement(site.con, originals)}
    site_words = [_words(form) for e in site.site_entities if e in site.entities
                  for form in [site.name(e), *(site.entities[e]["aliases"] or [])]]
    top = max(1, math.ceil(len(site.weights) * TOP_SHARE))
    page_keys = {normal_key(p["h1"]) for p in site.pages.values() if p["h1"]}
    page_keys |= {normal_key(segment) for p in site.pages.values()
                  for segment in urlsplit(p["url"]).path.split("/") if segment}
    main_words = [_words(site.name(main[0])) for page in site.nodes()
                  if (main := site.main(page["page_id"])) is not None
                  and main[0] not in site.site_entities]
    anchored = store.anchored_entity_ids(site.con)
    offered = {edge.to_id for edge in graph_queries.edges(site.con, "offers")
               if edge.from_id in anchored}
    secondary_of = {c[0] for chosen in site.chosen.values() for c in chosen
                    if c[1] == "secondary"}
    parents = {edge.to_id for edge in graph_queries.edges(site.con, "part_of")}
    found = []
    for entity_id, weight in site.weights.items():
        entity = site.entities[entity_id]
        if site.rank[entity_id] > top or len(groups[entity_id]) < MIN_PAGES \
                or weight["main_pages"] \
                or entity["type"] in NO_PAGE_TYPES or entity["subtype"] in NO_PAGE_SUBTYPES \
                or entity_id in site.site_entities or entity["anchor"] is not None:
            continue
        if run is not None:
            run.missing_literal += 1
        forms = [entity["name"], *(entity["aliases"] or [])]
        if len(headed[entity_id]) < min_structural or weight["mentions"] < MIN_MENTIONS \
                or entity_id in secondary_of \
                or entity_id in offered or {normal_key(f) for f in forms} & page_keys \
                or (entity_id not in parents
                    and any(_words(entity["name"]) <= words for words in main_words)):
            continue
        total, fixed = placed.get(entity_id, (0, 0))
        name = entity["name"].strip()
        own_words = _words(name)
        naming = [f for f in forms if alias_key(f) == alias_key(name) or not (
            len(_squash(f)) <= SHORT_ALIAS_CHARS
            and any(name_words[w] > (w in own_words) for w in _words(f)))]
        found.append({
            "entity_id": entity_id, "weight": weight, "parent": entity_id in parents,
            "headline": next((url for url, h1, title, alone in headlines
                              if (any if alone else all)((
                                  names_in(naming, h1), names_in(naming, title, cut=True)))),
                             None),
            "common_word": entity["type"] == "concept" and name.isalpha()
            and (name == name.lower() or entity_id in site.lower_forms),
            "family": entity["type"] == "product" and (entity["subtype"] == "line"
                                                       or entity_id in parents),
            "page_share": on_pages[entity_id] / len(indexable) if indexable else 0.0,
            "template_share": fixed / total if total else 0.0,
            "in_site_name": any(_words(entity["name"]) <= words for words in site_words),
            "page_groups": len(groups[entity_id]), "structural": len(headed[entity_id])})
    return found


def is_context(candidate: dict) -> bool:
    """Kontextus-entitás: az említései legalább `CONTEXT_SHARE` részben sablon- vagy
    chrome-helyen állnak, vagy a neve a site nevében szerepel."""
    return candidate["template_share"] >= CONTEXT_SHARE or candidate["in_site_name"]


def exclusion(candidate: dict) -> str | None:
    """Miért nem lefedetlen téma a jelölt (None: az): `context` (`is_context`); `headline`: egy
    indexelhető oldal H1-e és title-je is megnevezi (van róla szóló oldal; a csak az egyikben
    álló említés mellékes, kivéve a `NAMING_ALONE_ROLES` szerepű oldalt, pl. az ajánlatoldalt,
    ahol a title vagy a H1 önmagában is megnevezés; a legfeljebb `SHORT_ALIAS_CHARS` jelű alias, amely egy másik entitás
    nevének szava, pl. az „AI” az „AI Search” mellett, nem megnevezés; a szülőre, pl. a
    termékcsaládra nem vonatkozik, mert a termékei címében mindig ott áll); `common_word`:
    egyszavas, kisbetűs köznévi fogalom (nem rövidítés, nem tulajdonnév)."""
    if is_context(candidate):
        return "context"
    if candidate["headline"] is not None and not candidate["parent"]:
        return "headline"
    if candidate["common_word"]:
        return "common_word"
    return None


def _uncovered(site: _Site, run: FindingsRun) -> list[tuple]:
    kept = []
    for candidate in uncovered_candidates(site, run):
        reason = exclusion(candidate)
        if reason == "context":
            run.context.append(site.name(candidate["entity_id"]))
        elif reason is None:
            kept.append(candidate)
    parent_of = {from_id: to_id for from_id, to_id in sorted(
        (edge.from_id, edge.to_id) for edge in graph_queries.edges(site.con, "part_of")
        if edge.from_kind == "entity" and edge.to_kind == "entity")}
    families = {c["entity_id"] for c in kept if c["family"]}
    under: dict[int, list[int]] = defaultdict(list)          # hiányzó szülő → alcsaládjai
    for entity_id in sorted(families):
        seen, parent = {entity_id}, parent_of.get(entity_id)
        top = None
        while parent is not None and parent not in seen:
            if parent in families:
                top = parent
            seen.add(parent)
            parent = parent_of.get(parent)
        if top is not None:
            under[top].append(entity_id)
    nested = {child for children in under.values() for child in children}
    partial = crawl.completeness(site.con).site_partial
    found = []
    for candidate in kept:
        entity_id, weight = candidate["entity_id"], candidate["weight"]
        if entity_id in nested:
            continue
        entity = site.entities[entity_id]
        family = candidate["family"]
        kind = "missing_page" if family else "uncovered_topic"
        pages = [(site.pages[p]["url"], w, counts) for p, edges in site.mention_edges.items()
                 if p in site.pages and site.pages[p]["canonical"] is None
                 for e, w, counts in edges if e == entity_id and w > 0]
        pages.sort(key=lambda item: (-item[1], item[0]))
        subfamilies = [{"entity": site.name(child), "page_count": site.weights[child]["pages"]}
                       for child in sorted(under.get(entity_id, []), key=site.name)]
        many = weight["pages"] >= HIGH_PAGES
        severity =("high" if many else "medium") if family else ("medium" if many else "low")
        tail = f"; alcsaládjai: {', '.join(c['entity'] for c in subfamilies)}" \
            if subfamilies else ""
        found.append((kind, severity, None, entity_id,
                      (f"{entity['name']} ({site.kind(entity_id)}): {weight['pages']} oldalon "
                       f"szerepel, egyiknek sem fő entitása{tail}"
                       + (" (részleges bejárás: a be nem járt oldalak között lehet saját oldala)"
                          if partial else "")),
                      {"entity": entity["name"], "type": site.kind(entity_id),
                       **({"crawl": "partial"} if partial else {}),
                       "action": ACTIONS[kind], "page_share": round(candidate["page_share"], 2),
                       "template_share": round(candidate["template_share"], 2),
                       "weight": weight["weight"], "rank": site.rank[entity_id],
                       "ranked": len(site.weights), "page_count": weight["pages"],
                       "mentions": weight["mentions"], "structural": candidate["structural"],
                       "page_groups": candidate["page_groups"], "subfamilies": subfamilies,
                       "top_pages": [{"url": url, "mention_weight": w, "positions": counts}
                                     for url, w, counts in pages[:EVIDENCE_PAGES]]}))
    return found


def _unclear_topics(site: _Site) -> list[tuple]:
    found = []
    for page in site.nodes():
        main = site.main(page["page_id"])
        if page["status"] == "none":
            found.append(("unclear_topic", "high", page["page_id"], None,
                          "az oldalnak nincs fő entitás-jelöltje",
                          {"url": page["url"], "h1": page["h1"], "title": page["title"]}))
        elif page["status"] == "main" and main is not None and main[2] == "weak" \
                and not {"h1", "title"} <= set(main[3]):
            found.append(("unclear_topic", "medium", page["page_id"], main[0],
                          (f"gyenge fő entitás: {site.name(main[0])} "
                           f"({evidence_text(main[3])})"),
                          {"url": page["url"], "h1": page["h1"], "title": page["title"],
                           "main_entity": site.name(main[0]), "evidence": main[3]}))
    return found


def _canonical_issues(site: _Site) -> list[tuple]:
    """Hibás canonical (közepes): a cél hibás státuszú, nem oldal-csomópont, a lánc körbeér,
    a cél más típusú oldal, vagy a cél nincs a készletben és ez nem a crawl keretéből adódik.

    A készleten kívüli célnál a keret: (1) a site-fájl `include` mintájára nem illeszkedő vagy
    egy `exclude` mintájára illeszkedő cél, vagy más host: a crawl szándékosan nem járta be,
    nincs megállapítás; (2) korlátozott crawl (sitemap-mód, vagy az oldalszám elérte a
    `max_pages` határt): a cél a kereten kívül eshetett, nincs megállapítás, kivéve ha a
    canonical az oldal saját URL-je a lekérdezés nélkül (a tartalmat kiválasztó lekérdezést
    dobja el, pl. terméklista → `index.php`); (3) teljes crawl és a kereten belüli cél: a cél
    a site-on sehol nem érhető el, megállapítás (pl. `/hu/` → `/hu/hu/`)."""
    state = crawl.completeness(site.con)        # a crawl-modul rögzített teljességi állapota
    limited = state.partial
    hosts = {urlsplit(page["url"]).netloc for page in site.pages.values()}
    found = []
    for page in sorted(site.pages.values(), key=lambda p: p["url"]):
        detail = page["issue_detail"]
        if not page["issue"] or not detail:
            continue
        target = urljoin(page["url"], detail.get("canonical") or "")
        if page["issue"] == "not_crawled":
            outside = urlsplit(target).netloc not in hosts \
                or (state.include and not re.search(state.include, target)) \
                or (state.exclude and re.search(state.exclude, target))
            drops_query = "?" in page["url"] and canonical_key(target) == canonical_key(
                page["url"].split("?", 1)[0])
            if outside or (limited and not drops_query):
                continue
        label = CANONICAL_LABELS.get(page["issue"], page["issue"])
        found.append(("canonical_issue", "medium", page["page_id"], None,
                      f"hibás canonical ({label}): {target}",
                      {"url": page["url"], "canonical": target, "issue": page["issue"],
                       "status": detail.get("status")}))
    return found


def _legal_pages(site: _Site) -> list[tuple]:
    """Webshopon (van termékoldal) a jogi oldalak megléte és elérhetősége a láblécből, a
    `LEGAL_LABELS` fajtáira. Megvan a fajta, ha van ilyen oldal (`pages.legal_kind`; a
    canonical-duplikátum is számít), vagy ha egy jogi oldal egy headingje vagy legfeljebb
    `LEGAL_TITLE_WORDS` szavas bekezdése (címként álló sor) megnevezi (`LEGAL_HEADING_WORDS`:
    pl. az „Elállási jog” az ÁSZF-en belül). Ha az ÁSZF szövege külső keretben (iframe) áll, a jellemzően benne lévő fajták
    (`EMBEDDED_KINDS`: elállás, szállítás és fizetés, garancia) hiánya nem állapítható meg,
    nincs megállapítás. Megállapítás: a fajta hiányzik (közepes), vagy megvan, de egyik oldalára sem mutat
    lábléc-link (alacsony; a bizonyíték jelzi, ha a menüből elérhető). Nem webshopon nem fut: ott a hat fajta nem mind várható."""
    if not any(page["role"] == "product" for page in site.pages.values()):
        return []
    by_kind: dict[str, list[dict]] = defaultdict(list)
    embedded = False                           # az ÁSZF szövege külső keretben (iframe) áll
    for page in site.pages.values():
        kind = legal_kind(page["url"])
        if kind == "terms" and "<iframe" in (crawl.rendered_dom(site.con, page["page_id"])
                                              or "").lower():
            embedded = True
        if kind is None and page["support"] == "contact":
            kind = "contact"
        if kind is not None:
            by_kind[kind].append(page)
    links = crawl.links(site.con)
    footer = {link.to_page_id for link in links if link.position == "footer"}
    menu = {link.to_page_id for link in links if link.position == "nav"}
    # jogi oldalra mutató link, amelynek a célja nincs bejárva: fajta → (cím, pozíciók)
    unchecked: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for link in links:
        if link.to_page_id is None and (found_kind := legal_kind(link.to_url)) is not None:
            unchecked[found_kind][link.to_url].add(link.position)
    partial = crawl.completeness(site.con).site_partial
    legal = {page["page_id"]: page for pages in by_kind.values() for page in pages}
    titles: dict[int, list[str]] = defaultdict(list)       # jogi oldal → címként álló sorok
    for block in extract_queries.blocks(site.con):
        if block.page_id in legal and block.region == "content" and (
                block.kind == "heading" or (block.kind == "paragraph" and len(
                    (block.text or "").split()) <= LEGAL_TITLE_WORDS)):
            titles[block.page_id].append(block.text or "")
    found = []
    for kind in LEGAL_KINDS:
        label = LEGAL_LABELS[kind]
        pages = sorted(by_kind.get(kind, []), key=lambda p: p["url"])
        inside = []
        if not pages:
            for page in sorted(legal.values(), key=lambda p: p["url"]):
                named = [text for text in titles[page["page_id"]]
                         if any(word in alias_key(text) for word in LEGAL_HEADING_WORDS[kind])]
                if named:
                    inside.append({"url": page["url"], "headings": named[:3],
                                   "page_id": page["page_id"]})
        holders = pages or inside
        evidence = {"group": f"legal_page | {kind}", "kind": kind, "label": label,
                    "pages": [{"url": p["url"]} for p in pages],
                    "inside": [{"url": p["url"], "headings": p["headings"]} for p in inside]}
        if not holders and embedded and kind in EMBEDDED_KINDS:
            continue                           # a beágyazott ÁSZF tartalma nem látható
        if not holders and unchecked.get(kind):
            # a hivatkozás megvan, a céloldal nincs bejárva: a hiány nem állítható
            targets = sorted(unchecked[kind])
            found.append(("legal_page", "low", None, None,
                          ("jogi oldal hivatkozva, a cél nincs bejárva (nem ellenőrzött): "
                           f"{label} ({targets[0]})"),
                          {**evidence, "status": "linked_unchecked",
                           "links": [{"url": url, "positions": sorted(unchecked[kind][url])}
                                     for url in targets]}))
        elif not holders:
            found.append(("legal_page", "medium", None, None,
                          f"hiányzó jogi oldal: {label}"
                          + (" (részleges bejárás: nem ellenőrzött)" if partial else ""),
                          {**evidence, "status": "missing",
                           **({"crawl": "partial"} if partial else {})}))
        elif not any(p["page_id"] in footer for p in holders):
            where = holders[0]["url"]
            in_menu = any(p["page_id"] in menu for p in holders)
            found.append(("legal_page", "low", None, None,
                          f"a jogi oldal nem érhető el a láblécből: {label} ({where})"
                          + ("; a menüből elérhető" if in_menu else ""),
                          {**evidence, "status": "not_in_footer", "in_menu": in_menu}))
    return found


def _schema_id_names(site: _Site) -> list[tuple]:
    """A site strukturált adatának következetlensége: ugyanaz az azonosító (`@id`) oldalanként
    más névvel szerepel (a kis- és nagybetű, az írásjelek eltérése nem számít). Egy
    megállapítás a site-ra, alacsony. Azonosítónként a nevek az oldal nyelve szerint bontva
    (`names`: név, nyelv, és minden oldal, ahol a csomópont így szerepel), és két külön jelzés:
    `several_languages`, ha az azonosító alatt több nyelv oldalain más-más név áll (a két
    nyelvi alak nem ugyanaz a hiba, mint egy nyelven belül két név); `url_mismatch`, ha a
    csomópont `url`-je nem mindenütt ugyanarra az oldalra mutat, vagy nem arra, amelyikre az
    azonosító (`node_urls`: a csomópont `url` értékei). Az entitásokat ez nem érinti: az azonos
    azonosítójú csomópontok egy entitást adnak, a nevek aliasok."""
    names: dict[str, dict[tuple[str, str], set[str]]] = defaultdict(lambda: defaultdict(set))
    targets: dict[str, set[str]] = defaultdict(set)
    for item in crawl.schema_items(site.con):
        page = site.pages.get(item.page_id)
        if page is None:
            continue
        for node in _walk(item.data):
            if "@type" in node and isinstance(node.get("@id"), str) \
                    and isinstance(node.get("name"), str) and node["name"].strip():
                ref = node["@id"].strip()
                names[ref][(page["lang"] or "", node["name"].strip())].add(page["url"])
                if isinstance(node.get("url"), str) and node["url"].strip():
                    targets[ref].add(node["url"].strip())
    ids = []
    for ref, forms in sorted(names.items()):
        if len({alias_key(name) for _, name in forms}) < 2:
            continue
        by_lang: dict[str, set[str]] = defaultdict(set)
        for lang, name in forms:
            by_lang[lang].add(alias_key(name))
        home = canonical_key(ref.split("#", 1)[0])
        ids.append({
            "id": ref,
            "names": [{"name": name, "lang": lang, "pages": len(urls), "urls": sorted(urls)}
                      for (lang, name), urls in sorted(
                          forms.items(), key=lambda kv: (kv[0][0], -len(kv[1]), kv[0][1]))],
            "languages": sorted(by_lang),
            # egy nyelven belül is több név áll-e (ez a tényleges következetlenség)
            "within_language": sorted(lang for lang, keys in by_lang.items() if len(keys) > 1),
            "several_languages": len(by_lang) > 1,
            "node_urls": sorted(targets[ref]),
            "url_mismatch": len({canonical_key(url) for url in targets[ref]}) > 1 or any(
                canonical_key(url) != home for url in targets[ref])})
    if not ids:
        return []
    several = sum(1 for item in ids if item["several_languages"])
    mismatch = sum(1 for item in ids if item["url_mismatch"])
    notes = [text for count, text in (
        (several, f"{several} alatt több nyelv neve áll"),
        (mismatch, f"{mismatch} csomópontjának url-je nem a saját oldalára mutat")) if count]
    return [("schema_id_names", "low", None, None,
             f"a strukturált adatban {len(ids)} azonosító (@id) több névvel szerepel"
             + (f" ({'; '.join(notes)})" if notes else ""),
             {"group": "schema_id_names", "ids": ids})]


def article_like(page: Mapping, fields: titles.TitleFields, word_count: int | None
                 ) -> str | None:
    """Cikk jellegű-e az oldal, és mi alapján (None: nem az): `article` szerepű oldal; vagy
    segédfajta nélküli `support` oldal, amelyet a site `og:type` = `article` jelöléssel lát el,
    és legalább `ARTICLE_MIN_WORDS` szó (a webshopok tudástár- és blogcikkei ilyenek)."""
    if page["support"] is not None:
        return None
    if page["role"] == "article":
        return "cikkoldal (szerep)"
    if page["role"] == "support" and fields.og_type == "article" \
            and (word_count or 0) >= ARTICLE_MIN_WORDS:
        return f"egyéb oldal, og:type = article, legalább {ARTICLE_MIN_WORDS} szó"
    return None


def title_naming(site: _Site, page: Mapping, main: tuple) -> tuple[bool, bool, list[str]]:
    """(a title megnevezi-e a fő entitást, a látható cím megnevezi-e, az elfogadott
    megnevezések) a szigorú összevetéssel (`titles.names_whole`); a kinyerés tárolt title- és
    H1-említése is megnevezés. A látható címnél az oldal H1-e is számít, ha az nem a tartalmi
    régió első címsora (a H1 a fő tartalmon kívül áll, előtte egy kisebb címsorral): az oldal
    ott megnevezi a fő entitást. Ha a title és a látható cím szövege azonos, a két válasz is
    az."""
    fields = site.title_fields[page["page_id"]]
    forms = list(dict.fromkeys([*site.naming_forms(main[0], dict(page)),
                                site.shown(main[0], page)]))
    longer = site.other_forms(page, main[0])
    stored = main[3] if site.entities[main[0]]["anchor"] is None else {}
    in_title = titles.names_whole(forms, fields.title, page["lang"], longer) \
        or stored.get("title") is True
    in_visible = titles.names_whole(forms, fields.visible, page["lang"], longer) \
        or titles.names_whole(forms, page["h1"], page["lang"], longer) \
        or stored.get("h1") is True
    if fields.title and titles.compare_key(fields.title) == titles.compare_key(fields.visible):
        in_title = in_visible = in_title or in_visible
    return in_title, in_visible, forms


def metrics_only(named: list[dict]) -> bool:
    """A cím által megnevezett entitások mind mérőszámok-e (`metric` altípus); üres listára
    hamis."""
    return bool(named) and all(item["type"].partition("/")[2] == "metric" for item in named)


def _title_naming(site: _Site, mismatches: list[tuple] = ()) -> list[tuple]:
    """A `title_without_main_entity` jelöltek (lásd a modul leírását). `mismatches`: a
    `h1_title_mismatch` megállapítások sorai; amelyik oldalon már van ilyen, ott a `title_only`
    eset nem külön megállapítás, hanem annak a bizonyítékába kerül (`visible_title_note`,
    `visible_title`)."""
    covered: dict[str, dict] = {}
    for row in mismatches:
        for evidence in row[5].get("pages") or [row[5]]:
            covered[evidence["url"]] = evidence
    word_counts = {page.page_id: page.word_count for page in crawl.pages(site.con)}
    records = []
    seen: set[tuple] = set()
    for page in site.nodes():
        main = site.main(page["page_id"])
        fields = site.title_fields.get(page["page_id"])
        if page["status"] != "main" or main is None or fields is None \
                or page["role"] == "home" or main[0] in site.site_entities:
            continue
        key = (page["group"], main[0], fields.title, fields.visible)
        if key in seen:                        # a fülek nem ismétlik
            continue
        seen.add(key)
        in_title, in_visible, forms = title_naming(site, page, main)
        basis = article_like(page, fields, word_counts.get(page["page_id"]))
        named: list[dict] = []
        if not in_title and not in_visible and basis and (fields.title or fields.visible):
            named = site.title_entities(page, main[0], fields)
            case = "other_entity" if named else "no_entity"
        elif in_title and fields.visible and not in_visible:
            case = "title_only"
        else:
            continue
        if case == "title_only" and page["url"] in covered:
            covered[page["url"]].update({"visible_title_note": VISIBLE_TITLE_NOTE,
                                         "visible_title": fields.visible})
            continue
        records.append((page, main[0], {
            "url": page["url"], "main_entity": site.name(main[0]), "confidence": main[2],
            "case": case, "title": fields.title, "raw_title": fields.raw_title,
            "visible_title": fields.visible, "visible_title_element": fields.visible_element,
            "in_title": in_title, "in_visible_title": in_visible, "names": forms[:8],
            "title_entities": named, "title_metrics_only": metrics_only(named),
            "article_basis": basis, "claim": TITLE_CLAIM}))
    groups: dict[tuple, list[tuple]] = defaultdict(list)
    for record in records:
        groups[(record[2]["case"], record[0]["role"],
                record[2]["title_metrics_only"])].append(record)
    found = []
    for (case, role, metrics), members in sorted(groups.items()):
        page, entity_id, evidence = members[0]
        named = "; ".join(item["entity"] for item in evidence["title_entities"])
        if len(members) == 1:
            summary = {
                "no_entity": f"a cím nem nevez meg entitást; a fő entitás: "
                             f"{site.name(entity_id)}",
                "other_entity": ("a cím mérőszámot nevez meg: " if metrics
                                 else "a cím ezt nevezi meg: ")
                + f"{named}; a fő entitás: {site.name(entity_id)}",
                "title_only": f"{site.name(entity_id)}: {TITLE_CASES[case]}"}[case]
            found.append(("title_without_main_entity", "low", page["page_id"], entity_id,
                          summary, evidence))
            continue
        found.append(("title_without_main_entity", "low", None, None,
                      (f"{TITLE_METRICS if metrics else TITLE_CASES[case]}: {len(members)} "
                       f"{ROLE_LABELS.get(role, role)}"),
                      {"group": f"{case} | {role}" + (" | metric" if metrics else ""),
                       "role": role, "case": case, "title_metrics_only": metrics,
                       "claim": TITLE_CLAIM,
                       "pages": [m[2] for m in sorted(members, key=lambda m: m[0]["url"])]}))
    return found


def not_article_kind(site: _Site, page: Mapping) -> str | None:
    """Biztosan nem cikk-e az oldal, és melyik fajta (`NOT_ARTICLE_LABELS` kulcsa): a
    kezdőoldal; a kapcsolatoldal; ajánlat-, termék- vagy kategória-szerepű oldal; rólunk- vagy
    karrieroldal (URL-szó, és a fő entitás a site saját entitása). None: cikk lehet."""
    if page["role"] == "home":
        return "home"
    if page["support"] == "contact":
        return "contact"
    if page["role"] in OWN_THING_PAGE_ROLES:
        return "offer"
    main = site.main(page["page_id"])
    if main is not None and main[0] in site.site_entities \
            and url_has_word(page["url"], ABOUT_URL_WORDS):
        return "about"
    return None


def _article_markup(site: _Site) -> list[tuple]:
    """Az `article_markup_on_other_pages` megállapítás (lásd a modul leírását)."""
    nodes = site.nodes()
    marked = []
    for page in nodes:
        fields = site.title_fields.get(page["page_id"])
        kinds = sorted(set(fields.schema_types) & titles.ARTICLE_TYPES) if fields else []
        if kinds:
            marked.append((page, kinds))
    if not nodes or len(marked) < ARTICLE_MARKUP_SHARE * len(nodes):
        return []
    groups: dict[str, list[dict]] = {key: [] for key in NOT_ARTICLE_LABELS}
    maybe = []
    for page, kinds in marked:
        entry = {"url": page["url"], "schema_types": kinds,
                 "role": page["role"] + (f" ({page['support']})" if page["support"] else "")}
        kind = not_article_kind(site, page)
        (groups[kind] if kind else maybe).append(entry)
    wrong = sum(len(found) for found in groups.values())
    if not wrong:
        return []
    by_type = Counter(kind for _, kinds in marked for kind in kinds)
    types = ", ".join(f"{kind} {count}" for kind, count in sorted(by_type.items()))
    parts = ", ".join(f"{len(found)} {NOT_ARTICLE_LABELS[key]}"
                      for key, found in groups.items() if found)
    return [("article_markup_on_other_pages", "medium", None, None,
             (f"{len(nodes)} HTML oldalból {len(marked)} cikk-jelölésű ({types}); köztük "
              f"{wrong} biztosan nem cikk ({parts})"),
             {"group": "article_markup", "html_pages": len(nodes), "marked": len(marked),
              "share": round(len(marked) / len(nodes), 2), "by_type": dict(sorted(
                  by_type.items())),
              "counts": {NOT_ARTICLE_LABELS[key]: len(found) for key, found in groups.items()},
              "not_articles": {NOT_ARTICLE_LABELS[key]: found
                               for key, found in groups.items() if found},
              "maybe_articles": maybe})]


def _breadcrumb_home(site: _Site) -> list[tuple]:
    """A `breadcrumb_foreign_home` megállapítás (lásd a modul leírását)."""
    structure = site.structure
    home_lang = {page_id: lang for lang, page_id in structure.homes.items()}
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for page in site.nodes():
        lang = page["lang"] or ""
        crumb = structure.crumbs.get(page["page_id"])
        if crumb is None or lang not in structure.homes or page["page_id"] in home_lang:
            continue
        first_url, first_name = crumb[1][0]
        target = site.page_of(first_url)
        if target in home_lang and home_lang[target] != lang:
            groups[(lang, target)].append({"url": page["url"], "first_url": first_url,
                                           "first_anchor": first_name})
    rows = []
    for (lang, target), pages in sorted(groups.items()):
        own = site.pages[structure.homes[lang]]["url"]
        foreign = site.pages[target]["url"]
        rows.append(("breadcrumb_foreign_home", "low", None, None,
                     (f"{len(pages)} {lang} nyelvű oldalon a morzsa első eleme a(z) "
                      f"{home_lang[target]} nyelvű kezdőoldalra mutat ({foreign}); a(z) {lang} "
                      f"kezdőoldal: {own}"),
                     {"group": "breadcrumb_foreign_home", "lang": lang, "home": own,
                      "foreign_lang": home_lang[target], "foreign_home": foreign,
                      "pages": sorted(pages, key=lambda item: item["url"])}))
    return rows


def _menu_home(site: _Site) -> list[tuple]:
    """A `menu_home_target` megállapítás (lásd a modul leírását)."""
    structure = site.structure
    home_ids = set(structure.homes.values()) | ({structure.start} if structure.start is not None
                                                else set())
    rows = []
    for lang in sorted({item.lang for item in structure.menu}):
        header = [item for item in structure.menu if item.lang == lang and item.area == "header"]
        home_id = structure.homes.get(lang, structure.start)
        if home_id is None:
            continue
        home_url = site.pages[home_id]["url"]
        logo = next((item for item in header if item.marker == "logo" and item.url), None)
        for item in header:
            named = item.marker == "home_icon" or alias_key(item.anchor) in HOME_ANCHORS
            if not named or item.url is None or item.marker == "logo" \
                    or site.page_of(item.url) in home_ids:
                continue
            logo_home = logo is not None and site.page_of(logo.url) in home_ids
            in_set = item.page_id in site.pages
            rows.append((
                "menu_home_target", "medium", item.page_id if in_set else None, None,
                (f"A(z) {lang} menü „{item.anchor or 'kezdőoldal-ikon'}” pontja nem a "
                 f"kezdőoldalra mutat: {item.url} (a kezdőoldal: {home_url})"
                 + ("; a logó a kezdőoldalra mutat, így a site-nak két kezdőoldala van"
                    if logo_home else "")),
                {"group": "menu_home", "lang": lang, "home": home_url,
                 "menu_item": {"anchor": item.anchor, "url": item.url, "pages": item.pages,
                               "area_pages": item.area_pages, "in_set": in_set},
                 "logo": {"anchor": logo.anchor, "url": logo.url, "points_home": logo_home}
                 if logo is not None else None,
                 "two_homes": logo_home,
                 "pages": [{"url": site.pages[item.page_id]["url"]}] if in_set else []}))
    return rows


def _menu_targets(site: _Site) -> list[tuple]:
    """A `menu_broken_target` megállapítások (lásd a modul leírását)."""
    stored = {canonical_key(page.url): page for page in crawl.pages(site.con)}
    kinds = page_types(site.con, load_site_config(site_domain(site.con)).page_types)
    targets: dict[str, list] = defaultdict(list)
    for item in site.structure.menu:
        if item.url:
            targets[canonical_key(item.url)].append(item)
    rows = []
    for key, items in sorted(targets.items()):
        page = stored.get(key)
        if page is None or (page.error or "").startswith("non_html"):
            continue
        final = page.final_url if page.final_url and canonical_key(
            page.final_url.split("?", 1)[0]) != canonical_key(page.url.split("?", 1)[0]) else None
        if kinds.get(page.page_id) == "not_found":
            problem, detail = "not_found", ""
        elif page.status is None or page.status >= 400:
            problem = "error_status"
            detail = f": {page.status}" if page.status is not None else ": nem töltődött be"
        elif final:
            problem, detail = "redirect", f" ide: {final}"
        else:
            continue
        first = items[0]
        areas = ", ".join(dict.fromkeys(site_structure.AREA_LABELS[item.area] for item in items))
        in_set = page.page_id in site.pages
        rows.append((
            "menu_broken_target", "medium", page.page_id if in_set else None, None,
            (f"A menü hibás oldalra mutat: „{first.anchor or '(szöveg nélkül)'}” ({areas}) → "
             f"{page.url} — {MENU_TARGET_PROBLEMS[problem]}{detail}"),
            {"group": "menu_target", "url": page.url, "problem": problem,
             "problem_label": MENU_TARGET_PROBLEMS[problem] + detail, "status": page.status,
             "final_url": final,
             "menu_items": [{"anchor": item.anchor, "area": site_structure.AREA_LABELS[item.area],
                             "lang": item.lang, "pages": item.pages,
                             "area_pages": item.area_pages} for item in items]}))
    return rows


def _link_forms(site: _Site, menu_targets: Sequence[tuple] = ()) -> list[tuple]:
    """A `link_not_final_url` megállapítások (lásd a modul leírását). `menu_targets`: a
    `menu_broken_target` sorai (az ott jelzett átirányító menülink itt nem ismétlődik)."""
    stored = {page.url: page for page in crawl.pages(site.con)}
    variants = {variant.raw_url: variant for variant in crawl.link_variants(site.con)}
    in_menu_finding = {canonical_key(row[5]["url"]) for row in menu_targets
                       if row[5]["problem"] == "redirect"}
    profile = crawl.site(site.con)
    own_host = (urlsplit(profile.seed_url).hostname or "").lower().removeprefix("www.") \
        if profile is not None else ""
    patterns: dict[tuple[str, str], dict] = {}
    for link in crawl.counted_links(site.con):
        source = site.pages.get(link.from_page_id)
        if not link.raw_url or source is None or (urlsplit(
                link.raw_url).hostname or "").lower().removeprefix("www.") != own_host:
            continue
        differences = list(form_differences(link.raw_url, link.to_url))
        page = stored.get(link.to_url)
        final = None
        if page is not None and page.final_url and canonical_key(
                page.final_url.split("?", 1)[0]) != canonical_key(page.url.split("?", 1)[0]):
            if link.position in crawl.MENU_POSITIONS \
                    and canonical_key(page.url) in in_menu_finding:
                continue
            final = page.final_url
            differences.append(LINK_REDIRECT)
        if not differences:
            continue
        found = patterns.setdefault((link.raw_url, final or link.to_url), {
            "stored": link.to_url, "final": final, "differences": differences, "page": page,
            "sources": defaultdict(lambda: {"areas": set(), "anchors": set()}),
            "areas": Counter()})
        found["sources"][source["url"]]["areas"].add(LINK_AREAS.get(link.position,
                                                                     link.position))
        found["sources"][source["url"]]["anchors"].add(link.anchor or "")
        found["areas"][link.position] += 1
    rows = []
    for (raw_url, target), found in sorted(
            patterns.items(), key=lambda item: (-len(item[1]["sources"]), item[0])):
        variant, page = variants.get(raw_url), found["page"]
        final = variant.final_url if variant is not None and variant.final_url \
            else found["final"]
        if variant is not None and variant.status is not None:
            status, hops = variant.status, variant.hops
        elif found["final"] and page is not None:
            status, hops = page.status, page.redirect_hops
        else:
            status = hops = None
        count = len(found["sources"])
        in_menu = any(area in crawl.MENU_POSITIONS for area in found["areas"])
        by_area = {LINK_AREAS.get(area, area): number
                   for area, number in sorted(found["areas"].items())}
        in_set = page is not None and page.page_id in site.pages
        rows.append((
            "link_not_final_url",
            "medium" if in_menu or count >= LINK_FORM_MANY_PAGES else "low",
            page.page_id if in_set else None, None,
            (f"{count} oldalról mutat belső link erre a címre: {raw_url} — "
             + (f"a végleges cím: {final}" if final else f"{LINK_STORED_NOTE}: {target}")
             + f" (eltérés: {', '.join(found['differences'])})"),
            {"group": "link_form", "raw_url": raw_url, "stored_url": found["stored"],
             "final_url": final, "final_note": None if final else LINK_STORED_NOTE,
             "differences": found["differences"],
             "linked_status": LINK_NOT_MEASURED if status is None else status,
             "hops": LINK_NOT_MEASURED if hops is None else hops,
             "measured": variant is not None and variant.status is not None,
             "source_pages": count, "by_area": by_area,
             "pages": [{"url": url, "areas": sorted(item["areas"]),
                        "anchors": sorted(item["anchors"])}
                       for url, item in sorted(found["sources"].items())]}))
    return rows


def _orphans(site: _Site) -> list[tuple]:
    """Az `orphan_pages` megállapítások (lásd a modul leírását): legfeljebb kettő, a
    csoportonként egy."""
    structure = site.structure
    nodes = {page["page_id"]: page for page in site.nodes()}
    unreachable = {page_id for page_id, found in structure.pages.items() if found["unreachable"]}
    if not unreachable:
        return []
    page_rows = page_facts(site.con)
    stored = {page.page_id: page for page in crawl.pages(site.con)}
    declared = {meta.page_id: meta.canonical for meta in crawl.page_metas(site.con)}
    state = crawl.completeness(site.con)
    groups: dict[str, list[int]] = {key: [] for key in ORPHAN_GROUPS}
    for page_id in unreachable:
        sources = structure.inbound.get(page_id, set())
        if sources and sources <= unreachable:
            groups["linked_only_by_orphans"].append(page_id)
        elif not sources and page_rows.get(page_id, {}).get("csak sitemapből ismert") == "igen":
            groups["sitemap_only"].append(page_id)
    rows = []
    for group, members in groups.items():
        if not members:
            continue
        pages = []
        key_page = False
        for page_id in sorted(members, key=lambda p: nodes[p]["url"]):
            page = nodes[page_id]
            main = site.main(page_id)
            canonical = declared.get(page_id)
            own = not canonical or canonical_key(canonical) == canonical_key(page["url"])
            noindex = bool(stored[page_id].noindex) if page_id in stored else False
            hub = (site.hubs.get(page_id) or {}).get("label")
            key_page = key_page or page["role"] in ORPHAN_KEY_ROLES or bool(hub)
            pages.append({
                "url": page["url"],
                "role": page["role"] + (f" ({page['support']})" if page["support"] else ""),
                "hub": hub, "main_entity": site.shown(main[0], page) if main else None,
                "indexable": not noindex and own, "noindex": noindex,
                "canonical": None if own else canonical,
                "word_count": stored[page_id].word_count if page_id in stored else None,
                **({"linked_from": sorted(nodes[p]["url"]
                                          for p in structure.inbound.get(page_id, set()))}
                   if group == "linked_only_by_orphans" else {})})
        rows.append((
            "orphan_pages", "medium" if key_page else "low", None, None,
            (f"{len(pages)} oldal belső linken nem érhető el a kezdőoldalról: "
             f"{ORPHAN_GROUPS[group]}" + (f" ({ORPHAN_PARTIAL})" if state.partial else "")),
            {"group": group, "pages": pages,
             **({"crawl": "partial", "crawl_states": list(state.states),
                 "crawl_note": ORPHAN_PARTIAL} if state.partial else {})}))
    return rows


def _breadcrumb_menu(site: _Site) -> list[tuple]:
    """A `breadcrumb_ignores_menu` megállapítás (lásd a modul leírását)."""
    structure = site.structure
    home_ids = set(structure.homes.values()) | ({structure.start} if structure.start is not None
                                                else set())
    by_id = {item.item_id: item for item in structure.menu}
    nodes = {page["page_id"]: page for page in site.nodes()}
    seen: set[int] = set()
    flat = []
    for item in structure.menu:
        if item.area != "header" or item.level < 1 or item.page_id not in nodes \
                or item.page_id in seen or item.parent_id not in by_id:
            continue
        seen.add(item.page_id)
        above = by_id[item.parent_id]
        page = nodes[item.page_id]
        crumb = structure.crumbs.get(item.page_id)
        if crumb is None:
            continue
        trail = crumb[1]
        last = trail[-1][0]
        before = trail[:-1] if last is None \
            or canonical_key(last) == canonical_key(page["url"]) else trail
        if len(before) == 1 and site.page_of(before[0][0]) in home_ids:
            flat.append({"url": page["url"], "menu_parent": above.anchor,
                         "menu_parent_url": above.url,
                         "breadcrumb": [{"name": name, "url": url} for url, name in trail]})
    if len(flat) < CRUMB_FLAT_MIN or len(flat) < CRUMB_FLAT_SHARE * len(seen):
        return []
    return [("breadcrumb_ignores_menu", "low", None, None,
             (f"A fejléc-menü almenüiben álló {len(seen)} oldalból {len(flat)} morzsája csak "
              f"„kezdőoldal > oldal”: a menü-szülő nincs a morzsában"),
             {"group": "breadcrumb_menu", "submenu_pages": len(seen), "flat": len(flat),
              "pages": sorted(flat, key=lambda item: item["url"])})]


def _soft_404(site: _Site) -> list[tuple]:
    """A 200-as státusszal kiszolgált „nem található” oldalak (`pages.page_types`: not_found;
    ma a Shoprenter `not_found_body` jele ismeri fel) egy közepes megállapításban, az oldalak
    listájával: a keresőnek és a látogatónak létező oldalnak látszanak."""
    kinds = page_types(site.con, load_site_config(site_domain(site.con)).page_types)
    pages = sorted((page for page in site.pages.values()
                    if kinds.get(page["page_id"]) == "not_found"), key=lambda p: p["url"])
    if not pages:
        return []
    return [("soft_404", "medium", None, None,
             f"nem található oldal 200-as státusszal: {len(pages)} oldal",
             {"group": "soft_404", "pages": [{"url": page["url"], "title": page["title"]}
                                             for page in pages]})]


# ---------------------------------------------------------------------------
# kivonatok és nézetek
# ---------------------------------------------------------------------------


def _findings(con: duckdb.DuckDBPyConnection) -> list[dict]:
    return [dict(zip(("id", "type", "severity", "page_id", "entity_id", "summary", "evidence"),
                     (*row[:6], json.loads(row[6])), strict=True)) for row in con.execute(
        "SELECT finding_id, type, severity, page_id, entity_id, summary, evidence FROM findings "
        "ORDER BY finding_id").fetchall()]


def stored_findings(con: duckdb.DuckDBPyConnection) -> list[Finding]:
    """A tárolt megállapítások szerződésként, az azonosítójuk szerint."""
    cursor = con.execute("SELECT * FROM findings ORDER BY finding_id")
    names = [column[0] for column in cursor.description]
    return [Finding.from_row(dict(zip(names, row, strict=True))) for row in cursor.fetchall()]


def _affected(finding: dict) -> list[str]:
    """A megállapítás érintett oldalai (a lefedetlen témánál a legtöbbet említők)."""
    evidence = finding["evidence"]
    if "url" in evidence:
        return [evidence["url"]]
    if "not_articles" in evidence:              # a biztosan nem cikk oldalak, fajtánként
        return [p["url"] for found in evidence["not_articles"].values() for p in found]
    return [p["url"] for p in evidence.get("pages") or evidence.get("top_pages") or []]


def export_findings(con: duckdb.DuckDBPyConnection, out: Path, name: str) -> Path:
    """A megállapítások: `<név>-findings.csv` (típus, súlyosság, entitás, érintett oldalak,
    összefoglaló, bizonyíték)."""
    site = _Site(con)
    rows = [{"azonosító": f["id"], "típus": TYPE_LABELS[f["type"]], "súlyosság": f["severity"],
             "entitás": site.name(f["entity_id"]) if f["entity_id"] is not None else "",
             "oldalak": " | ".join(_affected(f)), "összefoglaló": f["summary"],
             "bizonyíték": dumps(f["evidence"], ensure_ascii=False)}
            for f in _findings(con)]
    return _write(out / f"{name}-findings.csv", rows,
                  ["azonosító", "típus", "súlyosság", "entitás", "oldalak", "összefoglaló",
                   "bizonyíték"])


def _relations(site: _Site) -> dict[int, list[tuple[str, str, int]]]:
    """entitás → (éltípus, irány, a másik entitás) az entitás–entitás élekből."""
    found: dict[int, list[tuple[str, str, int]]] = defaultdict(list)
    for from_id, to_id, kind in sorted(
            ((edge.from_id, edge.to_id, edge.type) for edge in graph_queries.edges(site.con)
             if edge.from_kind == "entity" and edge.to_kind == "entity"),
            key=lambda row: (row[2], row[0], row[1])):
        if from_id in site.entities and to_id in site.entities:
            found[from_id].append((kind, "→", to_id))
            found[to_id].append((kind, "←", from_id))
    return found


def _overview_ids(site: _Site) -> list[int]:
    """A site-áttekintő entitásai: a legnagyobb súlyúak és mindegyik, amelyik fő entitás."""
    ranked = sorted(site.weights, key=lambda e: site.rank[e])
    return [e for e in ranked if site.rank[e] <= OVERVIEW_TOP or site.weights[e]["main_pages"]]


def finding_items(site: _Site) -> list[dict]:
    """A megállapítások a megjelenítéshez: a típus címkéje, az entitás neve és az érintett
    oldalak a tárolt mezők mellett."""
    return [{**finding, "label": TYPE_LABELS[finding["type"]],
             "entity": site.name(finding["entity_id"]) if finding["entity_id"] is not None
             else None,
             "pages": _affected(finding)} for finding in _findings(site.con)]


def view_data(site: _Site) -> tuple[list[dict], list[dict]]:
    """A nézetek strukturált adata: (entitások, oldalak). Entitásonként (a site-áttekintő
    entitásai, a rangsor szerint): a kapcsolatok típus és irány szerint, a fő oldalak, a csak
    említő oldalak; oldalanként (URL szerint): a szerep, a canonical-döntés, a fő entitás a
    bizonyítékaival, a másodlagos entitások, a H1 és a title megnevezése, a további említett
    entitások a súlyukkal és az oldal megállapításai. A CSV- és HTML-nézetek és a JSON-szerződés
    ebből készül. Az entitásoknál a megtartott név áll, a többi nyelvű név külön
    (`other_names`); az oldalaknál az entitások neve az oldal nyelvén (`_Site.shown`). A
    megállapítások szövege a megtartott nevet használja."""
    relations = _relations(site)
    main_pages: dict[int, list[str]] = defaultdict(list)
    for page in site.nodes():
        main = site.main(page["page_id"])
        if main is not None:
            main_pages[main[0]].append(page["url"])
    mention_pages: dict[int, list[str]] = defaultdict(list)
    for page_id, edges in site.mention_edges.items():
        if page_id in site.pages and site.pages[page_id]["canonical"] is None:
            for entity_id, weight, _ in edges:
                if weight > 0:
                    mention_pages[entity_id].append(site.pages[page_id]["url"])
    by_url: dict[str, list[str]] = defaultdict(list)
    for finding in _findings(site.con):
        label = f"{TYPE_LABELS[finding['type']]} ({finding['severity']}): {finding['summary']}"
        if finding["type"] not in ("missing_page", "uncovered_topic"):
            for url in _affected(finding):
                by_url[url].append(label)
    entities = []
    for entity_id in _overview_ids(site):
        weight = site.weights[entity_id]
        entity = site.entities[entity_id]
        entities.append({
            "entity_id": entity_id, "name": entity["name"], "type": entity["type"],
            "subtype": entity["subtype"], "rank": site.rank[entity_id],
            "weight": weight["weight"], "pages": weight["pages"],
            "mentions": weight["mentions"],
            "relations": [{"type": kind, "direction": direction, "entity_id": other,
                           "entity": site.name(other)}
                          for kind, direction, other in relations.get(entity_id, [])],
            "main_pages": sorted(main_pages[entity_id]),
            "mention_only_pages": sorted(set(mention_pages[entity_id])
                                         - set(main_pages[entity_id])),
            "other_names": site.display.other_names(entity_id)})
    pages = []
    for page in sorted(site.pages.values(), key=lambda p: p["url"]):
        chosen = site.chosen.get(page["page_id"], [])
        main = next((c for c in chosen if c[1] == "main"), None)
        duplicate = page["canonical"] is not None
        in_h1, in_title, _ = site.named(page, main) if main else (False, False, [])
        picked = {c[0] for c in chosen}
        fields = site.title_fields.get(page["page_id"]) or titles.TitleFields()
        pages.append({
            "page_id": page["page_id"], "url": page["url"], "role": page["role"],
            "support_kind": page["support"], "lang": page["lang"],
            "duplicate_of": site.pages[page["canonical"]]["url"] if duplicate else None,
            "canonical_issue": page["issue"],
            "main_entity_id": main[0] if main else None,
            "main_entity": site.shown(main[0], page) if main else None,
            "main_entity_type": site.kind(main[0]) if main else None,
            "confidence": main[2] if main else None,
            "evidence": main[3] if main else None,
            "evidence_text": evidence_text(main[3]) if main else None,
            "secondary": [site.shown(c[0], page) for c in chosen if c[1] == "secondary"],
            "h1": page["h1"], "in_h1": in_h1 if main else None,
            "title": page["title"], "in_title": in_title if main else None,
            "title_cut": fields.title, "visible_title": fields.visible,
            "visible_title_element": fields.visible_element, "h1_count": fields.h1_count,
            "og_title": fields.og_title,
            "schema_titles": [{"field": label, "text": text} for label, text in fields.schema],
            "title_differences": list(fields.differing),
            "schema_about": [dict(item) for item in fields.about],
            "schema_types": list(fields.schema_types),
            "role_markup_note": role_markup_note(page["role"], fields.schema_types),
            "hub": (site.hubs.get(page["page_id"]) or {}).get("label"),
            "hub_children": [dict(child) for child in (
                site.hubs.get(page["page_id"]) or {}).get("children", [])],
            **page_structure_fields(site.structure.pages.get(page["page_id"])),
            "other_mentions": [{"entity_id": e, "entity": site.shown(e, page), "weight": w}
                               for e, w, _ in site.mention_edges[page["page_id"]]
                               if e not in picked and w > 0][:PAGE_MENTIONS],
            "findings": [] if duplicate else list(by_url[page["url"]]),
            "notes": [] if duplicate else page_notes(page),
            **_heading_view(site.headings.get(page["page_id"]),
                            lambda entity, page=page: site.shown(entity["entity_id"], page)
                            if entity["entity_id"] in site.entities else entity["entity"])})
    return entities, pages


def page_notes(page: Mapping) -> list[str]:
    """Az oldal jelzései a gráf döntéséből (`signals`): a kinyerés nem nevezett meg fő témát (a
    segédoldalon nem jelzés: annak nincs fő entitása), és a nyelvi pár fő entitása eltér."""
    signals = page.get("signals") or {}
    notes = []
    if signals.get("primary_empty") and page["status"] != "support":
        notes.append("a kinyerés nem nevezett meg fő témát")
    for other in signals.get("pair_differs") or []:
        notes.append(f"a nyelvi pár fő entitása eltér: {other['url']} → {other['entity']}")
    return notes


def _heading_view(data: dict | None, shown: Callable[[dict], str] | None = None) -> dict:
    """Az oldal heading-fája a nézethez: a fa (`headings`), a fő tartalmon kívüli H1-ek
    (`h1_outside`) és a több H1 megítélése (`h1_justified`). `shown`: a heading entitásának
    megjelenített neve (az oldal nyelvén)."""
    if data is None:
        return {"headings": [], "h1_outside": [], "h1_justified": None}

    def node_view(node: dict) -> dict:
        return {"level": node["level"], "text": node["text"],
                "entities": [{**entity, "entity": shown(entity)} if shown else dict(entity)
                             for entity in node["entities"]],
                "relation": node["relation"], "words": node["words"],
                "total_words": node["total_words"], "empty": node["empty"],
                "media_only": node["media_only"],
                "skipped_level": node["skipped_level"], "template": node["template"],
                "children": [node_view(child) for child in node["children"]]}

    return {"headings": [node_view(node) for node in data["tree"]],
            "h1_outside": [{"where": where, "text": text} for where, text in data["outside_h1"]],
            "h1_justified": data["h1_justified"]}


def heading_rows(pages: list[dict]) -> list[dict]:
    """A heading-fa CSV-sorai: oldalanként a headingek mélységi bejárásban."""
    rows = []
    for page in pages:
        for where in page["h1_outside"]:
            rows.append({"url": page["url"], "mélység": "", "szint": "H1",
                         "heading": where["text"], "entitások": "",
                         "kapcsolat a fő entitáshoz": "", "szavak": "", "szavak összesen": "",
                         "megjegyzés": "a fő tartalmon kívül: "
                                       + heading_tree.OUTSIDE_LABELS[where["where"]]})
        for root in page["headings"]:
            for row in heading_tree.tree_rows(root):
                notes = [text for flag, text in (
                    (row["empty"], "üres szakasz"),
                    (row["media_only"], "csak kép / űrlap"),
                    (row["skipped_level"], "kihagyott szint"),
                    (row["template"], "sablon-heading"),
                    (row["level"] == 1 and page["h1_justified"] is True, "indokolt több H1"),
                    (row["level"] == 1 and page["h1_justified"] is False,
                     "indokolatlan több H1")) if flag]
                rows.append({
                    "url": page["url"], "mélység": row["depth"], "szint": f"H{row['level']}",
                    "heading": row["text"],
                    "entitások": "; ".join(
                        f"{e['entity']} ({RELATION_LABELS[e['relation']]})"
                        for e in row["entities"]),
                    "kapcsolat a fő entitáshoz": RELATION_LABELS[row["relation"]],
                    "szavak": row["words"], "szavak összesen": row["total_words"],
                    "megjegyzés": "; ".join(notes)})
    return rows


TITLE_COLUMNS = ("title utótag nélkül", "látható cím", "a látható cím eleme", "H1-ek száma",
                 "og:title", "schema name / headline", "eltérő címmezők", "schema about",
                 "az about és a fő entitás", "schema típusok", "szerep és jelölés")
ROLE_MARKUP = {"cikk": titles.ARTICLE_TYPES, "ajánlat vagy termék": titles.OWN_THING_TYPES}
OWN_THING_PAGE_ROLES = ("offer", "product", "category")


def role_markup_note(role: str, schema_types: Iterable[str]) -> str | None:
    """A szerep és a schema.org-jelölés ellentmondása tényként (megállapítás nélkül): ajánlat-,
    termék- vagy kategória-szerepű oldal cikk jelöléssel (Article, BlogPosting, …) és saját
    Service / Product csomópont nélkül; vagy cikk szerepű oldal Service / Product jelöléssel
    és cikk jelölés nélkül. None, ha nincs ellentmondás."""
    kinds = set(schema_types)
    article, thing = kinds & ROLE_MARKUP["cikk"], kinds & ROLE_MARKUP["ajánlat vagy termék"]
    if role in OWN_THING_PAGE_ROLES and article and not thing:
        return (f"a szerep {ROLE_LABELS.get(role, role)}, a jelölés cikk "
                f"({', '.join(sorted(article))})")
    if role == "article" and thing and not article:
        return f"a szerep cikkoldal, a jelölés {', '.join(sorted(thing))}"
    return None



def title_columns(page: Mapping) -> dict:
    """Az oldal címmezői az oldalnézet oszlopaiként (`TITLE_COLUMNS`). Az about-nál: a
    hivatkozás neve, név nélküli `@id`-nél az `@id` és a csomópontjainak összes neve; mire
    mutat (entitás és típus), és azonos-e a fő entitással."""
    about = page["schema_about"]

    def named(item: Mapping) -> str:
        if item["name"]:
            return item["name"]
        names = " | ".join(item["names"]) or "nincs neve a készletben"
        return f"@id {item['id']} (nevei: {names})"

    def target(item: Mapping) -> str:
        if item["entity"] is None:
            return "nem oldható fel"
        same = {True: "azonos a fő entitással", False: "nem azonos a fő entitással",
                None: "az oldalnak nincs fő entitása"}[item["same_as_main"]]
        return f"{item['entity']} ({item['type']}): {same}"

    return {
        "title utótag nélkül": page["title_cut"] or "", "látható cím": page["visible_title"] or "",
        "a látható cím eleme": page["visible_title_element"] or "",
        "H1-ek száma": page["h1_count"], "og:title": page["og_title"] or "",
        "schema name / headline": "; ".join(f"{item['field']}: {item['text']}"
                                            for item in page["schema_titles"]),
        "eltérő címmezők": "; ".join(page["title_differences"]),
        "schema about": "; ".join(named(item) for item in about),
        "az about és a fő entitás": "; ".join(target(item) for item in about),
        "schema típusok": ", ".join(page["schema_types"]),
        "szerep és jelölés": page["role_markup_note"] or ""}


PAGE_FACT_COLUMNS = ("státusz", "végső URL", "meta description", "noindex", "hreflang",
                     "szószám", "külső linkek", "bejövő belső linkek", "hivatkozó oldalak",
                     "kimenő belső linkek", "önlinkek", "csak sitemapből ismert")


STRUCTURE_COLUMNS = ("mélység (minden link)", "mélység (csak menü)", "mélység (csak tartalom)",
                     "elérhetetlen a kezdőoldalról", "menüszint (fejléc)", "lábléc-menüben",
                     "oldalsáv-menüben", "morzsa-szint", "URL-szint", "szülő (menüfa)",
                     "szülő (morzsa)", "szülő (URL)", "a szülők egyeznek")
URL_PARENT_FLAT = "nem értelmezhető"
MENU_COLUMNS = ["nyelv", "terület", "szint", "menüpont", "URL", "szülő menüpont",
                "a készlet oldala", "fő entitás", "szerep", "kattintási mélység",
                "a cél noindex", "hány oldalon áll", "a területet hordozó oldalak", "jelölés"]
MENU_DIFFERENCE_COLUMNS = ["oldal", "nyelv", "terület", "eltérés", "menüpont", "horgonyszöveg",
                           "szülő"]
DIFFERENCE_LABELS = {"extra": "többlet", "missing": "hiány"}


def page_structure_fields(found: Mapping | None) -> dict:
    """Az oldal struktúra-mezői az oldalnézethez (`PageView`); a canonical-duplikátumnál és
    struktúra-adat nélkül mind None."""
    if found is None:
        return dict.fromkeys(
            ("click_depth", "click_depth_menu", "click_depth_content", "unreachable",
             "menu_level", "in_footer_menu", "in_sidebar_menu", "breadcrumb_level", "url_level",
             "menu_parent", "breadcrumb_parent", "url_parent", "url_parent_applicable",
             "parents_agree"))
    return {"click_depth": found["depth"]["all"], "click_depth_menu": found["depth"]["menu"],
            "click_depth_content": found["depth"]["body"], "unreachable": found["unreachable"],
            "menu_level": found["menu_level"], "in_footer_menu": found["in_footer"],
            "in_sidebar_menu": found["in_sidebar"], "breadcrumb_level": found["crumb_level"],
            "url_level": found["url_level"], "menu_parent": found["menu_parent"],
            "breadcrumb_parent": found["crumb_parent"], "url_parent": found["url_parent"],
            "url_parent_applicable": found["url_parent_applicable"],
            "parents_agree": found["parents_agree"]}


def structure_columns(page: Mapping) -> dict:
    """Az oldalnézet struktúra-oszlopai (`STRUCTURE_COLUMNS`). Ahol az URL-szülő nem számít
    (lapos URL-szerkezetű site, vagy egyszakaszos oldal), ott „nem értelmezhető”; a
    canonical-duplikátum sorai üresek."""
    def shown(value: object) -> object:
        return "" if value is None else value

    def yes(value: bool | None) -> str:
        return "" if value is None else "igen" if value else "nem"

    return {"mélység (minden link)": shown(page["click_depth"]),
            "mélység (csak menü)": shown(page["click_depth_menu"]),
            "mélység (csak tartalom)": shown(page["click_depth_content"]),
            "elérhetetlen a kezdőoldalról": "igen" if page["unreachable"] else "",
            "menüszint (fejléc)": shown(page["menu_level"]),
            "lábléc-menüben": yes(page["in_footer_menu"]),
            "oldalsáv-menüben": yes(page["in_sidebar_menu"]),
            "morzsa-szint": shown(page["breadcrumb_level"]),
            "URL-szint": shown(page["url_level"]),
            "szülő (menüfa)": shown(page["menu_parent"]),
            "szülő (morzsa)": shown(page["breadcrumb_parent"]),
            "szülő (URL)": URL_PARENT_FLAT if page["url_parent_applicable"] is False
            else shown(page["url_parent"]),
            "a szülők egyeznek": yes(page["parents_agree"])}


def structure_view(site: _Site) -> dict:
    """A site belső struktúrája a nézetekhez (`SiteStructureView`): a menüfa menüpontjai a
    céloldal fő entitásával, szerepével és kattintási mélységével, a mélység-eloszlás
    nyelvenként, a kezdőoldalról elérhetetlen oldalak és az URL-hierarchia mért feltétele."""
    structure = site.structure
    by_id = {item.item_id: item for item in structure.menu}
    menu = []
    for item in structure.menu:
        page = site.pages.get(item.page_id) if item.page_id is not None else None
        main = site.main(item.page_id) if page is not None else None
        above = by_id.get(item.parent_id) if item.parent_id is not None else None
        found = structure.pages.get(item.page_id) if page is not None else None
        menu.append({
            "lang": item.lang, "area": item.area, "level": item.level, "anchor": item.anchor,
            "url": item.url, "parent": (above.anchor or above.url) if above is not None else None,
            "in_set": page is not None,
            "main_entity": site.shown(main[0], page) if main is not None else None,
            "role": page["role"] if page is not None else None,
            "click_depth": found["depth"]["all"] if found is not None else None,
            "target_noindex": bool(page["noindex"]) if page is not None else None,
            "pages": item.pages, "area_pages": item.area_pages, "marker": item.marker})
    hierarchy = structure.url_hierarchy
    return {"menu": menu,
            "depth": [{"lang": row["lang"],
                       "home": site.pages[row["home"]]["url"] if row["home"] is not None
                       else None, "counts": row["counts"]} for row in structure.distribution()],
            "unreachable": sorted(site.pages[page_id]["url"]
                                  for page_id, found in structure.pages.items()
                                  if found["unreachable"]),
            "url_hierarchical": hierarchy["hierarchical"], "url_pages": hierarchy["pages"],
            "url_deep": hierarchy["deep"], "url_with_parent": hierarchy["with_parent"]}


def menu_rows(view: Mapping) -> list[dict]:
    """A menüfa a CSV-nézethez (`MENU_COLUMNS`), a tárolt sorrendben."""
    return [{"nyelv": item["lang"], "terület": site_structure.AREA_LABELS[item["area"]],
             "szint": item["level"], "menüpont": item["anchor"], "URL": item["url"] or "",
             "szülő menüpont": item["parent"] or "",
             "a készlet oldala": "igen" if item["in_set"] else "nem",
             "fő entitás": item["main_entity"] or "", "szerep": item["role"] or "",
             "kattintási mélység": "" if item["click_depth"] is None else item["click_depth"],
             "a cél noindex": "" if item["target_noindex"] is None
             else "igen" if item["target_noindex"] else "nem",
             "hány oldalon áll": item["pages"],
             "a területet hordozó oldalak": item["area_pages"],
             "jelölés": item["marker"] or ""} for item in view["menu"]]


def menu_difference_rows(site: _Site) -> list[dict]:
    """Az oldalfüggő menü-eltérések a CSV-nézethez (`MENU_DIFFERENCE_COLUMNS`)."""
    return [{"oldal": site.pages[item.page_id]["url"] if item.page_id in site.pages
             else item.page_id, "nyelv": item.lang,
             "terület": site_structure.AREA_LABELS[item.area],
             "eltérés": DIFFERENCE_LABELS[item.kind], "menüpont": item.url,
             "horgonyszöveg": item.anchor, "szülő": item.parent or ""}
            for item in site_structure.menu_differences(site.con)]


def page_facts(con: duckdb.DuckDBPyConnection) -> dict[int, dict]:
    """Oldalanként a crawl tényei az oldalnézethez (`PAGE_FACT_COLUMNS`), a crawl-modul
    szerződésein át: státuszkód, végső URL, meta description, noindex, hreflang (nyelv|URL
    párok), szószám, a külső linkek száma, a bejövő belső linkek száma (linksor), a
    hivatkozó oldalak száma (különböző forrásoldalak; a menü miatt a linksor-szám sokszoros
    lehet), és a kimenő belső linkek száma (linksor; a készleten kívüli belső cél is számít).
    A linksorok a menümásolatok nélkül számítanak (`crawl.counted_links`: menüterületen
    ugyanaz a forrás, cél és horgonyszöveg egyszer).
    A cél a feloldott céloldal (`crawl.links`, `link_targets`: a kategóriaúttal bővített cím is a
    készletbeli oldalnak számít); az oldal önmagára mutató linkjei ezekből kimaradnak, a
    számuk külön oszlop (`önlinkek`). `csak sitemapből ismert`: az oldalra nem mutat belső
    link (az önlinket nem számítva), de a legfrissebb tárolt sitemap-pillanatképben szerepel
    (igen / nem; üres, ha nincs tárolt sitemap-adat). A nem tárolt érték üres."""
    hreflang = {meta.page_id: meta.hreflang for meta in crawl.page_metas(con)}
    inbound: Counter[int] = Counter()
    outbound: Counter[int] = Counter()
    referrers: dict[int, set[int]] = defaultdict(set)
    own: Counter[int] = Counter()
    listed = crawl.sitemap_urls(con)
    snapshots = {item.snapshot for item in listed}
    current = "refetch" if "refetch" in snapshots else "crawl" if "crawl" in snapshots else None
    in_sitemap = {canonical_key(item.url) for item in listed
                  if item.snapshot == current and item.url and item.internal}

    def sitemap_only(page_id: int, url: str) -> str:
        if current is None:
            return ""
        return "igen" if not inbound[page_id] and canonical_key(url) in in_sitemap else "nem"

    for link in crawl.counted_links(con):
        target = link.to_page_id
        if target == link.from_page_id:
            own[link.from_page_id] += 1
            continue
        outbound[link.from_page_id] += 1
        if target is not None:
            inbound[target] += 1
            referrers[target].add(link.from_page_id)

    def shown(value: object) -> object:
        return "" if value is None else value

    return {page.page_id: {
        "státusz": shown(page.status), "végső URL": page.final_url or "",
        "meta description": page.meta_description or "",
        "noindex": "" if page.noindex is None else "igen" if page.noindex else "nem",
        "hreflang": " | ".join(hreflang.get(page.page_id) or []),
        "szószám": shown(page.word_count), "külső linkek": shown(page.external_link_count),
        "bejövő belső linkek": inbound[page.page_id],
        "hivatkozó oldalak": len(referrers[page.page_id]),
        "kimenő belső linkek": outbound[page.page_id], "önlinkek": own[page.page_id],
        "csak sitemapből ismert": sitemap_only(page.page_id, page.url)}
        for page in crawl.pages(con)}


def _views(site: _Site) -> tuple[list[dict], list[dict], list[dict]]:
    """A három CSV-nézet sorai a `view_data` adataiból: site-áttekintő (típus és rang szerint),
    entitásonkénti szomszédság (rang szerint), oldalak (URL szerint)."""
    entities, page_rows = view_data(site)

    def related(entity: dict, kind: str, direction: str) -> str:
        return "; ".join(r["entity"] for r in entity["relations"]
                         if r["type"] == kind and r["direction"] == direction)

    overview, neighbourhood = [], []
    for entity in entities:
        overview.append({
            "típus": entity["type"], "altípus": entity["subtype"] or "",
            "entitás": entity["name"],
            OTHER_NAMES_COLUMN: site.display.other_text(entity["entity_id"]),
            "rang": entity["rank"], "súly": entity["weight"],
            "szülő": related(entity, "part_of", "→"),
            "kategória": related(entity, "is_a", "→"),
            "márka": related(entity, "brand_of", "←"),
            "oldalak": entity["pages"], "említések": entity["mentions"],
            "fő oldalak": " | ".join(entity["main_pages"])})
        only = entity["mention_only_pages"]
        neighbourhood.append({
            "entitás": entity["name"],
            OTHER_NAMES_COLUMN: site.display.other_text(entity["entity_id"]),
            "típus": "/".join(filter(None, (entity["type"], entity["subtype"]))),
            "rang": entity["rank"], "súly": entity["weight"],
            "élek": "; ".join(f"{r['type']} {r['direction']} {r['entity']}"
                              for r in entity["relations"]),
            "fő oldalak": " | ".join(entity["main_pages"]),
            "csak említő oldalak száma": len(only), "csak említő oldalak": " | ".join(only)})
    overview.sort(key=lambda r: (r["típus"], r["rang"]))
    pages = []
    facts = page_facts(site.con)
    for page in page_rows:
        has_main = page["main_entity_id"] is not None
        pages.append({
            "url": page["url"], "szerep": page["role"], "segédoldal": page["support_kind"] or "",
            "nyelv": page["lang"] or "",
            "canonical": f"duplikátum: {page['duplicate_of']}" if page["duplicate_of"] is not None
            else page["canonical_issue"] or "",
            "fő entitás": page["main_entity"] if has_main else "",
            NAME_NOTE_COLUMN: site.display.note(page["main_entity_id"], page["lang"])
            if has_main else "",
            "típus": page["main_entity_type"] if has_main else "",
            "megbízhatóság": page["confidence"] if has_main else "",
            "bizonyítékok": page["evidence_text"] if has_main else "",
            "másodlagos": "; ".join(page["secondary"]),
            "H1": page["h1"] or "", "a H1-ben": _yes(has_main or None, bool(page["in_h1"])),
            "title": page["title"] or "",
            "a title-ben": _yes(has_main or None, bool(page["in_title"])),
            **title_columns(page),
            "hub": page["hub"] or "",
            "hub gyerekei": " | ".join(f"{child['url']} ({child['source']})"
                                       for child in page["hub_children"]),
            **structure_columns(page),
            "további említett entitások": "; ".join(
                f"{m['entity']} ({m['weight']:g})" for m in page["other_mentions"]),
            "megállapítások": " || ".join(page["findings"]),
            "jelzések": " || ".join(page["notes"]),
            **facts.get(page["page_id"], dict.fromkeys(PAGE_FACT_COLUMNS, ""))})
    return overview, neighbourhood, pages


def site_views(con: duckdb.DuckDBPyConnection, name: str, domain: str | None = None
               ) -> SiteViews:
    """A megállapítások és a nézetek szerződésként (a riport bemenete): a megállapítások az
    azonosítójuk, az entitások a rangjuk, az oldalak az URL-jük szerint."""
    site = _Site(con)
    entities, pages = view_data(site)
    return SiteViews(
        site=name, domain=domain,
        findings=[FindingView(
            finding_id=f["id"], type=f["type"], label=f["label"], severity=f["severity"],
            entity_id=f["entity_id"], entity=f["entity"], pages=f["pages"],
            summary=f["summary"], evidence=f["evidence"]) for f in finding_items(site)],
        entities=[EntityView(**entity) for entity in entities],
        pages=[PageView(**page) for page in pages],
        structure=SiteStructureView(**structure_view(site)))


def _page_tree_html(page: dict | None) -> str:
    if page is None:
        return ""
    outside = "".join(
        f"<div>H1 a fő tartalmon kívül ({_e(heading_tree.OUTSIDE_LABELS[o['where']])}): "
        f"{_e(o['text'])}</div>" for o in page["h1_outside"])
    justified = {True: "<div>több H1: indokolt</div>",
                 False: "<div>több H1: indokolatlan</div>"}.get(page["h1_justified"], "")
    return outside + justified + (_tree_html(page["headings"]) or "<span>nincs heading</span>")


def _yes(main: object | None, named: bool) -> str:
    return "" if main is None else "igen" if named else "nem"


LANGUAGE_PAIR_COLUMNS = ["entitás", "a megtartott név", "nyelv", "oldal", "a másik nyelvű név",
                         "a másik nyelv", "a másik oldal", "típus", "altípus", "alap"]
LANGUAGE_PAIR_BASIS = ("következtetett: a hreflang-pár két oldalának fő témája "
                       "(language_pair)")


def language_pair_rows(site: _Site) -> list[dict]:
    """A nyelvi összevonások soronként, az összevonások naplójából: melyik két nyelvi alak
    lett egy entitás, és melyik két oldal alapján. Minden sor következtetés, nem tárolt tény."""
    return [{"entitás": site.name(merge["entity_id"]) if merge["entity_id"] in site.entities
             else merge["kept_name"], "a megtartott név": merge["kept_name"],
             "nyelv": merge["kept_lang"], "oldal": merge["kept_page"],
             "a másik nyelvű név": merge["removed_name"],
             "a másik nyelv": merge["removed_lang"], "a másik oldal": merge["removed_page"],
             "típus": merge["type"] or "", "altípus": merge["subtype"] or "",
             "alap": LANGUAGE_PAIR_BASIS}
            for merge in resolver_queries.language_pair_merges(site.con)]


def export_views(con: duckdb.DuckDBPyConnection, out: Path, name: str) -> dict[str, Path]:
    """A négy nézet CSV-ben (`<név>-view-site.csv`, `-view-entities.csv`, `-view-pages.csv`,
    `-view-headings.csv`), a nyelvi összevonások listája (`-view-language-pairs.csv`), a menüfa
    (`-view-menu.csv`) az oldalfüggő eltéréseivel (`-view-menu-differences.csv`) és egy
    lenyitható HTML-oldal (`<név>-views.html`) a megállapításokkal együtt."""
    out.mkdir(parents=True, exist_ok=True)
    site = _Site(con)
    overview, neighbourhood, pages = _views(site)
    structure = structure_view(site)
    trees = {page["url"]: page for page in view_data(site)[1]}
    paths = {
        "site": _write(out / f"{name}-view-site.csv", overview, [
            "típus", "altípus", "entitás", OTHER_NAMES_COLUMN, "rang", "súly", "szülő",
            "kategória", "márka", "oldalak", "említések", "fő oldalak"]),
        "entities": _write(out / f"{name}-view-entities.csv", neighbourhood, [
            "entitás", OTHER_NAMES_COLUMN, "típus", "rang", "súly", "élek", "fő oldalak",
            "csak említő oldalak száma", "csak említő oldalak"]),
        "pages": _write(out / f"{name}-view-pages.csv", pages, [
            "url", "szerep", "segédoldal", "nyelv", "canonical", "fő entitás",
            NAME_NOTE_COLUMN, "típus", "megbízhatóság", "bizonyítékok", "másodlagos", "H1", "a H1-ben", "title",
            "a title-ben", *TITLE_COLUMNS, "hub", "hub gyerekei", *STRUCTURE_COLUMNS,
            "további említett entitások", "megállapítások",
            "jelzések",
            *PAGE_FACT_COLUMNS]),
        "headings": _write(out / f"{name}-view-headings.csv",
                           heading_rows(list(trees.values())), [
            "url", "mélység", "szint", "heading", "entitások", "kapcsolat a fő entitáshoz",
            "szavak", "szavak összesen", "megjegyzés"]),
        "language_pairs": _write(out / f"{name}-view-language-pairs.csv",
                                 language_pair_rows(site), LANGUAGE_PAIR_COLUMNS),
        "menu": _write(out / f"{name}-view-menu.csv", menu_rows(structure), MENU_COLUMNS),
        "menu_differences": _write(out / f"{name}-view-menu-differences.csv",
                                   menu_difference_rows(site), MENU_DIFFERENCE_COLUMNS)}
    paths["html"] = out / f"{name}-views.html"
    paths["html"].write_text(
        _html(name, _findings(con), site, overview, neighbourhood, pages, trees, structure),
        encoding="utf-8")
    return paths


def _write(path: Path, rows: list[dict], columns: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


STYLE = """
body{font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px;color:#1c1c1c;
background:#fff}
h1{font-size:1.4em}h2{font-size:1.15em;margin-top:2em;border-bottom:1px solid #ccc}
details{border:1px solid #ddd;border-radius:6px;margin:6px 0;padding:6px 10px}
details details{margin-left:8px}summary{cursor:pointer;font-weight:600}
summary span{font-weight:400;color:#555}
table{border-collapse:collapse;width:100%;margin:6px 0}
td,th{border-top:1px solid #eee;padding:3px 6px;text-align:left;vertical-align:top}
th{width:14em;color:#555;font-weight:400}
.wrap{overflow-x:auto}.high{color:#a40000}.medium{color:#8a5a00}.low{color:#555}
a{color:#0b57d0;word-break:break-all}
@media (prefers-color-scheme:dark){body{background:#151515;color:#e6e6e6}
details{border-color:#444}td,th{border-color:#333}th,summary span,.low{color:#aaa}
h2{border-color:#555}a{color:#8ab4f8}.high{color:#ff8a80}.medium{color:#ffcc80}}
"""


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _link(url: str) -> str:
    return f'<a href="{_e(url)}">{_e(url)}</a>'


def _links(text: str) -> str:
    return "<br>".join(_link(u) for u in text.split(" | ") if u)


def _table(pairs: list[tuple[str, str]]) -> str:
    rows = "".join(f"<tr><th>{_e(k)}</th><td>{v}</td></tr>" for k, v in pairs if v)
    return f'<div class="wrap"><table>{rows}</table></div>'


def _finding_html(finding: dict) -> str:
    evidence = finding["evidence"]
    pairs: list[tuple[str, str]] = []
    if finding["type"] == "h1_title_mismatch" and "pages" in evidence:
        pairs = [(p["main_entity"], (
            f"{_link(p['url'])}<br>H1: {_e(p['h1']) or '(nincs)'}<br>title: {_e(p['title'])}"
            + (f"<br>{_e(p['visible_title_note'])}" if p.get("visible_title_note") else "")))
            for p in evidence["pages"]]
    elif finding["type"] == "h1_title_mismatch":
        pairs = [("oldal", _link(evidence["url"])), ("fő entitás", _e(evidence["main_entity"])),
                 ("H1", _e(evidence["h1"]) or "(nincs)"), ("title", _e(evidence["title"])),
                 ("a H1 más entitásai", _e("; ".join(evidence["h1_entities"]))),
                 ("elfogadott megnevezések", _e("; ".join(evidence["names"]))),
                 ("látható cím", _e(evidence.get("visible_title_note", "")))]
    elif finding["type"] in ("cannibalization", "shared_topic"):
        pairs = [("oldalak", "<br>".join(
            f"{_link(p['url'])} — {_e(p['role'])}; title: {_e(p['title'])}; másodlagos: "
            f"{_e('; '.join(p['secondary']) or '—')}" for p in evidence["pages"])),
            ("átfedés", "<br>".join(
                f"{_e(' ↔ '.join(o['pages']))}: közös másodlagos: "
                f"{_e('; '.join(o['secondary']) or '—')}, title-hasonlóság "
                f"{o['title_similarity']}"
                + (f", viszony: {_e(o['relation'])}, tartalmi link: {_e(o['content_links'])}, "
                   f"bármilyen belső link: {'van' if o['any_link'] else 'nincs'}"
                   if "relation" in o else "") for o in evidence["overlaps"]))]
    elif finding["type"] in ("missing_page", "uncovered_topic"):
        pairs = [("teendő", _e(evidence["action"])),
                 ("alcsaládjai", _e("; ".join(f"{c['entity']} ({c['page_count']} oldal)"
                                              for c in evidence["subfamilies"]))),
                 ("súly és rang", _e(f"{evidence['weight']} ({evidence['rank']}. a "
                                     f"{evidence['ranked']}-ból)")),
                 ("említés", _e(f"{evidence['page_count']} oldal, {evidence['mentions']} említés, "
                                f"{evidence['structural']} oldalon szerkezeti helyen")),
                 ("a legtöbbet említő oldalak", "<br>".join(
                     f"{_link(p['url'])} ({p['mention_weight']:g})"
                     for p in evidence["top_pages"]))]
    elif finding["type"] in heading_tree.STRUCTURE_TYPES:
        pairs = [("oldal", _link(page["url"]) + "<br>" + _e(_structure_detail(page)))
                 for page in evidence.get("pages") or [evidence]]
    elif finding["type"] == "canonical_issue":
        pairs = [("oldal", _link(evidence["url"])), ("canonical", _e(evidence["canonical"])),
                 ("ok", _e(CANONICAL_LABELS.get(evidence["issue"], evidence["issue"])))]
    elif finding["type"] == "article_markup_on_other_pages":
        def listed(found: list[dict]) -> str:
            return "<br>".join(f"{_link(p['url'])} — {_e(', '.join(p['schema_types']))}; "
                               f"mai szerep: {_e(p['role'])}" for p in found)

        pairs = [("számok", _e(
            f"{evidence['html_pages']} HTML oldal, {evidence['marked']} cikk-jelölésű "
            f"({evidence['share']:.0%}); típusonként: "
            + ", ".join(f"{kind} {count}" for kind, count in evidence["by_type"].items())))]
        pairs += [(f"biztosan nem cikk: {label} ({len(found)})",
                   f"<details><summary>{len(found)} oldal</summary>{listed(found)}</details>")
                  for label, found in evidence["not_articles"].items()]
        pairs.append((f"cikk lehet ({len(evidence['maybe_articles'])})",
                      (f"<details><summary>{len(evidence['maybe_articles'])} oldal</summary>"
                       f"{listed(evidence['maybe_articles'])}</details>")))
    elif finding["type"] == "soft_404":
        pairs = [("oldalak", "<br>".join(_link(page["url"]) for page in evidence["pages"]))]
    elif finding["type"] == "schema_id_names":
        pairs = [("azonosítók", "<br>".join(
            f"{_e(item['id'])}"
            + (" [több nyelv neve áll alatta: " + _e(", ".join(item["languages"])) + "]"
               if item.get("several_languages") else "")
            + (" [a csomópont url-je nem a saját oldalára mutat: "
               + _e(", ".join(item["node_urls"])) + "]" if item.get("url_mismatch") else "")
            + ": " + "; ".join(
                (f"{_e(n['lang'])}: " if n.get("lang") else "")
                + f"„{_e(n['name'])}” ({n['pages']} oldal: "
                + ", ".join(_link(url) for url in n["urls"]) + ")"
                for n in item["names"]) for item in evidence["ids"]))]
    elif finding["type"] == "title_without_main_entity":
        pairs = [(p["main_entity"], (
            f"{_link(p['url'])}<br>title: {_e(p['title'])}<br>látható cím "
            f"({_e(p['visible_title_element'] or 'nincs')}): {_e(p['visible_title'])}<br>"
            f"elfogadott megnevezések: {_e('; '.join(p['names']))}"
            + ("<br>a cím ezt nevezi meg: "
               + _e("; ".join(f"{t['entity']} ({t['type']})" for t in p["title_entities"]))
               if p.get("title_entities") else "")
            + (f"<br>cikk jellegű: {_e(p['article_basis'])}" if p["article_basis"] else "")))
            for p in evidence.get("pages") or [evidence]]
        pairs.append(("állítás", _e(f"tény: {evidence['claim']['fact']}; következtetés: "
                                    f"{evidence['claim']['inference']}")))
    elif finding["type"] == "breadcrumb_foreign_home":
        pairs = [("a nyelv kezdőoldala", _link(evidence["home"])),
                 ("a morzsa első eleme ide mutat", _link(evidence["foreign_home"])),
                 ("oldalak", "<br>".join(
                     f"{_link(page['url'])} — morzsa: „{_e(page['first_anchor'])}” → "
                     f"{_link(page['first_url'])}" for page in evidence["pages"]))]
    elif finding["type"] == "menu_broken_target":
        pairs = [("cél", _link(evidence["url"])), ("hiba", _e(evidence["problem_label"])),
                 ("menüpontok", "<br>".join(
                     f"„{_e(item['anchor'])}” — {_e(item['area'])}, {_e(item['lang'])}: "
                     f"{item['pages']} / {item['area_pages']} oldalon"
                     for item in evidence["menu_items"]))]
    elif finding["type"] == "link_not_final_url":
        pairs = [("a link címe", _link(evidence["raw_url"])),
                 ("a tárolt cél", _link(evidence["stored_url"])),
                 ("végleges cím", _link(evidence["final_url"]) if evidence["final_url"]
                  else f"{LINK_NOT_MEASURED} ({_e(evidence['final_note'])}: "
                       f"{_link(evidence['stored_url'])})"),
                 ("eltérés", _e(", ".join(evidence["differences"]))),
                 ("a linkelt alak státusza / ugrások", _e(f"{evidence['linked_status']} / "
                                                         f"{evidence['hops']}")),
                 ("területek", _e(", ".join(f"{area}: {number}" for area, number
                                            in evidence["by_area"].items()))),
                 (f"forrásoldalak ({evidence['source_pages']})", "<br>".join(
                     f"{_link(page['url'])} — {_e(', '.join(page['areas']))}: "
                     f"„{_e('”, „'.join(page['anchors']))}”" for page in evidence["pages"]))]
    elif finding["type"] == "orphan_pages":
        pairs = [("csoport", _e(ORPHAN_GROUPS[evidence["group"]])),
                 ("bejárás", _e(evidence.get("crawl_note", "teljes"))),
                 ("oldalak", "<br>".join(
                     f"{_link(page['url'])} — {_e(page['role'])}"
                     + (f"; {_e(page['hub'])}" if page.get("hub") else "")
                     + f"; fő entitás: {_e(page['main_entity'] or '—')}; indexelhető: "
                     f"{'igen' if page['indexable'] else 'nem'}; szószám: "
                     f"{_e(page['word_count'])}" for page in evidence["pages"]))]
    elif finding["type"] == "breadcrumb_ignores_menu":
        pairs = [("oldalak", "<br>".join(
            f"{_link(page['url'])} — menü-szülő: „{_e(page['menu_parent'])}”; morzsa: "
            + _e(" > ".join(part["name"] or "(név nélkül)" for part in page["breadcrumb"]))
            for page in evidence["pages"]))]
    elif finding["type"] == "menu_home_target":
        item, logo = evidence["menu_item"], evidence["logo"]
        pairs = [("menüpont", (f"„{_e(item['anchor'])}” → {_link(item['url'])} "
                               f"({item['pages']} / {item['area_pages']} oldalon)")),
                 ("kezdőoldal", _link(evidence["home"])),
                 ("logó", (f"„{_e(logo['anchor'])}” → {_link(logo['url'])} — a kezdőoldalra "
                           f"mutat: {'igen' if logo['points_home'] else 'nem'}")
                  if logo else "nincs logólink a fejléc-menüben"),
                 ("két kezdőoldal", "igen" if evidence["two_homes"] else "nem")]
    elif finding["type"] == "legal_page":
        pairs = [("fajta", _e(evidence["label"])),
                 ("oldalak", "<br>".join(_link(page["url"]) for page in evidence["pages"])),
                 ("más jogi oldalon belül", "<br>".join(
                     f"{_link(page['url'])}: {_e('; '.join(page['headings']))}"
                     for page in evidence["inside"]))]
        if evidence.get("links"):
            pairs.append(("hivatkozott, be nem járt cél", "<br>".join(
                _link(link["url"]) for link in evidence["links"])))
    else:
        pairs = [("oldal", _link(evidence["url"])), ("H1", _e(evidence["h1"])),
                 ("title", _e(evidence["title"])),
                 ("bizonyítékok", _e(evidence_text(evidence["evidence"]))
                  if "evidence" in evidence else "")]
    return (f'<details><summary><span class="{finding["severity"]}">[{finding["severity"]}]'
            f'</span> {_e(finding["summary"])}</summary>{_table(pairs)}</details>')


def _structure_detail(page: dict) -> str:
    """Egy szerkezeti megállapítás oldalának részletei egy sorban."""
    parts = []
    if "outside" in page:
        parts.append("; ".join(f"{heading_tree.OUTSIDE_LABELS[o['where']]}: „{o['text']}”"
                               for o in page["outside"]))
        if page.get("content_h1"):
            parts.append("H1 a fő tartalomban: " + "; ".join(page["content_h1"]))
    if "h1" in page:
        parts.append("H1-ek: " + "; ".join(page["h1"]))
        parts.append("; ".join(page["reasons"]))
    if isinstance(page.get("headings"), list):
        parts.append("; ".join(page["headings"]))
    if "words" in page:
        parts.append(f"{page['words']} szó a fő tartalomban, H2 nélkül")
    return " — ".join(part for part in parts if part)


def _tree_html(nodes: list[dict]) -> str:
    """A heading-fa lenyitható listaként."""
    if not nodes:
        return ""
    items = []
    for node in nodes:
        entities = "; ".join(f"{e['entity']} ({RELATION_LABELS[e['relation']]})"
                             for e in node["entities"]) or RELATION_LABELS[node["relation"]]
        notes = "".join(f" <b>[{text}]</b>" for flag, text in (
            (node["empty"], "üres szakasz"), (node["media_only"], "csak kép / űrlap"),
            (node["skipped_level"], "kihagyott szint"),
            (node["template"], "sablon")) if flag)
        label = (f"H{node['level']} {_e(node['text'])} <span>— {_e(entities)}; "
                 f"{node['total_words']} szó</span>{notes}")
        items.append(f"<li><details><summary>{label}</summary>{_tree_html(node['children'])}"
                     f"</details></li>" if node["children"] else f"<li>{label}</li>")
    return f"<ul>{''.join(items)}</ul>"


def _structure_html(structure: Mapping) -> str:
    """A belső struktúra a site-áttekintőben: a menüfa nyelvenként és területenként behúzva, a
    mélység-eloszlás és a kezdőoldalról elérhetetlen oldalak."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in structure["menu"]:
        groups[(item["lang"], item["area"])].append(item)
    trees = []
    for (lang, area), items in groups.items():
        lines = "".join(
            f'<div style="margin-left:{1.5 * item["level"]:g}em">'
            f"{_e(item['anchor'] or '(szöveg nélkül)')} — "
            + (_link(item["url"]) if item["url"] else "link nélküli címke")
            + (f" <span>— fő entitás: {_e(item['main_entity'] or '—')}; szerep: "
               f"{_e(item['role'])}; mélység: "
               f"{'elérhetetlen' if item['click_depth'] is None else item['click_depth']}"
               f"{'; a cél noindex' if item['target_noindex'] else ''}</span>"
               if item["in_set"] else
               (" <span>— nincs a készlet oldalai között</span>" if item["url"] else ""))
            + f" <span>({item['pages']} / {item['area_pages']} oldalon)</span></div>"
            for item in items)
        trees.append(f"<details><summary>{_e(lang or 'nyelv nélkül')} — "
                     f"{site_structure.AREA_LABELS[area]} <span>({len(items)} menüpont)</span>"
                     f"</summary>{lines}</details>")
    buckets = site_structure.DEPTH_BUCKETS
    depth = "".join(
        f"<tr><td>{_e(row['lang'] or 'nyelv nélkül')}</td><td>{_link(row['home'] or '')}</td>"
        + "".join(f"<td>{row['counts'].get(bucket, 0)}</td>" for bucket in buckets) + "</tr>"
        for row in structure["depth"])
    hierarchy = (
        f"URL-szerkezet: {'hierarchikus' if structure['url_hierarchical'] else 'lapos'} "
        f"({structure['url_pages']} nem kezdőoldalból {structure['url_deep']} áll legalább két "
        f"útvonal-szakaszon, ezekből {structure['url_with_parent']} szülő útvonalán áll létező "
        f"oldal); lapos szerkezetnél az URL-szülő nem értelmezhető.")
    return (
        f"<details><summary>Menüfa <span>({len(structure['menu'])} menüpont)</span></summary>"
        + "".join(trees) + "</details>"
        "<details><summary>Kattintási mélység <span>(a saját nyelvű kezdőoldaltól, minden "
        'link)</span></summary><div class="wrap"><table><tr><td>nyelv</td><td>kezdőoldal</td>'
        + "".join(f"<td>{_e(bucket)}</td>" for bucket in buckets) + f"</tr>{depth}</table></div>"
        f"<p>{_e(hierarchy)}</p></details>"
        f"<details><summary>A kezdőoldalról elérhetetlen oldalak <span>"
        f"({len(structure['unreachable'])})</span></summary>"
        + "<br>".join(_link(url) for url in structure["unreachable"]) + "</details>")


def _html(name: str, findings: list[dict], site: _Site, overview: list[dict],
          neighbourhood: list[dict], pages: list[dict],
          trees: dict[str, dict] | None = None, structure: Mapping | None = None) -> str:
    parts = [(f'<!doctype html><html lang="hu"><head><meta charset="utf-8">'
              f'<meta name="viewport" content="width=device-width,initial-scale=1">'
              f"<title>{_e(name)} — entitásgráf-nézetek</title><style>{STYLE}</style></head>"
              f"<body><h1>{_e(name)} — megállapítások és nézetek</h1>"
              f"<p>{len(site.pages)} oldal, {len(site.weights)} súlyozott entitás, "
              f"{len(findings)} megállapítás. Ellenőrző nézet, nem ügyfélriport.</p>")]
    parts.append("<h2>Megállapítások</h2>")
    for kind in TYPES:
        group = [f for f in findings if f["type"] == kind]
        counts = Counter(f["severity"] for f in group)
        detail = ", ".join(f"{counts[s]} {s}" for s in SEVERITY_ORDER if counts[s])
        parts.append(f"<details><summary>{TYPE_LABELS[kind]} <span>({len(group)}"
                     f"{': ' + detail if detail else ''})</span></summary>"
                     + "".join(_finding_html(f) for f in group) + "</details>")
    parts.append("<h2>Site-áttekintő</h2>")
    hub_rows = [row for row in pages if row["hub"]]
    parts.append(
        f"<details><summary>Hubok <span>({len(hub_rows)})</span></summary>" + _table([
            (f"{_e(row['hub'])}: {_link(row['url'])}",
             "<br>".join(_e(child) for child in row["hub gyerekei"].split(" | ")))
            for row in hub_rows]) + "</details>")
    if structure is not None:
        parts.append(_structure_html(structure))
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in overview:
        by_type[row["típus"]].append(row)
    for kind, rows in sorted(by_type.items()):
        body = "".join(
            f"<tr><td>{r['rang']}.</td><td>{_e(r['entitás'])}"
            f"{' <span>(' + _e(r['altípus']) + ')</span>' if r['altípus'] else ''}</td>"
            f"<td>{r['súly']}</td><td>{_e(r['szülő'] or r['kategória'] or r['márka'])}</td>"
            f"<td>{_links(r['fő oldalak'])}</td></tr>" for r in rows)
        parts.append(f"<details><summary>{_e(kind)} <span>({len(rows)})</span></summary>"
                     f'<div class="wrap"><table><tr><td>rang</td><td>entitás</td><td>súly</td>'
                     f"<td>szülő / kategória / márka</td><td>fő oldalak</td></tr>{body}"
                     f"</table></div></details>")
    parts.append("<h2>Entitás környezete</h2>")
    for row in neighbourhood:
        parts.append(
            f"<details><summary>{_e(row['entitás'])} <span>({_e(row['típus'])}; "
            f"{row['rang']}., súly {row['súly']})</span></summary>" + _table([
                ("élek", _e(row["élek"]).replace("; ", "<br>")),
                ("fő oldalak", _links(row["fő oldalak"])),
                (f"csak említi ({row['csak említő oldalak száma']})",
                 _links(row["csak említő oldalak"]))]) + "</details>")
    parts.append("<h2>Oldalnézet</h2>")
    for row in pages:
        label = row["fő entitás"] or row["segédoldal"] or "nincs fő entitás"
        parts.append(
            f"<details><summary>{_e(urlsplit(row['url']).path or '/')}"
            f"{'?' + _e(urlsplit(row['url']).query) if urlsplit(row['url']).query else ''} "
            f"<span>— {_e(label)}</span></summary>" + _table([
                ("URL", _link(row["url"])),
                ("szerep", _e(" / ".join(filter(None, (row["szerep"], row["segédoldal"]))))),
                ("canonical", _e(row["canonical"])),
                ("fő entitás", _e(f"{row['fő entitás']} ({row['típus']}; "
                                  f"{row['megbízhatóság']})") if row["fő entitás"] else ""),
                ("bizonyítékok", _e(row["bizonyítékok"])),
                ("másodlagos", _e(row["másodlagos"])),
                ("H1", f"{_e(row['H1'])} <span>— benne a fő entitás: {row['a H1-ben']}</span>"
                 if row["fő entitás"] else _e(row["H1"])),
                ("title", f"{_e(row['title'])} <span>— benne a fő entitás: "
                          f"{row['a title-ben']}</span>" if row["fő entitás"]
                 else _e(row["title"])),
                ("hub", _e(row["hub"]) + ("<br>" + "<br>".join(
                    _e(child) for child in row["hub gyerekei"].split(" | "))
                    if row["hub"] else "")),
                ("struktúra", "<br>".join(
                    f"{_e(column)}: {_e(row[column])}" for column in STRUCTURE_COLUMNS
                    if row[column] not in ("", None))),
                ("címmezők", "<br>".join(
                    f"{_e(column)}: {_e(row[column])}" for column in TITLE_COLUMNS
                    if row[column] not in ("", None))),
                ("amit még említ", _e(row["további említett entitások"])),
                ("megállapítások", _e(row["megállapítások"]).replace(" || ", "<br>")),
                ("jelzések", _e(row["jelzések"]).replace(" || ", "<br>")),
                ("heading-fa", _page_tree_html((trees or {}).get(row["url"])))])
            + "</details>")
    parts.append("</body></html>")
    return "".join(parts)
