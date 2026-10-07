"""Почему по VIN несколько номеров одной детали и как выбрать — только из фактов, без догадок.

Lacetti 2005 г., задние колодки: каталог по VIN отдаёт 96405131 и 96800089 (обе match=True). Факты:
  аналоги       — у номеров по ~160 кроссов, общих 22: это разные детали, а не «новый номер старой»;
  «ставится с»  — в названии «SHOULD USE T/W 96800088»: вторые ставятся вместе с прижимной пружиной;
  соседи в узле — в «Заднем тормозе» три разных суппорта, какой на машине — каталог по VIN не знает;
  описания      — у 96800089 поставщики пишут «без ушек» (5 раз), у 96405131 — ни разу.
Из этого и собирается ответ клиенту + «как проверить»: посмотреть на снятую деталь или прислать фото.
"""
import collections
import re
from typing import Iterable

from . import text as T

# «SHOULD USE T/W 96800088», «USE WITH 1K0…», «ставится вместе с 8K0…»
_WITH = re.compile(r"(?:\bT/W\b|\bUSE\s+WITH\b|вместе\s+с)\s*[:#]?\s*([A-Z0-9][A-Z0-9 .-]{4,18}[A-Z0-9])", re.I)
# Слова-признаки: «без ушек», «с датчиком», «со шплинтом» — то, что видно на детали в руках
_FEATURE = re.compile(r"\b(без|с|со)\s+([а-яё]{3,}(?:\s+[а-яё]{3,})?)", re.I)
_NOT_FEATURE = {T.stem(w) for w in ("доставкой", "гарантией", "сертификатом", "документами", "чеком", "ндс", "учетом",
                                    "учётом", "года", "момента", "завода", "двигателем", "мотором", "кузовом",
                                    "комплекте", "сборе")}


def overlap(a: Iterable[str], b: Iterable[str]) -> tuple[int, int]:
    """Сколько артикулов-аналогов общих у двух номеров и из скольких (у меньшего)."""
    a, b = set(a or ()), set(b or ())
    return len(a & b), min(len(a), len(b))


def interchangeable(common: int, total: int) -> bool | None:
    """Одна деталь (почти те же аналоги), разные (почти нет общих) или не понять (мало данных / середина)."""
    if total < 8:
        return None
    share = common / total
    return True if share >= 0.6 else False if share <= 0.3 else None


def partner(*texts: str) -> str:
    """Номер, с которым деталь ставится вместе («T/W 96800088»), из названия или примечания каталога."""
    for t in texts:
        m = _WITH.search(t or "")
        if m:
            return re.sub(r"\s+", "", m.group(1)).upper()
    return ""


def _descs(rows: list[dict]) -> list[str]:
    """Одно описание на артикул: поставщики дублируют одно и то же десятками строк."""
    per: dict[tuple, str] = {}
    for r in rows or []:
        k = (str(r.get("brand") or "").lower(), re.sub(r"\W", "", str(r.get("number") or "").upper()))
        d = str(r.get("description") or "").strip()
        if d and k not in per:
            per[k] = d
    return list(per.values())


def _terms(desc: str, firms: set[str]) -> dict[str, str]:
    """Признаки в описании: «без ушек» и модели («Gentra»); ключ — для счёта, значение — как показать."""
    out: dict[str, str] = {}
    for m in _FEATURE.finditer(desc):
        words = m.group(2).split()
        if T.stem(words[0]) in _NOT_FEATURE:
            continue
        # «без ушек» — одно слово после предлога; второе берём, только если первое — прилагательное («с антискрип. пл»)
        shown = f"{m.group(1).lower()} {words[0].lower()}"
        out["f:" + m.group(1).lower().replace("со", "с") + " " + T.stem(words[0])] = shown
    # Модели из описаний («Jetta», «Solaris», «Sedan») не берём: по парку они ничего не объясняли, только шумели
    return out


def contrast(rows_a: list[dict], rows_b: list[dict], limit: int = 3) -> tuple[list[str], list[str]]:
    """Чем описания поставщиков одного номера отличаются от другого: признак часто у одного и почти никогда
    у другого (не меньше 3 артикулов и вчетверо чаще). Признаки «без/с …» — первыми: их видно на детали."""
    firms = {str(r.get("brand") or "").lower() for r in (rows_a or []) + (rows_b or [])}
    da, db = _descs(rows_a), _descs(rows_b)
    if len(da) < 5 or len(db) < 5:
        return [], []
    ca, cb = collections.Counter(), collections.Counter()
    shown: dict[str, str] = {}
    for descs, cnt in ((da, ca), (db, cb)):
        for d in descs:
            ts = _terms(d, firms)
            shown.update(ts)
            cnt.update(ts.keys())

    def pick(x: collections.Counter, y: collections.Counter, nx: int, ny: int) -> list[str]:
        good = [k for k, n in x.items() if n >= 3 and n / nx >= 0.02 and y[k] / ny <= (n / nx) / 4]
        good.sort(key=lambda k: (not k.startswith("f:"), -(x[k] / nx - y[k] / ny)))
        return [shown[k] for k in good[:limit]]
    return pick(ca, cb, len(da), len(db)), pick(cb, ca, len(db), len(da))


# Соседи, от которых зависит выбор: колодки — под суппорт. Остальное в узле («Корпус», «Шток», «Болт» у VAG без
# отметки match) — не причина, по парку только сбивало
_DECIDES = re.compile(r"суппорт|caliper|скоба\s+тормоз", re.I)


def economy(oem: str, name: str) -> bool:
    """VAG Economy: номера на JZW и «Economy»/«'ECO'» в названии — та же деталь от VAG, бюджетная линейка."""
    return bool(re.match(r"JZW", oem or "", re.I) or re.search(r"\beconomy\b|'eco'", name or "", re.I))


def siblings(details: list, variant_oems: set[str], variant_unit: str) -> tuple[str, int] | None:
    """В том же узле — несколько разных суппортов, которые каталог по VIN не различает (match не True):
    «три разных суппорта» у Lacetti. Левый и правый одной детали — один вид."""
    kinds: dict[str, set[str]] = collections.defaultdict(set)
    for d in details:
        if d.unit != variant_unit or d.oem in variant_oems or d.match is True or d.match is False \
                or not _DECIDES.search(d.name):
            continue
        base = re.sub(r"\b(?:LH|RH|L|R)\b|левы[йея]|правы[йея]|,?\s*$", "", d.name, flags=re.I).strip(" ,")
        if not base:
            continue
        side = T.side(d.name).lr or ("left" if re.search(r"\bLH\b", d.name) else "right" if re.search(r"\bRH\b", d.name) else "")
        kinds[base].add(f"{side}:{d.oem}")
    best = None
    for base, items in kinds.items():
        n = len({o.split(":", 1)[1] for o in items}) // max(len({o.split(":")[0] for o in items}), 1)
        if n >= 2 and (best is None or n > best[1]):
            best = (base, n)
    return best
