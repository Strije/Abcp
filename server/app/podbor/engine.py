"""Подбор по заявке: VIN → машина (Laximo) → группа по словам клиента → деталь и её сторона → предложения ABCP.

Номера берутся только из Laximo и ABCP, сами мы их не придумываем. Сторону детали проверяем
в двух независимых местах: что пишет каталог (название и примечание узла, PR-коды VAG)
и что пишут поставщики в описаниях этого номера. Не сошлось или не хватает данных — вопрос клиенту.
"""
import asyncio
import collections
import json
import re
import time
from difflib import SequenceMatcher
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .. import brands as AB
from ..abcp import _key, _num
from . import dialog as D
from . import emb as EMB
from . import article as A
from . import differ as DF
from . import makes as MK
from . import schemes as SC
from . import reviews as R
from . import understand as U
from . import text as T
from .catalog import Catalog, Detail, LaximoError, TreeIndex, Vehicle, image_url
from .offers import _SMALL as _SMALL_OFFERS
from .offers import (_desc_head, _offer, axis_vote, bkey, brand_candidates, curate, days, lr_vote, oem_brand_for,
                     original_keys, replacement_candidates)

RULES_FILE = Path(__file__).resolve().parent.parent / "data" / "podbor_rules.json"
MAX_POSITIONS = 15   # сообщение мастера «по всей подвеске» — до 11–12 позиций
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
    pair: bool = False           # «левый» и «правый» в каталоге под одним номером — одна деталь на обе стороны

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
    def __init__(self, src, folder: Path | None, warranty: set[str] | None = None, rules: dict | None = None,
                 llm: Any = None):
        rules = rules or load_rules()
        self.llm = llm   # языковая модель для разбора сумбурных сообщений; None — только правила
        stop = {w.lower() for w in rules.get("stop", [])}
        self.stop = frozenset(stop | {T.stem(w) for w in stop})
        self.pr_axis = {code: axis for axis, codes in rules.get("pr_axis", {}).items() for code in codes}
        self.notes = [{k: [T.stem(w) for w in n.get(k, [])] for k in ("query", "detail", "lacks", "unless")}
                      | {"text": n["text"]} for n in rules.get("notes", [])]
        self.not_catalog = [st for st in (T.stems(w, self.stop) for w in rules.get("not_catalog", {}).get("words", [])) if st]
        # Нет деталей в группе — где искать ещё: «комплект ГРМ» у мотора с цепью → группы цепи
        self.fallback = [{"from": set(f["from"]), "to": f["to"], "note": f["note"],
                          "require": [T.stem(w) for w in f.get("require", [])]} for f in rules.get("fallback", [])]
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

    async def run(self, text: str, vehicle: int | None = None, memory: dict | None = None, analogs: int = 3,
                  llm: bool | None = None) -> dict:
        """Заявка. С памятью прошлого ответа и без VIN в тексте — следующая реплика того же разговора."""
        t0 = time.time()
        # Прилагательные — из слов этой заявки и памяти разговора, не из прошлых чужих (см. T.ADJ)
        T.fresh_adj()
        T.mark_adj(" ".join([text] + [str(p.get("query") or "") for p in (memory or {}).get("positions") or []]))
        req = T.parse(text)
        pending = (memory or {}).get("pending") or ""
        if pending and (req.ident or req.plate):
            # «Хорошие фильтры на машину» без VIN, следом — только VIN: просьба не теряется, продолжаем её
            text = pending + "\n" + text
            req, memory = T.parse(text), None
        unknown = (memory or {}).get("unknown_vin") or ""
        confirmed = False   # клиент ответил на «для какой машины?»: марку из его слов больше не сверяем с VIN
        if unknown and pending and not req.ident and not req.plate:
            confirmed = bool((memory or {}).get("mismatch"))
            # Просили описать машину (VIN каталог не знает): ответ «Шкода Рапид 2019, 1.6, автомат» — к прежней просьбе и тому же VIN
            text = pending + "\n" + text + "\n" + unknown
            req, memory = T.parse(text), None
        if memory and (memory.get("ident") or memory.get("plate")) and not req.ident and not req.plate:
            return await self._follow(text, req, memory, analogs, t0, llm is not False)
        res: dict[str, Any] = {"request": {"ident": req.ident, "plate": req.plate, "model": req.model,
                                           "chunks": req.chunks, "vehicle": vehicle},
                               "status": "ok", "vehicle": None, "vehicles": [], "warnings": [], "positions": []}
        if not req.ident and not req.plate:
            out = self._done(res, "no_vin", t0)
            if req.chunks:   # запомнить, что просил клиент: когда пришлёт VIN, ответим на это
                out["memory"]["pending"] = (pending + "\n" + text).strip()[-1500:]
            return out
        try:
            found = await self.catalog.vehicles(req.ident, req.plate)
        except LaximoError as e:
            res["warnings"].append(f"Каталог ответил ошибкой {e.code}")
            return self._done(res, "catalog_error", t0)
        if not found and req.ident:
            # VIN каталог не знает (сборка РФ, свежие модели): марка, модель и год из слов клиента → мастер подбора Laximo
            found = await self.catalog.by_text(text, req.ident)
            if found:
                res["car_text"] = text[:400]
                res["warnings"].append(BY_TEXT_WARNING)
        if not found:
            out = self._done(res, "vehicle_not_found", t0)
            if req.ident:   # просьба и VIN не теряются: клиент опишет машину словами, и подбор продолжится
                out["memory"]["pending"] = text[-1500:]
                out["memory"]["unknown_vin"] = req.ident
            return out
        if len(found) > 1 and vehicle is None:
            res["vehicles"] = [v.public() for v in found]
            return self._done(res, "choose_vehicle", t0)
        v = found[min(vehicle or 0, len(found) - 1)]
        res["vehicle"] = v.public()
        res["warnings"] += compare_model(req.model, v)
        said = None if confirmed or res.get("car_text") or not req.ident else MK.mismatch(" ".join(req.chunks), v.brand)
        if said:
            # «Шайбы форсунок на сан енг Кайрон» при VIN от BMW: подобрали бы болты для BMW. Сначала спросим, какая машина
            res["mismatch"] = {"said": said[0], "said_car": MK.FAMILIES[said[1]][0],
                               "vin_car": res["vehicle"].get("short") or v.summary()}
            out = self._done(res, "car_mismatch", t0)
            out["memory"]["pending"] = re.sub(re.escape(req.ident), " ", text, flags=re.I).strip()[-1500:]
            out["memory"]["unknown_vin"], out["memory"]["mismatch"] = req.ident, True
            return out
        try:
            tree = await self.catalog.tree(v)
        except LaximoError as e:
            if e.code == "E_NOTSUPPORTED":
                # У каталога нет быстрых групп (SsangYong, АвтоВАЗ): машину нашли, детали — только вручную
                return self._done(res, "no_quick_groups", t0)
            res["warnings"].append(f"Каталог групп недоступен ({e.code})")
            return self._done(res, "catalog_error", t0)
        meta: list[dict] = []
        parsed = None
        # Правила сначала — дёшево. Модель зовём не только на сумбурное, но и когда фраза больше чем наполовину не из
        # слов каталога («Доброе утро, подскажите по цене насос гур на gentra» правила уводили в «чехлы») или когда
        # в разборе правил нет ни одной детали («Подойдёт ли для Honda Jazz?», «Приветствую»)
        rules_items = self.split(req.chunks, tree)
        partish = [x for x in rules_items if self._partish(x[0], tree)]
        if llm is not False and (llm or needs_llm(req.chunks) or self._chatty(req.chunks, tree)
                                 or (rules_items and not partish)):
            # Сумбурное сообщение («Задок: … Передок: … Тяги+ наконечники …») — разбирает модель, ищем мы
            parsed = await U.understand(self.llm, "\n".join(req.chunks))
        if parsed and parsed["positions"]:
            got = U.items(parsed)
            items, meta = [(q, s) for q, s, _ in got], [m for _, _, m in got]
            res["understood"] = {"by": "llm", **parsed}
            res["handoff"] = parsed["questions"] + [f"не из каталога: {x}" for x in parsed["not_parts"]]
        else:
            # Фраза без единого слова детали — не позиция: клиенту «1) Приветствую» не показываем
            items = partish
            if parsed is not None or (llm is not False and self.llm and getattr(self.llm, "enabled", False)
                                      and needs_llm(req.chunks)):
                res["understood"] = {"by": "rules"}
            if parsed:
                res["handoff"] = parsed["questions"] + [f"не из каталога: {x}" for x in parsed["not_parts"]]
        arts = A.find("\n".join(req.chunks))
        hints: dict[str, str] = {}   # слова клиента рядом с номером: «прокладка на патрубок турбины V758424680»
        if arts:
            # Позиции из самого номера («571003L100 - Насос ГУР», «артикул 26209425906») заменит проверка номера
            # Из фразы вырезаем сам номер и «артикул … подойдёт?» — остальное («И масляный фильтр») остаётся позицией
            cut = re.compile("|".join(re.escape(a) for a in arts) + r"|\bартикул\w*|\bномер\w*|\bподойд\w*|\bподход\w*"
                             r"|запчасть по", re.I)
            kept_items, kept_meta = [], []
            for i, (q, sd) in enumerate(items):
                rest = re.sub(r"\s+", " ", cut.sub(" ", q)).strip(" ,.?!-—")
                # «571003L100 - Насос ГУР», «Масляный фильтр mann HU9326X» — подпись к номеру: её покажет проверка номера
                # «…подойдёт? И масляный фильтр» — после знака или «и» начинается другая просьба, её оставляем
                rest = re.sub(r"^(?:и|а|также|ещ[её]|плюс)\s+", "", rest, flags=re.I)
                other = rest == q.strip() or bool(re.search(r"[?!;]|\.\s|\s(?:и|а)\s|\+|\bтакже\b|\bещ[её]\b", q, re.I))
                if rest and self._partish(rest, tree) and not other:
                    for a in arts:
                        if A.norm(a) in A.norm(q):
                            hints[a] = rest
                if rest and self._partish(rest, tree) and other:
                    kept_items.append((rest if rest != q.strip() else q, sd))
                    if meta:
                        kept_meta.append(meta[i])
            items, meta = kept_items, (kept_meta if meta else meta)
        if not items and not arts:
            return self._done(res, "no_positions", t0)
        # «Фильтр воздушный mann» — фирма названа в первой реплике: ищем деталь без неё, фирму проверяем отдельно
        items, asks = self._split_brands(items if meta else self._all_around(items, any(_AROUND.search(c) for c in req.chunks)), v)
        reqs = [(want_qty(q), want_attrs(q)) for q, _ in items]
        items = [(strip_qty(q) or q, side) for q, side in items]   # «4 шт», «два» — не часть названия детали
        kinds = [m.get("kind", "") for m in meta] + [""] * len(items)   # «в круг» растит items только без модели
        # Без модели («Свечи ngk для киа пиканто 2») машина названа в самой фразе: ищем деталь без неё, слова клиента — в заголовке
        carless = [(_without_car(q, v), s) if not meta else (q, s) for q, s in items[:MAX_POSITIONS]]
        res["positions"] = list(await asyncio.gather(*(self.position(v, tree, q, s, kinds[i])
                                                       for i, (q, s) in enumerate(carless))))
        for p, (q, _), (cq, _) in zip(res["positions"], items, carless):
            if cq != q and p.get("query") == cq:
                p["core"], p["query"] = cq, q   # «core» — фраза без машины: по ней считаем покрытие слов
        asks = [a for a in asks if res["positions"][a["position"]].get("variants")]   # «комплект Zekkert» без детали — не про фирму
        if asks:
            res["reply"] = await self._reply({"kind": "answer", "answers": [], "picks": [], "ask_brand": asks},
                                             res["positions"], v)
        if not meta:
            if llm is not False:
                await self._arbitrate(v, tree, res["positions"])
            self._drop_lost(res["positions"], tree)
        if not meta:
            for p, (k, attrs) in zip(res["positions"], reqs):
                p["want_qty"], p["want_attrs"] = k, attrs
                check_attrs(p)
        for p, m in zip(res["positions"], meta):
            # «под вопросом», «вместо рычага в сборе — отдельно шаровые» — клиенту видно, что это не обязательно
            p["llm"] = {"uncertain": m["uncertain"], "note": m["note"], "qty": m["qty"], "lr": m["lr"]}
        for a in arts:
            pos = await self._article(v, tree, a, hints.get(a, ""), res["positions"])
            if pos is not None:
                res["positions"].append(pos)
        if len(items) > MAX_POSITIONS:
            res["warnings"].append(f"Разобраны первые {MAX_POSITIONS} позиций из {len(items)}")
        if T.OIL_SET.search(text) and not any("топлив" in (p.get("query") or "").lower() for p in res["positions"]):
            # Набор для замены масла: топливный фильтр меняют не каждый раз — спрашиваем, а не кладём молча
            res["warnings"].append("топливный фильтр меняют не при каждой замене масла — если нужен, напишите «и топливный»")
        return self._done(res, "ok", t0)

    async def _article(self, v: Vehicle, tree: TreeIndex, number: str, hint: str = "",
                       positions: list[dict] | None = None) -> dict | None:
        """«Подойдёт ли 26209425906?» — что это за деталь (поставщики), что ставится по VIN (каталог) и есть ли номер
        клиента среди аналогов этого оригинала."""
        try:
            rows = await self.src.brands(number)
        except Exception:
            rows = []
        brand, desc = A.what(rows)
        # Номер самого производителя машины (VAG у Audi): «не значится» может значить замену номера, а не «не подойдёт»
        # (строка производителя с описанием «… аналог MANNFILTER» у HU7008Z — не его номер)
        own = original_keys(v.brand)
        oem = any(bkey(r.get("brand")) in own and re.search(r"[а-яё]{4,}", str(r.get("description") or ""), re.I)
                  and not re.search(r"аналог|analog|замена\s+для", str(r.get("description") or ""), re.I) for r in rows)
        info = {"number": number, "brand": brand, "desc": desc, "fit": None, "oem": oem}
        try:
            # Ответ самого каталога: подходит ли номер этой машине (по VIN) — надёжнее догадок по аналогам поставщиков
            info["catalog"] = await self.catalog.applicability(v, number)
        except Exception:   # noqa: BLE001
            info["catalog"] = None
        # Деталь клиент назвал сам — ищем по его словам: у V758424680 поставщики пишут «вентиляционная решётка»
        name = self.clean(hint) if hint else A.name_of(desc)
        head = _name_head(name) if name else ""
        for p in positions or []:
            # «генератор krauf ALB1689DD или другой» — генератор уже в ответе: номер проверяем там, без новой позиции
            if head and T.same(_name_head(p.get("query", "")), head) and not p.get("article"):
                info["desc"] = info["desc"] or name
                info["fit"] = A.fits(number, p.get("variants") or [])
                p["article"] = info
                return None
        if not name:
            return {"query": f"номер {number}", "side": {"axis": "", "lr": ""}, "status": "not_found", "groups": [],
                    "variants": [], "question": "", "note": "", "kind": "part", "article": info}
        pos = await self.position(v, tree, name, T.Side())
        info["fit"] = A.fits(number, pos.get("variants") or [])
        info["desc"] = info["desc"] or name
        return pos | {"query": f"{(hint or desc)[:60]} ({number})", "article": info}

    def _done(self, res: dict, status: str, t0: float, prev: dict | None = None,
              replaced: list[int] | None = None) -> dict:
        res["status"] = status
        res["seconds"] = round(time.time() - t0, 1)
        attach_schemes(res)
        res["memory"] = D.remember(res, prev, replaced)
        self._manager_handoff(res)
        res["text"] = draft(res)
        return res

    @staticmethod
    def _manager_handoff(res: dict) -> None:
        """Не автозапчасти и всё, что бот не оценит сам (шины, инструмент, химия, аксессуары, жидкости без цены в каталоге),
        — менеджеру: позиция попадает в «Ответьте сами» на странице подбора, клиенту говорим, что передали."""
        out = res.setdefault("handoff", [])
        said = [p["adj_default"][:5] for p in res.get("positions", []) if p.get("adj_default")]
        if said:   # модель спросила «топливный фильтр нужен?» — мы уже ищем его, вопрос клиенту не нужен
            out[:] = [q for q in out if not any(w in q.lower() for w in said)]
        for p in res.get("positions", []):
            kind = p.get("kind", "")
            if (p.get("article") or {}).get("catalog", {}) and p["article"]["catalog"].get("status") == "FULLY":
                continue   # номер клиента подтверждён каталогом: передавать менеджеру нечего
            if p.get("dropped"):   # нашлась лишь часть слов клиента, модель лучше не нашла — менеджеру
                item = f"{p['query']} — подбор неуверенный, передано менеджеру"
                if item not in out:
                    out.append(item)
                continue
            if p.get("status") == "not_found" and (kind in MANAGER_KINDS or "не деталь каталога" in (p.get("note") or "")):
                label = MANAGER_KINDS.get(kind, "не из каталога автомобиля")
                item = f"{p['query']} — {label}"
                if item not in out:
                    out.append(item)

    # ---------- следующая реплика разговора ----------

    async def _follow(self, text: str, req: T.Request, mem: dict, analogs: int, t0: float,
                      use_llm: bool = True) -> dict:
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
        if not found and mem.get("car_text"):
            found = await self.catalog.by_text(mem["car_text"], mem.get("ident", ""))
            if found:
                res["car_text"] = mem["car_text"]
        if not found:
            return self._done(res, "vehicle_not_found", t0, mem)
        v = found[min(mem.get("vehicle") or 0, len(found) - 1)]
        res["vehicle"] = v.public()
        try:
            tree = await self.catalog.tree(v)
        except LaximoError as e:
            return self._done(res, "no_quick_groups" if e.code == "E_NOTSUPPORTED" else "catalog_error", t0, mem)
        positions = mem.get("positions") or []
        picked = scheme_pick(text, positions)
        if picked:
            # «03763», «поз. 54154B» — клиент выбрал деталь по номеру на схеме узла, которую мы прислали
            i, code = picked
            p = positions[i]
            vs = [x for x in p.get("variants", []) if str(x.get("code") or "").upper() == code]
            res["positions"] = [dict(p, variants=vs, status="found", question="", schemes=[],
                                     note=f"Поз. {code} на схеме узла.")]
            return self._done(res, "ok", t0, mem, [i])
        chosen = filter_pick(text, positions)
        if chosen:
            # «Нет», «без АБС» на вопрос комплектации — варианты, чьё примечание каталога это и говорит (новых запросов нет)
            i, val = chosen
            p, q = positions[i], positions[i]["filter_q"]
            vs = [x for x in p.get("variants", []) if _spec_says(x.get("specs", ""), q["name"], val)]
            if vs:
                res["positions"] = [dict(p, variants=vs, status="found", question="", filter_q={}, schemes=[],
                                         note=f"По каталогу: {q['name']} — {val}.")]
                return self._done(res, "ok", t0, mem, [i])
        unsure = filter_unsure(text, positions)
        if unsure is not None:
            # «Не знаю», «хз», «а что это?»: выбор не сужаем — как проверить самому, и менеджеру на уточнение
            p = positions[unsure]
            name = (p.get("filter_q") or {}).get("name", "")
            res["positions"] = [dict(p, schemes=[], note=filter_help(name))]
            res["handoff"] = [f"{p['query']} — клиент не знает «{name}», номер уточнить по машине"]
            return self._done(res, "ok", t0, mem, [unsure])
        offered = D.asked_part(mem.get("asked", "")) if D.YES.fullmatch(text.strip()) else ""
        if offered and self._partish(offered, tree):
            # «Да» на «Топливный фильтр нужен?» — робот сам предложил деталь, её и ищем (с моделью и без)
            jobs = self.split([offered], tree)
            if jobs:
                res["positions"] = list(await asyncio.gather(*(self.position(v, tree, q, s) for q, s in jobs[:MAX_POSITIONS])))
                return self._done(res, "ok", t0, mem)
        plan = D.plan(text, mem, self.stop, tree, analogs, self.clean)
        if D.CORRECTION.search(text):
            plan = dict(plan, sure=False)   # клиент поправляет робота — разговор целиком разбирает модель
        if not plan["sure"] and use_llm and self.llm is not None and getattr(self.llm, "enabled", False):
            # Правила не уверены («Хорошо, давайте», длинное сообщение с новыми деталями) — спрашиваем модель
            parsed = await U.understand_reply(self.llm, text, mem, analogs)
            if parsed is not None:
                res["understood"] = {"by": "llm", "rules": plan["kind"], **parsed}
                hp = D.from_llm(parsed, mem, analogs, text)
                if hp["reply"] or hp["jobs"] or hp["handoff"]:
                    plan = hp
        if plan["kind"] == "chat" and not plan.get("reply") and not plan.get("jobs") and "?" not in text \
                and not D.STATUS.search(text) and not D.DEFER.search(text) and self._partish(text, tree):
            # На реплике с деталями не молчим (Skoda 13.10: «фильтр салонный воздушный масляный топливный» → пусто):
            # слово из прошлого ответа («масляный») не значит, что клиент говорит о показанном
            plan = dict(plan, kind="new")
        arts = A.find(text, limit=6)
        if arts and plan["kind"] not in ("pick", "answer"):
            return await self._follow_articles(res, text, arts, plan, mem, positions, v, tree, t0)
        if plan["kind"] == "llm":
            res["handoff"] = plan["handoff"]
            if plan["reply"]:
                res["reply"] = await self._reply(plan["reply"], positions, v)
            if not plan["jobs"]:
                if not plan["reply"]:
                    return self._done(res, "chat", t0, mem)
                return self._done(res, "answer" if plan["reply"]["kind"] == "answer" else "order", t0, mem)
            res["positions"] = list(await asyncio.gather(*(self.position(v, tree, q, s)
                                                           for q, s in self._all_around(plan["jobs"])[:MAX_POSITIONS])))
            return self._done(res, "ok", t0, mem)
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

    async def _follow_articles(self, res: dict, text: str, arts: list[str], plan: dict, mem: dict, positions: list[dict],
                               v: Vehicle, tree: TreeIndex, t0: float) -> dict:
        """Номера деталей в продолжении разговора («LEMFORDER 3394401 две штуки, CB0349 две штуки»): номер, который уже
        показывали, — это выбор (с количеством); новый номер проверяем как номер, а не ищем словами по каталогу
        (08.10: робот искал «LEMFORDER 3394401» как название, писал «не нашли», и на «все вместе» оформлять было нечего)."""
        lines = [x for x in re.split(r"[\n;]", text) if x.strip()] or [text]
        picks, new = [], []
        for a in arts:
            line = next((x for x in lines if A.norm(a) in A.norm(x)), text)
            hit = next(((p, x) for p in positions for x in D.all_offers(p)
                        if A.norm(str(x["offer"].get("number") or "")) == A.norm(a)), None)
            if hit:
                picks.append(D._pick(hit[0], hit[1], False, want_qty(line) or 0))
            else:
                new.append((a, line))
        found = [await self._article(v, tree, a, "", None) for a, _ in new]
        res["positions"] = [p | {"want_qty": want_qty(line)} for p, (_, line) in zip(found, new) if p is not None]
        # Остальные детали из той же реплики («…и ещё масляный фильтр») — как обычно, без строк с номерами
        jobs = [(q, s) for q, s in (plan.get("jobs") or [])
                if not any(A.norm(a) in A.norm(q) for a in arts) and self._partish(q, tree)]
        res["positions"] += list(await asyncio.gather(*(self.position(v, tree, q, s) for q, s in jobs[:MAX_POSITIONS])))
        if picks:
            res["reply"] = await self._reply({"kind": "order", "picks": picks, "unclear": []}, positions, v)
        if not res["positions"]:
            return self._done(res, "order" if picks else "chat", t0, mem)
        return self._done(res, "ok", t0, mem)

    def _word(self, s: str, tree: TreeIndex) -> bool:
        """Слово из словаря каталога этой машины (опечатку правим только в слово словаря: «масленный» — да,
        «приветствую», «подойдёт», «кое» — нет: known() правит слишком охотно)."""
        s = s.rstrip(".")
        if len(s) <= 2 or s in _GENERIC_HEADS:
            return False
        if s in tree.vocab or (len(s) >= 5 and tree.fix(s) in tree.vocab):
            return True
        # Слитно: «ДАТЧИККОЛЕНВАЛА» — словарь каталога делит на «датчик» + «коленвал»
        return any(x != s and x in tree.vocab for x in tree.split(s))

    def _partish(self, query: str, tree: TreeIndex) -> bool:
        """Есть ли во фразе хоть одно слово детали (не сторона, не «комплект»)."""
        return any(self._word(s, tree) and not T.side_of_word(s) for s in T.stems(T.expand(query), self.stop))

    def _chatty(self, chunks: list[str], tree: TreeIndex) -> bool:
        """Больше половины значимых слов — не из каталога, и их не меньше пяти: правилам такую фразу не доверяем."""
        st = [s for s in T.stems(T.expand(" ".join(chunks)), self.stop) if len(s) > 2 and not s[:1].isdigit()]
        return len(st) >= 5 and sum(1 for s in st if not self._word(s, tree)) * 2 > len(st)

    @staticmethod
    def _vague(q: list[str], query: str) -> str:
        """Запрос из одного общего слова («комплект», «подшипник») или одного признака («наружный») ничего не называет:
        групп с таким словом десятки, и любая выбранная — случайная. Возвращает вопрос клиенту или пустую строку."""
        # По словам запроса, а не по T.ADJ: тот пополняется из деревьев машин («ремен» попадает в прилагательные)
        words = [w for w in T.words(query) if T.stem(w) in q and re.fullmatch(r"[а-яё]+", w.lower())]
        nouns = [T.stem(w) for w in words if not D.adj_word(w)]
        if q and words and not nouns:
            return f"«{query}» — это только признак детали. Напишите, какая деталь нужна."
        # Фирма или артикул рядом («Peugeot-Citroen БАЧОК», «Zekkert комплект») деталь не называют: считаем только русские слова
        cyr = [s for s in q if re.fullmatch(r"[а-яё]+", s)]
        if len(nouns) == 1 and len(cyr) == 1 and _word_in(nouns[0], VAGUE_NOUNS):
            examples = next(v for k, v in VAGUE_NOUNS.items() if T.same(nouns[0], k))
            return f"«{query}» — это может быть разное. Уточните, пожалуйста, что именно нужно: {examples} или что-то другое?"
        return ""

    def _drop_lost(self, positions: list[dict], tree: TreeIndex | None = None) -> None:
        """Главное слово клиента не нашлось в каталоге и группа оценена слабо («кольцо подвесного» → уплотнения форсунки) или
        нашлась только часть слов («тормозной бачок» → бачок омывателя): показывать чужую деталь хуже, чем честно передать
        менеджеру. Модель уже посмотрела (арбитраж) и лучше не нашла."""
        for i, p in enumerate(positions):
            if p.get("kind", "") not in ("", "part") or p["status"] == "not_found" or "llm_part" in p or "qualifier_from" in p                     or p.get("via_units"):
                continue
            lost = self._score(p) < WEAK_SCORE and "уточним по каталогу отдельно" in (p.get("note") or "")
            if lost or self._partial(p, tree):
                positions[i] = dict(p, status="not_found", variants=[], groups=[], question="", note="",
                                    dropped=(p.get("note") or p.get("query") or "")[:200])

    @staticmethod
    def _score(p: dict) -> float:
        return max((g.get("score", 0) for g in p.get("groups", [])), default=0.0)

    def _weak(self, p: dict, tree: TreeIndex | None = None) -> bool:
        """Правила нашли частично: оценка низкая, главное слово потеряно («Кольцо» уточним отдельно) или в названии группы и
        вариантов нет части слов клиента («тормозной бачок» → бачок омывателя): такое проверяет модель, а не показываем сразу."""
        if p.get("kind", "") not in ("", "part") or p.get("via_units") or p.get("settled"):
            return False   # узел полного каталога выбран по структуре каталога: слабая оценка слов — не повод звать арбитра
        if p["status"] == "not_found" and not p.get("groups"):
            # Правила ничего не нашли («крышка омывателя фар», «кожух замка капота»): модель переведёт в название каталога
            return bool(T.stems(p.get("query", ""), self.stop))
        return self._score(p) < WEAK_SCORE or "уточним по каталогу отдельно" in (p.get("note") or "")             or self._partial(p, tree)

    def _coverage_info(self, p: dict, tree: TreeIndex | None = None) -> tuple[float, int]:
        """(доля значимых слов клиента, нашедшихся в названиях группы, её синонимах и вариантов; сколько слов всего)."""
        # «Комплект», «набор» — не слова детали: «Комплект передних рычагов» без них снимался как частичное совпадение
        words = [s for s in T.stems(T.expand(p.get("core") or p.get("query", "")), self.stop)
                 if not T.side_of_word(s) and not s.endswith(".") and s not in _GENERIC_HEADS]
        if tree is not None:
            words = [tree.fix(s) for s in words]   # «подшибник» = «подшипник»: опечатка — не потерянное слово
        if len(words) < 2 or p.get("status") == "not_found":
            return 1.0, len(words)
        have = [s for g in p.get("groups", []) for s in T.stems(g.get("name", ""), self.stop)]
        if tree is not None:
            for g in p.get("groups", []):
                grp = tree.groups.get(g.get("id"))
                have += [s for ph, _ in (grp.phrases if grp else []) for s in ph]
        have += [s for v in p.get("variants", [])[:6] for s in T.stems(v.get("name", ""), self.stop)]
        return sum(1 for w in words if any(T.same(w, h) for h in have)) / len(words), len(words)

    def _coverage(self, p: dict, tree: TreeIndex | None = None) -> float:
        return self._coverage_info(p, tree)[0]

    def _partial(self, p: dict, tree: TreeIndex | None = None) -> bool:
        """Нашлась лишь часть слов клиента. В короткой фразе («тормозной бачок», 2–3 слова) не хватает любого слова — это
        другая деталь; в длинной допускаем потерю до половины (лишние слова, марка, год)."""
        ratio, n = self._coverage_info(p, tree)
        return ratio < (0.67 if n <= 3 else 0.5)

    async def _arbitrate(self, v: Vehicle, tree: TreeIndex, positions: list[dict]) -> None:
        """Слабый результат правил проверяем моделью: «кольцо подвесного» правила вели в уплотнения форсунки, а
        «рем комплект для ручника супарта» — в цепь ГРМ. Модель переводит жаргон в название детали каталога, мы ищем
        по нему тем же поиском; берём результат модели, только если он заметно лучше (оценка группы выше)."""
        if not self.llm or not getattr(self.llm, "enabled", False):
            return
        emb = EMB.get()
        handled = await self._arbitrate_emb(emb, v, tree, positions) if emb is not None else set()
        weak = [i for i, p in enumerate(positions)
                if i not in handled and not p.get("parts") and self._weak(p, tree)][:MAX_ARBITER]
        if not weak:
            return
        parsed = await asyncio.gather(*(U.understand(self.llm, positions[i]["query"]) for i in weak),
                                      return_exceptions=True)
        for i, pr in zip(weak, parsed):
            if not isinstance(pr, dict) or not pr.get("positions"):
                continue
            first, old = pr["positions"][0], positions[i]
            if first.get("kind") not in ("part", "") or first["part"].lower() == old["query"].lower():
                continue
            side = T.Side(first["axis"], first["lr"] if first["lr"] in ("left", "right") else "")
            try:
                alt = await self.position(v, tree, first["part"], side)
            except Exception:
                continue
            found = alt["status"] in ("found", "choose")
            if old["status"] == "not_found" and not old.get("groups"):
                better = found and self._score(alt) >= WEAK_SCORE - 0.05   # из «ничего» берём только уверенную находку
            else:
                better = found and self._score(alt) >= self._score(old) + 0.1
            if better or (alt["status"] in ("found", "choose") and "уточним по каталогу отдельно" in (old.get("note") or "")
                          and self._score(alt) >= self._score(old)):
                # Слова клиента остаются в заголовке и памяти; что искали по версии модели — в llm_part
                positions[i] = alt | {"query": old["query"], "llm_part": first["part"], "was_score": self._score(old)}

    async def _arbitrate_emb(self, emb, v: Vehicle, tree: TreeIndex, positions: list[dict]) -> set[int]:
        """Арбитр с кандидатами «3 от правил + 3 от эмбеддингов» (emb.py). Уверенные правила, чья группа есть в пятёрке
        эмбеддингов, не трогаем; остальное выбирает модель. «Не деталь / уточнить / менеджеру» — позицию не показываем,
        её получит менеджер. Возвращает номера разобранных позиций; прочие слабые идут старым путём."""
        # Детали узла по позициям схемы (Audi: все рычаги) — структура каталога надёжнее мнения модели: не трогаем
        idx = [i for i, p in enumerate(positions)
               if p.get("kind", "") in ("", "part") and T.stems(p.get("query", ""), self.stop) and not p.get("parts")
               and not p.get("via_units") and not p.get("settled")]
        vecs = await emb.embed([positions[i]["query"] for i in idx]) if idx else None
        if not vecs:
            return set()
        names = [g.name for g in tree.groups.values()]
        todo = []
        feats: dict[int, tuple] = {}   # для «замка»: вектор фразы, оценка правил, близость лучшей группы эмбеддингов
        for i, vec in zip(idx, vecs):
            p = positions[i]
            ranked = emb.top(vec, names, 5)
            top = [n for n, _ in ranked]
            rules = [g["name"] for g in p.get("groups", [])[:3]]
            if not top or (rules and rules[0] in top and self._score(p) >= SURE_SCORE and not self._weak(p, tree)):
                continue
            feats[i] = (vec, self._score(p), ranked[0][1])
            todo.append((i, list(dict.fromkeys(rules + top[:3]))))
        todo = todo[:MAX_ARBITER]
        picks = await asyncio.gather(*(U.pick_group(self.llm, positions[i]["query"], c) for i, c in todo),
                                     return_exceptions=True)
        done = set()
        for (i, cands), pick in zip(todo, picks):
            old = positions[i]
            if not isinstance(pick, str) or not pick:
                continue
            gated = False
            if pick not in U.PICK_CODES:
                # «Замок»: арбитр выбрал группу, а обученная на эталоне модель уверенно говорит «это не деталь» — молчим
                # («других баков нет» → «Бак топливный»)
                verdict = emb.gate(*feats[i]) if i in feats and hasattr(emb, "gate") else None
                if verdict and verdict[1] >= GATE_MARGIN:
                    pick, gated = verdict[0], True
            if pick in U.PICK_CODES:
                positions[i] = dict(old, status="not_found", variants=[], groups=[], question="", note="",
                                    dropped=(old.get("query") or "")[:200], arbiter=pick, **({"gate": True} if gated else {}))
                done.add(i)
                continue
            if any(g.get("name") == pick for g in old.get("groups") or []):
                done.add(i)   # модель подтвердила одну из групп правил
                continue
            if self._score(old) >= SURE_SCORE and not self._weak(old, tree):
                # Уверенные правила модель может подтвердить или снять («т-банк» → глушитель), но не заменить: на «лямбда»
                # (Датчик кислорода, 1.0) она выбирала «Электронику двигателя, датчики» — и в ответ попадал датчик детонации
                done.add(i)
                continue
            side = T.Side(old["side"].get("axis", ""), old["side"].get("lr", ""))
            try:
                alt = await self.position(v, tree, pick, side)
            except Exception:
                continue
            if alt["status"] in ("found", "choose"):
                positions[i] = alt | {"query": old["query"], "llm_part": pick, "was_score": self._score(old)}
                done.add(i)
        return done

    def _split_brands(self, items: list[tuple[str, T.Side]], v: Vehicle) -> tuple[list, list[dict]]:
        """Фирмы из текста позиций: (позиции без слов фирмы, что спросить у поставщиков). Фирма машины («Ниссан»)
        и слово, из которого не осталось бы детали, — не запрос фирмы."""
        out, asks = [], []
        car = AB.get().key(v.brand)
        for i, (q, side) in enumerate(items):
            for w, key in D.brand_words(q):
                if len(w) < 3:
                    continue   # «GE» — код мотора Mazda, а не фирма
                rest = re.sub(rf"(?<![\wа-яё]){re.escape(w)}(?![\wа-яё])", " ", q, flags=re.I)
                rest = re.sub(r"\s+", " ", rest).strip(" ,;")
                if key == car or not T.stems(rest, self.stop):
                    continue
                q = rest
                asks.append({"position": i, "brand": key, "word": w})
            out.append((q, side))
        return out, asks

    async def _reply(self, r: dict, positions: list[dict], v: Vehicle) -> dict:
        """Ответ про показанные предложения — в виде, который можно сохранить и пересобрать в текст."""
        def item(kind: str, p: dict, x: dict | None, **extra) -> dict:
            axis, lr = D.shown_side(p, x["var"]) if x else ("", "")
            out = {"type": kind, "query": p["query"], "name": x["var"]["name"] if x else "",
                   "who": x["who"] if x else "", "offer": x["offer"] if x else None, "axis": axis, "lr": lr}
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
                    # Найденное по вопросу — в память позиции: «Беру беру» следующей репликой выбирает его (Mercedes, 12.10)
                    o = var.get("offers") if isinstance(var.get("offers"), dict) else None
                    if o is None:
                        o = var["offers"] = {"original": None, "analogs": [], "stats": {}}
                    if not any(D.same_offer(x, got["offer"]) for x in D.all_offers(p)):
                        o.setdefault("asked", []).append(got["offer"])
                    break
            answers.append(got or item("no_brand", p, None, word=a["word"]))
        picks = [{"type": "pick", **x} for x in r.get("picks", [])]
        return {"kind": r["kind"], "answers": answers, "picks": picks, "unclear": r.get("unclear", []),
                "clarify": r.get("clarify", "")}

    # ---------- позиции ----------

    def _share_head(self, pieces: list[str], tree: TreeIndex) -> list[str]:
        """«Сальник коленвала и распредвала» → «сальник распредвала»: одно слово в родительном падеже берёт главное слово
        соседа «деталь + чего» (Mercedes C200D, судья 11.10: робот показывал сам распредвал). Только если в каталоге
        машины такое сочетание и правда есть: «сальник коленвала и помпа» — помпа отдельно."""
        out = list(pieces)
        for i in range(1, len(pieces)):
            words, prev = T.words(pieces[i]), T.words(out[i - 1])
            st, pst = T.stems(pieces[i], self.stop), T.stems(out[i - 1], self.stop)
            if len(words) != 1 or len(st) != 1 or st[0] in T.ADJ or len(pst) < 2 or T.stem(words[0]) == words[0]:
                continue   # «распредвал» в именительном — сама деталь
            head = T.head(pst)
            deps = [w for w in prev if T.stem(w) in pst and T.stem(w) != head and T.stem(w) not in T.ADJ]
            if not head or not deps or T.stem(deps[-1]) == deps[-1]:
                continue   # у соседа зависимое слово тоже должно быть в родительном: «коленвала»
            hw = next((w for w in prev if T.stem(w) == head), head)
            cand = f"{hw} {pieces[i].strip()}"
            rank = tree.rank(T.stems(cand, self.stop), T.side(cand))
            if rank and rank[0][1] >= 0.8 and any(T.same(head, t) for ph, _ in rank[0][0].phrases for t in ph):
                out[i] = cand
        return out

    def _adj_list(self, chunk: str, tree: TreeIndex) -> list[str]:
        """«фильтр салонный воздушный маслянный топливный» без запятых (Skoda 13.10 — робот молчал): одно существительное и
        несколько прилагательных, и каждое с ним — своя группа каталога → отдельные позиции. «Шаровая опора нижняя передняя» —
        признаки одной детали (группа одна), «колодки передние задние» — стороны (_both_axes): не делим."""
        words = [w for w in T.words(chunk) if T.stem(w) not in self.stop]
        nouns = [w for w in words if not D.adj_word(w)]
        adjs = [w for w in words if D.adj_word(w) and not T.side_of_word(T.stem(w))]
        if len(nouns) != 1 or len(adjs) < 2 or len(adjs) + 1 != len(words):
            return []
        tops = []
        for a in adjs:
            r = tree.rank(T.stems(T.expand(f"{a} {nouns[0]}"), self.stop), T.Side())
            if not r or r[0][1] < 0.8:
                return []
            tops.append(r[0][0].id)
        return [f"{nouns[0]} {a}" for a in adjs] if len(set(tops)) == len(adjs) else []

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
            pieces = self._share_head(share_noun(T.split_pieces(chunk), self.stop), tree)
            if len(pieces) == 1:
                listed = self._adj_list(chunk, tree)
                if listed:
                    out += [(self.clean(q), whole) for q in listed]
                    continue
                both = self._both_axes(chunk)
                if both:
                    out += both   # «колодки передние задние» — две позиции, а не вопрос «передние или задние?»
                elif T.stems(chunk, self.stop):
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

    def _both_axes(self, chunk: str) -> list[tuple[str, T.Side]]:
        """Клиент назвал обе оси в одной фразе («колодки передние задние», «перед и зад амортизаторы»): передние и задние
        — две позиции. Пусто, если оси названы не обе или деталь без них не называется."""
        if not (T.FRONT.search(chunk) and T.REAR.search(chunk)):
            return []
        base = re.sub(r"\s+", " ", T.REAR.sub(" ", T.FRONT.sub(" ", chunk))).strip(" ,.")
        base = re.sub(r"(?:^|\s)(?:и|а|или|а также|на)(?=\s|$)", " ", base).strip()
        if not T.stems(base, self.stop):
            return []
        return [(self.clean(f"{base} передние"), T.Side("front", "")), (self.clean(f"{base} задние"), T.Side("rear", ""))]

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
        query = _PRICE_TAIL.sub("", query)   # «А шаровые сколько стоят?» → «шаровые»
        query = _PCS_GLUE.sub(lambda m: m.group(1) + "шт", query)   # «4 шт» в конце не срезать как стоп-слово: это количество
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
        return any(all(any(T.same(w, s) for s in q) for w in nc) and T.same(_main(q), _main(nc))
                   for nc in self.not_catalog)

    async def position(self, v: Vehicle, tree: TreeIndex, query: str, want: T.Side, kind: str = "") -> dict:
        T.mark_adj(query)   # запрос позиции бывает и от модели («салонный фильтр») — его прилагательные тоже в заявке
        kit = _KIT_OF.match(query)
        if kit:
            # Ищем сами детали («передних рычагов»), в заголовке — слова клиента; «все» решит node_parts/wants_all
            pos = await self.position(v, tree, kit.group(1), want, kind)
            return pos | {"query": query}
        only = T.stems(query, self.stop)
        if len(only) == 1 and only[0] in _ADJ_DEFAULT:
            # «Нужен топливный» — почти всегда фильтр: ищем его и говорим, как поняли (12.10: «Да» на вопрос «фильтр?» уходило в никуда)
            full = _ADJ_DEFAULT[only[0]]
            word = next((w for w in T.words(query) if T.stem(w) == only[0]), only[0])
            pos = await self.position(v, tree, full, want, kind)
            if pos["status"] in ("found", "choose"):
                return pos | {"query": full[:1].upper() + full[1:], "adj_default": word.lower(),
                              "note": f"Поняли «{word.lower()}» как «{full}»; если нужна другая деталь — напишите."}
        qs = T.stems(T.expand(query), self.stop)
        if _SUB_Q.search(query) and any(T.same(T.head(qs) or "", b) for b in _BUSH + _SUB_MOUNT[:2]):
            # «Сайлентблок подрамника»: быстрые группы дают сайлентблок рычага — ищем в узлах подрамника. Там нет, и группа
            # подрамника («Вспомогательная рама») ничего не дала — честно менеджеру, а не сайлентблок рычага
            pos = await self._subframe(v, query, want)
            if pos is None:
                pos = await self._position(v, tree, query, want, kind)
                named = " ".join([g["name"] for g in pos.get("groups", [])] +
                                 [f"{x.get('unit', '')} {x.get('name', '')}" for x in pos.get("variants", [])])
                if not _SUB_UNIT.search(named) and not re.search(r"подрамн|вспомогательн", named, re.I):
                    # settled: решено по устройству каталога — арбитр и эмбеддинги снова привели бы к сайлентблоку рычага
                    pos = dict(pos, status="not_found", variants=[], groups=[], question="", note="", dropped="подрамник",
                               settled=True)
            await self._confirm_replacements(v, pos)
            return pos
        if _SPARK.search(query) and not any("свеч" in g.name.lower() and "зажиган" in g.name.lower()
                                            for g in tree.groups.values()):
            # «Свечи зажигания» на дизеле (Mercedes E 290 Turbodiesel, 12.10): группы свечей зажигания в каталоге нет,
            # правила находили «Свечи накаливания» по слову «свечи» и тут же выбрасывали за потерянное «зажигания» —
            # «передано менеджеру». Клиенту нужны свечи накаливания: показываем их и говорим почему. У бензиновой
            # машины группы накаливания нет (strict в podbor_rules) — останется «не нашли»
            alt = await self._position(v, tree, "свечи накаливания", want, kind)
            if alt["status"] != "not_found":
                alt.update(query="Свечи накаливания", settled=True,
                           note="У вашей машины дизельный двигатель — свечей зажигания у него нет, "
                                "вместо них свечи накаливания.")
                await self._confirm_replacements(v, alt)
                return alt
        pos = await self._position(v, tree, query, want, kind)
        if pos["status"] == "not_found" and want.axis == "rear" and _DISC.search(query):
            # «Диски задние» на машине с барабанами: дисков в каталоге нет — даём барабаны и говорим об этом
            alt = await self._position(v, tree, "барабан тормозной", want, kind)
            if alt["status"] != "not_found":
                alt["query"] = "Барабаны тормозные задние"
                alt["note"] = ("Сзади у вашей машины барабанные тормоза, тормозных дисков там нет — "
                               "вместо дисков стоят барабаны (колодки к ним — барабанные).")
                await self._confirm_replacements(v, alt)
                return alt
        if pos["status"] in ("found", "choose") and _LOST_NOTE.search(pos.get("note") or "") and pos["kind"] in ("", "part"):
            # Нашлось только уточнение («вакуумный насос»), а главного слова («шланг») там нет: деталь ищем внутри узла по
            # уточнению — «Трубка вакуумного насоса», а не сам насос (12.10, L200)
            pos = await self._by_unit_part(v, query, want, kind) or await self._by_units_llm(v, query, want, kind) or pos
        if pos["status"] == "not_found" and not pos["groups"] and not pos["note"] and pos["kind"] in ("", "part"):
            # Быстрые группы Laximo (около 680) — урезанный список: подрамника, поперечины и многого другого там нет.
            # Ищем по узлам полного каталога машины: по словам, а не нашлось — узлы выбирает модель
            pos = await self._by_units(v, query, want, kind) or pos
        if pos["status"] == "not_found" and pos["kind"] in ("", "part") and not pos.get("via_units") \
                and not self.outside(qs) and not self._vague(qs, query):
            # Не нашли ни группами, ни словами по узлам (бывает и с пояснением «шланг уточним отдельно»): узлы выбирает модель
            pos = await self._by_units_llm(v, query, want, kind) or pos
        out = await self._qualifier(v, tree, query, want, kind, pos) or pos
        miss = out.get("head_miss") or ""
        glued = T.stem(miss.lower())
        # «ДАТЧИККОЛЕНВАЛА» слитно: «датчик» из найденного «Импульсный датчик коленчатого вала» — часть слова клиента
        inside = any(len(s) >= 4 and s in glued for x in out.get("variants", []) for s in T.stems(x.get("name", ""), self.stop))
        if out["status"] in ("found", "choose") and miss and not out.get("via_units") and not inside \
                and not (re.fullmatch(r"[а-яё]+", miss.lower()) and T._ADJ_END.search(miss.lower())):
            # Главного слова клиента нет ни в одной найденной детали, и узлы полного каталога его не нашли: «соленоиды АКПП»
            # → гидротрансформатор, «сальник распредвала» → распредвал, «барабан» → рычаг ручника (перепрогон 12.10: 11 из 32
            # «не та деталь»). Соседа не показываем — менеджеру. «Вентилируемый» — признак, а не деталь: такое оставляем
            out = dict(out, status="not_found", variants=[], groups=[], question="", note="",
                       dropped=(out.get("note") or query)[:200])
        await self._confirm_replacements(v, out)
        return out

    async def _by_units(self, v: Vehicle, query: str, want: T.Side, kind: str) -> dict | None:
        """Тот же поиск по узлам полного каталога вместо быстрых групп. Берём только уверенное совпадение с названием узла."""
        try:
            ut = await self.catalog.unit_tree(v)
            if ut is None or any(T.same(T.head_text(T.expand(query), self.stop) or "", b) for b in _BUSH):
                return None   # сайлентблок узла: чей именно (рычага, подрамника) по узлу не понять — менеджеру
            alt = await self._position(v, ut, query, want, kind, unit_mode=True)
        except Exception:   # noqa: BLE001 — запасной путь: ошибка = ничего не нашли, как раньше
            return None
        if not alt or alt["status"] not in ("found", "choose") or not alt["variants"]                 or max((g["score"] for g in alt["groups"]), default=0.0) < UNIT_SCORE:
            return await self._by_unit_part(v, query, want, kind)
        said = {w[:5] for w in T.stems(T.expand(query), self.stop)}
        named = " ".join([g["name"] for g in alt["groups"][:2]] + [x["name"] for x in alt["variants"][:3]]).lower()
        if any(m in named and m not in said for m in _ID_MODS):
            return None   # «цилиндры задние» привели в «Главный тормозной цилиндр»: слово, меняющее деталь, клиент не называл (12.10)
        alt["via_units"] = True
        units = ", ".join(dict.fromkeys(g["name"] for g in alt["groups"][:2]))
        alt["note"] = alt.get("note") or f"Нашли в полном каталоге машины (узел «{units}»), в быстрых группах такой детали нет."
        return alt

    async def _by_unit_part(self, v: Vehicle, query: str, want: T.Side, kind: str) -> dict | None:
        """«Шланг вакуумного насоса»: узел называется «Проводка двигателя — генератор и вакуумный насос» и слова «шланг» в
        нём нет — по всей фразе он не находится. Узел ищем по уточнению («вакуумного насоса»), а деталь в нём — по главному
        слову и его родне (шланг = трубка = патрубок). Показываем только то, что названо и узлом, и деталью (12.10: вместо
        трубки насоса шёл «Патрубок воздушного фильтра»)."""
        try:
            ut = await self.catalog.unit_tree(v)
            q = T.stems(T.expand(query), self.stop)
            head = T.head(q) or ""
            qual = [w for w in q if w != head and not T.side_of_word(w) and not _word_in(w, _GENERIC_HEADS)]
            if ut is None or not head or not qual:
                return None
            ranked = [(g, sc) for g, sc in ut.rank(qual, want) if sc >= UNIT_SCORE][:2]
            if not ranked:
                return None
            aliases = [head] + _PART_ALIASES.get(head, []) + _HOSE_KIN.get(head, [])
            pool = {}
            for g, _ in ranked:
                for d in await self.catalog.details(v, g.id, True):
                    st = T.stems(d.name, self.stop)
                    if any(T.same(a, s) for a in aliases for s in st) and all(any(T.same(w, s) for s in st) for w in qual[:1]):
                        pool.setdefault(_key(d.oem), d)
            if not pool or len(pool) > 4:
                return None
            groups = [{"id": g.id, "name": g.name, "path": g.path, "score": round(sc, 2)} for g, sc in ranked]
            return await self._pos_from_details(v, query, want, list(pool.values()), groups)
        except Exception:   # noqa: BLE001 — запасной путь: ошибка = ничего не нашли, как раньше
            return None

    async def _subframe(self, v: Vehicle, query: str, want: T.Side) -> dict | None:
        """«Сайлентблок (заднего) подрамника»: узлы подрамника машины под любым его названием → сайлентблоки и опоры в них,
        кроме чужих (рычага, стабилизатора, КП). Своих нет (Kia, Mitsubishi, Mazda: подрамник продаётся в сборе) —
        сайлентблоки из кроссов номера самого подрамника. Узла подрамника у машины нет — None."""
        try:
            ut = await self.catalog.unit_tree(v)
            if ut is None:
                return None
            units = [g for g in ut.groups.values() if _SUB_UNIT.search(g.name) and not _SUB_NOT.search(g.name)]
            mounts: dict[str, tuple[Detail, str]] = {}
            frames: dict[str, tuple[Detail, str]] = {}
            for g in units[:6]:
                for d in await self.catalog.details(v, g.id, True):
                    name = _ru_words(d.name)
                    head = T.head_text(name, self.stop) or ""
                    axis = _sub_axis(name) or _sub_axis(g.name)
                    if any(T.same(head, p) for p in _SUB_PART):
                        frames.setdefault(_key(d.oem), (d, axis))
                    elif (is_bushing(name) or any(T.same(head, m) for m in _SUB_MOUNT)) and not _SUB_OTHER.search(name) \
                            and not (_SUB_OTHER.search(g.name) and not is_bushing(name)):
                        mounts.setdefault(_key(d.oem), (d, axis))
            if want.axis:
                # «Заднего подрамника» — только то, что каталог прямо называет задним: «Подрамник» без стороны у Audi и
                # «Подрамник передней подвески» у Kia — передние, клиенту нужен другой
                mounts = {k: x for k, x in mounts.items() if x[1] == want.axis}
                frames = {k: x for k, x in frames.items() if x[1] == want.axis}
            names = ", ".join(dict.fromkeys(g.name for g in units[:2]))
            groups = [{"id": g.id, "name": g.name, "path": g.path, "score": UNIT_SCORE} for g in units[:2]]
            pos = {"query": query, "side": {"axis": want.axis, "lr": want.lr}, "status": "found", "groups": groups,
                   "variants": [], "question": "", "kind": "part", "via_units": True,
                   "note": f"Нашли в полном каталоге машины (узел «{names}»), в быстрых группах подрамника нет."}
            if 0 < len(mounts) <= 8:
                cands = [Candidate(d, 0.8, 0.8, True, T.Side(ax, ""), "каталог" if ax else "") for d, ax in mounts.values()]
                kept = await self._check_sides(v, cands, T.Side("", want.lr))
                if kept:
                    pos["variants"] = [self._variant(c, v, alt=i > 0) for i, c in enumerate(kept)]
            if not pos["variants"]:
                host = ([T.stem("подрамник"), T.stem("поперечина"), T.stem("балка"), T.stem("траверса")],
                        ("подрамник", "подрамника", "подрамник"))
                for d, _ in list(frames.values())[:3]:
                    found = await self._bushings_by_host(v, [d], T.Side(), host)
                    if found:
                        c, note = found
                        pos["variants"] = [self._variant(c, v, alt=False)]
                        pos["variants"][0]["via"] = c.d.oem
                        pos["note"] = note
                        break
            if not pos["variants"]:
                return None
            for var in pos["variants"]:
                R.annotate(var, [query])
            return pos
        except Exception:   # noqa: BLE001 — запасной путь: ошибка = не нашли, передаём менеджеру
            return None

    async def _pos_from_details(self, v: Vehicle, query: str, want: T.Side, details: list[Detail],
                                groups: list[dict]) -> dict | None:
        """Позиция из деталей, найденных в узлах полного каталога: цены, стороны и варианты — тем же кодом."""
        cands = [Candidate(d, 0.8, 0.8, True, want, "каталог") for d in details]
        kept = await self._check_sides(v, cands, want)
        if not kept:
            return None
        units = ", ".join(dict.fromkeys(g["name"] for g in groups[:2]))
        pos = {"query": query, "side": {"axis": want.axis, "lr": want.lr}, "status": "found", "groups": groups,
               "variants": [self._variant(c, v, alt=i > 0) for i, c in enumerate(kept)], "question": "", "kind": "part",
               "via_units": True,
               "note": f"Нашли в полном каталоге машины (узел «{units}»), в быстрых группах такой детали нет."}
        for var in pos["variants"]:
            R.annotate(var, [query])
        return pos

    async def _by_units_llm(self, v: Vehicle, query: str, want: T.Side, kind: str) -> dict | None:
        """Ни быстрые группы, ни слова не нашли узел. Модель по знанию устройства машины выбирает до трёх узлов из
        списка ЭТОЙ машины (выдумать узел не может), деталь в них ищем по главному слову, его родне и по-английски
        (HOSE = шланг), уточнение — в названии детали или узла. Деталей больше шести — это уже не ответ, а гадание."""
        if not self.llm or not getattr(self.llm, "enabled", False):
            return None
        try:
            ut = await self.catalog.unit_tree(v)
            q = T.stems(T.expand(query), self.stop)
            head = T.head(q) or ""
            if ut is None or not head or any(T.same(head, b) for b in _BUSH):
                return None
            ids = self.catalog._unit_ids.get(self.catalog.tree_key(v) + "-u") or {}
            names = list(dict.fromkeys(u["name"] for u in ids.values()))
            if sum(len(n) for n in names) > 24000:   # BMW: больше тысячи узлов — сначала те, где есть слово фразы
                names = sorted(names, key=lambda n: -sum(1 for w in q for s in T.stems(_ru_words(n), self.stop)
                                                          if T.same(w, s)))[:600]
            picked = await U.pick_units(self.llm, query, names)
            chosen = {names[i] for i in picked}
            gids = [gid for gid, u in ids.items() if u["name"] in chosen][:6]
            if not gids:
                return None
            aliases = [head] + _PART_ALIASES.get(head, []) + _HOSE_KIN.get(head, [])
            qual = [w for w in q if w != head and not T.side_of_word(w) and not _word_in(w, _GENERIC_HEADS)]
            first = {gid for gid, u in ids.items() if picked and u["name"] == names[picked[0]]}
            scored: dict[str, tuple[float, Detail]] = {}
            for gid in gids:
                for d in await self.catalog.details(v, gid, True):
                    st = T.stems(_ru_words(d.name), self.stop)
                    if not any(T.same(a, x) for a in aliases for x in st) \
                            or any(m in d.name.lower() and m not in {w[:5] for w in q} for m in _ID_MODS):
                        continue
                    unit_st = T.stems(_ru_words(d.unit), self.stop)
                    in_name = sum(1 for w in qual if any(T.same(w, x) for x in st))
                    in_unit = sum(1 for w in qual if any(T.same(w, x) for x in unit_st))
                    if qual and not in_name and not in_unit:
                        continue
                    # «HOSE,BRAKE BOOSTER VACUUM» (уточнение в самом названии) важнее «ТРУБКИ ТОРМОЗНОЙ» из соседнего узла
                    score = 2 * in_name + in_unit + any(T.same(head, x) for x in st) + 0.5 * (gid in first)
                    k = _key(d.oem)
                    if k not in scored or scored[k][0] < score:
                        scored[k] = (score, d)
            best = max((sc for sc, _ in scored.values()), default=0)
            pool = {k: d for k, (sc, d) in scored.items() if sc == best}
            if not pool or len(pool) > 6:
                return None
            groups = [{"id": gid, "name": ids[gid]["name"], "path": ids[gid].get("cat", ""), "score": UNIT_SCORE}
                      for gid in gids if any(d.group_id == gid for d in pool.values())]
            pos = await self._pos_from_details(v, query, want, list(pool.values()), groups)
            if pos:
                pos["via_llm_units"] = True
            return pos
        except Exception:   # noqa: BLE001 — запасной путь: ошибка = ничего не нашли, как раньше
            return None

    async def _qualifier(self, v: Vehicle, tree: TreeIndex, query: str, want: T.Side, kind: str, pos: dict) -> dict | None:
        """«Подшипник маховика» → группа «Подшипник коленвала»: слово-уточнение (маховик) в группе не нашлось, это другая
        деталь. Ищем по уточнению («маховик»), берём из найденного узла то, что называет деталь (подшипник). Если у общей
        группы («Ремкомплект», «Комплект») уточнения нет и узла по нему тоже — показывать случайную группу нельзя."""
        if pos.get("kind", "") not in ("", "part") or pos["status"] not in ("found", "choose") or not pos.get("groups"):
            return None
        g = tree.groups.get(pos["groups"][0]["id"])
        q = T.stems(T.expand(query), self.stop)
        lost = lost_words(q, tree, g, self.stop) if g else []
        if not lost:
            return None
        head = T.head(tree.known(q) or q) or ""
        generic = _word_in(head, _GENERIC_HEADS) or _word_in(head, VAGUE_NOUNS)
        # Одни общие слова («прокладки») узла не назовут: ищем по конкретному уточнению
        specific = [w for w in lost if not _word_in(w, VAGUE_NOUNS) and not _word_in(w, _GENERIC_HEADS)]
        alt = {"status": "not_found", "variants": [], "groups": []}
        if specific:
            try:
                alt = await self._position(v, tree, " ".join(specific), want, "")
            except Exception:
                pass
        # Узел должен называть ВСЕ слова-уточнения («турбокомпрессор» и «прокладки»), а не одно из них
        alt_group = tree.groups.get(alt["groups"][0]["id"]) if alt.get("groups") else None
        alt_ok = alt["status"] in ("found", "choose") and alt.get("variants") and self._score(alt) >= 0.6             and alt_group is not None and not lost_words(specific, tree, alt_group, self.stop)
        if alt_ok:
            aliases = [head] + _PART_ALIASES.get(head, [])
            named = [x for x in alt["variants"] if any(T.same(a, s) for a in aliases for s in T.stems(x["name"], self.stop))]
            # «Ремкомплект прокладок турбокомпрессора»: узел «Турбокомпрессор» нашёлся, но в нём — сама турбина. Общие
            # слова-детали («прокладки») должны быть в названии варианта, иначе это не набор, а весь узел (12.10, Fiesta)
            loose = [w for w in lost if _word_in(w, VAGUE_NOUNS) and not _word_in(w, _GENERIC_HEADS) and not T.same(w, head)]
            whole = [x for x in alt["variants"] if all(any(T.same(w, s) for s in T.stems(x["name"], self.stop)) for w in loose)]
            # «Комплект/ремкомплект X» — деталь и есть узел X; «датчик/подшипник X» — нужны варианты, где названа сама деталь
            if named or (_word_in(head, _GENERIC_HEADS) and whole):
                named = named or whole
                alt = dict(alt, variants=named or alt["variants"], query=query, qualifier_from=g.name,
                           note=f"Нашли узел «{alt['groups'][0]['name']}»: {query.lower()} уточним по нему.")
                return alt
        if generic:
            # Общая группа и уточнение без своего узла: показывать «Ремкомплект» вообще — случайность
            return dict(pos, status="not_found", variants=[], groups=[], question="", note="", dropped=g.name)
        return None

    @staticmethod
    def _all_around(items: list[tuple[str, T.Side]], around: bool = False) -> list[tuple[str, T.Side]]:
        """«Диски и колодки в круг» → передние и задние отдельно: сзади бывают барабаны, и это надо показать.
        around — «в круг» сказано про всё сообщение: тормозные детали без стороны тоже делим на перед и зад."""
        out = []
        for q, side in items:
            if (_AROUND.search(q) or (around and _BRAKE.search(q))) and not side.axis:
                base = re.sub(r"\s+", " ", _AROUND.sub(" ", q)).strip(" ,")
                if T.stems(base, frozenset()):
                    out += [(base, T.Side("front", "")), (base, T.Side("rear", ""))]
                    continue
            out.append((q, side))
        return out

    async def _position(self, v: Vehicle, tree: TreeIndex, query: str, want: T.Side, kind: str = "",
                        unit_mode: bool = False) -> dict:
        q = T.stems(T.expand(query), self.stop)
        kind = kind or kind_of(q)
        pos: dict[str, Any] = {"query": query, "side": {"axis": want.axis, "lr": want.lr}, "status": "not_found",
                               "groups": [], "variants": [], "question": "", "note": "", "kind": kind}
        if kind in ("fluid", "battery", "tire", "tool", "chemistry", "accessory"):
            # Не каталог машины: подберёт менеджер — так и пишем. Масла и жидкости — тоже (решение 13.10): допусков и
            # объёма заправки у нас нет, между двумя маслами каталога честно выбрать нечем, только аналогами
            pos["note"] = KIND_NOTE.get(kind, "")
            return pos
        if self.outside(q):
            pos["note"] = "Это не деталь каталога автомобиля — подберём по названию."
            return pos
        vague = self._vague(q, query)
        if vague:
            pos["note"] = vague   # «Комплект», «подшипник», «наружный»: гадать группу нельзя — спрашиваем, какая деталь
            return pos
        ranked = tree.rank(q, want)
        if not ranked or ranked[0][1] < 0.3:
            return pos
        if kind == "fluid" and not any(T.same(T.head_text(ranked[0][0].name, self.stop) or "", T.stem(w))
                                       for w in _KIND_WORDS["fluid"]):
            return pos   # «масло моторное» привело в «Датчик давления масла» — это не масло: подберём по допуску
        best = ranked[0][1]
        groups = [(g, s) for g, s in ranked[:3] if s >= max(0.3, best - 0.2)]
        pos["groups"] = [{"id": g.id, "name": g.name, "path": g.path, "score": round(s, 2)} for g, s in groups]
        lists = await asyncio.gather(*(self.catalog.details(v, g.id, False) for g, _ in groups),
                                     return_exceptions=True)
        pool = [d for x in lists if isinstance(x, list) for d in x]
        members = {(d.group_id, _key(d.oem)) for d in pool}
        cands = self._candidates(tree, q, want, groups, pool, members, unit_mode=unit_mode)
        if not cands:
            # В составе группы нужной стороны нет — ищем по словам во всех узлах этих групп
            lists = await asyncio.gather(*(self.catalog.details(v, g.id, True) for g, _ in groups),
                                         return_exceptions=True)
            pool += [d for x in lists if isinstance(x, list) for d in x]
            cands = self._candidates(tree, q, want, groups, pool, members, unit_mode=unit_mode)
        head_miss = ""
        if cands:
            # «Тормозные барабаны» у Nissan: отдельной группы нет, а «барабанные» в названии группы колодок
            # совпало с «барабаны». Главного слова клиента нет ни в одном найденном названии — ищем его
            # по всем узлам этих групп и берём, если нашлось
            known = tree.known(q) or q
            raw_head = T.head(q)
            # Главного слова клиента нет в словаре каталога: «сайлентблок переднего рычага» у Toyota привёл
            # в «Рычаг передний нижний» одним «рычагом» — сайлентблок ищем внутри узла (у Toyota это «втулка»)
            # «подшибник» — опечатка, словарь сам правит её в «подшипник»: это не потерянное слово
            lost = bool(raw_head) and raw_head not in known and tree.fix(raw_head) not in known                 and raw_head not in T.ADJ and raw_head not in _GENERIC_HEADS
            head = raw_head if lost else T.head(known)
            aliases = [head] + _PART_ALIASES.get(head, []) + _NAME_KIN.get(head, [])
            has = lambda name: any(T.same(a, s) for a in aliases for s in T.stems(name, self.stop))  # noqa: E731
            # Иначе — только при неполном совпадении с группой: «Стойки стабилизатора» у Toyota совпали точно,
            # а деталь в ней — «Шарнир переднего стабилизатора»; искать «стойку» по узлам — найти «Опору стойки»
            # Сайлентблок — всегда: «сайлентблок задней цапфы» точно привёл в «Рычаги и тяги подвески»,
            # а в составе группы — «Тяга распоры»; сам сайлентблок лежит в узлах группы
            if not unit_mode and (best < 1.0 or lost or head in _DEEP or head in _NAME_KIN) and head and head not in T.ADJ \
                    and head not in _GENERIC_HEADS and not any(has(c.d.name) for c in cands):
                lists = await asyncio.gather(*(self.catalog.details(v, g.id, True) for g, _ in groups),
                                             return_exceptions=True)
                full = [d for x in lists if isinstance(x, list) for d in x if has(d.name)
                        and (head not in _PART_ALIASES or any(T.same(a, T.head_text(T.expand(d.name), self.stop) or "")
                                                               for a in aliases))]
                better = []
                if head in _NAME_KIN:
                    # «Клапанная крышка с прокладкой» — сама крышка, «Прокладка ГБЦ» — другая прокладка: берём детали, где
                    # прокладка (или её другое имя, «К-т прокладок», «ПРОКЛАДОЧНОЕ КОЛЬЦО») — главное слово, а прочие слова
                    # названия клиент назвал сам. Таких нет — в узлах ничего не берём (останется герметик или менеджер)
                    own = aliases + [T.stem(w) for w in ("кольцо", "прокладочное")] + list(_GENERIC_HEADS)
                    is_own = lambda w: any(T.same(a, w) for a in own)  # noqa: E731
                    full = [d for d in full if is_own(T.head_text(T.expand(d.name), self.stop) or "")
                            and all(is_own(w) or any(T.same(w, x) for x in q) for w in T.stems(T.expand(d.name), self.stop)
                                    if not w.isdigit() and w not in T.ADJ)]
                    better = [Candidate(d, 0.8, 0.8, True, T.side(d.context, self.pr_axis), "каталог" if T.side(d.context).axis else "")
                              for d in {_key(d.oem): d for d in full}.values()][:4]
                elif full:
                    better = self._candidates(tree, q + aliases[1:2], want, groups, full, set(), loose=True)
                if better:
                    cands = better
                elif not (T.same(head, T.stem("прокладка")) and any(_SEALANT.search(c.d.name) for c in cands)):
                    # Герметик вместо прокладки — законный ответ на «прокладку» (с пояснением ниже), а не потерянное слово
                    head_miss = next((w for w in T.words(query) if T.same(T.stem(w), head)), head)
        fallback_note = ""
        # «Комплект ГРМ» у мотора с цепью: в группе ремня пусто или нашлось не про ГРМ (у Audi 1.8 TFSI
        # в «Ремень ГРМ, натяжители» — зубчатый ремень помпы и кожух) — смотрим группы цепи (правило fallback)
        about = lambda c, words: any(T.same(w, x) for w in words  # noqa: E731
                                     for x in T.stems(f"{c.d.name} {c.d.unit} {c.d.unit_note}", self.stop))
        if not cands or any(fb["require"] and not any(about(c, fb["require"]) for c in cands)
                            and any(g.id in fb["from"] for g, _ in groups) for fb in self.fallback):
            found_before = cands
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
            cands = cands or found_before
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
        # VAG: «1K0498103 X» — обменная (восстановленная) деталь, сдаётся старая. Есть новая — обменную не показываем,
        # иначе у Octavia «внутренний ШРУС» превращался в выбор между одним и тем же номером
        bases = {_key(c.d.oem) for c in kept}
        kept = [c for c in kept if not (_key(c.d.oem).endswith("X") and _key(c.d.oem)[:-1] in bases)] or kept
        if not kept:
            pos["note"] = "По описаниям поставщиков найденные номера относятся к другой стороне."
            return pos
        kept = prefer_named_offers(kept, q, strict=unit_mode)
        if not kept:
            pos["note"] = "В узле полного каталога нужной детали по описаниям поставщиков не нашли."
            return pos
        part = self._part_of_assembly(v, kept, q)
        if part:
            c, note = part
            pos.update(status="found", question="", note=note, variants=[self._variant(c, v, alt=False)])
            pos["variants"][0]["via"] = c.d.oem
            R.annotate(pos["variants"][0], [query])
            return pos
        host = _host(q) if any(T.same(T.head(q) or "", b) for b in _BUSH) else None
        if host and not any(is_bushing(c.d.name) and any(T.same(h, s) for h in host[0]
                                                           for s in T.stems(c.d.name, self.stop)) for c in kept):
            # «Сайлентблоки переднего рычага» у Toyota: в каталоге только рычаг и «Фиксатор сайлентблока»,
            # «сайлентблок задней цапфы» — только сама цапфа. Сайлентблоки — по кроссам их номера
            # Только группы найденного: в «Рычаг стеклоочистителя» рядом с «Рычагом передним нижним» — тоже рычаги
            mine = [g for g, _ in groups if g.id in {c.d.group_id for c in kept}] or [g for g, _ in groups]
            lists = await asyncio.gather(*(self.catalog.details(v, g.id, True) for g in mine), return_exceptions=True)
            every = [d for d in pool if d.group_id in {g.id for g in mine}] \
                + [d for x in lists if isinstance(x, list) for d in x]
            own = self._bushings_in_unit(every, want, host)
            if own:
                kept = await self._check_sides(v, own, want)   # Ford: «Втулка» в узле «Задний кулак и рычаги»
            else:
                found = await self._bushings_by_host(v, every, want, host)
                if found:
                    c, note = found
                    pos.update(status="found", question="", note=note, variants=[self._variant(c, v, alt=False)])
                    pos["variants"][0]["via"] = c.d.oem
                    R.annotate(pos["variants"][0], [query])
                    return pos
                if not any(any(T.same(h, s) for h in host[0] for s in T.stems(c.d.name, self.stop)) for c in kept):
                    # «Сайлентблок задней цапфы» у Golf: нашлись только рычаги — это не то, что просили
                    return pos
        kinds: dict[tuple, list[Candidate]] = {}
        for c in kept:
            kinds.setdefault(c.kind, []).append(c)
        kinds = by_suppliers(kinds, tree.known(q))
        shafts = other_shaft(kinds, tree.known(q))
        if shafts is not None:
            kinds, asked, other = shafts
            if not kinds:
                # Mercedes C200D (судья 11.10): «сальник распредвала» — Laximo сводит его в группу сальников, а там только
                # сальники коленвала. Отдельной детали нет — честно «не нашли», а не сальник коленвала. settled — арбитр
                # и узлы иначе вернули бы тот же сальник коленвала
                pos.update(status="not_found", variants=[], question="", settled=True,
                           note=f"По каталогу вашей машины здесь только детали {other}, отдельной детали {asked} не нашли — "
                                "уточним вручную.")
                return pos
        pos["variants"] = [self._variant(c, v, alt=i > 0, name_hint=(query if unit_mode else "")) for cs in kinds.values()
                           for i, c in enumerate(sorted(cs, key=lambda c: (not (c.rows and _has_original(c, v)), -c.score)))]
        # Отзывы владельцев о фирмах по этому виду детали — по словам клиента и названиям групп
        for var in pos["variants"]:
            R.annotate(var, [query] + [g.name for g, _ in groups])
        second = None
        if host and any(is_bushing(c.d.name) for c in kept):
            try:
                second = await self._second_bushing(v, groups, pool, kept, want, host)
            except Exception:
                second = None   # подсказка: из-за неё подбор не падает
        pos["status"], pos["question"] = verdict(list(kinds), want, query)
        if not pos["question"] or pos["question"].startswith("В каталоге несколько вариантов"):
            try:
                await self._filter_ask(v, pos, kept)   # Renault: «АБС — есть или нет?» — вопрос самого каталога
            except Exception:   # noqa: BLE001 — подсказка: из-за неё подбор не падает
                pass
        try:
            await self._explain(v, pos, kept)   # «почему два номера и как выбрать» — из фактов
        except Exception:
            pass   # объяснение — подсказка; из-за него подбор не падает
        for a in self.ask:
            if all(any(T.same(w, s) for s in q) for w in a["query"]) \
                    and not any(T.same(w, s) for w in a["unless"] for s in q):
                pos["status"] = "choose"
                pos["question"] = (pos["question"] + " " if pos["question"] else "") + a["text"]
                pos["asked"] = True
        seal = [c for c in kept if _SEALANT.search(c.d.name)] if T.same(T.head(q) or "", T.stem("прокладка")) else []
        if seal:
            pos["variants"].sort(key=lambda var: bool(_SEALANT.search(var["name"])))   # прокладка — первой, герметик — ниже
        main = next(c for c in kept if c.d.oem == pos["variants"][0]["oem"])
        pos["note"] = fallback_note or self._note(q, query, main, tree)
        if seal:
            # VAG D 176 501 A1, Audi AKD 456 000 02: производитель ставит герметик вместо прокладки — без пояснения клиент
            # видит «клей» в ответ на «прокладку» (Passat 2002, перепрогон 12.10)
            pos["note"] = (pos["note"] + " " if pos["note"] else "") + (
                f"По каталогу здесь также используется герметик ({seal[0].d.oem})." if len(seal) < len(kept) else
                f"На вашей машине производитель ставит здесь не прокладку, а герметик ({ru_name(seal[0].d.name)}, "
                f"{seal[0].d.oem}). Готовые прокладки других фирм, если продаются, — среди аналогов.")
        if head_miss and not pos["note"] and re.search(r"[а-яё]", main.d.name, re.I):   # «BOOT KIT…» — не пропуск   # своё пояснение важнее: «подшипник» → «ступица в сборе»
            pos["note"] = (f"«{head_miss[:1].upper() + head_miss[1:]}» уточним по каталогу отдельно, "
                           f"ниже — «{ru_name(main.d.name)}».")
            pos["head_miss"] = head_miss   # если узлы и модель деталь не найдут — соседа не показываем (position)
        before = pos["note"]
        order_execution(pos, query)
        if pos["note"] != before:
            # «Вентилируемый» — исполнение, а не потерянное слово: пояснение «возможен и обычный» заменяет «уточним отдельно»,
            # общий вопрос «уточните по примечанию» не нужен
            pos.pop("head_miss", None)
            pos["question"] = ""
        if second:
            c2, note2 = second
            var = self._variant(c2, v, alt=False)
            var["via"] = c2.d.oem
            R.annotate(var, [query])
            pos["variants"].append(var)
            pos["note"] = (pos["note"] + " " if pos["note"] else "") + note2
        if pos.get("kind") == "fluid":
            # Масла на схеме — выбор, а не «стоят все», и «сравните со снятой деталью» к маслу не относится (Skoda 13.10)
            pos.pop("differ", None)
        elif pos["question"].startswith("В каталоге несколько вариантов") and not pos.get("asked") \
                and node_parts(pos["variants"]):
            # Разные позиции одной схемы — разные детали, а не выбор: «сравните со снятой» тут не к месту
            pos["parts"] = True
            pos.pop("differ", None)
            if wants_all(query, self.stop):
                try:
                    extra = await self._complete_node(v, groups, pool, kept, want)
                except Exception:
                    extra = []   # добор — подсказка: из-за него подбор не падает
                pos["variants"] += [self._variant(c, v, alt=False) for c in extra]
                # «РЫЧАГ» 8K0407509A без «перед» в примечании — в узле передней подвески: ось как у остальных деталей узла
                axes = {x["axis"] for x in pos["variants"]} - {""}
                if len(axes) == 1:
                    for x in pos["variants"]:
                        x["axis"] = x["axis"] or next(iter(axes))
                pos["status"], pos["question"] = "found", ""
                pos["note"] = ("Это разные детали узла, на машине стоят все — у каждой своя позиция на схеме."
                               + (" " + pos["note"] if pos["note"] else ""))
            else:
                pos["question"] = ("Это разные детали узла — на машине стоят все, у каждой своя позиция на схеме. "
                                   "Какую меняете? Можно написать номер позиции.")
        if pos["status"] == "found" and not main.member and main.prec >= 1.0:
            self.catalog.learn(main.d.group_id, main.d.name)
        return pos

    def _candidates(self, tree: TreeIndex, q: list[str], want: T.Side, groups, pool: list[Detail],
                    members: set, loose: bool = False, unit_mode: bool = False) -> list[Candidate]:
        """loose — пул уже отобран по главному слову клиента (поиск по узлам): считаем только по словам клиента.
        Слова группы там мешают: «Сайлентблок задней цапфы» в «Рычагах и тягах подвески» терял половину веса."""
        gscore = {g.id: s for g, s in groups}
        gwords = {g.id: T.stems(g.name, self.stop) for g, _ in groups}
        names = {id(d): T.stems(d.name, self.stop) for d in pool}
        known = tree.known(q)
        extra = [s for s in q if s not in known and any(T.same(s, t) for st in names.values() for t in st)]
        chosen = {g.id for g, _ in groups}
        near = [t for g, _ in groups for p, _ in g.phrases for t in p]
        cands: dict[str, Candidate] = {}
        lrs: dict[str, set[str]] = {}
        # Номер встречается в каталоге несколько раз, а сторона записана не везде (Renault R19: «SAĞ» — только у одной из
        # двух записей правого кулака). Хоть одна запись — про другую сторону, и ни одна — про нужную: номер не берём
        sides: dict[str, list[tuple[T.Side, str]]] = {}
        for d in pool:
            sides.setdefault(_key(d.oem), []).append((T.side(d.context, self.pr_axis), d.context))
        said = {"left": T.LEFT, "right": T.RIGHT, "front": T.FRONT, "rear": T.REAR}
        def other_side(k: str) -> bool:
            ss = sides.get(k, [])
            # По каждому признаку отдельно: «передняя» у записи без «лев/прав» не спасает номер, записанный «правым».
            # Нужная сторона упомянута хоть где-то — номер годится: Lancer «JOINT KIT…,LH» в узле «RH..M/T» (обе стороны)
            lr = want.lr and any(x.lr and x.lr != want.lr for x, _ in ss) \
                and not any(x.lr == want.lr or said[want.lr].search(c) for x, c in ss)
            axis = want.axis and any(x.axis and x.axis != want.axis for x, _ in ss) \
                and not any(x.axis == want.axis or said[want.axis].search(c) for x, c in ss)
            return bool(lr or axis)
        for d in pool:
            if d.match is False:
                continue
            ds = T.side(d.context, self.pr_axis)
            if want.conflicts(ds) or other_side(_key(d.oem)):
                continue
            lrs.setdefault(_key(d.oem), set()).add(ds.lr)
            member = (d.group_id, _key(d.oem)) in members
            if member:
                # Состав группы — главный признак; слова клиента в названии только упорядочивают
                # (у Ford воздушный фильтр — «Фильтрующий элемент», трос ручника — тоже в «колодках ручника»)
                prec, rec = tree.score(known + extra, names[id(d)])
                score = gscore.get(d.group_id, 0) + 0.5 * prec
                foreign = self._foreign(tree, names[id(d)], known, chosen, near)
            else:
                # Не из состава группы — только по словам; слова группы помогают («ступичн» ~ «ступица»)
                qd = list(dict.fromkeys(known + extra + ([] if loose else gwords.get(d.group_id, []))))
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
        # «Пыльник рейки» у Golf (12.10): группы «Пыльник рулевой рейки» (там «Манжета» 1K0423831B) и «Пыльник» привода.
        # Пыльник ШРУСа набирал больше за слово «пыльник» в названии, а «рейки» нет ни в нём, ни в его группе. Деталь из
        # группы, что покрывает меньше слов клиента, отбрасываем, если недостающих слов нет и в её названии — когда есть
        # деталь из группы, покрывающей больше. В одной группе (или при равном покрытии групп) ничего не меняется
        gcov = {g.id: {s for s in known if any(T.same(s, t) for p, _ in g.phrases for t in p)} for g, _ in groups}
        if len({len(x) for x in gcov.values()}) > 1:
            most = max(len(x) for x in gcov.values())
            def thin(c: Candidate) -> bool:
                own = gcov.get(c.d.group_id, set())
                return len(own) < most and len(own | {s for s in known if any(T.same(s, t) for t in names[id(c.d)])}) < most
            if any(c.member and len(gcov.get(c.d.group_id, ())) == most for c in cands.values()):
                cands = {k: c for k, c in cands.items() if not (c.member and thin(c))}
        # «Ролик приводного ремня» у Nissan Pathfinder (12.10): ролики там — «Холостой шкив», натяжитель — «Автоматическое
        # натяжное устройство», слова «ролик» нет ни у кого, и ремни («Ремень привода вентилятора…») набирали больше за
        # «ремень» и «привод». Но «ремень» у клиента — уточнение («ролик чего»), а не деталь. Главного слова нет ни в одном
        # названии — деталь, чьё главное слово и есть уточнение клиента, не берём, если есть другие
        head = T.head(known) if len(known) >= 2 else None
        quals = [s for s in known if s != head and s not in T.ADJ]
        if head and quals and not any(any(T.same(head, t) for t in names[id(c.d)]) for c in cands.values()):
            host = {k for k, c in cands.items() if any(T.same(_name_head(c.d.name), s) for s in quals)}
            if host and len(host) < len(cands):
                cands = {k: c for k, c in cands.items() if k not in host}
        for k, c in cands.items():
            if {"left", "right"} <= lrs.get(k, set()):
                # Наконечник Lexus RX: «Левый» и «Правый рулевой наконечник» — оба 45460-29435.
                # Это одна деталь на обе стороны, а не «правый»: на машину нужно две
                c.side, c.pair = T.Side(c.side.axis, ""), True
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
        if unit_mode:
            return self._unit_tier(cands, q)
        tier = drop_accessories(tier, q)
        tier = prefer_named(tier, q)
        return sorted(tier, key=lambda c: -c.score)[:MAX_VARIANTS]

    def _unit_tier(self, cands: dict[str, Candidate], q: list[str]) -> list[Candidate]:
        """Узел полного каталога: главная деталь называется по-своему («Поперечная балка в сборе» в узле «Подрамник»), слова
        клиента есть в названии узла и крепежа. Берём все значимые детали узла (не крепёж): какая из них та, что просили,
        решает название у поставщиков (prefer_named по ценам)."""
        small = [*_SMALL, *(T.stem(w) for w in ("винт", "хомут", "шплинт", "шпилька", "накладка", "подушка", "планка"))]
        head = T.head(q)
        big = [c for c in cands.values() if not any(T.same(_name_head(c.d.name), x) for x in small)]
        named = [c for c in big if head and T.same(_name_head(c.d.name), head)]
        def mine(c: Candidate) -> int:
            # Hyundai/Kia: узел «60-624» — главные детали 624051H000, 624762H000… Сначала проверяем их, остальные — после
            m = re.search(r"-(\d{2,4})", c.d.unit_code or "")
            return 0 if m and _key(c.d.oem).startswith(m.group(1)) else 1
        pick = sorted(named or big, key=lambda c: (mine(c), -c.score))
        # Один код на схеме — одна деталь с разными номерами (у балки Kia три: 624051H000/H020/H021, продаётся третий)
        seen: collections.Counter = collections.Counter()
        out = []
        for c in pick:
            seen[c.d.code_on_image] += 1
            if seen[c.d.code_on_image] <= 4:
                out.append(c)
        return out[:UNIT_VARIANTS]

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
            if not c.side.lr and not c.pair and c.rows and (lr := lr_vote(c.rows)):
                c.side = T.Side(c.side.axis, lr)
                c.side_source = c.side_source or "поставщики"
            if not want.conflicts(c.side):
                kept.append(c)
        return kept

    def _bushings_in_unit(self, pool: list[Detail], want: T.Side, host: tuple[list[str], tuple]) -> list[Candidate]:
        """Сайлентблок в узле цапфы или рычага, названный без хозяина: у Ford Focus — просто «Втулка» в узле
        «Задний кулак и рычаги подвески». Берём, только если такой один (или пара левый/правый):
        у Golf в узле «…Рычаг подвески Поворотный кулак» два разных «Сайлент-блока» — чей какой, не понять."""
        own: dict[str, Detail] = {}
        for d in pool:
            if is_bushing(d.name) and any(T.same(h, s) for h in host[0] for s in T.stems(d.unit, self.stop)) \
                    and not want.conflicts(T.side(d.context, self.pr_axis)):
                own.setdefault(_key(d.oem), d)
        sides = [T.side(d.context, self.pr_axis) for d in own.values()]
        if not own or len(own) > 2 or (len(own) == 2 and {x.lr for x in sides} != {"left", "right"}):
            return []
        return [Candidate(d, 1.0, 1.0, True, x, "каталог" if x.axis else "") for d, x in zip(own.values(), sides)]

    async def _filter_ask(self, v: Vehicle, pos: dict, kept: list[Candidate]) -> None:
        """Номера одной детали различает комплектация, которую VIN не расшифровывает (Renault, Mazda, BMW): каталог сам
        даёт вопрос (getFilterByDetail). Спрашиваем ровно его, а ответ клиента сузит варианты по примечаниям («АБС = Нет»)
        без новых запросов (Engine._follow → filter_pick)."""
        # Все номера позиции, и «альтернативы» тоже: у Renault оба кулака — один вид детали, второй шёл альтернативой,
        # и клиент видел два номера без подсказки, какой его
        main = {x["oem"] for x in pos["variants"]}
        if len(main) < 2:
            return
        c = next((c for c in kept if c.d.filter and c.d.oem in main), None)
        qs = await self.catalog.filter_question(v, c.d) if c else None
        if not qs:
            return
        q = qs[0]
        # Вопрос годится, только если примечания вариантов и правда различаются по нему: «АБС = Да» / «АБС = Нет»
        said = {x["oem"]: [val for val in q["values"] if _spec_says(x.get("specs", ""), q["name"], val)]
                for x in pos["variants"] if x["oem"] in main}
        if len({tuple(s) for s in said.values()}) < 2:
            return
        pos["filter_q"] = q
        pos["status"] = "choose"
        pos["question"] = f"Номер зависит от комплектации — уточните: {q['name']} — {' или '.join(q['values'])}?"

    async def _explain(self, v: Vehicle, pos: dict, kept: list[Candidate]):
        """Несколько номеров одной детали в одном узле (Lacetti: колодки 96405131 и 96800089): чем они отличаются
        по фактам — общие аналоги, «ставится вместе с», соседние детали узла, которые каталог по VIN не различает,
        признаки в описаниях поставщиков. Пишем в pos["differ"], текст собирает draft."""
        main = [x for x in pos["variants"] if not x["alt"]]
        same: dict[tuple, list[dict]] = {}
        for x in main:
            same.setdefault((x["axis"], x["lr"], x["unit"]), []).append(x)
        pair = max(same.values(), key=len) if same else []
        if len(pair) < 2:
            return
        a, b = pair[:2]
        by_oem = {c.d.oem: c for c in kept}
        ca, cb = by_oem.get(a["oem"]), by_oem.get(b["oem"])
        common, total = DF.overlap((a["offers"] or {}).get("numbers", []), (b["offers"] or {}).get("numbers", []))
        hints_a, hints_b = DF.contrast((ca.rows if ca else None) or [], (cb.rows if cb else None) or [])
        info: dict[str, Any] = {"oems": [a["oem"], b["oem"]], "common": common, "total": total,
                                "same": DF.interchangeable(common, total),
                                "hints": {a["oem"]: hints_a, b["oem"]: hints_b}, "with": {}, "siblings": None,
                                "vin": bool(a.get("match") and b.get("match")),
                                "eco": [x["oem"] for x in (a, b) if DF.economy(x["oem"], x["name"])]}
        for x in (a, b):
            n = DF.partner(x["name"], x["note"])
            if n:
                info["with"][x["oem"]] = {"number": n, "name": await self._describe(n, v)}
        if a.get("match") and b.get("match") and ca:
            # Обе подходят по VIN — значит, каталог не знает, что стоит на машине: ищем, чего он не различает
            sib = DF.siblings(await self.catalog.details(v, ca.d.group_id, True), {a["oem"], b["oem"]}, a["unit"])
            if sib:
                info["siblings"] = {"name": sib[0].split()[0].capitalize(), "count": sib[1]}
        if info["same"] is not None or any(info["hints"].values()) or info["with"] or info["siblings"] or info["eco"]:
            pos["differ"] = info

    async def _describe(self, number: str, v: Vehicle) -> str:
        """Что за деталь под номером — по описаниям поставщиков («Пружина прижимная тормозного суппорта»)."""
        try:
            rows = await self.src.brands(number)
        except Exception:
            return ""
        own = set(brand_candidates(rows, v.brand))
        descs = [str(r.get("description") or "").strip() for r in sorted(rows, key=lambda r: r.get("brand") not in own)]
        ru = [d for d in descs if re.search(r"[а-яё]{4,}", d, re.I)]
        return (ru or [""])[0][:70]

    async def _bushings_by_host(self, v: Vehicle, pool: list[Detail], want: T.Side,
                                host: tuple[list[str], str]) -> tuple[Candidate, str] | None:
        """Сайлентблоки переднего рычага и задней цапфы Toyota отдельно не продаёт: в каталоге рычаг «в подсборе»
        и цапфа. Неоригинальные (Masuma, Febest, CTR) поставщики привязывают к номеру рычага или цапфы —
        берём из его кроссов только сайлентблоки. Одна деталь (или пара левая/правая), иначе не угадать, чья."""
        words, what = host
        arms: dict[str, Detail] = {}
        for d in pool:
            st = T.stems(d.name, self.stop)
            if st and any(T.same(T.head(st) or "", h) for h in words) \
                    and not want.conflicts(T.side(d.context, self.pr_axis)):
                arms.setdefault(_key(d.oem), d)
        sides = [T.side(d.context, self.pr_axis) for d in arms.values()]
        if not arms or len(arms) > 2 or len({s.axis for s in sides}) > 1 \
                or (len(arms) == 2 and {s.lr for s in sides} != {"left", "right"}):
            return None
        got = await asyncio.gather(*(self.offers(d.oem, v.brand) for d in arms.values()), return_exceptions=True)
        rows, brand = [], ""
        for x in got:
            if isinstance(x, tuple):
                brand = brand or x[0]
                rows += [r for r in x[1] if is_bushing(str(r.get("description") or ""))]
        # В цапфе Lexus RX два сайлентблока: плавающий и продольной тяги — кроссы цапфы дают оба.
        # Если поставщики называют, чей сайлентблок («задней цапфы»), берём только такие
        named = [r for r in rows if any(T.same(h, s) for h in words for s in T.stems(str(r.get("description") or "")))]
        rows = named or rows
        if not rows:
            return None
        arm = next(iter(arms.values()))
        d = Detail(oem=arm.oem, name=f"Сайлентблок {what[1]}", note="", amount="", match=None, unit=arm.unit,
                   unit_note=arm.unit_note, unit_id=arm.unit_id, unit_ssd=arm.unit_ssd, image=arm.image,
                   code_on_image=arm.code_on_image, category=arm.category, group_id=arm.group_id)
        c = Candidate(d, 1.0, 1.0, True, T.Side(sides[0].axis, ""), "каталог", brand=brand, rows=rows,
                      vote=axis_vote(rows))
        maker = nice_brand(oem_brand_for(v.brand))
        note = (f"Отдельно {maker} эти сайлентблоки не продаёт — только {what[0]} в сборе. Ниже — сайлентблоки "
                f"других фирм, которые подходят к вашей машине. Можно поменять и {what[0]} целиком — "
                f"напишите «{what[2]}».")
        return c, note

    async def _complete_node(self, v: Vehicle, groups: list, pool: list[Detail], kept: list[Candidate],
                             want: T.Side) -> list[Candidate]:
        """«Передние рычаги» у Audi A4: группы каталога дали несущий и верхние рычаги, а подрулевой (направляющий) лежит в том
        же узле под другой группой. Клиент просит все — добираем из узла детали с тем же главным словом на других позициях;
        мелочь узла («Кронштейн рычага», болты) — нет. Больше шести — это уже не «все рычаги», а что-то не то."""
        heads = {_name_head(c.d.name) for c in kept} - {""}
        units = {c.d.unit_id for c in kept}
        if len(heads) != 1 or len(units) != 1:
            return []
        head, unit = next(iter(heads)), next(iter(units))
        have = {str(c.d.code_on_image) for c in kept} | {_key(c.d.oem) for c in kept}
        mine = [g for g, _ in groups if g.id in {c.d.group_id for c in kept}] or [g for g, _ in groups]
        lists = await asyncio.gather(*(self.catalog.details(v, g.id, True) for g in mine), return_exceptions=True)
        every = list(pool) + [d for x in lists if isinstance(x, list) for d in x]
        others = _NEIGHBORS - {head}
        extra: dict[str, Candidate] = {}
        for d in every:
            if d.unit_id != unit or not d.code_on_image or _key(d.oem) in have or str(d.code_on_image) in have:
                continue
            st = T.stems(T.expand(d.name), self.stop)
            if not any(T.same(head, s) for s in st) or any(T.same(n, s) for n in others for s in st):
                continue
            side = T.side(d.context, self.pr_axis)
            if not want.conflicts(side):
                extra.setdefault(_key(d.oem), Candidate(d, 1.0, 1.0, True, side, "каталог" if side.axis else ""))
        if not extra or len(extra) > 6:
            return []
        return await self._check_sides(v, list(extra.values()), want)

    async def _second_bushing(self, v: Vehicle, groups: list, pool: list[Detail], kept: list[Candidate], want: T.Side,
                              host: tuple) -> tuple[Candidate, str] | None:
        """В рычаге Suzuki Swift два сайлентблока: передний есть в каталоге (45530-62J00), задний Suzuki отдельно не
        продаёт — его номера (Febest SZAB-SX4B, Fenox CAB20018, ASVA 0701-013B) поставщики привязывают только к рычагу.
        Берём сайлентблоки из кроссов рычага, которых нет среди кроссов известного; меньше двух номеров — случайность."""
        mine = [g for g, _ in groups if g.id in {c.d.group_id for c in kept}] or [g for g, _ in groups]
        lists = await asyncio.gather(*(self.catalog.details(v, g.id, True) for g in mine), return_exceptions=True)
        every = [d for d in pool if d.group_id in {g.id for g in mine}] + [d for x in lists if isinstance(x, list) for d in x]
        # Каталог сам продаёт другие сайлентблоки этого узла (у Hyundai передний и задний — свои номера): «второй
        # отдельно не продаётся» было бы неправдой, а клиент мог просить именно этот
        own = self._bushings_in_unit(every, want, host)
        if {_key(c.d.oem) for c in own} - {_key(c.d.oem) for c in kept}:
            return None
        found = await self._bushings_by_host(v, every, want, host)
        if not found:
            return None
        c, _ = found
        num = lambda r: (bkey(r.get("brand")), _key(r.get("numberFix") or r.get("number")))  # noqa: E731
        known = {num(r) for k in kept for r in k.rows or []} | {("", _key(k.d.oem)) for k in kept}
        rest = [r for r in c.rows or [] if num(r) not in known and ("", num(r)[1]) not in known]
        if len({num(r) for r in rest}) < 2:
            return None
        c.rows, c.vote = rest, axis_vote(rest)
        c.d = replace(c.d, name=f"Сайлентблок {host[1][1]}, второй")
        maker = nice_brand(oem_brand_for(v.brand))
        return c, (f"Сайлентблоков у {host[1][1]} больше одного: второй {maker} отдельно не продаёт — только {host[1][0]} "
                   f"в сборе, поэтому ниже он показан отдельно, сайлентблоками других фирм.")

    def _part_of_assembly(self, v: Vehicle, kept: list[Candidate], q: list[str]) -> tuple[Candidate, str] | None:
        """Производитель продаёт узел целиком, а другие фирмы — его детали отдельно: на «шаровые» Suzuki Swift каталог дал
        только рычаги в сборе (45201/45202-62J00), а среди аналогов рычага есть шаровые опоры (Jikiu JB23562). Клиент
        назвал деталь — показываем из кроссов узла только её; рычаг целиком — по отдельной просьбе.
        Описания поставщиков уже есть (_check_sides), новых запросов нет."""
        for words, desc_words, hosts, (title, what, many, host_what, ask) in _ASSEMBLY_PARTS:
            if not all(any(T.same(w, s) for s in q) for w in words):
                continue
            # В каталоге сама деталь есть отдельно — обычный путь
            if not kept or any(any(T.same(w, s) for w in desc_words for s in T.stems(c.d.name, self.stop)) for c in kept):
                return None
            # В «Рычагах и тягах подвески» рядом с рычагом — тяги: деталь берём из кроссов самих рычагов
            kept = [c for c in kept if any(T.same(_name_head(c.d.name), h) for h in hosts)]
            if not kept:
                return None
            rows, seen = [], set()
            for c in kept:
                for r in c.rows or []:
                    desc = str(r.get("description") or "")
                    st = T.stems(desc, self.stop)
                    key = (bkey(r.get("brand")), _key(r.get("numberFix") or r.get("number")))
                    if key in seen or not any(T.same(w, s) for w in desc_words for s in st) \
                            or any(T.same(_desc_head(desc), h) for h in hosts):
                        continue
                    seen.add(key)
                    rows.append(r)
            if not rows:
                return None
            unit = kept[0]
            d = Detail(oem=unit.d.oem, name=title, note="", amount="", match=None, unit=unit.d.unit,
                       unit_note=unit.d.unit_note, unit_id=unit.d.unit_id, unit_ssd=unit.d.unit_ssd, image=unit.d.image,
                       code_on_image=unit.d.code_on_image, category=unit.d.category, group_id=unit.d.group_id)
            c = Candidate(d, 1.0, 1.0, True, T.Side(unit.side.axis, ""), "каталог", brand=unit.brand, rows=rows,
                          vote=axis_vote(rows))
            maker = nice_brand(oem_brand_for(v.brand))
            note = (f"Отдельно {maker} {what} не продаёт — только {host_what} в сборе. Ниже — {many} других фирм, которые "
                    f"подходят к вашей машине. Можно поменять и {host_what} целиком — напишите «{ask}».")
            return c, note
        return None

    async def _price(self, v: Vehicle, c: Candidate):
        c.brand, c.rows = "", None
        for attempt in range(2):   # поставщики разово не ответили — иначе «цену и срок уточним» при живых ценах
            try:
                c.brand, c.rows = await self.offers(c.d.oem, v.brand)
                break
            except Exception:
                if attempt:
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

    def _variant(self, c: Candidate, v: Vehicle, alt: bool, name_hint: str = "") -> dict:
        d = c.d
        amount = d.amount
        if c.pair:
            k = re.match(r"\d+", amount or "")
            amount = str(2 * (int(k.group(0)) if k else 1))
        return {
            "oem": d.oem, "brand": c.brand or oem_brand_for(v.brand), "name": d.name, "note": d.note,
            "amount": amount, "pair": c.pair, "unit": d.unit, "unit_note": d.unit_note, "match": d.match, "alt": alt,
            "axis": c.side.axis, "lr": c.side.lr, "side_source": c.side_source,
            "vote": {"front": c.vote[1], "rear": c.vote[2]}, "warning": c.warning, "score": round(c.score, 2),
            "scheme": {"catalog": v.catalog, "unit_id": d.unit_id, "ssd": d.unit_ssd,
                       "image": image_url(d.image) if d.image else "", "code": d.code_on_image,
                       "view": SC.link(image_url(d.image), absolute=False) if d.image else ""},   # через наш сервер
            "offers": self._offers_of(c, v, name_hint),
            "specs": d.specs,   # все примечания каталога: «АБС = Нет» — по ним ответ на вопрос комплектации сужает выбор
            "facts": desc_facts(c.rows or []),
        }

    def _offers_of(self, c: Candidate, v: Vehicle, name_hint: str = "") -> dict | None:
        if not c.rows:
            return None
        # name_hint — слова клиента для узла полного каталога: у главной детали там своё название («Поперечная балка в сборе»),
        # по нему кросс-мусор («Колодки тормозные» у подрамника) не отсечь, а по слову клиента — можно
        rows = c.rows
        if name_hint:
            # Узел полного каталога: кроссы поставщиков тянут чужое («Колодки тормозные» к подрамнику). Аналог — только если
            # в его описании есть слово клиента; оригинал (строка с номером каталога) остаётся
            head = T.head_text(T.expand(name_hint), self.stop)
            own = _key(c.d.oem)
            if head:
                rows = [r for r in c.rows if _key(r.get("numberFix") or r.get("number")) == own
                        or any(T.same(head, w) for w in T.stems(str(r.get("description") or "")))]
        o = curate(rows or c.rows, c.d.oem, v.brand, self.warranty, name=name_hint or c.d.name)
        # Замены производителя — только кандидаты; клиенту идут после проверки каталогом (_confirm_replacements)
        o["replacements"] = replacement_candidates(c.rows, c.d.oem, v.brand, name=c.d.name)
        return o

    async def _confirm_replacements(self, v: Vehicle, pos: dict) -> None:
        """Номер марки под другим номером подходит этой машине, только если так говорит каталог (getOEMPartApplicability):
        8K0407509N у Audi A4 не подошёл — показывать его как замену нельзя. Не подтверждено или каталог молчит — убираем."""
        todo = [(var, rp) for var in pos.get("variants", [])[:MAX_VARIANTS] for rp in (var.get("offers") or {}).get("replacements") or []]
        if not todo:
            return
        answers = await asyncio.gather(*(self.catalog.applicability(v, rp["number"]) for _, rp in todo), return_exceptions=True)
        own = {_key(var.get("oem")).lstrip("0") for var in pos.get("variants", [])}   # эти номера уже показаны вариантами
        taken: set[str] = set()
        keep: dict[int, list[dict]] = {}
        for (var, rp), ans in zip(todo, answers):
            k = _key(rp["number"]).lstrip("0")
            if not (isinstance(ans, dict) and ans.get("status") in ("FULLY", "PARTIAL")) or k in own or k in taken:
                continue
            # Лишние строки клиенту не нужны: замена показывается, если оригинала с ценой нет или она быстрее / заметно дешевле
            o = var["offers"].get("original")
            if o and not (rp["days"] < o["days"] or rp["price"] < 0.9 * o["price"]):
                continue
            taken.add(k)
            keep.setdefault(id(var), []).append(rp | {"catalog": ans["status"]})
        for var in pos.get("variants", []):
            o = var.get("offers")
            if o and "replacements" in o:
                o["replacements"] = keep.get(id(var), [])

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


