"""Эталон лаборатории: 550 фраз клиентов с верными группами (разметка 08.10, проверена пользователем) — что о каждой скажут
правила того кода, из папки которого запущен скрипт. Нужен rules_check.sh: «верных не меньше, ошибок не больше».

    cd <рабочая копия>/server && python <путь>/gold_eval.py run ПАПКА_ЭТАЛОНА отчёт.json
    python gold_eval.py diff до.json после.json

ПАПКА_ЭТАЛОНА — label_sample.jsonl (id, part) и labels.tsv (id → группы через | или код НЕ/УТОЧ/МЕН/НЕТ).
Ответ правил по фразе — группа, которую правила уверенно (оценка ≥ 0.8, без равного соперника) выбирают на большинстве
машин парка; иначе правила «молчат» (спросят клиента или передадут менеджеру).
  верно  — уверенная группа из эталона; или код в эталоне, а правила молчат;
  ошибка — уверенная группа не из эталона (в том числе группа там, где эталон говорит «не деталь»);
  молчит — у фразы есть верная группа, а правила её не выбрали.
Без Laximo, цен и модели: деревья машин парка из .podbor-cache.
"""
import collections
import json
import sys
from pathlib import Path

CODES = {"НЕ", "УТОЧ", "МЕН", "НЕТ"}
SURE = 0.8
CARS = 40   # первые машины парка по имени: 40 деревьев хватает, все 214 — в пять раз дольше


def run(ref: str, out: str) -> None:
    sys.path.insert(0, str(Path.cwd()))   # код под проверкой — из текущей папки (main или ветка)
    from app.podbor import text as T
    from app.podbor.engine import Engine, kind_of
    from app.podbor.measure import ENGINE, STOP, _fleet_trees

    folder = Path(ref)
    rows = [json.loads(x) for x in (folder / "label_sample.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    gold = {}
    for line in (folder / "labels.tsv").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            i, lab = line.split("\t")
            gold[int(i)] = {x.strip() for x in lab.split("|")}
    trees = dict(sorted(_fleet_trees().items())[:CARS])
    report = []
    for r in rows:
        q, want = ENGINE.clean(r["part"]), gold.get(r["id"])
        if want is None:
            continue
        st = T.stems(T.expand(q), STOP)
        answer = ""
        if st and not ENGINE.outside(st) and kind_of(st) == "part" and not Engine._vague(st, q):
            votes = collections.Counter()
            for tree in trees.values():
                rank = tree.rank(T.stems(q, STOP), T.side(q))
                if not rank or rank[0][1] < SURE:
                    continue
                if len(rank) > 1 and rank[1][1] >= rank[0][1] - 0.05 and rank[1][0].name.lower() != rank[0][0].name.lower():
                    continue   # два равных кандидата — бот спросит, а не выберет
                votes[rank[0][0].name] += 1
            if votes and votes.most_common(1)[0][1] * 2 > len(trees):
                answer = votes.most_common(1)[0][0]
        low = {w.lower() for w in want}
        if want & CODES:
            verdict = "ошибка" if answer else "верно"
        elif not answer:
            verdict = "молчит"
        else:
            verdict = "верно" if answer.lower() in low else "ошибка"
        report.append({"id": r["id"], "phrase": r["part"], "gold": sorted(want), "answer": answer, "verdict": verdict})
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    c = collections.Counter(x["verdict"] for x in report)
    print(f"Эталон: {len(report)} фраз — верно {c['верно']}, ошибка {c['ошибка']}, молчит {c['молчит']}")


def diff(before: str, after: str) -> int:
    """Сравнение списков, а не только итога: новая ошибка — разбор, даже если итог вырос. 1 — если стало хуже."""
    a = {x["id"]: x for x in json.loads(Path(before).read_text(encoding="utf-8"))}
    b = {x["id"]: x for x in json.loads(Path(after).read_text(encoding="utf-8"))}
    ca = collections.Counter(x["verdict"] for x in a.values())
    cb = collections.Counter(x["verdict"] for x in b.values())
    print(f"Эталон, main → ветка: верно {ca['верно']} → {cb['верно']}, ошибка {ca['ошибка']} → {cb['ошибка']}, "
          f"молчит {ca['молчит']} → {cb['молчит']}")
    rank = {"верно": 0, "молчит": 1, "ошибка": 2}
    worse = [(a[i], b[i]) for i in a.keys() & b.keys() if rank[b[i]["verdict"]] > rank[a[i]["verdict"]]
             or (a[i]["verdict"] == b[i]["verdict"] == "ошибка" and a[i]["answer"] != b[i]["answer"])]
    better = [(a[i], b[i]) for i in a.keys() & b.keys() if rank[b[i]["verdict"]] < rank[a[i]["verdict"]]]
    for title, rows in (("Хуже (разобрать до выкладки)", worse), ("Лучше", better)):
        if rows:
            print(f"\n== {title}: {len(rows)}")
            for x, y in sorted(rows, key=lambda p: p[0]["id"])[:40]:
                print(f"  #{x['id']} «{x['phrase'][:45]}» {x['verdict']} «{x['answer'] or '—'}» → {y['verdict']} "
                      f"«{y['answer'] or '—'}» | эталон {' | '.join(x['gold'])[:50]}")
    return 1 if worse or cb["ошибка"] > ca["ошибка"] or cb["верно"] < ca["верно"] else 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "run" and len(sys.argv) == 4:
        run(sys.argv[2], sys.argv[3])
    elif cmd == "diff" and len(sys.argv) == 4:
        sys.exit(diff(sys.argv[2], sys.argv[3]))
    else:
        sys.exit(__doc__)
