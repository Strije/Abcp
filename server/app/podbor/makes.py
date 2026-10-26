"""Марка машины в словах клиента против марки по VIN: «шайбы форсунок на сан енг Кайрон» при VIN от BMW.

Без этой сверки робот молча подбирает деталь на машину из VIN, а клиент спрашивал про другую. Сверяем осторожно: машина,
названная клиентом, должна быть в нашей таблице, марка по VIN — тоже, и семейства у них разные (Toyota = Lexus, VW = Audi = Skoda,
Hyundai = Kia, Opel = Chevrolet = Daewoo — запчасти и клиенты там общие). Любое слово из семейства машины по VIN снимает вопрос."""
import re

from . import knowledge as K

# семейство → (как показать клиенту, марки каталога без пробелов и дефисов) и слова клиента → семейство
# (русские формы принимают окончания — «тойоте», латинские — как есть): data/podbor_knowledge.json, раздел makes
FAMILIES: dict[str, tuple[str, tuple[str, ...]]] = K.families()
_WORDS: dict[str, tuple[str, ...]] = K.family_words()


def _compile() -> tuple[re.Pattern, dict[str, str]]:
    forms: dict[str, str] = {}
    for fam, words in _WORDS.items():
        for w in words:
            forms[w.replace("ё", "е")] = fam
    def one(w: str) -> str:
        body = re.escape(w).replace(r"\ ", r"[\s\-]*")
        if not re.search("[а-я]", w):
            return body
        if w[-1] in "ая":
            body = body[:-1]
        return body + r"(?:а|я|у|ы|е|и|ю|ой|ою|ей|ом|ов|ам|ах|ами)?"
    alt = "|".join(sorted((one(w) for w in forms), key=len, reverse=True))
    return re.compile(r"(?<![а-яa-z0-9])(?:" + alt + r")(?![а-яa-z0-9])", re.I), forms


_RX, _FORMS = _compile()
_BRAND_FAMILY = {b: fam for fam, (_, brands) in FAMILIES.items() for b in brands}
_ORIGINAL = re.compile(r"(?:оригинал\w*|original|ориг\.?|фирменн\w*)\s*$|^\s*(?:оригинал\w*|original|ориг\.?)", re.I)


def family_of_brand(brand: str) -> str:
    return _BRAND_FAMILY.get(re.sub(r"[^A-Z]", "", (brand or "").upper()), "")


_NORM = {re.sub(r"[\s\-]+", "", k): fam for k, fam in _FORMS.items()}
_NORM |= {k[:-1]: fam for k, fam in list(_NORM.items()) if re.search("[а-я]", k) and k[-1] in "ая"}


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