# Исполнения одной детали, которые каталог по VIN не различает, а на машине стоит одно (T4 1992: диск с плавающим суппортом
# — обычный, с рамочным — вентилируемый). Клиент назвал исполнение — его первым, второе не прячем: клиент мог ошибиться
# (замечание пользователя 12.10). (как назвал клиент, другое исполнение, как отличить)
_EXECUTIONS = [(re.compile(r"(?<!не)(?<!не\s)вентилир", re.I), re.compile(r"невентилир|сплошн|обычн|(?<![а-яё])не\s+вентилир", re.I),
                "вентилируемый", "обычный",
                "у вентилируемого два диска с каналами между ними, он заметно толще")]


def order_execution(pos: dict, query: str) -> None:
    """Клиент назвал исполнение («вентилируемый»), а каталог дал оба: нужное — первым, второе — ниже с подсказкой
    «возможен и …, проверьте». Признак — в названии или примечании детали каталога («Тормозной диск (вентилир.)»)."""
    text = lambda x: f"{x.get('name', '')} {x.get('note', '')} {x.get('unit_note', '')}"  # noqa: E731
    for mark, other, mark_name, other_name, how in _EXECUTIONS:
        # Каталог помечает одно исполнение («(вентилир.)»), второе — просто без пометки
        marked = [x for x in pos.get("variants", []) if mark.search(text(x)) and not other.search(text(x))]
        plain = [x for x in pos.get("variants", []) if x not in marked]
        if not marked or not plain:
            continue
        if other.search(query):
            first, rest, want_name, rest_name = plain, marked, other_name, mark_name
        elif mark.search(query):
            first, rest, want_name, rest_name = marked, plain, mark_name, other_name
        else:
            continue
        pos["variants"] = first + rest
        pos["note"] = (f"Первым — {want_name}, как вы написали. По каталогу для вашей машины возможен и {rest_name} "
                       f"({rest[0]['oem']}) — проверьте: {how}.")
        return


