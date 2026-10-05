"""Подбор по заявке: VIN → машина (Laximo) → группа по словам клиента → деталь и её сторона → предложения ABCP.

Номера берутся только из Laximo и ABCP, сами мы их не придумываем. Сторону детали проверяем
в двух независимых местах: что пишет каталог (название и примечание узла, PR-коды VAG)
и что пишут поставщики в описаниях этого номера. Не сошлось или не хватает данных — вопрос клиенту.
"""
import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import brands as AB
from ..abcp import _key, _num
from . import dialog as D
from . import reviews as R
from . import text as T
from .catalog import Catalog, Detail, LaximoError, TreeIndex, Vehicle, image_url
from .offers import _offer, axis_vote, brand_candidates, curate, days, lr_vote, oem_brand_for

RULES_FILE = Path(__file__).resolve().parent.parent / "data" / "podbor_rules.json"
MAX_POSITIONS = 10
MAX_VARIANTS = 6
MAX_PRICED = 4


def load_rules(path: Path = RULES_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Candidate:
    d: Detail
    score: float
    prec: float
    member: bool                 # деталь из состава группы, а не просто из того же узла
    side: T.Side
    side_source: str = ""        # каталог / поставщики
    brand: str = ""
    rows: list[dict] | None = None
    vote: tuple[str, int, int] = ("", 0, 0)
    warning: str = ""
    foreign: bool = False        # по названию это деталь другой группы («Ремень грм» в «Ремне приводном» у Ford)

    @property
    def kind(self) -> tuple[str, str, str]:
        """Одна и та же деталь под разными номерами: у Ford рядом с оригиналом стоит его же
        версия Motorcraft («…, Не для гарантийного ремонта автомобиля, Motorcraft»)."""
        return self.side.axis, self.side.lr, base_name(self.d.name).lower()


def base_name(name: str) -> str:
    """Название детали без хвоста после запятой («…, Motorcraft», «…, Не включает водяной насос»).
    Английские каталоги (Chrysler) пишут «BELT, POWER STEERING» — там после запятой сама суть, не режем."""
    if re.fullmatch(r"[A-Z0-9 ,./&()'+-]+", name or ""):
        return name.strip()
    return (name or "").split(",")[0].strip()


# Английские названия каталога для клиента: «BELT, ALTERNATOR AND A/C COMPRESSOR» → «Ремень генератора и кондиционера».
# Только частые слова; чего нет в словаре — остаётся как есть.
_EN_HEAD = {"BELT": "Ремень", "COVER": "Крышка", "TENSIONER": "Натяжитель", "PULLEY": "Шкив", "FILTER": "Фильтр",
            "PAD": "Колодки", "PADS": "Колодки", "ROTOR": "Диск", "BEARING": "Подшипник", "HUB": "Ступица",
            "PUMP": "Насос", "SENSOR": "Датчик", "GASKET": "Прокладка", "SEAL": "Сальник", "STRUT": "Стойка",
            "SHOCK": "Амортизатор", "LINK": "Тяга", "ARM": "Рычаг", "BULB": "Лампа", "LAMP": "Фонарь",
            "HEADLAMP": "Фара", "MIRROR": "Зеркало", "RADIATOR": "Радиатор", "THERMOSTAT": "Термостат",
            "BRAKE": "Тормоз", "CALIPER": "Суппорт", "SPARK": "Свеча", "PLUG": "Свеча", "COIL": "Катушка",
            "BOOT": "Пыльник", "BUSHING": "Втулка", "MOUNT": "Опора", "CHAIN": "Цепь", "IDLER": "Ролик обводной"}
_EN_OF = {"A/C COMPRESSOR": "кондиционера", "TIMING BELT": "ГРМ", "TIMING CHAIN": "цепи ГРМ", "ALTERNATOR": "генератора", "A/C": "кондиционера", "COMPRESSOR": "компрессора", "POWER STEERING": "ГУР",
          "WATER PUMP": "помпы", "TIMING": "ГРМ", "FAN": "вентилятора", "SERPENTINE": "поликлиновой",
          "ACCESSORY DRIVE": "навесного оборудования", "OIL": "масляный", "AIR": "воздушный", "FUEL": "топливный",
          "CABIN": "салонный", "FRONT": "передний", "REAR": "задний", "LEFT": "левый", "RIGHT": "правый",
          "AND": "и", "ENGINE": "двигателя", "WHEEL": "колеса", "STABILIZER": "стабилизатора", "SWAY BAR": "стабилизатора"}


def ru_name(name: str) -> str:
    if not re.fullmatch(r"[A-Z0-9 ,./&()'+-]+", name or ""):
        return name
    parts = [p.strip() for p in name.split(",") if p.strip()]
    head = _EN_HEAD.get(parts[0])
    if not head:
        return name
    rest = " ".join(parts[1:])
    for phrase in sorted(_EN_OF, key=len, reverse=True):   # сначала длинные: «POWER STEERING» раньше «POWER»
        rest = re.sub(rf"(?<![A-Z]){re.escape(phrase)}(?![A-Z])", _EN_OF[phrase], rest)
    return re.sub(r"\s+", " ", f"{head} {rest}").strip()


class Engine:
    def __init__(self, src, folder: Path | None, warranty: set[str] | None = None, rules: dict | None = None):
        rules = rules or load_rules()
        stop = {w.lower() for w in rules.get("stop", [])}
        self.stop = frozenset(stop | {T.stem(w) for w in stop})
        self.pr_axis = {code: axis for axis, codes in rules.get("pr_axis", {}).items() for code in codes}
        self.notes = [{k: [T.stem(w) for w in n.get(k, [])] for k in ("query", "detail", "lacks", "unless")}
                      | {"text": n["text"]} for n in rules.get("notes", [])]
        self.not_catalog = [st for st in (T.stems(w, self.stop) for w in rules.get("not_catalog", {}).get("words", [])) if st]
        # Нет деталей в группе — где искать ещё: «комплект ГРМ» у мотора с цепью → группы цепи
        self.fallback = [{"from": set(f["from"]), "to": f["to"], "note": f["note"]} for f in rules.get("fallback", [])]
        # Соседи уточняют: «диски» рядом с «колодками» — тормозные
        self.context = [{"word": T.stem(c["word"]), "near": [T.stem(w) for w in c["near"]], "add": c["add"],
                         "add_stem": T.stem(c["add"])} for c in rules.get("context", [])]
        # Многозначные слова: «ГБЦ» — головка целиком или прокладка? Спросить, если в позиции нет уточнения
        self.ask = [{"query": [T.stem(w) for w in a["query"]], "unless": [T.stem(w) for w in a.get("unless", [])],
                     "text": a["text"]} for a in rules.get("ask", [])]
        self.src = src
        not_typos = frozenset(T.stem(w) for w in rules.get("not_typos", {}).get("words", []))
        self.catalog = Catalog(src.laximo, folder, self.stop, rules.get("synonyms", []), not_typos)
        self.warranty = {AB.get().key(b) for b in (warranty or set())}
        self._own: dict[tuple[int, tuple[str, ...]], tuple | None] = {}

    # ---------- заявка целиком ----------

    async def run(self, text: str, vehicle: int | None = None, memory: dict | None = None, analogs: int = 3) -> dict:
        """Заявка. С памятью прошлого ответа и без VIN в тексте — следующая реплика того же разговора."""
        t0 = time.time()
        req = T.parse(text)
        if memory and (memory.get("ident") or memory.get("plate")) and not req.ident and not req.plate:
            return await self._follow(text, req, memory, analogs, t0)
        res: dict[str, Any] = {"request": {"ident": req.ident, "plate": req.plate, "model": req.model,
                                           "chunks": req.chunks, "vehicle": vehicle},
                               "status": "ok", "vehicle": None, "vehicles": [], "warnings": [], "positions": []}
        if not req.ident and not req.plate:
            return self._done(res, "no_vin", t0)
        try:
            found = await self.catalog.vehicles(req.ident, req.plate)
        except LaximoError as e:
            res["warnings"].append(f"Каталог ответил ошибкой {e.code}")
            return self._done(res, "catalog_error", t0)
        if not found:
            return self._done(res, "vehicle_not_found", t0)
        if len(found) > 1 and vehicle is None:
            res["vehicles"] = [v.public() for v in found]
            return self._done(res, "choose_vehicle", t0)
        v = found[min(vehicle or 0, len(found) - 1)]
        res["vehicle"] = v.public()
        res["warnings"] += compare_model(req.model, v)
        try:
            tree = await self.catalog.tree(v)
        except LaximoError as e:
            if e.code == "E_NOTSUPPORTED":
                # У каталога нет быстрых групп (SsangYong, АвтоВАЗ): машину нашли, детали — только вручную
                return self._done(res, "no_quick_groups", t0)
            res["warnings"].append(f"Каталог групп недоступен ({e.code})")
            return self._done(res, "catalog_error", t0)
        items = self.split(req.chunks, tree)
        if not items:
            return self._done(res, "no_positions", t0)
        res["positions"] = list(await asyncio.gather(*(self.position(v, tree, q, s) for q, s in items[:MAX_POSITIONS])))
        if len(items) > MAX_POSITIONS:
            res["warnings"].append(f"Разобраны первые {MAX_POSITIONS} позиций из {len(items)}")
        return self._done(res, "ok", t0)

    def _done(self, res: dict, status: str, t0: float, prev: dict | None = None,
              replaced: list[int] | None = None) -> dict:
        res["status"] = status
        res["seconds"] = round(time.time() - t0, 1)
        res["memory"] = D.remember(res, prev, replaced)
        res["text"] = draft(res)
        return res

    # ---------- следующая реплика разговора ----------

    async def _follow(self, text: str, req: T.Request, mem: dict, analogs: int, t0: float) -> dict:
        """«давайте за 1650», «а задние?», «прокладку», «а расходомер воздуха?» — машина и позиции из памяти."""
        res: dict[str, Any] = {"request": {"ident": mem.get("ident", ""), "plate": mem.get("plate", ""), "model": "",
                                           "chunks": req.chunks, "vehicle": mem.get("vehicle")},
                               "status": "ok", "followup": True, "vehicle": None, "vehicles": [], "warnings": [],
                               "positions": [], "reply": None}
        try:
            found = await self.catalog.vehicles(mem.get("ident", ""), mem.get("plate", ""))
        except LaximoError as e:
            res["warnings"].append(f"Каталог ответил ошибкой {e.code}")
            return self._done(res, "catalog_error", t0, mem)
        if not found:
            return self._done(res, "vehicle_not_found", t0, mem)
        v = found[min(mem.get("vehicle") or 0, len(found) - 1)]
        res["vehicle"] = v.public()
        try:
            tree = await self.catalog.tree(v)
        except LaximoError as e:
            return self._done(res, "no_quick_groups" if e.code == "E_NOTSUPPORTED" else "catalog_error", t0, mem)
        positions = mem.get("positions") or []
        plan = D.plan(text, mem, self.stop, tree, analogs, self.clean)
        if plan["kind"] != "chat" and plan.get("handoff"):
            res["handoff"] = plan["handoff"]   # на это менеджер отвечает сам — бот сделал свою часть
        if plan["kind"] in ("pick", "pick_unclear", "answer"):
            r = plan["reply"]
            res["reply"] = await self._reply(r, positions, v)
            return self._done(res, "answer" if r["kind"] == "answer" else "order", t0, mem)
        jobs, replaced = plan["jobs"], plan["replaced"]
        if plan["kind"] == "new":
            jobs = self.split(req.chunks, tree)
        if not jobs:
            return self._done(res, "chat", t0, mem)
        res["positions"] = list(await asyncio.gather(*(self.position(v, tree, q, s) for q, s in jobs[:MAX_POSITIONS])))
        return self._done(res, "ok", t0, mem, replaced)

    async def _reply(self, r: dict, positions: list[dict], v: Vehicle) -> dict:
        """Ответ про показанные предложения — в виде, который можно сохранить и пересобрать в текст."""
        def item(kind: str, p: dict, x: dict | None, **extra) -> dict:
            out = {"type": kind, "query": p["query"], "name": x["var"]["name"] if x else "",
                   "who": x["who"] if x else "", "offer": x["offer"] if x else None,
                   "axis": x["var"]["axis"] if x else "", "lr": x["var"]["lr"] if x else ""}
            return out | extra | ({"why": x["why"]} if x and x.get("why") else {})
        answers = [item(k if x else "none_" + k, p, x) for k, p, x in r.get("answers", [])]
        for a in r.get("ask_brand", []):
            p = positions[a["position"]]
            got = None
            # Все номера позиции, и «тот же оригинал» тоже: у свечей Audi Bosch есть у 06H905611, а у 06H905621 нет
            for var in p.get("variants", [])[:MAX_VARIANTS]:
                try:
                    _, rows = await self.offers(var["oem"], v.brand)
                except Exception:
                    rows = []
                rows = [x for x in rows or [] if AB.get().key(x.get("brand")) == a["brand"] and _num(x.get("price")) > 0]
                if rows:
                    best = min(rows, key=lambda x: (days(x.get("deliveryPeriod")), _num(x.get("price"))))
                    got = item("brand", p, {"var": var, "who": "", "offer": _offer(best)})
                    break
            answers.append(got or item("no_brand", p, None, word=a["word"]))
        picks = [{"type": "pick", **x} for x in r.get("picks", [])]
        return {"kind": r["kind"], "answers": answers, "picks": picks, "unclear": r.get("unclear", [])}

    # ---------- позиции ----------

    def split(self, chunks: list[str], tree: TreeIndex) -> list[tuple[str, T.Side]]:
        """«Масляный фильтр, воздушный, салонный» → три позиции; «колодки передние, комплект» → одна.
        «Колодки передние и задние» → две: одно слово стороны повторяет соседнюю деталь."""
        out: list[tuple[str, T.Side]] = []
        for chunk in chunks:
            # «…колодки и диски, спасибо» — вежливый хвост через запятую не делает из одной позиции две
            segs = chunk.split(",")
            while len(segs) > 1 and all(w in self.stop for w in T.words(segs[-1])):
                segs.pop()
            chunk = ",".join(segs)
            whole = T.side(chunk)
            pieces = share_noun(T.split_pieces(chunk), self.stop)
            if len(pieces) == 1:
                if T.stems(chunk, self.stop):
                    out.append((self.clean(chunk), whole))
                continue
            parts: list[dict] = []
            for p in pieces:
                st, ps = T.stems(p, self.stop), T.side(p)
                if not st:
                    if ps.axis or ps.lr:
                        parts.append({"text": p, "side": ps, "only_side": True, "top": None})
                    elif parts and not all(w in self.stop or w.isdigit() for w in T.words(p)):
                        parts[-1]["text"] += " " + p   # «оригинал» — к детали; «спасибо» не приклеиваем
                    continue
                ranked = tree.rank(st, ps)
                top = ranked[0][0].id if ranked and ranked[0][1] >= 0.5 else None
                if top is None and parts and not parts[-1]["only_side"]:
                    parts[-1]["text"] += ", " + p   # «комплект», «оригинал» — уточнение к предыдущей детали
                else:
                    parts.append({"text": p, "side": ps, "only_side": False, "top": top})
            self._context(parts, tree)
            details = [x for x in parts if not x["only_side"]]
            if not details or (len({x["top"] for x in details if x["top"] is not None}) < 2
                               and len(details) == len(parts)):
                out.append((self.clean(chunk), whole))
                continue
            # «Колодки и диски передние» — сторона на обе детали. «Подшипник ступицы, колодки передние» —
            # через запятую разные позиции: «передние» относится только к колодкам.
            shared = T.Side() if "," in chunk else whole
            for i, x in enumerate(parts):
                if not x["only_side"]:
                    out.append((self.clean(x["text"]), T.Side(x["side"].axis or shared.axis, x["side"].lr or shared.lr)))
                    continue
                near = (next((y for y in reversed(parts[:i]) if not y["only_side"]), None)
                        or next((y for y in parts[i + 1:] if not y["only_side"]), None))
                if near:
                    base = T.RIGHT.sub("", T.LEFT.sub("", T.REAR.sub("", T.FRONT.sub("", near["text"]))))
                    out.append((self.clean(f"{base} {x['text']}"), x["side"]))
        return out

    def _context(self, parts: list[dict], tree: TreeIndex):
        """Соседние позиции уточняют друг друга: «колодки и диски» — диски тормозные, а не колёсные;
        «салонник, воздухан, масляный» — масляный фильтр (существительное из группы соседа)."""
        items = [x for x in parts if not x["only_side"]]
        for i, x in enumerate(items):
            st = T.stems(x["text"], self.stop)
            if not st:
                continue
            others = [s for y in items if y is not x for s in T.stems(y["text"], self.stop)]
            changed = False
            for rule in self.context:
                if T.same(T.head(st), rule["word"]) and not any(T.same(rule["add_stem"], s) for s in st) \
                        and any(T.same(n, s) for n in rule["near"] for s in others):
                    x["text"] += " " + rule["add"]
                    changed = True
                    break   # «диски» и «диск» — одна основа: второй раз не добавляем
            if all(s in T.ADJ for s in st):
                for y in items[i - 1::-1] + items[i + 1:] if i else items[i + 1:]:
                    g = tree.groups.get(y["top"]) if y["top"] is not None else None
                    noun = next((w for w in T.words(g.name) if T.stem(w) not in T.ADJ), "") if g else ""
                    if noun:
                        x["text"] += " " + noun
                        changed = True
                        break
            if changed:
                r = tree.rank(T.stems(x["text"], self.stop), x["side"])
                x["top"] = r[0][0].id if r and r[0][1] >= 0.5 else None

    def clean(self, query: str) -> str:
        """«Здравствуйте, вин , нужны передние колодки» → «передние колодки»."""
        ws = re.split(r"(\s+|,)", query)
        i = 0
        # Пропускаем стоп-слова и связки: «Здравствуйте, можно узнать цену и сроки, масляный фильтр» → «масляный фильтр»
        skip = lambda w: (not w.strip(" ,.!?") or w.strip(" ,.!?").lower() in self.stop  # noqa: E731
                          or w.strip(" ,.!?").lower() in ("и", "а", "по", "на", "в", "к", "с"))
        while i < len(ws) and skip(ws[i]):
            i += 1
        # И с конца: «…стоимость обводного ремня ....Спасибо.» → «обводного ремня»
        j = len(ws)
        while j > i and (skip(ws[j - 1]) or all(skip(x) for x in re.split(r"[.!?]+", ws[j - 1]) if x)):
            j -= 1
        return re.sub(r"\s+", " ", re.sub(r"\s*,\s*(,\s*)+", ", ", "".join(ws[i:j]))).strip(" ,.!?") or query.strip()

    def outside(self, q: list[str]) -> bool:
        """Инструмент, химия, аксессуары: главное слово запроса — из списка «не каталог»
        («очиститель тормозов», «камера заднего вида»), а не просто встречается в нём."""
        h = T.head(q)
        return any(all(any(T.same(w, s) for s in q) for w in nc) and T.same(h, T.head(nc)) for nc in self.not_catalog)

    async def position(self, v: Vehicle, tree: TreeIndex, query: str, want: T.Side) -> dict:
        pos: dict[str, Any] = {"query": query, "side": {"axis": want.axis, "lr": want.lr}, "status": "not_found",
                               "groups": [], "variants": [], "question": "", "note": ""}
        q = T.stems(query, self.stop)
        if self.outside(q):
            pos["note"] = "Это не деталь каталога автомобиля — подберём по названию."
            return pos
        ranked = tree.rank(q, want)
        if not ranked or ranked[0][1] < 0.3:
            return pos
        best = ranked[0][1]
        groups = [(g, s) for g, s in ranked[:3] if s >= max(0.3, best - 0.2)]
        pos["groups"] = [{"id": g.id, "name": g.name, "path": g.path, "score": round(s, 2)} for g, s in groups]
        lists = await asyncio.gather(*(self.catalog.details(v, g.id, False) for g, _ in groups),
                                     return_exceptions=True)
        pool = [d for x in lists if isinstance(x, list) for d in x]
        members = {(d.group_id, _key(d.oem)) for d in pool}
        cands = self._candidates(tree, q, want, groups, pool, members)
        if not cands:
            # В составе группы нужной стороны нет — ищем по словам во всех узлах этих групп
            lists = await asyncio.gather(*(self.catalog.details(v, g.id, True) for g, _ in groups),
                                         return_exceptions=True)
            pool += [d for x in lists if isinstance(x, list) for d in x]
            cands = self._candidates(tree, q, want, groups, pool, members)
        head_miss = ""
        if cands:
            # «Тормозные барабаны» у Nissan: отдельной группы нет, а «барабанные» в названии группы колодок
            # совпало с «барабаны». Главного слова клиента нет ни в одном найденном названии — ищем его
            # по всем узлам этих групп и берём, если нашлось
            head = T.head(tree.known(q) or q)
            has = lambda name: any(T.same(head, s) for s in T.stems(name, self.stop))  # noqa: E731
            if head and head not in T.ADJ and not any(has(c.d.name) for c in cands):
                lists = await asyncio.gather(*(self.catalog.details(v, g.id, True) for g, _ in groups),
                                             return_exceptions=True)
                full = [d for x in lists if isinstance(x, list) for d in x if has(d.name)]
                better = self._candidates(tree, q, want, groups, full, set()) if full else []
                if better:
                    cands = better
                else:
                    head_miss = next((w for w in T.words(query) if T.same(T.stem(w), head)), head)
        fallback_note = ""
        if not cands:
            # «Комплект ГРМ» у мотора с цепью: в группе ремня пусто — смотрим группы цепи (правило fallback)
            for fb in self.fallback:
                alt = [(tree.groups[i], best) for i in fb["to"] if i in tree.groups]
                if not alt or not any(g.id in fb["from"] for g, _ in groups):
                    continue
                lists = await asyncio.gather(*(self.catalog.details(v, g.id, False) for g, _ in alt),
                                             return_exceptions=True)
                pool = [d for x in lists if isinstance(x, list) for d in x]
                cands = self._candidates(tree, q, want, alt, pool, {(d.group_id, _key(d.oem)) for d in pool})
                if cands:
                    groups, fallback_note = alt, fb["note"]
                    pos["groups"] = [{"id": g.id, "name": g.name, "path": g.path, "score": round(s, 2)} for g, s in alt]
                    break
        if not cands:
            return pos

        kept = await self._check_sides(v, cands, want)   # заодно цены и описания поставщиков у всех
        # «ШРУС наружный», а в группе только внутренний: поставщики почти все пишут «внутренний»
        fit, other = attr_check(cands, query)
        if not fit:
            pos["note"] = (f"В каталоге в этой группе нашёлся только {other} вариант — "
                           f"нужный уточним по каталогу и напишем.")
            return pos
        kept = [c for c in kept if c in fit]
        if not kept:
            pos["note"] = "По описаниям поставщиков найденные номера относятся к другой стороне."
            return pos
        kinds: dict[tuple, list[Candidate]] = {}
        for c in kept:
            kinds.setdefault(c.kind, []).append(c)
        kinds = by_suppliers(kinds, tree.known(q))
        pos["variants"] = [self._variant(c, v, alt=i > 0) for cs in kinds.values()
                           for i, c in enumerate(sorted(cs, key=lambda c: (not (c.rows and _has_original(c, v)), -c.score)))]
        # Отзывы владельцев о фирмах по этому виду детали — по словам клиента и названиям групп
        for var in pos["variants"]:
            R.annotate(var, [query] + [g.name for g, _ in groups])
        pos["status"], pos["question"] = verdict(list(kinds), want, query)
        for a in self.ask:
            if all(any(T.same(w, s) for s in q) for w in a["query"]) \
                    and not any(T.same(w, s) for w in a["unless"] for s in q):
                pos["status"] = "choose"
                pos["question"] = (pos["question"] + " " if pos["question"] else "") + a["text"]
                pos["asked"] = True
        main = next(c for c in kept if c.d.oem == pos["variants"][0]["oem"])
        pos["note"] = fallback_note or self._note(q, query, main, tree)
        if head_miss and not pos["note"]:   # своё пояснение важнее: «подшипник» → «ступица в сборе»
            pos["note"] = (f"«{head_miss[:1].upper() + head_miss[1:]}» уточним по каталогу отдельно, "
                           f"ниже — «{ru_name(main.d.name)}».")
        if pos["status"] == "found" and not main.member and main.prec >= 1.0:
            self.catalog.learn(main.d.group_id, main.d.name)
        return pos

    def _candidates(self, tree: TreeIndex, q: list[str], want: T.Side, groups, pool: list[Detail],
                    members: set) -> list[Candidate]:
        gscore = {g.id: s for g, s in groups}
        gwords = {g.id: T.stems(g.name, self.stop) for g, _ in groups}
        names = {id(d): T.stems(d.name, self.stop) for d in pool}
        known = tree.known(q)
        extra = [s for s in q if s not in known and any(T.same(s, t) for st in names.values() for t in st)]
        chosen = {g.id for g, _ in groups}
        near = [t for g, _ in groups for p, _ in g.phrases for t in p]
        cands: dict[str, Candidate] = {}
        for d in pool:
            if d.match is False:
                continue
            ds = T.side(d.context, self.pr_axis)
            if want.conflicts(ds):
                continue
            member = (d.group_id, _key(d.oem)) in members
            if member:
                # Состав группы — главный признак; слова клиента в названии только упорядочивают
                # (у Ford воздушный фильтр — «Фильтрующий элемент», трос ручника — тоже в «колодках ручника»)
                prec, rec = tree.score(known + extra, names[id(d)])
                score = gscore.get(d.group_id, 0) + 0.5 * prec
                foreign = self._foreign(tree, names[id(d)], known, chosen, near)
            else:
                # Не из состава группы — только по словам; слова группы помогают («ступичн» ~ «ступица»)
                qd = list(dict.fromkeys(known + extra + gwords.get(d.group_id, [])))
                prec, rec = tree.score(qd, names[id(d)])
                if prec < 0.45:
                    continue
                score = 0.6 * prec + 0.2 * rec
            score += 0.05 if d.match else 0
            k = _key(d.oem)
            if k not in cands or score > cands[k].score:
                cands[k] = Candidate(d, score, prec, member, ds, "каталог" if ds.axis else "",
                                     foreign=member and foreign)
        if not cands:
            return []
        if any(c.member and not c.foreign for c in cands.values()):
            cands = {k: c for k, c in cands.items() if not c.foreign}
        mem = [c for c in cands.values() if c.member]
        if mem:
            top = max(c.score for c in mem)
            tier = [c for c in mem if c.score >= 0.85 * top]
            if not want.axis:
                # Клиент сторону не назвал: «подшипник ступицы» у Ford — передний подшипник, а сзади
                # в той же группе ступица в сборе. Покажем и другую сторону, чтобы спросить, а не угадывать.
                axes = {c.side.axis for c in tier}
                other = sorted((c for c in cands.values() if c not in tier and c.side.axis
                                and c.side.axis not in axes
                                and (c.score >= 0.7 * top if c.member else c.prec >= 0.5)), key=lambda c: -c.score)
                tier += other[:1]
        else:
            top = max(c.score for c in cands.values())
            tier = [c for c in cands.values() if c.score >= 0.85 * top]
        if any(c.d.match for c in tier):
            tier = [c for c in tier if c.d.match]
        tier = drop_accessories(tier, q)
        return sorted(tier, key=lambda c: -c.score)[:MAX_VARIANTS]

    def _foreign(self, tree: TreeIndex, name: list[str], q: list[str], chosen: set[int], near: list[str]) -> bool:
        """Название детали целиком — название другой группы, и в той есть слово, которого нет ни в запросе,
        ни в выбранных группах: «Ремень грм» при запросе «ремень генератора». Задняя «Ступица колеса»
        в «Подшипнике ступичном» не чужая: «колеса» есть в разделе «Ступица колеса, составляющие»."""
        if not name:
            return False
        key = (id(tree), tuple(name))
        if key not in self._own:
            r = tree.rank(name, T.Side())
            self._own[key] = r[0] if r and r[0][1] >= 1.0 else None
        hit = self._own[key]
        if not hit or hit[0].id in chosen:
            return False
        words = hit[0].phrases[0][0]
        return any(not any(T.same(w, t) for t in q + near) for w in words)

    async def _check_sides(self, v: Vehicle, cands: list[Candidate], want: T.Side) -> list[Candidate]:
        """Цены и описания поставщиков: заодно проверяем сторону там, где каталог её не написал."""
        priced, seen = [], set()
        for c in cands:   # сначала по одному номеру на каждую деталь, потом остальные
            if c.kind not in seen:
                priced.append(c)
                seen.add(c.kind)
        priced += [c for c in cands if c not in priced]
        await asyncio.gather(*(self._price(v, c) for c in priced[:MAX_PRICED]))
        kept = []
        for c in cands:
            axis, f, r = c.vote
            if axis and not c.side.axis:
                c.side, c.side_source = T.Side(axis, c.side.lr), "поставщики"
            elif axis and c.side.axis and axis != c.side.axis:
                c.warning = (f"Каталог: {T.AXIS_RU[c.side.axis]}, а поставщики чаще пишут "
                             f"{T.AXIS_RU[axis]} ({f} против {r}) — проверьте")
            if not c.side.lr and c.rows and (lr := lr_vote(c.rows)):
                c.side = T.Side(c.side.axis, lr)
                c.side_source = c.side_source or "поставщики"
            if not want.conflicts(c.side):
                kept.append(c)
        return kept

    async def _price(self, v: Vehicle, c: Candidate):
        try:
            c.brand, c.rows = await self.offers(c.d.oem, v.brand)
        except Exception:
            c.brand, c.rows = "", None
        if c.rows:
            c.vote = axis_vote(c.rows)

    async def offers(self, oem: str, car_brand: str) -> tuple[str, list[dict]]:
        """Бренд оригинала из «Вы искали» и его предложения. FOMOCO у Ford отдаёт только сам номер,
        без аналогов — поэтому сначала основной бренд (FORD), а пустой ответ — повод взять следующий.
        Не кэшируем: цены и сроки меняются, каждый подбор — свежие."""
        brands = brand_candidates(await self.src.brands(oem), car_brand) or [oem_brand_for(car_brand)]
        brand, rows = brands[0], []
        for b in brands[:2]:
            rows = await self.src.offers(oem, b)
            if rows:
                brand = b
                break
        return brand, rows

    def _variant(self, c: Candidate, v: Vehicle, alt: bool) -> dict:
        d = c.d
        return {
            "oem": d.oem, "brand": c.brand or oem_brand_for(v.brand), "name": d.name, "note": d.note,
            "amount": d.amount, "unit": d.unit, "unit_note": d.unit_note, "match": d.match, "alt": alt,
            "axis": c.side.axis, "lr": c.side.lr, "side_source": c.side_source,
            "vote": {"front": c.vote[1], "rear": c.vote[2]}, "warning": c.warning, "score": round(c.score, 2),
            "scheme": {"catalog": v.catalog, "unit_id": d.unit_id, "ssd": d.unit_ssd,
                       "image": image_url(d.image) if d.image else "", "code": d.code_on_image},
            "offers": curate(c.rows, d.oem, v.brand, self.warranty, name=d.name) if c.rows else None,
        }

    def _note(self, q: list[str], query: str, c: Candidate, tree: TreeIndex) -> str:
        """Чего из запроса нет в найденной детали: «подшипник» → в каталоге «Ступица колеса»."""
        have = T.stems(c.d.name + " " + c.d.unit, self.stop)
        has = lambda s: any(T.same(s, x) for x in have)  # noqa: E731
        for n in self.notes:
            if all(any(T.same(s, x) for x in q) for s in n["query"]) \
                    and not any(T.same(s, x) for s in n["unless"] for x in q) \
                    and all(has(s) for s in n["detail"]) and not any(has(s) for s in n["lacks"]):
                return n["text"]
        if c.member:
            return ""   # деталь из состава группы — названия у Ford бывают любые («Фильтрующий элемент»)
        missing = set(tree.known([s for s in q if not has(s)]))  # марку и модель не считаем
        words = [w for w in T.words(query) if T.stem(w) in missing]
        if not words:
            return ""
        return f"Отдельно «{' '.join(dict.fromkeys(words))}» в каталоге не нашли — для этой машины там «{c.d.name}»."


# Противоположные признаки, которые поставщики пишут в описании: клиент просит один — номер про другой
_ATTRS = [(re.compile(r"наружн|внешн", re.I), re.compile(r"внутр", re.I), "наружный", "внутренний"),
          (re.compile(r"верхн", re.I), re.compile(r"нижн", re.I), "верхний", "нижний"),
          (re.compile(r"впуск", re.I), re.compile(r"выпуск", re.I), "впускной", "выпускной")]


def attr_check(cands: list["Candidate"], query: str) -> tuple[list["Candidate"], str]:
    """Убираем номера, про которые поставщики почти единогласно пишут обратный признак
    (3+ артикула и вчетверо больше, как со стороной). Возвращаем ещё, какой признак нашёлся вместо нужного."""
    found = ""
    for a, b, a_name, b_name in _ATTRS:
        for want, other, other_name in ((a, b, b_name), (b, a, a_name)):
            if not want.search(query) or other.search(query):
                continue
            keep = []
            for c in cands:
                seen: dict[tuple, str] = {}
                for r in c.rows or []:
                    k = (str(r.get("brand") or "").upper(), _key(r.get("numberFix") or r.get("number")))
                    seen.setdefault(k, str(r.get("description") or ""))
                yes = sum(1 for d in seen.values() if want.search(d) and not other.search(d))
                no = sum(1 for d in seen.values() if other.search(d) and not want.search(d))
                if no >= 3 and no >= 4 * yes:
                    found = other_name
                else:
                    keep.append(c)
            cands = keep
    return cands, found


_PARKING = [T.stem(w) for w in ("стояночного", "стояночный", "ручного", "ручник")]
_EXCLUSIVE = [(T.stem("ремень"), T.stem("цепи")), (T.stem("цепь"), T.stem("ремня"))]
# Мелочь при детали в каталоге. Ремкомплекта нет: «ремкомплект подшипника» у Ford — сам подшипник с крепежом
_SMALL = [T.stem(w) for w in ("пружина", "направляющая", "прокладка", "уплотнительная", "уплотнение", "болт", "гайка", "шайба",
                               "скоба", "клипса", "фиксатор", "заглушка", "кольцо", "стопорное", "датчик", "пыльник",
                               "сальник", "втулка", "кронштейн", "крышка")]


def drop_accessories(tier: list["Candidate"], q: list[str]) -> list["Candidate"]:
    """«Колодки передние» у Toyota: в группе и колодки стояночного тормоза, и их пружины. Если клиент
    не писал про ручник — стояночные убираем; мелочь («Натяжная пружина…», «Направляющая цепи»,
    «Уплотнительная прокладка натяжителя») — если клиент спрашивал саму деталь, а не её. Только когда
    после этого что-то остаётся: у Ford воздушный фильтр зовётся «Фильтрующий элемент»."""
    def first_two(c: "Candidate") -> list[str]:
        return T.stems(c.d.name)[:2]

    for a, b in _EXCLUSIVE:
        if any(T.same(x, a) for x in q) and not any(T.same(x, b) for x in q):
            only = [c for c in tier if not any(T.same(s, b) for s in T.stems(c.d.name))]
            tier = only or tier
    wants_parking = any(T.same(a, b) for a in q for b in _PARKING)
    wants_small = any(T.same(a, b) for a in q for b in _SMALL)
    keep = [c for c in tier
            if (wants_parking or not any(T.same(a, b) for a in T.stems(c.d.name) for b in _PARKING))
            and (wants_small or not any(T.same(a, b) for a in first_two(c) for b in _SMALL))]
    return keep or tier


def by_suppliers(kinds: dict[tuple, list["Candidate"]], known: list[str]) -> dict[tuple, list["Candidate"]]:
    """В группе несколько разных деталей (у VAG в «Электронике двигателя» — «Датчик импульсов», детонации,
    давления), а клиент назвал какую: «датчик коленвала». Оставляем те, у которых поставщики в описаниях
    пишут это слово («датчик положения коленвала»), если у других его нет совсем."""
    if len(kinds) < 2 or len(known) < 2:
        return kinds
    head = T.head(known)
    words = [s for s in known if s != head]
    share = {}
    for k, cs in kinds.items():
        descs = {str(r.get("description") or "").lower() for c in cs for r in (c.rows or [])} - {""}
        if len(descs) >= 5:
            share[k] = sum(1 for d in descs if any(T.same(w, s) for w in words for s in T.stems(d))) / len(descs)
    if not share or max(share.values()) < 0.3:
        return kinds
    return {k: cs for k, cs in kinds.items() if share.get(k, 1.0) >= 0.05}


def share_noun(pieces: list[str], stop: frozenset[str]) -> list[str]:
    """Кусок из одних прилагательных берёт существительное у соседа с той же конструкцией
    «прилагательное + существительное»: «2 впускных и 2 выпускных клапана» — у следующего,
    «масляный фильтр, воздушный, салонник» — у предыдущего («салонник» без прилагательного не годится)."""
    def noun_of(piece: str) -> str:
        st = T.stems(piece, stop)
        h = T.head(st) if st else None
        if not h or h in T.ADJ or not any(s in T.ADJ for s in st):
            return ""
        return next((w for w in T.words(piece) if T.stem(w) == h), "")

    out = list(pieces)
    for i, p in enumerate(pieces):
        st = T.stems(p, stop)
        if not st or any(s not in T.ADJ for s in st):
            continue
        noun = (noun_of(pieces[i + 1]) if i + 1 < len(pieces) else "") or (noun_of(pieces[i - 1]) if i else "")
        if noun:
            out[i] = f"{p} {noun}"
    return out


def _has_original(c: Candidate, v: Vehicle) -> bool:
    o = curate(c.rows or [], c.d.oem, v.brand, set())
    return bool(o["original"])


def verdict(kinds: list[tuple[str, str, str]], want: T.Side, query: str = "") -> tuple[str, str]:
    if len(kinds) == 1:
        return "found", ""
    axes = {k[0] for k in kinds}
    lrs = [k[1] for k in kinds]
    if not want.axis and len(axes - {""}) > 1:
        return "choose", T.ask_axis(query)   # «Нужен передний или задний?» — в согласии с деталью
    if len(axes) == 1 and "" not in lrs and len(set(lrs)) == len(kinds) and not want.lr:
        return "found", ""   # левая и правая — разные номера, нужны обе
    return "choose", "В каталоге несколько вариантов — уточните по примечанию или по номеру позиции на схеме."


def compare_model(model: str, v: Vehicle) -> list[str]:
    """Клиент выбирает модель из списка руками и ошибается — сверяем только год и объём двигателя."""
    out = []
    if not model:
        return out
    m = re.search(r"\b(19[89]\d|20[0-4]\d)\b", model)
    made = v.attrs.get("manufactured") or (re.search(r"(19|20)\d\d", v.attrs.get("date", "")) or [""])[0]
    if m and made and abs(int(m.group(1)) - int(made[:4])) > 1:
        out.append(f"В заявке {m.group(1)} год, по VIN — {made[:4]}")
    m = re.search(r"\b(\d)[.,](\d)\b", model)
    eng = " ".join(v.attrs.get(k, "") for k in ("engine", "engine_info"))
    e = re.search(r"\b(\d)[.,](\d)\s*L\b", eng, re.I) or re.search(r"\b(\d)(\d)\d\d\s*CC\b", eng, re.I)
    if m and e and (m.group(1), m.group(2)) != (e.group(1), e.group(2)):
        out.append(f"В заявке двигатель {m.group(1)}.{m.group(2)}, по VIN — {e.group(1)}.{e.group(2)}")
    return out


# ---------- черновик ответа клиенту ----------

def money(x: float) -> str:
    return f"{int(round(x)):,}".replace(",", " ") + " ₽"


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def when(days: int) -> str:
    """Срок поставки так, как его скажет менеджер: «в наличии», «1 день», «6 дней»."""
    return "в наличии" if days <= 0 else f"{days} {plural(days, 'день', 'дня', 'дней')}"


def nice_brand(b: str) -> str:
    """«FORD» → «Ford», «FEBI BILSTEIN» → «Febi Bilstein»; аббревиатуры (NGK, SKF, TRW) и «LYNXauto» — как есть."""
    b = (b or "").strip()
    return b.title() if b.isupper() and len(b.replace(" ", "").replace("-", "")) > 3 else b


def nice_name(desc: str, brand: str = "", number: str = "") -> str:
    """Наименование поставщика для клиента: без бренда и артикула внутри, без КРИКА заглавными, не длиннее 60 знаков.
    «ПОДШИПНИК СТУПИЦЫ ПЕРЕДНЕЙ FAG 713679190» → «Подшипник ступицы передней»."""
    t = desc or ""
    for x in (brand, number, re.sub(r"[\s.\-/]", "", number or "")):
        if x and len(x) >= 3:
            t = re.sub(re.escape(x), " ", t, flags=re.I)
    # Свой артикул поставщика в начале: «173-609_ _Колодки тормозные» → «Колодки тормозные»
    t = re.sub(r"^\s*(?=[\w./-]*\d)[\w./-]*[-_/][\w./-]*[\s_]+(?=[^\W\d_])", "", t)
    t = re.sub(r"\s+", " ", re.sub(r"[|;_]+", " ", t)).strip(" ,.-—()[]")
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        t = t.lower()
    t = t[:1].upper() + t[1:]
    cut = len(t) > 60
    if cut:
        t = t[:60].rsplit(" ", 1)[0]
    # Не оставлять открытую скобку: «Подшипник ступицы (с ABS, 42x45x82» → «Подшипник ступицы»
    for o, c in ("()", "[]"):
        if t.count(o) > t.count(c) and t.rindex(o) > 0:
            t, cut = t[:t.rindex(o)], True
    t = re.sub(r"(?:\s+(?:и|с|для|на|в|от|по))+$", "", t.rstrip(" ,.-—/"))   # «Подшипник ступицы и»
    return t + "…" if cut else t


# Пометки аналогов, которые понятны клиенту. «Быстрее всего» не пишем: список и так по сроку.
CLIENT_TAGS = {"дешевле всего": "самый дешёвый", "частая замена": "часто берут", "гарантия магазина": "гарантия магазина"}


_VAGUE = {"", "деталь", "автодеталь", "запчасть", "автозапчасть", "запасная часть", "товар", "изделие", "part",
          "деталь автомобиля", "аналог", "оригинал"}


def _offer_line(o: dict, numbers: bool, name: str = "", who: str = "", fallback: str = "") -> str:
    """«• Kroner — Ступица задняя с подшипником — 3 930 ₽, 1 день · гарантия магазина».
    Артикул — только если менеджер включил его в настройках: клиенту он обычно не нужен."""
    brand = nice_brand(o["brand"])
    head = f"{brand} {o['number']}" if numbers else brand
    if who:
        head += f" ({who})"
    title = name or nice_name(o.get("description", ""), o["brand"], o["number"])
    if not name and title.lower().strip(" .…") in _VAGUE:
        title = fallback   # «Деталь», «Автодеталь» — ни о чём: берём название из каталога
    line = f"• {head}" + (f" — {title}" if title else "") + f" — {money(o['price'])}, {when(o['days'])}"
    if o.get("cheaper"):
        line += f" (или {money(o['cheaper']['price'])} за {when(o['cheaper']['days'])})"
    tags = [CLIENT_TAGS[t] for t in o.get("tags", []) if t in CLIENT_TAGS]
    rv = o.get("reviews") or {}
    if rv.get("client"):   # только хорошее и только при 3+ отзывах — см. reviews.py
        n = rv["good"] + rv["bad"] + rv["mixed"]
        tags.append(f"хорошие отзывы владельцев ({rv['good']} из {n})")
    return line + (f" · {', '.join(tags)}" if tags else "")


def _block(var: dict, alts: list[dict], numbers: bool, analogs: int, query: str = "") -> tuple[list[str], int]:
    """Строки одного варианта: оригинал, тот же оригинал под другим номером, аналоги по сроку."""
    out = []
    o = var["offers"]
    catalog_name = ru_name(base_name(var["name"]))
    if catalog_name.lower().strip(" .…") in _VAGUE:   # у VAG бывает и просто «Деталь»
        catalog_name = query[:1].upper() + query[1:]
    if o and o["original"]:
        # Номер — из каталога: поставщики пишут его как попало («1 712 024»). Название — поставщика, если оно
        # про ту же деталь («Колодки тормозные передние»), иначе каталожное: «Focus 2011-> задний» ни о чём
        orig = dict(o["original"], number=var["oem"])
        theirs = nice_name(orig["description"], orig["brand"], orig["number"])
        same = theirs.lower().strip(" .…") not in _VAGUE and             any(T.same(a, b) for a in T.stems(theirs) for b in T.stems(catalog_name))
        out.append(_offer_line(orig, numbers, theirs if same else catalog_name, "оригинал"))
    else:
        num = f" {var['oem']}" if numbers else ""
        out.append(f"• {nice_brand(var['brand'])}{num} (оригинал) — {catalog_name} — цену и срок уточним")
    for a in alts:
        ao = a["offers"]
        who = "Motorcraft, тот же оригинал" if "motorcraft" in a["name"].lower() else "тот же оригинал"
        if ao and ao["original"]:
            x = dict(ao["original"], number=a["oem"])
            out.append(_offer_line(x, numbers, nice_name(x["description"], x["brand"], x["number"]), who))
    more = 0
    if o and o["analogs"]:
        shown = o["analogs"][:analogs]
        out += [_offer_line(a, numbers, fallback=catalog_name) for a in shown]
        more = o["stats"]["articles"] - 1 - len(shown)
    return out, max(more, 0)


def draft(res: dict, numbers: bool = False, analogs: int = 3) -> str:
    """Ответ клиенту. По позиции: при нескольких вариантах — блоки «Передний:» / «Задний:», в каждом
    «• Фирма — наименование — цена, срок», оригинал первым, дальше аналоги — что привезём быстрее."""
    st = res["status"]
    if st == "no_vin":
        return "Пришлите, пожалуйста, VIN (17 знаков) или номер кузова — подберём точно по вашей машине."
    if st == "vehicle_not_found":
        req = res.get("request") or {}
        if re.match(r"[ZX]", req.get("ident", "")) and \
                re.search(r"ssang|санг|саньен|санйон|korando|kyron|actyon|rexton|корандо|кайрон|актион|рекстон",
                          (req.get("model", "") + " " + " ".join(req.get("chunks", []))), re.I):
            # SsangYong российской сборки: каталог знает только корейский VIN (начинается на K)
            return ("У SsangYong российской сборки VIN в каталоге не ищется. Пришлите, пожалуйста, корейский VIN — "
                    "он начинается на букву K: в ПТС в «Особых отметках» (номер шасси) или на табличке "
                    "в проёме водительской двери.")
        return ("По этому VIN машина в каталоге не нашлась. Проверьте VIN или пришлите фото СТС — "
                "подберём вручную.")
    if st == "catalog_error":
        return "Каталог сейчас не отвечает, подберём вручную."
    if st == "no_quick_groups":
        v = res["vehicle"]
        return (f"Ваш автомобиль: {(v.get('short') or v['summary']).rstrip('.')}.\n"
                "По этой машине каталог не даёт быстрый поиск деталей — менеджер подберёт вручную и напишет.")
    if st == "choose_vehicle":
        lines = ["По VIN нашлось несколько вариантов машины, уточните ваш:"]
        lines += [f"{i + 1}) {x['summary']}" for i, x in enumerate(res["vehicles"])]
        return "\n".join(lines)
    if st == "no_positions":
        return "Машину нашли. Напишите, какие запчасти нужны."
    if st == "chat":
        return ""   # не про подбор: оплата, адрес, «когда забрать» — отвечает менеджер
    if st in ("order", "answer"):
        return reply_text(res["reply"], numbers)
    v = res["vehicle"]
    if res.get("followup"):
        lines = []   # продолжение разговора: без приветствия и машины, они уже были
    else:
        lines = ["Здравствуйте! Подобрали запчасти по VIN для вашего автомобиля:", v.get("short") or v["summary"]]
    lines += [f"Обратите внимание: {w[0].lower() + w[1:]}." for w in res["warnings"]]
    found = 0
    for i, p in enumerate(res["positions"], 1):
        query = p["query"]
        lines += ["", f"{i}) {query[:1].upper() + query[1:]}"]
        if p["status"] == "not_found":
            if "не деталь каталога" in p["note"]:
                lines.append("   Это не из каталога автомобиля — подберём по названию и напишем.")
            elif p["note"].startswith("В каталоге в этой группе нашёлся только"):
                lines.append("   " + p["note"])   # «нашёлся только внутренний вариант — нужный уточним»
            else:
                lines.append("   В каталоге для вашей машины сразу не нашли — уточним и напишем.")
            continue
        if p["question"]:
            lines.append("   " + p["question"].replace(
                "В каталоге несколько вариантов — уточните по примечанию или по номеру позиции на схеме.",
                "Есть несколько вариантов, уточните, какой нужен:"))
        if p["note"] and "не нашли" not in p["note"]:
            lines.append("   " + p["note"])
        # Варианты: основной номер и «тот же оригинал» под другими номерами (Motorcraft) — к нему
        groups: list[tuple[dict, list[dict]]] = []
        for var in p["variants"]:
            if var["alt"] and groups:
                groups[-1][1].append(var)
            else:
                groups.append((var, []))
        # Сначала передние, потом задние; левые перед правыми — как читает клиент
        order = {"front": 0, "": 1, "rear": 2}
        groups.sort(key=lambda g: (order.get(g[0]["axis"], 1), {"left": 0, "": 1, "right": 2}.get(g[0]["lr"], 1)))
        many = len(groups) > 1
        labels = [T.side_label(query, var["axis"], var["lr"]) for var, _ in groups]
        seen: dict[str, int] = {}
        for n, (var, alts) in enumerate(groups, 1):
            found += 1
            rows, more = _block(var, alts, numbers, analogs if not many else min(analogs, 2), query)
            amount = re.match(r"\d+", var["amount"] or "")
            k = int(amount.group(0)) if amount else 1   # каталог пишет и «2», и «01»
            per = f"на машину нужно {k} шт., цены за штуку" if k > 1 else ""
            if many:
                side = labels[n - 1]
                if not side or labels.count(side) > 1:
                    # Несколько вариантов на одной стороне: «Задняя, вариант 2 — «Скоба»»
                    seen[side] = seen.get(side, 0) + 1
                    name = ru_name(base_name(var["name"]))
                    side = (f"{side}, вариант {seen[side]}" if side else f"Вариант {n}") + f" — «{name}»"
                lines += ["", f"   {side}" + (f" ({per})" if per else "") + ":"]
            elif per:
                lines.append(f"   {per[:1].upper() + per[1:]}.")
            lines += ["   " + r for r in rows]
            if more:
                lines.append(f"   Есть ещё {more} {plural(more, 'вариант', 'варианта', 'вариантов')} — подберём под бюджет.")
    lines.append("")
    lines.append("Цены и сроки на сегодня. Напишите, какие позиции оформить — закажем." if found
                 else "Уточним по позициям и напишем.")
    return "\n".join(lines).strip("\n")


def _title(x: dict) -> str:
    """«Колодки тормозные передние» — название из каталога, со стороной, если её нет в названии."""
    # Словами клиента: «Колодки передние», а не «1 комплект тормозных колодок с индик. износа…»
    name = x.get("query") or ru_name(base_name(x.get("name") or ""))
    name = name[:1].upper() + name[1:]
    side = T.side_label(name, x.get("axis", ""), x.get("lr", "")).lower()
    return f"{name} {side}" if side and not T.side(name).axis and not T.side(name).lr else name


def reply_text(r: dict, numbers: bool = False) -> str:
    """Ответ на реплику про показанные предложения: «Оформляем: …, итого» или «Самый недорогой — …»."""
    lines: list[str] = []
    for a in r.get("answers", []):
        t, title = a["type"], _title(a)
        if t == "cheapest":
            lines += [f"{title} — самый недорогой вариант:", _offer_line(a["offer"], numbers, who=a["who"])]
        elif t == "original":
            lines += [f"{title} — оригинал:", _offer_line(a["offer"], numbers, who="оригинал")]
        elif t == "none_original":
            lines.append(f"{title}: оригинала у поставщиков сейчас нет — только аналоги.")
        elif t == "best":
            why = {"гарантия магазина": "на него гарантия магазина",
                   "отзывы": "у него хорошие отзывы владельцев",
                   "частая замена": "его чаще всего берут",
                   "оригинал": "это оригинал"}.get(a.get("why", ""), "")
            lines += [f"{title} — советуем этот вариант" + (f": {why}." if why else "."),
                      _offer_line(a["offer"], numbers, who=a["who"])]
        elif t == "brand":
            lines += [f"{title} — есть:", _offer_line(a["offer"], numbers, who=a["who"])]
        elif t in ("stock", "fastest", "more", "quality"):
            head = {"stock": f"{title} — в наличии:",
                    "fastest": f"{title}: в наличии нет, быстрее всего привезём:",
                    "more": f"{title} — ещё варианты:",
                    "quality": f"{title} — хорошие отзывы владельцев:"}[t]
            if lines and lines[-1] == "" and head in lines:
                lines.pop()   # та же позиция и тот же вопрос — строки подряд под одним заголовком
            else:
                lines.append(head)
            lines.append(_offer_line(a["offer"], numbers, who=a["who"]))
        elif t == "none_more":
            lines.append(f"{title}: подберём ещё варианты — подскажите бюджет или фирму, которую рассматриваете.")
        elif t == "no_brand":
            lines.append(f"{title}: фирмы {a.get('word', '')} у поставщиков сейчас нет.")
        elif t.startswith("none_"):
            lines.append(f"{title}: уточним и напишем.")
        lines.append("")
    picks = r.get("picks", [])
    if picks:
        lines.append("Оформляем:")
        total, longest = 0.0, 0
        for x in picks:
            o, qty = x["offer"], x.get("qty") or 1
            line = _offer_line(o, numbers, _title(x), "оригинал" if x["who"] == "оригинал" else "")
            if qty > 1:
                price = money(o["price"])
                line = line.replace(f" — {price}", f" — {price} × {qty} = {money(o['price'] * qty)}", 1)
            lines.append(line)
            total += o["price"] * qty
            longest = max(longest, o["days"])
            k = re.match(r"\d+", x.get("amount") or "")
            if not x.get("qty") and k and int(k.group(0)) > 1:
                lines.append(f"   Цена за штуку, на машину нужно {int(k.group(0))} — сколько штук оформить?")
        lines.append(f"Итого: {money(total)}. " + ("Всё в наличии." if longest <= 0 else f"Срок — {when(longest)}."))
    for q in r.get("unclear", []):
        lines.append(f"По позиции «{q}» напишите, какой вариант оформить — фирму или цену.")
    if r.get("kind") == "order" and not picks and not r.get("unclear"):
        lines.append("Напишите, какой вариант оформить — фирму или цену.")
    return "\n".join(lines).strip("\n")
