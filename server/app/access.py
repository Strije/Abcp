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
        r = await http.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


class ButtonListener:
    """Ждёт нажатий «✅ Доступ включён» в чате менеджеров (long polling getUpdates, вебхук не нужен).

    Принимает кнопки только из настроенного чата: закрывает заявку и вызывает on_granted(uid) — push клиенту.
    """

    def __init__(self, http: httpx.AsyncClient, token: str, chat_id: str, on_granted):
        self.http, self.token, self.chat_id, self.on_granted = http, token, str(chat_id), on_granted
        self.offset = 0

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    async def handle(self, update: dict):
        cb = update.get("callback_query") or {}
        data = str(cb.get("data") or "")
        msg = cb.get("message") or {}
        if not data.startswith(GRANTED) or str((msg.get("chat") or {}).get("id")) != self.chat_id:
            return
        uid = data[len(GRANTED):]
        who = (cb.get("from") or {}).get("first_name") or "менеджер"
        delivered = await self.on_granted(uid)
        note = "клиенту отправлено уведомление" if delivered else "уведомление не доставлено (нет push) — клиент увидит при входе"
        await self.http.post(self._url("answerCallbackQuery"), json={"callback_query_id": cb.get("id"), "text": note})
        await self.http.post(self._url("editMessageText"), json={
            "chat_id": msg["chat"]["id"], "message_id": msg.get("message_id"),
            "text": f"{msg.get('text', '')}\n\n✅ Включено ({who}): {note}",
        })

    async def poll_once(self, timeout: int = 25):
        r = await self.http.get(self._url("getUpdates"), params={
            "offset": self.offset, "timeout": timeout, "allowed_updates": '["callback_query"]'},
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
