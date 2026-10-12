"""Laximo для подбора: разбор ответов, кэш и поиск группы по словам клиента.

Дерево быстрых групп (≈400 групп с синонимами вроде «воздухан», «салонник») одно на каталог
и меняется редко — храним на диске неделю, поэтому слова клиента сопоставляются без запросов
к Laximo. Машину по VIN и списки деталей держим в памяти несколько часов: менеджер часто
спрашивает по той же машине ещё раз.
"""
import asyncio
import gzip
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import text as T
from . import wizard as W

# Всё, что пришло из Laximo, хранится на диске навсегда и берётся оттуда: это и кэш, и материал для
# проверок и обучения (название группы → как детали называются у разных марок). Тариф платится раз
# в месяц, лимит неизвестен — повторно одно и то же не спрашиваем. Обновить — удалить файл.
TREE_TTL = float("inf")
VEHICLE_TTL = float("inf")
DETAILS_TTL = float("inf")


class LaximoError(Exception):
    """Ошибка Laximo вида E_…: каталог ответил, но не то, что нужно."""

    def __init__(self, code: str, extra: str = ""):
        super().__init__(f"{code}:{extra}" if extra else code)
        self.code = code


def check_error(data: Any) -> Any:
    if isinstance(data, dict) and str(data.get("message", "")).startswith("E_"):
        code, _, extra = str(data["message"]).partition(":")
        raise LaximoError(code, extra)
    return data


# ---------- машина ----------

@dataclass
class Vehicle:
    catalog: str
    vehicle_id: str
    ssd: str
    brand: str
    name: str
    attrs: dict[str, str]   # key → значение (без огромного списка опций)

    def summary(self) -> str:
        a = self.attrs
        parts = [f"{self.brand} {self.name}".strip()]
        engine, info = a.get("engine"), a.get("engine_info")
        if engine or info:
            parts.append(f"{engine} ({info})" if engine and info else engine or info)
        if a.get("transmission"):
            parts.append(f"КПП {a['transmission']}")
        # body_style в сводку не берём: у Ford «5-дверный седан» — это хэтчбек (5-door saloon)
        when = a.get("date") or a.get("manufactured")
        if when:
            parts.append("выпуск " + when.replace("/", "."))
        return ", ".join(parts)

    def short(self) -> str:
        """Для клиента: «Ford Focus CB8, 2012 г., 1.6 л 123 л.с., АКПП» — без кодов моторов и диапазонов лет."""
        a = self.attrs
        brand = _MAKE_NAMES.get(self.brand.upper().replace(" ", "")) or \
            (self.brand.title() if self.brand.isupper() and len(self.brand) > 3 else self.brand)
        name = re.sub(r"\s*\(?\b(19|20)\d\d\s*[-–]\s*((19|20)\d\d)?\)?\s*$", "", self.name).strip()
        if name.isupper() and len(name) > 3:
            name = name.title()   # «FORTUNER» → «Fortuner»; «X5», «CX-5» не трогаем
        parts = [f"{brand} {name}".strip()]
        year = a.get("manufactured") or (re.search(r"(19|20)\d\d", a.get("date", "")) or [""])[0]
        if year:
            parts.append(f"{year[:4]} г.")
        eng = " ".join(a.get(k, "") for k in ("engine", "engine_info"))
        vol = re.search(r"\b(\d)[.,](\d)\s*L\b", eng, re.I) or re.search(r"\b(\d)(\d)\d\d\s*CC\b", eng, re.I)
        hp = re.search(r"\b(\d{2,3})\s*(?:PS|hp|л\.?\s*с)", eng, re.I)
        motor = " ".join(x for x in (f"{vol.group(1)}.{vol.group(2)} л" if vol else "",
                                     f"{hp.group(1)} л.с." if hp else "") if x)
        if motor:
            parts.append(motor)
        # Коробку пишем, только если каталог назвал её словами: у VAG там код («LKS») — по нему
        # не понять, механика это или вариатор multitronic, а ошибка в ответе клиенту хуже пропуска
        box = a.get("transmission", "").upper()
        kpp = ("вариатор" if re.search(r"CVT|ВАРИАТ|MULTITRONIC", box)
               else "АКПП" if re.search(r"АКПП|AUTO|\bAT\b|DSG|S.?TRONIC|TIPTRONIC|DCPS|POWERSHIFT|РОБОТ", box)
               else "МКПП" if re.search(r"МКПП|MANUAL|\bMT\b|МЕХАН|\d.?(?:СТУП|SPEED).*(?:МЕХ|MAN)", box) else "")
        if kpp:
            parts.append(kpp)
        return ", ".join(parts)

    def public(self) -> dict:
        return {"brand": self.brand, "name": self.name, "catalog": self.catalog,
                "summary": self.summary(), "short": self.short(), "attributes": self.attrs}


def parse_vehicles(data: Any) -> list[Vehicle]:
    rows = data
    if isinstance(data, dict):
        rows = next((data[k] for k in ("rows", "vehicles", "data") if isinstance(data.get(k), list)), [data])
    out = []
    for o in rows if isinstance(rows, list) else []:
        if not isinstance(o, dict) or not o.get("catalog") or not o.get("ssd"):
            continue
        attrs = {str(a.get("key")): str(a.get("value") or "") for a in o.get("attributes") or []
                 if isinstance(a, dict) and a.get("key") and a.get("key") != "options"}
        out.append(Vehicle(catalog=str(o["catalog"]), vehicle_id=str(o.get("vehicleId") or "0"),
                           ssd=str(o["ssd"]), brand=str(o.get("brand") or ""), name=str(o.get("name") or ""),
                           attrs=attrs))
    return out


# ---------- детали ----------

