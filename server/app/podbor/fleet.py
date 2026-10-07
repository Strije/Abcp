"""Парк машин для проверок без Laximo: деревья групп конкретных машин (у каждой своё) и детали групп.

    python -m app.podbor.fleet pick vinqu.jsonl [3] [2]    # выбрать ~45 машин из заявок (запасных 3, ×2 — ~90 машин)
    python -m app.podbor.fleet trees                        # машина и дерево групп для каждой (2 запроса на машину)
    python -m app.podbor.fleet details запросы.txt [1] [N]  # детали лучшей группы каждого запроса у первых N машин

Всё сохраняется в .podbor-cache навсегда (Catalog), повторно Laximo не спрашиваем. Запросы идут через
гостевой прокси рабочего сервера — их видно в счётчике /v1/podbor/laximo-usage. VIN клиентов — только
в .podbor-cache, в git не кладём.
"""
import asyncio
import collections
import json
import re
import sys
import time
from pathlib import Path

from . import text as T
from .catalog import Catalog, LaximoError
from .engine import load_rules
from .sources import Remote

CACHE = Path(".podbor-cache")
FLEET = CACHE / "fleet.json"

# Семейства каталогов и сколько машин брать: по заявкам ABCP это ~85% всего потока
FAMILIES = {
    "VAG": ({"VOLKSWAGEN": 2, "SKODA": 1, "AUDI": 1}),
    "Hyundai/Kia": ({"HYUNDAI": 2, "KIA": 2}),
    "GM": ({"OPEL": 2, "CHEVROLET": 2, "DAEWOO": 1}),
    "Ford": ({"FORD": 4}),
    "Nissan/Renault": ({"NISSAN": 3, "RENAULT": 2}),
    "Toyota/Lexus": ({"TOYOTA": 3, "LEXUS": 1}),
    "Mazda": ({"MAZDA": 3}),
    "Mercedes": ({"MERCEDES-BENZ": 3}),
    "Mitsubishi": ({"MITSUBISHI": 3}),
    "PSA": ({"PEUGEOT": 2, "CITROEN": 1}),
    "BMW": ({"BMW": 3}),
    "Honda": ({"HONDA": 3}),
}
DIESEL = re.compile(r"\b(?:\d\.\d\s*)?(?:d|td|tdi|crdi|cdi|dci|hdi|tdci|sdi|d-4d|dcti|jtd|cdti)\b|дизел", re.I)


