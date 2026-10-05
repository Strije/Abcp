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

ACCEPT = re.compile(r"\b(?:давайте|давай|беру|берём|берем|возьму|возьмём|возьмем|заказыва\w*|закаж\w*|заказу\w*|"
                    r"оформ\w*|выбер\w*|выбираю|подходит|устраивает)\b", re.I)
CHEAPER = re.compile(r"дешевл|подешевл|бюджетн|недорог|дорог", re.I)
ORIGINAL = re.compile(r"\bориг(?:инал\w*)?\b", re.I)
BEST = re.compile(r"\b(?:какой|какая|какие|какую|что|кого)\b.{0,25}\b(?:лучше|посовету\w*|порекоменду\w*|совету\w*)\b"
                  r"|\bчто\s+лучше\b|\bлучше\s+взять\b", re.I)
QUESTION = re.compile(r"\?|\b(?:есть|нет|нету|имеется|бывает|найдется|найдётся)\b", re.I)
BOTH = re.compile(r"\b(?:обе|оба|обои|обоих|все|те\s+и\s+те|и\s+те\s+и\s+другие|пару|комплект\s+на\s+ось)\b", re.I)
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
            "лузар": "LUZAR", "маршал": "MARSHALL", "стеллокс": "STELLOX", "тойота": "TOYOTA"}
_NOT_BRAND = {"ok", "ок", "abs", "vin", "вин", "грм", "гбц", "акпп", "мкпп", "шрус", "дпкв", "дпрв", "тнвд", "egr",
              "lh", "rh", "fr", "rr", "the", "for", "and", "set", "kit", "oem"}

AXIS_WORD = {"front": T.FRONT, "rear": T.REAR, "left": T.LEFT, "right": T.RIGHT}


# ---------- память ----------

def _slim_offers(o: dict | None) -> dict | None:
    if not o:
        return None
    keep = ("brand", "number", "description", "price", "days", "tags", "cheaper")
    return {"original": {k: o["original"].get(k) for k in keep} if o.get("original") else None,
            "analogs": [{k: a.get(k) for k in keep} for a in o.get("analogs", [])],
            "stats": o.get("stats") or {}}


def _slim(p: dict) -> dict:
    return {"query": p["query"], "side": p.get("side") or {}, "status": p["status"], "question": p.get("question", ""),
            "asked": bool(p.get("asked")),
            "variants": [{"name": v["name"], "oem": v["oem"], "brand": v["brand"], "axis": v["axis"], "lr": v["lr"],
                          "amount": v.get("amount", ""), "alt": v.get("alt", False), "offers": _slim_offers(v.get("offers"))}
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
    for piece in pieces:
        scope = [p for p in positions if refers(piece, p, stop)]
        named = bool(scope)
        # Деталь не названа — речь о последнем ответе: «давайте первый» после «а задние?» — про задние
        scope = scope or recent
        side = T.side(piece)
        nums, brands, n_ord = _numbers(piece), brand_words(piece), _ordinal(piece)
        orig = bool(ORIGINAL.search(piece))
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
                    elif orig and x["who"] == "оригинал" and not brands and not nums:
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
        if question:
            if CHEAPER.search(piece):
                touched = True
                for p in scope:
                    answers.append(("cheapest", p, _cheapest(p)))
            elif BEST.search(piece):
                touched = True
                for p in scope:
                    answers.append(("best", p, _best(p)))
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
            continue
        if CHEAPER.search(piece) and not uniq and not ACCEPT.search(piece):
            touched = True   # «дорого», «дороговато» — покажем самый недорогой
            for p in scope:
                answers.append(("cheapest", p, _cheapest(p)))
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
    """«Какой лучше?» — то, за что ручается магазин: гарантия магазина, потом «часто берут», потом оригинал."""
    lst = all_offers(p)
    for tag in ("гарантия магазина", "частая замена"):
        x = next((x for x in lst if tag in (x["offer"].get("tags") or [])), None)
        if x:
            return dict(x, why=tag)
    x = next((x for x in lst if x["who"] == "оригинал"), None)
    return dict(x, why="оригинал") if x else None


# ---------- уточнение позиции ----------

OPPOSITE = [("верхн", "нижн"), ("передн", "задн"), ("лев", "прав"), ("внутрен", "наружн"), ("впускн", "выпускн"),
            ("внешн", "внутрен"), ("продольн", "поперечн")]


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
