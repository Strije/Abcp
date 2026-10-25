"""Ассистент «Кирилл»: проверяющий, уровни брендов, учёт денег, разговор с вопросами до подбора, Telegram."""
import asyncio
import json

import httpx
import pytest

from app.assistant import core as C
from app.assistant import quality as Q
from app.assistant.models import Gateway, OverBudget
from app.assistant.telegram import KirillBot, chunks, to_html
from app.podbor import Engine
from test_podbor import Fake

VIN = "X9FKXXEEBKCB57566"


def test_quality_tiers_from_file():
    assert Q.tier("KYB")[0] == "OE" and Q.tier("Kayaba")[0] == "OE" and Q.tier("Lemförder")[0] == "OE"
    assert Q.tier("Mann-Filter")[0] == "OE" and Q.tier("Masuma")[0] == "good" and Q.tier("Febest")[0] == "eco"
    assert Q.tier("Zekkert") == ("eco", "")


def test_checker_drops_cheap_other_part_and_names_original():
    """26.10, Lexus RX: «опора» за 310 ₽ при оригинале 4 280 ₽ — подшипник опоры; «стойка газомасляная» — амортизатор."""
    offers = [{"brand": "DAIHATSU", "price": 4280.0, "role": "оригинал", "description": "Опора"},
              {"brand": "PARTBERRY", "price": 310.0, "description": "Подшипник опоры амортизатора TOYOTA"},
              {"brand": "Zekkert", "price": 1340.0, "description": "Опора амортизатора передняя"}]
    kept, dropped = C.check("Опора стойки передней подвески, правая", offers, "LEXUS")
    assert [o["brand"] for o in kept] == ["Toyota (оригинал)", "Zekkert"] and "PARTBERRY" in dropped[0]
    shocks = [{"brand": "TOYOTA", "price": 29060.0, "role": "оригинал"},
              {"brand": "Just Drive", "price": 3350.0, "description": "Стойка газомасляная задняя левая"}]
    kept, dropped = C.check("Амортизатор задний левый", shocks, "LEXUS")
    assert len(kept) == 2 and not dropped


def test_quality_offers_from_all_rows():
    a = C.Assistant(Engine(Fake(), None, set()), Gateway("", ""), "m", None)
    rows = [{"brand": "Zekkert", "price": "450", "deliveryPeriod": "24", "description": "Тяга рулевая"},
            {"brand": "CTR", "price": "1470", "deliveryPeriod": "24", "description": "Тяга рулевая"},
            {"brand": "CTR", "price": "1300", "deliveryPeriod": "240", "description": "Тяга рулевая"},
            {"brand": "Masuma", "price": "960", "deliveryPeriod": "48", "description": "Тяга рулевая"},
            {"brand": "TOYOTA", "price": "8080", "deliveryPeriod": "120", "description": "Тяга"}]
    out = a.quality(rows, "TOYOTA", "Тяга рулевая", set())
    assert [(o["brand"], o["quality"], o["price"]) for o in out] == [("CTR", "OE", 1470.0), ("Masuma", "good", 960.0)]


def _gw_transport(answer: dict, usage=(1_000_000, 0)):
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"choices": [{"message": answer}],
                                         "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}})
    return httpx.MockTransport(handler)


def test_gateway_counts_rubles_and_stops_at_limit(tmp_path):
    gw = Gateway("https://gw.test", "k", str(tmp_path), day_rub=20.0,
                 transport=_gw_transport({"content": "ok"}))
    m, u = asyncio.run(gw.chat("anthropic/claude-haiku-5-5", [{"role": "user", "content": "hi"}]))
    assert m["content"] == "ok" and gw.today()["rub"] == 14.0   # 1 млн входящих Haiku — 14 ₽
    asyncio.run(gw.chat("anthropic/claude-haiku-5-5", [{"role": "user", "content": "hi"}]))
    with pytest.raises(OverBudget):   # 28 ₽ > 20 ₽ — дальше менеджеру
        asyncio.run(gw.chat("anthropic/claude-haiku-5-5", [{"role": "user", "content": "hi"}]))
    assert json.loads((tmp_path / "assistant_usage.json").read_text(encoding="utf-8"))