def pick(path: str, spare: int = 3, times: int = 1):
    """По каждой марке — разные модели, по возможности и бензин, и дизель; запасные — на случай,
    если VIN не найдётся в Laximo. times — во сколько раз больше машин каждой марки (ночные прогоны парка)."""
    spare, times = int(spare), int(times)
    rows = [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    by_make: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        vin = (r.get("vin") or "").strip().upper()
        if re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
            by_make[(r.get("car") or "?").split()[0].upper()].append(r)
    fleet = []
    for fam, makes in FAMILIES.items():
        for make, n in makes.items():
            n *= times
            seen_models, chosen = set(), []
            cands = by_make.get(make, [])
            # Один дизель на марку (наборы групп бензина и дизеля отличаются), остальные — бензин, как в парке
            diesel = [r for r in cands if DIESEL.search(r.get("car", ""))]
            petrol = [r for r in cands if not DIESEL.search(r.get("car", ""))]
            cands = diesel[:1] + petrol + diesel[1:]
            for r in cands:
                model = " ".join((r.get("car") or "").split()[:3])
                if model in seen_models or any(r["vin"] == c["vin"] for c in chosen):
                    continue
                seen_models.add(model)
                chosen.append(r)
                if len(chosen) >= n + spare:
                    break
            for i, r in enumerate(chosen):
                fleet.append({"family": fam, "make": make, "vin": r["vin"].upper(), "car": r.get("car", ""),
                              "spare": i >= n})
    FLEET.parent.mkdir(exist_ok=True)
    FLEET.write_text(json.dumps(fleet, ensure_ascii=False, indent=1), encoding="utf-8")
    main = [f for f in fleet if not f["spare"]]
    print(f"Машин: {len(main)} (+{len(fleet) - len(main)} запасных) → {FLEET}")
    for f in main:
        print(f"  {f['family']:<15} {f['vin']}  {f['car'][:60]}")


def _catalog(src) -> Catalog:
    rules = load_rules()
    stop = frozenset(rules["stop"]) | frozenset(T.stem(w) for w in rules["stop"])
    return Catalog(src.laximo, CACHE, stop, rules.get("synonyms", []))


async def trees():
    """Машина и её дерево групп. Не нашлась — берём запасную той же марки, пока не наберём нужное число."""
    fleet = json.loads(FLEET.read_text(encoding="utf-8"))
    src = Remote("https://109.73.199.217")
    cat = _catalog(src)
    need = collections.Counter(f["make"] for f in fleet if not f["spare"])
    got = collections.Counter()
    ok = []
    try:
        for f in fleet:
            if got[f["make"]] >= need[f["make"]]:
                continue
            try:
                vs = await cat.vehicles(f["vin"])
                if not vs:
                    print(f"  нет в Laximo: {f['vin']} {f['car'][:50]}")
                    continue
                v = vs[0]
                tree = await cat.tree(v)
            except LaximoError as e:
                print(f"  {f['vin']} {f['car'][:40]}: {e.code}")
                continue
            except Exception as e:   # сеть, лимит прокси — пробуем следующую
                print(f"  {f['vin']}: {type(e).__name__} {e}")
                await asyncio.sleep(5)
                continue
            got[f["make"]] += 1
            ok.append(dict(f, catalog=v.catalog, tree=Catalog.tree_key(v), name=v.summary()[:70],
                           groups=len(tree.groups)))
            print(f"  ✓ {f['family']:<15} {v.catalog:<16} групп {len(tree.groups):4}  {v.summary()[:60]}", flush=True)
            await asyncio.sleep(2)   # гостевой прокси сервера: не больше 60 запросов в минуту
    finally:
        await src.close()
    (CACHE / "fleet_ready.json").write_text(json.dumps(ok, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Готово машин: {len(ok)}; не набрали: "
          f"{', '.join(f'{m} {need[m] - got[m]}' for m in need if got[m] < need[m]) or 'всех набрали'}")


async def details(queries: str, per_query: str = "1", limit: str = "0"):
    """Детали групп, в которые ведут запросы, для каждой готовой машины — один запрос на группу,
    уже сохранённые не спрашиваем."""
    from .measure import RULES, STOP, ENGINE
    ready = json.loads((CACHE / "fleet_ready.json").read_text(encoding="utf-8"))
    if int(limit):
        ready = ready[:int(limit)]   # пачками: первые N машин
    qs = [q.strip() for q in Path(queries).read_text(encoding="utf-8").splitlines() if q.strip()]
    src = Remote("https://109.73.199.217")
    cat = _catalog(src)
    asked = 0
    try:
        for f in ready:
            v = (await cat.vehicles(f["vin"]))[0]
            tree = await cat.tree(v)
            gids = []
            for q in qs:
                st = T.stems(q, STOP)
                if ENGINE.outside(st):
                    continue
                r = tree.rank(st, T.side(q))
                gids += [g.id for g, s in r[:int(per_query)] if s >= 0.3]
            gids = list(dict.fromkeys(gids))
            before = sum(1 for _ in (CACHE / "details").rglob("*.gz")) if (CACHE / "details").exists() else 0
            for gid in gids:
                t0 = time.time()
                try:
                    await cat.details(v, gid, False)
                except Exception as e:
                    print(f"    {v.catalog} группа {gid}: {type(e).__name__} {e}")
                    if "TOO_MANY" in str(e) or "ACCESS" in str(e):
                        print("Laximo ограничил запросы — останавливаемся")
                        return
                if time.time() - t0 > 0.2:   # ходили в Laximo, а не на диск: прокси пускает 60 в минуту
                    await asyncio.sleep(1.1)
            after = sum(1 for _ in (CACHE / "details").rglob("*.gz"))
            asked += after - before
            print(f"  {f['family']:<15} {v.catalog:<16} групп для запросов {len(gids):3}, скачано новых {after - before}",
                  flush=True)
    finally:
        await src.close()
    print(f"Новых групп скачано: {asked}")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "pick":
        pick(*args)
    elif cmd == "trees":
        asyncio.run(trees())
    elif cmd == "details":
        asyncio.run(details(*args))
