"""Замеры понимания запросов — без обращений к Laximo, по объединению сохранённых деревьев групп.

    python -m app.podbor.measure requests vinqu.jsonl            # заявки ABCP: одна группа / выбор / мимо
    python -m app.podbor.measure chats chats.jsonl [отчёт.json]   # переписка Битрикс24: как пишут клиенты
    python -m app.podbor.measure typos chats.jsonl                # какие «опечатки» правятся чаще всего
    python -m app.podbor.measure snapshot vinqu.jsonl до.json      # разбор каждой позиции — для сравнения
    python -m app.podbor.measure diff до.json после.json           # что поменялось после правки

«Одна группа» — запрос однозначно ведёт в одну группу каталога (не значит «правильно», значит «без вопросов»),
«выбор» — несколько групп почти с одной оценкой (бот спросит), «мимо» — лучшая оценка ниже 0,5,
«не каталог» — химия, инструмент, шины. Переписку выгружает bitrix_export.py на сервере.
"""
import collections
import json
import re
import sys
from pathlib import Path

from . import text as T
from .catalog import TreeIndex
from .engine import Engine, load_rules, share_noun
from .gaps import positions
from .mine import union_tree

RULES = load_rules()
STOP = frozenset({w for w in RULES["stop"]} | {T.stem(w) for w in RULES["stop"]})
INDEX = TreeIndex(union_tree(), STOP, None, RULES["synonyms"],
                  frozenset(T.stem(w) for w in RULES.get("not_typos", {}).get("words", [])))


class _NoSource:
    laximo = None


ENGINE = Engine(_NoSource(), None, set(), RULES)
_vocab: dict[str, bool] = {}


def in_vocab(s: str) -> bool:
    if s not in _vocab:
        _vocab[s] = INDEX._in_vocab(s)
    return _vocab[s]


def classify(piece: str) -> tuple[str, list]:
    q = T.stems(piece, STOP)
    if ENGINE.outside(q):
        return "не каталог", []
    r = INDEX.rank(q, T.side(piece))
    if not r or r[0][1] < 0.5:
        return "мимо", r
    top = r[0][1]
    names = {g.name for g, s in r if s >= top - 0.05}
    return ("одна группа" if len(names) == 1 else "выбор из нескольких"), r


def _report(counts: collections.Counter, total: int) -> str:
    return ", ".join(f"{k} {v * 100 / max(total, 1):.1f}%" for k, v in counts.most_common())


def requests(path: str):
    asks = [p for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()
            for p in positions(json.loads(line))]
    asks = [p for p in asks if T.stems(p, STOP)]
    c = collections.Counter(classify(p)[0] for p in asks)
    print(f"Позиций: {len(asks)} — {_report(c, len(asks))}")


def snapshot(path: str, out: str):
    asks = sorted({p for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()
                   for p in positions(json.loads(line)) if T.stems(p, STOP)})
    data = {}
    for p in asks:
        k, r = classify(p)
        data[p] = [k, r[0][0].name if r else ""]
    Path(out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def diff(a: str, b: str):
    A = json.loads(Path(a).read_text(encoding="utf-8"))
    B = json.loads(Path(b).read_text(encoding="utf-8"))
    for p in A:
        if A[p] != B.get(p):
            print(f"«{p[:60]}»: {A[p][0][:5]} {A[p][1]}  →  {B.get(p, ['—', ''])[0][:5]} {B.get(p, ['', ''])[1]}")


# ---------- переписка ----------

JUNK = re.compile(r"^(отправлено|открыть|системное|call miss|автодруг бот|\(изменено\))", re.I)


def chat_pieces(path: str) -> tuple[list[str], dict]:
    """Фразы клиентов про детали: первые 4 сообщения клиента, без шаблонов (текст в 15+ диалогах — автоматика)."""
    from .chats import load
    rows = load(path)
    freq = collections.Counter()
    for r in rows:
        for t in {m["text"].strip().lower()[:80] for m in r["messages"] if m["role"] == "client"}:
            freq[t] += 1
    tmpl = {t for t, c in freq.items() if c >= 15}
    pieces, with_part, memo = [], 0, {}
    for r in rows:
        got = False
        for m in [m for m in r["messages"] if m["role"] == "client"][:4]:
            t = m["text"]
            if t.strip().lower()[:80] in tmpl or JUNK.match(t.strip()):
                continue
            for ch in T.parse(t).chunks:
                for p in share_noun(T.split_pieces(ch) if "," in ch else [ch], STOP):
                    p = ENGINE.clean(p)
                    if not 3 <= len(p) <= 120:
                        continue
                    if p not in memo:
                        memo[p] = bool(INDEX.known(T.stems(p, STOP)))
                    if memo[p]:
                        pieces.append(p)
                        got = True
        with_part += got
    return pieces, {"dialogs": len(rows), "templates": len(tmpl), "with_part": with_part}


def chats(path: str, out: str | None = None):
    pieces, info = chat_pieces(path)
    uniq = collections.Counter(pieces)
    print(f"Диалогов: {info['dialogs']}, шаблонов: {info['templates']}, с деталью в начале: {info['with_part']}, "
          f"фраз про детали: {len(pieces)} (разных {len(uniq)})", flush=True)
    counts = collections.Counter()
    per = collections.defaultdict(list)
    for i, (p, c) in enumerate(uniq.items()):
        if i and i % 5000 == 0:
            print(f"  разобрано {i}/{len(uniq)}", flush=True)
        k, r = classify(p)
        counts[k] += c
        per[k].append((p, r))
    print(_report(counts, len(pieces)))
    if not out:
        return
    # Главные слова не из каталога и куда тянут соседние слова — кандидаты в словарь
    heads, ex, votes = collections.Counter(), collections.defaultdict(list), collections.defaultdict(collections.Counter)
    for p, c in uniq.items():
        st = [s for s in T.stems(p, STOP) if not s.endswith(".")]
        h = T.head(st) if st else None
        if not h or len(h) < 4 or in_vocab(INDEX.fix(h)):
            continue
        heads[h] += c
        if len(ex[h]) < 3:
            ex[h].append(p[:70])
        rest = [s for s in st if s != h]
        rr = INDEX.rank(rest, T.Side()) if rest else []
        if rr and rr[0][1] >= 0.6:
            votes[h][(rr[0][0].id, rr[0][0].name)] += 1
    report = {"counts": dict(counts), "info": info,
              "heads": [(h, c, votes[h].most_common(2), ex[h]) for h, c in heads.most_common(300)],
              "miss": [p for p, _ in per["мимо"]][:3000],
              "choose": [(p, [(g.name, round(s, 2)) for g, s in r[:3]]) for p, r in per["выбор из нескольких"]][:3000]}
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=0, default=str), encoding="utf-8")
    print(f"Отчёт: {out}")


def typos(path: str):
    """Какие слова переписки «исправляются» как опечатки: частые — почти наверняка обычные слова (в not_typos)."""
    from .chats import load
    words = collections.Counter(s for r in load(path) for m in r["messages"] if m["role"] == "client"
                                for s in T.stems(m["text"], STOP))
    fixes = collections.Counter({(s, INDEX.fix(s)): c for s, c in words.items()
                                 if len(s) >= 5 and not s.endswith(".") and INDEX.fix(s) != s})
    for (s, f), c in fixes.most_common(80):
        print(f"{c:6} {s} → {f}")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    {"requests": requests, "chats": chats, "typos": typos, "snapshot": snapshot, "diff": diff}[cmd](*args)
