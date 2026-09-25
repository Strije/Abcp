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
import re
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


# Офисы самовывоза ABCP → адрес (как в приложении, StoreInfo.kt)
OFFICES = {"27993": "ул. Хрусталёва, 111", "60602": "пр. Октябрьской Революции, 20"}


def pickup_hint(now: datetime | None = None) -> str:
    """«сегодня до 19:00» / «завтра с 9:00» — часы магазинов: Пн–Пт 9–19, Сб–Вс 9–17."""
    now = now or datetime.now(MSK)
    close = 19 if now.weekday() < 5 else 17
    if now.hour < 9:
        return "сегодня с 9:00"
    if now.hour < close:
        return f"сегодня до {close}:00"
    return "завтра с 9:00"


def special_push(number: str, items: list, order: dict, now: datetime | None = None) -> dict | None:
    """Заметные статусы — своим текстом: готово к выдаче (с адресом и часами), ждёт оплаты, задерживается."""
    statuses = [s.lower() for _, s in items]
    if any("готов" in s and "выдач" in s for s in statuses):
        address = OFFICES.get(str(order.get("deliveryOfficeId") or "")) or (order.get("deliveryOffice") or "")
        part = "" if all("готов" in s for s in statuses) else "Часть заказа — "
        where = f"{address} · {pickup_hint(now)}" if address else pickup_hint(now).capitalize()
        return {"kind": "ready", "title": f"Заказ № {number} готов к выдаче 🎉",
                "body": f"{part}можно забирать: {where}", "address": address}
    if any("ожидает оплаты" in s for s in statuses):
        return {"kind": "pay", "title": f"Заказ № {number} ждёт оплаты",
                "body": "Оплатите в приложении — откройте заказ и нажмите «Оплатить»."}
    if any("задерж" in s for s in statuses):
        return {"kind": "delay", "title": f"Поставка по заказу № {number} задерживается",
                "body": "Менеджер свяжется с вами и подскажет новый срок."}
    return None


# Тип push → канал уведомлений в приложении (каналы приложение создаёт при запуске)
NOTIFY_CHANNELS = {"promo": "promo", "chat": "chat", "access_granted": "order_status"}
OPEN_CHAT_ACTION = "ru.avtodrug92.OPEN_CHAT"  # intent-filter у ChatActivity


def notification_for(data: dict) -> dict | None:
    channel = NOTIFY_CHANNELS.get(data.get("type", ""))
    if not channel:
        return None
    n = {"title": data.get("title") or "Автодруг92", "body": data.get("body") or "", "channel_id": channel}
    if data.get("type") == "chat":
        n["click_action"] = OPEN_CHAT_ACTION
    return n


class RuStorePush:
    def __init__(self, http: httpx.AsyncClient, host: str, project_id: str, service_token: str):
        self.http, self.host, self.project, self.token = http, host.rstrip("/"), project_id, service_token

    @property
    def enabled(self) -> bool:
        return bool(self.project and self.token)

    async def send(self, device_token: str, data: dict[str, str]) -> int:
        """data — для приложения (лента, кнопки). Рассылкам, чату и «доступ включён» добавляем готовое
        уведомление: его показывает сам RuStore, не дожидаясь, пока система разбудит приложение.
        Статусы заказов — только data: там у приложения свои кнопки («Маршрут», «Оплатить»). Возвращает HTTP-код."""
        android: dict = {"ttl": "86400s"}
        notice = notification_for(data)
        if notice:
            android["notification"] = notice
        body = {"message": {"token": device_token, "data": data, "android": android}}
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
        by_number = {str(o.get("number")): o for o in orders}
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
                special = special_push(number, items, by_number.get(number, {}))
                if special:
                    data.update(special)
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


# ---------- адресные рассылки из бота: /pushto ----------

def split_recipients(arg: str) -> tuple[str, str]:
    """«9497384, +7 978 123-45-67 Текст» → («9497384,+79781234567», «Текст»). Можно явно через двоеточие."""
    head, sep, tail = arg.partition(":")
    if sep and re.fullmatch(r"[\d+()\-,\s]+", head):
        return re.sub(r"\s", "", head), tail.strip()
    words = arg.split()
    i = 0
    while i < len(words) and re.fullmatch(r"[\d+()\-,]+", words[i]):
        i += 1
    return "".join(words[:i]), " ".join(words[i:])


def phone_digits(s: str) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 10 else ""


def match_recipients(who: str, mobiles: dict[str, str]) -> tuple[set[str], list[str]]:
    """Кого нашли среди клиентов с push (ID ABCP или телефон) и кого нет."""
    found, missing = set(), []
    for key in filter(None, who.split(",")):
        d = re.sub(r"\D", "", key)
        hit = {d} & set(mobiles) or {u for u, m in mobiles.items() if len(d) >= 10 and phone_digits(m) == phone_digits(d)}
        if hit:
            found |= hit
        else:
            missing.append(key)
    return found, missing
