"""Ассистент «Кирилл»: три агента поверх робота подбора.

1. Расшифровщик — знает устройство машины: сумбур клиента → точный список деталей (ось, сколько, «под вопросом»).
2. Подборщик — по каждой детали ищет в каталоге ЭТОЙ машины инструментами робота (дерево групп, векторы, состав
   группы, детали к сборке у поставщиков) и выбирает детали со схемы.
3. Цены — ABCP, как у робота (Engine.offers + curate); проверяющий (код) отсекает чужой товар и подписывает оригинал
   маркой машины.
4. Продавец — варианты под потребность клиента (качество / оптимально / подешевле, срочно или нет) с обоснованием;
   цифры только из данных. «Качество» — уровни брендов из app/data/brand_quality.json (поставщики конвейера), а не
   гарантии магазина.

До подбора — вопросы, как у продавца (руководитель 26.10: «выявлять потребность, три вопроса до подбора»):
«надолго или подешевле», «срочно или можно подождать» и уточнения расшифровщика. Ответы — только на этот подбор,
в следующий раз спрашиваем снова (пользователь 26.10: запоминать не нужно).

Номера и цены модели не придумывают: они видят только то, что вернули инструменты. Модели получают текст без
телефонов и VIN (understand.mask). Лаборатория и замеры — /srv/podbor-lab/runs/2026-10-26-agent (26.10.2026).
"""
import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from ..podbor import emb as EM
from ..podbor import text as T
from ..podbor.engine import nice_brand
from ..podbor.offers import _desc_head, _offer, curate, days, not_the_part, oem_brand_for, original_keys
from ..podbor.understand import mask
from . import quality as Q
from .models import Gateway, OverBudget

log = logging.getLogger("assistant")

MAX_ITEMS = 15          # позиций в одном сообщении: дальше — менеджеру
SELECT_STEPS = 7        # шагов подборщика на одну деталь
HISTORY = 8             # реплик разговора в памяти

ASK_VIN = ("Пришлите, пожалуйста, VIN (17 знаков), номер кузова японских машин (например GX110-1234567) или госномер — "
           "подберу запчасти точно по вашей машине.")
NO_CAR = ("По этому номеру машина в каталоге не нашлась. Проверьте VIN (17 знаков, есть в СТС и ПТС) или пришлите "
          "госномер — либо напишите марку, модель, год и объём двигателя, передам менеджеру.")
FAILED = "Сейчас не получается подобрать автоматически — передал ваш вопрос менеджеру, он ответит здесь же."
MAX_QUESTIONS = 3       # вопросов до подбора — не больше трёх

GOALS = {"quality": "Качество", "optimal": "Оптимально", "cheap": "Подешевле"}
WHEN = {"urgent": "Срочно, 1–2 дня", "wait": "Могу подождать"}
BUTTONS = [[(v, f"goal:{k}") for k, v in GOALS.items()], [(v, f"when:{k}") for k, v in WHEN.items()],
           [("✅ Подобрать", "go")]]

DECODER = """Ты — опытный мастер-приёмщик автосервиса и продавец запчастей. Машина клиента: {car}.
Клиент пишет сумбурно, жаргоном, с сокращениями. Переведи его ПОСЛЕДНЕЕ сообщение (с учётом разговора выше) в точный
список запчастей, как их назвал бы каталог, используя знание устройства автомобиля:
- жаргон → нормальное название («плавающие» — плавающие сайлентблоки задних рычагов/цапфы, «шаров» — шаровая опора,
  «майл» — сайлентблоки рычага, «стаб» — стабилизатор, «аморт» — амортизатор, «телевизор» — рамка радиатора);
- «компл», «комплект», «+ всё что нужно» — раскрой, что обычно меняют вместе (опора стойки → подшипник опоры,
  пыльник, отбойник; ГРМ → ремень, ролики, помпа). Такие детали помечай certainty=maybe;
- «под вопросом», «по факту», «если надо» — certainty=maybe и note, что проверить при осмотре;
- ось (front/rear) и сколько штук на машину (парные детали обычно 2: левая и правая);
- не деталь (вопрос о цене, сроке, оплате, доставке, приветствие, «спасибо») — в not_parts;
- неясно, какая именно деталь («диски» — тормозные или колёсные?) — вопрос в questions, а не позиция.
Машина уже определена по VIN — VIN, кузов и марку не спрашивай.
Ответь вызовом decoded."""

