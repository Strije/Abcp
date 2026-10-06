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

{"positions": [{"part": "...", "axis": "front|rear|", "lr": "left|right|both|", "qty": число или null,
                "alternatives": ["..."], "uncertain": true|false, "note": "..."}],
 "questions": ["..."], "not_parts": ["..."]}

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
  коротко; своих вопросов не добавляй. Не запчасти (инструмент, химия, масло, антифриз, шины) — в not_parts.
- Служебный текст (системные сообщения, рассылки, карточки контакта CRM, реклама) — не запчасти.
- Нет запчастей в сообщении — positions пустой."""

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
        out.append({"part": str(p["part"]).strip()[:80], "axis": axis, "lr": lr, "qty": qty, "alternatives": alts,
                    "uncertain": bool(p.get("uncertain")), "note": str(p.get("note") or "")[:120]})
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
