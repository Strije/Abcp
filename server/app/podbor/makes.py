"""Марка машины в словах клиента против марки по VIN: «шайбы форсунок на сан енг Кайрон» при VIN от BMW.

Без этой сверки робот молча подбирает деталь на машину из VIN, а клиент спрашивал про другую. Сверяем осторожно: машина,
названная клиентом, должна быть в нашей таблице, марка по VIN — тоже, и семейства у них разные (Toyota = Lexus, VW = Audi = Skoda,
Hyundai = Kia, Opel = Chevrolet = Daewoo — запчасти и клиенты там общие). Любое слово из семейства машины по VIN снимает вопрос."""
import re

# семейство → (как показать клиенту, марки каталога без пробелов и дефисов)
FAMILIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "vag": ("Volkswagen/Audi/Skoda", ("VOLKSWAGEN", "VW", "AUDI", "SKODA", "SEAT", "PORSCHE", "CUPRA")),
    "toyota": ("Toyota/Lexus", ("TOYOTA", "LEXUS")),
    "hk": ("Hyundai/Kia", ("HYUNDAI", "KIA", "GENESIS")),
    "nissan": ("Nissan/Infiniti", ("NISSAN", "INFINITI")),
    "renault": ("Renault", ("RENAULT", "DACIA")),
    "gm": ("Opel/Chevrolet", ("OPEL", "VAUXHALL", "CHEVROLET", "DAEWOO", "CADILLAC", "GM")),
    "psa": ("Peugeot/Citroen", ("PEUGEOT", "CITROEN", "DS")),
    "bmw": ("BMW", ("BMW", "MINI")),
    "mercedes": ("Mercedes-Benz", ("MERCEDES", "MERCEDESBENZ", "SMART")),
    "ford": ("Ford", ("FORD",)),
    "mitsubishi": ("Mitsubishi", ("MITSUBISHI",)),
    "mazda": ("Mazda", ("MAZDA",)),
    "honda": ("Honda", ("HONDA", "ACURA")),
    "subaru": ("Subaru", ("SUBARU",)),
    "suzuki": ("Suzuki", ("SUZUKI",)),
    "ssangyong": ("SsangYong", ("SSANGYONG",)),
    "volvo": ("Volvo", ("VOLVO",)),
    "landrover": ("Land Rover/Jaguar", ("LANDROVER", "JAGUAR", "RANGEROVER")),
    "fiat": ("Fiat/Alfa Romeo", ("FIAT", "ALFAROMEO", "LANCIA", "ABARTH")),
    "chery": ("Chery", ("CHERY",)),
    "geely": ("Geely", ("GEELY",)),
    "haval": ("Haval/Great Wall", ("HAVAL", "GREATWALL")),
    "changan": ("Changan", ("CHANGAN",)),
}