SELECTOR = """Ты подбираешь одну деталь в каталоге машины {car}. Нужно: {need} (ось: {axis}; клиент писал: «{words}»).
Найди группу search_catalog (можно несколько формулировок: жаргон → каталожное название), открой show_parts и выбери
детали, которые и есть нужная (с нужной осью; левую и правую — обе). Названия и позиции бери только из show_parts.
Бывает, что производитель отдельно деталь не продаёт (сайлентблок, пыльник, втулка, шаровая — только в сборе с рычагом,
цапфой, стойкой), а у неоригинальных фирм она есть. Тогда открой сборку show_parts и вызови assembly_parts(id, сборка,
что нужно); если предложения есть — pick с via=assembly (в parts — сама сборка, в want — что нужно).
Нет детали ни в каталоге, ни у поставщиков — found=false. Потом вызови pick (не больше 6 вызовов инструментов до pick)."""

SELLER = """Ты — Кирилл, консультант магазина автозапчастей Автодруг92 (Севастополь), пишешь клиенту в чат. Машина: {car}.
Потребность клиента: {need}.
Ниже — данные подбора: по каждой детали номер каталога и предложения поставщиков: бренд, цена, срок в днях, метки,
гарантия бренда и quality — OE (поставщик этой детали на конвейер, по сути оригинал без логотипа), good (проверенный
aftermarket), eco (эконом); why — почему бренд такого уровня.
Напиши ответ клиенту:
{variants}
- детали certainty=maybe — отдельным блоком «Проверьте при осмотре» с объяснением зачем;
- found=false — честно: «передам менеджеру»;
- оставшиеся вопросы — коротко в конце; в самом конце — «Напишите, какие варианты оформить».
Цены, сроки, бренды, номера и уровни — ТОЛЬКО из данных, ничего не придумывай. Номер — только у оригинала.
Пиши по-русски, просто, без воды; выделение — **жирным**, без таблиц."""

VARIANTS = {
    "quality": "- клиенту важно КАЧЕСТВО («делаем надолго»): по каждой детали «Оригинал», «Качество» — лучший OE-поставщик "
               "из данных (объясни why), при наличии «Хорошая альтернатива» (good). Эконом (eco) не предлагай, кроме случая, "
               "когда другого нет — тогда честно скажи, что качественного аналога сейчас нет в продаже;",
    "optimal": "- по каждой детали до трёх вариантов: «Оригинал», «Оптимально» (лучшее соотношение качества и цены — "
               "предпочитай OE и good), «Эконом» (дешевле «Оптимально») — с коротким обоснованием каждого;",
    "cheap": "- клиенту важна ЦЕНА: по каждой детали «Эконом» — самый дешёвый разумный вариант (не подозрительно дешёвый), "
             "и «Чуть дороже, но надёжнее» — OE или good, если есть; оригинал — одной строкой для сравнения;",
}

