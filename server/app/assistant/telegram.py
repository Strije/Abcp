"""Telegram-бот «Кирилл» (@AvtoDrugAi_bot) поверх ассистента: текст и голосовые → подбор по машине клиента.

Long polling getUpdates (вебхук не нужен), как access.ButtonListener. Собеседник — «tg-<chat_id>»: машина и разговор
помнятся в сессии ассистента, реплики пишутся в журнал подбора. Токен — ASSISTANT_TG_TOKEN на сервере.
"""
import asyncio
import html
import json
import logging
import re
import time
from collections import defaultdict, deque

import httpx

from .models import ModelError, OverBudget

log = logging.getLogger("assistant.tg")
TG = "https://api.telegram.org"

HELLO = ("Здравствуйте! Я Кирилл, помощник магазина Автодруг92 🔧\n\n"
         "Пришлите VIN вашей машины (или госномер) и напишите, что нужно — как привыкли, хоть списком, хоть "
         "голосовым. Подберу детали по каталогу именно вашей машины: оригинал и аналоги с ценами и сроками.\n\n"
         "Например: «XW8ZZZ8K7AG200538 передние колодки и диски».")
PHONE_FIRST = ("Чтобы начать, нажмите «📱 Поделиться номером» внизу экрана — так менеджер сможет связаться с вами "
               "по заказу. Номер видят только сотрудники магазина.")
PHONE_KEYBOARD = {"keyboard": [[{"text": "📱 Поделиться номером", "request_contact": True}]],
                  "resize_keyboard": True, "one_time_keyboard": True}
PHOTO = "Фото пока не читаю 🙈 Напишите, пожалуйста, VIN или что нужно текстом — или запишите голосовое."
SLOW = "Подбираю по каталогу вашей машины, это займёт до минуты…"
TOO_MANY = "Слишком много сообщений подряд — подождите минуту, пожалуйста."
RATE, WINDOW = 6, 60   # сообщений от одного собеседника за минуту
MAX_LEN = 3800        # Telegram — до 4096 знаков в сообщении


def to_html(md: str) -> str:
    """**жирный** и *курсив* продавца → HTML Telegram; всё остальное экранируем."""
    t = html.escape(md)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<i>\1</i>", t)
    t = re.sub(r"^\s*[-*]\s+", "• ", t, flags=re.M)   # «- пункт» → «• пункт»: в Telegram читается легче
    return re.sub(r"^#{1,4}\s*(.+)$", r"<b>\1</b>", t, flags=re.M)


def chunks(text: str, size: int = MAX_LEN) -> list[str]:
    """Длинный ответ — частями по границам строк."""
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > size and cur:
            out.append(cur)
            cur = ""
        cur = (cur + "\n" + line) if cur else line
    return out + ([cur] if cur else [])


