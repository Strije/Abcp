"""Очередь заявок на доступ к API ABCP.

Новым клиентам ABCP включает права на API сам, а существующим менеджер ставит галочки в карточке
клиента вручную. Приложение после входа проверяет доступ пробными запросами; если прав нет —
клиент отправляет заявку, сервер пишет менеджерам в Telegram (из приложения Telegram в РФ недоступен).
Когда проверка в приложении проходит, заявка закрывается.

Персональных данных здесь нет ни на диске, ни в Telegram: только ID клиента в ABCP —
менеджер находит карточку по нему в панели ABCP.
"""
import json
import os
import tempfile
import time
from pathlib import Path

import asyncio
import logging

import httpx

# С сервера в РФ Telegram может быть недоступен — тогда адрес API через свой прокси (TELEGRAM_API в env)
TG_API = os.environ.get("TELEGRAM_API", "https://api.telegram.org").rstrip("/")

log = logging.getLogger("avtodrug")

REPEAT_NOTIFY = 12 * 3600  # повторная заявка того же клиента — напоминание не чаще раза в 12 часов

FEATURES = {
    "brands": "искать бренд по артикулу",
    "articles": "искать товар по артикулу и бренду",
    "basket": "корзина (добавлять, получать список)",
    "order": "отправлять корзину в заказ",
    "orders": "информация об оформленных заказах",
    "history": "история поиска",
}


class AccessQueue:
    def __init__(self, folder: Path):
        self.file = folder / "access_requests.json"

    def load(self) -> dict:
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def _save(self, data: dict):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.file.parent, suffix=".part")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.file)

    def add(self, uid: str, missing: list[str], now: float | None = None) -> tuple[dict, bool]:
        """Возвращает заявку и признак «надо написать менеджерам»."""
        now = now or time.time()
        data = self.load()
        old = data.get(uid)
        notify = not old or now - old.get("notifiedAt", 0) >= REPEAT_NOTIFY
        req = {
            **(old or {"createdAt": now}),
            "missing": missing,
            "updatedAt": now,
        }
        data[uid] = req
        self._save(data)
        return req, notify

    def mark_notified(self, uid: str, now: float | None = None):
        data = self.load()
        if uid in data:
            data[uid]["notifiedAt"] = now or time.time()
            self._save(data)

    def unsent(self) -> list[str]:
        """Заявки, которые менеджерам так и не ушли (бот не был настроен, Telegram недоступен)."""
        return [uid for uid, r in self.load().items() if "notifiedAt" not in r]

    def get(self, uid: str) -> dict | None:
        return self.load().get(uid)

    def close(self, uid: str) -> dict | None:
        data = self.load()
        req = data.pop(uid, None)
        if req is not None:
            self._save(data)
        return req


def request_text(uid: str, req: dict, repeat: bool) -> str:
    missing = ", ".join(FEATURES.get(m, m) for m in req.get("missing", [])) or "не уточнено"
    lines = [
        ("🔁 Повторная заявка" if repeat else "🔑 Заявка на доступ из приложения"),
        f"ID клиента в ABCP: {uid}",
        f"Не хватает прав: {missing}",
        "",
        "Панель ABCP → клиенты → найти по ID → доступ к API → включить поиск, корзину, заказы.",
        "Потом нажмите кнопку ниже — клиенту придёт уведомление.",
    ]
    return "\n".join(lines)


GRANTED = "granted:"


def granted_button(uid: str) -> dict:
    return {"inline_keyboard": [[{"text": "✅ Доступ включён", "callback_data": GRANTED + uid}]]}


async def telegram(http: httpx.AsyncClient, token: str, chat_id: str, text: str, markup: dict | None = None) -> bool:
    if not token or not chat_id:
        return False
    body = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if markup:
        body["reply_markup"] = markup
    try:
        r = await http.post(f"{TG_API}/bot{token}/sendMessage", json=body)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


class ButtonListener:
    """Ждёт нажатий «✅ Доступ включён» в чате менеджеров (long polling getUpdates, вебхук не нужен).

    Принимает кнопки только из настроенного чата: закрывает заявку и вызывает on_granted(uid) — push клиенту.
    """

    def __init__(self, http: httpx.AsyncClient, token: str, chat_id: str, on_granted, on_command=None, on_callback=None):
        """on_command(text) -> (ответ, кнопки|None) — команды из чата (/push, /stats…);
        on_callback(data) -> текст для отметки в сообщении, или None — чужая кнопка."""
        self.http, self.token, self.chat_id, self.on_granted = http, token, str(chat_id), on_granted
        self.on_command, self.on_callback = on_command, on_callback
        self.offset = 0

    def _url(self, method: str) -> str:
        return f"{TG_API}/bot{self.token}/{method}"

    async def send(self, text: str, markup: dict | None = None):
        body = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
        if markup:
            body["reply_markup"] = markup
        await self.http.post(self._url("sendMessage"), json=body)

    async def handle(self, update: dict):
        # Команды — только из нашего чата: чужой, нашедший бота, ничего разослать не сможет
        message = update.get("message") or {}
        if message:
            text = str(message.get("text") or "")
            if str((message.get("chat") or {}).get("id")) == self.chat_id and text.startswith("/") and self.on_command:
                reply = await self.on_command(text)
                if reply:
                    await self.send(*reply)
            return
        cb = update.get("callback_query") or {}
        data = str(cb.get("data") or "")
        msg = cb.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != self.chat_id:
            return
        if not data.startswith(GRANTED):
            note = await self.on_callback(data) if self.on_callback else None
            if note:
                await self.http.post(self._url("answerCallbackQuery"), json={"callback_query_id": cb.get("id"), "text": note[:190]})
                await self.http.post(self._url("editMessageText"), json={
                    "chat_id": msg["chat"]["id"], "message_id": msg.get("message_id"),
                    "text": f"{msg.get('text', '')}\n\n{note}",
                })
            return
        uid = data[len(GRANTED):]
        who = (cb.get("from") or {}).get("first_name") or "менеджер"
        delivered = await self.on_granted(uid)
        log.info("access granted by button: %s delivered=%s", uid, delivered)
        note = "клиенту отправлено уведомление" if delivered else "уведомление не доставлено (нет push) — клиент увидит при входе"
        await self.http.post(self._url("answerCallbackQuery"), json={"callback_query_id": cb.get("id"), "text": note})
        await self.http.post(self._url("editMessageText"), json={
            "chat_id": msg["chat"]["id"], "message_id": msg.get("message_id"),
            "text": f"{msg.get('text', '')}\n\n✅ Включено ({who}): {note}",
        })

    async def poll_once(self, timeout: int = 25):
        r = await self.http.get(self._url("getUpdates"), params={
            "offset": self.offset, "timeout": timeout, "allowed_updates": '["callback_query","message"]'},
            timeout=timeout + 10)
        updates = r.json().get("result", [])
        for u in updates:
            self.offset = u["update_id"] + 1
            await self.handle(u)
        if not updates:
            await asyncio.sleep(1)  # на случай, если Telegram ответил сразу, а не через timeout

    async def run(self):
        while True:
            try:
                await self.poll_once()
            except Exception as e:  # сеть/Telegram — подождём и снова
                log.info("telegram poll error: %s", type(e).__name__)
                await asyncio.sleep(10)