class ScriptGW(Gateway):
    """Подставная модель: расшифровщик — одна деталь и вопрос, подборщик — «не нашёл», продавец — текст."""

    def __init__(self):
        super().__init__("https://gw.test", "k")
        self.calls: list[str] = []

    async def chat(self, model, messages, tools=None, max_out=8000):
        names = {t["function"]["name"] for t in tools or []}
        if "decoded" in names:
            self.calls.append("decode")
            args = {"items": [{"need": "Колодки тормозные передние", "axis": "front", "certainty": "sure",
                               "words": "колодки"}], "questions": ["Диски тоже меняете?"]}
            return {"tool_calls": [{"id": "1", "function": {"name": "decoded", "arguments": json.dumps(args)}}]}, {"in": 10, "out": 5}
        if "pick" in names:
            self.calls.append("select")
            return {"tool_calls": [{"id": "2", "function": {"name": "pick", "arguments": '{"found": false}'}}]}, {"in": 10, "out": 5}
        self.calls.append("sell:" + messages[0]["content"].split("Потребность клиента: ")[1].split(".")[0])
        return {"content": "**Колодки:** передам менеджеру."}, {"in": 10, "out": 5}


def test_conversation_vin_questions_buttons_then_podbor(tmp_path):
    gw = ScriptGW()
    a = C.Assistant(Engine(Fake(), None, set()), gw, "m", tmp_path)
    r = asyncio.run(a.reply("tg-1", "нужны колодки передние"))
    assert r["status"] == "no_vin" and r["text"] == C.ASK_VIN
    r = asyncio.run(a.reply("tg-1", VIN))   # просьба до VIN не теряется: сразу вопросы до подбора
    assert r["status"] == "ask" and "Колодки тормозные передние" in r["text"] and "Диски тоже меняете?" in r["text"]
    assert r["buttons"][0][0] == ("Качество", "goal:quality") and gw.calls == ["decode"]
    r = asyncio.run(a.reply("tg-1", "", action="goal:quality"))
    assert r["status"] == "pref" and r["ack"] == "Принято: Качество"
    # Ответ текстом на уточнение — подбор с выбранным «качеством» и «срочно» из слов клиента
    r = asyncio.run(a.reply("tg-1", "диски не надо, нужно срочно"))
    # Не нашли — строка без модели; рамку ответа собирает код: потребность видна в заголовке
    assert r["status"] == "ok" and "Колодки" in r["text"] and "качество, срочно" in r["text"]
    assert r["text"].startswith("Здравствуйте!")
    # Тот же разговор: «надолго / срочно» не переспрашиваем (26.10, Audi: «Диски тормозные передние» снова получили
    # вопросы), только уточнение по делу — и одна кнопка «Подобрать»; предпочтения — с прошлого подбора
    r = asyncio.run(a.reply("tg-1", "а задние колодки?"))
    assert r["status"] == "ask" and "надолго" not in r["text"] and r["buttons"] == [[("✅ Подобрать", "go")]]
    r = asyncio.run(a.reply("tg-1", "", action="go"))
    assert r["status"] == "ok" and "качество, срочно" in r["text"] and not r["text"].startswith("Здравствуйте")
    assert asyncio.run(a.reply("tg-1", "", action="go"))["status"] == "stale"   # кнопка от старого вопроса
    # Новый разговор (больше получаса) — снова выясняем потребность
    s = a.sessions.load("tg-1")
    s["last_ok"] = 0
    a.sessions.save("tg-1", s)
    assert "надолго" in asyncio.run(a.reply("tg-1", "колодки задние"))["text"]


def test_prefs_from_text():
    assert C._prefs_from_text("Надолго, не срочно") == {"goal": "quality", "when": "wait"}
    assert C._prefs_from_text("подешевле и побыстрее") == {"goal": "cheap", "when": "urgent"}


def test_telegram_markup_and_chunks():
    assert to_html("**Колодки** <TRW> & *аналог*") == "<b>Колодки</b> &lt;TRW&gt; &amp; <i>аналог</i>"
    parts = chunks("строка\n" * 1000, 500)
    assert all(len(p) <= 500 for p in parts) and sum(p.count("строка") for p in parts) == 1000