class KirillBot:
    def __init__(self, http: httpx.AsyncClient, token: str, assistant, gateway, managers_chat: str = "",
                 folder=None, photos: bool = False):
        self.http, self.token, self.assistant, self.gw = http, token, assistant, gateway
        self.photos_on = photos   # ASSISTANT_PHOTOS=1 — читать фото (на СТС персональные данные владельца)
        self.managers_chat = managers_chat
        self.folder = folder
        if assistant is not None and managers_chat:
            assistant.notify = self.notify   # заказы и номера клиентов — в группу менеджеров
        self.offset = 0
        self.seen: dict[str, deque] = defaultdict(deque)
        self.busy: set[str] = set()
        self.tasks: set[asyncio.Task] = set()   # ссылки на задачи сообщений, пока не отработали
        self.groups_seen: set = set()

    def _url(self, method: str) -> str:
        return f"{TG}/bot{self.token}/{method}"

    async def send(self, chat: int | str, text: str, md: bool = True, buttons: list | None = None,
                   contact: bool = False):
        parts = chunks(text)
        for n, part in enumerate(parts):
            body = {"chat_id": chat, "text": to_html(part) if md else part, "disable_web_page_preview": True}
            if md:
                body["parse_mode"] = "HTML"
            if buttons and n == len(parts) - 1:   # кнопки — под последней частью
                body["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row]
                                                            for row in buttons]}
            elif contact and n == len(parts) - 1:   # заказ: номер одной кнопкой, Telegram передаёт его сам
                body["reply_markup"] = {"keyboard": [[{"text": "📱 Поделиться номером", "request_contact": True}]],
                                        "resize_keyboard": True, "one_time_keyboard": True}
            r = await self.http.post(self._url("sendMessage"), json=body)
            if md and r.status_code == 400:   # разметка не прошла — отправим как есть, но отправим
                body.pop("parse_mode")
                body["text"] = part
                await self.http.post(self._url("sendMessage"), json=body)

    async def photos(self, chat, sid: str, idx: list[int]):
        """Схемы узлов с отмеченной деталью — картинками (рисует ассистент, отправляем как фото)."""
        for i in idx[:4]:
            got = await self.assistant.scheme(sid, i)
            if not got:
                await self.send(chat, "Схему сейчас не получить — каталог не отдал картинку.", md=False)
                continue
            jpg, cap = got
            await self.http.post(self._url("sendPhoto"), data={"chat_id": str(chat), "caption": cap},
                                 files={"photo": ("scheme.jpg", jpg, "image/jpeg")})

    async def notify(self, text: str):
        await self.http.post(self._url("sendMessage"), json={"chat_id": self.managers_chat, "text": text,
                                                             "disable_web_page_preview": True})

    def _group(self, upd: dict):
        """Кирилла добавили в группу — запомнить id и кто добавил. Сами группе не доверяем: заказы с данными клиентов
        уходят только в ASSISTANT_MANAGERS_CHAT, который ставим вручную (иначе любой, добавивший бота к себе,
        получал бы чужие заказы)."""
        chat, who = upd.get("chat") or {}, upd.get("from") or {}
        status = (upd.get("new_chat_member") or {}).get("status")
        rec = {"id": chat.get("id"), "title": chat.get("title"), "status": status, "by": who.get("id"),
               "by_name": who.get("username") or who.get("first_name"), "t": time.time()}
        log.warning("kirill: группа %s «%s» — %s (добавил %s)", rec["id"], rec["title"], status, rec["by"])
        if self.folder is not None:
            try:
                f = self.folder / "groups.jsonl"
                f.parent.mkdir(parents=True, exist_ok=True)
                with open(f, "a", encoding="utf-8") as out:
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except OSError:
                pass

    async def press(self, cb: dict):
        """Кнопки: предпочтение на этот подбор, «Подобрать» (go) или «📐 деталь» — схема (sch:N)."""
        msg = cb.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        if not chat:
            return
        sid = f"tg-{chat}"
        data = str(cb.get("data") or "")
        res = await self._with_typing(chat, sid, lambda: self.assistant.reply(sid, "", action=data))
        note = res.get("ack") or ("Подбираю…" if res.get("text") else "Этот вопрос уже неактуален")
        await self.http.post(self._url("answerCallbackQuery"), json={"callback_query_id": cb.get("id"), "text": note[:190]})
        if res.get("text"):
            await self.send(chat, res["text"], buttons=res.get("buttons"))
        await self.photos(chat, sid, res.get("photos") or [])

    async def _with_typing(self, chat, sid: str, work):
        stop = asyncio.Event()
        typing = asyncio.create_task(self._typing(chat, stop))
        self.busy.add(sid)
        slow = asyncio.get_running_loop().call_later(12, lambda: asyncio.ensure_future(self.send(chat, SLOW, md=False)))
        try:
            return await work()
        finally:
            slow.cancel()
            self.busy.discard(sid)
            stop.set()
            typing.cancel()

    async def _typing(self, chat, stop: asyncio.Event):
        while not stop.is_set():
            try:
                await self.http.post(self._url("sendChatAction"), json={"chat_id": chat, "action": "typing"})
            except httpx.HTTPError:
                pass
            try:
                await asyncio.wait_for(stop.wait(), 4.5)
            except asyncio.TimeoutError:
                pass

    def _allowed(self, sid: str) -> bool:
        q, now = self.seen[sid], time.time()
        while q and q[0] < now - WINDOW:
            q.popleft()
        if len(q) >= RATE:
            return False
        q.append(now)
        return True

    async def _voice(self, file_id: str) -> str:
        r = await self.http.get(self._url("getFile"), params={"file_id": file_id})
        path = (r.json().get("result") or {}).get("file_path")
        if not path:
            return ""
        audio = await self.http.get(f"{TG}/file/bot{self.token}/{path}")
        # Telegram отдаёт голосовые как «file_0.oga», распознавание принимает тот же OGG/Opus только как «.ogg»
        # (26.10: «Unsupported file format oga»)
        name = re.sub(r"\.oga$", ".ogg", path.rsplit("/", 1)[-1])
        if "." not in name:   # «кружок» и файлы без расширения — Telegram отдаёт MP4
            name += ".mp4"
        return await self.gw.transcribe(audio.content, name)

    async def handle(self, update: dict):
        if update.get("my_chat_member"):
            if ((update["my_chat_member"].get("chat") or {}).get("type")) in ("group", "supergroup"):
                self._group(update["my_chat_member"])
            return
        if update.get("callback_query"):
            cb = update["callback_query"]
            if ((cb.get("message") or {}).get("chat") or {}).get("type") == "private":
                await self.press(cb)
            return
        m = update.get("message") or {}
        chat = (m.get("chat") or {}).get("id")
        if not chat or (m.get("chat") or {}).get("type") != "private":
            if (m.get("chat") or {}).get("type") in ("group", "supergroup") and chat not in self.groups_seen:
                # Бота добавили раньше, чем он научился это замечать: номер группы — по первому сообщению в ней
                self.groups_seen.add(chat)
                self._group({"chat": m["chat"], "from": m.get("from") or {}, "new_chat_member": {"status": "message"}})
            return   # только личные чаты: в группах бот молчит
        sid = f"tg-{chat}"
        text = str(m.get("text") or m.get("caption") or "").strip()
        contact = m.get("contact") or {}
        if contact.get("phone_number"):
            if contact.get("user_id") != (m.get("from") or {}).get("id"):
                await self.send(chat, "Нужен ваш собственный номер — нажмите «📱 Поделиться номером».", md=False)
                return
            name = " ".join(x for x in (contact.get("first_name"), contact.get("last_name")) if x)
            u = (m.get("from") or {}).get("username")
            reply, pending = await self.assistant.contact(sid, "+" + contact["phone_number"].lstrip("+"),
                                                          name + (f" (@{u})" if u else ""))
            await self.http.post(self._url("sendMessage"), json={"chat_id": chat, "text": reply,
                                                                 "reply_markup": {"remove_keyboard": True}})
            for t in pending:   # что клиент написал до номера — отвечаем теперь
                res = await self._with_typing(chat, sid, lambda t=t: self.assistant.reply(sid, t))
                await self.send(chat, res["text"], buttons=res.get("buttons"))
                await self.photos(chat, sid, res.get("photos") or [])
            return
        if text.startswith("/start"):
            await self.send(chat, HELLO, md=False)
            if not self.assistant.has_phone(sid):
                await self.http.post(self._url("sendMessage"), json={"chat_id": chat, "text": PHONE_FIRST,
                                                                     "reply_markup": PHONE_KEYBOARD})
            return
        if not self.assistant.has_phone(sid):
            # Номер — первым шагом (пользователь 26.10: «без него не нужно начинать»); просьбу не теряем
            if text:
                self.assistant.keep(sid, text)
            await self.http.post(self._url("sendMessage"), json={"chat_id": chat, "text": PHONE_FIRST,
                                                                 "reply_markup": PHONE_KEYBOARD})
            return
        if not self._allowed(sid):
            await self.send(chat, TOO_MANY, md=False)
            return
        if sid in self.busy:
            await self.send(chat, "Ещё подбираю по прошлому сообщению — секунду, отвечу и на это.", md=False)
        # Голос — и голосовое, и «кружок», и аудиофайл (26.10: голосовое пользователя прошло молча — тип не узнали)
        doc = m.get("document") or {}
        voice = m.get("voice") or m.get("audio") or m.get("video_note") or \
            (doc if str(doc.get("mime_type") or "").startswith(("audio/", "video/")) else None)
        kind = "voice" if voice else "photo" if m.get("photo") else "text" if text else \
            next((k for k in ("sticker", "video", "document", "location", "contact") if m.get(k)), "other")
        log.info("kirill: %s %s", sid, kind)   # без содержимого — только что пришло
        if voice:
            try:
                heard = await self._with_typing(chat, sid, lambda: self._voice(voice["file_id"]))
            except (ModelError, OverBudget, httpx.HTTPError, KeyError, ValueError) as e:
                log.info("voice failed: %s", e)
                heard = ""
            if not heard:
                await self.send(chat, "Не разобрал голосовое 😕 Напишите, пожалуйста, текстом.", md=False)
                return
            await self.send(chat, f"🎤 «{heard}»", md=False)
            text = heard
        elif m.get("photo") and self.photos_on:
            # СТС, табличка VIN, деталь — читает модель; фото не храним
            try:
                big = max(m["photo"], key=lambda p: p.get("file_size") or p.get("width", 0))
                r = await self.http.get(self._url("getFile"), params={"file_id": big["file_id"]})
                path = (r.json().get("result") or {}).get("file_path")
                img = (await self.http.get(f"{TG}/file/bot{self.token}/{path}")).content if path else b""
                said, note = await self._with_typing(chat, sid, lambda: self.assistant.photo_text(img, text))
            except (ModelError, OverBudget, httpx.HTTPError, KeyError, ValueError) as e:
                log.info("photo failed: %s", e)
                said, note = "", "📷 Не получилось прочитать фото — напишите, пожалуйста, текстом."
            await self.send(chat, note, md=False)
            if not said:
                return
            text = said
        elif m.get("photo") and not text:
            await self.send(chat, PHOTO, md=False)
            return
        if not text:
            # Стикер, видео, геометка… — не молчим: клиент не поймёт, дошло ли
            await self.send(chat, "Я пока понимаю текст и голосовые 🙂 Напишите или запишите, что нужно.", md=False)
            return
        res = await self._with_typing(chat, sid, lambda: self.assistant.reply(sid, text))
        await self.send(chat, res["text"], buttons=res.get("buttons"), contact=bool(res.get("contact")))
        await self.photos(chat, sid, res.get("photos") or [])

    async def poll_once(self, timeout: int = 25):
        r = await self.http.get(self._url("getUpdates"), params={
            "offset": self.offset, "timeout": timeout, "allowed_updates": '["message","callback_query","my_chat_member"]'}, timeout=timeout + 10)
        updates = r.json().get("result", [])
        for u in updates:
            self.offset = u["update_id"] + 1
            # Каждое сообщение — своей задачей: подбор идёт до минуты, другие собеседники не ждут
            t = asyncio.create_task(self._safe(u))
            self.tasks.add(t)
            t.add_done_callback(self.tasks.discard)
        if not updates:
            await asyncio.sleep(1)

    async def _safe(self, u: dict):
        try:
            await self.handle(u)
        except Exception as e:  # noqa: BLE001 — один сбой не роняет бота
            log.warning("kirill update failed: %s: %s", type(e).__name__, str(e)[:200])

    async def run(self):
        while True:
            try:
                await self.poll_once()
            except Exception as e:  # noqa: BLE001 — сеть/Telegram — подождём и снова
                log.info("kirill poll error: %s", type(e).__name__)
                await asyncio.sleep(10)
