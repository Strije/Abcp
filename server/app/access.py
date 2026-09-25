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

import httpx

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
        if notify:
            req["notifiedAt"] = now
        data[uid] = req
        self._save(data)
        return req, notify

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
    ]
    return "\n".join(lines)


async def telegram(http: httpx.AsyncClient, token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    try:
        r = await http.post(f"https://api.telegram.org/bot{token}/sendMessage",
                            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
        return r.status_code == 200
    except httpx.HTTPError:
        return False
