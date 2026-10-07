"""Судья: модель читает вчерашние разговоры из журнала и отмечает, где робот мог ошибиться.

    python -m app.podbor.review run [папка журнала] [--days 1] [--limit 200]   # разобрать новые реплики
    python -m app.podbor.review report [папка журнала] [--days 7]               # подозрения за дни

Журнал — <state_dir>/podbor/journal/ГГГГ-ММ-ДД.jsonl (journal.py), находки — рядом, в review/ГГГГ-ММ-ДД.jsonl.
Судья ничего не исправляет и в ответы клиентам не вмешивается: он только находит места для разбора человеком,
а подтверждённая ошибка становится сценарием (podbor_scenarios.json) или примером для инструкции модели.
Он ошибается и сам, поэтому в находке — причина и слова клиента; решает человек. Ключ модели — LLM_API_KEY на сервере.
Реплика уже разобрана — повторно не отправляем; лимит в день — защита тарифа (--limit).
"""
import argparse
import asyncio
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

KINDS = ("wrong_part", "lost_word", "extra_position", "wrong_order", "bad_price", "bad_question", "no_answer", "other")

SYSTEM = """Ты проверяешь работу робота подбора автозапчастей. Клиент магазина пишет сообщение, робот отвечает:
находит детали в каталоге по VIN, показывает варианты с ценой и сроком, принимает выбор, отвечает на вопросы.
Тебе дают предыдущие реплики клиента (если были), текущую реплику и ответ робота. Найди ошибки робота.
Ответь ТОЛЬКО JSON без пояснений:

{"verdict": "ok|suspect", "problems": [{"kind": "...", "what": "одно предложение: что не так, словами клиента"}]}

kind:
- wrong_part — показана не та деталь, о которой просил клиент (монтажный комплект вместо колодок, пыльник вместо ШРУСа,
  свеча накаливания вместо свечи зажигания, оригинал для другой стороны или другого узла);
- lost_word — слово клиента потеряно или понято не так (фирма, сторона, количество, «внутренний», «салонный»);
- extra_position — в ответе позиции, о которых клиент не просил (свечи «сзади» после «а задние?» про колодки);
- wrong_order — оформлено не то: выбран не названный вариант, потерян или добавлен лишний товар, неверное количество;
- bad_price — цена выглядит ошибкой (в разы ниже других, ноль, одна цена за комплект и за штуку вперемешку);
- bad_question — вопрос клиенту лишний, уже названный им, или не относится к делу;
- no_answer — клиент спросил, а ответа по сути нет («не нашли», хотя варианты есть выше; пустая отписка);
- other — другое, объясни в what.

Правила:
- Не придумывай ошибок. Нет явной ошибки или сомневаешься — verdict ok и пустой список.
- Судишь только по тексту реплики и ответа. Номера деталей, совместимость с машиной и «правильную» цену ты не знаешь:
  название поставщика с другой машиной в описании — не ошибка (совместимость по OEM-номеру из каталога).
- Ответ робота — текст клиенту; «Есть ещё N вариантов — подберём под бюджет» — норма, это не отписка.
- Если клиент пишет о не-запчастях (оплата, доставка, адрес, фото) и робот передаёт менеджеру — это не ошибка.
- Не больше трёх проблем, самые заметные."""