def desc_facts(rows: list[dict]) -> list[str]:
    """Признаки детали по всем описаниям поставщиков: «наружный», «передний». Для ответа на
    «это внутренний или наружный?» — берём только явное: есть «за» и почти нет «против»."""
    descs = {str(r.get("description") or "").strip().lower() for r in rows} - {""}
    out = []
    for a, b, a_name, b_name in _ATTRS + [(T.FRONT, T.REAR, "передний", "задний")]:
        ya = sum(1 for d in descs if a.search(d) and not b.search(d))
        yb = sum(1 for d in descs if b.search(d) and not a.search(d))
        if ya and ya >= 3 * yb:
            out.append(a_name)
        elif yb and yb >= 3 * ya:
            out.append(b_name)
    return out


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
                # Все разные описания, не одно на артикул: у пыльника GM оригинал — один номер, и только
                # один поставщик из нескольких пишет «пыльник шруса внешнего»
                descs = {str(r.get("description") or "").strip().lower() for r in c.rows or []} - {""}
                yes = sum(1 for d in descs if want.search(d) and not other.search(d))
                no = sum(1 for d in descs if other.search(d) and not want.search(d))
                # Много описаний — нужен явный перевес; мало (только оригинал) — хватит одного явного и ни одного «за»
                if (no >= 3 and no >= 4 * yes) or (no >= 1 and yes == 0 and len(descs) <= 4):
                    found = other_name
                else:
                    keep.append(c)
            cands = keep
    return cands, found


