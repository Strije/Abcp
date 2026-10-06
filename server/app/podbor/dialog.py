"""Память разговора: второе и следующие сообщения клиента — уже без VIN.

Сервер разговоров не хранит: каждый ответ подбора несёт короткую «память» (машина, позиции, показанные
предложения), страница присылает её вместе со следующим сообщением. Что пишут клиенты после ответа с ценами
(переписка Битрикс24, 4 тыс. ответов):
  выбор       — «давайте первый», «1650 выберу», «ДПКВ за 2340, свечи NGK», «оригинал», «заказывайте»
  вопрос      — «дешевле нет?», «а оригинал есть?», «Хелла нету?», «какой лучше?»
  уточнение   — «а задние», «мне обе нужны», «левую и правую», «а верхнюю?», ответ на вопрос бота
  новая деталь — «А расходомер воздуха?», «И ступицы передние нужны»
Остальное (фото, оплата, адрес, «когда забрать») — менеджеру.
"""
import re
from typing import Any

from .. import brands as AB
from . import text as T

MAX_REMEMBERED = 12

ACCEPT = re.compile(r"\b(?:давайте|давай|беру|берём|берем|возьму|возьмём|возьмем|заказыва\w*|закаж\w*|заказу\w*|заказать|"
                    r"оформ\w*|выбер\w*|выбираю|подходит|устраивает)\b", re.I)
CHEAPER = re.compile(r"дешевл|подешевл|бюджетн|недорог|дорог", re.I)
ORIGINAL = re.compile(r"\bориг(?:инал\w*)?\b", re.I)
BEST = re.compile(r"\b(?:какой|какая|какие|какую|что|кого)\b.{0,25}\b(?:лучше|посовету\w*|порекоменду\w*|совету\w*)\b"
                  r"|\bчто\s+лучше\b|\bлучше\s+взять\b", re.I)
QUESTION = re.compile(r"\?|\b(?:есть|нет|нету|имеется|бывает|найдется|найдётся)\b", re.I)
# «все» и «пару» сюда не входят: «В наличии все?», «цена за штуку или за пару?» — вопросы менеджеру
BOTH = re.compile(r"\b(?:обе|оба|обои|обоих|те\s+и\s+те|и\s+те\s+и\s+другие|комплект\s+на\s+ось|на\s+обе\s+стороны|"
                  r"с\s+двух\s+сторон)\b", re.I)
# Отсрочка и отказ: «подумаю», «закажу попозже», «не актуально» — это не выбор, отвечает менеджер
DEFER = re.compile(r"подума|подумать|попозже|позже|потом\s+(?:закаж|напиш|отпиш)|отпиш[уе]с|перезвон|наберу|сообщу|"
                   r"не\s+надо|не\s+нужн|не\s+заказыв|отмен|не\s*актуал|передумал|в\s+другом\s+месте|нашл[иа]?\b|нашёл|нашел|"
                   r"определимся|согласую|пока\s+не|пока\s+ни|думает|думаю|отбой|решу|дам\s+ответ|"
                   r"\b(?:уже\s+)?(?:купил|взял|нашёл|нашел)\b(?!\s+бы)", re.I)
# Вопросы, на которые память подбора не отвечает: наличие, цена за штуку, сроки, оплата, адрес, фото
MANAGER = re.compile(r"наличи|за\s+(?:штуку|шт|пару|1|одну|один|комплект)\b|правильно|верно|когда|во\s+сколько|сколько\s+ехать|"
                     r"адрес|оплат|карт[уы]|чек|qr|ссылк|фото|скидк|возврат|доставк|отправ|забрать|заберу|работаете|"
                     r"банк|сбер|перевод|перевести", re.I)
QTY = re.compile(r"\b(\d{1,2})\s*(?:шт|штук|штуки|компл|комплект|к-т)\w*", re.I)
NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:[  ]\d{3})+|\d{3,6})(?:[.,]\d{1,2})?(?![\w])")
ORDINAL = re.compile(r"\b(?:(?P<n>[1-9])\s*(?:-?(?:й|ой|ый|ий|я|е))?\s*(?:вариант|позици\w*|пункт)"
                     r"|(?:вариант|позици\w*|пункт)\s*(?:№\s*)?(?P<m>[1-9])"
                     r"|(?P<w>перв|втор|трет|четв[её]рт|пят|последн)\w*)\b", re.I)
_ORD = {"перв": 1, "втор": 2, "трет": 3, "четверт": 4, "четвёрт": 4, "пят": 5, "последн": -1}

# Связки и вежливость второй реплики: без них «а задние?» — это только сторона
FILLER = {T.stem(w) for w in (
    "а", "и", "еще", "ещё", "тоже", "также", "нужен", "нужна", "нужно", "нужны", "надо", "можно", "мне", "нам",
    "обе", "оба", "обои", "две", "два", "пару", "пара", "шт", "штуки", "штук", "все", "те", "другие", "тогда",
    "есть", "нет", "нету", "будет", "какие", "какой", "какая", "так", "же", "сторона", "стороны", "посмотрите",
    "гляньте", "глянуть", "посмотреть", "подберите", "подобрать", "интересует", "давайте", "давай", "плиз")}

