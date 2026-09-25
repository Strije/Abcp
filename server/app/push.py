"""Push-уведомления о статусах заказов через RuStore.

Приложение присылает push-токен устройства → храним «ID клиента → токены».
Раз в несколько минут смотрим заказы, изменённые в ABCP (cp/orders по dateUpdated), и если у позиции
клиента с токеном сменился статус — шлём push. Имён и телефонов не храним: только ID клиента,
токены устройств и статусы позиций.
"""
import asyncio
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("avtodrug")
MSK = ZoneInfo("Europe/Moscow")  # время ABCP — московское (Севастополь тоже)
KEEP_POSITIONS = 120 * 24 * 3600  # статусы позиций старше 4 месяцев забываем


class JsonFile:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def save(self, data: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".part")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self.path)


class TokenStore:
    """ID клиента → {токен: когда обновлён}. Один токен — одно устройство — один клиент."""

    def __init__(self, folder: Path):
        self.f = JsonFile(folder / "push_tokens.json")

    def add(self, uid: str, token: str):
        data = self.f.load()
        for other in list(data):  # на устройстве сменили аккаунт — токен переезжает к новому клиенту
            data[other].pop(token, None)
            if not data[other]:
                del data[other]
        data.setdefault(uid, {})[token] = time.time()
        self.f.save(data)

    def remove(self, token: str):
        data = self.f.load()
        changed = False
        for uid in list(data):
            if data[uid].pop(token, None) is not None:
                changed = True
            if not data[uid]:
                del data[uid]
        if changed:
            self.f.save(data)

    def tokens(self, uid: str) -> list[str]:
        return list(self.f.load().get(uid, {}))

    def uids(self) -> set[str]:
        return set(self.f.load())


def position_key(p: dict) -> str | None:
    pid = p.get("id")
    return str(pid) if pid not in (None, "") else None


def position_title(p: dict) -> str:
    t = f"{p.get('brand') or ''} {p.get('number') or ''}".strip()
    return t or (p.get("description") or "Позиция")


def diff_orders(orders: list[dict], known: dict, subscribed: set[str], now: float) -> dict[str, dict[str, list]]:
    """Сверяет свежие заказы со статусами, что мы видели. Возвращает {uid: {номер заказа: [(название, статус)]}}.

    Новую для нас позицию только запоминаем — чтобы после запуска не прислать уведомления по старым заказам.
    """
    changes: dict[str, dict[str, list]] = {}
    for o in orders:
        uid = str(o.get("userId") or "")
        if uid not in subscribed:
            continue
        number = str(o.get("number") or "")
        for p in o.get("positions") or []:
            if not isinstance(p, dict) or str(p.get("isDelete")) in ("1", "true", "True"):
                continue
            key = position_key(p)
            status = str(p.get("status") or "").strip()
            if not key or not status:
                continue
            old = known.get(key)
            if old and old.get("s") != status:
                changes.setdefault(uid, {}).setdefault(number, []).append((position_title(p), status))
            known[key] = {"s": status, "t": now}
    return changes


def push_text(number: str, items: list) -> tuple[str, str]:
    if len(items) == 1:
        title, status = items[0]
        return f"Заказ № {number}: {status}", title
    statuses = {s for _, s in items}
    head = f"Заказ № {number}: {next(iter(statuses))}" if len(statuses) == 1 else f"Заказ № {number}: изменились статусы"
    return head, "\n".join(f"{t} — {s}" for t, s in items)


class RuStorePush:
    def __init__(self, http: httpx.AsyncClient, host: str, project_id: str, service_token: str):
        self.http, self.host, self.project, self.token = http, host.rstrip("/"), project_id, service_token

    @property
    def enabled(self) -> bool:
        return bool(self.project and self.token)

    async def send(self, device_token: str, data: dict[str, str]) -> int:
        """Только data — уведомление рисует само приложение (и кладёт в ленту). Возвращает HTTP-код."""
        body = {"message": {"token": device_token, "data": data, "android": {"ttl": "86400s"}}}
        try:
            r = await self.http.post(f"{self.host}/v1/projects/{self.project}/messages:send",
                                     json=body, headers={"Authorization": f"Bearer {self.token}"})
        except httpx.HTTPError:
            return 0
        if r.status_code >= 400:
            log.info("rustore push -> %s %s", r.status_code, r.text[:200])
        return r.status_code


class OrderWatcher:
    """Фоновое слежение за статусами: cp/orders с dateUpdatedStart от прошлой проверки."""

    def __init__(self, abcp, pusher: RuStorePush, tokens: TokenStore, folder: Path, interval: int = 180):
        self.abcp, self.pusher, self.tokens, self.interval = abcp, pusher, tokens, interval
        self.state = JsonFile(folder / "order_watch.json")

    async def seed(self, uid: str):
        """Новый подписчик: запоминаем текущие статусы его заказов, чтобы дальше ловить только изменения."""
        since = (datetime.now(MSK) - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        orders = await self.abcp.orders_updated(since, user_id=uid)
        st = self.state.load()
        diff_orders(orders, st.setdefault("known", {}), {uid}, time.time())
        self.state.save(st)

    async def tick(self) -> int:
        subscribed = self.tokens.uids()
        if not subscribed or not self.pusher.enabled:
            return 0
        st = self.state.load()
        now_msk = datetime.now(MSK)
        # С запасом в 2 минуты — чтобы не потерять заказ, обновлённый на стыке проверок
        since = st.get("since") or (now_msk - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        since_dt = datetime.strptime(since, "%Y-%m-%d %H:%M:%S") - timedelta(minutes=2)
        orders = await self.abcp.orders_updated(since_dt.strftime("%Y-%m-%d %H:%M:%S"))
        now = time.time()
        known = st.setdefault("known", {})
        changes = diff_orders(orders, known, subscribed, now)
        st["since"] = now_msk.strftime("%Y-%m-%d %H:%M:%S")
        st["known"] = {k: v for k, v in known.items() if now - v.get("t", now) < KEEP_POSITIONS}
        self.state.save(st)

        sent = 0
        for uid, by_order in changes.items():
            for number, items in by_order.items():
                title, text = push_text(number, items)
                # items — для ленты уведомлений в приложении: [[позиция, статус], …]
                data = {"type": "order_status", "order": number, "title": title, "body": text,
                        "items": json.dumps([list(i) for i in items], ensure_ascii=False)}
                for t in self.tokens.tokens(uid):
                    code = await self.pusher.send(t, data)
                    if code in (400, 404):  # токен устарел (приложение удалено) — забываем
                        self.tokens.remove(t)
                    elif code == 200:
                        sent += 1
        return sent

    async def run(self):
        while True:
            try:
                n = await self.tick()
                if n:
                    log.info("order watch: sent %s push", n)
            except Exception as e:  # сеть/ABCP — попробуем в следующий раз
                log.info("order watch error: %s", type(e).__name__)
            await asyncio.sleep(self.interval)
