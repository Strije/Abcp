"""Что даёт запасной путь по узлам полного каталога: фразы клиентов × машины парка, офлайн (только кэш на диске).

    python -m app.podbor.unitlab фразы.tsv [фразы2.tsv …] --out отчёт.md [--emb]

Для каждой фразы и каждой машины: быстрые группы Laximo находят деталь или нет («мимо»). Где нет — ищем по узлам полного каталога той
же машины (списки узлов — `python -m app.podbor.fleet units`) и считаем «спасено», если оценка узла не ниже порога движка.
Спасённое надо глазами просмотреть: узел с верным названием — улучшение, узел-однофамилец — мусор («болт» → «Болт крепления»).
С `--emb` (нужен ключ Cloud.ru из окружения EMB_API_KEY) то же делают векторы названий узлов: сколько «мимо» они находят
сверх правил и согласны ли с ними, когда правила нашли. Закупочных цен и данных клиентов здесь нет: только фразы без имён и
названия узлов каталога."""
import argparse
import asyncio
import collections
import json
import re
import sys
from pathlib import Path

from . import emb as EMB
from . import text as T
from .catalog import TreeIndex
from .engine import UNIT_SCORE, Engine, kind_of, share_noun
from .fleet import CACHE, _catalog
from .lab import _MAKES, _has_part, _kind
from .measure import ENGINE, STOP, _fleet_trees
from .sources import Direct, Remote

SAMPLE = 40


def _parts(q: str, part) -> list[str]:
    clean = re.sub(r"\s+", " ", _MAKES.sub(" ", q)).strip()
    parts = [x for x in share_noun(T.split_pieces(clean), STOP) if T.stems(x, STOP)] or [clean]
    parts = [x for x in parts if part(x)] or parts
    stems_of = lambda x: T.stems(T.expand(x), STOP)   # noqa: E731
    return [x for x in parts if kind_of(stems_of(x)) == "part" and not ENGINE.outside(stems_of(x))]


async def _unit_trees(src, ready: list[dict]) -> dict[str, tuple[TreeIndex, list[dict]]]:
    """Машина парка → (дерево узлов, список узлов). Берём только то, что уже лежит на диске: сеть — один findVehicle на машину."""
    cat = _catalog(src)
    out = {}
    for f in ready:
        try:
            vs = await cat.vehicles(f["vin"])
            if not vs:
                continue
            ut = await cat.unit_tree(vs[0])
            if ut is None:
                continue
            units = await cat.units(vs[0])
        except Exception as e:   # noqa: BLE001 — одна машина не должна ронять ночной прогон
            print(f"  {f['make']}: {type(e).__name__} {str(e)[:80]}", file=sys.stderr)
            continue
        out[f"{f['make']} {f['name'][:40]}"] = (ut, units)
        cat._vehicles.clear()
        await asyncio.sleep(0.2)
    return out


def _head_in(unit: str, part: str) -> bool:
    """Узел называет главное слово фразы? Если нет — «спасение» подозрительное и идёт в просмотр."""
    head = T.head(T.stems(part, STOP))
    return bool(head) and any(T.same(head, s) for s in T.stems(unit, STOP))


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("phrases", nargs="+")
    ap.add_argument("--out", default="unitlab.md")
    ap.add_argument("--emb", action="store_true")
    ap.add_argument("--remote", default="")
    a = ap.parse_args(argv)
    ready = json.loads((CACHE / "fleet_ready.json").read_text(encoding="utf-8"))
    quick = _fleet_trees()
    if a.remote:
        src = Remote(a.remote)
    else:
        from ..config import load
        from ..laximo import Laximo, Usage
        lx = Laximo(load())
        lx.usage = Usage(None)
        src = Direct(lx, None, "")
    try:
        units = await _unit_trees(src, ready)
    finally:
        await src.close() if a.remote else await lx.close()
    cars = [c for c in quick if c in units]
    print(f"Машин с быстрыми группами и списком узлов: {len(cars)} из {len(quick)}")
    rows = []
    for p in a.phrases:
        rows += [x.split("\t")[0] for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = list(dict.fromkeys(rows))
    part = _has_part()
    report, miss_total, saved_total, checked = [], 0, 0, 0
    emb_jobs = []   # (фраза, часть, машина) для векторов
    for q in rows:
        parts = _parts(q, part)
        if not parts:
            continue
        miss = saved = 0
        names = collections.Counter()
        suspicious = 0
        for car in cars:
            tree, (ut, _) = quick[car], units[car]
            for x in parts:
                st = T.stems(x, STOP)
                kind, _, _ = _kind(tree.rank(st, T.side(x)), st, tree)
                checked += 1
                if kind != "мимо":
                    continue
                miss += 1
                got = ut.rank(st, T.side(x))
                if got and got[0][1] >= UNIT_SCORE:
                    saved += 1
                    names[got[0][0].name] += 1
                    suspicious += not _head_in(got[0][0].name, x)
                elif a.emb:
                    emb_jobs.append((q, x, car))
        if miss:
            report.append({"query": q, "miss": miss, "saved": saved, "suspicious": suspicious, "units": names.most_common(3)})
            miss_total += miss
            saved_total += saved
    lines = ["# Запасной путь по узлам полного каталога на парке", "",
             f"Машин: {len(cars)}, фраз: {len(rows)}, проверок «фраза × машина × часть»: {checked}.", "",
             f"Быстрые группы не нашли деталь («мимо»): {miss_total}. Узлы нашли из них: {saved_total} "
             f"({round(100 * saved_total / max(miss_total, 1))}%). Порог — оценка узла ≥ {UNIT_SCORE}.", ""]
    rescued = sorted((r for r in report if r["saved"]), key=lambda r: -r["saved"])
    lines += [f"## Спасённые фразы ({len(rescued)}) — просмотреть глазами; «?» — в названии узла нет главного слова фразы", ""]
    for r in rescued[:120]:
        lines.append(f"- {'?' if r['suspicious'] * 2 > r['saved'] else ' '} **{r['query'][:70]}** — спасено {r['saved']} из {r['miss']}: "
                     + "; ".join(f"«{n}» ×{c}" for n, c in r["units"]))
    lost = sorted((r for r in report if not r["saved"]), key=lambda r: -r["miss"])
    lines += ["", f"## Не нашлось и там ({len(lost)}), по числу машин", ""]
    lines += [f"- {r['query'][:80]} — мимо на {r['miss']} проверках" for r in lost[:80]]
    (Path(a.out)).write_text("\n".join(lines) + "\n", encoding="utf-8")
    Path(a.out).with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"отчёт: {a.out} · мимо {miss_total}, спасено узлами {saved_total}")
    if a.emb:
        await _emb(a, units, emb_jobs)
    return 0