_BUNDLE = {T.stem(w) for w in ("набор", "комплект", "ящик", "кейс")}


def _main(q: list[str]) -> str | None:
    """Главное слово; у «набора отвёрток», «комплекта бит» — то, что в наборе."""
    h = T.head(q)
    nouns = [s for s in q if s not in T.ADJ and s != h]
    return nouns[0] if h in _BUNDLE and nouns else h
# Как деталь, которую клиент называет по-своему, может называться в каталоге: сайлентблок у Toyota — «втулка»
_PART_ALIASES = {T.stem("сайлентблок"): [T.stem("втулка"), "bush", T.stem("сайлент")],
                 # у VAG пыльник рейки — «Манжета» 1K0423831B (Golf 2009, 12.10): не узнав её, робот уходил в пыльники ШРУСа
                 T.stem("пыльник"): [T.stem("чехол"), "boot", T.stem("манжета")],
                 T.stem("отбойник"): [T.stem("буфер"), "bumper"]}
# Другие названия той же детали — только чтобы найти её в узлах; главное слово детали при этом не проверяется (у Kia прокладки —
# «ПРОКЛАДОЧНОЕ КОЛЬЦО-…», у VAG — «К-т прокладок…»). Passat 2002: прокладка клапанной крышки — «Уплотнитель» 06A103483D,
# не узнав её, робот брал герметик
_NAME_KIN = {T.stem("прокладка"): [T.stem("уплотнитель"), T.stem("уплотнение"), "gasket"]}
# Герметик вместо прокладки (VAG D 176 501 A1, Audi AKD 456 000 02, GM sealer): деталь каталога, но не прокладка — объясняем
_SEALANT = re.compile(r"герметик|sealant|sealer|liquid\s+gasket|жидк\w*\s+прокладк", re.I)
# Пояснения движка «слово клиента потеряно»: «Шланг» уточним по каталогу отдельно…, Отдельно «усилителя» в каталоге не нашли…
_LOST_NOTE = re.compile(r"уточним по каталогу отдельно|в каталоге не нашли")

