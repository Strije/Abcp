"""Стенд «фразы × машины»: банк фраз (настоящие из переписки + синтетика модели) прогоняется правилами робота по деревьям
групп ВСЕХ машин парка — без сети, параллельно на всех ядрах. Карта провалов: какая фраза на каких марках уходит не туда.

    cd server
    python lab/bench.py bank                        # собрать банк: .podbor-cache/bench/bank.jsonl
    python lab/bench.py run  ИМЯ [--cars N]         # прогон кода из ТЕКУЩЕЙ папки → .podbor-cache/bench/run_ИМЯ.json
    python lab/bench.py diff ИМЯ_ДО ИМЯ_ПОСЛЕ        # что стало лучше/хуже по фразам

Источники банка (все — в .podbor-cache, в git не попадают): lab_big_clean.tsv, lab_phrases_clean.tsv, night_phrases.tsv
(переписка 2026), etalon/ (550 размеченных фраз с верными группами), bench/synthetic.jsonl (модель по словарю деталей:
{"text", "part", "family"}). Уровень 1 — разбор фразы и выбор группы; детали, цены и модель — выборкой на сервере.

Что считается по фразе на каждой машине (как lab.run): уверенно / слабо / выбор / мимо / потеряно слово / не каталог /
уточняем; плюс «верно»: у эталона — группа из разметки, у синтетики — главное слово детали в названии группы или её синонимах.
"""
import collections
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
CACHE = Path(".podbor-cache")
OUT = CACHE / "bench"
SURE = 0.8


