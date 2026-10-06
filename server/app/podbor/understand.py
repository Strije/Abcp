"""Разбор сообщения клиента языковой моделью: сумбурный текст → список позиций для подбора.

    «Задок: плавающие и втулки стабилизатора / Передок: Пер рычаги либо все либо отдельно шаров и майл /
     Тяги+ наконечники / Эл-ты стаба под вопросом / Опоры амортов + компл»
    → сайлентблоки плавающие (зад), втулки стабилизатора (зад), рычаги передние (или: шаровые опоры
      передние, сайлентблоки передних рычагов), тяги рулевые, наконечники рулевые, стабилизатор: стойки
      и втулки (под вопросом), опоры передних амортизаторов, комплект опоры амортизатора…

Модель не видит VIN и персональные данные (вырезаются здесь) и не называет номера и цены — только
названия деталей, как их ищут в каталоге. Ответ — JSON; не разобрался — None, тогда разбор правилами.
"""
import json
import re
from typing import Any

from . import text as T

SYSTEM = """Ты помощник менеджера магазина автозапчастей. Тебе дают сообщение клиента. Разбери его в список запчастей,
которые нужно подобрать по каталогу. Ответь ТОЛЬКО JSON без пояснений, по схеме:

{"positions": [{"part": "...", "kind": "part|fluid|battery|tire|tool|chemistry|accessory",
                "axis": "front|rear|", "lr": "left|right|both|", "qty": число или null,
                "alternatives": ["..."], "uncertain": true|false, "note": "..."}],
 "questions": ["..."]}

kind: part — запчасть автомобиля; fluid — масло (моторное, в коробку, ГУР), антифриз, тормозная жидкость,
омывайка; battery — аккумулятор; tire — шины, колёсные диски; tool — инструмент; chemistry — химия
(очистители, герметики, присадки, смазки); accessory — аксессуары (коврики, чехлы, регистраторы, лампы).

Правила:
- part — название детали по-русски, полностью, как в каталоге: «сайлентблок переднего рычага», «опора
  амортизатора передняя», «втулка стабилизатора задняя», «наконечник рулевой тяги». Раскрывай сокращения
  и жаргон: стаб — стабилизатор, аморт — амортизатор, шаров/шаровая — шаровая опора, сайл/сайлент/майл —
  сайлентблок, пер/перед/передок — передний, зад/задок — задний, граната — ШРУС наружный, ГБЦ — головка
  блока цилиндров, ДПКВ — датчик положения коленвала, салонник — фильтр салона.
- Заголовки «Задок:», «Передок:», «Спереди:», «Сзади:» задают сторону всем строкам ниже до следующего заголовка.
- «X + Y», «X и Y», «X, Y» — разные позиции. «Опоры амортов + компл» — опора амортизатора и комплект
  к ней (подшипник, пыльник, отбойник): две позиции.
- «либо все, либо отдельно A и B» — одна позиция с alternatives: деталь в сборе в part, A и B в alternatives.
- «под вопросом», «по факту», «если нужно», «посмотрим» — uncertain: true. Замечания в скобках — в note.
- Сторона: axis — перед или зад, lr — лево, право или both, если сказано «левый и правый», «обе», «пара».
  Не сказано — пусто.
- Не придумывай детали, которых клиент не просил, и не пиши номера, цены, бренды.
- Машина и VIN магазину уже известны (VIN из текста убран) — не проси их и не уточняй модель.
- questions — только то, что спросил сам клиент (наличие, сроки, оплата, доставка, фото), его словами
  коротко; своих вопросов не добавляй. Масла, жидкости, АКБ, шины, инструмент, химию — тоже в positions,
  с нужным kind.
- Служебный текст (системные сообщения, рассылки, карточки контакта CRM, реклама) — не запчасти.
- Нет запчастей в сообщении — positions пустой."""

KINDS = ("part", "fluid", "battery", "tire", "tool", "chemistry", "accessory")

