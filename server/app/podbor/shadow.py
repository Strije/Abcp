"""Теневой режим: модель разбирает каждое первое сообщение рядом с правилами, расхождения — в журнал.

Клиенту отвечают по-прежнему правила (быстро, без расхода тарифа на задержку); модель работает после ответа, в фоне.
Расхождение — повод для разбора: либо правила ошиблись (как «кольцо подвесного» → форсунки), либо модель выдумала.
Когда на каком-то виде запросов модель стабильно права, а правила нет, там можно переключать порядок: сначала модель.

    python -m app.podbor.review report   # в отчёте — раздел «Расхождения правил и модели»
"""
from typing import Any

from . import text as T
from . import understand as U


def _stems(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        out |= set(T.stems(T.expand(t or "")))
    return out


def compare(parsed: dict | None, res: dict) -> dict | None:
    """Позиции правил и модели: совпадают ли они по главному слову детали. None — сравнивать не с чем."""
    if not parsed or not parsed.get("positions") or res.get("status") not in ("ok", "answer"):
        return None
    rules = [p["query"] for p in res.get("positions", []) if p.get("kind", "") in ("", "part")]
    if not rules:
        return None
    pool = [_stems(p["query"], *[v["name"] for v in p.get("variants", [])], *[g["name"] for g in p.get("groups", [])])
            for p in res.get("positions", []) if p.get("kind", "") in ("", "part")]
    llm = [p["part"] for p in parsed["positions"] if p.get("kind") in ("part", "")]
    miss = []
    for part in llm:
        head = T.head(T.stems(part))
        if head and not any(any(T.same(head, s) for s in st) for st in pool):
            miss.append(part)
    agree = not miss and len(llm) >= len(rules)
    return {"agree": agree, "rules": rules, "llm": llm, "llm_not_in_rules": miss,
            "groups": [[g["name"] for g in p.get("groups", [])[:2]] for p in res.get("positions", [])]}


async def run(llm: Any, text: str, res: dict) -> dict | None:
    """Разобрать сообщение моделью и сравнить с ответом правил; ошибки модели — не наша забота (None)."""
    if not llm or not getattr(llm, "enabled", False):
        return None
    try:
        parsed = await U.understand(llm, "\n".join(res.get("request", {}).get("chunks") or [text]))
    except Exception:
        return None
    return compare(parsed, res)