_EN_PART = {"hose": "шланг", "pipe": "трубка", "tube": "трубка", "gasket": "прокладка", "seal": "уплотнитель",
            "sensor": "датчик", "bolt": "болт", "nut": "гайка", "bracket": "кронштейн", "clamp": "хомут", "pump": "насос",
            "filter": "фильтр", "valve": "клапан", "cover": "крышка", "bearing": "подшипник", "spring": "пружина",
            "switch": "выключатель", "relay": "реле", "bushing": "втулка", "belt": "ремень", "boot": "пыльник",
            "lamp": "фонарь", "mirror": "зеркало", "bumper": "бампер", "vacuum": "вакуумный", "brake": "тормозной",
            "booster": "усилитель", "oil": "масляный", "fuel": "топливный", "water": "водяной", "air": "воздушный",
            "radiator": "радиатор", "tank": "бачок", "cap": "крышка", "washer": "шайба", "screw": "винт", "cable": "трос"}


def _ru_words(name: str) -> str:
    """«HOSE,BRAKE BOOSTER VACUUM» → «шланг,тормозной усилитель вакуумный»: в каталогах Mitsubishi и др. пятая часть
    названий по-английски, а слова клиента русские."""
    return re.sub(r"[A-Za-z]+", lambda m: _EN_PART.get(m.group(0).lower(), m.group(0)), name or "")


_HOSE_KIN = {T.stem("шланг"): [T.stem("трубка"), T.stem("патрубок")], T.stem("трубка"): [T.stem("шланг"), T.stem("патрубок")],
             T.stem("патрубок"): [T.stem("шланг"), T.stem("трубка")]}   # так же называют одну деталь по-разному
