"""Egységteszt mesterséges oldalakon: a blokkos prompt v2 egy modellel, oldalanként egy hívás,
pontozás a referencialista ellen a nyers kimenetből.

    python -m tests.acceptance.synthetic_eval run [--model gpt-6-luna] [--tag r2]
    python -m tests.acceptance.synthetic_eval report [--model gpt-6-luna] [--tag r2]

A tesztoldalak: `tests/acceptance/synthetic/*.json` (blokkok, site-leírás, referencialista:
kötelező, opcionális, negatív). A bemenet a JSON blokkjaiból jön (`blocks.block_input`). A nyers
kimenet `data/compare/synthetic/<oldal>.<modell>[.<címke>].json` (a `--tag` a kört
különbözteti meg); a hívások a
`data/compare/synthetic.duckdb` `llm_calls`-ába és a főkönyvbe kerülnek. A pontozás nem ír
entitástárba, és nem használja az összevonási szabályokat.

Pontozás (`score_page`), oldalanként:

- ellenőrzés: a `surface_form` szerepel-e a megadott blokkban; ami nem, az kitalált, és kimarad;
- a modell entitásai a kanonikus név kulcsa (`alias_key`: kis-nagybetű, ékezet, kötőjel)
  szerint csoportosítva; találat, ha a kanonikus név vagy egy szöveg szerinti alak kulcsa egyezik
  egy referencia-tétel kanonikus nevének vagy egy aliasának kulcsával;
- recall a kötelező listán, összesítve és nehézség szerint;
- precizitás: a kötelező tételhez illeszkedő csoport jó, a negatív tételhez illeszkedő és a
  referenciában nem szereplő hiba, az opcionális semleges;
- típus- és altípuspontosság a talált kötelező tételeken (altípus csak ahol a referenciában van);
- forráshely: a talált kötelező tételek említéseiből hány áll a referencia szerinti blokkban;
- `primary_entities`: a referencia elsődleges tételeiből hányat adott vissza a modell;
- költség, token be / ki.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from aaa2.db.connect import DATA_DIR, connect
from aaa2.entities.blocks import block_input, block_prompt, check_surface
from aaa2.entities.rules import alias_key
from aaa2.llm.client import LLMError, SchemaMismatch, open_clients
from aaa2.llm.config import load_config
from aaa2.llm.schemas import BlockEntity, BlockExtraction

PAGES_DIR = Path(__file__).parent / "synthetic"
OUT_DIR = Path(__file__).parent / "out" / "synthetic"
DEFAULT_MODEL = "gpt-6-luna"


def load_pages(pages_dir: Path = PAGES_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(pages_dir.glob("*.json"))]


# ---------------------------------------------------------------------------
# futás
# ---------------------------------------------------------------------------


def output_path(data_dir: Path, page_id: str, model: str, tag: str = "") -> Path:
    return data_dir / "synthetic" / f"{page_id}.{model}{'.' + tag if tag else ''}.json"


def run(model: str, data_dir: Path, pages: list[dict], tag: str = "",
        with_kind: bool = False) -> None:
    """`with_kind`: a bemenet a blokktípust is megadja az azonosító mellett (3b)."""
    provider = load_config().provider_of(model)
    if provider is None:
        raise SystemExit(f"a {model} nincs a konfigurált modellek között")
    (data_dir / "synthetic").mkdir(parents=True, exist_ok=True)
    con = connect(data_dir / "synthetic.duckdb")
    try:
        clients, skipped = open_clients(con, models={provider: model})
        if provider not in clients:
            raise SystemExit(f"{model}: {skipped.get(provider)}")
        for page in pages:
            record = call(clients[provider], page, with_kind)
            output_path(data_dir, page["page_id"], model, tag).write_text(
                json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{page['page_id']}: {model}: "
                  + (record["error"] or f"{len(record['entities'])} említés, "
                     f"{len(record['primary_entities'])} elsődleges"))
    finally:
        con.close()


def call(client, page: dict, with_kind: bool = False) -> dict:
    record = {"page_id": page["page_id"], "model": client.model, "with_kind": with_kind,
              "call_id": None, "primary_entities": None, "entities": None, "error": None}
    text = block_input(page["site_description"], page["blocks"], with_kind)
    try:
        result = client.extract(BlockExtraction, block_prompt(with_kind), text,
                                domain="entity")
    except SchemaMismatch as exc:
        record.update(call_id=exc.call_id, error=f"schema_mismatch: {exc}"[:500])
        return record
    except LLMError as exc:
        record.update(error=f"call_error: {exc}"[:500])
        return record
    record.update(call_id=result.call_id, primary_entities=result.parsed.primary_entities,
                  entities=[entity.model_dump() for entity in result.parsed.entities])
    return record


# ---------------------------------------------------------------------------
# pontozás
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    canonical: str
    type: str | None
    subtype: str | None
    difficulty: str | None
    keys: frozenset[str]
    blocks: frozenset[str]
    types: frozenset[str] = frozenset()      # a helyes típusok: `acceptable_types`, vagy a típus
    subtypes: frozenset[str] = frozenset()   # a helyes altípusok: `acceptable_subtypes`, vagy az
                                             # altípus


def _items(entries: list[dict]) -> list[Item]:
    return [Item(e["canonical"], e.get("type"), e.get("subtype"), e.get("difficulty"),
                 frozenset(alias_key(n) for n in [e["canonical"], *e.get("aliases", [])]),
                 frozenset(s["block"] for s in e.get("surface_forms", [])),
                 frozenset(e.get("acceptable_types") or [e.get("type")]),
                 frozenset(alias_key(s) for s in e.get("acceptable_subtypes")
                           or ([e["subtype"]] if e.get("subtype") else [])))
            for e in entries]


@dataclass
class Group:
    """A modell egy entitása: az azonos kanonikus kulcsú (nem kitalált) említések."""

    canonical: str
    type: str
    subtype: str | None
    surfaces: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)

    def keys(self) -> set[str]:
        return {alias_key(self.canonical), *(alias_key(s) for s in self.surfaces)}


@dataclass
class PageScore:
    page_id: str
    required: int = 0
    found: int = 0
    by_difficulty: dict = field(default_factory=dict)       # nehézség → [talált, összes]
    named: int = 0                                          # nem concept típusú kötelező
    named_found: int = 0
    concepts: int = 0                                       # concept típusú kötelező
    concepts_found: int = 0
    good: int = 0
    wrong: int = 0
    neutral: int = 0
    type_right: int = 0
    subtype_right: int = 0
    subtype_total: int = 0
    block_right: int = 0
    block_total: int = 0
    primary_found: int = 0
    primary_total: int = 0
    mentions: int = 0
    missed: list = field(default_factory=list)              # (kanonikus, nehézség, típus)
    wrong_types: list = field(default_factory=list)         # (kanonikus, referencia, modell)
    wrong_subtypes: list = field(default_factory=list)
    wrong_blocks: list = field(default_factory=list)        # (kanonikus, blokk)
    negatives_hit: list = field(default_factory=list)       # (modell neve, negatív tétel)
    unlisted: list = field(default_factory=list)            # (modell neve, típus)
    fabricated: list = field(default_factory=list)          # (kanonikus, szöveg, blokk)
    extra_primary: list = field(default_factory=list)
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def precision_total(self) -> int:
        return self.good + self.wrong


def score_page(page: dict, record: dict, call_row: tuple | None = None) -> PageScore:
    """`call_row`: (cost_usd, tokens_in, tokens_out) az `llm_calls`-ból, ha van."""
    gold = page["gold"]
    required, optional = _items(gold["entities"]), _items(gold.get("optional", []))
    negatives = {alias_key(n["text"]): n["text"] for n in gold.get("negatives", [])}
    blocks = {b["id"]: b for b in page["blocks"]}
    score = PageScore(page["page_id"], required=len(required))
    if call_row:
        score.cost_usd, score.tokens_in, score.tokens_out = (call_row[0] or 0.0,
                                                             call_row[1] or 0, call_row[2] or 0)
    groups: dict[str, Group] = {}
    for raw in record.get("entities") or []:
        entity = BlockEntity.model_construct(**raw)          # validálás nélkül: a régi kör is
        score.mentions += 1
        if not check_surface(entity, blocks):
            score.fabricated.append((entity.canonical_name, entity.surface_form, entity.block_id))
            continue
        key = alias_key(entity.canonical_name)
        group = groups.setdefault(key, Group(entity.canonical_name, entity.type, entity.subtype))
        group.surfaces.append(entity.surface_form)
        group.blocks.append(entity.block_id)
    hits: dict[Item, Group] = {}
    for group in groups.values():
        keys = group.keys()
        target = next((item for item in required if item.keys & keys), None)
        if target is not None:
            score.good += 1
            hits.setdefault(target, group)
            score.block_total += len(group.blocks)
            right = [b for b in group.blocks if b in target.blocks]
            score.block_right += len(right)
            score.wrong_blocks += [(target.canonical, b) for b in group.blocks
                                   if b not in target.blocks]
        elif any(item.keys & keys for item in optional):
            score.neutral += 1
        elif negative := next((negatives[k] for k in keys if k in negatives), None):
            score.wrong += 1
            score.negatives_hit.append((group.canonical, negative))
        else:
            score.wrong += 1
            score.unlisted.append((group.canonical, group.type))
    for item in required:
        tally = score.by_difficulty.setdefault(item.difficulty or "?", [0, 0])
        tally[1] += 1
        concept = item.type == "concept"
        score.concepts += concept
        score.named += not concept
        group = hits.get(item)
        if group is None:
            score.missed.append((item.canonical, item.difficulty, item.type))
            continue
        score.found += 1
        score.concepts_found += concept
        score.named_found += not concept
        tally[0] += 1
        if group.type in item.types:
            score.type_right += 1
        else:
            score.wrong_types.append((item.canonical, item.type, group.type))
        if item.subtype:
            score.subtype_total += 1
            if alias_key(group.subtype or "") in item.subtypes:
                score.subtype_right += 1
            else:
                score.wrong_subtypes.append((item.canonical, item.subtype, group.subtype))
    model_primary = {alias_key(p) for p in record.get("primary_entities") or []}
    for name in gold.get("primary_entities", []):
        item = next((i for i in required if alias_key(name) in i.keys), None)
        keys = item.keys if item else {alias_key(name)}
        score.primary_total += 1
        score.primary_found += bool(keys & model_primary)
    gold_primary_keys = set()
    for name in gold.get("primary_entities", []):
        item = next((i for i in required if alias_key(name) in i.keys), None)
        gold_primary_keys |= item.keys if item else {alias_key(name)}
    score.extra_primary = [p for p in record.get("primary_entities") or []
                           if alias_key(p) not in gold_primary_keys]
    return score


# ---------------------------------------------------------------------------
# jelentés
# ---------------------------------------------------------------------------


def _pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:.1f}% ({part}/{whole})" if whole else "—"


SUMMED = ("required", "found", "named", "named_found", "concepts", "concepts_found", "good",
          "wrong", "type_right", "subtype_right", "subtype_total", "block_right", "block_total",
          "primary_found", "primary_total", "tokens_in", "tokens_out")
HEADER = ("| {first} | recall | megnevezett | fogalom | easy | hard | precizitás | típus | "
          "altípus | forrásblokk | primary | kitalált | hiba (neg. / kívüli) | USD | token be / ki |")
RULE = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"


def total_of(scores: list[PageScore], label: str = "összesen") -> PageScore:
    total = PageScore(label)
    for s in scores:
        for name in SUMMED:
            setattr(total, name, getattr(total, name) + getattr(s, name))
        total.cost_usd += s.cost_usd
        total.fabricated += s.fabricated
        total.negatives_hit += s.negatives_hit
        total.unlisted += s.unlisted
        for level, (found, all_) in s.by_difficulty.items():
            tally = total.by_difficulty.setdefault(level, [0, 0])
            tally[0] += found
            tally[1] += all_
    return total


def table_row(label: str, s: PageScore) -> str:
    easy, hard = s.by_difficulty.get("easy", [0, 0]), s.by_difficulty.get("hard", [0, 0])
    return (f"| {label} | {_pct(s.found, s.required)} | {_pct(s.named_found, s.named)} | "
            f"{_pct(s.concepts_found, s.concepts)} | {_pct(*easy)} | {_pct(*hard)} | "
            f"{_pct(s.good, s.precision_total)} | {_pct(s.type_right, s.found)} | "
            f"{_pct(s.subtype_right, s.subtype_total)} | {_pct(s.block_right, s.block_total)} | "
            f"{s.primary_found}/{s.primary_total} | {len(s.fabricated)} | "
            f"{len(s.negatives_hit)} / {len(s.unlisted)} | {s.cost_usd:.5f} | "
            f"{s.tokens_in} / {s.tokens_out} |")


def comparison_markdown(rows: list[tuple[str, list[PageScore]]]) -> str:
    """Több kör vagy változat összesítve, egymás alatt; alatta oldalanként is."""
    lines = [HEADER.format(first="kör"), RULE]
    lines += [table_row(f"**{label}**", total_of(scores)) for label, scores in rows]
    for page_id in [s.page_id for s in rows[0][1]]:
        lines += ["", f"### {page_id}", "", HEADER.format(first="kör"), RULE]
        lines += [table_row(label, s) for label, scores in rows for s in scores
                  if s.page_id == page_id]
    return "\n".join(lines) + "\n"


def report_markdown(model: str, scores: list[PageScore]) -> str:
    lines = [f"# Mesterséges oldalak: `{model}`, blokkos prompt, egész oldal", "",
             ("Pontozás a nyers kimenetből. Találat: a kanonikus név vagy a szöveg szerinti alak "
              "kulcsa (kis-nagybetű, ékezet, kötőjel nélkül) egyezik a referencia kanonikus "
              "nevével vagy egy aliasával. Megnevezett: a nem concept típusú kötelező tétel; "
              "fogalom: a concept típusú. Precizitás: negatív és referencián kívüli találat hiba, "
              "opcionális semleges."), "",
             HEADER.format(first="oldal"), RULE]
    lines += [table_row(s.page_id, s) for s in scores]
    if len(scores) > 1:
        lines.append(table_row("**összesen**", total_of(scores)))
    for s in scores:
        lines += ["", f"## {s.page_id}", "", f"Említés a kimenetben: {s.mentions}.", ""]
        lines.append(f"- **Kihagyott kötelező ({len(s.missed)}):** " + (", ".join(
            f"{name} ({kind}, {level})" for name, level, kind in s.missed) or "—"))
        lines.append(f"- **Hibás típus ({len(s.wrong_types)}):** " + (", ".join(
            f"{name}: {gold} → {got}" for name, gold, got in s.wrong_types) or "—"))
        lines.append(f"- **Hibás altípus ({len(s.wrong_subtypes)}):** " + (", ".join(
            f"{name}: {gold} → {got}" for name, gold, got in s.wrong_subtypes) or "—"))
        lines.append(f"- **Téves találat, negatív ({len(s.negatives_hit)}):** " + (", ".join(
            f"{name} (= „{neg}”)" for name, neg in s.negatives_hit) or "—"))
        lines.append(f"- **Téves találat, a referenciában nincs ({len(s.unlisted)}):** " + (
            ", ".join(f"{name} ({kind})" for name, kind in s.unlisted) or "—"))
        lines.append(f"- **Kitalált (a szöveg nincs a megadott blokkban) ({len(s.fabricated)}):** "
                     + (", ".join(f"{name}: „{text}” [{block}]"
                                  for name, text, block in s.fabricated) or "—"))
        lines.append(f"- **Rossz blokk ({len(s.wrong_blocks)}):** " + (", ".join(
            f"{name} [{block}]" for name, block in s.wrong_blocks) or "—"))
        lines.append("- **Elsődleges a referencián kívül:** "
                     + (", ".join(s.extra_primary) or "—"))
    return "\n".join(lines) + "\n"


def measure(model: str, data_dir: Path, pages: list[dict], tag: str = ""
            ) -> list[PageScore]:
    scores = []
    db = data_dir / "synthetic.duckdb"
    con = duckdb.connect(str(db), read_only=True) if db.exists() else None
    try:
        for page in pages:
            path = output_path(data_dir, page["page_id"], model, tag)
            if not path.exists():
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            row = con.execute("SELECT cost_usd, tokens_in, tokens_out FROM llm_calls "
                              "WHERE call_id = ?", [record["call_id"]]).fetchone() \
                if con is not None and record.get("call_id") else None
            scores.append(score_page(page, record, row))
    finally:
        if con is not None:
            con.close()
    return scores


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["run", "report"])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--tag", default="", help="a kör címkéje (r1, r2, …)")
    parser.add_argument("--variant", choices=["a", "b"], default="a",
                        help="b: a blokktípus is a bemenetben ([b12 code])")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    pages = load_pages()
    if args.command == "run":
        run(args.model, args.data_dir, pages, args.tag, with_kind=args.variant == "b")
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"{args.model}{'-' + args.tag if args.tag else ''}-report.md"
    out.write_text(report_markdown(args.model, measure(args.model, args.data_dir, pages,
                                                       args.tag)),
                   encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