_PHONE = re.compile(r"(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
_MAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_CARD = re.compile(r"\b(?:\d[ -]?){16}\b")


def mask(text: str) -> str:
    """Перед отправкой модели: без телефонов, почты, карт и VIN — ей нужен только текст про детали."""
    t = _CARD.sub("[карта]", _MAIL.sub("[почта]", _PHONE.sub("[тел]", text)))
    return T._VIN_TOKEN.sub("[VIN]", t) if hasattr(T, "_VIN_TOKEN") else t


def parse(raw: str) -> dict | None:
    """JSON из ответа модели — даже если она обернула его в ```json … ``` или добавила слова."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("positions"), list):
        return None
    out = []
    for p in data["positions"]:
        if not isinstance(p, dict) or not str(p.get("part") or "").strip():
            continue
        axis = p.get("axis") if p.get("axis") in ("front", "rear") else ""
        lr = p.get("lr") if p.get("lr") in ("left", "right", "both") else ""
        alts = [str(a).strip() for a in p.get("alternatives") or [] if str(a).strip()][:4]
        qty = p.get("qty") if isinstance(p.get("qty"), int) and 0 < p.get("qty") < 100 else None
        kind = p.get("kind") if p.get("kind") in KINDS else "part"
        out.append({"part": str(p["part"]).strip()[:80], "kind": kind, "axis": axis, "lr": lr, "qty": qty,
                    "alternatives": alts, "uncertain": bool(p.get("uncertain")), "note": str(p.get("note") or "")[:120]})
    return {"positions": out[:12],
            "questions": [str(q)[:200] for q in data.get("questions") or []][:5],
            "not_parts": [str(q)[:80] for q in data.get("not_parts") or []][:5]}


async def understand(llm: Any, body: str) -> dict | None:
    """Сообщение клиента (без VIN) → разбор моделью или None (модель недоступна или ответила не то)."""
    if not llm or not getattr(llm, "enabled", False) or not body.strip():
        return None
    try:
        raw = await llm.chat(SYSTEM, mask(body)[:3000])
    except Exception:
        return None
    return parse(raw)


def items(parsed: dict) -> list[tuple[str, T.Side, dict]]:
    """Разбор → запросы для движка: (текст, сторона, сведения позиции). «Левый и правый» — без стороны:
    движок сам покажет обе, если у них разные номера. Альтернативы — отдельными позициями с пометкой."""
    out = []
    for p in parsed["positions"]:
        side = T.Side(p["axis"], p["lr"] if p["lr"] in ("left", "right") else "")
        out.append((p["part"], side, p))
        for a in p["alternatives"]:
            out.append((a, T.Side(p["axis"], ""), dict(p, part=a, alternatives=[], note=f"вместо «{p['part']}»")))
    return out


# ---------- следующее сообщение разговора ----------

REPLY_SYSTEM = """Ты помощник менеджера магазина автозапчастей. Клиенту уже показали подбор: ниже позиции П1, П2…
и варианты В1, В2… с ценой и сроком. Пришло следующее сообщение клиента. Определи, чего он хочет. Ответь ТОЛЬКО JSON:

{"picks": [{"p": 1, "v": 2, "qty": null}],
 "asks": [{"p": 1, "about": "cheaper|original|analogs|best|stock|spec|brand", "brand": ""}],
 "refine": [{"p": 1, "axis": "front|rear|", "lr": "left|right|both|", "attr": ""}],
 "new_parts": [{"part": "...", "kind": "part|fluid|battery|tire|tool|chemistry|accessory", "axis": "", "lr": ""}],
 "manager": ["..."], "clarify": "", "unsure": false}

Правила:
- picks — только если клиент явно выбрал или согласился: «давайте», «беру», «заказывайте», «оформляем», цена
  варианта («за 1650»), фирма («зекерт»), номер («первый», «2 вариант»), «оригинал давайте». p и v — номера из списка.
  Согласился, но непонятно какой вариант — clarify: короткий вопрос, какой вариант оформить.
- asks — вопрос о показанном: дешевле (cheaper), оригинал (original), аналоги/другие варианты (analogs),
  какой лучше (best), в наличии/на сегодня (stock), что за деталь — внутренний/наружный, левый/правый (spec),
  есть ли фирма X (brand, в brand — фирма латиницей, как пишут на упаковке).
- refine — та же деталь, но другая сторона или признак: «а задние?», «и левый», «а верхний?», «внутренний нужен».
- new_parts — новая деталь, которой нет в списке.
- manager — то, на что отвечает менеджер: оплата, доставка, адрес, время работы, когда приедет заказ, фото,
  возврат, «подумаю», «позже», «отмена», жалобы. Коротко, словами клиента.
- Не уверен, о чём речь, — unsure: true и clarify: один короткий вопрос клиенту.
- Не придумывай позиций и вариантов, которых нет в списке. Пустые части — пустые списки."""


def digest(mem: dict, analogs: int = 3) -> str:
    """Что уже показали клиенту — коротко, с номерами для модели: «П1: колодки передние / В1: Toyota (оригинал)
    9 000 ₽, 5 дн.» Без артикулов и закупочных цен (их в памяти и нет)."""
    from .dialog import shown
    lines = []
    for i, p in enumerate(mem.get("positions") or [], 1):
        head = f"П{i}: {p['query']}"
        if p.get("question"):
            head += f" (бот спросил: {p['question']})"
        lines.append(head)
        for j, x in enumerate(shown(p, analogs), 1):
            o = x["offer"]
            who = " (оригинал)" if x["who"] == "оригинал" else ""
            side = " ".join(v for v in (x["var"].get("axis"), x["var"].get("lr")) if v)
            lines.append(f"  В{j}: {o.get('brand')}{who} — {int(round(o.get('price') or 0))} ₽, "
                         f"{int(o.get('days') or 0)} дн.{(' [' + side + ']') if side else ''}")
    return "\n".join(lines[:80])


def parse_reply(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    ints = lambda x: x if isinstance(x, int) and 0 < x < 100 else None  # noqa: E731
    out = {"picks": [], "asks": [], "refine": [], "new_parts": [], "manager": [], "clarify": "", "unsure": False}
    for x in d.get("picks") or []:
        if isinstance(x, dict) and ints(x.get("p")) and ints(x.get("v")):
            out["picks"].append({"p": x["p"], "v": x["v"], "qty": ints(x.get("qty"))})
    for x in d.get("asks") or []:
        if isinstance(x, dict) and x.get("about") in ("cheaper", "original", "analogs", "best", "stock", "spec", "brand"):
            out["asks"].append({"p": ints(x.get("p")), "about": x["about"], "brand": str(x.get("brand") or "")[:40]})
    for x in d.get("refine") or []:
        if isinstance(x, dict) and ints(x.get("p")):
            out["refine"].append({"p": x["p"], "axis": x.get("axis") if x.get("axis") in ("front", "rear") else "",
                                  "lr": x.get("lr") if x.get("lr") in ("left", "right", "both") else "",
                                  "attr": str(x.get("attr") or "")[:30]})
    for x in d.get("new_parts") or []:
        if isinstance(x, dict) and str(x.get("part") or "").strip():
            out["new_parts"].append({"part": str(x["part"]).strip()[:80],
                                     "kind": x.get("kind") if x.get("kind") in KINDS else "part",
                                     "axis": x.get("axis") if x.get("axis") in ("front", "rear") else "",
                                     "lr": x.get("lr") if x.get("lr") in ("left", "right", "both") else ""})
    out["manager"] = [str(x)[:200] for x in d.get("manager") or [] if str(x).strip()][:5]
    out["clarify"] = str(d.get("clarify") or "")[:300]
    out["unsure"] = bool(d.get("unsure"))
    return out


async def understand_reply(llm: Any, text: str, mem: dict, analogs: int = 3) -> dict | None:
    """Следующее сообщение клиента + что уже показали → чего он хочет (или None — модель недоступна)."""
    if not llm or not getattr(llm, "enabled", False) or not text.strip():
        return None
    user = f"Показано клиенту:\n{digest(mem, analogs)}\n\nСообщение клиента:\n{mask(text)[:1500]}"
    try:
        raw = await llm.chat(REPLY_SYSTEM, user, max_tokens=600)
    except Exception:
        return None
    return parse_reply(raw)
