"""Чат приложения как собственный канал открытой линии Битрикс24 (коннектор imconnector).

Клиент пишет в приложении → сервер передаёт сообщение в открытую линию (imconnector.send.messages).
Оператор отвечает в Битрикс24 → событие OnImConnectorMessageAdd на наш сервер → сохраняем и шлём push.
Внешний «чат» в терминах коннектора — ID клиента ABCP: один клиент — один диалог.

Установка: локальное серверное приложение Битрикс24 → POST /v1/bitrix/install (токены) → регистрируем
коннектор и подписываемся на событие; администратор включает канал в настройках линии →
Битрикс открывает PLACEMENT_HANDLER → активируем коннектор на этой линии.
"""
import json
import logging
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl

import httpx

log = logging.getLogger("avtodrug")

CONNECTOR = "avtodrug_app"
CONNECTOR_NAME = "Приложение Автодруг"
KEEP_DAYS = 180  # переписку на нашем сервере храним полгода (полная история — в Битрикс24)

# Иконка канала в настройках открытых линий: машинка в круге
ICON_SVG = ("data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20"
            "viewBox%3D%220%200%2070%2071%22%3E%3Cpath%20fill%3D%22%23FFF%22%20d%3D%22M18%2044h34l-4-13H22z%22%2F%3E"
            "%3Ccircle%20cx%3D%2225%22%20cy%3D%2248%22%20r%3D%224%22%20fill%3D%22%23FFF%22%2F%3E%3Ccircle%20cx%3D%2245%22"
            "%20cy%3D%2248%22%20r%3D%224%22%20fill%3D%22%23FFF%22%2F%3E%3C%2Fsvg%3E")


def parse_form(body: bytes) -> dict:
    """Битрикс шлёт события формой с вложенными ключами: data[MESSAGES][0][message][text]=… → вложенный dict."""
    out: dict = {}
    for key, value in parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True):
        parts = re.findall(r"[^\[\]]+", key)
        cur = out
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        if parts:
            cur[parts[-1]] = value
    return out


def as_list(x) -> list:
    """{"0": {...}, "1": {...}} или список → список."""
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        return [x[k] for k in sorted(x, key=lambda k: int(k) if str(k).isdigit() else 0)]
    return []


def clean_bb(text: str) -> tuple[str, str]:
    """«[b]Светлана:[/b] [br]Добрый день!» → ("Светлана", "Добрый день!")."""
    who = ""
    m = re.match(r"\s*\[b\](.+?):?\[/b\]\s*(\[br\])?", text or "")
    if m:
        who = m.group(1).strip().rstrip(":")
        text = text[m.end():]
    text = re.sub(r"\[br\]", "\n", text or "", flags=re.I)
    text = re.sub(r"\[/?(b|i|u|s|quote|code|size[^\]]*|color[^\]]*)\]", "", text, flags=re.I)
    text = re.sub(r"\[url=([^\]]+)\](.*?)\[/url\]", r"\2 (\1)", text, flags=re.I)
    return who, text.strip()


class BitrixState:
    """Токены приложения и выбранная линия — /var/lib/avtodrug-api/bitrix.json (права 600)."""

    def __init__(self, folder: Path):
        self.file = folder / "bitrix.json"

    def load(self) -> dict:
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def save(self, data: dict):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.file.parent, suffix=".part")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.file)

    def update(self, **kw):
        d = self.load()
        d.update(kw)
        self.save(d)
        return d


class ChatStore:
    """Переписка клиентов: SQLite, одна таблица. dir: in — от клиента, out — от оператора."""

    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / "chat.sqlite"
        with self._db() as db:
            db.execute("""create table if not exists messages(
                id integer primary key autoincrement, uid text not null, dir text not null,
                text text not null, author text, ts real not null, bx_chat_id text, bx_message_id text)""")
            db.execute("create index if not exists m_uid on messages(uid, id)")

    def _db(self):
        return sqlite3.connect(self.path)

    def add(self, uid: str, dir_: str, text: str, author: str = "", bx_chat_id: str = "", bx_message_id: str = "") -> int:
        with self._db() as db:
            if bx_message_id and db.execute("select 1 from messages where bx_message_id=?", (bx_message_id,)).fetchone():
                return 0  # Битрикс повторил событие — второй раз не сохраняем и не шлём push
            cur = db.execute(
                "insert into messages(uid,dir,text,author,ts,bx_chat_id,bx_message_id) values(?,?,?,?,?,?,?)",
                (uid, dir_, text, author, time.time(), bx_chat_id, bx_message_id))
            db.execute("delete from messages where ts < ?", (time.time() - KEEP_DAYS * 86400,))
            return cur.lastrowid

    def since(self, uid: str, after: int = 0, limit: int = 200) -> list[dict]:
        with self._db() as db:
            rows = db.execute("select id,dir,text,author,ts from messages where uid=? and id>? order by id limit ?",
                              (uid, after, limit)).fetchall()
        return [{"id": r[0], "dir": r[1], "text": r[2], "author": r[3] or "", "ts": int(r[4])} for r in rows]