async def _docs(e, texts: list[str], np):
    """Векторы названий узлов — как документы, без инструкции запроса (так считались и названия групп)."""
    import base64
    out = []
    for i in range(0, len(texts), 32):
        r = await e.http.post(f"{EMB.URL}/embeddings", json={"model": EMB.MODEL, "encoding_format": "base64",
                                                             "input": texts[i:i + 32]}, timeout=90)
        if r.status_code != 200:
            return None
        for x in sorted(r.json()["data"], key=lambda x: x["index"]):
            v = np.frombuffer(base64.b64decode(x["embedding"]), dtype=np.float32)
            out.append(v / (np.linalg.norm(v) or 1.0))
    return out


async def _emb(a, units, jobs) -> None:
    """Векторы названий узлов: на «мимо», где молчат и группы, и узлы по словам, — что бы они нашли. Просмотр глазами."""
    e = EMB.get()
    if e is None:
        print("эмбеддинги: ключа или файла векторов групп нет, пропуск")
        return
    np = e.np
    names = sorted({u["name"] for _, us in units.values() for u in us})
    print(f"эмбеддинги: уникальных названий узлов {len(names)}")
    cache_file = Path(a.out).with_name("unit_names.npz")
    have: dict[str, object] = {}
    if cache_file.exists():
        z = np.load(cache_file, allow_pickle=False)
        have = dict(zip(json.loads(str(z["names"])), z["vecs"]))
    todo = [n for n in names if n not in have]
    got = await _docs(e, todo, np) if todo else []
    if got is None:
        print("эмбеддинги: сервис не ответил, пропуск")
        return
    have |= dict(zip(todo, got))
    np.savez(cache_file, names=json.dumps(list(have), ensure_ascii=False), vecs=np.stack(list(have.values())))
    out = ["", "## Эмбеддинги названий узлов — где молчат и группы, и узлы по словам (до 3 машин на фразу, просмотреть глазами)", ""]
    seen, parts = collections.Counter(), {}
    for q, part, car in jobs:
        if seen[q] >= 3:
            continue
        seen[q] += 1
        parts.setdefault(part, None)
    pv = await e.embed(list(parts)) if parts else []
    if not pv:
        print("эмбеддинги: фразы не посчитались")
        return
    pvec = dict(zip(parts, pv))
    seen.clear()
    shown = 0
    for q, part, car in jobs:
        if seen[q] >= 3 or shown >= 200:
            continue
        seen[q] += 1
        own = list(dict.fromkeys(u["name"] for u in units[car][1]))
        sims = np.stack([have[n] for n in own]) @ pvec[part]
        j = int(np.argmax(sims))
        out.append(f"- {part[:60]} ({car[:26]}) → «{own[j]}» {float(sims[j]):.2f}")
        shown += 1
    Path(a.out).write_text(Path(a.out).read_text(encoding="utf-8") + chr(10).join(out) + chr(10), encoding="utf-8")
    print(f"эмбеддинги: примеров {shown}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