def test_telegram_start_and_buttons(tmp_path):
    sent = []

    def handler(request: httpx.Request):
        if request.url.path.endswith("/sendMessage"):
            sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gw = ScriptGW()
    bot = KirillBot(http, "TOKEN", C.Assistant(Engine(Fake(), None, set()), gw, "m", tmp_path), gw)
    msg = {"chat": {"id": 7, "type": "private"}}
    asyncio.run(bot.handle({"message": dict(msg, text="/start")}))
    assert "Кирилл" in sent[-1]["text"]
    asyncio.run(bot.handle({"message": dict(msg, text=VIN + " колодки передние")}))
    kb = sent[-1]["reply_markup"]["inline_keyboard"]
    assert kb[0][0] == {"text": "Качество", "callback_data": "goal:quality"} and sent[-1]["parse_mode"] == "HTML"
    asyncio.run(bot.handle({"message": {"chat": {"id": 8, "type": "group"}, "text": "колодки"}}))   # в группах молчит
    assert not any(s["chat_id"] == 8 for s in sent)


def test_one_broken_group_does_not_break_answer(tmp_path):
    """26.10, Lexus: Laximo «E_GROUP_IS_NOT_SEARCHABLE» на одной группе ронял весь подбор в «передал менеджеру»."""
    gw = ScriptGW()
    a = C.Assistant(Engine(Fake(), None, set()), gw, "m", tmp_path)

    async def broken(*args, **kw):
        raise RuntimeError("E_GROUP_IS_NOT_SEARCHABLE")
    a.select = broken
    asyncio.run(a.reply("tg-2", VIN + " колодки передние"))
    r = asyncio.run(a.reply("tg-2", "", action="go"))
    assert r["status"] == "ok" and "Колодки" in r["text"]


def test_double_go_runs_podbor_once(tmp_path):
    """26.10: «Подобрать», нажатое дважды подряд, подбирало два раза (и тратило вдвое)."""
    gw = ScriptGW()
    a = C.Assistant(Engine(Fake(), None, set()), gw, "m", tmp_path)
    asyncio.run(a.reply("tg-3", VIN + " колодки передние"))

    async def both():
        return await asyncio.gather(a.reply("tg-3", "", action="go"), a.reply("tg-3", "", action="go"))
    statuses = sorted(r["status"] for r in asyncio.run(both()))
    assert statuses == ["ok", "stale"] and gw.calls.count("select") == 1


def test_voice_oga_sent_as_ogg(tmp_path):
    """26.10: голосовые Telegram — «file_0.oga», распознавание отвергало формат oga."""
    got = {}

    def handler(request: httpx.Request):
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/file_0.oga"}})
        return httpx.Response(200, content=b"OggS")

    class GW(ScriptGW):
        async def transcribe(self, audio, filename="voice.ogg"):
            got["name"] = filename
            return "колодки"
    bot = KirillBot(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "T", None, GW())
    assert asyncio.run(bot._voice("f")) == "колодки" and got["name"] == "file_0.ogg"


def test_separate_goes_to_selector_prompt(tmp_path):
    """«Сайлентблоки отдельно» — подборщику: сборку вместо детали не предлагать."""
    seen = []

    class GW(ScriptGW):
        async def chat(self, model, messages, tools=None, max_out=8000):
            names = {t["function"]["name"] for t in tools or []}
            if "decoded" in names:
                args = {"items": [{"need": "Сайлентблок переднего рычага", "axis": "front", "certainty": "sure",
                                   "words": "сайлентблоки отдельно", "separate": True}]}
                return {"tool_calls": [{"id": "1", "function": {"name": "decoded", "arguments": json.dumps(args)}}]}, {}
            if "pick" in names:
                seen.append(messages[0]["content"])
                return {"tool_calls": [{"id": "2", "function": {"name": "pick", "arguments":
                        '{"found": false, "reason": "отдельно не продаётся, только в сборе с рычагом"}'}}]}, {}
            seen.append(messages[1]["content"])
            return {"content": "ok"}, {}
    a = C.Assistant(Engine(Fake(), None, set()), GW(), "m", tmp_path)
    asyncio.run(a.reply("tg-4", VIN + " сайлентблоки отдельно"))
    r = asyncio.run(a.reply("tg-4", "", action="go"))
    assert "ИМЕННО ОТДЕЛЬНО" in seen[0] and "отдельно не продаётся, только в сборе с рычагом" in r["text"]