def bank() -> None:
    rows, seen = [], set()

    def add(text, source, **extra):
        t = re.sub(r"\s+", " ", str(text or "")).strip()
        if 3 <= len(t) <= 160 and t.lower() not in seen:
            seen.add(t.lower())
            rows.append({"id": len(rows) + 1, "text": t, "source": source, **extra})

    for name, src in (("lab_big_clean.tsv", "переписка"), ("lab_phrases_clean.tsv", "переписка"),
                      ("night_phrases.tsv", "переписка")):
        p = CACHE / name
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    add(line.split("\t")[0], src)
    ref = CACHE / "etalon"
    if (ref / "labels.tsv").exists():
        gold = {}
        for line in (ref / "labels.tsv").read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                i, lab = line.split("\t")
                gold[int(i)] = [x.strip() for x in lab.split("|")]
        for line in (ref / "label_sample.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r["id"] in gold:
                add(r["part"], "эталон", gold=gold[r["id"]])
    syn = OUT / "synthetic.jsonl"
    if syn.exists():
        for line in syn.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            add(r["text"], "синтетика", part=r.get("part", ""), family=r.get("family", ""))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "bank.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print("банк:", len(rows), dict(collections.Counter(r["source"] for r in rows)))


# --- прогон: каждый процесс загружает деревья парка один раз ---
_W: dict = {}


def _init(cars: int):
    from app.podbor import text as T
    from app.podbor.engine import Engine, kind_of, share_noun
    from app.podbor.lab import _kind, _MAKES, _has_part
    from app.podbor.measure import ENGINE, STOP, _fleet_trees
    trees = dict(sorted(_fleet_trees().items()))
    if cars:
        # Поровну по маркам (первое слово «марка модель»), а не первые по алфавиту: иначе выборка — одни Audi и BMW
        by_make: dict[str, list] = collections.defaultdict(list)
        for k, t in trees.items():
            by_make[k.split()[0].lower()].append((k, t))
        picked: list = []
        while len(picked) < cars and any(by_make.values()):
            for m in sorted(by_make):
                if by_make[m] and len(picked) < cars:
                    picked.append(by_make[m].pop(0))
        trees = dict(picked)
    _W.update(T=T, Engine=Engine, kind_of=kind_of, share_noun=share_noun, _kind=_kind, MAKES=_MAKES, part=_has_part(),
              ENGINE=ENGINE, STOP=STOP, trees=trees)


def _one(r: dict) -> dict:
    T, STOP, ENGINE = _W["T"], _W["STOP"], _W["ENGINE"]
    clean = re.sub(r"\s+", " ", _W["MAKES"].sub(" ", r["text"])).strip()
    parts = [x for x in _W["share_noun"](T.split_pieces(clean), STOP) if T.stems(x, STOP)] or [clean]
    parts = [x for x in parts if _W["part"](x)] or parts
    stems_of = lambda x: T.stems(T.expand(x), STOP)  # noqa: E731
    out = {"id": r["id"], "kinds": {}, "groups": {}, "ok": None}
    if all(ENGINE.outside(stems_of(x)) or _W["kind_of"](stems_of(x)) != "part" for x in parts):
        out["cat"] = "не каталог"
        return out
    if all(_W["Engine"]._vague(stems_of(x), x) for x in parts if _W["kind_of"](stems_of(x)) == "part"):
        out["cat"] = "уточняем"
        return out
    kinds, groups = collections.Counter(), collections.Counter()
    want_head = T.head(T.stems(T.expand(r.get("part", "")), STOP)) if r.get("part") else ""
    gold = {g.lower() for g in r.get("gold") or []}
    right = wrong = 0
    for tree in _W["trees"].values():
        worst = ("уверенно", "", 1.0)
        top_group = None
        for x in parts:
            st = T.stems(x, STOP)
            if ENGINE.outside(st):
                continue
            rank = tree.rank(st, T.side(x))
            got = _W["_kind"](rank, st, tree)
            if {"уверенно": 0, "слабо": 1, "выбор": 2, "мимо": 3, "потеряно слово": 4}.get(got[0], 0) >= \
                    {"уверенно": 0, "слабо": 1, "выбор": 2, "мимо": 3, "потеряно слово": 4}.get(worst[0], 0):
                worst = got
            if rank and rank[0][1] >= SURE and top_group is None:
                top_group = rank[0][0]
        kinds[worst[0]] += 1
        if worst[1]:
            groups[worst[1].split(" | ")[0]] += 1
        if top_group is not None and (want_head or gold):
            names = [top_group.name] + [" ".join(ph) for ph, _ in getattr(top_group, "phrases", [])]
            hit = (top_group.name.lower() in gold) if gold else \
                any(T.same(want_head, s) for n in names for s in T.stems(T.expand(n), STOP))
            right += hit
            wrong += not hit
    n = max(sum(kinds.values()), 1)
    if kinds["потеряно слово"]:
        cat = "потеряно слово"
    elif kinds["мимо"] >= 0.9 * n:
        cat = "везде мимо"
    elif kinds["мимо"] >= 0.3 * n:
        cat = "у части машин нет"
    elif len(groups) >= 3 and groups.most_common(1)[0][1] / max(n - kinds["мимо"], 1) < 0.6:
        cat = "несогласованно"
    elif kinds["выбор"] >= 0.6 * n:
        cat = "выбор везде"
    elif kinds["слабо"] >= 0.5 * n:
        cat = "слабо"
    else:
        cat = "уверенно"
    out.update(cat=cat, kinds=dict(kinds), groups=dict(groups.most_common(4)),
               ok=(None if not (want_head or gold) else {"верно": right, "не туда": wrong}))
    return out


def run(name: str, cars: int = 0) -> None:
    rows = [json.loads(x) for x in (OUT / "bank.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    workers = max(1, (os.cpu_count() or 2) - 1)
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(cars,)) as ex:
        res = list(ex.map(_one, rows, chunksize=20))
    by = {r["id"]: r for r in rows}
    for x in res:
        x["text"], x["source"] = by[x["id"]]["text"], by[x["id"]]["source"]
    (OUT / f"run_{name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"прогон «{name}»: фраз {len(res)}, процессов {workers}")
    for src in sorted({x["source"] for x in res}):
        part = [x for x in res if x["source"] == src]
        c = collections.Counter(x["cat"] for x in part)
        ok = [x["ok"] for x in part if x.get("ok")]
        acc = ""
        if ok:
            r_, w_ = sum(o["верно"] for o in ok), sum(o["не туда"] for o in ok)
            acc = f" | уверенная группа верна {100 * r_ // max(r_ + w_, 1)}% ({r_} из {r_ + w_} фраза×машина)"
        print(f"  {src:<10} {len(part):>5}: " + ", ".join(f"{k} {v}" for k, v in c.most_common()) + acc)


def diff(a: str, b: str) -> None:
    ra = {x["id"]: x for x in json.loads((OUT / f"run_{a}.json").read_text(encoding="utf-8"))}
    rb = {x["id"]: x for x in json.loads((OUT / f"run_{b}.json").read_text(encoding="utf-8"))}
    score = {"уверенно": 0, "уточняем": 0, "не каталог": 0, "слабо": 1, "выбор везде": 1, "у части машин нет": 2,
             "везде мимо": 2, "несогласованно": 3, "потеряно слово": 4}
    worse = [i for i in ra.keys() & rb.keys() if score.get(rb[i]["cat"], 2) > score.get(ra[i]["cat"], 2)]
    better = [i for i in ra.keys() & rb.keys() if score.get(rb[i]["cat"], 2) < score.get(ra[i]["cat"], 2)]
    wrong = lambda x: (x.get("ok") or {}).get("не туда", 0)  # noqa: E731
    more_wrong = [i for i in ra.keys() & rb.keys() if wrong(rb[i]) > wrong(ra[i])]
    print(f"{a} → {b}: лучше {len(better)}, хуже {len(worse)}, «не туда» выросло у {len(more_wrong)} фраз")
    for title, ids in (("ХУЖЕ", worse), ("«НЕ ТУДА» ВЫРОСЛО", more_wrong), ("ЛУЧШЕ", better)):
        if ids:
            print(f"\n== {title}: {len(ids)}")
            for i in ids[:30]:
                print(f"  [{ra[i]['source']}] {ra[i]['text'][:60]:<60} {ra[i]['cat']} → {rb[i]['cat']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "bank":
        bank()
    elif cmd == "run" and len(sys.argv) >= 3:
        run(sys.argv[2], int(sys.argv[sys.argv.index("--cars") + 1]) if "--cars" in sys.argv else 0)
    elif cmd == "diff" and len(sys.argv) == 4:
        diff(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
