"""Журнал подбора: каждая реплика и ответ робота, оценки «помог / не помог» — для разбора и проверки после правок.

Файл на день: <state_dir>/podbor/journal/ГГГГ-ММ-ДД.jsonl. Телефоны, почта и номера карт заменяются
до записи (как перед моделью). Закупочных цен в ответе подбора нет — только цены клиента. Храним год.

    python -m app.podbor.journal /var/lib/avtodrug-api/podbor/journal [дней]   # разговоры и оценки за дни
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from .understand import _CARD, _MAIL, _PHONE

KEEP_DAYS = 365


def clean(text: str) -> str:
    """Без контактов клиента; VIN оставляем — без него разговор не повторить."""
    return _CARD.sub("[карта]", _MAIL.sub("[почта]", _PHONE.sub("[тел]", text or "")))


def brief(res: dict) -> dict:
    """Что ответил робот — коротко: позиции, найденные номера, вид ответа и текст клиенту."""
    reply = res.get("reply") or {}
    return {
        "status": res.get("status"), "seconds": res.get("seconds"),
        "llm": bool(res.get("understood")), "followup": bool(res.get("followup")),
        "car": (res.get("vehicle") or {}).get("short") or (res.get("vehicle") or {}).get("summary") or "",
        "positions": [{"query": p.get("query"), "kind": p.get("kind", ""), "status": p.get("status"),
                       "question": p.get("question", ""),
                       # Что подставила модель вместо слов клиента и какие группы давали правила — запас для словаря
                       # синонимов: повторяющиеся подтверждённые пары переносим в podbor_rules.json, модель им не нужна
                       **({"llm_part": p["llm_part"], "was_score": p.get("was_score")} if p.get("llm_part") else {}),
                       "groups": [g.get("name") for g in (p.get("groups") or [])[:3]],
                       "variants": [[v.get("oem"), v.get("name")] for v in p.get("variants", [])[:4]]}
                      for p in res.get("positions", [])],
        "reply": {"kind": reply.get("kind"), "picks": len(reply.get("picks") or []),
                  "unclear": reply.get("unclear") or []} if reply else None,
        "handoff": res.get("handoff") or [],
        "answer": clean(res.get("text") or "")[:6000],
    }


class Journal:
    def __init__(self, folder: Path | None):
        self.folder = folder
        self._cleaned = ""

    def _write(self, rec: dict):
        if not self.folder:
            return
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            day = date.today().isoformat()
            with open(self.folder / f"{day}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if self._cleaned != day:   # раз в день — старше года долой
                self._cleaned = day
                edge = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
                for old in self.folder.glob("*.jsonl"):
                    if old.stem < edge:
                        old.unlink(missing_ok=True)
        except OSError:
            pass   # журнал — для разбора; из-за него подбор не падает

    def turn(self, dialog: str, turn: int, text: str, res: dict):
        self._write({"t": datetime.now().isoformat(timespec="seconds"), "type": "turn", "dialog": dialog or "",
                     "turn": turn, "text": clean(text)[:4000]} | brief(res))

    def shadow(self, dialog: str, turn: int, diff: dict):
        """Разбор моделью рядом с правилами: совпали или нет — для судьи и для ручного разбора."""
        self._write({"t": datetime.now().isoformat(timespec="seconds"), "type": "shadow", "dialog": dialog or "",
                     "turn": turn, "agree": bool(diff.get("agree")), "rules": diff.get("rules"), "llm": diff.get("llm"),
                     "llm_not_in_rules": diff.get("llm_not_in_rules")})

    def feedback(self, dialog: str, turn: int, good: bool | None, comment: str):
        self._write({"t": datetime.now().isoformat(timespec="seconds"), "type": "feedback", "dialog": dialog or "",
                     "turn": turn, "good": good, "comment": clean(comment)[:1000]})

    def used(self, dialog: str, turn: int, how: str):
        """Подсказчик в Битрикс24: менеджер вставил (insert) или скопировал (copy) ответ робота — мера пользы."""
        self._write({"t": datetime.now().isoformat(timespec="seconds"), "type": "used", "dialog": dialog or "",
                     "turn": turn, "how": how})


def _read(folder: Path, edge: str) -> list[dict]:
    rows = []
    for f in sorted(folder.glob("*.jsonl")):
        if f.stem >= edge:
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass   # строка, оборванная при записи, — не повод не показать остальное
    return rows


def dialogs(folder: Path, days: int = 7) -> list[dict]:
    """Разговоры за дни для страницы журнала (/podbor/journal): реплики с ответами, оценки людей, находки судьи,
    вставки ответа менеджером. Свежие первыми."""
    edge = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = _read(folder, edge)
    judged = {(r.get("dialog"), r.get("turn"), r.get("t")): r for r in _read(folder / "review", edge)}
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r.get("dialog") or "", {"dialog": r.get("dialog") or "", "start": r.get("t", ""), "end": "",
                                                    "car": "", "turns": [], "good": 0, "bad": 0, "suspect": 0,
                                                    "used": 0, "empty": 0})
        d["end"] = max(d["end"], r.get("t", ""))
        kind = r.get("type")
        if kind == "turn":
            j = judged.get((r.get("dialog"), r.get("turn"), r.get("t"))) or {}
            d["car"] = d["car"] or r.get("car", "")
            empty = not (r.get("answer") or "").strip()
            d["empty"] += empty
            d["suspect"] += j.get("verdict") == "suspect"
            d["turns"].append({"turn": r.get("turn"), "t": r.get("t", ""), "text": r.get("text", ""),
                               "answer": r.get("answer", ""), "status": r.get("status", ""),
                               "seconds": r.get("seconds"), "llm": bool(r.get("llm")), "handoff": r.get("handoff") or [],
                               "positions": [{"query": p.get("query"), "status": p.get("status")}
                                             for p in r.get("positions") or []],
                               "judge": {"verdict": j["verdict"], "problems": j.get("problems") or []} if j.get("verdict") else None,
                               "feedback": [], "used": ""})
        elif kind in ("feedback", "used"):
            t = next((x for x in reversed(d["turns"]) if x["turn"] == r.get("turn")), None)
            if kind == "used":
                d["used"] += 1
                if t is not None:
                    t["used"] = r.get("how", "")
            else:
                d["good"] += r.get("good") is True
                d["bad"] += r.get("good") is False
                if t is not None:
                    t["feedback"].append({"good": r.get("good"), "comment": r.get("comment", "")})
    return sorted((d for d in out.values() if d["turns"]), key=lambda d: d["end"], reverse=True)


def show(folder: str, days: int = 1):
    """Разговоры за последние дни: реплика → ответ, с оценками."""
    edge = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = []
    for f in sorted(Path(folder).glob("*.jsonl")):
        if f.stem >= edge:
            rows += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    marks = {(r["dialog"], r["turn"]): r for r in rows if r["type"] == "feedback"}
    last = None
    for r in rows:
        if r["type"] != "turn":
            continue
        if r["dialog"] != last:
            print(f"\n######## разговор {r['dialog'] or '—'} · {r['t'][:16]} · {r.get('car', '')}")
            last = r["dialog"]
        print(f"\n— КЛИЕНТ: {r['text']}")
        print("— РОБОТ [" + r["status"] + (", модель" if r.get("llm") else "") + f", {r.get('seconds')} с]:")
        print("\n".join("    " + x for x in r.get("answer", "").splitlines()))
        m = marks.get((r["dialog"], r["turn"]))
        if m:
            print(f"    ОЦЕНКА: {'помог' if m['good'] else 'не помог' if m['good'] is False else '—'}"
                  + (f" — {m['comment']}" if m.get("comment") else ""))


if __name__ == "__main__":
    show(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1)