_BUSH = [T.stem("сайлентблок"), T.stem("втулка"), "bush", T.stem("сайлент")]   # VW: «Сайлент-блок»


# Деталь, в которую запрессован сайлентблок: слова, (что продаётся в сборе, чей сайлентблок, как попросить целиком)
_HOSTS = [([T.stem("рычаг")], ("рычаг", "рычага", "рычаг")),
          ([T.stem("цапфа"), T.stem("кулак")], ("цапфу", "цапфы", "цапфа задняя")),
          ([T.stem("тяга")], ("тягу", "тяги", "тяга")),
          ([T.stem("балка")], ("балку", "балки", "балка"))]

# Сайлентблок подрамника (Вордстат: 1,2% запросов о сайлентблоках). В быстрых группах подрамника нет — находился сайлентблок
# РЫЧАГА. Подрамник в узлах полного каталога зовут по-разному: «Подрамник» (VAG, Kia), «SUSPENSION CROSSMEMBER» и
# «поперечина передней подвески» (Toyota), «ТРАВЕРСА» (Mercedes), «CROSSMEMBER & STABILIZER» (Mazda)
_SUB_Q = re.compile(r"подрамн|траверс", re.I)
_SUB_UNIT = re.compile(r"подрамн|вспомогательн\w*\s+рам|cross\s*-?member|sub\s*-?frame|траверс|"
                       r"поперечин\w*\s+(?:передн\w*\s+|задн\w*\s+)?(?:подвеск|мост)", re.I)
_SUB_NOT = re.compile(r"багажник|панел|сиден|щит|колонк|педал|звукоизол|буксир|люк|стекл|двигател\w*\s+в\s+сборе", re.I)
# Сайлентблок чужой детали в том же узле: рычага, стабилизатора, КП («Опора» в «Детали крепления КП Подрамник» у Audi)
_SUB_OTHER = re.compile(r"рычаг|тяг|стабилиз|амортиз|штанг|рессор|кузов|радиатор|глушит|выхлоп|(?<![а-яё])кп(?![а-яё])|"
                        r"коробк|двигател|engine|transm|\barm\b|link|stabili|shock|body", re.I)
_SUB_MOUNT = [T.stem(w) for w in ("опора", "подушка")] + ["bush", "mount", "insulator"]
_SUB_PART = [T.stem(w) for w in ("подрамник", "поперечина", "балка", "траверса")] + ["crossmember", "subframe"]


def _sub_axis(text: str) -> str:
    """Чей это подрамник: «Опора элемента задней подвески, передняя правая» — задний (сторона самой опоры не в счёт),
    «Задний сайлентблок поперечины передней подвески» — передний, «REAR SUBFRAME» — задний."""
    for pat in (r"(передн|задн)\w*\.?\s+(?:подвеск|мост)", r"(передн|задн)\w*\.?\s*(?:подрамн|поперечин|травер)",
                r"(front|rear)\s*-?\s*(?:sub\s*-?frame|susp)", r"(?:cross\s*-?member|sub\s*-?frame)\s*-?\s*(front|rear)"):
        m = re.search(pat, text or "", re.I)
        if m:
            return "front" if m.group(1).lower() in ("передн", "front") else "rear"
    return ""


# Деталь, которую производитель продаёт только в узле: (слова клиента, слова детали в описании, узел,
# (название, деталь в вин. падеже, во мн. числе, узел в вин. падеже, как попросить узел целиком)). Сайлентблоки — отдельно (_bushings_by_host)
_ASSEMBLY_PARTS = [
    ([T.stem("шаровая")], [T.stem("шаровая"), T.stem("шаровой")], [T.stem("рычаг")],
     ("Опора шаровая", "шаровую опору", "шаровые опоры", "рычаг", "рычаг в сборе")),
    ([T.stem("подшипник"), T.stem("ступица")], [T.stem("подшипник")], [T.stem("ступица")],
     ("Подшипник ступицы", "подшипник ступицы", "подшипники ступицы", "ступицу", "ступица в сборе")),
]


def _host(q: list[str]) -> tuple[list[str], tuple[str, str, str]] | None:
    return next((h for h in _HOSTS if any(T.same(w, s) for w in h[0] for s in q)), None)


def is_bushing(name: str) -> bool:
    """«Сайлентблок…», «С/блок…», «Втулка рычага» — сам сайлентблок; «Фиксатор сайлентблока» — нет."""
    st = T.stems(T.expand(name))
    return bool(st) and any(T.same(T.head(st) or "", b) for b in _BUSH)


# Ищем в узлах группы, даже когда группа совпала точно: «Задний кулак (цапфа)» у Toyota лежит в узлах
# «Рычагов и тяг подвески», в составе группы его нет
_DEEP = set(_PART_ALIASES) | {T.stem("цапфа")}
_PARKING = [T.stem(w) for w in ("стояночного", "стояночный", "ручного", "ручник")]
# Общие слова: «комплект ГРМ» — не повод писать «Комплект уточним отдельно»
_GENERIC_HEADS = {T.stem(w) for w in ("комплект", "набор", "ремкомплект", "к-т", "деталь", "запчасть", "элемент")}
_EXCLUSIVE = [(T.stem("ремень"), T.stem("цепи")), (T.stem("цепь"), T.stem("ремня")),
              (T.stem("рычаг"), T.stem("очистителя")), (T.stem("рычаг"), T.stem("стеклоочистителя"))]
# Мелочь при детали в каталоге. Ремкомплекта нет: «ремкомплект подшипника» у Ford — сам подшипник с крепежом
_SMALL = [T.stem(w) for w in ("пружина", "направляющая", "прокладка", "уплотнительная", "уплотнение", "болт", "гайка", "шайба",
                               "скоба", "клипса", "фиксатор", "заглушка", "кольцо", "стопорное", "датчик", "пыльник",
                               "сальник", "втулка", "кронштейн", "крышка", "кожух")]


def _name_head(name: str) -> str:
    """Главное слово названия детали без «1 комплект», «набор»: «1 комплект тормозных колодок» → «колодк».
    Прилагательные — по окончанию самого слова: основа «маловязк» правило T.ADJ не узнаёт, и «Маловязкое
    моторное масло» теряло «масло»."""
    for w in T.words(T.expand(name)):
        s = T.stem(w)
        if not s or s[:1].isdigit() or s in _GENERIC_HEADS or s in T.ADJ:
            continue
        if re.fullmatch(r"[а-яё]+", w.lower()) and T._ADJ_END.search(w.lower()):
            continue
        return s
    return ""


# Составные части и соседи детали: каталог кладёт их в тот же узел, а поставщики — под тем же номером
_NEIGHBORS = set(_SMALL_OFFERS) | {T.stem(w) for w in ("рычаг", "корпус", "кожух", "щит", "ограничитель", "крышка", "успокоитель", "башмак", "звездочка", "шестерня")}


def _desc_share(rows: list[dict] | None, words: list[str]) -> float:
    """Доля разных описаний поставщиков по номеру, где есть слово клиента: у балки Kia оригинал в прайсах — «Поперечная балка»,
    а NTY, Sat, Quattro Freni пишут «Подрамник передний». Меньше пяти описаний — не судим."""
    descs = {str(r.get("description") or "").lower() for r in rows or []} - {""}
    if len(descs) < 5:
        return 0.0
    return sum(1 for d in descs if any(T.same(w, s) for w in words for s in T.stems(d))) / len(descs)


def prefer_named_offers(kept: list["Candidate"], q: list[str], strict: bool = False) -> list["Candidate"]:
    """«Цепь ГРМ» у Honda: в узле ещё натяжитель и рычаг натяжителя (каталог называет их по-английски, названия не
    сравнить), а описание оригинала у поставщиков понятное: «Цепь…», «Натяжитель цепи…». Клиент назвал деталь, и по
    описанию оригинала она есть среди найденного — варианты, где оригинал явно другая деталь (натяжитель, рычаг), убираем."""
    head = T.head(q) if q else ""
    if not head or head in T.ADJ or head in _GENERIC_HEADS:
        return kept
    aliases = [head] + _PART_ALIASES.get(head, [])

    def desc_head(c: "Candidate") -> str:
        oem = _key(c.d.oem)
        for r in c.rows or []:
            if _key(r.get("numberFix") or r.get("number")) == oem and str(r.get("description") or "").strip():
                return _desc_head(str(r["description"]))
        return ""

    def named(c: "Candidate") -> bool:
        h = desc_head(c)
        return any(T.same(h, a) for a in aliases) or any(T.same(_name_head(c.d.name), a) for a in aliases)             or (strict and _desc_share(c.rows, aliases) >= 0.3)
    good = [c for c in kept if named(c)]
    if strict:
        # Узел полного каталога: в нём и крепёж, и соседние детали. Оставляем только то, что поставщики называют, как клиент,
        # а подтвердить нечем — не показываем чужое («Подрамник» → рычаги и сайлентблоки узла)
        sold = {c.d.code_on_image for c in good if c.rows}
        return [c for c in good if c.rows or c.d.code_on_image not in sold]   # у кода есть продающийся номер — пустые не нужны
    if not good or len(good) == len(kept):
        return kept
    return [c for c in kept if c in good or not (desc_head(c) and any(T.same(desc_head(c), n) for n in _NEIGHBORS))]


def prefer_named(tier: list["Candidate"], q: list[str]) -> list["Candidate"]:
    """Клиент назвал деталь, и она есть среди найденного — её обвес на той же стороне не предлагаем: на «бампер
    передний» у Kia — сам бампер, а не «Гаситель энергии» и «Выступ» переднего бампера. Другая сторона остаётся:
    «подшипник ступицы» спереди и «ступица в сборе» сзади — повод спросить, какая нужна."""
    head = T.head(q) if q else ""
    if not head or head in T.ADJ or head in _GENERIC_HEADS:
        return tier
    # Названа и деталь, где есть все слова клиента, хоть и не первым словом: Ford «Блок датч. — положение распредвала»
    # на «датчик распредвала» (12.10) выпадал, а «Датчик детонации» оставался
    # Только когда клиент назвал и уточнение: на одно «бампер» и «Гаситель энергии бампера» содержит «всё»
    full = lambda c: len(q) >= 2 and all(any(T.same(s, t) for t in T.stems(c.d.name)) for s in q)  # noqa: E731
    named = [c for c in tier if T.same(_name_head(c.d.name), head) or full(c)]
    if not named or len(named) == len(tier):
        return tier
    # Убираем только обвес той же стороны, что и сама деталь: передний подшипник без явной стороны рядом с задней
    # ступицей у Audi — повод спросить «передняя или задняя?», а не лишний вариант
    # Сама деталь без «лев/прав» (бампер Peugeot) закрывает и левый, и правый обвес той же оси («Защита на бампере; левый»)
    def covered(c: "Candidate") -> bool:
        return any(n.side.axis == c.side.axis and n.side.lr in ("", c.side.lr) for n in named)
    return [c for c in tier if c in named or not covered(c)]


def drop_accessories(tier: list["Candidate"], q: list[str]) -> list["Candidate"]:
    """«Колодки передние» у Toyota: в группе и колодки стояночного тормоза, и их пружины. Если клиент
    не писал про ручник — стояночные убираем; мелочь («Натяжная пружина…», «Направляющая цепи»,
    «Уплотнительная прокладка натяжителя») — если клиент спрашивал саму деталь, а не её. Только когда
    после этого что-то остаётся: у Ford воздушный фильтр зовётся «Фильтрующий элемент»."""
    def first_two(c: "Candidate") -> list[str]:
        # До «с …»: «ШРУС с пыльником, монтажными деталями…» у VAG — это сам ШРУС (внутренний), не пыльник
        return T.stems(re.split(r"\s(?:с|со)\s|,", c.d.name)[0])[:2]

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


# Виды позиций: запчасти ищем в каталоге, масла и жидкости — тоже (у многих марок есть оригинал), остальное —
# подбираем отдельно, по параметрам или названию
KIND_TITLE = {0: "Запчасти", 1: "Масла и жидкости", 2: "Аккумулятор", 3: "Шины и диски", 4: "Другое"}
# Что бот не подбирает сам: передаём менеджеру («Ответьте сами» на странице подбора). Ключ — вид, значение — как назвать
MANAGER_KINDS = {"fluid": "жидкость, масло", "battery": "аккумулятор", "tire": "шины, диски", "tool": "инструмент",
                 "chemistry": "автохимия", "accessory": "аксессуары"}
KIND_NOTE = {
    "battery": "Аккумуляторы подбирает менеджер — передали ему, он напишет вам вариантами.",
    "tire": "Шины и диски подбирает менеджер по размеру — передали ему; напишите размер с боковины шины или пришлите фото.",
    "tool": "Это не деталь автомобиля — передали менеджеру, он подберёт и напишет.",
    "chemistry": "Это не деталь автомобиля — передали менеджеру, он подберёт и напишет.",
    "accessory": "Это не деталь автомобиля — передали менеджеру, он подберёт и напишет.",
}
_KIND_WORDS = {
    "fluid": ("масло", "масла", "антифриз", "тосол", "жидкость", "омывайка", "незамерзайка"),
    "battery": ("аккумулятор", "акб", "аккум", "батарея"),
    "tire": ("шина", "шины", "резина", "покрышка", "колесо"),
}


def kind_of(q: list[str]) -> str:
    """Вид по главному слову, когда модель не разбирала: «масло моторное» — жидкость, «АКБ» — аккумулятор.
    «Масляный фильтр» — запчасть: главное слово «фильтр». «Резина» — шины, только если она главное слово
    и рядом нет «уплотнительная», «двери» и т. п."""
    h = T.head(q) if q else ""
    for kind, words in _KIND_WORDS.items():
        if any(T.same(h, T.stem(w)) for w in words):
            if kind == "tire" and len(q) > 1 and not any(T.same(s, T.stem(w)) for s in q
                                                          for w in ("зимняя", "летняя", "шипованная", "липучка", "r")):
                return "part"   # «резинка двери», «колесо рулевое» — детали
            return kind
    return "part"


def needs_llm(chunks: list[str]) -> bool:
    """Когда звать модель: несколько строк, заголовки «Задок:», «+», «либо», длинный текст. Короткое
    «колодки передние» правила разбирают быстрее и не хуже."""
    body = "\n".join(chunks)
    return (len(chunks) >= 3 or len(body) > 120 or bool(re.search(r":\s*$|:\s|\+|\bлибо\b|\(", body, re.M)))


_LONG_SHAFT = ((re.compile(r"коленчат\w*\s+вал\w*"), "коленвал"), (re.compile(r"распределит\w*\s+вал\w*"), "распредвал"))


_SHAFTS = {T.stem("коленвал"): "коленвала", T.stem("распредвал"): "распредвала"}


def other_shaft(kinds: dict[tuple, list["Candidate"]], known: list[str]) -> tuple[dict, str, str] | None:
    """Клиент назвал вал («сальник распредвала»), а вариант — деталь другого вала: так пишет каталог (название, узел,
    примечание: «УПЛОТНЕНИЕ КОЛЕНЧАТОГО ВАЛА») или большинство поставщиков, а про вал клиента — никто. Такой вариант
    убираем. by_suppliers этого не умеет: он сравнивает варианты между собой, а тут чужие все. None — клиент вал не назвал."""
    asked = [k for k in _SHAFTS if any(T.same(k, s) for s in known)]
    if len(asked) != 1:
        return None
    mine, other = asked[0], next(k for k in _SHAFTS if k != asked[0])

    def text_stems(text: str) -> list[str]:
        t = text.lower()
        for rx, short in _LONG_SHAFT:
            t = rx.sub(short, t)
        return T.stems(t)

    def foreign(c: "Candidate") -> bool:
        cat = text_stems(f"{c.d.name} {c.d.context}")
        descs = _descs([c])
        if any(T.same(mine, s) for s in cat) or (descs and _share(descs, [mine]) >= 0.05):
            return False
        return any(T.same(other, s) for s in cat) or (len(descs) >= 3 and _share(descs, [other]) >= 0.3)

    out = {k: [c for c in cs if not foreign(c)] for k, cs in kinds.items()}
    return {k: cs for k, cs in out.items() if cs}, _SHAFTS[mine], _SHAFTS[other]


def _descs(cs: list["Candidate"]) -> set[str]:
    out = set()
    for c in cs:
        for r in c.rows or []:
            d = str(r.get("description") or "").lower()
            for rx, short in _LONG_SHAFT:   # «датчик положения коленчатого вала» — тот же «датчик коленвала»
                d = rx.sub(short, d)
            if d:
                out.add(d)
    return out


def _share(descs: set[str], words: list[str]) -> float:
    return sum(1 for d in descs if any(T.same(w, s) for w in words for s in T.stems(d))) / len(descs)


def by_suppliers(kinds: dict[tuple, list["Candidate"]], known: list[str]) -> dict[tuple, list["Candidate"]]:
    """В группе несколько разных деталей (у VAG в «Электронике двигателя» — «Датчик импульсов», детонации,
    давления), а клиент назвал какую: «датчик коленвала». Оставляем те, у которых поставщики в описаниях
    пишут это слово («датчик положения коленвала»), если у других его нет совсем. Потом то же по номерам внутри
    вида: «Датчиком импульсов» Audi зовёт и датчик распредвала (07L905163B), и коленвала (06H906433)."""
    if len(known) < 2:
        return kinds
    head = T.head(known)
    words = [s for s in known if s != head and not (T.side(s).axis or T.side(s).lr)]   # сторону уже проверили по ценам
    if not words:
        return kinds
    if len(kinds) >= 2:
        share = {}
        for k, cs in kinds.items():
            descs = _descs(cs)
            if len(descs) >= 5:
                share[k] = _share(descs, words)
        if share:
            top, rest = max(share.values()), sorted(share.values())[-2] if len(share) > 1 else 0.0
            # Слово только у одного вида — хватает и 10%: у «Датчика импульсов» Audi «коленвал» в 49 описаниях из 164
            # (прочие — «распредвала»), у датчиков детонации и давления — ни в одном
            if top >= 0.3 or (top >= 0.1 and rest == 0):
                kinds = {k: cs for k, cs in kinds.items() if share.get(k, 1.0) >= 0.05}
    out = {}
    for k, cs in kinds.items():
        descs = {id(c): d for c in cs if len(d := _descs([c])) >= 5}
        # Каждое слово отдельно: «положения» поставщики пишут у обоих датчиков — оно не решает, решает «коленвал»
        for w in words if len(descs) >= 2 else []:
            per = {id(c): _share(descs[id(c)], [w]) for c in cs if id(c) in descs}
            top = max(per.values(), default=0.0)
            # Номер, который поставщики почти не зовут словом клиента, когда соседа зовут: 06H906433 — «распредвал» в 17
            # из 86 описаний, 07L905163B — в 63 из 82. Номер без описаний не трогаем
            if top >= 0.3:
                cs = [c for c in cs if per.get(id(c), 1.0) * 3 >= top]
        out[k] = cs
    return out


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
        # «Комплекты передних и задних пружин»: «комплект» — не существительное детали, пружины берём у соседа
        if not st or any(s not in T.ADJ and s not in _GENERIC_HEADS for s in st):
            continue
        noun = (noun_of(pieces[i + 1]) if i + 1 < len(pieces) else "") or (noun_of(pieces[i - 1]) if i else "")
        if noun:
            out[i] = f"{p} {noun}"
    return out


def _has_original(c: Candidate, v: Vehicle) -> bool:
    o = curate(c.rows or [], c.d.oem, v.brand, set())
    return bool(o["original"])


def node_parts(variants: list[dict]) -> bool:
    """Найденное — разные детали одного узла, а не варианты на выбор: у Audi A4 спереди несущий, верхний передний и
    верхний задний рычаги — на схеме узла у каждого своя позиция, на машине стоят все. Варианты на выбор (комплектация,
    замена номера) стоят на одной позиции. Нет позиции у кого-то, повтор позиции или разные узлы — не решаем, как раньше."""
    by_side: dict[tuple, list[dict]] = {}
    for v in variants:
        if not v.get("alt"):
            by_side.setdefault((v.get("axis", ""), v.get("lr", "")), []).append(v)
    multi = [vs for vs in by_side.values() if len(vs) > 1]
    if not multi:
        return False
    for vs in multi:
        codes = [str((v.get("scheme") or {}).get("code") or "").strip() for v in vs]
        units = {(v.get("scheme") or {}).get("unit_id") for v in vs}
        if not all(codes) or len(set(codes)) != len(codes) or len(units) != 1:
            return False
    return True


_ALL_WORDS = re.compile(r"(?<![а-яё])(?:комплект\w*|набор\w*|все|вс[её]|в\s+круг|полностью)(?![а-яё])", re.I)


def wants_all(query: str, stop: frozenset[str]) -> bool:
    """Клиент просит все такие детали: «рычаги», «передних рычагов», «комплект», «все», «в круг» — а не одну («рычаг»)."""
    if _ALL_WORDS.search(query):
        return True
    st = T.stems(T.expand(query), stop)
    head = T.head(st) if st else None
    word = next((w for w in T.words(query) if head and T.stem(w) == head), "")
    return bool(re.search(r"(?:[иы]|ов|ев|ей)$", word.lower()))


# «Комплект передних рычагов», «набор сайлентблоков»: у производителя такой детали нет — это все такие детали узла
# (поштучно у VAG, комплектом — у других фирм). «Комплект сцепления/ГРМ/прокладок» — настоящие группы каталога, не трогаем
_KIT_OF = re.compile(r"^\s*(?:комплект\w*|набор\w*)\s+((?:[а-яё]+\s+){0,2}(?:рычаг|сайлентблок|сайлент|амортизатор|стоек|стойк|"
                     r"пружин|опор|втул|тяг|наконечник|свеч|катуш|форсун|шаров)[а-яё]*.*)$", re.I)


def _spec_says(specs: str, name: str, value: str) -> bool:
    """Примечание каталога говорит «<признак> = <значение>»: «Код оборудования = 11-,12-;АБС = Нет»."""
    return bool(re.search(rf"(?<![а-яёa-z]){re.escape(name)}\s*=\s*{re.escape(value)}(?![а-яёa-z])", specs or "", re.I))


_YES = re.compile(r"^(?:да|ага|есть|имеется|стоит|конечно|с\s+\w+)(?![а-яё])", re.I)
_NO = re.compile(r"(?<![а-яё])(?:нет|без|отсутствует)(?![а-яё])", re.I)
# Клиент не знает ответа на вопрос комплектации: не сужаем — подсказываем, как узнать, и передаём менеджеру
_UNSURE = re.compile(r"не\s*зна|не\s*помн|без\s+понятия|понятия\s+не|(?<![а-яё])хз(?![а-яё])|х\.\s?з|не\s+уверен|"
                     r"не\s+в\s+курсе|не\s+разбира|затрудня|что\s+это|что\s+такое|как\s+(?:узнать|проверить|понять|посмотреть)|"
                     r"где\s+(?:посмотреть|глянуть)|откуда\s+(?:мне|я)|наверн|может\s+быть|вроде", re.I)
