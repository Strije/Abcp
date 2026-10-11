"""Машина без VIN в каталоге: марка, модель и год из слов клиента → мастер подбора Laximo (getWizard2).

Нужен, когда VIN есть, а каталог его не знает (российская сборка Skoda Karoq/Rapid, ТагАЗ, Renault Duster II): клиент
всё равно пишет «шкода карок 2021». Модели в мастере — латиницей («Karoq»), клиент пишет кириллицей и по слуху
(«дастер»), поэтому сверяем по звуковому ключу, а не по буквам. Берём только однозначное совпадение; сомневаемся —
не гадаем, машину не нашли.
"""
import difflib
import re

# Марка в словах клиента → марка в списке каталогов Laximo (listCatalogs, поле brand)
_BRANDS: list[tuple[str, str]] = [
    (r"шкод|skoda", "SKODA"), (r"фольксваген|вольксваген|фольц|volkswagen|\bvw\b", "VOLKSWAGEN"),
    (r"рено|renault", "RENAULT"), (r"хенд[аэе]й|хундай|хёнд|хендэ|hyundai", "HYUNDAI"), (r"\bки[ая]\b|\bkia\b", "KIA"),
    (r"ниссан|nissan", "NISSAN"), (r"тойот|тайот|toyota", "TOYOTA"), (r"лексус|lexus", "LEXUS"),
    (r"мазд|mazda", "MAZDA"), (r"митсубиси|мицубиси|митсубиши|mitsubishi", "MITSUBISHI"),
    (r"мерседес|мерс\b|mercedes", "MERCEDES-BENZ"), (r"\bбмв\b|bmw", "BMW"), (r"\bауди\b|audi", "AUDI"),
    (r"\bфорд|ford", "FORD"), (r"шевроле|chevrolet", "CHEVROLET"), (r"опел[ья]|opel", "OPEL"),
    (r"пежо|peugeot", "PEUGEOT"), (r"ситроен|citro[eë]n", "CITROEN"), (r"хонд|honda", "HONDA"),
    (r"субару|subaru", "SUBARU"), (r"сузуки|suzuki", "SUZUKI"), (r"вольво|volvo", "VOLVO"),
    (r"санг\s*йонг|ссанг|саньенг|ssang", "SSANGYONG"), (r"инфинити|infiniti", "INFINITI"), (r"джип|jeep", "JEEP"),
    (r"ленд\s*ровер|land\s*rover", "LAND ROVER"), (r"дэу|daewoo|деу\b", "DAEWOO"), (r"сеат|seat\b", "SEAT"),
    (r"фиат|fiat", "FIAT"), (r"додж|dodge", "DODGE"), (r"крайслер|chrysler", "CHRYSLER"), (r"порше|porsche", "PORSCHE"),
    (r"ягуар|jaguar", "JAGUAR"), (r"исузу|isuzu", "ISUZU"), (r"дач[ия]|dacia", "DACIA"), (r"\bмини\b|\bmini\b", "MINI"),
]
_NOISE = {"подойдёт", "подойдет", "подходит", "ли", "для", "на", "мой", "мою", "авто", "автомобиль", "машину", "машина",
          "vin", "frame", "здравствуйте", "добрый", "день", "вечер", "утро", "нужен", "нужна", "нужно", "есть", "можно",
          "рестайлинг", "дорест", "дорестайлинг", "год", "года", "лет", "объем", "объём", "двигатель", "дизель", "бензин",
          "акпп", "мкпп", "амт", "при", "по", "под", "что", "это", "как", "ваш", "вам", "вас", "если", "данная", "модель",
          "подходящая", "наличии", "тоже", "уже", "писали", "вин", "вина", "код", "номер", "фото"}

_CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                 "u", "f", "h", "ts", "ch", "sh", "sh", "", "i", "", "e", "yu", "ya"]))


def key(word: str) -> str:
    """Звуковой ключ слова для сверки «карок» ↔ «Karoq», «дастер» ↔ «Duster»: латиница, c/q → k, гласные одинаковы,
    повторы схлопнуты."""
    t = "".join(_CYR.get(c, c) for c in word.lower())
    t = re.sub(r"[^a-z0-9]", "", t)
    t = re.sub(r"ts(?=[eiy])|c(?=[eiy])", "s", t)
    t = t.replace("ck", "k").replace("q", "k").replace("c", "k").replace("x", "ks").replace("w", "v").replace("j", "i")
    t = t.replace("ph", "f").replace("zh", "z").replace("sh", "s").replace("ch", "c").replace("ts", "s")
    t = re.sub(r"[aeiouy]+", "a", t)
    return re.sub(r"(.)\1+", r"\1", t)


def brand_of(text: str) -> str:
    low = text.lower()
    for rx, brand in _BRANDS:
        if re.search(rx, low):
            return brand
    return ""