T_SEARCH = {"type": "function", "function": {
    "name": "search_catalog", "description": "Найти группы деталей в каталоге этой машины по словам: id, название, путь.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}
T_PARTS = {"type": "function", "function": {
    "name": "show_parts", "description": "Детали группы каталога этой машины: название, позиция на схеме, примечание.",
    "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}}
T_ASSEMBLY = {"type": "function", "function": {
    "name": "assembly_parts",
    "description": "Предложения поставщиков детали, которую производитель отдельно не продаёт, к сборке из каталога",
    "parameters": {"type": "object", "properties": {
        "id": {"type": "string"}, "assembly": {"type": "string", "description": "название сборки из show_parts"},
        "want": {"type": "string", "description": "что нужно: сайлентблок, пыльник, втулка…"}},
        "required": ["id", "assembly", "want"]}}}
T_PICK = {"type": "function", "function": {
    "name": "pick", "description": "Итог по детали",
    "parameters": {"type": "object", "properties": {
        "found": {"type": "boolean"}, "id": {"type": "string"},
        "parts": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "pos": {"type": "string"}}, "required": ["name"]}},
        "via": {"type": "string", "enum": ["catalog", "assembly"]},
        "want": {"type": "string"}, "reason": {"type": "string"}}, "required": ["found"]}}}
T_DECODED = {"type": "function", "function": {
    "name": "decoded", "description": "Точный список деталей",
    "parameters": {"type": "object", "properties": {
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "need": {"type": "string", "description": "название детали, как в каталоге"},
            "axis": {"type": "string", "enum": ["front", "rear", ""]},
            "qty": {"type": "integer"}, "certainty": {"type": "string", "enum": ["sure", "maybe"]},
            "words": {"type": "string", "description": "слова клиента"}, "note": {"type": "string"}},
            "required": ["need", "axis", "certainty", "words"]}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "not_parts": {"type": "array", "items": {"type": "string"}}}, "required": ["items"]}}}

# Главные слова, которые поставщики пишут по-разному об одной детали: «Стойка газомасляная задняя» — амортизатор
_SAME_HEAD = [{T.stem(w) for w in g} for g in (("амортизатор", "стойка"), ("сайлентблок", "втулка", "с/блок"),
                                                ("наконечник", "тяга"), ("опора", "подушка"))]


def _heads_match(a: str, b: str) -> bool:
    return T.same(a, b) or any(any(T.same(a, x) for x in g) and any(T.same(b, x) for x in g) for g in _SAME_HEAD)


def check(part: str, offers: list[dict], car_brand: str) -> tuple[list[dict], list[str]]:
    """Проверяющий (код, без модели). Аналог в разы дешевле оригинала и с другой главной деталью в описании — не тот
    товар (26.10, Lexus: «опора» за 310 ₽ при оригинале 4 280 ₽ — подшипник опоры, «рычаг» за 430 ₽ — колодки).
    Оригинал — под маркой машины: ABCP отдаёт номер Toyota и под DAIHATSU, клиенту это «Toyota (оригинал)»."""
    maker = nice_brand(oem_brand_for(car_brand))
    orig = next((o for o in offers if o.get("role") == "оригинал"), None)
    head = _desc_head(part) or (T.head(T.stems(T.expand(part))) or "")
    kept, dropped = [], []
    for o in offers:
        if o.get("role") == "оригинал":
            o = dict(o, brand=f"{maker} (оригинал)")
        elif orig and o["price"] < 0.35 * orig["price"]:
            h = _desc_head(o.get("description", ""))
            if h and head and not _heads_match(h, head):
                dropped.append(f"{o['brand']} {o['price']:.0f} ₽ «{o.get('description', '')[:40]}»")
                continue
        kept.append(o)
    return kept, dropped


class Sessions:
    """Память разговора: машина и последние реплики — файл на собеседника в <state>/assistant/sessions."""

    def __init__(self, folder: Path | None):
        self.folder = folder

    def _path(self, sid: str) -> Path | None:
        return self.folder / (re.sub(r"[^A-Za-z0-9_-]", "_", sid)[:80] + ".json") if self.folder else None

    def load(self, sid: str) -> dict:
        p = self._path(sid)
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p and p.exists() else {}
        except (OSError, ValueError):
            return {}

    def save(self, sid: str, data: dict):
        p = self._path(sid)
        if not p:
            return
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