# Бренды кириллицей — как пишут в переписке
BRAND_RU = {"бош": "BOSCH", "хелла": "HELLA", "хела": "HELLA", "ман": "MANN", "манн": "MANN", "махле": "MAHLE",
            "мале": "MAHLE", "нгк": "NGK", "денсо": "DENSO", "лемфордер": "LEMFORDER", "лемфёрдер": "LEMFORDER",
            "сакс": "SACHS", "фебест": "FEBEST", "зеккерт": "ZEKKERT", "зекерт": "ZEKKERT",
            "кайаба": "KYB", "каяба": "KYB", "кйб": "KYB", "гейтс": "GATES", "контитех": "CONTITECH",
            "скф": "SKF", "фаг": "FAG", "ина": "INA", "трв": "TRW", "брембо": "BREMBO", "ферадо": "FERODO",
            "мейл": "MEYLE", "майле": "MEYLE", "сваг": "SWAG", "феби": "FEBI", "кронер": "KRONER",
            "лузар": "LUZAR", "маршал": "MARSHALL", "стеллокс": "STELLOX", "тойота": "TOYOTA",
            "триали": "TRIALLI", "триалли": "TRIALLI", "миля": "MILES", "майлз": "MILES", "майлс": "MILES",
            "патрон": "PATRON", "линкс": "LYNXAUTO", "азуми": "AZUMI", "сангсин": "SANGSIN", "кортеко": "CORTECO",
            "викторрейнц": "VICTOR REINZ", "рейнц": "VICTOR REINZ", "мобис": "MOBIS", "филтрон": "FILTRON",
            "фильтрон": "FILTRON", "кнехт": "KNECHT", "лукойл": "LUKOIL", "шелл": "SHELL", "мотул": "MOTUL",
            "суфикс": "SUFIX", "пиленга": "PILENGA", "элринг": "ELRING", "полкар": "POLCAR", "циммерман": "ZIMMERMANN",
            "зимерман": "ZIMMERMANN", "кашияма": "KASHIYAMA", "нагамочи": "SB NAGAMOCHI", "нагомочи": "SB NAGAMOCHI",
            "мапко": "MAPCO", "депо": "DEPO", "тайк": "TYC", "абсел": "ABSEL", "квадро": "QUATTRO FRENI",
            "кватро": "QUATTRO FRENI", "кваттро": "QUATTRO FRENI", "сакура": "SAKURA", "мотюль": "MOTUL"}
_NOT_BRAND = {"ok", "ок", "abs", "vin", "вин", "грм", "гбц", "акпп", "мкпп", "шрус", "дпкв", "дпрв", "тнвд", "egr",
              "lh", "rh", "fr", "rr", "the", "for", "and", "set", "kit", "oem"}

AXIS_WORD = {"front": T.FRONT, "rear": T.REAR, "left": T.LEFT, "right": T.RIGHT}


# ---------- память ----------

def _slim_offers(o: dict | None) -> dict | None:
    if not o:
        return None
    keep = ("brand", "number", "description", "price", "days", "tags", "cheaper", "reviews")
    return {"original": {k: o["original"].get(k) for k in keep} if o.get("original") else None,
            "analogs": [{k: a.get(k) for k in keep} for a in o.get("analogs", [])],
            "stats": o.get("stats") or {}}


def _slim(p: dict) -> dict:
    return {"query": p["query"], "side": p.get("side") or {}, "status": p["status"], "question": p.get("question", ""),
            "asked": bool(p.get("asked")),
            "variants": [{"name": v["name"], "oem": v["oem"], "brand": v["brand"], "axis": v["axis"], "lr": v["lr"],
                          "amount": v.get("amount", ""), "alt": v.get("alt", False), "offers": _slim_offers(v.get("offers")),
                          "facts": v.get("facts", [])}
                         for v in p.get("variants", [])]}


def remember(res: dict, prev: dict | None, replaced: list[int] | None = None) -> dict:
    """Память после ответа: позиции этого ответа заменяют уточнённые, остальные дописываются в конец."""
    req = res.get("request") or {}
    mem = dict(prev or {})
    if req.get("ident") or req.get("plate") or not mem:
        if req.get("ident") != mem.get("ident") or req.get("plate") != mem.get("plate"):
            mem = {"positions": []}
        mem.update(ident=req.get("ident", ""), plate=req.get("plate", ""), vehicle=req.get("vehicle"))
    if res.get("vehicle"):
        mem["car"] = res["vehicle"].get("brand", "")
        mem["summary"] = res["vehicle"].get("short") or res["vehicle"].get("summary", "")
    positions = list(mem.get("positions") or [])
    turn = mem.get("turn", 0) + (1 if res.get("positions") else 0)
    mem["turn"] = turn
    new = [_slim(p) | {"turn": turn} for p in res.get("positions", [])]
    slots = list(replaced or [])
    for p in new:
        if slots:
            i = slots.pop(0)
            if 0 <= i < len(positions):
                positions[i] = p
                continue
        positions.append(p)
    mem["positions"] = positions[-MAX_REMEMBERED:]
    return mem


# ---------- что показали клиенту ----------

def shown(p: dict, analogs: int = 3) -> list[dict]:
    """Предложения позиции в том порядке, в каком они стоят в ответе клиенту: «первый» — первая строка."""
    groups: list[tuple[dict, list[dict]]] = []
    for var in p.get("variants", []):
        if var.get("alt") and groups:
            groups[-1][1].append(var)
        else:
            groups.append((var, []))
    order = {"front": 0, "": 1, "rear": 2}
    groups.sort(key=lambda g: (order.get(g[0]["axis"], 1), {"left": 0, "": 1, "right": 2}.get(g[0]["lr"], 1)))
    per = analogs if len(groups) <= 1 else min(analogs, 2)
    out = []
    for var, alts in groups:
        o = var.get("offers") or {}
        if o.get("original"):
            out.append({"var": var, "offer": dict(o["original"], number=var["oem"]), "who": "оригинал"})
        for a in alts:
            ao = a.get("offers") or {}
            if ao.get("original"):
                out.append({"var": a, "offer": dict(ao["original"], number=a["oem"]), "who": "тот же оригинал"})
        out += [{"var": var, "offer": x, "who": ""} for x in (o.get("analogs") or [])[:per]]
    return out


def all_offers(p: dict) -> list[dict]:
    """Все запомненные предложения позиции, не только показанные (до пяти аналогов на деталь)."""
    return shown(p, analogs=99)


# ---------- разбор реплики ----------

