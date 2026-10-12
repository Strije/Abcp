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
import os
import re
import sys
import time
from pathlib import Path

from . import text as T
from .catalog import _safe, Catalog, LaximoError
from .engine import load_rules
from .sources import Direct, Remote

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


async def units():
    """Списки узлов полного каталога для готовых машин парка (listCategories → listUnits, 8–40 запросов на машину, на диск
    навсегда). Нужны лаборатории и ночным замерам: поиск по узлам вне быстрых групп (подрамник, поперечина…) считается по ним.
    Уже сохранённые не спрашиваем: повторный запуск продолжает с места остановки."""
    ready = json.loads((CACHE / "fleet_ready.json").read_text(encoding="utf-8"))
    direct = os.environ.get("FLEET_DIRECT") == "1"   # на сервере: напрямую в Laximo, без гостевого прокси (60 запросов в минуту)
    if direct:
        from ..config import load
        from ..laximo import Laximo, Usage
        lx = Laximo(load())
        lx.usage = Usage(None)   # счётчик боевого сервиса не трогаем: файл принадлежит службе
        src = Direct(lx, None, "")
    else:
        src = Remote("https://109.73.199.217")
    cat = _catalog(src)
    if direct:
        cat.units_pause = 0.5   # ≈120 запросов в минуту из 600 общих: боевой робот и приложение тоже ходят в Laximo
    done = fresh = total = 0
    try:
        for f in ready:
            try:
                vs = await cat.vehicles(f["vin"])
                if not vs:
                    continue
                v = vs[0]
                had = cat._load_any(f"units/{_safe(v.catalog)}/{cat._car(v)}.json.gz") is not None
                us = await cat.units(v)
            except Exception as e:   # сеть, лимит прокси — пробуем следующую
                print(f"  {f['vin'][:3]}…{f['vin'][-4:]}: {type(e).__name__} {e}")
                await asyncio.sleep(5)
                continue
            if not us:
                print(f"  ✗ {f['family']:<15} узлов нет: {v.summary()[:50]}")
                continue
            done += 1
            total += len(us)
            fresh += 0 if had else 1
            print(f"  {'·' if had else '✓'} {f['family']:<15} {v.catalog:<16} узлов {len(us):4}  {v.summary()[:50]}", flush=True)
            cat._vehicles.clear()   # память: сервер маленький, а машин больше двухсот
            if not had and not direct:
                await asyncio.sleep(2)   # гостевой прокси сервера: не больше 60 запросов в минуту
    finally:
        if direct:
            await lx.close()
        else:
            await src.close()
    print(f"Машин со списком узлов: {done} (новых {fresh}); узлов всего {total}")


async def parts(per: str = "1"):
    """Словарь деталей по семействам каталогов: для 1–2 машин каждого каталога (с самым длинным списком узлов) — детали всех
    узлов (listDetailByUnit, по запросу на узел, навсегда на диск). Итог — parts_dict/<каталог>.json: название детали →
    в каких узлах встречается и сколько раз. Нужен, чтобы модель и правила знали, где у этого семейства бывают шланги,
    датчики, прокладки, и как их называют (по-русски и по-английски: «ТРУБКА ВАКУУМНОГО НАСОСА», «HOSE,BRAKE BOOSTER VACUUM»).
    Повторный запуск продолжает: скачанное лежит в unit_details/."""
    ready = json.loads((CACHE / "fleet_ready.json").read_text(encoding="utf-8"))
    from ..config import load
    from ..laximo import Laximo, Usage
    lx = Laximo(load())
    lx.usage = Usage(None)
    cat = _catalog(Direct(lx, None, ""))
    out_dir = CACHE / "parts_dict"
    out_dir.mkdir(exist_ok=True)
    fams: dict[str, list[dict]] = collections.defaultdict(list)
    for f in ready:
        fams[f.get("catalog") or f["make"]].append(f)
    asked = 0
    try:
        for family, cars in sorted(fams.items()):
            sized = []
            for f in cars:   # самые подробные машины семейства: у них больше узлов
                try:
                    vs = await cat.vehicles(f["vin"])
                    if vs:
                        sized.append((len(await cat.units(vs[0])), f, vs[0]))
                except Exception as e:   # noqa: BLE001
                    print(f"  {family}: {type(e).__name__} {str(e)[:60]}", flush=True)
            sized.sort(key=lambda x: -x[0])
            names: dict[str, dict] = {}
            for n, f, v in sized[:int(per)]:
                await cat.unit_tree(v)
                ids = cat._unit_ids.get(cat.tree_key(v) + "-u") or {}
                for gid, u in ids.items():
                    on_disk = (CACHE / cat.unit_file(v, u)).exists()
                    for attempt in range(4):
                        try:
                            ds = await cat._unit_details(v, gid)
                            break
                        except LaximoError as e:
                            if "TOO_MANY" not in str(e) or attempt == 3:
                                ds = []
                                break
                            await asyncio.sleep(20 * (attempt + 1))
                        except Exception:   # noqa: BLE001 — сеть: узел пропускаем, повторный запуск докачает
                            ds = []
                            break
                    if not on_disk:   # Laximo отвечает за 30 мс: без паузы вышло бы 500 запросов в минуту из общих 600
                        asked += 1
                        await asyncio.sleep(0.5)   # общий лимит Laximo 600 в минуту: оставляем место боевому роботу
                    for d in ds:
                        key = re.sub(r"\s+", " ", d.name.strip().upper())
                        if not key:
                            continue
                        e = names.setdefault(key, {"name": d.name.strip(), "n": 0, "units": collections.Counter(), "oem": d.oem})
                        e["n"] += 1
                        e["units"][u["name"]] += 1
                    cat._details.clear()   # память: сервер маленький
                print(f"  {family:16} {v.summary()[:44]:44} узлов {n:4}, названий деталей {len(names):6}, запросов {asked}",
                      flush=True)
            rows = sorted(({"name": e["name"], "n": e["n"], "oem": e["oem"], "units": e["units"].most_common(6)}
                           for e in names.values()), key=lambda r: -r["n"])
            (out_dir / f"{_safe(family)}.json").write_text(json.dumps({"family": family, "parts": rows}, ensure_ascii=False),
                                                           encoding="utf-8")
            cat._vehicles.clear()
    finally:
        await lx.close()
    print(f"Готово: семейств {len(fams)}, запросов к Laximo {asked}")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "units":
        asyncio.run(units())
    elif cmd == "parts":
        asyncio.run(parts(*args))
    elif cmd == "pick":
        pick(*args)
    elif cmd == "trees":
        asyncio.run(trees())
    elif cmd == "details":
        asyncio.run(details(*args))