@dataclass
class Detail:
    oem: str
    name: str
    note: str
    amount: str
    match: bool | None
    unit: str
    unit_note: str
    unit_id: str
    unit_ssd: str
    image: str
    code_on_image: str
    category: str
    group_id: int = 0
    unit_code: str = ""   # код узла полного каталога («60-624»): у Hyundai/Kia номера главных деталей начинаются с «624»
    # Фильтр Laximo (Renault, Mazda, BMW: VIN не различает комплектацию) — по нему getFilterByDetail даёт вопрос клиенту
    # («АБС: да / нет»); ssd самой детали — для этого запроса; specs — ВСЕ примечания детали («АБС = Нет»): у Renault их
    # несколько с одним именем «Примечание», и в note остаётся только последнее
    filter: str = ""
    ssd: str = ""
    specs: str = ""
    # Прежние номера той же детали с их названиями: у Renault сторона только там — «7700827251 SOL AKS TAŞIYICI» (R19, 12.10)
    replaced: str = ""

    @property
    def context(self) -> str:
        """Всё, где каталог пишет сторону: VAG — в примечании узла («передн.;PR:1ZC»),
        Ford — в названии узла («Задний кулак…»), суппорты — в примечании детали («лев.»)."""
        return " | ".join(x for x in (self.name, self.note, self.unit, self.unit_note, self.category, self.replaced) if x)


def _attrs(o: dict) -> dict[str, str]:
    out = {}
    for a in o.get("attributes") or []:
        if isinstance(a, dict):
            key = str(a.get("key") or a.get("name") or "")
            if a.get("name") == "Примечание":
                key = "note"
            out[key] = str(a.get("value") or "")
    return out


def _specs(o: dict) -> str:
    """Все примечания детали одной строкой — и с одинаковыми именами (у Renault «Примечание» встречается трижды)."""
    return " ; ".join(str(a.get("value") or "") for a in o.get("attributes") or []
                      if isinstance(a, dict) and a.get("key") not in ("amount",) and a.get("value"))


# Машинный перевод Laximo, по которому деталь не найти словами клиента: у Toyota «CARRIER SUB-ASSY, RR AXLE»
# (42304/42305 — задняя цапфа, в неё запрессован «плавающий сайлентблок») переведено как
# «Крепление заднего моста, правый (в подсборе)»
_RENAME = [(re.compile(r"^Крепление заднего моста", re.I), re.compile(r"^4230[45]"), "Задний кулак (цапфа)"),
           # Hyundai/Kia «COVER ASSY-FRONT BUMPER» — это и есть бампер (облицовка), а «крышка» роботу — мелочь:
           # на «бампер передний» он показывал гаситель и выступ, а сам бампер прятал
           (re.compile(r"^КРЫШКА В СБОРЕ-ПЕРЕДН\.?\s*БАМПЕР.*", re.I), re.compile(r"^865"), "Бампер передний (облицовка в сборе)"),
           (re.compile(r"^КРЫШКА В СБОРЕ-ЗАДН\.?\s*БАМПЕР.*", re.I), re.compile(r"^866"), "Бампер задний (облицовка в сборе)"),
           # Peugeot 307: сам бампер — по-английски («OUT OF PRODUCTION FRONT BUMPER», 7401 T4), русский поиск его не видел
           (re.compile(r"^OUT OF PRODUCTION FRONT BUMPER$", re.I), re.compile(r"."), "Бампер передний (снят с производства)"),
           (re.compile(r"^OUT OF PRODUCTION REAR BUMPER$", re.I), re.compile(r"."), "Бампер задний (снят с производства)"),
           (re.compile(r"^FRONT BUMPER$", re.I), re.compile(r"."), "Бампер передний"),
           (re.compile(r"^REAR BUMPER$", re.I), re.compile(r"."), "Бампер задний"),
           # Peugeot: 4404 66/67 — задние суппорты, а перевод — «Крышка тормозного суппорта»
           (re.compile(r"^КРЫШКА ТОРМОЗНОГО СУППОРТА", re.I), re.compile(r"^440\d"), "Суппорт тормозной"),
           # Toyota «LINK ASSY, STEERING» (45500/45510) — рулевая рейка в сборе, а перевод — «Тяга рулевого механизма»
           (re.compile(r"^Тяга рулевого механизма \(в сборе\)", re.I), re.compile(r"^455[01]0"), "Рейка рулевая (в сборе)")]


def _rename(name: str, oem: str) -> str:
    for rx, num, to in _RENAME:
        if rx.search(name) and num.search(re.sub(r"[^0-9A-Z]", "", oem.upper())):
            return rx.sub(to, name)
    return name


def parse_details(data: Any, group_id: int = 0) -> list[Detail]:
    cats = data if isinstance(data, list) else (data.get("categories") or data.get("data") or []) \
        if isinstance(data, dict) else []
    out = []
    for c in cats:
        if not isinstance(c, dict):
            continue
        for u in c.get("units") or []:
            ua = _attrs(u)
            for d in u.get("details") or []:
                oem = re.sub(r"\s+", " ", str(d.get("oem") or "")).strip()
                if not oem:
                    continue  # «Смазка не является запчастью» и т.п.
                da = _attrs(d)
                out.append(Detail(
                    oem=oem, name=_rename(str(d.get("name") or "").strip(), oem), note=da.get("note", ""),
                    amount=da.get("amount", ""), match=d.get("match"),
                    unit=str(u.get("name") or ""), unit_note=ua.get("note", ""),
                    unit_id=str(u.get("unitId") or ""), unit_ssd=str(u.get("ssd") or ""),
                    image=str(u.get("largeImageUrl") or u.get("imageUrl") or ""),
                    code_on_image=str(d.get("codeOnImage") or ""), category=str(c.get("name") or ""),
                    group_id=group_id, filter=str(d.get("filter") or ""), ssd=str(d.get("ssd") or ""), specs=_specs(d),
                    replaced=da.get("replacement", ""),
                ))
    return out