def _numbers(text: str) -> list[int]:
    qty = {m.start(1) for m in QTY.finditer(text)}
    out = []
    for m in NUMBER.finditer(text):
        if m.start(1) in qty:
            continue
        n = int(re.sub(r"\D", "", m.group(1)))
        if 1900 <= n <= 2035 and re.search(r"\bг(?:од|\.)", text[m.end():m.end() + 5]):
            continue
        out.append(n)
    return out


def brand_words(text: str) -> list[tuple[str, str]]:
    """Бренды в реплике: (как написал клиент, ключ бренда ABCP). Латиница — по справочнику ABCP, кириллица — по BRAND_RU."""
    full = AB.get()
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z\-]{1,}|[а-яё]{3,}", text, re.I):
        lw = w.lower()
        if lw in _NOT_BRAND:
            continue
        if re.fullmatch(r"[а-яё]+", lw):
            key = BRAND_RU.get(lw) or BRAND_RU.get(lw[:-1]) or BRAND_RU.get(lw[:-2] if len(lw) > 5 else "")
            if key:
                out.append((w, AB.get().key(key)))
        elif len(lw) >= 2 and full.loaded and AB.norm(w) in full.alias:
            out.append((w, full.key(w)))
    return out


def _bk(brand: Any) -> str:
    return AB.get().key(brand)


def refers(piece: str, p: dict, stop: frozenset[str]) -> bool:
    """Реплика про эту позицию: слово запроса или каталожного названия («свечи», «фильтр масляный»)."""
    st = [s for s in T.stems(piece, stop) if s not in FILLER and s not in T.ADJ]
    if not st:
        return False
    names = T.stems(p["query"], stop) + [s for v in p.get("variants", []) for s in T.stems(v["name"], stop)]
    # И описания поставщиков: каталог зовёт «Амортизатор», поставщик — «Стойка газовая» — клиент пишет как поставщик
    names += [s for x in all_offers(p)[:8] for s in T.stems(str(x["offer"].get("description") or "")[:60], stop)]
    return any(T.same(a, b) for a in st for b in names)


def _ordinal(piece: str) -> int:
    m = ORDINAL.search(piece)
    if not m:
        return 0
    if m.group("n") or m.group("m"):
        return int(m.group("n") or m.group("m"))
    w = m.group("w").lower()
    return next((v for k, v in _ORD.items() if w.startswith(k)), 0)


def _qty(piece: str) -> int:
    m = QTY.search(piece)
    return int(m.group(1)) if m else 0


def _pieces(text: str) -> list[str]:
    """«ДПКВ за 2340, свечи NGK» → две части; «Свечи за 1520 и масляный фильтр» → тоже две."""
    parts = re.split(r"[,;\n]+|\s+и\s+(?=[а-яёa-z])", text, flags=re.I)
    return [p.strip(" .!") for p in parts if p.strip(" .!")]


