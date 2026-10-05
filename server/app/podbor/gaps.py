"""Где слова покупателей расходятся с группами Laximo: прогон заявок через разбор без запросов в каталог.

    python -m app.podbor.gaps vinqu.jsonl [дерево.json] > отчёт.md

Заявка → позиции (как в подборе) → лучшая группа дерева Laximo. Позиции делим на «уверенно»,
«спорно» и «мимо»; из «спорно» и «мимо» собираем слова, которых в дереве нет, с примерами —
это кандидаты в словарь синонимов. Дерево групп одно на все каталоги, берём любое из кэша.
"""
import collections
import json
import re
import sys
from pathlib import Path

from . import text as T
from .catalog import TreeIndex
from .engine import load_rules

CACHE = Path(__file__).resolve().parents[2] / ".podbor-cache"


def load_tree(path: str | None) -> dict:
    p = Path(path) if path else next((CACHE / "trees").glob("*.json"))
    return json.loads(p.read_text(encoding="utf-8"))["tree"]


def positions(r: dict) -> list[str]:
    """Позиции заявки: из карточки (после «Нужны:») или из строки списка."""
    claim = r.get("claim") or ""
    body = claim.split("Нужны:", 1)[1] if "Нужны:" in claim else (r.get("query") or "").replace(" | ", "\n")
    out = []
    for chunk in T.split_chunks(body):
        out += T.split_pieces(chunk) if "," in chunk else [chunk]
    return [p for p in out if p.strip()]


def main(argv: list[str]) -> None:
    rows = [json.loads(line) for line in Path(argv[0]).read_text(encoding="utf-8").splitlines() if line.strip()]
    rules = load_rules()
    stop = frozenset({w for w in rules["stop"]} | {T.stem(w) for w in rules["stop"]})
    ix = TreeIndex(load_tree(argv[1] if len(argv) > 1 else None), stop)

    buckets = collections.Counter()
    groups = collections.Counter()
    missing = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    shaky: list[tuple[float, str, str]] = []
    vins = sum(1 for r in rows if T.find_ident((r.get("vin") or "") + " " + (r.get("query") or "")))
    total = 0
    for r in rows:
        for p in positions(r):
            q = T.stems(p, stop)
            if not q:
                continue
            total += 1
            ranked = ix.rank(q, T.side(p))
            s = ranked[0][1] if ranked else 0
            b = "уверенно" if s >= 0.9 else "спорно" if s >= 0.6 else "мимо"
            buckets[b] += 1
            if ranked and s >= 0.6:
                groups[ranked[0][0].name] += 1
            if b != "уверенно":
                if ranked and b == "спорно":
                    shaky.append((s, p, ranked[0][0].name))
                for st in q:
                    if not any(T.same(st, v) for v in ix.vocab):
                        missing[st] += 1
                        if len(examples[st]) < 3:
                            examples[st].append(p[:70])

    print(f"# Заявки против групп Laximo\n\nЗаявок: {len(rows)}, с VIN или номером кузова: {vins}. Позиций: {total}.\n")
    for b in ("уверенно", "спорно", "мимо"):
        print(f"- {b}: {buckets[b]} ({buckets[b] * 100 // max(total, 1)}%)")
    print("\n## Чаще всего просят (группы Laximo)\n")
    for g, c in groups.most_common(30):
        print(f"- {c} — {g}")
    print("\n## Слов нет в дереве Laximo — кандидаты в словарь\n")
    for st, c in missing.most_common(80):
        print(f"- **{st}** ×{c}: " + " · ".join(f"«{e}»" for e in examples[st]))
    print("\n## Спорные сопоставления (проверить глазами)\n")
    for s, p, g in sorted(shaky, key=lambda x: x[0])[:60]:
        print(f"- {s:.2f} «{p[:60]}» → {g}")


if __name__ == "__main__":
    main(sys.argv[1:])