# Начало VIN (производитель) → марки, чьи каталоги пробуем, когда клиент марку не назвал («бампер на карок»):
# заводы РФ и СНГ, которые Laximo по VIN знает хуже всего. Марок несколько — модель решает, в каком каталоге искать.
_WMI_BRANDS = {
    "XW8": ["VOLKSWAGEN", "SKODA"], "XW7": ["TOYOTA"], "X7L": ["RENAULT"], "X7M": ["HYUNDAI"], "XWE": ["KIA", "HYUNDAI"],
    "Z94": ["HYUNDAI", "KIA"], "XUU": ["CHEVROLET"], "XWB": ["DAEWOO", "CHEVROLET"], "X9F": ["FORD"], "Z6F": ["FORD"],
    "XUF": ["CHEVROLET", "OPEL"], "X4X": ["BMW"], "XMC": ["MITSUBISHI"], "Z8N": ["NISSAN"], "Z8T": ["PEUGEOT", "CITROEN"],
    "WVW": ["VOLKSWAGEN"], "WV1": ["VOLKSWAGEN"], "WV2": ["VOLKSWAGEN"], "TMB": ["SKODA"], "WAU": ["AUDI"],
}


def wmi_brands(vin: str) -> list[str]:
    return _WMI_BRANDS.get((vin or "")[:3].upper(), [])


def catalog_of(brand: str, catalogs: list[dict]) -> str:
    """Код каталога марки из listCatalogs; если марок с таким именем несколько — не гадаем."""
    codes = [c["code"] for c in catalogs if str(c.get("brand", "")).upper() == brand.upper() and c.get("code")]
    return codes[0] if len(codes) == 1 else ""


def tokens(text: str, brand_rx: str = "") -> list[str]:
    """Слова клиента, которые могут быть названием модели: без служебных, самой марки и чисел."""
    out = []
    for w in re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9-]*", text):
        # Короткие слова и слитые с цифрами («XUUJA2G26P0004388» — это VIN) названием модели не бывают
        if w.lower() in _NOISE or len(w) < 4 or brand_of(w) or (len(w) >= 8 and re.search(r"\d", w)):
            continue
        out.append(w)
    return out


def _option_tokens(value: str) -> list[str]:
    return [t for t in re.split(r"[\s/,.()\-]+", value) if len(t) >= 3 and re.search(r"[A-Za-zА-Яа-я]", t)]


def match_options(words: list[str], options: list[dict], need: float = 0.95) -> list[dict]:
    """Варианты мастера, которые клиент назвал: слово и соседняя пара слов против слов варианта по звуковому ключу.
    Берём лучшие (в пределах 0,02) — «Octavia» и «Octavia A5» оба; слабее порога — ничего."""
    cands = list(words) + [a + b for a, b in zip(words, words[1:])]
    scored = []
    for o in options:
        best = 0.0
        for t in _option_tokens(o.get("value", "")):
            kt = key(t)
            for w in cands:
                kw = key(w)
                if len(kt) < 3 or len(kw) < 3:
                    continue
                best = max(best, difflib.SequenceMatcher(None, kw, kt).ratio())
        if best >= need:
            scored.append((best, o))
    if not scored:
        return []
    top = max(s for s, _ in scored)
    return [o for s, o in scored if s >= top - 0.02]


def year_of(text: str, vin: str = "") -> str:
    """Год: из слов клиента («2021 года», «2003—2007» → первый); нет — из 10-го знака VIN."""
    m = re.search(r"(?<!\d)(19[7-9]\d|20[0-3]\d)(?!\d)", text)
    if m:
        return m.group(1)
    if len(vin) == 17:
        letters = "ABCDEFGHJKLMNPRSTVWXY"
        c = vin[9].upper()
        if c in "123456789":
            return str(2000 + int(c))
        if c in letters[:17]:
            return str(2010 + letters.index(c))
    return ""


def pick_condition(rows: list, *needles: str) -> dict | None:
    for c in rows if isinstance(rows, list) else []:
        if any(n in str(c.get("name", "")).lower() for n in needles):
            return c
    return None


# Рынок варианта и откуда машина по VIN: китайский Karoq (SAIC VW) российскому не годится, американскую Forte в Европу не ищем
_REGION_OF_VIN = {"1": "na", "2": "na", "3": "na", "4": "na", "5": "na", "J": "jp", "K": "kr", "L": "cn"}
_MARKET = (("na", r"сша|usa|америк|\bus\b|канад|\bcan\b|мексик|\bmex\b"), ("cn", r"китай|china|saic|faw|gac|chery|\bcn\b"),
           ("jp", r"япон|japan|\bjpn\b"), ("au", r"австрал|\baus\b"))


def market_ok(market: str, vin: str = "") -> bool:
    """Подходит ли рынок варианта машине с таким VIN. Рынок не опознали — допускаем."""
    low = (market or "").lower()
    where = next((r for r, rx in _MARKET if re.search(rx, low)), "")
    if not where:
        return True
    home = _REGION_OF_VIN.get((vin or " ")[0].upper(), "eu")
    return where == home
