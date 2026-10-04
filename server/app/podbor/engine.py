"""Подбор по заявке: VIN → машина (Laximo) → группа по словам клиента → деталь и её сторона → предложения ABCP.

Номера берутся только из Laximo и ABCP, сами мы их не придумываем. Сторону детали проверяем
в двух независимых местах: что пишет каталог (название и примечание узла, PR-коды VAG)
и что пишут поставщики в описаниях этого номера. Не сошлось или не хватает данных — вопрос клиенту.
"""
import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import brands as AB
from ..abcp import _key
from . import text as T
from .catalog import Catalog, Detail, LaximoError, TreeIndex, Vehicle, image_url
from .offers import axis_vote, brand_candidates, curate, oem_brand_for

RULES_FILE = Path(__file__).resolve().parent.parent / "data" / "podbor_rules.json"
OFFERS_TTL = 600
MAX_POSITIONS = 10
MAX_VARIANTS = 6
MAX_PRICED = 4


def load_rules(path: Path = RULES_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Candidate:
    d: Detail
    score: float
    prec: float
    member: bool                 # деталь из состава группы, а не просто из того же узла
    side: T.Side
    side_source: str = ""        # каталог / поставщики
    brand: str = ""
    rows: list[dict] | None = None
    vote: tuple[str, int, int] = ("", 0, 0)
    warning: str = ""

    @property
    def kind(self) -> tuple[str, str, str]:
        """Одна и та же деталь под разными номерами: у Ford рядом с оригиналом стоит его же
        версия Motorcraft («…, Не для гарантийного ремонта автомобиля, Motorcraft»)."""
        return self.side.axis, self.side.lr, self.d.name.split(",")[0].strip().lower()


class Engine:
    def __init__(self, src, folder: Path | None, warranty: set[str] | None = None, rules: dict | None = None):
        rules = rules or load_rules()
        stop = {w.lower() for w in rules.get("stop", [])}
        self.stop = frozenset(stop | {T.stem(w) for w in stop})
        self.pr_axis = {code: axis for axis, codes in rules.get("pr_axis", {}).items() for code in codes}
        self.notes = [{k: [T.stem(w) for w in n[k]] for k in ("query", "detail", "lacks")} | {"text": n["text"]}
                      for n in rules.get("notes", [])]
        self.src = src
        self.catalog = Catalog(src.laximo, folder, self.stop)
        self.warranty = {AB.get().key(b) for b in (warranty or set())}
        self._offers: dict[tuple[str, str], tuple[float, str, list[dict]]] = {}

    # ---------- заявка целиком ----------

    async def run(self, text: str, vehicle: int | None = None) -> dict:
        t0 = time.time()
        req = T.parse(text)
        res: dict[str, Any] = {"request": {"ident": req.ident, "plate": req.plate, "model": req.model,
                                           "chunks": req.chunks},
                               "status": "ok", "vehicle": None, "vehicles": [], "warnings": [], "positions": []}
        if not req.ident and not req.plate:
            return self._done(res, "no_vin", t0)
        try:
            found = await self.catalog.vehicles(req.ident, req.plate)
        except LaximoError as e:
            res["warnings"].append(f"Каталог ответил ошибкой {e.code}")
            return self._done(res, "catalog_error", t0)
        if not found:
            return self._done(res, "vehicle_not_found", t0)
        if len(found) > 1 and vehicle is None:
            res["vehicles"] = [v.public() for v in found]
            return self._done(res, "choose_vehicle", t0)
        v = found[min(vehicle or 0, len(found) - 1)]
        res["vehicle"] = v.public()
        res["warnings"] += compare_model(req.model, v)
        try:
            tree = await self.catalog.tree(v)
        except LaximoError as e:
            res["warnings"].append(f"Каталог групп недоступен ({e.code})")
            return self._done(res, "catalog_error", t0)
        items = self.split(req.chunks, tree)
        if not items:
            return self._done(res, "no_positions", t0)
        res["positions"] = list(await asyncio.gather(*(self.position(v, tree, q, s) for q, s in items[:MAX_POSITIONS])))
        if len(items) > MAX_POSITIONS:
            res["warnings"].append(f"Разобраны первые {MAX_POSITIONS} позиций из {len(items)}")
        return self._done(res, "ok", t0)

    def _done(self, res: dict, status: str, t0: float) -> dict:
        res["status"] = status
        res["seconds"] = round(time.time() - t0, 1)
        res["text"] = draft(res)
        return res

    # ---------- позиции ----------

    def split(self, chunks: list[str], tree: TreeIndex) -> list[tuple[str, T.Side]]:
        """«Масляный фильтр, воздушный, салонный» → три позиции; «колодки передние, комплект» → одна.
        «Колодки передние и задние» → две: одно слово стороны повторяет соседнюю деталь."""
        out: list[tuple[str, T.Side]] = []
        for chunk in chunks:
            whole = T.side(chunk)
            pieces = T.split_pieces(chunk)
            if len(pieces) == 1:
                if T.stems(chunk, self.stop):
                    out.append((self.clean(chunk), whole))
                continue
            parts: list[dict] = []
            for p in pieces:
                st, ps = T.stems(p, self.stop), T.side(p)
                if not st:
                    if ps.axis or ps.lr:
                        parts.append({"text": p, "side": ps, "only_side": True, "top": None})
                    elif parts:
                        parts[-1]["text"] += " " + p
                    continue
                ranked = tree.rank(st, ps)
                top = ranked[0][0].id if ranked and ranked[0][1] >= 0.5 else None
                if top is None and parts and not parts[-1]["only_side"]:
                    parts[-1]["text"] += ", " + p   # «комплект», «оригинал» — уточнение к предыдущей детали
                else:
                    parts.append({"text": p, "side": ps, "only_side": False, "top": top})
            details = [x for x in parts if not x["only_side"]]
            if not details or (len({x["top"] for x in details if x["top"] is not None}) < 2
                               and len(details) == len(parts)):
                out.append((self.clean(chunk), whole))
                continue
            for i, x in enumerate(parts):
                if not x["only_side"]:
                    out.append((self.clean(x["text"]), T.Side(x["side"].axis or whole.axis, x["side"].lr or whole.lr)))
                    continue
                near = (next((y for y in reversed(parts[:i]) if not y["only_side"]), None)
                        or next((y for y in parts[i + 1:] if not y["only_side"]), None))
                if near:
                    base = T.RIGHT.sub("", T.LEFT.sub("", T.REAR.sub("", T.FRONT.sub("", near["text"]))))
                    out.append((self.clean(f"{base} {x['text']}"), x["side"]))
        return out

    def clean(self, query: str) -> str:
        """«Здравствуйте, вин , нужны передние колодки» → «передние колодки»."""
        ws = re.split(r"(\s+|,)", query)
        i = 0
        while i < len(ws) and (not ws[i].strip(" ,.!") or ws[i].strip(" ,.!").lower() in self.stop):
            i += 1
        return re.sub(r"\s+", " ", re.sub(r"\s*,\s*(,\s*)+", ", ", "".join(ws[i:]))).strip(" ,.") or query.strip()

    async def position(self, v: Vehicle, tree: TreeIndex, query: str, want: T.Side) -> dict:
        pos: dict[str, Any] = {"query": query, "side": {"axis": want.axis, "lr": want.lr}, "status": "not_found",
                               "groups": [], "variants": [], "question": "", "note": ""}
        q = T.stems(query, self.stop)
        ranked = tree.rank(q, want)
        if not ranked or ranked[0][1] < 0.3:
            return pos
        best = ranked[0][1]
        groups = [(g, s) for g, s in ranked[:3] if s >= max(0.3, best - 0.2)]
        pos["groups"] = [{"id": g.id, "name": g.name, "path": g.path, "score": round(s, 2)} for g, s in groups]
        lists = await asyncio.gather(*(self.catalog.details(v, g.id, False) for g, _ in groups),
                                     return_exceptions=True)
        pool = [d for x in lists if isinstance(x, list) for d in x]
        members = {(d.group_id, _key(d.oem)) for d in pool}
        cands = self._candidates(tree, q, want, groups, pool, members)
        if not cands:
            # В составе группы нужной стороны нет — ищем по словам во всех узлах этих групп
            lists = await asyncio.gather(*(self.catalog.details(v, g.id, True) for g, _ in groups),
                                         return_exceptions=True)
            pool += [d for x in lists if isinstance(x, list) for d in x]
            cands = self._candidates(tree, q, want, groups, pool, members)
        if not cands:
            return pos

        kept = await self._check_sides(v, cands, want)
        if not kept:
            pos["note"] = "По описаниям поставщиков найденные номера относятся к другой стороне."
            return pos
        kinds: dict[tuple, list[Candidate]] = {}
        for c in kept:
            kinds.setdefault(c.kind, []).append(c)
        pos["variants"] = [self._variant(c, v, alt=i > 0) for cs in kinds.values()
                           for i, c in enumerate(sorted(cs, key=lambda c: (not (c.rows and _has_original(c, v)), -c.score)))]
        pos["status"], pos["question"] = verdict(list(kinds), want)
        main = next(c for c in kept if c.d.oem == pos["variants"][0]["oem"])
        pos["note"] = self._note(q, query, main, tree)
        if pos["status"] == "found" and not main.member and main.prec >= 1.0:
            self.catalog.learn(main.d.group_id, main.d.name)
        return pos

    def _candidates(self, tree: TreeIndex, q: list[str], want: T.Side, groups, pool: list[Detail],
                    members: set) -> list[Candidate]:
        gscore = {g.id: s for g, s in groups}
        gwords = {g.id: T.stems(g.name, self.stop) for g, _ in groups}
        names = {id(d): T.stems(d.name, self.stop) for d in pool}
        known = tree.known(q)
        extra = [s for s in q if s not in known and any(T.same(s, t) for st in names.values() for t in st)]
        cands: dict[str, Candidate] = {}
        for d in pool:
            if d.match is False:
                continue
            ds = T.side(d.context, self.pr_axis)
            if want.conflicts(ds):
                continue
            member = (d.group_id, _key(d.oem)) in members
            if member:
                # Состав группы — главный признак; слова клиента в названии только упорядочивают
                # (у Ford воздушный фильтр — «Фильтрующий элемент», трос ручника — тоже в «колодках ручника»)
                prec, rec = tree.score(known + extra, names[id(d)])
                score = gscore.get(d.group_id, 0) + 0.5 * prec
            else:
                # Не из состава группы — только по словам; слова группы помогают («ступичн» ~ «ступица»)
                qd = list(dict.fromkeys(known + extra + gwords.get(d.group_id, [])))
                prec, rec = tree.score(qd, names[id(d)])
                if prec < 0.45:
                    continue
                score = 0.6 * prec + 0.2 * rec
            score += 0.05 if d.match else 0
            k = _key(d.oem)
            if k not in cands or score > cands[k].score:
                cands[k] = Candidate(d, score, prec, member, ds, "каталог" if ds.axis else "")
        if not cands:
            return []
        mem = [c for c in cands.values() if c.member]
        if mem:
            top = max(c.score for c in mem)
            tier = [c for c in mem if c.score >= 0.85 * top]
            if not want.axis:
                # Клиент сторону не назвал: «подшипник ступицы» у Ford — передний подшипник, а сзади
                # в той же группе ступица в сборе. Покажем и другую сторону, чтобы спросить, а не угадывать.
                axes = {c.side.axis for c in tier}
                other = sorted((c for c in cands.values() if c not in tier and c.side.axis
                                and c.side.axis not in axes
                                and (c.score >= 0.7 * top if c.member else c.prec >= 0.5)), key=lambda c: -c.score)
                tier += other[:1]
        else:
            top = max(c.score for c in cands.values())
            tier = [c for c in cands.values() if c.score >= 0.85 * top]
        if any(c.d.match for c in tier):
            tier = [c for c in tier if c.d.match]
        return sorted(tier, key=lambda c: -c.score)[:MAX_VARIANTS]

    async def _check_sides(self, v: Vehicle, cands: list[Candidate], want: T.Side) -> list[Candidate]:
        """Цены и описания поставщиков: заодно проверяем сторону там, где каталог её не написал."""
        priced, seen = [], set()
        for c in cands:   # сначала по одному номеру на каждую деталь, потом остальные
            if c.kind not in seen:
                priced.append(c)
                seen.add(c.kind)
        priced += [c for c in cands if c not in priced]
        await asyncio.gather(*(self._price(v, c) for c in priced[:MAX_PRICED]))
        kept = []
        for c in cands:
            axis, f, r = c.vote
            if axis and not c.side.axis:
                c.side, c.side_source = T.Side(axis, c.side.lr), "поставщики"
            elif axis and c.side.axis and axis != c.side.axis:
                c.warning = (f"Каталог: {T.AXIS_RU[c.side.axis]}, а поставщики чаще пишут "
                             f"{T.AXIS_RU[axis]} ({f} против {r}) — проверьте")
            if not want.conflicts(c.side):
                kept.append(c)
        return kept

    async def _price(self, v: Vehicle, c: Candidate):
        try:
            c.brand, c.rows = await self.offers(c.d.oem, v.brand)
        except Exception:
            c.brand, c.rows = "", None
        if c.rows:
            c.vote = axis_vote(c.rows)

    async def offers(self, oem: str, car_brand: str) -> tuple[str, list[dict]]:
        """Бренд оригинала из «Вы искали» и его предложения. FOMOCO у Ford отдаёт только сам номер,
        без аналогов — поэтому сначала основной бренд (FORD), а пустой ответ — повод взять следующий."""
        key = (_key(oem), _key(car_brand))
        hit = self._offers.get(key)
        if hit and time.time() - hit[0] < OFFERS_TTL:
            return hit[1], hit[2]
        brands = brand_candidates(await self.src.brands(oem), car_brand) or [oem_brand_for(car_brand)]
        brand, rows = brands[0], []
        for b in brands[:2]:
            rows = await self.src.offers(oem, b)
            if rows:
                brand = b
                break
        self._offers[key] = (time.time(), brand, rows)
        if len(self._offers) > 3000:
            self._offers.clear()
        return brand, rows

    def _variant(self, c: Candidate, v: Vehicle, alt: bool) -> dict:
        d = c.d
        return {
            "oem": d.oem, "brand": c.brand or oem_brand_for(v.brand), "name": d.name, "note": d.note,
            "amount": d.amount, "unit": d.unit, "unit_note": d.unit_note, "match": d.match, "alt": alt,
            "axis": c.side.axis, "lr": c.side.lr, "side_source": c.side_source,
            "vote": {"front": c.vote[1], "rear": c.vote[2]}, "warning": c.warning, "score": round(c.score, 2),
            "scheme": {"catalog": v.catalog, "unit_id": d.unit_id, "ssd": d.unit_ssd,
                       "image": image_url(d.image) if d.image else "", "code": d.code_on_image},
            "offers": curate(c.rows, d.oem, v.brand, self.warranty) if c.rows else None,
        }

    def _note(self, q: list[str], query: str, c: Candidate, tree: TreeIndex) -> str:
        """Чего из запроса нет в найденной детали: «подшипник» → в каталоге «Ступица колеса»."""
        have = T.stems(c.d.name + " " + c.d.unit, self.stop)
        has = lambda s: any(T.same(s, x) for x in have)  # noqa: E731
        for n in self.notes:
            if all(any(T.same(s, x) for x in q) for s in n["query"]) \
                    and all(has(s) for s in n["detail"]) and not any(has(s) for s in n["lacks"]):
                return n["text"]
        if c.member:
            return ""   # деталь из состава группы — названия у Ford бывают любые («Фильтрующий элемент»)
        missing = set(tree.known([s for s in q if not has(s)]))  # марку и модель не считаем
        words = [w for w in T.words(query) if T.stem(w) in missing]
        if not words:
            return ""
        return f"Отдельно «{' '.join(dict.fromkeys(words))}» в каталоге не нашли — для этой машины там «{c.d.name}»."


def _has_original(c: Candidate, v: Vehicle) -> bool:
    o = curate(c.rows or [], c.d.oem, v.brand, set())
    return bool(o["original"])


def verdict(kinds: list[tuple[str, str, str]], want: T.Side) -> tuple[str, str]:
    if len(kinds) == 1:
        return "found", ""
    axes = {k[0] for k in kinds}
    lrs = [k[1] for k in kinds]
    if not want.axis and len(axes - {""}) > 1:
        return "choose", "Нужны передние или задние?"
    if len(axes) == 1 and "" not in lrs and len(set(lrs)) == len(kinds) and not want.lr:
        return "found", ""   # левая и правая — разные номера, нужны обе
    return "choose", "В каталоге несколько вариантов — уточните по примечанию или по номеру позиции на схеме."


def compare_model(model: str, v: Vehicle) -> list[str]:
    """Клиент выбирает модель из списка руками и ошибается — сверяем только год и объём двигателя."""
    out = []
    if not model:
        return out
    m = re.search(r"\b(19[89]\d|20[0-4]\d)\b", model)
    made = v.attrs.get("manufactured") or (re.search(r"(19|20)\d\d", v.attrs.get("date", "")) or [""])[0]
    if m and made and abs(int(m.group(1)) - int(made[:4])) > 1:
        out.append(f"В заявке {m.group(1)} год, по VIN — {made[:4]}")
    m = re.search(r"\b(\d)[.,](\d)\b", model)
    eng = " ".join(v.attrs.get(k, "") for k in ("engine", "engine_info"))
    e = re.search(r"\b(\d)[.,](\d)\s*L\b", eng, re.I) or re.search(r"\b(\d)(\d)\d\d\s*CC\b", eng, re.I)
    if m and e and (m.group(1), m.group(2)) != (e.group(1), e.group(2)):
        out.append(f"В заявке двигатель {m.group(1)}.{m.group(2)}, по VIN — {e.group(1)}.{e.group(2)}")
    return out


# ---------- черновик ответа клиенту ----------

def money(x: float) -> str:
    return f"{int(round(x)):,}".replace(",", " ") + " ₽"


def when(days: int) -> str:
    return "в наличии" if days <= 0 else f"~{days} дн."


def _side_ru(var: dict) -> str:
    return " ".join(x for x in (T.AXIS_RU.get(var["axis"], ""), T.LR_RU.get(var["lr"], "")) if x)


def draft(res: dict) -> str:
    st = res["status"]
    if st == "no_vin":
        return "Пришлите, пожалуйста, VIN (17 знаков) или номер кузова — подберём точно по вашей машине."
    if st == "vehicle_not_found":
        return ("По этому VIN машина в каталоге не нашлась. Проверьте VIN или пришлите фото СТС — "
                "подберём вручную.")
    if st == "catalog_error":
        return "Каталог сейчас не отвечает, подберём вручную."
    if st == "choose_vehicle":
        lines = ["По VIN нашлось несколько вариантов машины, уточните ваш:"]
        lines += [f"{i + 1}) {x['summary']}" for i, x in enumerate(res["vehicles"])]
        return "\n".join(lines)
    if st == "no_positions":
        return "Машину нашли. Напишите, какие запчасти нужны."
    lines = [f"Ваш автомобиль: {res['vehicle']['summary']}."]
    lines += [f"⚠ {w}" for w in res["warnings"]]
    for i, p in enumerate(res["positions"], 1):
        lines += ["", f"{i}. {p['query']}"]
        if p["status"] == "not_found":
            lines.append(p["note"] or "В каталоге для вашей машины такой позиции не нашли — подберёт менеджер.")
            continue
        if p["note"]:
            lines.append(p["note"])
        if p["question"]:
            lines.append(p["question"])
        shown = 3 if p["status"] == "choose" else 5
        for var in p["variants"]:
            side = _side_ru(var)
            o = var["offers"]
            if var["alt"]:
                price = f" — {money(o['original']['price'])}" if o and o["original"] else ""
                what = "Версия Motorcraft" if "motorcraft" in var["name"].lower() else "Тот же оригинал под другим номером"
                lines.append(f"{what}: {var['brand']} {var['oem']}{price}")
                continue
            name, _, rest = var["name"].partition(",")
            n = re.match(r"\d+", var["amount"] or "")
            amount = f", {n.group(0)} шт. на автомобиль" if n and n.group(0) != "1" else ""
            head = f"Оригинал: {var['brand']} {var['oem']} «{name.strip()}»" + (f" ({side})" if side else "") + amount
            if o and o["original"]:
                head += f" — {money(o['original']['price'])}, {when(o['original']['days'])}"
            lines.append(head)
            if rest.strip():
                lines.append(f"Из каталога: {rest.strip()}")
            if var["warning"]:
                lines.append(f"⚠ {var['warning']}")
            if o and o["analogs"]:
                lines.append("Аналоги:")
                for a in o["analogs"][:shown]:
                    tags = f" ({', '.join(a['tags'])})" if a["tags"] else ""
                    lines.append(f"• {a['brand']} {a['number']} — {money(a['price'])}, {when(a['days'])}{tags}")
                s = o["stats"]
                lines.append(f"Всего вариантов: {s['articles']}, от {money(s['price_min'])} до {money(s['price_max'])}.")
            elif o is None:
                lines.append("Цены уточнит менеджер.")
    return "\n".join(lines)
