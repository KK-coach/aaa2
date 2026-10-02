# Táblatulajdonlás: javaslat a közös táblákra (döntésre vár)

Készült 2026-10-02-án, az architektúra 3. lépésében. Ahol a gazda egyértelmű (a crawl, a gráf és
az llm táblái, és az extract, illetve a resolve azon táblái, amelyeket csak ők írnak), ott a csere
megtörtént: 182 idegen hozzáférésből 84. A maradék 98 (23 írás, 75 olvasás) mind érint legalább
egyet abból az öt táblából, amelyet ma az extract és a resolve közösen ír (`entities`,
`page_entities`, `mention_sources`, `soft_checks`, `entity_runs`). Ezeknél a mai gazda vagy a
függési irány miatt nem tartható, ezért az átírás előtt döntés kell.

## A 23 idegen írás

| tábla | mai gazda | ki írja idegenként | hány hely | mit csinál |
|---|---|---|---|---|
| `entities` | resolve | extract (`rules.py`, `extract.py`) | 8 | a szabálykör és az LLM-kör létrehozza az entitást, aliast fűz hozzá, szavazatot ír, és törli az említés nélkülit |
| `page_entities` | extract | resolve (`merge`, `navigation`, `offers`, `flags`) | 7 | összevonáskor az említés átkerül, az anchor és a kártyacím új említés, a sablonjelölés módosít |
| `mention_sources` | extract | resolve (`merge`, `navigation`, `offers`) | 5 | az említéssel együtt mozog |
| `soft_checks` | extract | resolve (`merge`) | 1 | összevonáskor az entitás azonosítója cserélődik |
| `entity_runs` | extract | resolve (`site.py`) | 2 | a site-kör is ebbe a futásnaplóba ír |

Az öt tábla teljes forgalma (saját és idegen együtt): `entities` 31 írás a resolve-ból és 8 az
extractból; `page_entities` 6 + 7; `mention_sources` 5 + 5; `soft_checks` 3 + 1; `entity_runs` 4 + 2.

## Miért nem elég a hívásokat „átirányítani”

A függési irány `extract` ← `resolve`: a resolve hívhatja az extract függvényeit, fordítva nem.

- Az `entities` gazdája ma a resolve. Az extract 8 írását a resolve írófüggvényein át kellene
  vinni, de az extract nem importálhatja a resolve-ot. **A mai gazda így nem tartható.**
- A másik négy táblánál a gazda (extract) jó irányban van: a resolve hívhatja az extract
  írófüggvényeit.

## Három lehetőség

### A) Entitástár az extractban (ajánlott)
Az öt tábla gazdája egységesen az extract, egy külön fájlban (`aaa2/entities/store.py`,
„entitástár”): entitás létrehozása és keresése, alias, szavazat, említés írása és mozgatása,
futásnapló. Az extract és a resolve is ezen át ír és olvas. Az `entities` gazdája resolve →
extract; az `Entity` és a `KbLink` szerződés a resolve kimenete marad (a resolve lekérdező
függvénye adja, a tárból olvasva).

- Előny: a függési irány rendben; a viselkedés nem változik; egy helyen van minden SQL, amely
  ezeket a táblákat éri.
- Hátrány: a resolve 31 írása az `entities`-re is a tár függvényein át megy (ma saját tábla), így
  az átírandó helyek száma nő: kb. 60 írás és a 75 idegen olvasás mellett a resolve saját olvasásai az `entities`-re (45 hely).
- A spechez képest: a spec szerint az `Entity` a resolve szerződése; ez megmarad, de a tábla
  gazdája az extract lesz. Ezt a specben rögzíteni kell.

### B) Az entitás létrehozása a resolve-ba kerül (a spec szó szerinti olvasata)
Az extract csak említést és jelöltet ad (`Mention`, `Candidate`, entitás-azonosító nélkül), az
entitást a resolve rendeli hozzá.

- Előny: tiszta határ, pontosan a spec szerződéslistája.
- Hátrány: sémaváltozás (`page_entities.entity_id` ma kötelező), a szabálykör és az LLM-kör
  átszervezése; ez nem átcímkézés, hanem a pipeline átalakítása. A bájtra azonos kimenet így is cél
  lehet, de a kockázat és a munka többszöröse az A-nak.

### C) Külön közös réteg a crawl és az extract között
Az öt tábla egy új `store` modulé (a függési sorban a `crawl` után), az extract és a resolve is
hívja.

- Előny: egyik modul sem „gazdája” a másik kimenetének.
- Hátrány: új modul a specben; tartalmában ugyanaz, mint az A, csak más címkével.

## Második kérdés: mit adjon vissza egy lekérdező függvény

A crawl, a gráf és az llm tábláinál a függvények szerződést adnak vissza, és a hívó Pythonban
szűr, rendez, kapcsol. Két helyen ettől eltértem, és a közös tábláknál ez sokszor előjön majd:

- **Összesítés** (pl. hívások száma és költsége cél szerint): a gazda modul célfüggvénye adja,
  az eredeti SQL-lel (`llm/calls.py`: `total_cost`, `usage_by_purpose`, `spend_by_model`). Így a
  lebegőpontos összeg ugyanaz marad, mint eddig.
- **A tárolt renderelt DOM** nem része a `Page` szerződésnek (tömörített bájtok), külön függvény
  adja (`engine/queries.py`: `rendered`, `rendered_html`).

A közös tábláknál sok a több táblás, több gazdát érintő lekérdezés (pl. említés ⋈ blokk ⋈
oldal-csomópont). Javaslat: ahol a kapcsolás egy gazdán belül marad, a gazda célfüggvénye adja az
eredeti SQL-lel; ahol két gazda tábláit kapcsolja, a hívó a két gazda szerződéseit kéri le, és
Pythonban kapcsol. Mindkét esetben a rögzített hash-ek döntenek.

## Ami a döntés után következik
1. Az öt tábla gazdájának átírása a `tables.toml`-ban.
2. Az írófüggvények, előbb a 23 idegen írás, utána a gazdán belüli írások.
3. A 75 idegen olvasás.
4. Az architektúra-teszt idegen hozzáférés-száma 0, vagy a maradék listázva, okkal.
