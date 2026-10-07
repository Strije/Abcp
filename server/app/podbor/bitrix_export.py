"""Выгрузка диалогов открытых линий Битрикс24 для обучения подбора — без персональных данных.

Портал отвечает только нашему серверу, поэтому запускать там (скрипт самостоятельный, только stdlib):

    scp server/app/podbor/bitrix_export.py root@109.73.199.217:/root/
    BX=https://…/rest/ID/КЛЮЧ/ python3 bitrix_export.py 2025-10-05 out.jsonl [2026-10-05]

Третий аргумент — «по какой день» (не включая). Вебхук: права imopenlines, im, crm.

Сохраняем только текст переписки и роль (клиент / менеджер). Имена участников, телефоны, почты, ссылки,
номера карт и госномера вырезаются; VIN остаётся — он нужен для подбора. Адрес вебхука — только из окружения.
Повторный запуск продолжает с места остановки.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BX = os.environ["BX"].rstrip("/")
SINCE, OUT = sys.argv[1], sys.argv[2]
UNTIL = sys.argv[3] if len(sys.argv) > 3 else ""   # не включая этот день
BATCH = 25


def call(method: str, params: dict | list) -> dict:
    data = urllib.parse.urlencode(params, doseq=True).encode()
    for attempt in range(8):
        try:
            with urllib.request.urlopen(f"{BX}/{method}.json", data=data, timeout=60) as r:
                d = json.load(r)
        except Exception as e:  # сеть, 5xx, 503 QUERY_LIMIT_EXCEEDED
            time.sleep(5 * (attempt + 1))
            continue
        if d.get("error") == "QUERY_LIMIT_EXCEEDED":
            time.sleep(5 * (attempt + 1))
            continue
        return d
    raise RuntimeError(f"{method}: не отвечает")


# ---------- маскирование ----------

# Мобильный и без префикса («903 425-76-49», «(978) 123-45-67»), и любой номер с +7 / 8 / 7 в начале:
# городской «8 (8692) 12-34-56» тоже. VIN и артикулы не задеваем: перед номером не бывает буквы или цифры
PHONE = re.compile(r"(?<![\dA-Za-z])(?:(?:\+7|8|7)[\s\-()]*(?:\d[\s\-()]*){10}|\(?9\d{2}\)?[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2})(?!\d)")
ROBOT = re.compile(r"^\s*Отправлено роботом", re.I)   # «Благодарим за заказ №…» — рассылка магазина, не клиент
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
CARD = re.compile(r"(?<!\d)(?:\d{4}[\s-]?){3}\d{4}(?!\d)")
PLATE = re.compile(r"(?<![0-9А-ЯA-Z])[АВЕКМНОРСТУХABEKMHOPCTYX]\s?\d{3}\s?[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s?\d{2,3}(?!\d)", re.I)
BB_USER = re.compile(r"\[USER=\d+[^\]]*\].*?\[/USER\]", re.I | re.S)
BB = re.compile(r"\[/?[A-Z]+(?:=[^\]]*)?\]", re.I)


# Частые имена (с уменьшительными): клиенты пишут менеджеру «Мансур, салам», «Саша, добрый день».
# Без слов, которые бывают обычными («Лада», «Вера», «Слава», «Лев», «Роман», «Роза»).
FIRST_NAMES = """Александр Алексей Анатолий Андрей Антон Аркадий Арсений Артём Артем Артур Борис Вадим Валентин Валерий
Василий Виктор Виталий Владимир Владислав Всеволод Вячеслав Геннадий Георгий Герман Глеб Григорий Даниил Данил Денис
Дмитрий Евгений Егор Иван Игорь Илья Кирилл Константин Леонид Максим Матвей Михаил Никита Николай Олег Павел Пётр
Петр Ростислав Руслан Святослав Семён Семен Сергей Станислав Степан Тимофей Тимур Фёдор Федор Филипп Эдуард Юрий
Ярослав Саша Шура Лёша Леша Андрюша Толя Дима Дмитрий Женя Жека Серёжа Сережа Серега Серёга Коля Миша Паша Петя Витя
Вова Володя Валера Вася Гена Гоша Гриша Костя Кирюха Максимка Никитос Олежка Стас Тёма Тема Юра Ярик Влад Вадик Денчик
Алла Алёна Алена Алина Алиса Анастасия Настя Ангелина Анна Аня Антонина Валентина Валерия Варвара Вероника Виктория
Вика Галина Галя Дарья Даша Диана Евгения Екатерина Катя Елена Лена Елизавета Лиза Жанна Зинаида Зоя Инна Ирина Ира
Карина Кира Кристина Ксения Ксюша Лариса Лидия Людмила Люда Маргарита Рита Марина Мария Маша Милана Наталья Наталия
Наташа Нина Оксана Ольга Оля Полина Светлана Света Софья София Соня Таисия Тамара Татьяна Таня Ульяна Юлия Юля Яна
Мансур Рустам Ахмед Ахмад Магомед Мухаммад Мухаммед Тимур Мурат Ислам Казбек Анзор Шамиль Ибрагим Омар Эльдар Заур
Рамазан Расул Джамал Алибек Арсен Ахмат Батыр Бахтияр Бекзод Ботир Вагиф Гусейн Давид Джабраил Дамир Ильдар Ильяс Иса
Исмаил Камиль Карим Марат Махмуд Муса Назар Нурлан Рамиль Ринат Рашид Ренат Салим Самир Сулейман Тагир Тахир Фарид
Хасан Хусейн Шахин Эмиль Юсуф Айдар Азат Альберт Арман Ашот Армен Гагик Самвел Тигран Гоги Гиви Резо Зураб Вано
Анзор Алихан Аслан Асланбек Беслан Заира Зарема Лейла Мадина Малика Марьям Патимат Сабина Фатима Эльвира Эльмира""".split()
_NAME_RE = re.compile(r"(?<![А-Яа-яЁё])(?:" + "|".join(sorted({re.escape(n[:-1] if n[-1] in "ая" else n) for n in FIRST_NAMES},
                      key=len, reverse=True)) + r")(?:а|я|у|ю|е|ой|ей|ом|ем|ы|и|ик|ичка|ка)?(?![а-яё])")


def clean(text: str, names: set[str]) -> str:
    t = BB_USER.sub("[имя]", text or "")
    t = BB.sub("", t)
    t = URL.sub("[ссылка]", t)
    t = EMAIL.sub("[почта]", t)
    t = CARD.sub("[карта]", t)
    t = PHONE.sub("[тел]", t)
    t = PLATE.sub("[госномер]", t)
    t = _NAME_RE.sub("[имя]", t)   # с заглавной буквы: «роман» и «лиза» (лизинг?) в тексте не трогаем
    for n in sorted(names, key=len, reverse=True):
        t = re.sub(rf"(?<![а-яёa-z]){re.escape(n)}(?![а-яёa-z])", "[имя]", t, flags=re.I)
    return re.sub(r"[ \t]+", " ", t).strip()


OPS: set[str] = set()          # имена менеджеров: клиенты зовут их по имени («Мансур, салам»)
SEEN_IDS: set[str] = set()


def learn_operators(r: dict):
    """Имена менеджеров — по их id из сообщений (в списке участников диалога их нет)."""
    users = r.get("users") or {}
    users = users if isinstance(users, dict) else {str(u.get("id")): u for u in users}
    msgs = r.get("message") or {}
    msgs = list(msgs.values()) if isinstance(msgs, dict) else msgs
    for m in msgs:
        sid = str(m.get("senderid") or "0")
        if sid == "0" or sid in users or sid in SEEN_IDS:
            continue
        SEEN_IDS.add(sid)
        u = call("im.user.get", {"ID": sid}).get("result") or {}
        for k in ("first_name", "last_name", "name", "firstName", "lastName"):
            for part in str(u.get(k) or "").split():
                if len(part) >= 3:
                    OPS.add(part)


def dialog(sid: str, meta: dict, r: dict) -> dict | None:
    users = r.get("users") or {}
    users = users if isinstance(users, dict) else {str(u.get("id")): u for u in users}
    clients = {k for k, u in users.items() if u.get("connector") or u.get("extranet")}
    bots = {k for k, u in users.items() if u.get("bot")}
    names = set(OPS)
    for u in users.values():
        for k in ("firstName", "lastName", "name"):
            for part in str(u.get(k) or "").split():
                if len(part) >= 3:
                    names.add(part)
    msgs = r.get("message") or {}
    msgs = list(msgs.values()) if isinstance(msgs, dict) else msgs
    out = []
    for m in sorted(msgs, key=lambda m: int(m.get("id") or 0)):
        sender = str(m.get("senderid") or "0")
        if sender == "0" or sender in bots:
            continue   # системные сообщения и боты
        text = clean(str(m.get("text") or ""), names)
        if not text or ROBOT.match(text):
            continue
        role = "client" if sender in clients else "manager"
        if out and out[-1]["role"] == role:
            out[-1]["text"] += "\n" + text   # подряд от одного — одно сообщение
        else:
            out.append({"role": role, "text": text})
    if not any(x["role"] == "client" for x in out):
        return None
    return {"sid": sid, "line": meta["line"], "created": meta["created"][:10], "messages": out}


def main():
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            done = {json.loads(line)["sid"] for line in f if line.strip()}
    sess_file = OUT + ".sessions.json"
    if os.path.exists(sess_file):
        sessions = json.load(open(sess_file, encoding="utf-8"))
    else:
        sessions, last = {}, 0
        while True:   # быстрая пагинация по ID без подсчёта total
            f = {"filter[<CREATED]": UNTIL} if UNTIL else {}
            d = call("crm.activity.list", {**f, "filter[PROVIDER_ID]": "IMOPENLINES_SESSION", "filter[>=CREATED]": SINCE,
                                           "filter[>ID]": last, "order[ID]": "ASC", "start": -1,
                                           "select[]": ["ID", "ASSOCIATED_ENTITY_ID", "PROVIDER_TYPE_ID", "CREATED"]})
            rows = d.get("result") or []
            for x in rows:
                sessions[str(x["ASSOCIATED_ENTITY_ID"])] = {"line": x.get("PROVIDER_TYPE_ID"), "created": x["CREATED"]}
            if len(rows) < 50:
                break
            last = rows[-1]["ID"]
            time.sleep(0.3)
        json.dump(sessions, open(sess_file, "w", encoding="utf-8"))
    todo = [s for s in sessions if s not in done]
    print(f"сессий: {len(sessions)}, уже выгружено: {len(done)}, осталось: {len(todo)}", flush=True)
    kept = skipped = 0
    t0 = time.time()
    with open(OUT, "a", encoding="utf-8") as f:
        for i in range(0, len(todo), BATCH):
            part = todo[i:i + BATCH]
            cmd = {f"cmd[s{s}]": f"imopenlines.session.history.get?SESSION_ID={s}" for s in part}
            d = call("batch", {"halt": 0, **cmd})
            res = (d.get("result") or {}).get("result") or {}
            for s in part:
                if isinstance(res.get(f"s{s}"), dict):
                    learn_operators(res[f"s{s}"])
            for s in part:
                r = res.get(f"s{s}")
                x = dialog(s, sessions[s], r) if isinstance(r, dict) else None
                if x:
                    f.write(json.dumps(x, ensure_ascii=False) + "\n")
                    kept += 1
                else:
                    f.write(json.dumps({"sid": s, "skip": True}) + "\n")   # чтобы не перезапрашивать
                    skipped += 1
            f.flush()
            if (i // BATCH) % 20 == 0:
                rate = (i + len(part)) / max(time.time() - t0, 1)
                print(f"{i + len(part)}/{len(todo)} · с перепиской {kept}, пустых {skipped} · "
                      f"осталось ~{(len(todo) - i) / max(rate, 0.01) / 60:.0f} мин", flush=True)
            time.sleep(0.6)
    print(f"готово: с перепиской {kept}, пустых {skipped}", flush=True)


if __name__ == "__main__":
    main()
