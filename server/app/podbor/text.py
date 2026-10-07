"""Текст заявки: VIN / кузов / госномер, список позиций, основы слов и указания стороны.

Заявка с сайта ABCP выглядит так:
    Модель авто: FORD FOCUS III Хетчбек 1.6 Ti 125 л.с. 2012 год
    VIN/FRAME: X9FKXXEEBKCB57566
    Запрос состоит из следующих позиций:  Подшипник задней ступицы форд фокус 3
Свободный текст («вот вин …, нужны передние колодки») разбирается так же, только без шаблона.
"""
import re
from dataclasses import dataclass, field
from functools import lru_cache

import snowballstemmer

_stemmer = snowballstemmer.stemmer("russian")

# Кириллица, похожая на латиницу: люди набирают VIN и госномер вперемешку
_TO_LATIN = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")
_TO_CYR = str.maketrans("ABEKMHOPCTYX", "АВЕКМНОРСТУХ")
_VIN_TOKEN = re.compile(r"(?<![0-9A-ZА-Я])[0-9A-ZА-Я]{17}(?![0-9A-ZА-Я])")
_FRAME = re.compile(r"(?<![0-9A-Z-])[A-Z0-9]{2,8}-\d{4,8}(?![0-9-])")
_PLATE = re.compile(r"(?<![0-9А-Я])[АВЕКМНОРСТУХ]\s?\d{3}\s?[АВЕКМНОРСТУХ]{2}\s?\d{2,3}(?!\d)")
_VIN_OK = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

_MODEL = re.compile(r"модель\s+авто\s*:\s*(.+)", re.I)
_IDENT_LINE = re.compile(r"vin\s*/?\s*(?:frame)?\s*:\s*(\S+)", re.I)
_POSITIONS = re.compile(r"запрос\s+состоит\s+из\s+следующих\s+позиций\s*:?(.*)", re.I | re.S)

# Сторона в тексте запроса и в каталоге. «передач» (коробка передач) — не «перед».
FRONT = re.compile(r"\b(?:передн[а-яё]*|перед(?![а-яё])|пер\.|спереди(?![а-яё])|front(?![a-z])|fr(?![a-z]))", re.I)
REAR = re.compile(r"\b(?:задн[а-яё]*|зад(?![а-яё])|сзади(?![а-яё])|rear(?![a-z])|rr(?![a-z]))", re.I)
LEFT = re.compile(r"\b(?:лев[а-яё]*|слева(?![а-яё])|lh(?![a-z])|left(?![a-z]))", re.I)
RIGHT = re.compile(r"\b(?:прав(?!ил)[а-яё]*|справа(?![а-яё])|rh(?![a-z])|right(?![a-z]))", re.I)
_PR = re.compile(r"PR\s*:\s*([0-9A-Z]{3})")

_WORD = re.compile(r"[a-zа-яё0-9]+", re.I)


@dataclass
class Request:
    ident: str = ""          # VIN или номер кузова
    plate: str = ""          # госномер, если VIN нет
    model: str = ""          # «Модель авто» как написал клиент
    chunks: list[str] = field(default_factory=list)  # позиции до разбиения по запятым


_TRANSLIT = {**{str(i): i for i in range(10)}, **dict(zip("ABCDEFGH", range(1, 9))), **dict(zip("JKLMN", range(1, 6))),
             "P": 7, "R": 9, **dict(zip("STUVWXYZ", range(2, 10)))}
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)


def _check_digit_ok(vin: str) -> bool:
    """Девятый знак VIN — контрольный (ISO 3779, обязателен в США и Канаде)."""
    try:
        total = sum(_TRANSLIT[c] * w for c, w in zip(vin, _WEIGHTS)) % 11
    except KeyError:
        return False
    return vin[8] == ("X" if total == 10 else str(total))


def _vin(token: str) -> str | None:
    """17 знаков → VIN. O/I/Q в VIN не бывает — это опечатки вместо 0/1/0."""
    s = token.upper().translate(_TO_LATIN).replace("O", "0").replace("I", "1").replace("Q", "0")
    if not (_VIN_OK.match(s) and re.search(r"\d", s) and re.search(r"[A-Z]", s)):
        return None
    # Склеенные слова с фото СТС («HRETETLCTEGJLAT10») — почти без цифр: у настоящего VIN их не меньше пяти,
    # и в хвосте цифры (по ISO 3779 последние четыре — цифры; у Z94C241BALR15141B в хвосте буква, это бывает)
    if sum(c.isdigit() for c in s) < 5 or sum(c.isdigit() for c in s[-4:]) < 3:
        return None
    # Контрольная цифра обязательна только в Северной Америке (VIN на 1–5): у остальных её нет, 2/3 настоящих не сходятся
    if s[0] in "12345" and not _check_digit_ok(s):
        return None
    return s


