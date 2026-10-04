"""Laximo для подбора: разбор ответов, кэш и поиск группы по словам клиента.

Дерево быстрых групп (≈400 групп с синонимами вроде «воздухан», «салонник») одно на каталог
и меняется редко — храним на диске неделю, поэтому слова клиента сопоставляются без запросов
к Laximo. Машину по VIN и списки деталей держим в памяти несколько часов: менеджер часто
спрашивает по той же машине ещё раз.
"""
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import text as T

TREE_TTL = 7 * 24 * 3600
VEHICLE_TTL = 6 * 3600
DETAILS_TTL = 6 * 3600


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

    def public(self) -> dict:
        return {"brand": self.brand, "name": self.name, "catalog": self.catalog,
                "summary": self.summary(), "attributes": self.attrs}


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

    def __init__(self, tree: Any, stop: frozenset[str], learned: dict[str, list[str]] | None = None):
        self.stop = stop
        self.groups: dict[int, Group] = {}
        roots = tree if isinstance(tree, list) else [tree]
        for r in roots:
            if isinstance(r, dict):
                self._walk(r, [])
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

    def _walk(self, node: dict, path: list[str]):
        name = str(node.get("name") or node.get("quickGroupName") or "").strip()
        try:
            gid = int(node.get("quickGroupId"))
        except (TypeError, ValueError):
            gid = None
        if gid is not None and node.get("link") and name and gid not in self.groups:
            texts = [name] + [s.strip() for s in str(node.get("synonyms") or "").split(",") if s.strip()]
            phrases = [(st, 1.0) for st in (T.stems(t, self.stop) for t in texts) if st]
            if phrases:
                self.groups[gid] = Group(gid, name, " › ".join(path), phrases, T.side(name))
        for ch in node.get("children") or []:
            if isinstance(ch, dict):
                self._walk(ch, path + [name] if name and gid != 0 else path)

    def weight(self, s: str) -> float:
        return self.idf.get(s) or next((v for k, v in self.idf.items() if T.same(s, k)), self.max_idf)

    def known(self, q: list[str]) -> list[str]:
        """Слова запроса, которые есть в каталоге. «форд», «фокус», «3» сюда не попадут и не мешают."""
        return [s for s in dict.fromkeys(q) if any(T.same(s, v) for v in self.vocab)]

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
        q = self.known(q)
        out = []
        for g in self.groups.values():
            best = 0.0
            for p, w in g.phrases:
                prec, rec = self.score(q, p)
                if prec and rec:
                    best = max(best, w * 2 * prec * rec / (prec + rec))
            if best <= 0:
                continue
            if want.axis and g.side.axis:
                best += 0.05 if want.axis == g.side.axis else -0.4
            out.append((g, best))
        out.sort(key=lambda x: -x[1])
        return out


# ---------- обращения к Laximo с кэшем ----------

Call = Callable[[str, dict], Awaitable[Any]]


class Catalog:
    def __init__(self, call: Call, folder: Path | None, stop: frozenset[str]):
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
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    # --- машина ---
    async def vehicles(self, ident: str = "", plate: str = "") -> list[Vehicle]:
        key = ident or "plate:" + plate
        hit = self._vehicles.get(key)
        if hit and time.time() - hit[0] < VEHICLE_TTL:
            return hit[1]
        if ident:
            data = await self.call("findVehicle", {"identString": ident})
        else:
            data = await self.call("findVehicleByPlateNumber", {"countryCode": "ru", "plateNumber": plate})
        found = parse_vehicles(check_error(data))
        self._vehicles[key] = (time.time(), found)
        return found

    # --- дерево групп ---
    async def tree(self, v: Vehicle) -> TreeIndex:
        hit = self._raw.get(v.catalog)
        if not hit or time.time() - hit[0] > TREE_TTL:
            name = f"trees/{re.sub(r'[^A-Za-z0-9_-]', '_', v.catalog)}.json"
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
            self._raw[v.catalog] = hit
            self._trees.pop(v.catalog, None)
        if v.catalog not in self._trees:
            self._trees[v.catalog] = TreeIndex(hit[1], self.stop, self.learned)
        return self._trees[v.catalog]

    # --- детали группы ---
    async def details(self, v: Vehicle, group_id: int, full: bool) -> list[Detail]:
        """full=False — только детали самой группы (у Ford воздушный фильтр там «Фильтрующий элемент»,
        по словам его не найти). full=True — узлы целиком, запасной путь для поиска по словам,
        когда в составе группы нужной детали нет."""
        key = (v.catalog, v.vehicle_id, v.ssd, group_id, full)
        hit = self._details.get(key)
        if hit and time.time() - hit[0] < DETAILS_TTL:
            return hit[1]
        data = check_error(await self.call("listQuickDetail", {
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
