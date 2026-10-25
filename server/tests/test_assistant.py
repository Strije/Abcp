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
    assert Q.tier("Mann-Filter")[0] == "OE" and Q.tier("Febest")[0] == "good"
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
    assert r["status"] == "ok" and "Колодки" in r["text"]
    assert gw.calls[-1].startswith("sell:Качество; срочно")
    # Следующая просьба — вопросы снова (предпочтения не запоминаем, решение пользователя 26.10)
    r = asyncio.run(a.reply("tg-1", "а задние колодки?"))
    assert r["status"] == "ask"
    assert asyncio.run(a.reply("tg-1", "", action="go"))["status"] == "ok"
    assert asyncio.run(a.reply("tg-1", "", action="go"))["status"] == "stale"   # кнопка от старого вопроса


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