def image_url(raw: str, size: str = "source") -> str:
    """Как в приложении (LaximoImage.kt): %size% → 150…250 или source."""
    url = raw.strip().replace("%size%", size)
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://ws.laximo.ru" + url
    return url


# ---------- дерево групп и поиск по словам ----------

@dataclass
class Group:
    id: int
    name: str
    path: str
    phrases: list[tuple[list[str], float]]   # основы фразы и её вес (название, синоним, выученное)
    side: T.Side = field(default_factory=T.Side)


class TreeIndex:
    """Группы, в которые можно зайти (link), с основами названий и синонимов и весом слов (IDF):
    редкое «ступиц» значит больше, чем частое «колес»."""

    PATH_W = 0.9
    WHOLE_REC = 0.5   # синоним: доля веса фразы, которая должна совпасть, и обязательно его главное слово

    def __init__(self, tree: Any, stop: frozenset[str], learned: dict[str, list[str]] | None = None,
                 synonyms: list[dict] | None = None, not_typos: frozenset[str] = frozenset()):
        self.stop = stop
        self.not_typos = not_typos
        self.groups: dict[int, Group] = {}
        self.need: dict[tuple[int, tuple[str, ...]], str] = {}   # синоним только для одной стороны
        self.whole: set[tuple[int, tuple[str, ...]]] = set()      # синоним считается только целиком
        # Синонимы, чьих групп у машины нет: «свечи накаливания» у бензиновой, «цепь ГРМ» у мотора с ремнём.
        # Такой запрос целиком — не ищем похожее («Свечи зажигания»), а честно не находим
        self.absent: set[tuple[frozenset[str], str]] = set()
        # Главное слово каждой фразы группы — по форме слов её же текста: «Салонный фильтр» → «фильтр», «Ремень
        # генератора» → «ремень», даже если где-то в дереве есть «Ременный шкив». Общий T.ADJ теперь только про заявку
        # клиента (12.10) — раньше он копил прилагательные всех деревьев и ответ зависел от истории службы
        self.heads: dict[tuple[str, ...], str] = {}
        roots = tree if isinstance(tree, list) else [tree]
        for r in roots:
            if isinstance(r, dict):
                self._walk(r, [])
        # Словарь: слова покупателей и прайсов → группы по приоритету. Первой группы у машины нет
        # (шаровая у Focus — часть рычага) — слово уходит в следующую, которая есть.
        extra_vocab: set[str] = set()   # слова деталей, которых нет в быстрых группах: без них фраза отбрасывается как «не деталь»
        for syn in synonyms or []:
            if syn.get("vocab"):
                extra_vocab.update(x for w in syn["words"] for x in T.stems(w, stop))
            gid = next((int(g) for g in syn.get("groups", []) if int(g) in self.groups), None)
            if gid is None:
                # Только помеченные «strict»: у них перечислены все группы этой детали. У «сайлентблок рычага»
                # не все (у Toyota сайлентблоки — в «Рычаг передний нижний»), там «не нашли» было бы ложью
                for w in syn["words"] if syn.get("strict") else []:
                    st = T.stems(w, stop)
                    if len(st) >= 2:   # одно слово — слишком общее, чтобы решать за весь каталог
                        self.absent.add((frozenset(st), T.side(w).axis))
                continue
            for w in syn["words"]:
                st = T.stems(w, stop)
                if not st:
                    continue
                self.heads.setdefault(tuple(st), T.head_text(w, stop) or st[0])
                self.groups[gid].phrases.append((st, 1.0))
                # Синоним — точная фраза: «ремкомплект грм» не должен ловить любой «ремкомплект»,
                # «резинка дворника» — любую «резинку». Названия Laximo совпадают и частично.
                self.whole.add((gid, tuple(st)))
                # Слова стороны из основ выпадают: «фара задняя» без этого стала бы просто «фарой»
                axis = T.side(w).axis
                if axis:
                    self.need[(gid, tuple(st))] = axis
        for gid, names in (learned or {}).items():
            g = self.groups.get(int(gid))
            if g:
                for n in names:
                    st = T.stems(n, stop)
                    if st:
                        g.phrases.append((st, 0.8))
                        self.heads.setdefault(tuple(st), T.head_text(n, stop) or st[0])
        df: dict[str, int] = {}
        for g in self.groups.values():
            for s in {s for st, _ in g.phrases for s in st}:
                df[s] = df.get(s, 0) + 1
        n = max(len(self.groups), 1)
        self.idf = {s: math.log((n + 1) / (c + 1)) + 1 for s, c in df.items()}
        self.max_idf = max(self.idf.values(), default=1.0)
        self.vocab = set(self.idf) | extra_vocab
        self._fix: dict[str, str] = {}
        self._split: dict[str, list[str]] = {}

    def fix(self, s: str) -> str:
        """Опечатка в одну букву → слово каталога («масленн» → «маслян», «подшибник» → «подшипник»).
        Первая буква не меняется (котор ≠ мотор, балон ≠ салон), и не трогаем обычные слова, похожие
        на детали: «готов» ≠ «голов», «Гранта» ≠ «граната», «решение» ≠ «ремень» (список из переписки)."""
        if s not in self._fix:
            if s.endswith(".") or s in self.not_typos or any(T.same(s, v) for v in self.vocab):
                self._fix[s] = s
            else:
                hit = next((v for v in sorted(self.vocab) if v[:1] == s[:1] and T.near(s, v)), s)
                if hit == s and len(s) >= 6:
                    # Звуковая опечатка в несколько букв: «тармазн» = «тормозн», «калектор» = «коллектор»
                    hit = self._by_sound().get(T.sound(s), s)
                    hit = hit if hit[:1] == s[:1] else s
                self._fix[s] = hit
        return self._fix[s]

    def _by_sound(self) -> dict[str, str]:
        """Слово каталога по звучанию; если два слова звучат одинаково, оба выпадают — угадывать нельзя."""
        if getattr(self, "_sound", None) is None:
            seen: dict[str, str | None] = {}
            for v in sorted(self.vocab):
                if len(v) >= 6 and not v.endswith("."):
                    k = T.sound(v)
                    seen[k] = v if k not in seen else None
            self._sound = {k: v for k, v in seen.items() if v}
        return self._sound

    def _walk(self, node: dict, path: list[str]):
        name = str(node.get("name") or node.get("quickGroupName") or "").strip()
        try:
            gid = int(node.get("quickGroupId"))
        except (TypeError, ValueError):
            gid = None
        if gid is not None and node.get("link") and name and gid not in self.groups:
            texts = [name] + [s.strip() for s in str(node.get("synonyms") or "").split(",") if s.strip()]
            phrases = []
            for t in texts:
                st = T.stems(t, self.stop)
                if st:
                    phrases.append((st, 1.0))
                    self.heads.setdefault(tuple(st), T.head_text(t, self.stop) or st[0])
            if phrases and path:
                # Название раздела уточняет группу: «Компрессор» в «Кондиционере», «Выключатель, датчик»
                # в «Системе охлаждения». Вес ниже: раздел — контекст, а не название детали.
                own = phrases[0][0]
                extra = [s for s in T.stems(path[-1], self.stop) if not any(T.same(s, t) for t in own)]
                if extra:
                    phrases.append((own + extra, self.PATH_W))
                    self.heads.setdefault(tuple(own + extra), self.heads.get(tuple(own)) or own[0])
            if phrases:
                self.groups[gid] = Group(gid, name, " › ".join(path), phrases, T.side(name))
        for ch in node.get("children") or []:
            if isinstance(ch, dict):
                self._walk(ch, path + [name] if name and gid != 0 else path)

    def head(self, p: list[str]) -> str | None:
        """Главное слово фразы группы — запомненное по её тексту (см. heads)."""
        return self.heads.get(tuple(p)) or T.head(p)

    def weight(self, s: str) -> float:
        return self.idf.get(s) or next((v for k, v in self.idf.items() if T.same(s, k)), self.max_idf)

    def _in_vocab(self, s: str) -> bool:
        return any(T.same(s, v) for v in self.vocab)

    def split(self, s: str) -> list[str]:
        """Склеенные слова из прайсов: «датчикколенвала» → «датчик» + «коленва», «датчикabs» → «датчик» + «abs»,
        «стойкастабилизаторапереднего» → «стойк» + «стабилизатор» (сторона дальше не нужна)."""
        if s not in self._split:
            self._split[s] = [s]
            if not s.endswith(".") and len(s) >= 8 and not self._in_vocab(s):
                for i in range(5, len(s) - 2):
                    a = T.stem(s[:i])
                    if not self._in_vocab(a):
                        continue
                    b = T.stem(s[i:])
                    if self._in_vocab(b) or T.side_of_word(s[i:]):
                        self._split[s] = [a] + ([b] if self._in_vocab(b) else [])
                        break
                    rest = self.split(s[i:])
                    if rest != [s[i:]]:
                        self._split[s] = [a] + rest
                        break
        return self._split[s]

    def known(self, q: list[str]) -> list[str]:
        """Слова запроса, которые есть в каталоге (опечатки исправлены). «форд», «фокус», «3» сюда не попадут."""
        parts = [p for x in q for p in self.split(x)]
        return [s for s in dict.fromkeys(self.fix(x) for x in parts) if self._in_vocab(s)]

    def score(self, q: list[str], p: list[str]) -> tuple[float, float]:
        """(сколько запроса покрыто фразой, сколько фразы покрыто запросом) — с весами слов."""
        if not q or not p:
            return 0.0, 0.0
        wq = sum(self.weight(s) for s in q)
        wp = sum(self.weight(t) for t in p)
        mq = sum(self.weight(s) for s in q if any(T.same(s, t) for t in p))
        mp = sum(self.weight(t) for t in p if any(T.same(s, t) for s in q))
        return mq / wq, mp / wp

    def rank(self, q: list[str], want: T.Side) -> list[tuple[Group, float]]:
        bare = frozenset(s.rstrip(".") for s in q)   # «фильтр топл.» = синоним «фильтр топл»
        if self.absent and ((bare, want.axis) in self.absent or (bare, "") in self.absent):
            return []
        q = self.known(q)
        # Главное слово: «ремень генератора», «датчик положения распредвала», «топливный фильтр» → «фильтр»
        head = T.head(q) if len(q) > 1 else None
        out = []
        for g in self.groups.values():
            best = 0.0
            for p, w in g.phrases:
                need = self.need.get((g.id, tuple(p))) if self.need else None
                if need and want.axis != need:
                    continue
                prec, rec = self.score(q, p)
                if rec < 0.999 and (g.id, tuple(p)) in self.whole and (
                        rec < self.WHOLE_REC or not any(T.same(self.head(p), s) for s in q)):
                    continue
                if prec and rec:
                    f = w * 2 * prec * rec / (prec + rec)
                    if len(q) == 1 and not T.same(q[0], self.head(p)):
                        f *= 0.8   # «масло» — не «Датчик давления масла»: в группе главное слово другое
                    if head and any(T.same(head, t) for t in p):
                        f += 0.1 if T.same(head, self.head(p)) else 0.0
                    elif head:
                        f *= 0.7   # главного слова нет — «ремень генератора» не «Генератор»
                    best = max(best, f)
            if best <= 0:
                continue
            if want.axis and g.side.axis:
                best += 0.05 if want.axis == g.side.axis else -0.4
            out.append((g, best))
        out.sort(key=lambda x: -x[1])
        return out