def offer_reply(text: str, mem: dict, stop: frozenset[str], analogs: int = 3) -> dict | None:
    """Реплика про уже показанные предложения: выбор («беру за 1650») или вопрос («дешевле нет?»).
    None — реплика не про предложения: уточнение, новая деталь или разговор для менеджера."""
    positions = [p for p in mem.get("positions", []) if p.get("variants")]
    if not positions:
        return None
    question = bool(QUESTION.search(text)) and not ACCEPT.search(text)
    picks, unclear, answers, ask_brand = [], [], [], []
    touched = False
    last_turn = max(p.get("turn", 0) for p in positions)
    recent = [p for p in positions if p.get("turn", 0) == last_turn]
    pieces = _pieces(text) or [text]
    chooses = lambda pc: bool(_numbers(pc) or brand_words(pc) or _ordinal(pc) or ORIGINAL.search(pc))  # noqa: E731
    for i, piece in enumerate(pieces):
        scope = [p for p in positions if refers(piece, p, stop)]
        # «Тяги» — про «Рулевую тягу», а не про «Наконечник рулевой тяги»: где слово клиента — главное
        heads = [p for p in scope if any(T.same(w, T.head(T.stems(p["query"], stop)) or "") for w in T.stems(piece, stop))]
        scope = heads or scope
        named = bool(scope)
        # «Тяги и наконечники давайте зекерт» — фирма из следующей части относится и к этой
        said = piece if chooses(piece) else next((pc for pc in pieces[i + 1:] if chooses(pc)), piece)
        # Деталь не названа — речь о последнем ответе: «давайте первый» после «а задние?» — про задние
        scope = scope or recent
        side = T.side(piece)
        nums, brands, n_ord = _numbers(said), brand_words(said), _ordinal(said)
        orig = bool(ORIGINAL.search(said)) and not NOT_ORIGINAL.search(said)
        orig_pick = orig and bool(ACCEPT.search(said))   # «оригинал давайте», а не «оригинал есть» и не «оригинал дорого»

        def match(where: list[dict]) -> list[tuple]:
            out = []
            for p in where:
                for x in all_offers(p):
                    if side.conflicts(T.Side(x["var"]["axis"], x["var"]["lr"])):
                        continue
                    o = x["offer"]
                    cheaper = o.get("cheaper") or {}
                    if nums and any(abs(n - round(o["price"])) <= max(1, o["price"] * 0.005) for n in nums):
                        out.append((p, x, False))
                    elif nums and cheaper and any(abs(n - round(cheaper["price"])) <= max(1, cheaper["price"] * 0.005)
                                                  for n in nums):
                        out.append((p, x, True))
                    elif brands and any(k == _bk(o["brand"]) for _, k in brands):
                        out.append((p, x, False))
                    elif orig_pick and x["who"] == "оригинал" and not brands and not nums:
                        out.append((p, x, False))
            return out

        hits = match(scope)
        if not hits and not named and (nums or brands or side.axis or side.lr):
            # «беру передние за 9000» после ответа про задние — цена и сторона называют более раннюю позицию
            hits = match(positions)
        for p in scope:
            if n_ord and not hits and len(scope) == 1:
                vis = [x for x in shown(p, analogs) if not side.conflicts(T.Side(x["var"]["axis"], x["var"]["lr"]))]
                idx = n_ord - 1 if n_ord > 0 else len(vis) - 1
                if 0 <= idx < len(vis):
                    hits.append((p, vis[idx], False))
        # Один артикул у разных предложений позиции встречается один раз; цена могла совпасть у двух позиций
        uniq = list({(id(p), x["offer"]["brand"], x["offer"]["number"]): (p, x, c) for p, x, c in hits}.values())
        if question and SPEC.search(piece) and SPEC_ASK.search(piece) and not NEED.search(piece):
            touched = True
            for p in scope:
                spec = _spec(p, analogs)
                if spec:
                    answers.append(("spec", p, spec))
            if ONLY_ORIGINAL.search(piece) or orig:
                answers += [_analogs_answer(p, analogs) for p in scope]
            continue
        if question:
            if CHEAPER.search(piece):
                touched = True
                for p in scope:
                    answers.append(("cheapest", p, _cheapest(p)))
            elif BEST.search(piece):
                touched = True
                for p in scope:
                    answers.append(("best", p, _best(p)))
            elif ONLY_ORIGINAL.search(piece) and not orig:
                touched = True   # «аналоги есть?», «только оригинал?»
                answers += [_analogs_answer(p, analogs) for p in scope]
            elif orig:
                touched = True
                for p in scope:
                    x = next((x for x in all_offers(p) if x["who"] == "оригинал"), None)
                    answers.append(("original", p, x))
            elif brands:
                touched = True
                found = {_bk(x["offer"]["brand"]) for _, x, _ in uniq}
                for p, x, _ in uniq:
                    answers.append(("brand", p, x))
                for w, k in brands:
                    if k not in found:
                        ask_brand += [{"position": positions.index(p), "brand": k, "word": w}
                                      for p in scope]
            elif STOCK.search(piece):
                touched = True
                for p in scope:
                    vis = shown(p, analogs)
                    here = [x for x in vis if x["offer"]["days"] <= 0][:3]
                    if here:
                        answers += [("stock", p, x) for x in here]
                    elif vis:
                        answers.append(("fastest", p, min(vis, key=lambda x: (x["offer"]["days"], x["offer"]["price"]))))
            elif MORE.search(piece):
                touched = True
                for p in scope:
                    seen = {(x["offer"]["brand"], x["offer"]["number"]) for x in shown(p, analogs)}
                    extra = [x for x in all_offers(p) if (x["offer"]["brand"], x["offer"]["number"]) not in seen][:3]
                    answers += [("more", p, x) for x in extra] or [_analogs_answer(p, analogs)]
            elif QUALITY.search(piece):
                rated = [(p, x) for p in scope for x in shown(p, analogs) if (x["offer"].get("reviews") or {}).get("client")]
                if rated:   # без отзывов о качестве судить нечем — это менеджеру
                    touched = True
                    answers += [("quality", p, x) for p, x in rated]
            continue
        if (CHEAPER.search(piece) or NOT_ORIGINAL.search(piece)) and not uniq and not ACCEPT.search(piece):
            touched = True   # «дорого», «любую другую фирму» — самый недорогой, а если аналогов нет — так и скажем
            for p in scope:
                has = any(x["who"] == "" for x in all_offers(p))
                answers.append(("cheapest", p, _cheapest(p)) if has else _analogs_answer(p, analogs))
            continue
        if uniq:
            touched = True
            # Цена «1650» совпала у двух позиций — выбирать будем там, где клиент назвал деталь, иначе спросим
            by_pos: dict[int, list] = {}
            for p, x, c in uniq:
                by_pos.setdefault(id(p), []).append((p, x, c))
            for group in by_pos.values():
                if len(group) == 1 or len({g[1]["offer"]["brand"] for g in group}) == 1:
                    p, x, c = group[0]
                    picks.append(_pick(p, x, c, _qty(piece)))
                else:
                    unclear.append(group[0][0])
        elif ACCEPT.search(piece) or (named and _qty(piece)):
            touched = True
            for p in scope:
                vis = shown(p, analogs)
                if len(vis) == 1:
                    picks.append(_pick(p, vis[0], False, _qty(piece)))
                else:
                    unclear.append(p)
    if not touched:
        return None
    if answers or ask_brand:
        return {"kind": "answer", "answers": answers, "ask_brand": ask_brand, "picks": picks}
    return {"kind": "order", "picks": picks, "unclear": list(dict.fromkeys(p["query"] for p in unclear))}


def _analogs_answer(p: dict, analogs: int) -> tuple:
    """«Аналоги есть?» Есть — покажем; нет — честно: только оригинал, поищем другие фирмы (это менеджеру)."""
    extra = [x for x in all_offers(p) if x["who"] == ""]
    if extra:
        return ("more", p, extra[0])
    return ("no_analogs", p, None)


def _spec(p: dict, analogs: int) -> dict | None:
    """«Это внутренний или наружный? левый или правый?» — по каталогу (сторона) и по описаниям поставщиков
    (наружный/внутренний, верхний/нижний). Чего нет в данных — так и говорим, не угадываем."""
    vis = shown(p, analogs)
    if not vis:
        return None
    x = vis[0]
    descs = [str(y["offer"].get("description") or "") for y in all_offers(p)]
    # Признаки, посчитанные при подборе по всем описаниям поставщиков (в памяти — по одному на предложение)
    facts = list(dict.fromkeys(f for v in p.get("variants", []) for f in v.get("facts", [])))
    for a, b, a_name, b_name in _DESC_ATTRS:
        if a_name in facts or b_name in facts:
            continue
        ya = sum(1 for d in descs if a.search(d) and not b.search(d))
        yb = sum(1 for d in descs if b.search(d) and not a.search(d))
        if ya and ya >= 2 * yb:
            facts.append(a_name)
        elif yb and yb >= 2 * ya:
            facts.append(b_name)
    axes = {v["axis"] for v in p.get("variants", [])}
    if "передний" not in facts and "задний" not in facts and len(axes - {""}) == 1:
        facts.append({"front": "передний", "rear": "задний"}[next(iter(axes - {""}))])
    lrs = {v["lr"] for v in p.get("variants", []) if not v.get("alt")} - {""}
    if len(lrs) == 2:
        side = "левый и правый — разные номера, оба есть"
    elif len(lrs) == 1:
        side = {"left": "левый", "right": "правый"}[next(iter(lrs))]
    else:
        side = "левый или правый — в каталоге не различается"
    text = (", ".join(facts) + "; " if facts else "") + side
    return dict(x, why=text[:1].upper() + text[1:] + ".")