def find_ident(text: str) -> str:
    m = _IDENT_LINE.search(text)
    if m:
        v = _vin(m.group(1)) or (m.group(1).upper() if _FRAME.fullmatch(m.group(1).upper()) else None)
        if v:
            return v
    for t in _VIN_TOKEN.findall(text.upper()):
        v = _vin(t)
        if v:
            return v
    m = _FRAME.search(text.upper())
    return m.group(0) if m else ""


def find_plate(text: str) -> str:
    m = _PLATE.search(text.upper().translate(_TO_CYR))
    return re.sub(r"\s", "", m.group(0)) if m else ""


def parse(text: str) -> Request:
    r = Request(ident=find_ident(text))
    if not r.ident:
        r.plate = find_plate(text)
    m = _MODEL.search(text)
    if m:
        r.model = m.group(1).strip()
    m = _POSITIONS.search(text)
    if m:
        body = m.group(1)
    else:
        # Свободный текст: всё, кроме служебных строк и самого VIN
        body = "\n".join(line for line in text.splitlines()
                         if not _MODEL.search(line) and not _IDENT_LINE.search(line))
        for token in (r.ident, r.plate):
            if token:
                body = re.sub(re.escape(token), " ", body, flags=re.I)
        body = _VIN_TOKEN.sub(" ", body)
    r.chunks = split_chunks(expand(body))
    return r


def split_chunks(body: str) -> list[str]:
    """Позиции: по строкам, «;» и нумерации «1.»/«2)». Запятые — потом, когда понятно, где деталь."""
    parts = re.split(r"[\n;]+|(?:(?<=\s)|^)\d{1,2}[.)](?=\s)", body)
    return [p.strip(" ,.-—–\t") for p in parts if p and p.strip(" ,.-—–\t")]


# Сокращения через «/» и «к-т» — раскрываем до разбора, иначе «/» режет позицию пополам:
# «С/блок внутренний зад. ниж. рычага», «сальник ступицы и п/о», «г/ц сцепления».
_SHORT = [
    (re.compile(r"(?<![а-яё])с\s*/\s*б(?:лок(?:и|а|ов)?)?(?![а-яё])", re.I), "сайлентблок"),
    (re.compile(r"(?<![а-яё])ш\s*/\s*о(?:пор[аыу])?(?![а-яё])", re.I), "шаровая опора"),
    (re.compile(r"(?<![а-яё])п\s*/\s*о(?:с[ьи])?(?![а-яё])", re.I), "полуось"),
    # Как пишут поставщики: «Сайленблок задней цапфы», «Полиуретан. сайл.блок задней подв»
    (re.compile(r"(?<![а-яё])(?:сайленблок|сайл\.\s*блок)", re.I), "сайлентблок"),
    # «Плавающий сайлентблок» — сайлентблок задней цапфы (кулака) с шарниром внутри: Toyota, Lexus, Ford Focus, Mazda 3
    (re.compile(r"плавающ[а-яё]*\s+(сайлентблок[а-яё]*)", re.I), r"\1 задней цапфы"),
    (re.compile(r"(сайлентблок[а-яё]*)\s+плавающ[а-яё]*", re.I), r"\1 задней цапфы"),
    (re.compile(r"(?<![а-яё])плавающ(?:ие|их)(?![а-яё])", re.I), "сайлентблоки задней цапфы"),   # «Задок: плавающие»
    # «Масло с фильтрами хочу поменять» — замена масла: масло и масляный фильтр (про остальные фильтры спросим)
    (re.compile(r"(?<![а-яё])масл[оа]\s+(?:с|и|\+|плюс)\s+фильтр(?:ом|ами|ы|а)?(?![а-яё])", re.I),
     "моторное масло, масляный фильтр"),
    (re.compile(r"(?<![а-яё])г\s*/\s*ц(?![а-яё])", re.I), "главный цилиндр"),
    (re.compile(r"(?<![а-яё])р\s*/\s*ц(?![а-яё])", re.I), "рабочий цилиндр"),
    (re.compile(r"(?<![а-яё])р\s*/\s*к(?![а-яё])", re.I), "ремкомплект"),
    (re.compile(r"(?<![а-яё])т\s*/\s*ж(?![а-яё])", re.I), "тормозная жидкость"),
    (re.compile(r"(?<![а-яё])о\s*/\s*ж(?![а-яё])", re.I), "охлаждающая жидкость"),
    (re.compile(r"(?<![а-яё])к-?к?т(?![а-яё])\.?", re.I), "комплект"),
]


def expand(text: str) -> str:
    for rx, full in _SHORT:
        text = rx.sub(full, text)
    return text


def split_pieces(chunk: str) -> list[str]:
    return [p.strip() for p in re.split(r",|\+|\s+и\s+|/", expand(chunk)) if p.strip()]


