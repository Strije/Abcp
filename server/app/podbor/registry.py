"""Реестр синонимов: всё, что мы знаем о словах клиентов, в одном месте — чтобы не искать по десяти файлам.

    python -m app.podbor.registry build [--cache .podbor-cache] [--out .podbor-cache/registry]

Собирает из всех источников в `synonym_registry.json` (для кода и сверки) и `synonyms.md` (для людей: «группа → все слова»):
  laximo   — синонимы групп из быстрых деревьев Laximo («воздухан»);
  rules    — наши синонимы в podbor_rules.json (слова клиентов и прайсов → номера групп);
  learned  — названия деталей, которые робот запомнил (learned.json);
  typos    — опечатки (podbor_typos.json);
  abbrev   — сокращения и жаргон в text._SHORT («с/б» → сайлентблок);
  brands   — как клиенты называют фирмы по-русски («бош» → BOSCH);
  proposed — кандидаты из переписки и прайсов (mined_pairs.json), ждут решения человека.
Реестр лежит в кэше (в git не попадает): синонимы групп — данные Laximo, кандидаты — слова клиентов. Решения человека
хранятся в репозитории: принятое — в podbor_rules.json (synonyms), отклонённое — в podbor_synonyms_rejected.json.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
RULES = DATA / "podbor_rules.json"
TYPOS = DATA / "podbor_typos.json"
REJECTED = DATA / "podbor_synonyms_rejected.json"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def group_names(cache: Path) -> tuple[dict[int, str], list[dict]]:
    """(номер группы → название, синонимы групп Laximo) по всем деревьям кэша. Номера групп внутри каталога
    одинаковы у разных машин, названия берём самые частые."""
    names: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    syn: dict[tuple[str, str], set] = collections.defaultdict(set)

    def walk(node, catalog):
        if node.get("link"):
            gid, name = node.get("quickGroupId"), (node.get("name") or "").strip()
            if gid is not None and name:
                names[int(gid)][name] += 1
                for s in str(node.get("synonyms") or "").split(","):
                    if s.strip():
                        syn[(s.strip().lower(), name)].add(catalog)
        for ch in node.get("children") or []:
            walk(ch, catalog)

    seen = set()
    for tf in sorted(glob.glob(str(cache / "trees" / "*.json"))):
        catalog = os.path.basename(tf).split("-")[0].replace(".json", "")
        if catalog in seen:
            continue
        seen.add(catalog)
        tree = _read(Path(tf), {}).get("tree")
        if tree:
            walk(tree, catalog)
    ids = {gid: c.most_common(1)[0][0] for gid, c in names.items()}
    out = [{"phrase": p, "target": t, "catalogs": sorted(cats)} for (p, t), cats in sorted(syn.items())]
    return ids, out


def build(cache: Path) -> dict:
    ids, laximo = group_names(cache)
    entries: list[dict] = []

    def add(kind, source, phrase, target, status="active", **extra):
        entries.append({"kind": kind, "source": source, "phrase": phrase, "target": target, "status": status, **extra})

    for e in laximo:
        add("group_synonym", "laximo", e["phrase"], e["target"], catalogs=e["catalogs"])
    rules = _read(RULES, {})
    for r in rules.get("synonyms", []):
        targets = [ids.get(int(g), f"группа {g}") for g in r.get("groups", [])]
        for w in r.get("words", []):
            add("group_synonym", "rules", w.lower(), targets[0] if targets else "", group_ids=r.get("groups", []),
                strict=bool(r.get("strict")), note=r.get("why", "")[:160], also=targets[1:])
    for gid, names in _read(cache / "learned.json", {}).items():
        for n in names:
            add("learned_name", "learned", n.lower(), ids.get(int(gid), f"группа {gid}"), group_ids=[int(gid)])
    for wrong, right in (_read(TYPOS, {}).get("words") or {}).items():
        add("typo", "typos", wrong, right)
    try:
        from . import text as T
        for rx, repl in T._SHORT:
            add("abbrev", "abbrev", rx.pattern[:80], repl if isinstance(repl, str) else "(по правилу в коде)")
    except Exception:  # noqa: BLE001 — реестр строится и без кода разбора
        pass
    try:
        from . import dialog as D
        for w, b in sorted(D.BRAND_RU.items()):
            add("brand_word", "brands", w, b)
    except Exception:  # noqa: BLE001
        pass
    rejected = {(r["phrase"], r["target"]) for r in _read(REJECTED, [])}
    for p in _read(cache / "mined_pairs.json", []):
        key = (p["phrase"], p["group"])
        add("group_synonym", "proposed", p["phrase"], p["group"], status="rejected" if key in rejected else "proposed",
            count=p.get("n", 1), example=p.get("chunk", ""), manager=p.get("item", ""), now=p.get("cur", ""))
    return {"version": 1, "built": datetime.date.today().isoformat(), "groups": len(ids),
            "counts": dict(collections.Counter(f"{e['source']}:{e['status']}" for e in entries)), "entries": entries}


def render(reg: dict) -> str:
    """Читаемый указатель: по группам — все слова из всех источников; отдельно кандидаты на проверку."""
    by_group: dict[str, list] = collections.defaultdict(list)
    for e in reg["entries"]:
        if e["kind"] in ("group_synonym", "learned_name") and e["status"] == "active" and e["target"]:
            by_group[e["target"]].append(e)
    out = ["# Реестр синонимов", "",
           f"Собран {reg['built']}. " + ", ".join(f"{k} — {v}" for k, v in sorted(reg["counts"].items())), "",
           "Источники: `laximo` — синонимы групп из деревьев Laximo; `rules` — наши (podbor_rules.json); `learned` — запомнил робот.",
           "", "## Группа → все слова", ""]
    for g in sorted(by_group, key=str.lower):
        words = collections.OrderedDict()
        for e in by_group[g]:
            words.setdefault(e["phrase"], set()).add(e["source"])
        out.append(f"- **{g}**: " + "; ".join(f"{w} ({'/'.join(sorted(s))})" for w, s in words.items()))
    prop = sorted((e for e in reg["entries"] if e["status"] == "proposed"), key=lambda e: -e.get("count", 1))
    out += ["", f"## Кандидаты на проверку ({len(prop)})", "",
            "Принять — добавить в `synonyms` файла podbor_rules.json; отклонить — в podbor_synonyms_rejected.json.", ""]
    for i, e in enumerate(prop[:200], 1):
        out.append(f"{i}. ×{e.get('count', 1)} «{e.get('example') or e['phrase']}» → **{e['target']}** | менеджер: "
                   f"«{e.get('manager', '')}» | правила сейчас: {e.get('now') or '—'}")
    typos = [e for e in reg["entries"] if e["kind"] == "typo"]
    brands = [e for e in reg["entries"] if e["kind"] == "brand_word"]
    abbr = [e for e in reg["entries"] if e["kind"] == "abbrev"]
    out += ["", f"## Опечатки ({len(typos)})", ", ".join(f"{e['phrase']}→{e['target']}" for e in typos),
            "", f"## Фирмы по-русски ({len(brands)})", ", ".join(f"{e['phrase']}→{e['target']}" for e in brands),
            "", f"## Сокращения и жаргон в коде ({len(abbr)})", ""]
    out += [f"- `{e['phrase']}` → {e['target']}" for e in abbr]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Реестр синонимов")
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--cache", default=".podbor-cache")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    cache = Path(a.cache)
    out = Path(a.out) if a.out else cache / "registry"
    out.mkdir(parents=True, exist_ok=True)
    reg = build(cache)
    (out / "synonym_registry.json").write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    (out / "synonyms.md").write_text(render(reg), encoding="utf-8")
    print("реестр:", reg["counts"], "| групп:", reg["groups"], "| файлы:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