# слово клиента → семейство. Русские формы принимают окончания («тойоте»), латинские — как есть.
_WORDS: dict[str, tuple[str, ...]] = {
    "vag": ("volkswagen", "vw", "фольксваген", "фольц", "audi", "ауди", "skoda", "шкода", "seat", "сеат", "порше", "гольф", "golf",
            "поло", "тигуан", "tiguan", "пассат", "passat", "джетта", "jetta", "октавия", "octavia", "рапид", "rapid", "йети", "yeti",
            "суперб", "superb", "фабия", "fabia"),
    "toyota": ("toyota", "тойота", "тоёта", "lexus", "лексус", "камри", "camry", "королла", "corolla", "рав4", "rav4", "прадо", "prado",
               "ленд крузер", "land cruiser", "виста", "аурис", "auris", "авенсис", "avensis", "хайлендер", "highlander"),
    "hk": ("hyundai", "хундай", "хендай", "хёндай", "хьюндай", "kia", "киа", "кия", "солярис", "solaris", "крета", "creta",
           "элантра", "elantra", "туксон", "tucson", "санта фе", "santa fe", "рио", "rio", "ceed", "спортейдж", "sportage",
           "соренто", "sorento", "пиканто", "picanto", "церато", "cerato", "оптима", "optima"),
    "nissan": ("nissan", "ниссан", "infiniti", "инфинити", "х трейл", "x trail", "x-trail", "кашкай", "qashqai", "juke",
               "альмера", "almera", "теана", "teana", "микра", "micra", "тиида", "tiida", "мурано", "murano"),
    "renault": ("renault", "рено", "логан", "logan", "сандеро", "sandero", "дастер", "duster", "меган", "megane", "клио", "clio",
                "каптур", "kaptur", "флюенс", "fluence", "трафик", "traffic"),
    "gm": ("opel", "опель", "chevrolet", "шевроле", "шевролет", "daewoo", "дэу", "деу", "астра", "astra", "корса", "corsa",
           "вектра", "vectra", "зафира", "zafira", "мерива", "meriva", "инсигния", "insignia", "лачетти", "lacetti", "авео", "aveo",
           "круз", "cruze", "каптива", "captiva", "ланос", "lanos", "нексия", "nexia", "матиз", "matiz", "кобальт", "cobalt"),
    "psa": ("peugeot", "пежо", "citroen", "ситроен", "ситроэн", "берлинго", "berlingo"),
    "bmw": ("bmw", "бмв", "бумер", "mini cooper", "мини купер"),
    "mercedes": ("mercedes", "мерседес", "мерс", "спринтер", "sprinter", "виано", "viano", "вито", "vito"),
    "ford": ("ford", "форд", "фокус", "focus", "мондео", "mondeo", "фиеста", "fiesta", "куга", "kuga", "транзит", "transit"),
    "mitsubishi": ("mitsubishi", "митсубиси", "митсубиши", "мицубиси", "лансер", "lancer", "аутлендер", "оутлендер", "outlander",
                   "паджеро", "pajero", "l200", "л200"),
    "mazda": ("mazda", "мазда", "cx5", "cx-5", "cx 5"),
    "honda": ("honda", "хонда", "цивик", "civic", "аккорд", "accord", "acura", "акура"),
    "subaru": ("subaru", "субару", "импреза", "impreza", "форестер", "forester"),
    "suzuki": ("suzuki", "сузуки", "свифт", "swift", "гранд витара", "grand vitara", "витара", "vitara"),
    "ssangyong": ("ssangyong", "ssang yong", "сангйонг", "санг йонг", "сан енг", "сан йонг", "сансенг", "саньенг", "санйонг",
                  "кайрон", "kyron", "актион", "actyon", "рекстон", "rexton", "корандо", "korando"),
    "volvo": ("volvo", "вольво"),
    "landrover": ("land rover", "ленд ровер", "ландровер", "range rover", "рэнж ровер", "рендж ровер", "jaguar", "ягуар",
                  "дискавери", "discovery", "фрилендер", "freelander"),
    "fiat": ("fiat", "фиат", "альфа ромео", "alfa romeo"),
    "chery": ("chery", "чери"),
    "geely": ("geely", "джили"),
    "haval": ("haval", "хавейл", "хавал", "great wall", "грейт волл"),
    "changan": ("changan", "чанган"),
}


def _compile() -> tuple[re.Pattern, dict[str, str]]:
    forms: dict[str, str] = {}
    for fam, words in _WORDS.items():
        for w in words:
            forms[w.replace("ё", "е")] = fam
    alt = "|".join(sorted((re.escape(w).replace(r"\ ", r"[\s\-]*") + (r"[а-я]{0,3}" if re.search("[а-я]", w) else "")
                           for w in forms), key=len, reverse=True))
    return re.compile(r"(?<![а-яa-z0-9])(?:" + alt + r")(?![а-яa-z0-9])", re.I), forms


_RX, _FORMS = _compile()
_BRAND_FAMILY = {b: fam for fam, (_, brands) in FAMILIES.items() for b in brands}
_ORIGINAL = re.compile(r"(?:оригинал\w*|original|ориг\.?|фирменн\w*)\s*$|^\s*(?:оригинал\w*|original|ориг\.?)", re.I)


def family_of_brand(brand: str) -> str:
    return _BRAND_FAMILY.get(re.sub(r"[^A-Z]", "", (brand or "").upper()), "")


_NORM = {re.sub(r"[\s\-]+", "", k): fam for k, fam in _FORMS.items()}


def _form_of(word: str) -> str:
    w = re.sub(r"[\s\-]+", "", word.lower().replace("ё", "е"))
    for n in range(len(w), 1, -1):          # русская форма с окончанием: «тойоте» → «тойота»
        if w[:n] in _NORM:
            return _NORM[w[:n]]
    return ""


def mentioned(text: str) -> list[tuple[str, str]]:
    """[(слова клиента, семейство)] — марки и ходовые модели в тексте. «Оригинал Toyota» — про фирму детали, не про машину."""
    low = text.replace("ё", "е").replace("Ё", "е")
    out = []
    for m in _RX.finditer(low):
        before, after = low[max(0, m.start() - 14):m.start()], low[m.end():m.end() + 14]
        if _ORIGINAL.search(before) or re.match(r"\s*(?:оригинал|original)", after, re.I):
            continue
        fam = _form_of(m.group(0))
        if fam:
            out.append((text[m.start():m.end()].strip(), fam))
    return out


def mismatch(text: str, brand: str) -> tuple[str, str] | None:
    """(как назвал клиент, семейство клиента), если названа машина другой марки, чем по VIN; иначе None."""
    mine = family_of_brand(brand)
    if not mine:
        return None
    said = mentioned(text)
    if not said or any(fam == mine for _, fam in said):
        return None
    word, fam = said[0]
    return word, fam