# Прилагательное: окончание прилагательного и основа на -н/-ск/-ов/-ев/-ющ… («топливный» → «топливн»).
# «помпой» (тоже на -ой) сюда не попадёт: основа «помп». Нужно, чтобы найти главное слово:
# в «топливный фильтр» это «фильтр», а не первое слово.
# «-ей» — только у причастий («охлаждающей»): «ремней», «шестерней», «вкладышей» — существительные.
_ADJ_END = re.compile(r"(?:ый|ий|ой|ая|яя|ое|ее|ые|ие|ого|его|ому|ему|ую|юю|ым|им|ых|их|ыми|ими)$|(?:ющ|ащ|ящ|ущ)ей$")
_ADJ_STEM = re.compile(r"(?:н|ск|цк|ов|ев|ющ|ящ|ащ|ущ)$")
ADJ: set[str] = set()


def norm(word: str) -> str:
    return word.lower().replace("ё", "е").replace("ë", "е").replace("ъ", "ь")


@lru_cache(maxsize=20000)
def stem(word: str) -> str:
    w = norm(word)
    s = _stemmer.stemWord(w)
    if _ADJ_END.search(w) and _ADJ_STEM.search(s) and len(s) >= 4:
        ADJ.add(s)
    return s


def head(q: list[str]) -> str | None:
    """Главное слово фразы: первое не прилагательное («топливный фильтр» → «фильтр»)."""
    return next((s for s in q if s not in ADJ), q[0] if q else None)


# «Рем комплект», «рем. комплект» → «ремкомплект»
_REMKOMPLEKT = re.compile(r"\bрем\.?\s*(?=комплект)", re.I)


def words(text: str) -> list[str]:
    return [norm(w) for w in _WORD.findall(text or "")]


# Сокращение с точкой — и посреди фразы («торм. колодки»), и в конце или перед скобкой: «фильтр топл.»,
# «фильтр масл. (вставка)» — без этого «топл» никуда не вело и фильтр уходил в трансмиссионный
_ABBR = re.compile(r"([a-zа-яё0-9]+)(\.(?=\s*[a-zа-яё]|\s*$|\s*[,;:()\[\]!?/\\]))?", re.I)


def stems(text: str, stop: frozenset[str] = frozenset()) -> list[str]:
    """Основы значимых слов: без чисел, коротких слов, стоп-слов и указаний стороны.
    Сокращение с точкой посреди фразы («Комплект торм. колодок», «Повор.кулак») помечаем точкой:
    оно совпадает с любым словом, которое так начинается."""
    out = []
    src = _REMKOMPLEKT.sub("рем", text or "")
    for m in _ABBR.finditer(src):
        w = norm(m.group(1))
        if len(w) < 3 or any(ch.isdigit() for ch in w) or side_of_word(w):
            continue
        s = stem(w)
        if s in stop or w in stop:
            continue
        abbr = bool(m.group(2)) and len(w) <= 6
        if abbr and not re.match(r"\s*[a-zа-яё]", src[m.end():], re.I):
            # В конце фразы точка чаще конец предложения: «Нужен ремень.» Сокращение там — короткое
            # и на согласную: «фильтр топл.», «возд.», «масл.», «торм.»
            abbr = len(w) <= 5 and w[-1] not in "аеёиоуыэюяьйaeiouy"
        out.append(w + "." if abbr else s)
    return out


def side_of_word(w: str) -> bool:
    return bool(FRONT.fullmatch(w) or REAR.fullmatch(w) or LEFT.fullmatch(w) or RIGHT.fullmatch(w))


def same(a: str, b: str) -> bool:
    """Одна и та же основа: совпадают или общее начало ≥5 букв почти во всю длину
    («ступиц» ~ «ступичн», «колодк» ~ «колодок», но «крыш» ≠ «крышк»)."""
    if a == b:
        return True
    if fleeting(a, b) or fleeting(b, a):
        return True
    if a.endswith(".") or b.endswith("."):
        abbr, full = (a, b) if a.endswith(".") else (b, a)
        core = abbr[:-1]
        return len(core) >= 3 and (full.rstrip(".").startswith(core) or core.startswith(full.rstrip(".")))
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    # Разница в длине — не больше двух букв: «стекл» ≠ «стеклоочистител», «датчик» ≠ «датчикabs» (склейка)
    return n >= 5 and n >= min(len(a), len(b)) - 1 and abs(len(a) - len(b)) <= 2


def fleeting(a: str, b: str) -> bool:
    """Беглая гласная: «ремен» (ремень) ~ «ремн» (ремня), «бачок» ~ «бачк», «замок» ~ «замк»."""
    return len(a) >= 5 and a[-2] in "ео" and a[:-2] + a[-1] == b