def test_scheme_render_marks_position():
    import io
    from PIL import Image
    from app.assistant.scheme_img import render
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, "PNG")
    jpg = render(buf.getvalue(), [{"code": "5", "x1": 100, "y1": 100, "x2": 130, "y2": 120}, {"code": "5", "x1": "bad"}])
    im = Image.open(io.BytesIO(jpg)).convert("RGB")
    assert im.size == (400, 300) and im.getpixel((115, 110))[2] < 200   # выноска залита жёлтым


def test_schemes_buttons_and_show(tmp_path):
    """Схемы прошлого подбора: кнопка «📐 …» → картинка; «покажи, где стоит» — тоже картинка, а не новый подбор."""
    import io
    from PIL import Image
    a = C.Assistant(Engine(Fake(), None, set()), ScriptGW(), "m", tmp_path / "assistant")
    url = "https://img.laximo.ru/x.png"
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), "white").save(buf, "PNG")
    f = tmp_path / "podbor" / "schemes"
    f.mkdir(parents=True)
    import hashlib
    (f / (hashlib.sha1(url.encode()).hexdigest() + ".img")).write_bytes(buf.getvalue())
    data = {"items": [{"catalog": [{"part": "Тяга рулевая (левая)", "scheme": {"catalog": "TOY", "unit_id": "1", "ssd": "s",
                                                                          "image": url, "code": "7", "unit": "Рулевое"}}]}]}
    sch = C._schemes(data)
    assert sch[0]["label"] == "Тяга рулевая" and C._scheme_buttons(sch) == [[("📐 Тяга рулевая", "sch:0")]]
    a.sessions.save("tg-9", {"ident": VIN, "schemes": sch})

    async def imagemap(method, params):
        assert method == "listImageMapByUnit"
        return [{"code": "7", "x1": 10, "y1": 10, "x2": 40, "y2": 30}, {"code": "8", "x1": 0, "y1": 0, "x2": 5, "y2": 5}]
    a.eng.src.laximo = imagemap
    r = asyncio.run(a.reply("tg-9", "", action="sch:0"))
    assert r["status"] == "scheme" and r["photos"] == [0]
    jpg, cap = asyncio.run(a.scheme("tg-9", 0))
    assert jpg[:2] == b"\xff\xd8" and "позиция 7 (отмечена)" in cap
    r = asyncio.run(a.reply("tg-9", "покажи где стоит тяга"))
    assert r["status"] == "scheme" and r["photos"] == [0]
    assert asyncio.run(a.reply("tg-9", "", action="sch:5"))["status"] == "stale"


def test_video_note_and_unknown_messages_are_not_silent(tmp_path):
    """26.10: голосовое пользователя прошло молча — «кружок»/аудиофайл бот не узнавал и ничего не отвечал."""
    sent, got = [], {}

    def handler(request: httpx.Request):
        if request.url.path.endswith("/sendMessage"):
            sent.append(json.loads(request.content))
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "video_notes/file_3"}})
        return httpx.Response(200, json={"ok": True, "result": []})

    class GW(ScriptGW):
        async def transcribe(self, audio, filename="voice.ogg"):
            got["name"] = filename
            return "колодки"
    gw = GW()
    bot = KirillBot(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "T",
                    C.Assistant(Engine(Fake(), None, set()), gw, "m", tmp_path), gw)
    chat = {"chat": {"id": 5, "type": "private"}}
    asyncio.run(bot.handle({"message": dict(chat, video_note={"file_id": "v"})}))
    assert got["name"] == "file_3.mp4" and any("🎤 «колодки»" in s["text"] for s in sent)
    asyncio.run(bot.handle({"message": dict(chat, sticker={"file_id": "s"})}))
    assert "понимаю текст и голосовые" in sent[-1]["text"]