def parse(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("verdict") not in ("ok", "suspect"):
        return None
    problems = []
    for x in d.get("problems") or []:
        if isinstance(x, dict) and str(x.get("what") or "").strip():
            kind = x.get("kind") if x.get("kind") in KINDS else "other"
            problems.append({"kind": kind, "what": str(x["what"]).strip()[:300]})
    problems = problems[:3]
    # «suspect» без проблем — не находка, а «ok» с проблемами — противоречие: доверяем списку
    return {"verdict": "suspect" if problems else "ok", "problems": problems}


def _read(folder: Path, days: int) -> list[dict]:
    edge = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = []
    for f in sorted(folder.glob("*.jsonl")):
        if f.stem >= edge:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass   # оборванная строка при падении — пропускаем
    return rows


def cases(rows: list[dict], done: set[str]) -> list[dict]:
    """Реплики на разбор: история разговора, реплика, ответ. Сначала те, что люди отметили «не помог»."""
    marks = {(r["dialog"], r["turn"]): r for r in rows if r.get("type") == "feedback"}
    history: dict[str, list[str]] = {}
    replies: dict[str, list[str]] = {}   # что робот отвечал раньше: без этого «ещё варианты» похожи на повтор
    out = []
    for r in rows:
        if r.get("type") != "turn":
            continue
        dialog = r.get("dialog") or ""
        before = history.setdefault(dialog, []) if dialog else []
        earlier = replies.setdefault(dialog, []) if dialog else []
        key = f"{dialog}:{r.get('turn')}:{r.get('t')}"
        if key not in done and r.get("answer"):
            m = marks.get((dialog, r.get("turn")))
            out.append({"key": key, "dialog": dialog, "turn": r.get("turn"), "t": r.get("t"), "car": r.get("car", ""),
                        "before": list(before[-3:]), "earlier": list(earlier[-2:]), "text": r.get("text", ""), "answer": r.get("answer", ""),
                        "status": r.get("status"), "human": None if not m else m.get("good"),
                        "comment": (m or {}).get("comment", "")})
        if dialog:
            before.append(r.get("text", ""))
            earlier.append(r.get("answer", "")[:900])
    out.sort(key=lambda c: (c["human"] is not False, c["t"] or ""))
    return out


def prompt(c: dict) -> str:
    parts = []
    if c["car"]:
        parts.append(f"Машина: {c['car']}")
    if c["before"]:
        parts.append("Раньше клиент писал:\n" + "\n".join(f"— {t[:300]}" for t in c["before"]))
    if c.get("earlier"):
        parts.append("Раньше робот ответил (чтобы не считать новое повтором):\n" + "\n---\n".join(c["earlier"]))
    parts.append(f"Сейчас клиент пишет:\n{c['text'][:1200]}")
    parts.append(f"Ответ робота [{c['status']}]:\n{c['answer'][:3500]}")
    return "\n\n".join(parts)


async def review(llm, c: dict) -> dict | None:
    try:
        raw = await llm.chat(SYSTEM, prompt(c), max_tokens=500)
    except Exception:
        return None   # модель недоступна — реплика останется на следующий запуск
    return parse(raw)


async def run(folder: Path, llm, days: int = 1, limit: int = 200) -> dict:
    out_dir = folder / "review"
    out_dir.mkdir(parents=True, exist_ok=True)
    done = set()
    for f in out_dir.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["key"])
            except (ValueError, KeyError):
                pass
    todo = cases(_read(folder, days), done)
    stat = {"cases": len(todo), "sent": 0, "suspect": 0, "failed": 0}
    out_file = out_dir / f"{date.today().isoformat()}.jsonl"
    for c in todo[:limit]:
        res = await review(llm, c)
        stat["sent"] += 1
        if res is None:
            stat["failed"] += 1
            continue
        stat["suspect"] += res["verdict"] == "suspect"
        rec = {"key": c["key"], "dialog": c["dialog"], "turn": c["turn"], "t": c["t"],
               "reviewed": datetime.now().isoformat(timespec="seconds"), "human": c["human"], "comment": c["comment"],
               "text": c["text"][:600], "answer_head": c["answer"][:600]} | res
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return stat


def report(folder: Path, days: int = 7) -> str:
    edge = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = []
    for f in sorted((folder / "review").glob("*.jsonl")):
        if f.stem >= edge:
            rows += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    bad = [r for r in rows if r["verdict"] == "suspect" or r.get("human") is False]
    kinds: dict[str, int] = {}
    for r in bad:
        for p in r.get("problems") or [{"kind": "human"}]:
            kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    lines = [f"Разобрано {len(rows)}, подозрений {sum(r['verdict'] == 'suspect' for r in rows)}, "
             f"отмечено «не помог» людьми {sum(r.get('human') is False for r in rows)}",
             "По видам: " + (", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda x: -x[1])) or "—")]
    for r in bad:
        lines += ["", f"— {r['t'][:16]} разговор {r['dialog'] or '—'}, реплика {r['turn']}"
                      + (" · не помог" if r.get("human") is False else ""),
                  f"  КЛИЕНТ: {r['text'][:300]}"]
        lines += [f"  [{p['kind']}] {p['what']}" for p in r.get("problems") or []]
        if r.get("comment"):
            lines.append(f"  КОММЕНТАРИЙ: {r['comment']}")
    return "\n".join(lines)


def shadow_report(folder: Path, days: int = 7) -> str:
    """Где модель и правила разобрали сообщение по-разному (теневой режим): кандидат в «сначала модель»."""
    rows = [r for r in _read(folder, days) if r.get("type") == "shadow"]
    diff = [r for r in rows if not r.get("agree")]
    lines = [f"Теневой разбор: {len(rows)}, совпало {len(rows) - len(diff)}, расхождений {len(diff)}"]
    for r in diff[:40]:
        lines += ["", f"— {r['t'][:16]} разговор {r['dialog'] or '—'}",
                  f"  правила: {', '.join(r.get('rules') or []) or '—'}", f"  модель:  {', '.join(r.get('llm') or []) or '—'}"]
    return "\n".join(lines)


def _folder(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    from ..config import load
    return Path(load().state_dir) / "podbor" / "journal"


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Судья: разбор журнала подбора моделью")
    ap.add_argument("cmd", choices=("run", "report"))
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args(argv)
    folder = _folder(args.folder)
    if args.cmd == "report":
        print(report(folder, args.days or 7))
        print("\n" + shadow_report(folder, args.days or 7))
        return 0
    from ..config import load
    from ..llm import LLM
    llm = LLM(load())
    if not llm.enabled:
        print("модель не подключена (LLM_URL, LLM_API_KEY)")
        return 1
    stat = await run(folder, llm, args.days or 1, args.limit)
    print(f"реплик на разбор {stat['cases']}, отправлено {stat['sent']}, подозрений {stat['suspect']}, "
          f"не удалось {stat['failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