def spec_need(text: str, target: dict) -> list[tuple[str, "T.Side"]]:
    """«Нужны внутренние левый и правый и наружные» → поиски: «пыльник шруса внутренний», «… наружный»."""
    if not NEED.search(text):
        return []
    attrs = [name for rx, name in _ATTR_WORDS if rx.search(text)]
    if not attrs:
        return []
    base = strip_side(target["query"])
    for rx, _ in _ATTR_WORDS:
        base = " ".join(w for w in base.split() if not rx.search(w))
    return [(f"{base} {a}", T.Side()) for a in attrs]


def _pick(p: dict, x: dict, cheaper: bool, qty: int) -> dict:
    o = dict(x["offer"])
    if cheaper and o.get("cheaper"):
        o["price"], o["days"] = o["cheaper"]["price"], o["cheaper"]["days"]
    o["cheaper"] = None
    return {"query": p["query"], "name": x["var"]["name"], "who": x["who"], "offer": o, "qty": qty,
            "axis": x["var"]["axis"], "lr": x["var"]["lr"], "amount": x["var"].get("amount", "")}


def _cheapest(p: dict) -> dict | None:
    best = None
    for x in all_offers(p):
        o = x["offer"]
        for price, days in [(o["price"], o["days"])] + ([(o["cheaper"]["price"], o["cheaper"]["days"])]
                                                         if o.get("cheaper") else []):
            if best is None or price < best[0]:
                best = (price, days, x)
    if not best:
        return None
    price, days, x = best
    return dict(x, offer=dict(x["offer"], price=price, days=days, cheaper=None))


def _best(p: dict) -> dict | None:
    """«Какой лучше?» — то, за что ручается магазин: гарантия магазина, потом хорошие отзывы владельцев
    (3+ отзыва, лучший счёт), потом «часто берут», потом оригинал."""
    lst = all_offers(p)
    x = next((x for x in lst if "гарантия магазина" in (x["offer"].get("tags") or [])), None)
    if x:
        return dict(x, why="гарантия магазина")
    rated = [x for x in lst if (x["offer"].get("reviews") or {}).get("client")]
    if rated:
        return dict(max(rated, key=lambda x: x["offer"]["reviews"]["score"]), why="отзывы")
    x = next((x for x in lst if "частая замена" in (x["offer"].get("tags") or [])), None)
    if x:
        return dict(x, why="частая замена")
    x = next((x for x in lst if x["who"] == "оригинал"), None)
    return dict(x, why="оригинал") if x else None


# ---------- уточнение позиции ----------

OPPOSITE = [("верхн", "нижн"), ("передн", "задн"), ("лев", "прав"), ("внутрен", "наружн"), ("впускн", "выпускн"),
            ("внешн", "внутрен"), ("продольн", "поперечн")]


# Вопрос про уже предложенную деталь: «В сборе она?», «Это с колбой вместе или сам насос», «там 2 сайлента?»
ABOUT_OFFER = re.compile(r"\b(?:это|он|она|оно|они|эти|этот|эта|такой|такая)\b.*\?|\?.*\b(?:это|он|она|оно|они)\b"
                         r"|\bили\s+(?:нет|сам|сама|само|отдельно|без|с|со)\b|\bвместе\s+или\b|\bтам\b[^?]*\?|\bза\s+одно\b"
                         r"|\bчем\s+(?:хуже|лучше|отлича)|\bразниц|\bкачеств|\bправильно\b", re.I | re.S)
# Статус заказа, визит, оплата: «не пришёл датчик?», «подъеду завтра», «адрес магазина» — это менеджеру
STATUS = re.compile(r"\bприш[её]л|\bпришл[аи]\b|приехал|доехал|приедет|подъед|оплач|заберу|забрать|\bадрес|маршрут|"
                    r"прицени|не\s+понадоб|на\s+когда|до\s+какого\s+часа|во\s+сколько|отпишу|забира|переводом|"
                    r"\bв\s+корзину", re.I)
# Согласие на показанное: «давайте этот вариант», «беру все кроме болтов» — выбор, а не новая деталь
ACCEPT_THIS = re.compile(r"\b(?:давайте|давай|беру|возьму|берём|берем|заказываю|заказываем|закаж\w*|заказыва\w*)\b.*"
                         r"\b(?:этот|эту|это|его|е[её]|их|все|вс[её]|вариант|тоже)\b"
                         r"|\bкроме\b.*\b(?:закаж\w*|заказыва\w*|беру|давайте|оформ\w*)", re.I | re.S)
# Что менеджер должен ответить сам, даже если остальное в сообщении бот понял
HANDOFF = re.compile(STATUS.pattern + r"|друг\w*\s+фирм|\bаналог|оплат|\bкарт[уы]\b|\bqr\b|\bчек\b|доставк|отправ|скидк|фото|ссылк|возврат|"
                     r"работаете|когда|перев[оеё]д|перевест|\bсбер|\bбанк", re.I)