def near(a: str, b: str) -> bool:
    """Одна опечатка: замена, пропуск или лишняя буква. Короче 5 букв не правим — «крыш» и «крыс» разные."""
    if a == b or abs(len(a) - len(b)) > 1 or min(len(a), len(b)) < 5:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


@dataclass(frozen=True)
class Side:
    axis: str = ""   # front / rear / "" (не сказано)
    lr: str = ""     # left / right / ""

    def conflicts(self, other: "Side") -> bool:
        return bool((self.axis and other.axis and self.axis != other.axis)
                    or (self.lr and other.lr and self.lr != other.lr))


def side(text: str, pr_axis: dict[str, str] | None = None) -> Side:
    """Сторона по тексту. Если в тексте и «перед», и «зад» — сторона не определена.
    PR-коды VAG (1Z…/1L… — передние тормоза, 1K… — задние) — только когда слов нет."""
    t = text or ""
    f, r = bool(FRONT.search(t)), bool(REAR.search(t))
    axis = "front" if f and not r else "rear" if r and not f else ""
    if not axis and pr_axis:
        found = {pr_axis[c[:2]] for c in _PR.findall(t) if c[:2] in pr_axis}
        if len(found) == 1:
            axis = found.pop()
    lft, rgt = bool(LEFT.search(t)), bool(RIGHT.search(t))
    lr = "left" if lft and not rgt else "right" if rgt and not lft else ""
    return Side(axis, lr)


# ---------- согласование: «Нужен передний подшипник», «Нужна передняя стойка», «Нужны передние колодки» ----------

# Женский род на мягкий знак — остальные на «-ь» в запчастях мужского рода («ремень», «шкворень»)
_FEM_SOFT = {"дверь", "ось", "полуось", "цепь", "петля", "тяга", "щель", "сеть", "мазь"}
_FORMS = {
    "m": {"need": "Нужен", "front": "передний", "rear": "задний", "left": "левый", "right": "правый"},
    "f": {"need": "Нужна", "front": "передняя", "rear": "задняя", "left": "левая", "right": "правая"},
    "n": {"need": "Нужно", "front": "переднее", "rear": "заднее", "left": "левое", "right": "правое"},
    "pl": {"need": "Нужны", "front": "передние", "rear": "задние", "left": "левые", "right": "правые"},
}
_NEED_WORDS = {"нужен", "нужна", "нужно", "нужны", "надо", "нужен", "нужно", "интересует", "есть", "для"}


def _adj_kind(w: str) -> str | None:
    for end, kind in (("ая", "f"), ("яя", "f"), ("ое", "n"), ("ее", "n"), ("ые", "pl"), ("ие", "pl"),
                      ("ый", "m"), ("ий", "m"), ("ой", "m")):
        if w.endswith(end):
            return kind
    return None


# Сокращения: род по главному слову расшифровки («ГБЦ» — головка, «АКПП» — коробка)
_ABBR_GENDER = {"гбц": "f", "акпп": "f", "мкпп": "f", "кпп": "f", "шрус": "m", "шруз": "m", "грм": "m", "тнвд": "m",
                "эбу": "m", "дмрв": "m", "гур": "m", "эур": "m", "абс": "f", "abs": "f", "дпкв": "m", "дпрв": "m"}


def gender(query: str) -> str:
    """Род и число главного слова запроса: m / f / n / pl. Первое существительное — не прилагательное,
    не сторона и не «нужен»; если одни прилагательные («шаровая») — по окончанию прилагательного."""
    ws = [w for w in words(query) if len(w) >= 3 and not w.isdigit() and re.fullmatch(r"[а-яё]+", w)]
    first_adj = None
    for w in ws:
        if side_of_word(w) or w in _NEED_WORDS:
            continue
        if _ADJ_END.search(w) and _ADJ_STEM.search(stem(w)):
            first_adj = first_adj or _adj_kind(w)
            continue
        if w in _ABBR_GENDER:
            return _ABBR_GENDER[w]
        if w[-1] in "ыи":
            return "pl"
        if w[-1] in "ая":
            return "f"
        if w[-1] in "оеё":
            return "n"
        if w[-1] == "ь":
            return "f" if w in _FEM_SOFT else "m"
        return "m"
    return first_adj or "pl"


def side_label(query: str, axis: str, lr: str = "") -> str:
    """«Передний», «Задняя левая», «Передние» — сторона в согласии с деталью из запроса."""
    f = _FORMS[gender(query)]
    s = " ".join(f[x] for x in (axis, lr) if x)
    return s[:1].upper() + s[1:]


def ask_axis(query: str) -> str:
    f = _FORMS[gender(query)]
    return f"{f['need']} {f['front']} или {f['rear']}?"


AXIS_RU = {"front": "передн.", "rear": "задн."}
LR_RU = {"left": "лев.", "right": "прав."}
