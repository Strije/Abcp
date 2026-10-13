"""Чат приложения как собственный канал открытой линии Битрикс24 (коннектор imconnector).

Клиент пишет в приложении → сервер передаёт сообщение в открытую линию (imconnector.send.messages).
Оператор отвечает в Битрикс24 → событие OnImConnectorMessageAdd на наш сервер → сохраняем и шлём push.
Внешний «чат» в терминах коннектора — ID клиента ABCP: один клиент — один диалог.

Установка: локальное серверное приложение Битрикс24 → POST /v1/bitrix/install (токены) → регистрируем
коннектор и подписываемся на событие; администратор включает канал в настройках линии →
Битрикс открывает PLACEMENT_HANDLER → активируем коннектор на этой линии.

Подсказчик подбора (IM_TEXTAREA): кнопка «Подбор» над полем ввода в чатах открытых линий. Битрикс открывает
/v1/bitrix/hint с токеном оператора → выдаём пропуск на полчаса (Tickets) → страница подбора читает переписку
клиента (im.dialog.messages.get от имени оператора), робот ищет, оператор вставляет ответ в поле и сам отправляет.
Клиенту робот ничего не пишет. Нужны права приложения: imconnector, imopenlines, im, placement.

Чат-приложение (imbot.app.register, 13.10): открытые линии портала идут в старом чате, встройки placement там не видны —
только «чат-приложения» (как у ChatApp). При установке регистрируем бота-владельца «Подбор» и приложение-окно
/v1/bitrix/chatapp. Окно получает DIALOG_ID, USER_ID и подписи md5(хост+HASH), md5(USER_ID+HASH) — проверяем; ключа
менеджера нет, поэтому переписку читаем ключом приложения, убедившись, что пользователь — участник этого чата.
Нужно право imbot.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import httpx

log = logging.getLogger("avtodrug")

CONNECTOR = "avtodrug_app"
CONNECTOR_NAME = "Приложение Автодруг"
KEEP_DAYS = 180  # переписку на нашем сервере храним полгода (полная история — в Битрикс24)
HINT_TITLE = "Подбор"
BOT_CODE = "avtodrug_podbor"
CHATAPP_CODE = "avtodrug_podbor_frame"
CHATAPP_ICON = Path(__file__).parent / "static" / "podbor_chat_icon.png"   # 108×66: серая и голубая машинка

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
        try:
            await self.call("event.bind", {"event": "OnImConnectorMessageAdd", "handler": f"{self.public_url}/v1/bitrix/event"})
        except RuntimeError as e:
            # Переустановка (12.10): «Handler already binded» — подписка уже есть, это не провал установки
            if "already bind" not in str(e).lower():
                raise
        try:
            await self.call("placement.bind", {
                "PLACEMENT": "IM_TEXTAREA", "HANDLER": f"{self.public_url}/v1/bitrix/hint", "TITLE": HINT_TITLE,
                "OPTIONS": {"iconName": "fa-car", "context": "LINES", "role": "USER", "extranet": "N", "color": "ORANGE"},
            })
        except RuntimeError as e:
            # Уже привязан при прошлой установке или у приложения нет прав placement/im — канал чата работает и без подсказчика
            log.info("bitrix hint placement: %s", e)
        try:
            await self.register_chatapp()
        except Exception as e:  # noqa: BLE001 — нет права imbot и т.п.: остальное работает и без чат-приложения
            log.info("bitrix chat app: %s", e)

    async def register_chatapp(self):
        """Бот-владелец и чат-приложение «Подбор» в меню чатов открытых линий (старый чат). Повторная установка не
        плодит ботов: ищем своего по коду; приложение — обновляем, если уже есть."""
        s = self.state.load()
        secret = s.get("chatapp_hash") or secrets.token_hex(16)
        bot_id = s.get("bot_id")
        if not bot_id:
            bots = (await self.call("imbot.bot.list", {})).get("result") or {}
            bots = bots.values() if isinstance(bots, dict) else bots
            bot_id = next((int(b["ID"]) for b in bots if str(b.get("CODE")) == BOT_CODE), None)
        if not bot_id:
            bot_id = int((await self.call("imbot.register", {
                "CODE": BOT_CODE, "TYPE": "B", "OPENLINE": "N", "EVENT_HANDLER": f"{self.public_url}/v1/bitrix/bot",
                "PROPERTIES": {"NAME": "Подбор", "LAST_NAME": "Автодруг", "COLOR": "ORANGE",
                               "WORK_POSITION": "Помощник менеджера: подбор деталей по переписке"},
            })).get("result"))
        fields = {"IFRAME": f"{self.public_url}/v1/bitrix/chatapp", "IFRAME_WIDTH": "900", "IFRAME_HEIGHT": "650",
                  "HASH": secret, "ICON_FILE": base64.b64encode(CHATAPP_ICON.read_bytes()).decode(), "CONTEXT": "LINES",
                  "HIDDEN": "N", "EXTRANET_SUPPORT": "N", "LIVECHAT_SUPPORT": "N", "IFRAME_POPUP": "N",
                  "LANG": [{"LANGUAGE_ID": "ru", "TITLE": "Подбор", "DESCRIPTION": "Подбор деталей по переписке с клиентом"}]}
        app_id = s.get("chatapp_id")
        if app_id:
            try:
                await self.call("imbot.app.update", {"APP_ID": app_id, "FIELDS": fields})
            except RuntimeError:
                app_id = None   # удалили в портале — регистрируем заново
        if not app_id:
            app_id = int((await self.call("imbot.app.register", {"BOT_ID": bot_id, "CODE": CHATAPP_CODE, **fields})).get("result"))
        self.state.update(bot_id=bot_id, chatapp_id=app_id, chatapp_hash=secret)

    async def chat_member(self, dialog: str, user_id: str) -> bool | None:
        """Пользователь — участник чата? None — портал не ответил (ключ приложения не видит этот чат)."""
        try:
            users = (await self.call("im.chat.user.list", {"CHAT_ID": int(dialog.removeprefix("chat"))})).get("result") or []
        except (RuntimeError, ValueError):
            return None
        return str(user_id) in {str(u) for u in users}

    async def dialog_messages(self, dialog: str) -> dict:
        """Переписка чата ключом приложения в виде im.dialog.messages.get. Установивший приложение не участник чата —
        через историю сессии открытой линии (так работала выгрузка переписки, bitrix_export)."""
        try:
            return (await self.call("im.dialog.messages.get", {"DIALOG_ID": dialog, "LIMIT": 50})).get("result") or {}
        except RuntimeError:
            chat = dialog.removeprefix("chat")
            info = (await self.call("imopenlines.dialog.get", {"CHAT_ID": chat})).get("result") or {}
            parts = str(info.get("entity_data_1") or "").split("|")
            sid = parts[5] if len(parts) > 5 and parts[5].isdigit() else ""
            if not sid:
                raise
            h = (await self.call("imopenlines.session.history.get", {"CHAT_ID": chat, "SESSION_ID": sid})).get("result") or {}
            return history_as_messages(h)

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


# ---------- подсказчик подбора в чате открытой линии ----------

def history_as_messages(h: dict) -> dict:
    """imopenlines.session.history.get → вид im.dialog.messages.get (messages с author_id, users с connector)."""
    msgs = h.get("message") or {}
    msgs = list(msgs.values()) if isinstance(msgs, dict) else msgs
    users = h.get("users") or {}
    users = list(users.values()) if isinstance(users, dict) else users
    return {"messages": [{"id": m.get("id"), "author_id": m.get("senderid") or m.get("author_id") or 0, "date": m.get("date"),
                          "text": m.get("text") or "", "params": m.get("params") or {}} for m in msgs],
            "users": [{"id": u.get("id"), "connector": bool(u.get("connector")), "extranet": bool(u.get("extranet"))}
                      for u in users]}


def chatapp_ok(params: dict, secret: str, domain: str) -> bool:
    """Подписи окна чат-приложения: DOMAIN_HASH = md5(хост + HASH), USER_HASH = md5(USER_ID + HASH), и портал — наш."""
    raw = str(params.get("DOMAIN") or "")
    host = (urlparse(raw).hostname or raw).lower()
    if not secret or not host or host != domain.lower():
        return False
    good_d = hashlib.md5((host + secret).encode()).hexdigest()
    good_u = hashlib.md5((str(params.get("USER_ID") or "") + secret).encode()).hexdigest()
    return (hmac.compare_digest(good_d, str(params.get("DOMAIN_HASH") or ""))
            and hmac.compare_digest(good_u, str(params.get("USER_HASH") or "")))


class Tickets:
    """Пропуск страницы подсказчика: токен оператора и диалог. Только в памяти процесса и на полчаса
    (токен Битрикса живёт час): перезапуск службы — оператор просто открывает «Подбор» заново."""
    TTL = 1800
    LIMIT = 500

    def __init__(self):
        self.items: dict[str, dict] = {}

    def issue(self, auth: str, endpoint: str, dialog: str, **extra) -> str:
        now = time.time()
        self.items = {k: v for k, v in self.items.items() if v["exp"] > now}
        while len(self.items) >= self.LIMIT:
            self.items.pop(next(iter(self.items)))
        t = secrets.token_urlsafe(24)
        self.items[t] = {"auth": auth, "endpoint": endpoint, "dialog": dialog, "exp": now + self.TTL, **extra}
        return t

    def get(self, ticket: str) -> dict | None:
        v = self.items.get(ticket or "")
        return v if v and v["exp"] > time.time() else None


async def operator_call(http: httpx.AsyncClient, endpoint: str, auth: str, method: str, params: dict) -> dict:
    """Вызов REST от имени оператора, открывшего подсказчик: видно ровно то, что видно ему."""
    r = await http.post(endpoint + method + ".json", json={**params, "auth": auth})
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"{method}: {d.get('error')} {d.get('error_description', '')}")
    return d


ROBOT = re.compile(r"^\s*Отправлено роботом", re.I)   # рассылки магазина в том же чате — не слова клиента
_BB_USER = re.compile(r"\[USER=\d+[^\]]*\](.*?)\[/USER\]", re.I | re.S)
_ONLY_LINK = re.compile(r"\s*(?:(?:открыть|open|фото|файл|изображение)\s*)?\(?\s*(https?://\S+?)\s*\)?\s*", re.I)
_IMAGE_LINK = re.compile(r"chatFiles|/disk/|\.(?:png|jpe?g|webp|gif|heic|bmp)(?:$|[?#])", re.I)
HINT_RECENT_DAYS = 2
HINT_MAX_MESSAGES = 15


def _when(s) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def client_text(result: dict, after: int = 0, find_ident=None) -> dict:
    """Что написал клиент — для подбора, из ответа im.dialog.messages.get.
    after=0 (первое открытие): сообщения клиента за последние двое суток переписки, не больше 15; если VIN был раньше —
    подставляем его в начало. after>0: только сообщения клиента после этого (память разговора — у страницы подбора).
    Клиент — участник-коннектор (Telegram, Авито, сайт…) или экстранет; системные сообщения, менеджеры и рассылки — нет."""
    users = result.get("users") or []
    users = users.values() if isinstance(users, dict) else users
    clients = {str(u.get("id")) for u in users if u.get("connector") or u.get("extranet")}
    msgs = sorted(result.get("messages") or [], key=lambda m: int(m.get("id") or 0))
    last = max([after] + [int(m.get("id") or 0) for m in msgs])
    mine, photos = [], 0
    for m in msgs:
        if str(m.get("author_id") or "0") not in clients:
            continue
        _, text = clean_bb(_BB_USER.sub(r"\1", str(m.get("text") or "")))
        link = _ONLY_LINK.fullmatch(text or "")
        if link:
            # Max через ChatApp присылает фото ссылкой: «Открыть (https://…chatapp…/chatFiles/….png)» (13.10) — это фото,
            # а не слова клиента; одна посторонняя ссылка — тоже не просьба о детали
            if _IMAGE_LINK.search(link.group(1)) and int(m.get("id") or 0) > after:
                photos += 1
            continue
        if text and not ROBOT.match(text):
            mine.append((int(m.get("id") or 0), _when(m.get("date")), text))
        elif not text and (m.get("params") or {}).get("FILE_ID") and int(m.get("id") or 0) > after:
            photos += 1
    if after:
        new = [t for i, _, t in mine if i > after][-HINT_MAX_MESSAGES:]
        return {"text": _fit("\n".join(new)), "last": last, "count": len(new), "photos": photos}
    if not mine:
        return {"text": "", "last": last, "count": 0, "photos": photos}
    edge = mine[-1][1] - timedelta(days=HINT_RECENT_DAYS) if mine[-1][1] else None
    recent = [x for x in mine if edge is None or x[1] is None or x[1] >= edge][-HINT_MAX_MESSAGES:]
    text = "\n".join(t for _, _, t in recent)
    if find_ident and not find_ident(text):
        vin = next((v for v in (find_ident(t) for _, _, t in reversed(mine)) if v), "")
        if vin:
            text = f"VIN: {vin}\n{text}"
    return {"text": _fit(text, find_ident), "last": last, "count": len(recent), "photos": photos}


def _fit(text: str, find_ident=None, limit: int = 3800) -> str:
    """Подбор принимает до 4000 знаков: длинную переписку режем с начала, VIN сохраняем."""
    if len(text) <= limit:
        return text
    vin = find_ident(text) if find_ident else ""
    tail = text[-(limit - 30):]
    return f"VIN: {vin}\n{tail}" if vin and vin not in tail else tail