# Как клиенту узнать признак самому — для частых вопросов каталога; прочие — по номеру со снятой детали
_FILTER_HELP = [(re.compile(r"abs|абс", re.I), "при включении зажигания на приборной панели загорается значок ABS — "
                                               "значит, АБС есть"),
                (re.compile(r"кпп|коробк|transm", re.I), "автомат — нет педали сцепления, механика — есть"),
                (re.compile(r"привод|4wd|awd", re.I), "полный привод — обычно надпись 4WD/4x4/AWD на кузове или кнопка/"
                                                      "рычаг включения полного привода"),
                (re.compile(r"кондиц|a/c", re.I), "есть кнопка A/C или снежинка на панели — кондиционер есть")]


def filter_unsure(text: str, positions: list[dict]) -> int | None:
    """Позиция с вопросом комплектации, на который клиент ответил «не знаю», «хз», «а что это?», «да нет»."""
    t = (text or "").strip().lower()
    if not t or len(t) > 120:
        return None
    for i, p in enumerate(positions):
        if (p.get("filter_q") or {}) and (_UNSURE.search(t) or (_YES.search(t) and _NO.search(t))):
            return i
    return None


def filter_help(name: str) -> str:
    how = next((h for rx, h in _FILTER_HELP if rx.search(name)), "")
    return (f"Ничего страшного. Как проверить: {how}. " if how else "Ничего страшного. ") + \
        "Можно прислать фото снятой детали с номером — сверим. Или менеджер уточнит по вашей машине и напишет."


def filter_pick(text: str, positions: list[dict]) -> tuple[int, str] | None:
    """Ответ на вопрос комплектации из памяти («АБС — Да или Нет?»): «нет», «без абс», «есть», «да», или само значение
    («11-»). Только один однозначный ответ и только у позиции, где этот вопрос задан — иначе None (разберут правила)."""
    t = (text or "").strip().lower().strip(" .!)")
    if not t or len(t) > 60 or _UNSURE.search(t):
        return None   # «наверное нет», «да нет», «не знаю» — не ответ (filter_unsure)
    for i, p in enumerate(positions):
        q = p.get("filter_q") or {}
        if not q:
            continue
        hits = []
        for val in q.get("values") or []:
            vl = val.lower()
            if vl in ("да", "yes"):
                ok = bool(_YES.search(t)) and not _NO.search(t)
            elif vl in ("нет", "no") and _YES.search(t):
                ok = False   # «да нет» — не ответ
            elif vl in ("нет", "no"):
                ok = bool(_NO.search(t))
            else:
                ok = vl in t
            if ok:
                hits.append(val)
        if len(hits) == 1:
            return i, hits[0]
    return None


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


BY_TEXT_WARNING = ("машина подобрана по марке, модели и году из вашего сообщения — VIN каталог не знает, комплектацию "
                   "не проверяли; для точного подбора пришлите фото СТС")


def compare_model(model: str, v: Vehicle) -> list[str]:
    """Клиент выбирает модель из списка руками и ошибается — сверяем только год и объём двигателя."""
    out = []
    if not model:
        return out
    m = re.search(r"\b(19[89]\d|20[0-4]\d)\b", model)
    # Поле «выпущено» у Laximo бывает и словом («СТАР…»): год берём только если он там есть
    year = re.search(r"\b(?:19|20)\d\d\b", f"{v.attrs.get('manufactured', '')} {v.attrs.get('date', '')}")
    made = year.group(0) if year else ""
    if m and made and abs(int(m.group(1)) - int(made)) > 1:
        out.append(f"В заявке {m.group(1)} год, по VIN — {made}")
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
    # Номер — как в приложении и поиске ABCP: «A 001 159 26 01» → «A0011592601», «202 026-SX» → «202026SX»
    head = f"{brand} {_key(o['number'])}" if numbers else brand
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
    if catalog_name.lower().strip(" .…") in _VAGUE or not re.search(r"[а-яё]", catalog_name, re.I):
        # у VAG бывает и просто «Деталь», у GM — «BOOT KIT,FRT WHL DRV SHF CV JT»: тогда слова клиента
        catalog_name = query[:1].upper() + query[1:]
    if o and o["original"]:
        # Номер — из каталога: поставщики пишут его как попало («1 712 024»). Название — поставщика, если оно
        # про ту же деталь («Колодки тормозные передние»), иначе каталожное: «Focus 2011-> задний» ни о чём
        orig = dict(o["original"], number=var["oem"])
        theirs = nice_name(orig["description"], orig["brand"], orig["number"])
        same = theirs.lower().strip(" .…") not in _VAGUE and             any(T.same(a, b) for a in T.stems(theirs) for b in T.stems(catalog_name))
        out.append(_offer_line(orig, numbers, theirs if same else catalog_name, "оригинал"))
    else:
        num = f" {_key(var['oem'])}" if numbers else ""
        if not var.get("via"):   # сайлентблок по номеру рычага: оригинала отдельно нет, так и написано выше
            out.append(f"• {nice_brand(var['brand'])}{num} (оригинал) — {catalog_name} — цену и срок уточним")
    for a in alts:
        ao = a["offers"]
        who = "Motorcraft, тот же оригинал" if "motorcraft" in a["name"].lower() else "тот же оригинал"
        if ao and ao["original"]:
            x = dict(ao["original"], number=a["oem"])
            out.append(_offer_line(x, numbers, nice_name(x["description"], x["brand"], x["number"]), who))
    for rp in (o or {}).get("replacements") or []:
        if rp.get("catalog"):   # только подтверждённые каталогом по VIN: см. Engine._confirm_replacements
            out.append(_offer_line(rp, numbers, nice_name(rp["description"], rp["brand"], rp["number"]) or catalog_name,
                                   f"оригинал под другим номером {_key(rp['number'])}"))
    more = 0
    if o and o["analogs"]:
        shown = o["analogs"][:analogs]
        out += [_offer_line(a, numbers, fallback=catalog_name) for a in shown]
        more = o["stats"]["articles"] - (1 if o["original"] else 0) - len(shown)
    return out, max(more, 0)


def need_text(var: dict) -> str:
    """Сколько штук нужно — в каждом ответе по каждой позиции: каталог пишет количество на машину («2», «01»); нет данных —
    говорим честно, а не молчим (клиент не понимал, сколько покупать, когда у одних позиций число было, а у других нет)."""
    m = re.match(r"\d+", var.get("amount") or "")
    if not m:
        return "количество на машину по каталогу не указано — уточним"
    k = int(m.group(0))
    if k > 1:
        text = f"на машину нужно {k} шт., цены за штуку"
    elif var.get("lr") in ("left", "right"):
        text = "1 шт. на эту сторону"   # вторая сторона — отдельной строкой
    else:
        text = "на машину нужна 1 шт."
    return f"левый и правый одинаковые, {text}" if var.get("pair") else text


def quantity_summary(positions: list[dict]) -> list[str]:
    """Итог в конце ответа: сколько чего нужно купить. Стороны и оси складываем, варианты одной стороны — не складываем."""
    out = []
    for p in positions:
        groups = [v for v in p.get("variants", []) if not v.get("alt")]
        if p.get("status") not in ("found", "choose") or not groups:
            continue
        title = p["query"][:1].upper() + p["query"][1:]
        counts = [int(m.group(0)) for v in groups if (m := re.match(r"\d+", v.get("amount") or ""))]
        sides = [(v.get("axis", ""), v.get("lr", "")) for v in groups]
        axes = {a for a, _ in sides if a}
        if len(counts) != len(groups):
            out.append(f"• {title} — количество уточним по каталогу")
        elif p.get("parts") and p.get("status") == "found":
            out.append(f"• {title} — {sum(counts)} шт. (все детали узла, по позициям выше)")   # разные детали — складываем
        elif len(axes) > 1:
            # И передние, и задние показаны потому, что клиент не сказал, какие нужны: складывать оси нельзя
            per_axis = set(counts)
            out.append(f"• {title} — по {min(counts)} шт. на ось: нужны передние или задние?" if len(per_axis) == 1
                       else f"• {title} — зависит от оси: нужны передние или задние?")
        elif len(set(sides)) == len(sides):
            note = " на одну сторону" if len(groups) == 1 and groups[0].get("lr") in ("left", "right") else ""
            out.append(f"• {title} — {sum(counts)} шт.{note}")
        else:
            out.append(f"• {title} — {min(counts)} шт. (количество не зависит от варианта: выберите нужный)")
    return out if len(out) >= 2 else []


def requirement_lines(p: dict) -> list[str]:
    """Что клиент просил кроме детали: сколько штук и признаки («иридиевые», «белый») — и что с этим нашлось."""
    out = []
    k = p.get("want_qty")
    prices = [x["price"] for var in p.get("variants", []) for x in
              ([(var.get("offers") or {}).get("original")] + list((var.get("offers") or {}).get("analogs") or [])) if x and x.get("price")]
    if k and prices and len(p.get("variants", [])) == 1:
        out.append(f"   Вы просили {k} шт.: цены ниже за штуку, на {k} шт. — от {money(min(prices) * k)}.")
    elif k:
        out.append(f"   Вы просили {k} шт.: цены ниже за штуку.")
    chk = p.get("attr_check") or {}
    if chk.get("matched"):
        out.append(f"   Признак «{', '.join(chk['matched'])}» указан в описании предложений — они показаны первыми.")
    if chk.get("missing"):
        out.append(f"   Признак «{', '.join(chk['missing'])}» в описаниях предложений не указан — уточним у поставщиков и напишем.")
    return out


def attach_schemes(res: dict) -> None:
    """Каталог дал несколько разных деталей, а выбрать может только клиент («трубка кондиционера»: от компрессора или от
    радиатора). Пишем «уточните по схеме», и раньше клиент схемы не видел. Теперь — ссылка на картинку узла и номера позиций
    на ней: клиент отвечает номером. Только если у каждой детали свой номер на схеме (у вариантов одной позиции под
    разные модификации номер один — схема их не различит)."""
    for p in res.get("positions", []):
        p.pop("schemes", None)
        if p.get("status") != "choose" or p.get("parts"):
            continue
        main = [x for x in p.get("variants", []) if not x.get("alt")]
        sch = [x.get("scheme") or {} for x in main]
        codes = [str(sc.get("code") or "").strip() for sc in sch]
        if len(main) < 2 or not all(codes) or not all(sc.get("image") for sc in sch) or len(set(codes)) != len(codes):
            continue
        by: dict[str, dict] = {}
        for x, sc, code in zip(main, sch, codes):
            b = by.setdefault(sc["image"], {"unit": x.get("unit", ""), "image": SC.link(sc["image"]), "codes": []})
            b["codes"].append({"code": code, "oem": x["oem"], "name": x["name"]})
        p["schemes"] = list(by.values())


_SCHEME_WORD = re.compile(r"(?:поз\w*|№|номер\w*|n)\.?\s*$", re.I)


def scheme_pick(text: str, positions: list[dict]) -> tuple[int, str] | None:
    """Номер со схемы в ответе клиента → (номер позиции в памяти, номер на схеме). Короткие номера («2», «7») принимаем
    только в короткой реплике или после «поз.», «№», «номер»: иначе «2 штуки» станет выбором."""
    asked = [(i, {str(c).upper() for sc in p.get("schemes") or [] for c in sc.get("codes", [])})
             for i, p in enumerate(positions) if p.get("schemes")]
    if not asked:
        return None
    for m in re.finditer(r"[0-9A-Za-zА-Яа-я]+", text):
        tok = m.group(0).upper()
        for i, codes in asked:
            if tok not in codes:
                continue
            explicit = bool(_SCHEME_WORD.search(text[:m.start()]))
            if len(tok) >= 4 or explicit or len(text.strip()) <= 12:
                return i, tok
    return None


def draft(res: dict, numbers: bool = False, analogs: int = 3) -> str:
    """Ответ клиенту. По позиции: при нескольких вариантах — блоки «Передний:» / «Задний:», в каждом
    «• Фирма — наименование — цена, срок», оригинал первым, дальше аналоги — что привезём быстрее."""
    st = res["status"]
    if st == "no_vin":
        return ("Пришлите, пожалуйста, VIN (17 знаков) или frame — номер кузова японских машин, например GX110-1234567. "
                "Проверим по вашей машине.")
    if st == "vehicle_not_found":
        req = res.get("request") or {}
        if re.match(r"[ZX]", req.get("ident", "")) and \
                re.search(r"ssang|санг|саньен|санйон|korando|kyron|actyon|rexton|корандо|кайрон|актион|рекстон",
                          (req.get("model", "") + " " + " ".join(req.get("chunks", []))), re.I):
            # SsangYong российской сборки: каталог знает только корейский VIN (начинается на K)
            return ("У SsangYong российской сборки VIN в каталоге не ищется. Пришлите, пожалуйста, корейский VIN — "
                    "он начинается на букву K: в ПТС в «Особых отметках» (номер шасси) или на табличке "
                    "в проёме водительской двери.")
        if "-" in req.get("ident", ""):
            # Номер кузова (японские машины): Daihatsu L275V-1003754 в каталоге нет — про VIN и 17 знаков тут не к месту
            return ("По этому номеру кузова машина в каталоге не нашлась — возможно, в номере опечатка или такой модели "
                    "там нет. Напишите, пожалуйста: марку и модель, год выпуска, объём двигателя и коробку (автомат или "
                    "механика) — найдём по ним. Менеджер подберёт вручную и напишет, если описать машину не получится.")
        return ("По этому VIN машина в каталоге не нашлась — возможно, в VIN опечатка или такой модели там нет. "
                "Напишите, пожалуйста: марку и модель, год выпуска, объём двигателя (например, 1.6; бензин или дизель) "
                "и коробку (автомат или механика) — найдём по ним. Или пришлите фото СТС, либо проверьте VIN (17 знаков). "
                "Менеджер подберёт вручную и напишет, если описать машину не получится.")
    if st == "car_mismatch":
        m = res["mismatch"]
        return (f"VIN в сообщении — от машины {m['vin_car'].rstrip('.')}, а в тексте вы пишете про {m['said_car']} («{m['said']}»). "
                f"Уточните, пожалуйста, для какой машины нужна деталь. Если для {m['said_car']} — пришлите её VIN (17 знаков). "
                f"Если для {m['vin_car'].split(',')[0]} — ответьте «для неё», и подберём.")
    if st == "catalog_error":
        return "Каталог сейчас не отвечает, подберём вручную."
    if st == "no_quick_groups":
        v = res["vehicle"]
        return (f"Ваш автомобиль: {(v.get('short') or v['summary']).rstrip('.')}.\n"
                "По этой машине каталог не даёт быстрый поиск деталей — менеджер подберёт вручную и напишет.")
    if st == "choose_vehicle":
        lines = ["По вашим словам нашлось несколько вариантов машины, уточните ваш (VIN каталог не знает):"
                 if res.get("car_text") else "По VIN нашлось несколько вариантов машины, уточните ваш:"]
        lines += [f"{i + 1}) {x['summary']}" for i, x in enumerate(res["vehicles"])]
        return "\n".join(lines)
    if st == "no_positions":
        v = res.get("vehicle") or {}
        car = (v.get("short") or v.get("summary") or "").rstrip(".")
        return (f"Машину нашли{': ' + car if car else ''}. Напишите, какая деталь нужна — название, номер "
                f"или ссылку на товар, проверим по VIN.")
    if st == "chat":
        return ""   # не про подбор: оплата, адрес, «когда забрать» — отвечает менеджер
    if st in ("order", "answer"):
        return reply_text(res["reply"], numbers)
    v = res["vehicle"]
    if res.get("followup"):
        # продолжение разговора: без приветствия и машины, они уже были; ответ про показанное — первым
        lines = [reply_text(res["reply"], numbers)] if res.get("reply") else []
    else:
        lines = ["Здравствуйте! Подобрали запчасти по VIN для вашего автомобиля:", v.get("short") or v["summary"]]
    lines += [f"Обратите внимание: {w[0].lower() + w[1:]}." for w in res["warnings"]]
    found = 0
    order = {"part": 0, "": 0, "fluid": 1, "battery": 2, "tire": 3, "tool": 4, "chemistry": 4, "accessory": 4}
    # «Задок: … Передок: …» — запчасти так же, по частям машины, в порядке клиента
    axis_of = lambda p: (p.get("side") or {}).get("axis", "") if order.get(p.get("kind", ""), 0) == 0 else None  # noqa: E731
    axes = [axis_of(p) for p in res["positions"] if axis_of(p) is not None]
    by_axis = len(axes) >= 3 and {"front", "rear"} <= set(axes)
    first = {a: axes.index(a) for a in set(axes)}
    shown_positions = sorted(res["positions"], key=lambda p: (order.get(p.get("kind", ""), 0),
                                                             first.get(axis_of(p), 0) if by_axis else 0))
    sections = len({order.get(p.get("kind", ""), 0) for p in shown_positions}) > 1
    section, part = None, None
    for i, p in enumerate(shown_positions, 1):
        query = p["query"]
        sec = order.get(p.get("kind", ""), 0)
        if sections and sec != section:
            lines += ["", KIND_TITLE[sec] + ":"]
            section = sec
        if by_axis and sec == 0 and axis_of(p) != part:
            part = axis_of(p)
            lines += ["", AXIS_TITLE[part]]
        lines += ["", f"{i}) {query[:1].upper() + query[1:]}"]
        lines += requirement_lines(p)
        if p.get("article"):
            lines.append("   " + article_line(p, numbers))
            if p["status"] == "not_found" and (p["article"].get("catalog") or {}).get("status") == "FULLY":
                continue   # каталог подтвердил номер — «не смогли подобрать деталь» рядом было бы противоречием
        if p.get("kind") in KIND_NOTE and p["status"] == "not_found":
            lines.append("   " + KIND_NOTE[p["kind"]])
            continue
        if p["status"] == "not_found":
            if p.get("kind") == "fluid":
                lines.append("   Жидкости и масла подбирает менеджер по допуску производителя и объёму заправки — передали ему, он напишет вам с ценами.")
            elif "не деталь каталога" in p["note"]:
                lines.append("   Это не деталь автомобиля — передали менеджеру, он подберёт по названию и напишет.")
            elif p["note"].startswith("В каталоге в этой группе нашёлся только"):
                lines.append("   " + p["note"])   # «нашёлся только внутренний вариант — нужный уточним»
            elif "Уточните, пожалуйста, что именно нужно" in p["note"] or "это только признак" in p["note"]:
                lines.append("   " + p["note"])   # «бачок», «комплект»: деталь не названа — спрашиваем, а не гадаем
            elif p.get("dropped"):
                lines.append("   Не смогли уверенно подобрать эту деталь по каталогу — передали менеджеру, он уточнит и напишет.")
            else:
                lines.append("   В каталоге для вашей машины сразу не нашли — уточним и напишем.")
            continue
        llm = p.get("llm") or {}
        if llm.get("note", "").startswith("вместо «"):
            lines.append(f"   Вариант {llm['note']}.")
        if llm.get("uncertain"):
            lines.append("   Под вопросом — подберём, если понадобится.")
        if p["question"]:
            lines.append("   " + p["question"].replace(
                "В каталоге несколько вариантов — уточните по примечанию или по номеру позиции на схеме.",
                "Есть несколько вариантов, уточните, какой нужен:"))
        for sc in p.get("schemes") or []:
            unit = ru_name(sc["unit"]) if sc.get("unit") else "узла"
            lines.append(f"   Схема «{unit}»: {sc['image']}")
            lines.append("   На схеме: " + "; ".join(f"поз. {c['code']} — {ru_name(base_name(c['name']))}" for c in sc["codes"]) + ".")
        if p.get("schemes"):
            lines.append("   Напишите номер позиции со схемы — покажем цены именно на неё.")
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
        side_order = {"front": 0, "": 1, "rear": 2}   # не «order»: тот — порядок разделов, нужен следующим позициям
        def pos_no(var: dict) -> int:   # детали узла — по номеру позиции на схеме: 1, 7, 10, 11, а не 1, 10, 11, 7
            m = re.match(r"\d+", str((var.get("scheme") or {}).get("code") or "")) if p.get("parts") else None
            return int(m.group(0)) if m else 0
        groups.sort(key=lambda g: (side_order.get(g[0]["axis"], 1), {"left": 0, "": 1, "right": 2}.get(g[0]["lr"], 1),
                                   pos_no(g[0])))
        many = len(groups) > 1
        labels = [T.side_label(query, var["axis"], var["lr"]) for var, _ in groups]
        seen: dict[str, int] = {}
        diffs = note_diffs([g[0] for g in groups]) if many else []
        # Как каждый вариант назван в ответе («Задние, вариант 2») — чтобы объяснение ссылалось на то же
        called, cnt = {}, {}
        code = lambda var: str((var.get("scheme") or {}).get("code") or "").strip()  # noqa: E731
        for n, (var, _) in enumerate(groups, 1):
            side = labels[n - 1]
            if not side or labels.count(side) > 1:
                cnt[side] = cnt.get(side, 0) + 1
                called[var["oem"]] = (f"поз. {code(var)}" if p.get("parts")
                                      else f"вариант {cnt[side]}" if side else f"вариант {n}")
            else:
                called[var["oem"]] = side.lower()
        if many and p.get("differ"):
            lines += ["   " + x for x in differ_lines(p["differ"], called, numbers)]
        for n, (var, alts) in enumerate(groups, 1):
            found += 1
            rows, more = _block(var, alts, numbers, analogs if not many else min(analogs, 2), query)
            per = need_text(var)
            if many:
                side = labels[n - 1]
                if not side or labels.count(side) > 1:
                    # Несколько вариантов на одной стороне: «Задняя, вариант 2 — «Скоба»»
                    seen[side] = seen.get(side, 0) + 1
                    name = ru_name(base_name(var["name"]))
                    if p.get("parts"):
                        # Разные детали узла: номер позиции на схеме — по нему клиент и скажет, что нужно
                        side = (f"{side}, поз. {code(var)}" if side else f"Поз. {code(var)}") + f" — «{name}»"
                    else:
                        side = (f"{side}, вариант {seen[side]}" if side else f"Вариант {n}") + f" — «{name}»"
                lines += ["", f"   {side}" + (f" ({per})" if per else "") + ":"]
                if diffs[n - 1]:
                    lines.append(f"   Отличие по каталогу: {diffs[n - 1]}.")   # размер, фирма, комплектация из примечаний
            elif per:
                lines.append(f"   {per[:1].upper() + per[1:].rstrip('.')}.")   # «1 шт.» — без второй точки
            lines += ["   " + r for r in rows]
            if more:
                lines.append(f"   Есть ещё {more} {plural(more, 'вариант', 'варианта', 'вариантов')} — подберём под бюджет.")
    if not res.get("followup") and res.get("reply"):
        extra = reply_text(res["reply"], numbers)   # «Фильтр воздушный — есть: Mann …» или «фирмы … нет»
        if extra:
            lines += ["", extra]
    lines.append("")
    summary = quantity_summary(shown_positions)
    if summary:
        lines += ["", "Сколько нужно купить на машину:"] + summary
    said = " ".join((res.get("request") or {}).get("chunks") or [])
    ordered = bool(D.STATUS.search(said) or _ORDERED.search(said))   # «уже заказал», «оплачено»: оформлять нечего
    only_confirmed = bool(res["positions"]) and all((p.get("article") or {}).get("catalog", {}) and
                                                    p["article"]["catalog"].get("status") == "FULLY" for p in res["positions"])
    if found or not only_confirmed:   # одни подтверждённые каталогом номера: «уточним по позициям» было бы лишним
        lines.append(("Цены и сроки на сегодня." if ordered else "Цены и сроки на сегодня. Напишите, какие позиции оформить — закажем.") if found
                     else "Уточним по позициям и напишем.")
    lines.append(ANYTHING_ELSE)
    return "\n".join(lines).strip("\n")


