"""Черновик словаря: слова из прайсов и заявок, которых нет в дереве групп Laximo, и к какой группе их отнести.

    python -m app.podbor.mine catalog.parquet vinqu.jsonl > черновик.md

«Голова» названия — первое значимое слово («Сайлентблок переднего рычага» → «сайлентблок»).
Если головы нет в дереве, смотрим, куда ведут остальные слова тех же названий («переднего рычага» →
«Рычаг передний нижний»), и голосуем. Номера групп Laximo одинаковы во всех каталогах, поэтому
эталон — объединение всех деревьев из кэша.
"""
import collections
import glob
import json
import random
import sys
from pathlib import Path

from . import text as T
from .catalog import TreeIndex
from .engine import load_rules
from .gaps import CACHE, positions

PER_HEAD = 150   # сколько названий на голову разбирать для голосования


def union_tree() -> list[dict]:
    return [json.loads(Path(p).read_text(encoding="utf-8"))["tree"] for p in sorted(glob.glob(str(CACHE / "trees" / "*.json")))]


def near(a: str, b: str) -> bool:
    """Одна опечатка: замена, пропуск или лишняя буква («масленн» ~ «маслян», «шруз» ~ «шрус»)."""
    if abs(len(a) - len(b)) > 1 or min(len(a), len(b)) < 4 or a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, l = (a, b) if len(a) < len(b) else (b, a)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


def main(argv: list[str]) -> None:
    import pandas as pd

    rules = load_rules()
    stop = frozenset({w for w in rules["stop"]} | {T.stem(w) for w in rules["stop"]})
    ix = TreeIndex(union_tree(), stop)
    vocab = ix.vocab

    names = pd.read_parquet(argv[0], columns=["name"])["name"].drop_duplicates()
    names = names.sample(min(len(names), 400_000), random_state=7).tolist()
    asks = [p for line in Path(argv[1]).read_text(encoding="utf-8").splitlines() if line.strip()
            for p in positions(json.loads(line))]

    known = {}

    def is_known(s: str) -> bool:
        if s not in known:
            known[s] = any(T.same(s, v) for v in vocab)
        return known[s]

    from .. import brands as AB
    full = AB.get()
    service = {"вывед", "ассортимент", "стар", "замен", "нов", "оригина", "аналог", "уценк", "распродаж"}

    def head(st: list[str]) -> str | None:
        """Первое значимое слово: без бренда запчасти в начале («PILENGA Амортизатор») и служебных пометок."""
        for s in st:
            if s.endswith(".") or any(s.startswith(x) for x in service):
                continue
            if full.loaded and AB.norm(s) in full.alias and not is_known(s):
                continue   # бренд, а не деталь
            return s
        return None

    by_head: dict[str, list[tuple[str, list[str]]]] = collections.defaultdict(list)
    cnt = collections.Counter()
    ask_cnt = collections.Counter()
    for src, items in (("прайс", names), ("заявка", asks)):
        for n in items:
            st = T.stems(n, stop)
            h = head(st)
            if not h or is_known(h):
                continue
            (cnt if src == "прайс" else ask_cnt)[h] += 1
            if len(by_head[h]) < PER_HEAD:
                by_head[h].append((n, [s for s in st if s != h]))

    heads = sorted(set(cnt) | set(ask_cnt), key=lambda h: -(cnt[h] + 20 * ask_cnt[h]))[:150]
    print("# Черновик словаря: слова, которых нет в дереве Laximo\n")
    print(f"Эталон — {len(ix.groups)} групп из {len(union_tree())} каталогов. Прайс: {len(names):,} названий, "
          f"заявок-позиций: {len(asks)}. Порядок — по частоте, слово из заявки весит как 20 из прайса.\n")
    print("| слово | прайс | заявки | опечатка от | куда тянут соседние слова | доля | примеры |")
    print("|---|---|---|---|---|---|---|")
    for h in heads:
        votes = collections.Counter()
        for _, rest in by_head[h]:
            r = ix.rank(rest, T.Side()) if rest else []
            if r and r[0][1] >= 0.6:
                votes[(r[0][0].id, r[0][0].name)] += 1
        top = votes.most_common(1)
        typo = next((v for v in vocab if near(h, v)), "")
        share = f"{top[0][1] * 100 // max(len(by_head[h]), 1)}%" if top else ""
        group = f"{top[0][0][1]} [{top[0][0][0]}]" if top else "—"
        ex = " · ".join(f"«{n[:45]}»" for n, _ in random.Random(h).sample(by_head[h], min(2, len(by_head[h]))))
        print(f"| **{h}** | {cnt[h]} | {ask_cnt[h]} | {typo} | {group} | {share} | {ex} |")


if __name__ == "__main__":
    main(sys.argv[1:])