# «Оригинал» как отказ, а не выбор: «оригинал — это дорого», «любую другую фирму», «не оригинал»
NOT_ORIGINAL = re.compile(r"дорог|друг\w*\s+фирм|люб\w*\s+(?:друг|фирм)|\bне\s+ориг|кроме\s+ориг|подешевле", re.I)
# Вопрос о свойствах показанной детали: «это внутренний или наружный? левый или правый?»
SPEC = re.compile(r"внутрен|наружн|внешн|\bлев\w*|\bправ\w*|передн|задн|верхн|нижн", re.I)
# …именно вопрос о показанном («это внутренний или наружный?»), а не просьба («а задние?»)
SPEC_ASK = re.compile(r"\b(?:это|он|она|оно|они|или|какой|какая|какие)\b", re.I)
ONLY_ORIGINAL = re.compile(r"только\s+ориг|аналог", re.I)
# «Нужны внутренние и наружные» — новый поиск по признаку, а не вопрос
NEED = re.compile(r"\bнуж\w*|\bнадо\b|\bтребу\w*", re.I)
_ATTR_WORDS = [(re.compile(r"внутр", re.I), "внутренний"), (re.compile(r"наружн|внешн", re.I), "наружный"),
               (re.compile(r"верхн", re.I), "верхний"), (re.compile(r"нижн", re.I), "нижний")]
_DESC_ATTRS = [(re.compile(r"наружн|внешн", re.I), re.compile(r"внутр", re.I), "наружный", "внутренний"),
               (re.compile(r"верхн", re.I), re.compile(r"нижн", re.I), "верхний", "нижний"),
               (T.FRONT, T.REAR, "передний", "задний")]

# Вопросы по показанным предложениям, на которые память отвечает сама
STOCK = re.compile(r"наличи|на\s+сегодня|сегодня\s+(?:есть|будет|можно|забрать)|сейчас\s+есть", re.I)
MORE = re.compile(r"\b(?:какие|что)\s+(?:ещ[её]|еще)\s+(?:есть|бывают|можно)|други[ех]\s+(?:вариант|фирм|производ)|"
                  r"ещ[её]\s+вариант|\bаналог\w*\s+есть|\bесть\s+аналог", re.I)
QUALITY = re.compile(r"качеств|хорош\w*\s*\?|надёжн|надежн|\bнорм\w*\s*\?|как\s+(?:ходят|ходит|служ)", re.I)
_ADJ_WORD = re.compile(r"(?:ый|ий|ой|ая|яя|ое|ее|ые|ие|ого|его|ому|ему|ую|юю|ым|им|ых|их|ыми|ими)$")
_NOUN_LIKE = re.compile(r"(?:ние|тие|ье|ьё)$")


# Общие слова: «детали приедут», «машина у мастера», «кулак» в «Кулакова» — не новая деталь
GENERIC = {T.stem(w) for w in ("деталь", "детали", "запчасть", "запчасти", "машина", "авто", "автомобиль", "механик",
                                "мастер", "вопрос", "товар", "заказ", "кулаков", "цена", "фирма", "производитель",
                                # Свойства предложенной детали: «Комплект?», «А в сборе весь есть?», «диаметр какой?»
                                "комплект", "сборе", "сбор", "диаметр", "размер", "толщина", "длина", "ширина", "вид",
                                "формат", "качество", "штука", "пара", "перевод", "корзину")}


def adj_word(w: str) -> bool:
    """«задние», «угольный», «внутрение» — да; «сцепление», «крепление», «ремень» — нет."""
    w = w.lower()
    return bool(_ADJ_WORD.search(w)) and not _NOUN_LIKE.search(w)


def content(text: str, stop: frozenset[str]) -> list[str]:
    """Слова реплики без вежливости, связок и стороны: пусто — значит, клиент назвал только сторону или «обе»."""
    return [s for s in T.stems(text, stop) if s not in FILLER and not T.side_of_word(s) and not s.isdigit()]


def strip_side(query: str) -> str:
    q = query
    for rx in (T.FRONT, T.REAR, T.LEFT, T.RIGHT):
        q = rx.sub("", q)
    return re.sub(r"\s+", " ", q).strip(" ,")


def sides_wanted(text: str, target: dict) -> list[T.Side]:
    """«а задние» → задние; «обе», «и передние и задние» → обе стороны; «левую и правую» → обе."""
    f, r, lft, rgt = (bool(rx.search(text)) for rx in (T.FRONT, T.REAR, T.LEFT, T.RIGHT))
    if f and r:
        return [T.Side("front"), T.Side("rear")]
    if lft and rgt:
        ax = (target.get("side") or {}).get("axis", "")
        return [T.Side(ax, "left"), T.Side(ax, "right")]
    if BOTH.search(text):
        axes = {v["axis"] for v in target.get("variants", [])} - {""}
        if len(axes) > 1 or not (target.get("side") or {}).get("axis"):
            return [T.Side("front"), T.Side("rear")]
        lrs = {v["lr"] for v in target.get("variants", [])} - {""}
        if len(lrs) > 1:
            ax = (target.get("side") or {}).get("axis", "")
            return [T.Side(ax, "left"), T.Side(ax, "right")]
        return []
    s = T.side(text)
    old = target.get("side") or {}
    if s.axis or s.lr:
        return [T.Side(s.axis or ("" if s.lr else old.get("axis", "")), s.lr)]
    return []


def side_query(base: str, s: T.Side) -> str:
    """«колодки» + задние → «колодки задние» (в согласии с деталью: «ступица задняя»)."""
    label = T.side_label(base, s.axis, s.lr).lower()
    return f"{base} {label}".strip()


def replace_adj(query: str, text: str) -> str:
    """«шаровая опора нижняя» + «а верхнюю?» → «шаровая опора верхнюю»: противоположное слово заменяем."""
    new = [w for w in T.words(text) if T.stem(w) in T.ADJ]
    out = query.split()
    for w in new:
        sw = T.stem(w)
        pair = next((p for p in OPPOSITE if any(sw.startswith(x) for x in p)), None)
        if pair:
            out = [x for x in out if not any(T.stem(x).startswith(y) for y in pair)]
        out.append(w)
    return " ".join(out)


def targets(mem: dict) -> list[int]:
    """К каким позициям относится уточнение: где бот задал вопрос, иначе — последняя."""
    ps = mem.get("positions", [])
    open_ = [i for i, p in enumerate(ps) if p.get("question")]
    return open_ or ([len(ps) - 1] if ps else [])


