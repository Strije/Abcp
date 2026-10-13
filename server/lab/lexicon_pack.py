"""Словарь модели (lexicon.jsonl: key, name, canon, syn, kind) → компактный файл для робота (app/podbor/lexicon.py).

Оставляем: многословные названия (однословные без узла ненадёжны), не «other» (жгуты, таблички). Для каждого — главные
слова покупателя из canon и syn, которых нет в самом названии: «холостой шкив» → [ролик]. Если все слова покупателя уже
в названии («Поворотный кулак» → «поворотный кулак») — запись не нужна.

    cd server
    python lab/lexicon_pack.py lexicon.jsonl lexicon.json      # → потом на сервер: STATE_DIR/podbor/lexicon.json
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from app.podbor import text as T  # noqa: E402
from app.podbor.engine import _GENERIC_HEADS  # noqa: E402
from app.podbor.lexicon import family_key  # noqa: E402

MAX_HEADS = 4


def head(phrase: str) -> str:
    """Главное слово по форме слов, без «комплект»/«набор»: «комплект шруса передней полуоси» → «шрус»."""
    # Прилагательное — по окончанию самого слова («вентилируемый», «маловязкое»): основу правило T.ADJ знает не всегда
    for w in T.words(T.expand(phrase)):
        s = T.stem(w)
        if not s or len(s) < 3 or s in T.ADJ or s in _GENERIC_HEADS:
            continue
        # «крепление», «уплотнение» на «-ие» — существительные (иначе «Кронштейн натяжного шкива» → «ролик»)
        if re.fullmatch(r"[а-яё]+", w) and T._ADJ_END.search(w) and not re.search(r"[еа]ние$", w):
            continue
        return s
    return ""


def main(src: str, dst: str) -> None:
    out: dict[str, list[str]] = {}
    kept = skipped = 0
    for line in open(src, encoding="utf-8"):
        r = json.loads(line)
        key = r["key"]
        if r.get("kind") == "other" or len(re.findall(r"[a-zа-яё]{2,}", key)) < 2:
            skipped += 1
            continue
        own = T.stems(T.expand(r.get("name") or key))
        hs: list[str] = []
        words: list[str] = []
        for phrase in [r.get("canon") or ""] + list(r.get("syn") or []):
            h = head(phrase)
            if h and not any(T.same(h, s) for s in own) and h not in hs:
                hs.append(h)
            # Остальные слова покупателя («обводной ролик», «паразитный ролик») — для покрытия слов клиента
            words += [s for s in T.stems(T.expand(phrase)) if len(s) >= 3 and s not in _GENERIC_HEADS
                      and not s.endswith(".") and not any(T.same(s, o) for o in own + words)]
        if not hs:
            skipped += 1
            continue
        hs = hs[:MAX_HEADS]
        hs = [hs, [w for w in words if w not in hs][:8]]
        out[key] = hs
        fk = family_key(key)
        if len(fk.split()) >= 2:   # «втулка (резиновая)» → «втулка»: однословный почти-ключ ловил бы любую «Втулку»
            out.setdefault(fk, hs)
        kept += 1
    Path(dst).write_text(json.dumps({"why": "другие имена деталей (lab/lexicon_pack.py); не править руками", "names": out},
                                    ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"названий со словами покупателя: {kept}, пропущено: {skipped}, ключей в файле: {len(out)}, "
          f"размер: {Path(dst).stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