class Bitrix:
    def __init__(self, http: httpx.AsyncClient, state: BitrixState, client_id: str, client_secret: str, public_url: str):
        self.http, self.state = http, state
        self.client_id, self.client_secret = client_id, client_secret
        self.public_url = public_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def ready(self) -> bool:
        s = self.state.load()
        return bool(s.get("access_token") and s.get("line"))

    def save_auth(self, auth: dict):
        """Токены из установки / события. application_token запоминаем при установке — им проверяем события."""
        s = self.state.load()
        upd = {k: auth[k] for k in ("access_token", "refresh_token", "client_endpoint", "server_endpoint", "domain", "member_id")
               if auth.get(k)}
        if auth.get("expires_in"):
            upd["expires"] = time.time() + int(auth["expires_in"]) - 60
        s.update(upd)
        self.state.save(s)

    async def refresh(self) -> str:
        s = self.state.load()
        server = (s.get("server_endpoint") or "https://oauth.bitrix.info/rest/").rstrip("/")
        url = server[:-5] + "/oauth/token/" if server.endswith("/rest") else server + "/oauth/token/"
        r = await self.http.get(url, params={"grant_type": "refresh_token", "client_id": self.client_id,
                                             "client_secret": self.client_secret, "refresh_token": s.get("refresh_token", "")})
        d = r.json()
        if not d.get("access_token"):
            raise RuntimeError(f"Битрикс24 не обновил токен: {d.get('error_description') or d.get('error')}")
        self.save_auth(d)
        return d["access_token"]

    async def call(self, method: str, params: dict) -> dict:
        s = self.state.load()
        token = s.get("access_token", "")
        if not token or time.time() > s.get("expires", 0):
            token = await self.refresh()
        base = s.get("client_endpoint") or f"https://{s.get('domain')}/rest/"
        for attempt in range(2):
            r = await self.http.post(base + method + ".json", json={**params, "auth": token})
            d = r.json()
            if d.get("error") in ("expired_token", "invalid_token") and attempt == 0:
                token = await self.refresh()
                continue
            if "error" in d:
                raise RuntimeError(f"{method}: {d.get('error')} {d.get('error_description', '')}")
            return d
        return {}

    async def setup(self):
        """После установки: регистрируем канал и подписываемся на ответы операторов."""
        await self.call("imconnector.register", {
            "ID": CONNECTOR, "NAME": CONNECTOR_NAME,
            "ICON": {"DATA_IMAGE": ICON_SVG, "COLOR": "#1C3A6E", "SIZE": "100%", "POSITION": "center"},
            "PLACEMENT_HANDLER": f"{self.public_url}/v1/bitrix/placement",
        })
        await self.call("event.bind", {"event": "OnImConnectorMessageAdd", "handler": f"{self.public_url}/v1/bitrix/event"})

    async def activate(self, line: str):
        await self.call("imconnector.activate", {"CONNECTOR": CONNECTOR, "LINE": int(line), "ACTIVE": "1"})
        await self.call("imconnector.connector.data.set", {
            "CONNECTOR": CONNECTOR, "LINE": int(line),
            "DATA": {"id": CONNECTOR, "url_im": "", "name": CONNECTOR_NAME},
        })
        self.state.update(line=str(line))

    async def send_client_message(self, uid: str, msg_id: int, text: str, client: dict):
        s = self.state.load()
        user = {"id": uid, "name": client.get("name") or f"Клиент {uid}", "skip_phone_validate": "Y"}
        if client.get("surname"):
            user["last_name"] = client["surname"]
        if client.get("mobile"):
            user["phone"] = "+" + client["mobile"].lstrip("+")
        if client.get("email"):
            user["email"] = client["email"]
        await self.call("imconnector.send.messages", {
            "CONNECTOR": CONNECTOR, "LINE": int(s["line"]),
            "MESSAGES": [{
                "user": user,
                "message": {"id": str(msg_id), "date": int(time.time()), "text": text},
                "chat": {"id": uid, "name": f"Приложение: {user['name']}"},
            }],
        })

    async def delivered(self, line: str, items: list[dict]):
        """Отметка «доставлено» у сообщений операторов — иначе в Битриксе висит «не доставлено»."""
        if items:
            await self.call("imconnector.send.status.delivery", {"CONNECTOR": CONNECTOR, "LINE": int(line), "MESSAGES": items})
