"""Laximo для подбора: разбор ответов, кэш и поиск группы по словам клиента.

Дерево быстрых групп (≈400 групп с синонимами вроде «воздухан», «салонник») одно на каталог
и меняется редко — храним на диске неделю, поэтому слова клиента сопоставляются без запросов
к Laximo. Машину по VIN и списки деталей держим в памяти несколько часов: менеджер часто
спрашивает по той же машине ещё раз.
"""
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

    @property
    def context(self) -> str:
        """Всё, где каталог пишет сторону: VAG — в примечании узла («передн.;PR:1ZC»),
        Ford — в названии узла («Задний кулак…»), суппорты — в примечании детали («лев.»)."""
        return " | ".join(x for x in (self.name, self.note, self.unit, self.unit_note, self.category) if x)


def _attrs(o: dict) -> dict[str, str]:
    out = {}
    for a in o.get("attributes") or []:
        if isinstance(a, dict):
            key = str(a.get("key") or a.get("name") or "")
            if a.get("name") == "Примечание":
                key = "note"
            out[key] = str(a.get("value") or "")
    return out


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
                    oem=oem, name=str(d.get("name") or "").strip(), note=da.get("note", ""),
                    amount=da.get("amount", ""), match=d.get("match"),
                    unit=str(u.get("name") or ""), unit_note=ua.get("note", ""),
                    unit_id=str(u.get("unitId") or ""), unit_ssd=str(u.get("ssd") or ""),
                    image=str(u.get("largeImageUrl") or u.get("imageUrl") or ""),
                    code_on_image=str(d.get("codeOnImage") or ""), category=str(c.get("name") or ""),
                    group_id=group_id,
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
        roots = tree if isinstance(tree, list) else [tree]
        for r in roots:
            if isinstance(r, dict):
                self._walk(r, [])
        # Словарь: слова покупателей и прайсов → группы по приоритету. Первой группы у машины нет
        # (шаровая у Focus — часть рычага) — слово уходит в следующую, которая есть.
        for syn in synonyms or []:
            gid = next((int(g) for g in syn.get("groups", []) if int(g) in self.groups), None)
            if gid is None:
                for w in syn["words"]:
                    st = T.stems(w, stop)
                    if len(st) >= 2:   # одно слово — слишком общее, чтобы решать за весь каталог
                        self.absent.add((frozenset(st), T.side(w).axis))
                continue
            for w in syn["words"]:
                st = T.stems(w, stop)
                if not st:
                    continue
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
                g.phrases += [(st, 0.8) for st in (T.stems(n, stop) for n in names) if st]
        df: dict[str, int] = {}
        for g in self.groups.values():
            for s in {s for st, _ in g.phrases for s in st}:
                df[s] = df.get(s, 0) + 1
        n = max(len(self.groups), 1)
        self.idf = {s: math.log((n + 1) / (c + 1)) + 1 for s, c in df.items()}
        self.max_idf = max(self.idf.values(), default=1.0)
        self.vocab = set(self.idf)
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
                self._fix[s] = next((v for v in sorted(self.vocab) if v[:1] == s[:1] and T.near(s, v)), s)
        return self._fix[s]

    def _walk(self, node: dict, path: list[str]):
        name = str(node.get("name") or node.get("quickGroupName") or "").strip()
        try:
            gid = int(node.get("quickGroupId"))
        except (TypeError, ValueError):
            gid = None
        if gid is not None and node.get("link") and name and gid not in self.groups:
            texts = [name] + [s.strip() for s in str(node.get("synonyms") or "").split(",") if s.strip()]
            phrases = [(st, 1.0) for st in (T.stems(t, self.stop) for t in texts) if st]
            if phrases and path:
                # Название раздела уточняет группу: «Компрессор» в «Кондиционере», «Выключатель, датчик»
                # в «Системе охлаждения». Вес ниже: раздел — контекст, а не название детали.
                own = phrases[0][0]
                extra = [s for s in T.stems(path[-1], self.stop) if not any(T.same(s, t) for t in own)]
                if extra:
                    phrases.append((own + extra, self.PATH_W))
            if phrases:
                self.groups[gid] = Group(gid, name, " › ".join(path), phrases, T.side(name))
        for ch in node.get("children") or []:
            if isinstance(ch, dict):
                self._walk(ch, path + [name] if name and gid != 0 else path)

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
                        rec < self.WHOLE_REC or not any(T.same(T.head(p), s) for s in q)):
                    continue
                if prec and rec:
                    f = w * 2 * prec * rec / (prec + rec)
                    if len(q) == 1 and not T.same(q[0], T.head(p)):
                        f *= 0.8   # «масло» — не «Датчик давления масла»: в группе главное слово другое
                    if head and any(T.same(head, t) for t in p):
                        f += 0.1 if T.same(head, T.head(p)) else 0.0
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
        self._details: dict[tuple, tuple[float, list[Detail]]] = {}
        self._raw: dict[str, tuple[float, Any]] = {}
        self._trees: dict[str, TreeIndex] = {}
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
        names = self.learned.setdefault(str(group_id), [])
        if detail_name and detail_name not in names and len(names) < 200:
            names.append(detail_name)
            self._save("learned.json", self.learned)
            self._trees.clear()  # индексы пересоберутся из сохранённых деревьев с новыми словами