class Assistant:
    def __init__(self, engine, gateway: Gateway, model: str, folder: Path | None, journal=None, warranty: dict | None = None):
        self.eng, self.gw, self.model, self.journal = engine, gateway, model, journal
        self.cat = engine.catalog
        self.sessions = Sessions(folder / "sessions" if folder else None)
        self.warranty = warranty or {}
        self.pace = asyncio.Semaphore(4)   # параллельных подборщиков: Laximo 600/мин на весь сервис

    # ---------- инструменты подборщика ----------

    async def search(self, tree, query: str) -> list[dict]:
        """Кандидаты из дерева групп машины: правила робота (основы, синонимы, опечатки) + векторы по смыслу."""
        st = T.stems(query, self.eng.stop)
        out: dict[str, dict] = {}
        for g, _ in tree.rank(st, T.Side())[:8] if st else []:
            out.setdefault(f"g{g.id}", {"id": f"g{g.id}", "name": g.name, "path": g.path[-80:]})
        emb = EM.get()
        if emb is not None:
            vec = await emb.embed([query])
            names = {g.name.lower(): g for g in tree.groups.values()}
            for name, _ in emb.top(vec[0] if vec else None, [g.name for g in tree.groups.values()], 8):
                g = names.get(name.lower())
                if g:
                    out.setdefault(f"g{g.id}", {"id": f"g{g.id}", "name": g.name, "path": g.path[-80:]})
        return list(out.values())[:14]

    async def parts(self, v, tree, gid: str) -> list[dict]:
        if not gid[1:].isdigit() or int(gid[1:]) not in tree.groups:
            return [{"error": "нет такой группы, возьми id из search_catalog"}]
        ds = await self.cat.details(v, int(gid[1:]), False)
        seen, out = set(), []
        for d in ds:
            if (d.name, d.code_on_image) in seen:
                continue
            seen.add((d.name, d.code_on_image))
            out.append({"name": d.name[:70], "pos": d.code_on_image, "note": (d.note or d.specs or "")[:60],
                        "unit": d.unit[:50]})
        return out[:30] or [{"info": "в группе нет деталей"}]

    async def assembly_rows(self, v, gid: str, assembly: str, want: str) -> list[dict]:
        """Как робот (_bushings_by_host): кроссы номера сборки у поставщиков — только строки нужной детали."""
        if not gid[1:].isdigit():
            return []
        ds = await self.cat.details(v, int(gid[1:]), False)
        hosts = [d for d in ds if d.name[:70] == assembly] or [d for d in ds if assembly[:20].lower() in d.name.lower()]
        wst = T.stems(T.expand(want))
        if not hosts or not wst:
            return []
        out, seen = [], set()
        for d in hosts[:2]:
            _, rows = await self.eng.offers(d.oem, v.brand)
            for r in rows or []:
                h = _desc_head(str(r.get("description") or ""))
                if h and any(T.same(h, w) for w in wst) and (r.get("brand"), r.get("number")) not in seen:
                    seen.add((r.get("brand"), r.get("number")))
                    out.append(r)
        return out

    # ---------- агенты ----------

    async def decode(self, car: str, history: list[dict], text: str, used: dict) -> dict:
        msgs = [{"role": "system", "content": DECODER.format(car=car)}] + history + [{"role": "user", "content": text}]
        for _ in range(2):   # tool_choice у Claude через шлюз не поддерживается — просим словами
            m, u = await self.gw.chat(self.model, msgs, [T_DECODED])
            _add(used, u)
            c = (m.get("tool_calls") or [None])[0]
            if c:
                try:
                    return json.loads(c["function"].get("arguments") or "{}")
                except ValueError:
                    return {"items": []}
            msgs += [{"role": "assistant", "content": m.get("content") or ""},
                     {"role": "user", "content": "Ответь вызовом decoded."}]
        return {"items": []}

    async def select(self, v, tree, item: dict, used: dict) -> dict:
        async with self.pace:
            msgs = [{"role": "system", "content": SELECTOR.format(car=v.summary(), need=item["need"],
                                                                  axis=item.get("axis") or "любая", words=item.get("words", ""))},
                    {"role": "user", "content": "Подбери."}]
            for _ in range(SELECT_STEPS):
                m, u = await self.gw.chat(self.model, msgs, [T_SEARCH, T_PARTS, T_ASSEMBLY, T_PICK])
                _add(used, u)
                calls = m.get("tool_calls") or []
                msgs.append({"role": "assistant", "content": m.get("content") or "", **({"tool_calls": calls} if calls else {})})
                if not calls:
                    msgs.append({"role": "user", "content": "Вызови pick."})
                    continue
                for c in calls:
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    fn = c["function"]["name"]
                    if fn == "pick":
                        return args
                    try:
                        res = await self._tool(v, tree, fn, args)
                    except Exception as e:  # noqa: BLE001 — Laximo «группа недоступна для поиска» (26.10, Lexus)
                        log.info("инструмент %s: %s", fn, type(e).__name__)
                        res = [{"error": "эта группа сейчас недоступна, возьми другую из search_catalog"}]
                    msgs.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(res, ensure_ascii=False)})
            return {"found": False, "reason": "шаги кончились"}

    async def _tool(self, v, tree, fn: str, args: dict) -> Any:
        if fn == "search_catalog":
            return await self.search(tree, str(args.get("query", "")))
        if fn == "assembly_parts":
            rows = await self.assembly_rows(v, str(args.get("id", "")), str(args.get("assembly", "")),
                                            str(args.get("want", "")))
            return [{"brand": r.get("brand"), "description": str(r.get("description") or "")[:70],
                     "price": r.get("price")} for r in rows[:8]] or [{"info": "у поставщиков такой детали нет"}]
        return await self.parts(v, tree, str(args.get("id", "")))

    async def _safe(self, coro, fallback):
        """Сбой одной детали (каталог, поставщики, модель) не роняет весь ответ: по ней — «передам менеджеру»."""
        try:
            return await coro
        except OverBudget:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("деталь не подобрана: %s: %s", type(e).__name__, str(e)[:150])
            return fallback

    def _offer(self, o: dict, role: str = "") -> dict:
        t, why = ("OE", "оригинал") if role == "оригинал" else Q.tier(o["brand"])
        return {"brand": o["brand"], "price": o["price"], "days": o["days"], "tags": o.get("tags", []),
                "description": o.get("description", ""), "role": role or o.get("role", ""), "offers": o.get("offers", 1),
                "quality": t, "why": why,
                "warranty": (self.warranty.get(str(o["brand"]).upper()) or self.warranty.get(str(o["brand"])) or {}).get("warranty", "")}

    def quality(self, rows: list[dict], car_brand: str, name: str, have: set[str]) -> list[dict]:
        """Лучшие по качеству фирмы из ВСЕХ предложений по номеру (curate оставляет пять ходовых — быстрых и дешёвых,
        качественные фирмы до продавца не доходили): OE, потом good. Не оригинал, не чужой товар по описанию;
        по бренду — самое быстрое предложение."""
        own = {Q.key(x) for x in original_keys(car_brand)}
        best: dict[str, dict] = {}
        for r in rows or []:
            k = Q.key(r.get("brand"))
            t, _ = Q.tier(r.get("brand"))
            if t == "eco" or k in own or k in have:
                continue
            if name and not_the_part(str(r.get("description") or ""), name):
                continue
            try:
                price = float(r.get("price") or 0)
            except (TypeError, ValueError):
                continue
            cur = best.get(k)
            if price > 0 and (cur is None or (days(r.get("deliveryPeriod")), price) <
                              (days(cur.get("deliveryPeriod")), float(cur.get("price") or 0))):
                best[k] = r
        out = [self._offer(_offer(r)) for r in best.values()]
        out.sort(key=lambda o: (o["quality"] != "OE", o["days"], o["price"]))
        return out[:3]

    async def priced(self, v, item: dict, pick: dict) -> list[dict]:
        """Выбранные детали → номера каталога → предложения ABCP → проверяющий."""
        gid = str(pick.get("id") or "")
        if not pick.get("found") or not gid[1:].isdigit():
            return []
        if pick.get("via") == "assembly":
            host = (pick.get("parts") or [{}])[0].get("name", "")
            want = pick.get("want") or item["need"]
            rows = await self.assembly_rows(v, gid, host, want)
            if not rows:
                return []
            cur = curate(rows, "", v.brand, self.eng.warranty, name=want)
            offers = [self._offer(o) for o in cur.get("analogs", [])[:6]]
            offers += self.quality(rows, v.brand, "", {Q.key(o["brand"]) for o in offers})
            return [{"part": f"{want} (к сборке «{host}»)", "oem": "", "pos": "",
                     "note": f"{nice_brand(oem_brand_for(v.brand))} отдельно не продаёт — только {host}",
                     "offers": offers, "dropped": []}]
        ds = await self.cat.details(v, int(gid[1:]), False)
        out: list[dict] = []
        for p in pick.get("parts") or []:
            d = next((d for d in ds if d.name[:70] == p.get("name") and (not p.get("pos") or d.code_on_image == p["pos"])), None) \
                or next((d for d in ds if d.name[:70] == p.get("name")), None)
            if not d or any(o["oem"] == d.oem for o in out):
                continue
            _, rows = await self.eng.offers(d.oem, v.brand)
            cur = curate(rows or [], d.oem, v.brand, self.eng.warranty, name=d.name)
            offers = ([self._offer(cur["original"], "оригинал")] if cur.get("original") else []) + \
                [self._offer(o) for o in cur.get("analogs", [])]
            kept, dropped = check(d.name, offers[:7], v.brand)
            kept = kept[:6]
            extra, more = check(d.name, self.quality(rows, v.brand, d.name, {Q.key(o["brand"]) for o in kept}), v.brand)
            if dropped or more:
                log.info("проверяющий убрал: %s", dropped + more)
            out.append({"part": d.name, "oem": d.oem, "pos": d.code_on_image, "offers": kept + extra,
                        "dropped": dropped + more})
        return out

    async def sell(self, car: str, data: dict, used: dict, prefs: dict | None = None) -> str:
        prefs = prefs or {}
        goal = prefs.get("goal") if prefs.get("goal") in VARIANTS else "optimal"
        need = GOALS[goal] + ("; срочно — в первую очередь то, что привезём за 1–2 дня" if prefs.get("when") == "urgent"
                              else "; можно подождать ради цены и качества" if prefs.get("when") == "wait" else "")
        m, u = await self.gw.chat(self.model, [{"role": "system", "content": SELLER.format(car=car, need=need,
                                                                                          variants=VARIANTS[goal])},
                                               {"role": "user", "content": json.dumps(data, ensure_ascii=False)}],
                                  max_out=20000)
        _add(used, u)
        return (m.get("content") or "").strip()

    # ---------- разговор ----------

    async def reply(self, sid: str, text: str, action: str = "") -> dict:
        """Ответ собеседнику sid («tg-123»): {"text", "status", "buttons", "used", "data", "seconds"}.
        action — нажатая кнопка: «goal:quality», «when:urgent», «go» (подобрать с тем, что уже ответил)."""
        t0 = time.time()
        s = self.sessions.load(sid)
        if action:
            return await self._action(sid, s, action, t0)
        req = T.parse(text)
        if req.ident or req.plate:
            try:
                found = await self.cat.vehicles(req.ident, req.plate)
            except Exception as e:  # noqa: BLE001 — каталог недоступен: честно, без падения
                log.info("vehicles error: %s", type(e).__name__)
                return self._done(sid, s, text, FAILED, "catalog_error", t0)
            if not found:
                return self._done(sid, s, text, NO_CAR, "vehicle_not_found", t0)
            v = found[0]
            s.update(ident=req.ident, plate=req.plate if not req.ident else "", car=v.summary(), history=[])
            s.pop("asking", None)
            rest = re.sub(re.escape(req.ident or req.plate), " ", text, flags=re.I).strip(" ,.:;-\n")
            if len(T.words(rest)) < 2 and s.get("pending"):
                rest = s.pop("pending")   # сначала просьба, потом VIN — отвечаем на просьбу
            if len(T.words(rest)) < 2:
                return self._done(sid, s, text, f"Машина: {v.summary()}. Что подобрать?", "car", t0)
            text = rest
        if not s.get("ident") and not s.get("plate"):
            s["pending"] = (s.get("pending", "") + "\n" + text).strip()[-1500:]
            return self._done(sid, s, text, ASK_VIN, "no_vin", t0)
        asking = s.pop("asking", None)
        if asking:
            # Ответ текстом на наши вопросы — подбираем по просьбе вместе с ответами
            prefs = dict(asking.get("prefs") or {})
            prefs.update(_prefs_from_text(text))
            return await self._podbor(sid, s, asking["text"] + "\nОтветы клиента на вопросы: " + text, prefs, t0,
                                      shown=text)
        return await self._podbor(sid, s, text, {}, t0, ask=True)

    async def _action(self, sid: str, s: dict, action: str, t0: float) -> dict:
        asking = s.get("asking")
        if not asking:
            return {"text": "", "status": "stale", "buttons": [], "used": {}, "data": {}, "seconds": 0.0}
        kind, _, val = action.partition(":")
        if kind == "goal" and val in GOALS or kind == "when" and val in WHEN:
            asking.setdefault("prefs", {})[kind] = val
            self.sessions.save(sid, s)
            if not ({"goal", "when"} <= set(asking["prefs"])) or asking.get("questions"):
                label = GOALS.get(val) or WHEN.get(val)
                return {"text": "", "status": "pref", "ack": f"Принято: {label}", "buttons": [], "used": {},
                        "data": {}, "seconds": 0.0}
        elif action != "go":
            return {"text": "", "status": "stale", "buttons": [], "used": {}, "data": {}, "seconds": 0.0}
        s.pop("asking", None)
        return await self._podbor(sid, s, asking["text"], asking.get("prefs") or {}, t0, shown="(подобрать)")

    async def _podbor(self, sid: str, s: dict, text: str, prefs: dict, t0: float, ask: bool = False,
                      shown: str | None = None) -> dict:
        try:
            v = (await self.cat.vehicles(s.get("ident", ""), s.get("plate", "")))[0]
            tree = await self.cat.tree(v)
            used = {"in": 0, "out": 0, "calls": 0}
            dec = await self.decode(v.summary(), s.get("history", []), mask(text), used)
            items = (dec.get("items") or [])[:MAX_ITEMS]
            if ask and items:
                # До подбора — выявить потребность: «надолго или подешевле», «срочно или нет» и уточнения расшифровщика
                qs = [q for q in dec.get("questions") or [] if q.strip()][:MAX_QUESTIONS - 2]
                s["asking"] = {"text": text, "questions": qs, "prefs": {}}
                return self._done(sid, s, shown or text, _questions(items, qs), "ask", t0, used, buttons=BUTTONS)
            answer, data = await self._pipeline(v, tree, dec, items, mask(text), prefs, used)
        except OverBudget:
            return self._done(sid, s, shown or text, FAILED, "over_budget", t0)
        except Exception as e:  # noqa: BLE001 — модель/каталог сбоят: менеджеру, а не тишина
            log.warning("assistant failed: %s: %s", type(e).__name__, str(e)[:200])
            return self._done(sid, s, shown or text, FAILED, "error", t0)
        return self._done(sid, s, shown or text, answer or FAILED, "ok" if answer else "error", t0, used, data)

    async def _pipeline(self, v, tree, dec: dict, items: list[dict], text: str, prefs: dict,
                        used: dict) -> tuple[str, dict]:
        car = v.summary()
        if not items:
            # Не про детали: короткий ответ продавца на вопросы и болтовню, без подбора
            data = {"items": [], "questions": dec.get("questions", []), "not_parts": dec.get("not_parts", []),
                    "client": text}
            return await self.sell(car, data, used, prefs), data
        picks = await asyncio.gather(*(self._safe(self.select(v, tree, it, used), {"found": False}) for it in items))
        priced = await asyncio.gather(*(self._safe(self.priced(v, it, p), []) for it, p in zip(items, picks)))
        data = {"items": [{"need": it["need"], "axis": it.get("axis"), "qty": it.get("qty"),
                           "certainty": it.get("certainty"), "client_words": it.get("words"), "note": it.get("note", ""),
                           "found": bool(p.get("found")) and bool(pr), "catalog": pr}
                          for it, p, pr in zip(items, picks, priced)],
                "questions": dec.get("questions", []), "not_parts": dec.get("not_parts", [])}
        return await self.sell(car, data, used, prefs), data

    def _done(self, sid: str, s: dict, text: str, answer: str, status: str, t0: float,
              used: dict | None = None, data: dict | None = None, buttons: list | None = None) -> dict:
        hist = s.get("history", []) + [{"role": "user", "content": mask(text)[:1500]},
                                       {"role": "assistant", "content": answer[:1500]}]
        s["history"] = hist[-HISTORY:]
        s["updated"] = time.time()
        self.sessions.save(sid, s)
        res = {"text": answer, "status": status, "buttons": buttons or [], "used": used or {}, "data": data or {},
               "seconds": round(time.time() - t0, 1)}
        if self.journal is not None:
            # В тот же журнал, что и робот: страница «Разговоры с роботом» показывает и Кирилла
            self.journal.turn(sid, len(hist) // 2, text, {
                "status": f"assistant:{status}", "seconds": res["seconds"], "text": answer,
                "vehicle": {"summary": s.get("car", "")},
                "positions": [{"query": i["need"], "status": "found" if i["found"] else "not_found"}
                              for i in (data or {}).get("items", [])]})
        return res