# ---------- что это за реплика ----------

KINDS = {
    "pick": "выбор варианта",
    "pick_unclear": "согласие без варианта — переспросим",
    "answer": "вопрос по предложению",
    "side": "уточнение стороны",
    "attr": "уточнение признака",
    "reply": "ответ на вопрос бота",
    "new": "новая деталь",
    "chat": "не про подбор — менеджеру",
}


def plan(text: str, mem: dict, stop: frozenset[str], tree, analogs: int = 3,
         clean=lambda t: t.strip()) -> dict:
    """Разбор правилами и отметка, уверены ли они («sure»). Не уверены — сервер спросит модель (from_llm)."""
    out = _plan(text, mem, stop, tree, analogs, clean)
    out["sure"] = _sure(out, text)
    return out


def _sure(out: dict, text: str) -> bool:
    """Правила уверены: выбор по цене, фирме, номеру; вопрос «дешевле?», «оригинал есть?»; «а задние?»;
    ответ на вопрос бота; короткая новая деталь; «подумаю». Не уверены: согласие без варианта, длинное
    или многострочное сообщение с новой деталью, «не про подбор» без явной отсрочки."""
    kind = out["kind"]
    # Длинное или в несколько строк — правилам не доверяем: «Это не тот / Тогда давайте вкладыши шатунные»
    # они принимали за выбор, «если нужен будет, закажу» — тоже (замер на переписке 2024: 24% ошибок)
    # «Тяги давайте зекерт, остальное подумаю. Куда платить?» — отсрочка рядом с выбором: решает модель
    picked = bool(ACCEPT.search(text) or brand_words(text) or _numbers(text))
    if len(text.strip()) > 50 or "\n" in text.strip():
        return kind == "chat" and bool(DEFER.search(text)) and len(text) <= 120 and not picked
    # Вопрос — не выбор и не новая деталь: «От Соренто по креплениям подходит?», «А установка сколько стоит?»
    if "?" in text and kind in ("pick", "pick_unclear", "new"):
        return False
    if kind == "pick":
        return bool((out.get("reply") or {}).get("picks"))
    if kind in ("answer", "side", "attr", "reply"):
        return True
    if kind == "new":
        # «масло моторное и аккумулятор» — несколько вещей в одной строке правила склеивают
        return "\n" not in text.strip() and not re.search(r",|\sи\s|\+", text)
    if kind == "chat":
        return bool(DEFER.search(text)) and not picked
    return False


def from_llm(d: dict, mem: dict, analogs: int = 3, text: str = "") -> dict:
    """Ответ модели о следующем сообщении → те же действия, что у правил: выбор, ответы, поиск, менеджеру,
    уточняющий вопрос клиенту. Ссылки модели на П/В проверяем: несуществующие пропускаем."""
    positions = mem.get("positions") or []
    last_turn = max((p.get("turn", 0) for p in positions), default=0)
    recent = [p for p in positions if p.get("turn", 0) == last_turn] or positions[-1:]

    def pos(i):
        return positions[i - 1] if i and 0 < i <= len(positions) else None

    picks, answers, ask_brand, jobs, unclear = [], [], [], [], []
    # Модель любит читать «2 штуки», «ну да», «одну нам надо» как выбор первого варианта. Выбор принимаем, только
    # если клиент назвал вариант (цену, фирму, номер, «оригинал») или вариант один — иначе спрашиваем какой
    named = bool(_numbers(text) or brand_words(text) or _ordinal(text) or ORIGINAL.search(text)) if text else True
    for x in d.get("picks", []):
        p = pos(x["p"])
        vis = shown(p, analogs) if p else []
        if p and 0 < x["v"] <= len(vis):
            if named or len(vis) == 1:
                picks.append(_pick(p, vis[x["v"] - 1], False, x.get("qty") or _qty(text)))
            else:
                unclear.append(p["query"])
    for a in d.get("asks", []):
        for p in ([pos(a["p"])] if pos(a.get("p")) else recent):
            about = a["about"]
            if about == "cheaper":
                has = any(x["who"] == "" for x in all_offers(p))
                answers.append(("cheapest", p, _cheapest(p)) if has else _analogs_answer(p, analogs))
            elif about == "original":
                answers.append(("original", p, next((x for x in all_offers(p) if x["who"] == "оригинал"), None)))
            elif about == "analogs":
                seen = {(x["offer"]["brand"], x["offer"]["number"]) for x in shown(p, analogs)}
                extra = [x for x in all_offers(p) if x["who"] == "" and
                         (x["offer"]["brand"], x["offer"]["number"]) not in seen][:3]
                answers += [("more", p, x) for x in extra] or [_analogs_answer(p, analogs)]
            elif about == "best":
                answers.append(("best", p, _best(p)))
            elif about == "stock":
                vis = shown(p, analogs)
                here = [x for x in vis if x["offer"]["days"] <= 0][:3]
                answers += [("stock", p, x) for x in here] or (
                    [("fastest", p, min(vis, key=lambda x: (x["offer"]["days"], x["offer"]["price"])))] if vis else [])
            elif about == "spec":
                spec = _spec(p, analogs)
                if spec:
                    answers.append(("spec", p, spec))
            elif about == "brand" and a.get("brand"):
                key = _bk(a["brand"])
                hit = next((x for x in all_offers(p) if _bk(x["offer"]["brand"]) == key), None)
                if hit:
                    answers.append(("brand", p, hit))
                else:
                    ask_brand.append({"position": positions.index(p), "brand": key, "word": a["brand"]})
    for r in d.get("refine", []):
        p = pos(r["p"])
        if not p:
            continue
        base = strip_side(p["query"])
        if r.get("attr"):
            base = replace_adj(base, r["attr"])
        side = T.Side(r.get("axis") or "", r["lr"] if r.get("lr") in ("left", "right") else "")
        jobs.append((side_query(base, side) if side.axis or side.lr else base, side))
    for n in d.get("new_parts", []):
        jobs.append((n["part"], T.Side(n.get("axis") or "", n["lr"] if n.get("lr") in ("left", "right") else "")))
    clarify = d.get("clarify", "") if (d.get("unsure") or not (picks or answers or ask_brand or jobs or unclear)) else ""
    reply = None
    if picks or answers or ask_brand or clarify or unclear:
        reply = {"kind": "order" if (picks or unclear) and not answers and not ask_brand else "answer",
                 "picks": picks, "answers": answers, "ask_brand": ask_brand, "unclear": list(dict.fromkeys(unclear)),
                 "clarify": clarify}
    return {"kind": "llm", "reply": reply, "jobs": jobs, "replaced": [], "handoff": d.get("manager", []),
            "sure": not d.get("unsure")}