def article_line(p: dict, numbers: bool = False) -> str:
    """Что с номером клиента: подходит (оригинал или аналог оригинала по VIN), не значится или не нашли вовсе."""
    a = p["article"]
    who = ""   # фирма «Вы искали» — по самому частому описанию (у HU7008Z выходила «Foton»): клиенту не показываем
    cat = a.get("catalog") or {}
    where = f" (раздел «{cat['where'][0]}»)" if cat.get("where") else ""
    fit = a.get("fit")
    if cat.get("status") == "FULLY" and not (fit and fit.get("original")):
        # Каталог по VIN сам говорит «подходит» — самое надёжное; аналоги поставщиков для этого не нужны
        return f"Номер {a['number']} подходит вашей машине — это подтверждает каталог по VIN{where}."
    if not a.get("desc"):
        if cat.get("status") == "NONAPPLICABLE" and a.get("oem"):
            return f"Номера {a['number']} в каталоге вашей машины по VIN нет — проверим, не заменён ли он, и напишем."
        return f"Номер {a['number']} у поставщиков не нашли — проверим вручную и напишем."
    if fit and fit.get("original"):
        return f"Номер {a['number']}{who} — оригинал для вашей машины по VIN, подходит."
    if cat.get("status") == "PARTIAL":
        return (f"Номер {a['number']}{who} возможно подойдёт: в каталоге этой модели он есть{where}, но машину мы нашли без VIN, "
                f"комплектацию не проверили. Пришлите VIN или фото СТС — подтвердим.")
    if fit:
        orig = f" {fit['oem']}" if numbers else ""
        return f"Номер {a['number']}{who} подходит: поставщики ведут его как аналог оригинала{orig} для вашей машины."
    if p.get("variants") and a.get("oem"):
        if cat.get("status") == "NONAPPLICABLE":
            return (f"Номер {a['number']} — оригинальный, но в каталоге вашей машины по VIN его нет: либо не подходит, либо "
                    f"заменён новым номером. Ниже — что ставится по VIN.")
        return (f"Номер {a['number']} — оригинальный, но для вашей машины по VIN каталог даёт другой номер: проверим, "
                f"не замена ли это. Ниже — что ставится по VIN.")
    if p.get("variants"):
        return (f"Номер {a['number']}{who} среди аналогов оригинала для вашей машины не значится — скорее всего "
                f"не подойдёт. Ниже — что ставится по VIN.")
    return f"Номер {a['number']}{who} — «{a['desc'][:50]}»: по каталогу вашей машины сразу не нашли, проверим вручную."


def differ_lines(d: dict, called: dict[str, str], numbers: bool = False) -> list[str]:
    """«Почему несколько вариантов и как выбрать» — по фактам из _explain."""
    a, b = d["oems"]
    na, nb = called.get(a, "первый вариант"), called.get(b, "второй вариант")
    out = []
    if d.get("eco") and len(d["eco"]) < 2:
        # VAG: «JZW…», «…'ECO'» — не загадка: та же деталь от VAG, только бюджетная линейка
        e = d["eco"][0]
        out.append(f"{called.get(e, 'Вариант').capitalize()} — линейка VAG Economy: та же деталь от VAG, дешевле, "
                   f"ставится на то же место.")
        return out
    if d.get("siblings"):
        s = d["siblings"]
        # Соседи берутся только среди суппортов (differ._DECIDES): у VAG это «Корпус» суппорта — пишем по смыслу
        out.append(f"Почему несколько вариантов: по каталогу у этой модели {s['count']} "
                   f"{plural(s['count'], 'вариант', 'варианта', 'вариантов')} суппорта, "
                   f"а какой стоит на вашей машине, по VIN не видно.")
    elif d.get("vin"):
        out.append("Почему несколько вариантов: по VIN каталог их не различает — обе подходят к вашей машине.")
    if d.get("same") is False:
        out.append(f"Это разные детали, а не новый номер старой: общих аналогов {d['common']} из {d['total']}.")
    elif d.get("same") is True:
        out.append(f"По аналогам это почти одна деталь ({d['common']} общих из {d['total']}) — "
                   f"подойдёт любой вариант, выбирайте по цене и сроку.")
    for oem, w in d.get("with", {}).items():
        # «Пружина прижимная тормозного суппорта CHEVROLET LACETTI» — без марки в хвосте
        what = re.sub(r"(?:\s+[A-Z][A-Z0-9().,/-]*)+\s*$", "", w.get("name") or "").strip(" ,.")
        num = f" {w['number']}" if numbers or not what else ""
        out.append(f"{called.get(oem, 'Вариант').capitalize()} ставится вместе с отдельной деталью"
                   + (f" «{what}»" if what else "") + f"{num} — её тоже нужно заказать.")
    hints = [(called.get(o, ""), h) for o, h in d.get("hints", {}).items() if h]
    if hints:
        out.append("По описаниям поставщиков: " + "; ".join(
            f"{name} — " + ", ".join(f"«{x}»" for x in h) for name, h in hints) + ".")
    if d.get("same") is not True and out:
        feats = [x for _, h in hints for x in h if x.split()[0] in ("без", "с", "со")]
        look = f" — например, {' или '.join('«' + x + '»' for x in feats[:2])}" if feats else ""
        out.append(f"Как выбрать: сравните со снятой деталью{look}, или пришлите её фото — подскажем.")
    return out


# Что в примечании каталога клиент может сравнить сам: размер («314x25mm», «Ø280», «300 мм») и комплектация.
# Остальное (коды PR, номера лет, названия заводов) — шум, его не показываем
_ORDERED = re.compile(r"уже\s+(?:заказ|оплат)|оплачен|заказал\w*|когда\s+(?:придёт|придет|ожидать)", re.I)
_NOTE_SIZE = re.compile(r"(?:Ø|d\s*=\s*)?\d{2,3}\s*[xх×]\s*\d{1,3}(?:\s*(?:mm|мм))?|(?:Ø|d\s*=\s*)\d{2,3}(?:\s*(?:mm|мм))?|\b\d{3}\s*(?:mm|мм)\b", re.I)
_NOTE_KIT = re.compile(r"спортивн\w*|усилен\w*|с\s+датчик\w*|без\s+датчик\w*|с\s+abs|без\s+abs|4x4|4wd|полный\s+привод|"
                       r"невентилир\w*|вентилир\w*|с\s+индик\w*|без\s+индик\w*", re.I)   # «Тормозной диск (вентилир.)»; не «вентилятор» (Nissan, 12.10)


def note_diffs(variants: list[dict]) -> list[str]:
    """Чем варианты отличаются в примечаниях каталога: размер и комплектация. Названия варианты и так показывают;
    «Отличие по каталогу: 314x25mm» против «300x12» помогает клиенту выбрать, не гадая по схеме."""
    def facts(v: dict) -> list[str]:
        text = " ".join(str(v.get(k) or "") for k in ("note", "unit_note", "name"))
        out = []
        for rx in (_NOTE_SIZE, _NOTE_KIT):
            for m in rx.finditer(text):
                f = re.sub(r"\s+", " ", m.group(0)).strip().lower()
                f = re.sub(r"^(не)?вентил\w*", lambda x: (x.group(1) or "") + "вентилируемый", f)   # «вентилир.»
                if f not in out:
                    out.append(f)
        return out
    per = [facts(v) for v in variants]
    units = [re.sub(r"\(\d+\)|\s+", " ", str(v.get("unit") or "")).strip().lower() for v in variants]
    # Узел VAG («поперечный рычаг поворотный кулак d - 05.01.2009>>*») — служебная строка, клиенту не отличие
    units = [u if u and not re.search(r"\d|>>|\*", u) and len(u) <= 40 else "" for u in units]
    if len(set(units)) > 1:
        # Колодки Lacetti: две в «Заднем тормозе (дисковом)», третья — в «Стояночном тормозе»: узел пишем только у той,
        # что не там, где большинство
        usual = collections.Counter(units).most_common(1)[0][0]
        per = [x + ([f"узел «{u}»"] if u and u != usual else []) for x, u in zip(per, units)]
    common = set.intersection(*(set(x) for x in per)) if per else set()
    res = [" ".join(w for w in x if w not in common)[:60] for x in per]
    return res if sum(1 for x in res if x) >= 1 and len(set(res)) > 1 else [""] * len(per)


# Общие слова, которые сами по себе не называют деталь: ключ — основа слова, значение — примеры для вопроса клиенту
VAGUE_NOUNS = {T.stem(k): v for k, v in {
    "комплект": "например, комплект ГРМ, сцепления, тормозных колодок", "набор": "например, набор ГРМ или колодок",
    "ремкомплект": "например, ремкомплект суппорта, рулевой рейки, ШРУСа", "цилиндр": "главный тормозной, рабочий тормозной, сцепления",
    "подшипник": "ступичный, выжимной, натяжного ролика, подвесной", "датчик": "ABS, коленвала, кислорода, температуры",
    "насос": "водяной, топливный, ГУР, масляный",
    "прокладка": "ГБЦ, клапанной крышки, поддона", "крышка": "клапанная, бензобака, расширительного бачка",
    "бачок": "расширительный, омывателя, тормозной жидкости, ГУР", "шланг": "радиатора, ГУР, тормозной", "патрубок": "радиатора, впускной, термостата", "деталь": "название детали",
    "запчасть": "название детали", "элемент": "название детали",
}.items()}
def lost_words(q: list[str], tree: TreeIndex, g, stop: frozenset[str] = frozenset()) -> list[str]:
    """Существительные запроса, которых нет ни в названии выбранной группы, ни в её синонимах:
    «прокладка крышки головки» → «Прокладка головки цилиндра» теряет «крышки» — это другая деталь."""
    have = [s for p, _ in g.phrases for s in p] + T.stems(g.name, stop)
    known = tree.known(q)
    return [s for s in known if s not in T.ADJ and s not in _LOST_IGNORE and not any(T.same(s, h) for h in have)]


# Слова, без которых деталь та же: «прокладка выпускного коллектора двигателя», «клапан системы вентиляции»
_LOST_IGNORE = {T.stem(w) for w in ("двигателя", "двигатель", "системы", "система", "включения", "автомобиля", "машины", "сборе", "сборка",
                                    "стекла", "комплект")}


def _word_in(w: str, words) -> bool:
    """Слово — одно из общих: «прокладок» и «прокладка» — одно слово, а основа у них разная."""
    return bool(w) and any(T.same(w, k) for k in words)


# ---------- требования клиента: количество и признаки ----------

_QTY_WORDS = {"два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10}
_QTY_NOT = re.compile(r"^(?:год\w*|лет|дн\w*|день|месяц\w*|час\w*|раз\w*|тысяч\w*|литр\w*|км|минут\w*|недел\w*|суток|сутки|"
                      r"поколени\w*|двер\w*|мест\w*|цилиндр\w*|ступен\w*)$")
_QTY_PCS = re.compile(r"(?<![\d.,/-])(\d{1,2})\s*(?:шт\b|шт\.|штук\w*|пар(?:а|ы|у)?\b)", re.I)
_QTY_X = re.compile(r"(?:^|\s)[xх×]\s*(\d{1,2})(?!\d)", re.I)
_QTY_WORD = re.compile(r"\b(" + "|".join(_QTY_WORDS) + r")\s+((?:[а-яё]+\s+)?[а-яё]{4,})", re.I)


_PCS_GLUE = re.compile(r"(?<![\d.,/-])(\d{1,2})\s+(?:шт\.?|штук\w*)(?![а-яё])", re.I)


def want_qty(text: str) -> int | None:
    """Сколько штук просил клиент: «2 шт», «два ролика», «по 2шт», «x2». Год, объём, число дверей — не количество."""
    m = _QTY_PCS.search(text) or _QTY_X.search(text)
    if m and 2 <= int(m.group(1)) <= 40:
        return int(m.group(1))
    m = _QTY_WORD.search(text)
    if m and not _QTY_NOT.match(m.group(2).split()[-1].lower()) and not _QTY_NOT.match(m.group(2).split()[0].lower()):
        return _QTY_WORDS[m.group(1).lower()]
    return None


# Признаки, которые клиент называет и которые пишут в описаниях поставщиков: ключ — начало слова
_REQ_ATTRS = ("иридиев", "платинов", "полиуретанов", "керамическ", "перфорирован", "усилен", "спортивн", "хромирован",
          "подогрев", "обогрев", "бесключев", "антикоррозийн", "белый", "белая", "белое", "белые", "чёрный", "черный", "чёрная", "черная",
          "серебрист", "красный", "красная", "синий", "синяя")
_REQ_ATTR_RX = re.compile(r"(?<![а-яё])(" + "|".join(_REQ_ATTRS) + r")[а-яё]*", re.I)


def strip_qty(text: str) -> str:
    """Слова о количестве — не часть названия детали: «иридиевые свечи 4 шт» → «иридиевые свечи»."""
    t = _QTY_PCS.sub(" ", _QTY_X.sub(" ", text))
    m = _QTY_WORD.search(t)
    if m and want_qty(text):
        t = t[:m.start(1)] + t[m.end(1):]
    return re.sub(r"\s+", " ", t).strip(" ,.")


# Те же признаки латиницей — в описаниях поставщиков они чаще так: «NGK Iridium», «Ceramic», «Sport»
_ATTR_LATIN = {"иридиев": "iridi", "платинов": "platin", "керамическ": "ceram", "спортивн": "sport", "перфорирован": "perfor|drill",
               "усилен": "reinforc|heavy", "подогрев": "heated|heating", "обогрев": "heated|heating", "хромирован": "chrom"}


def want_attrs(text: str) -> list[tuple[str, str]]:
    """Признаки из слов клиента: [(как написал, начало слова для поиска в описаниях)] — «иридиевые», «белый», «с подогревом»."""
    out = []
    for m in _REQ_ATTR_RX.finditer(text):
        label, key = m.group(0).lower(), m.group(1).lower().replace("ё", "е")
        key = key[:3] if key.startswith(("бел", "черн", "красн", "син")) else key   # цвет: «белый» = «белая» = «белых»
        if (label, key) not in out:
            out.append((label, key))
    return out


def check_attrs(pos: dict) -> None:
    """Сверить признаки клиента с описаниями предложений: нашлись — такие предложения идут первыми, нет — скажем честно."""
    wants = pos.get("want_attrs") or []
    if not wants or not pos.get("variants"):
        return
    seen: dict[str, bool] = {k: False for _, k in wants}
    for var in pos["variants"]:
        o = var.get("offers") or {}
        rows = ([o["original"]] if o.get("original") else []) + list(o.get("analogs") or [])
        def has(r: dict, k: str) -> bool:
            d = str(r.get("description") or "").lower().replace("ё", "е")
            return bool(re.search(r"(?<![а-яё])" + re.escape(k), d) or (k in _ATTR_LATIN and re.search(_ATTR_LATIN[k], d)))
        for _, k in wants:
            if any(has(r, k) for r in rows):
                seen[k] = True
        if o.get("analogs"):
            o["analogs"] = sorted(o["analogs"], key=lambda r: not all(has(r, k) for _, k in wants if seen[k]))
    pos["attr_check"] = {"matched": [lab for lab, k in wants if seen[k]], "missing": [lab for lab, k in wants if not seen[k]]}


MAX_ARBITER = 3     # не больше трёх вызовов модели на сообщение
UNIT_VARIANTS = 14   # сколько деталей узла проверяем по ценам, чтобы найти главную
UNIT_SCORE = 0.55   # название узла полного каталога совпало с запросом настолько, чтобы показывать найденное
GATE_MARGIN = 0.3   # запас «не-детали» над «деталью», с которого замок отменяет выбор группы (эталон: исправлено 12, испорчено 2)
SURE_SCORE = 0.8    # правила уверены (эталон 08.10: при ≥0.8 первая группа верна в 142 случаях из 156)
_ID_MODS = ("главн", "основн")   # слова в названии узла, которые превращают деталь в другую (главный цилиндр ≠ рабочий)

_ADJ_DEFAULT = {"топливн": "топливный фильтр", "маслян": "масляный фильтр", "воздушн": "воздушный фильтр",
                "салон": "салонный фильтр"}   # одно прилагательное без детали: самое частое значение

_CAR_RU = {"рено": "renault", "мерс": "mercedes", "мерседес": "mercedes", "бмв": "bmw", "киа": "kia", "кия": "kia",
           "хундай": "hyundai", "хендай": "hyundai", "хьюндай": "hyundai", "тойота": "toyota", "шкода": "skoda",
           "фольксваген": "volkswagen", "фольц": "volkswagen", "ниссан": "nissan", "мицубиси": "mitsubishi",
           "митсубиси": "mitsubishi", "шевроле": "chevrolet", "ситроен": "citroen", "пежо": "peugeot", "вольво": "volvo"}


def _without_car(query: str, v: Vehicle) -> str:
    """Марка и модель машины из самой фразы клиента («свечи для киа пиканто 2» → «свечи»): они не часть названия детали, и
    правила без модели считали их потерянными словами и отдавали позицию менеджеру. Сравниваем на слух (D._sound):
    «пиканто» и Picanto, «дастер» и Duster. Предлог перед убранным словом уходит вместе с ним."""
    car = [D._sound(t) for t in re.findall(r"[A-Za-z]{3,}", f"{v.brand} {v.name}")]
    car = [c for c in car if len(c) >= 3]
    if not car:
        return query
    kill: set[int] = set()
    toks = list(re.finditer(r"[A-Za-zА-Яа-яЁё]+", query))
    for i, m in enumerate(toks):
        w = m.group(0)
        if len(w) < 3:
            continue
        snd = D._sound(_CAR_RU.get(w.lower(), w))
        if len(snd) >= 3 and any(snd == c or (len(snd) >= 4 and SequenceMatcher(None, snd, c).ratio() >= 0.8) for c in car):
            kill.add(i)
    if not kill:
        return query
    rest = query
    for i in sorted(kill, reverse=True):
        m = toks[i]
        rest = rest[:m.start()] + rest[m.end():]
    rest = re.sub(r"\s+", " ", rest).strip(" ,;.")
    # За маркой тянутся «для», поколение («пиканто 2») и объём («1.5») — хвост без детали
    rest = re.sub(r"(?:\s+(?:для|на|от|к|по|\d{1,2}(?:[.,]\d)?))+$", "", rest, flags=re.I).strip(" ,;.")
    return rest if T.stems(rest, frozenset()) else query


WEAK_SCORE = 0.65   # оценка группы каталога ниже — результат правил слабый (замер 07.10.2026: хорошие ≥0.69)
_PRICE_TAIL = re.compile(r"[\s,]*(?:сколько\s+)?(?:стоят|стоит|стоимость|цена|почем|почём)\s*[?.!]*\s*$", re.I)
_BRAKE = re.compile(r"диск|колодк|барабан|суппорт|тормоз", re.I)
_DISC = re.compile(r"диск\w*\s+тормоз|тормозн\w*\s+диск|^диск", re.I)
_SPARK = re.compile(r"свеч\w*\s+(?:\w+\s+)?зажиган|зажиган\w*\s+свеч", re.I)
_AROUND = re.compile(r"\bв\s+круг\b|\bпо\s+кругу\b|\bсо\s+всех\s+сторон\b|\bна\s+все\s+колес\w*|\bна\s+все\s+колёс\w*"
                     r"|\bвсе\s+четыре\b|\bна\s+4\s+колеса\b|\bна\s+все\s+4\b", re.I)

# Клиент часто пишет одну деталь, а нужно несколько: спросить, всё ли (менеджеры спрашивают так же)
ANYTHING_ELSE = "Что-то ещё нужно по этой машине?"
AXIS_TITLE = {"rear": "Сзади:", "front": "Спереди:", "": "Остальное:"}


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
    if r.get("clarify"):
        lines += [r["clarify"], ""]
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
        elif t == "spec":
            name = a["query"][:1].upper() + a["query"][1:]   # без стороны в заголовке: она в самом ответе
            lines.append(f"{name}: {a.get('why', '')}")
        elif t == "none_no_analogs":
            lines.append(f"{title}: аналогов у поставщиков по этому номеру сейчас нет — только оригинал. "
                         f"Поищем другие фирмы и напишем.")
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
            if x.get("kind") == "fluid" and not x.get("qty"):
                # Масло в каталоге — канистра 1 л или 5 л: «оформить одну» — не замена масла
                lines.append("   Сколько литров нужно? Если объём не знаете — подскажем.")
            elif not x.get("qty") and k and int(k.group(0)) > 1:
                lines.append(f"   Цена за штуку, на машину нужно {int(k.group(0))} — сколько штук оформить?")
        lines.append(f"Итого: {money(total)}. " + ("Всё в наличии." if longest <= 0 else f"Срок — {when(longest)}."))
        lines.append(ANYTHING_ELSE)
    for q in r.get("unclear", []):
        lines.append(f"По позиции «{q}» напишите, какой вариант оформить — фирму или цену.")
    if r.get("kind") == "order" and not picks and not r.get("unclear"):
        lines.append("Напишите, какой вариант оформить — фирму или цену.")
    return "\n".join(lines).strip("\n")