def _questions(items: list[dict], qs: list[str]) -> str:
    """Перед подбором: что поняли и не больше трёх вопросов — два о потребности и одно-два уточнения по делу."""
    need = "\n".join(f"• {it['need']}" + (" (под вопросом)" if it.get("certainty") == "maybe" else "")
                     for it in items[:MAX_ITEMS])
    lines = [f"Понял, нужно:\n{need}", "", "Чтобы подобрать точно под вас, пара вопросов:",
             "1. Ставим надолго или нужно подешевле?", "2. Когда нужно — срочно или можно подождать?"]
    lines += [f"{n}. {q}" for n, q in enumerate(qs, 3)]
    lines += ["", "Ответьте кнопками или напишите текстом — и я подберу."]
    return "\n".join(lines)


_GOAL_WORDS = [("quality", r"надолго|качеств|оригинал|хорош|надёжн|надежн"), ("cheap", r"дешев|дёшев|бюджет|эконом"),
               ("optimal", r"оптимал|средн|норм")]


def _prefs_from_text(text: str) -> dict:
    """Ответ текстом на «надолго или подешевле / срочно или подождать» — те же предпочтения, что кнопками."""
    t, out = (text or "").lower(), {}
    for goal, rx in _GOAL_WORDS:
        if re.search(rx, t):
            out["goal"] = goal
            break
    if re.search(r"подожд|не\s+срочн|не\s+горит|можно\s+ждать", t):   # «не срочно» — раньше, чем «срочно»
        out["when"] = "wait"
    elif re.search(r"срочн|быстр|сегодня|завтра|горит", t):
        out["when"] = "urgent"
    return out


def _add(used: dict, u: dict):
    used["in"] = used.get("in", 0) + u.get("in", 0)
    used["out"] = used.get("out", 0) + u.get("out", 0)
    used["calls"] = used.get("calls", 0) + 1