def _plan(text: str, mem: dict, stop: frozenset[str], tree, analogs: int = 3,
          clean=lambda t: t.strip()) -> dict:
    """Что делать со следующим сообщением клиента. Общая для сервера и замера по переписке
    (python -m app.podbor.measure followups), чтобы замер мерил то, что работает на самом деле.
    tree — дерево групп машины (TreeIndex); jobs — запросы для подбора: (текст, сторона)."""
    positions = mem.get("positions") or []
    cont = content(text, stop)
    known = tree.known(cont)
    names_old = any(refers(text, p, stop) for p in positions)
    surface = [w for w in T.words(text) if T.stem(w) in cont]
    adj_only = bool(surface) and all(adj_word(w) for w in surface)
    # Главное слово — первое не прилагательное из каталога: «Есть к нему шланг?» → «шланг»
    # Точно из словаря каталога (длинные — и с опечаткой): «Кулакова 18/3» — адрес, «трени» — не «тренога»
    noun = next((s for w in surface if not adj_word(w) and (s := T.stem(w)) not in GENERIC
                 and (s in tree.vocab or (len(s) >= 6 and tree.fix(s) in known))), None)
    new_part = bool(noun) and not names_old and not ACCEPT_THIS.search(text) and not STATUS.search(text)
    out: dict[str, Any] = {"kind": "chat", "reply": None, "jobs": [], "replaced": []}
    if DEFER.search(text):
        return out
    manager = bool(MANAGER.search(text))
    # Что в сообщении ответить менеджеру самому: «Заказывайте. Куда перевести?» — бот оформит, оплату — менеджер
    out["handoff"] = [s.strip() for s in re.split(r"(?<=[.?!])\s+|\n+", text) if s.strip() and HANDOFF.search(s)]

    if positions and names_old or (positions and not new_part):
        target_i = next((i for i, p in enumerate(positions) if refers(text, p, stop)), len(positions) - 1)
        jobs = spec_need(text, positions[target_i])
        if jobs:
            # Остальное в сообщении («любую другую фирму, оригинал дорого») — менеджеру
            return out | {"kind": "attr", "jobs": jobs, "replaced": [],
                          "handoff": out["handoff"] or ([text.strip()] if NOT_ORIGINAL.search(text) else [])}

    r = offer_reply(text, mem, stop, analogs)
    if r and (r.get("picks") or r.get("answers") or r.get("ask_brand") or not new_part):
        if r.get("answers") and all(k == "stock" or k == "fastest" for k, _, _ in r["answers"]):
            out["handoff"] = [s for s in out["handoff"] if not STOCK.search(s)]   # про наличие бот ответил сам
        kind = "answer" if r["kind"] == "answer" else "pick" if r.get("picks") else "pick_unclear"
        return out | {"kind": kind, "reply": r}

    jobs, replaced = out["jobs"], out["replaced"]
    last_turn = max((p.get("turn", 0) for p in positions), default=0)
    if positions and not cont and not manager:
        # «а задние?», «обе», «левую и правую» — сторона к позиции, по которой спрашивали, или к последним
        open_ = [i for i, p in enumerate(positions) if p.get("question")]
        idx = open_ or [i for i, p in enumerate(positions) if p.get("turn", 0) == last_turn]
        for i in idx:
            p = positions[i]
            sides = sides_wanted(text, p)
            base = strip_side(p["query"])
            jobs += [(side_query(base, s), s) for s in sides]
            if sides and i in open_:
                replaced += [i] * len(sides)
        if jobs:
            out["kind"] = "side"
    elif positions and cont and not manager and adj_only and len(known) == len(cont):
        # Только слова каталога: «угольный», «внутренний»; «не актуально», «да нормально» — не признак детали
        # «а верхнюю?», «моторное», «впускной» — признак к детали, по которой спрашивали, или к последней
        for i in targets(mem):
            p = positions[i]
            q = replace_adj(p["query"], text)
            jobs.append((q, T.side(q)))
            if p.get("question"):
                replaced.append(i)
        out["kind"] = "attr"
    else:
        asked = [i for i, p in enumerate(positions) if p.get("asked")]
        alone = tree.rank(cont, T.Side()) if cont else []
        sure = bool(alone) and alone[0][1] >= 0.8 and (len(alone) < 2 or alone[1][1] < alone[0][1] - 0.05)
        if asked and cont and len(cont) <= 2 and not sure:
            # Ответ на вопрос бота: «ГБЦ» → «прокладка», «масло» → «моторное 5 литров»
            for i in asked:
                q = f"{positions[i]['query']} {clean(text)}"
                jobs.append((q, T.side(q)))
                replaced.append(i)
            out["kind"] = "reply"
        elif new_part and not re.search(r"фото|ссылк|оплат|возврат|банк|номер|код[ыа]?\b|карт", text, re.I) \
                and not ABOUT_OFFER.search(text):
            # Новая деталь на ту же машину: главное слово — из каталога («А фара?», «катушка зажигания»).
            # «Оно резиновая?», «до скольки работаете?» — менеджеру
            out["kind"] = "new"
    return out