# ---------- обращения к Laximo с кэшем ----------

Call = Callable[[str, dict], Awaitable[Any]]


# Как марки пишут люди, если «.title()» исказит: SSANGYONG → SsangYong, не Ssangyong
_MAKE_NAMES = {"SSANGYONG": "SsangYong", "MERCEDES-BENZ": "Mercedes-Benz", "LANDROVER": "Land Rover",
               "ALFAROMEO": "Alfa Romeo", "GREATWALL": "Great Wall", "DS": "DS", "MG": "MG", "GMC": "GMC"}


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)


class Catalog:
    def __init__(self, call: Call, folder: Path | None, stop: frozenset[str], synonyms: list[dict] | None = None,
                 not_typos: frozenset[str] = frozenset()):
        self.synonyms = synonyms or []
        self.not_typos = not_typos
        self.call = call
        self.folder = folder
        self.stop = stop
        self._vehicles: dict[str, tuple[float, list[Vehicle]]] = {}
        self.units_pause = 0.0   # пауза между запросами списка узлов; ставит только массовая закачка парка
        self._pace = asyncio.Lock()
        self._details: dict[tuple, tuple[float, list[Detail]]] = {}
        self._raw: dict[str, tuple[float, Any]] = {}
        self._trees: dict[str, TreeIndex] = {}
        self._unit_trees: dict[str, TreeIndex | None] = {}      # узлы полного каталога как «группы» с отрицательными номерами
        self._unit_ids: dict[str, dict[int, dict]] = {}
        self.learned: dict[str, list[str]] = self._load("learned.json") or {}

    # --- диск ---
    def _path(self, name: str) -> Path | None:
        return self.folder / name if self.folder else None

    def _load(self, name: str) -> Any:
        p = self._path(name)
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p and p.exists() else None
        except (OSError, ValueError):
            return None

    def _save(self, name: str, data: Any):
        p = self._path(name)
        if not p:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        tmp.write_bytes(gzip.compress(raw) if name.endswith(".gz") else raw)
        tmp.replace(p)

    def _load_any(self, name: str) -> Any:
        p = self._path(name)
        try:
            if not p or not p.exists():
                return None
            raw = p.read_bytes()
            return json.loads(gzip.decompress(raw) if name.endswith(".gz") else raw)
        except (OSError, ValueError, EOFError):
            return None

    async def _cached(self, name: str, ttl: float, fetch: Callable[[], Awaitable[Any]]) -> Any:
        """Ответ Laximo с диска, если свежий; иначе спросить и сохранить. Laximo не ответил — старый с диска."""
        saved = self._load_any(name) if self.folder else None
        if saved and time.time() - float(saved.get("saved", 0)) < ttl:
            return saved["data"]
        try:
            data = check_error(await fetch())
        except Exception:
            if saved:
                return saved["data"]
            raise
        self._save(name, {"saved": time.time(), "data": data})
        return data

    # --- машина ---
    async def vehicles(self, ident: str = "", plate: str = "") -> list[Vehicle]:
        key = ident or "plate:" + plate
        hit = self._vehicles.get(key)
        if hit and time.time() - hit[0] < VEHICLE_TTL:
            return hit[1]
        if ident:
            data = await self._cached(f"vehicles/{_safe(ident)}.json", VEHICLE_TTL,
                                      lambda: self.call("findVehicle", {"identString": ident}))
        else:
            data = await self._cached(f"vehicles/plate_{_safe(plate)}.json", VEHICLE_TTL,
                                      lambda: self.call("findVehicleByPlateNumber",
                                                        {"countryCode": "ru", "plateNumber": plate}))
        found = parse_vehicles(data)
        self._vehicles[key] = (time.time(), found)
        return found

    # --- полный каталог: узлы вне быстрых групп (подрамник, поперечина…) ---
    def _car(self, v: Vehicle) -> str:
        return hashlib.sha1(f"{v.vehicle_id}|{v.ssd}".encode()).hexdigest()[:16]

    async def units(self, v: Vehicle) -> list[dict]:
        """Все узлы полного каталога машины (listCategories → listUnits): у Kia Ceed 7 категорий и 303 узла за 8 запросов.
        Хранится на диске навсегда. [{id, name, code, ssd, cat, image}]; сбой Laximo — пусто, поиск идёт как раньше."""
        name = f"units/{_safe(v.catalog)}/{self._car(v)}.json.gz"
        saved = self._load_any(name) if self.folder else None
        if saved:
            return saved["units"]
        base = {"catalog": v.catalog, "vehicleId": v.vehicle_id}
        out: list[dict] = []
        sem = asyncio.Semaphore(6)   # у VW сотни категорий: не заваливаем Laximo запросами разом

        async def ask(method: str, params: dict):
            async with sem:
                for attempt in range(4):
                    if self.units_pause:   # массовая закачка: у Laximo общий лимит 600 запросов в минуту на весь сервис
                        async with self._pace:
                            await asyncio.sleep(self.units_pause)
                    try:
                        return check_error(await self.call(method, params))
                    except Exception as e:   # noqa: BLE001
                        if "TOO_MANY_REQUESTS" not in str(e) or attempt == 3:
                            raise
                        await asyncio.sleep(15 * (attempt + 1))

        seen: set[str] = set()   # у GM Laximo на любой запрос отдаёт весь плоский список: без этого обход шёл по кругу бесконечно

        async def walk(cat: dict, path: str, depth: int):
            path = f"{path} / {cat.get('name')}" if path else str(cat.get("name") or "")
            ssd = cat.get("ssd") or v.ssd
            cid = str(cat["categoryId"])
            if str(cat.get("childrens")).lower() == "true" and depth < 4:
                kids = await ask("listCategories", base | {"categoryId": cid, "ssd": ssd})
                kids = [k for k in kids if isinstance(k, dict) and str(k.get("categoryId")) not in seen
                        and str(k.get("parentCategoryId") or cid) == cid]   # только дети этой категории
                seen.update(str(k["categoryId"]) for k in kids)
                await asyncio.gather(*(walk(k, path, depth + 1) for k in kids))
            else:
                us = await ask("listUnits", base | {"categoryId": str(cat["categoryId"]), "ssd": ssd})
                for u in us if isinstance(us, list) else []:
                    if isinstance(u, dict) and u.get("unitId") and u.get("name"):
                        out.append({"id": str(u["unitId"]), "name": str(u["name"]).strip(), "code": str(u.get("code") or ""),
                                    "ssd": str(u.get("ssd") or ""), "cat": path,
                                    "image": str(u.get("largeImageUrl") or u.get("imageUrl") or "")})
        try:
            top = await ask("listCategories", base | {"ssd": v.ssd})
            roots = [c for c in top if isinstance(c, dict) and str(c.get("parentCategoryId") or "0") == "0"]
            seen.update(str(c["categoryId"]) for c in roots)
            await asyncio.gather(*(walk(c, "", 0) for c in roots))
        except Exception:   # noqa: BLE001 — полный каталог запасной: ошибка = узлов нет, как раньше
            return []
        if out:
            self._save(name, {"saved": time.time(), "units": out})
        return out

    async def unit_tree(self, v: Vehicle) -> TreeIndex | None:
        """Узлы полного каталога как «быстрые группы» с отрицательными номерами: поиск по словам и всё, что за ним
        (стороны, цены, варианты), работает тем же кодом. None — узлов нет."""
        key = self.tree_key(v) + "-u"
        if key in self._unit_trees:
            return self._unit_trees[key]
        units = await self.units(v)
        if not units:
            self._unit_trees[key] = None
            return None
        ids: dict[int, dict] = {}
        by_cat: dict[str, list[dict]] = {}
        for i, u in enumerate(units, 1):
            ids[-i] = u
            by_cat.setdefault(u["cat"], []).append({"name": u["name"], "quickGroupId": -i, "synonyms": "", "link": True,
                                                    "children": []})
        tree = {"name": "units", "link": False,
                "children": [{"name": c, "link": False, "children": ch} for c, ch in by_cat.items()]}
        self._unit_ids[key] = ids
        self._unit_trees[key] = TreeIndex(tree, self.stop, {}, [], self.not_typos)
        return self._unit_trees[key]

    def unit_file(self, v: Vehicle, u: dict) -> str:
        """Файл деталей узла. У Ford, Mercedes, Mazda, Citroen номер узла у всех узлов «0» — узел различает только ssd:
        без него в файл первого узла попадали все остальные (12.10: «Ремонтные панели» вместо любого узла Ford)."""
        tag = hashlib.sha1(str(u.get("ssd") or "").encode()).hexdigest()[:12]
        return f"unit_details/{_safe(v.catalog)}/{self._car(v)}/{_safe(str(u['id']))}-{tag}.json.gz"

    async def _unit_details(self, v: Vehicle, group_id: int) -> list[Detail]:
        u = (self._unit_ids.get(self.tree_key(v) + "-u") or {}).get(group_id)
        if not u:
            return []
        key = (v.catalog, v.vehicle_id, v.ssd, group_id, False)
        hit = self._details.get(key)
        if hit:
            return hit[1]
        name = self.unit_file(v, u)
        data = await self._cached(name, DETAILS_TTL, lambda: self.call("listDetailByUnit", {
            "catalog": v.catalog, "vehicleId": v.vehicle_id, "ssd": u["ssd"], "unitId": u["id"]}))
        out = []
        for d in data if isinstance(data, list) else []:
            oem = re.sub(r"\s+", " ", str(d.get("oem") or "")).strip()
            if not oem:
                continue
            da = _attrs(d)
            out.append(Detail(oem=oem, name=_rename(str(d.get("name") or "").strip(), oem), note=da.get("note", ""),
                              amount=da.get("amount", ""), match=d.get("match"), unit=u["name"], unit_note="",
                              unit_id=u["id"], unit_ssd=u["ssd"], image=u["image"],
                              code_on_image=str(d.get("codeOnImage") or ""), category=u["cat"], group_id=group_id,
                              unit_code=u["code"], filter=str(d.get("filter") or ""), ssd=str(d.get("ssd") or ""),
                              specs=_specs(d), replaced=da.get("replacement", "")))
        self._details[key] = (time.time(), out)
        return out

    # --- применимость номера к машине (ответ самого каталога) ---
    async def applicability(self, v: Vehicle, number: str) -> dict | None:
        """Подходит ли номер этой машине по каталогу (getOEMPartApplicability): FULLY — да, PARTIAL — возможно (машина
        найдена по словам, комплектация не подтверждена), NONAPPLICABLE — нет или не упоминается в модели. Номер пишут
        по-разному: регистр и пробелы каталог терпит, дефисы у VAG ломают («06H-906-433»), а у Toyota они обязательны
        («48068-48020»), поэтому пробуем несколько записей. Не ответил — None."""
        typed = re.sub(r"\s+", "", number or "").upper()
        bare = re.sub(r"[\s.\-]", "", typed)
        forms = [bare, typed]
        if re.fullmatch(r"\d{10}", bare):
            forms.append(f"{bare[:5]}-{bare[5:]}")   # Toyota, Honda: 48068-48020
        answered = False
        for form in dict.fromkeys(f for f in forms if f):
            name = f"applic/{_safe(v.catalog)}-{hashlib.sha1(v.ssd.encode()).hexdigest()[:16]}-{_safe(form)}.json"

            async def fetch(form=form):
                d = await self.call("getOEMPartApplicability", {"catalog": v.catalog, "ssd": v.ssd, "oem": form, "all": "0"})
                if not isinstance(d, dict):
                    return d
                return {"applicability": d.get("applicability"),
                        "categories": [c.get("name") for c in (d.get("categories") or [])[:3] if isinstance(c, dict)]}
            try:
                data = await self._cached(name, DETAILS_TTL, fetch)
            except Exception:   # noqa: BLE001 — подсказка: не ответил каталог = не знаем, как раньше
                continue
            status = data.get("applicability") if isinstance(data, dict) else None
            if not status:
                continue
            answered = True
            if status != "NONAPPLICABLE":
                return {"status": status, "where": [w for w in data.get("categories") or [] if w], "oem": form}
        return {"status": "NONAPPLICABLE", "where": [], "oem": bare} if answered else None

    # --- вопрос по комплектации, когда VIN её не различает (Renault, Mazda, BMW) ---
    async def filter_question(self, v: Vehicle, d: Detail) -> list[dict] | None:
        """getFilterByDetail: чем различаются номера по каталогу — [{"name": "АБС", "values": ["Да", "Нет"]}]. Проба 12.10:
        Renault R19, поворотный кулак — «АБС: Да / Нет». detailId — позиция на схеме; ssd — самой детали. Нет фильтра
        или каталог не ответил — None (робот спросит, как раньше)."""
        if not d.filter or not d.unit_id:
            return None
        name = f"filters/{_safe(v.catalog)}-{hashlib.sha1((d.filter + '|' + d.unit_id).encode()).hexdigest()[:16]}.json"
        try:
            data = await self._cached(name, DETAILS_TTL, lambda: self.call("getFilterByDetail", {
                "catalog": v.catalog, "vehicleId": v.vehicle_id, "ssd": d.ssd or d.unit_ssd, "unitId": d.unit_id,
                "detailId": d.code_on_image, "filter": d.filter}))
        except Exception:   # noqa: BLE001 — подсказка: не ответил каталог — спросим по-старому
            return None
        out = []
        for q in data if isinstance(data, list) else []:
            vals = [str(x.get("name") or "").strip() for x in q.get("values") or [] if isinstance(x, dict)] \
                if isinstance(q, dict) else []
            vals = [x for x in vals if x]
            if isinstance(q, dict) and q.get("name") and len(vals) >= 2:
                out.append({"name": str(q["name"]).strip(), "values": vals[:6]})
        return out or None

    # --- машина по словам клиента, когда VIN каталог не знает ---
    async def by_text(self, text: str, vin: str = "", limit: int = 6) -> list[Vehicle]:
        """Марка, модель и год из слов клиента → мастер подбора Laximo (getWizard2). Только однозначное: модель совпала
        по звуку, в рынке такой год есть, а если марку клиент не назвал («бампер на карок») — модель нашлась лишь в
        одном из каталогов, подходящих по началу VIN. Не уверены — пусто, робот скажет «пришлите VIN»."""
        brand = W.brand_of(text)
        by_vin = W.wmi_brands(vin)
        if brand and by_vin and brand not in by_vin:
            return []   # в тексте одна марка, VIN от другой (опечатка в VIN или чужая машина) — ничему не верим
        brands = [brand] if brand else by_vin
        if not brands:
            return []
        try:
            cats = await self._cached("catalogs.json", 30 * 86400, lambda: self.call("listCatalogs", {}))
            codes = [c for c in (W.catalog_of(b, cats if isinstance(cats, list) else []) for b in brands) if c]
            found: list[tuple[str, list[Vehicle]]] = []
            for code in codes:
                vs = await self._wizard_vehicles(code, text, vin)
                if vs:
                    found.append((code, vs))
            if len(found) != 1:
                return []   # ни одного или модель есть в нескольких каталогах — не гадаем
            return found[0][1][:limit]
        except Exception:   # noqa: BLE001 — поиск «по словам» запасной: ошибка = не нашли, как раньше
            return []

    async def _wizard_vehicles(self, code: str, text: str, vin: str) -> list[Vehicle]:
        async def wiz(ssd: str):
            name = f"wizard/{code}-{hashlib.sha1(ssd.encode()).hexdigest()[:20]}.json"
            return await self._cached(name, DETAILS_TTL, lambda: self.call("getWizard2", {"catalog": code, "ssd": ssd}))

        async def listing(ssd: str) -> list[Vehicle]:
            name = f"wizard/{code}-{hashlib.sha1(ssd.encode()).hexdigest()[:20]}-v.json"
            data = await self._cached(name, DETAILS_TTL,
                                      lambda: self.call("findVehicleByWizard2", {"catalog": code, "ssd": ssd}))
            return parse_vehicles(data)

        hint = W.engine_hint(text)

        async def refine(ssd: str) -> str:
            """Объём и мощность двигателя, тип коробки — если клиент их назвал и вариант в мастере однозначный."""
            if hint["volume"]:
                ec = W.pick_condition(await wiz(ssd), "двигат", "engine")
                if ec and ec.get("options") and not ec.get("determined"):
                    o = W.pick_engine(ec["options"], hint)
                    ssd = o["key"] if o else ssd
            if hint["gear"]:
                tc = W.pick_condition(await wiz(ssd), "трансмис", "короб", "transmission", "gearbox")
                if tc and tc.get("options") and not tc.get("determined"):
                    o = W.pick_gear(tc["options"], hint["gear"])
                    ssd = o["key"] if o else ssd
            return ssd

        rows = await wiz("")
        cond = W.pick_condition(rows, "модел")
        models = W.match_options(W.tokens(text), cond["options"]) if cond and cond.get("options") else []
        year = W.year_of(text, vin)
        found: list[Vehicle] = []
        for model in models[:3]:
            rows = await wiz(model["key"])
            market = W.pick_condition(rows, "рынок")
            if any(c.get("allowListVehicles") for c in rows):
                starts = [(model["key"], rows)]
            elif market and market.get("options") and not market.get("determined"):
                starts = [(o["key"], await wiz(o["key"])) for o in market["options"]
                          if W.market_ok(o.get("value", ""), vin)][:3]   # рынок не назван — все, что подходят по VIN
            else:
                continue
            for ssd, rws in starts:
                yc = W.pick_condition(rws, "год")
                if year and yc and yc.get("options"):
                    yo = next((o for o in yc["options"] if str(o.get("value", "")).strip() == year), None)
                    if not yo:
                        continue   # на этом рынке такого года нет — не показываем всё подряд
                    ssd = yo["key"]
                refined = await refine(ssd)
                got = await listing(refined) if refined != ssd else []
                found += got or await listing(ssd)   # уточнение двигателем и коробкой ничего не дало — берём без него
        uniq: dict[tuple, Vehicle] = {}
        for v in found:
            if W.market_ok(v.attrs.get("market", ""), vin):
                uniq.setdefault((v.vehicle_id, v.name, v.attrs.get("market", ""), v.attrs.get("prodrange", "")), v)
        # Слишком много вариантов («TrailBlazer» — 20 машин разных рынков): выбирать из них клиенту нечем, пусть пришлёт VIN
        return list(uniq.values()) if len(uniq) <= 6 else []

    # --- дерево групп ---
    @staticmethod
    def tree_key(v: Vehicle) -> str:
        """Дерево групп — у каждой машины своё, не у каталога: в TOYOTA00 у Camry 34 группы, которых нет
        у Fortuner, а у Fortuner 59 своих (дизель, кардан, блокировка). Ключ — каталог и машина (ssd)."""
        return f"{re.sub(r'[^A-Za-z0-9_-]', '_', v.catalog)}-{hashlib.sha1(v.ssd.encode()).hexdigest()[:16]}"

    async def tree(self, v: Vehicle) -> TreeIndex:
        key = self.tree_key(v)
        hit = self._raw.get(key)
        if not hit or time.time() - hit[0] > TREE_TTL:
            name = f"trees/{key}.json"
            saved = self._load(name) or {}
            hit = (float(saved.get("saved", 0)), saved.get("tree"))
            if not hit[1] or time.time() - hit[0] > TREE_TTL:
                try:
                    raw = check_error(await self.call("listQuickGroup", {
                        "catalog": v.catalog, "ssd": v.ssd, "vehicleId": v.vehicle_id}))
                    hit = (time.time(), raw)
                    self._save(name, {"saved": hit[0], "catalog": v.catalog, "tree": raw})
                except Exception:
                    if not hit[1]:
                        raise  # нет ни свежего, ни старого дерева
            self._raw[key] = hit
            self._trees.pop(key, None)
        if key not in self._trees:
            self._trees[key] = TreeIndex(hit[1], self.stop, self.learned, self.synonyms, self.not_typos)
        return self._trees[key]

    # --- детали группы ---
    async def details(self, v: Vehicle, group_id: int, full: bool) -> list[Detail]:
        """full=False — только детали самой группы (у Ford воздушный фильтр там «Фильтрующий элемент»,
        по словам его не найти). full=True — узлы целиком, запасной путь для поиска по словам,
        когда в составе группы нужной детали нет."""
        if group_id < 0:
            return await self._unit_details(v, group_id)
        key = (v.catalog, v.vehicle_id, v.ssd, group_id, full)
        hit = self._details.get(key)
        if hit and time.time() - hit[0] < DETAILS_TTL:
            return hit[1]
        car = hashlib.sha1(f"{v.vehicle_id}|{v.ssd}".encode()).hexdigest()[:16]
        name = f"details/{_safe(v.catalog)}/{car}/{group_id}{'-all' if full else ''}.json.gz"
        data = await self._cached(name, DETAILS_TTL, lambda: self.call("listQuickDetail", {
            "catalog": v.catalog, "ssd": v.ssd, "vehicleId": v.vehicle_id,
            "quickGroupId": str(group_id), "all": "true" if full else "false"}))
        found = parse_details(data, group_id)
        self._details[key] = (time.time(), found)
        if len(self._details) > 2000:  # не копим бесконечно
            oldest = sorted(self._details, key=lambda k: self._details[k][0])[:500]
            for k in oldest:
                self._details.pop(k, None)
        return found

    def learn(self, group_id: int, detail_name: str):
        """Запоминаем, как называется найденная деталь: в следующий раз такие слова клиента
        сразу приведут в эту группу (вес ниже, чем у названий и синонимов Laximo)."""
        if group_id < 0:
            return   # узел полного каталога: номер — порядок в списке именно этой машины, общим знанием он не станет
        names = self.learned.setdefault(str(group_id), [])
        if detail_name and detail_name not in names and len(names) < 200:
            names.append(detail_name)
            self._save("learned.json", self.learned)
            self._trees.clear()  # индексы пересоберутся из сохранённых деревьев с новыми словами
