"""API для приложения «Автодруг»: то, что ABCP даёт только API-администратору.

POST /v1/session              логин клиента → токен (пароль не хранится)
GET  /v1/me/finance           баланс, долг, кредитный лимит, профиль (уровень цен)
GET  /v1/orders/{number}/pay  ссылка на оплату заказа (только своего и неоплаченного)
GET  /v1/topup?amount=        ссылка на пополнение баланса
POST /v1/images               картинки товаров для выдачи
POST /v1/reliable             достоверные аналоги (звёздочка)
POST /v1/laximo/{method}      подбор по авто через Laximo (пароль Laximo — только на сервере)
POST /v1/access-request       заявка на включение прав API (менеджерам в Telegram)
GET  /v1/access-request       отправлена ли заявка
DELETE /v1/access-request     доступ появился — закрыть заявку
POST /v1/push/token           push-токен устройства (RuStore) для уведомлений о заказах
DELETE /v1/push/token         забыть токен (выход из аккаунта)
POST /v1/admin/push-test      тестовый push клиенту (X-Upload-Token)
GET  /v1/app/latest           последняя сборка приложения (автообновление)
GET  /v1/app/apk/{code}       скачать сборку
PUT  /v1/app/apk/{code}       загрузка сборки из CI (токен X-Upload-Token)
"""
import asyncio
import hmac
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from . import access, push, releases, tokens
from .abcp import Abcp, AbcpError, guest_brands, guest_offers
from .config import Settings, load
from .laximo import METHODS as LAXIMO_METHODS, PARAMS as LAXIMO_PARAMS, Laximo

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("avtodrug")


class SessionIn(BaseModel):
    login: str = Field(min_length=1, max_length=200)
    passwordMd5: str = Field(min_length=32, max_length=32)


class Article(BaseModel):
    brand: str = Field(min_length=1, max_length=100)
    number: str = Field(min_length=1, max_length=100)


class ImagesIn(BaseModel):
    items: list[Article] = Field(max_length=40)


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    surname: str = Field(default="", max_length=60)
    mobile: str = Field(min_length=10, max_length=20)
    email: str = Field(default="", max_length=120)
    password: str = Field(min_length=6, max_length=64)
    office: str = Field(pattern=r"^\d{1,10}$")


class PushTokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=1000)


class PushTestIn(BaseModel):
    uid: str = Field(pattern=r"^\d{1,12}$")
    text: str = Field(default="Тестовое уведомление Автодруг92", max_length=200)


class AccessIn(BaseModel):
    missing: list[str] = Field(default_factory=list, max_length=10)


class RestoreIn(BaseModel):
    emailOrMobile: str = Field(min_length=5, max_length=120)
    code: str = Field(default="", max_length=20)
    passwordNew: str = Field(default="", max_length=64)


class RateLimiter:
    """Не больше `limit` попыток за `window` секунд с одного ключа (IP) — против перебора паролей."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = now or time.time()
        q = self.hits[key]
        while q and q[0] < now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


def create_app(settings: Settings | None = None, abcp: Abcp | None = None, laximo: Laximo | None = None) -> FastAPI:
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = settings or load()
        state["s"] = s
        state["abcp"] = abcp or Abcp(s)
        state["laximo"] = laximo or Laximo(s)
        folder = Path(s.state_dir)
        state["tokens"] = push.TokenStore(folder)
        state["pusher"] = push.RuStorePush(state["abcp"].http, s.rustore_push_host, s.rustore_project_id, s.rustore_push_token)
        state["watcher"] = push.OrderWatcher(state["abcp"], state["pusher"], state["tokens"], folder, s.order_watch_interval)
        app.state.watcher = state["watcher"]  # для тестов и ручного запуска
        tasks = []
        if state["pusher"].enabled and s.order_watch_interval > 0:
            tasks.append(asyncio.create_task(state["watcher"].run()))
        if s.telegram_bot_token and s.telegram_chat_id:
            app.state.buttons = access.ButtonListener(state["abcp"].http, s.telegram_bot_token, s.telegram_chat_id, access_granted)
            tasks.append(asyncio.create_task(app.state.buttons.run()))
            tasks.append(asyncio.create_task(resend_unsent()))
        yield
        for t in tasks:
            t.cancel()
        await state["abcp"].close()
        await state["laximo"].close()

    app = FastAPI(title="Avtodrug API", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        # Только метод, путь и код — без параметров, тела и токена
        t = time.time()
        resp = await call_next(request)
        log.info("%s %s -> %s %.0fms", request.method, request.url.path, resp.status_code, (time.time() - t) * 1000)
        return resp
    login_limit = RateLimiter(limit=10, window=60)
    # Регистрация и SMS восстановления — дорогие и заметные операции, лимит строже
    public_limit = RateLimiter(limit=5, window=3600)
    # Гостевой поиск: живой человек не ищет чаще раза в секунду
    guest_limit = RateLimiter(limit=60, window=60)

    def client_ip(request: Request) -> str:
        return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")

    def fail(e: AbcpError):
        # Наружу — только понятный текст, без деталей запросов к ABCP
        code = 404 if e.status == 404 else 502
        raise HTTPException(code, e.message)

    async def current_uid(authorization: str = Header(default="")) -> str:
        token = authorization.removeprefix("Bearer ").strip()
        uid = tokens.verify(state["s"].token_secret, token) if token else None
        if not uid:
            raise HTTPException(401, "Нужно войти заново")
        return uid

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/v1/session")
    async def session(body: SessionIn, request: Request):
        ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
        if not login_limit.allow(ip):
            raise HTTPException(429, "Слишком много попыток входа, подождите минуту")
        try:
            user = await state["abcp"].check_client(body.login.strip(), body.passwordMd5.lower())
        except AbcpError as e:
            if e.status in (401, 403):
                raise HTTPException(401, "Неверный логин или пароль")
            fail(e)
        uid = str(user.get("id") or "")
        if not uid:
            raise HTTPException(401, "Неверный логин или пароль")
        s = state["s"]
        return {"token": tokens.issue(s.token_secret, uid, s.token_ttl), "user": {"id": uid, "name": user.get("name")}}

    @app.get("/v1/me/finance")
    async def finance(uid: str = Depends(current_uid)):
        try:
            return await state["abcp"].finance(uid)
        except AbcpError as e:
            fail(e)

    @app.get("/v1/orders/{number}/pay")
    async def pay(number: str, uid: str = Depends(current_uid)):
        if not number.isdigit() or len(number) > 20:
            raise HTTPException(400, "Неверный номер заказа")
        a: Abcp = state["abcp"]
        try:
            order = await a.order(number)
            # Ссылку выдаём только на свой заказ — иначе можно было бы подсмотреть чужой
            if str(order.get("userId")) != uid:
                raise HTTPException(404, "Заказ не найден")
            if str(order.get("paid")) in ("1", "true", "True"):
                raise HTTPException(409, "Заказ уже оплачен")
            try:
                debt = float(str(order.get("debt") or 0).replace(",", "."))
            except ValueError:
                debt = 0.0
            if debt <= 0:
                raise HTTPException(409, "По заказу нет задолженности")
            return {"url": await a.payment_link(number), "amount": debt}
        except AbcpError as e:
            fail(e)

    @app.get("/v1/topup")
    async def topup(amount: float, uid: str = Depends(current_uid)):
        if not (1 <= amount <= 1_000_000):
            raise HTTPException(400, "Неверная сумма")
        try:
            return {"url": await state["abcp"].topup_link(uid, amount)}
        except AbcpError as e:
            fail(e)

    @app.post("/v1/images")
    async def images(body: ImagesIn, uid: str = Depends(current_uid)):
        a: Abcp = state["abcp"]
        results = await asyncio.gather(*(a.images(i.brand, i.number) for i in body.items))
        return {f"{i.brand}|{i.number}": urls for i, urls in zip(body.items, results)}

    def guest_guard(request: Request) -> str:
        pid = state["s"].guest_profile_id
        if not pid:
            raise HTTPException(403, "Поиск без входа выключен")
        if not guest_limit.allow("guest:" + client_ip(request)):
            raise HTTPException(429, "Слишком много запросов, подождите минуту")
        return pid

    @app.get("/v1/guest/brands")
    async def g_brands(number: str, request: Request):
        guest_guard(request)
        if not (1 <= len(number.strip()) <= 60):
            raise HTTPException(400, "Неверный номер")
        try:
            return await guest_brands(state["abcp"], number.strip())
        except AbcpError as e:
            fail(e)

    @app.get("/v1/guest/offers")
    async def g_offers(number: str, brand: str, request: Request, all: int = 0):
        pid = guest_guard(request)
        if not (1 <= len(number.strip()) <= 60) or not (1 <= len(brand.strip()) <= 100):
            raise HTTPException(400, "Неверный номер")
        try:
            return await guest_offers(state["abcp"], number.strip(), brand.strip(), pid, bool(all))
        except AbcpError as e:
            fail(e)

    @app.post("/v1/register")
    async def register(body: RegisterIn, request: Request):
        if not public_limit.allow("reg:" + client_ip(request)):
            raise HTTPException(429, "Слишком много попыток, попробуйте через час")
        mobile = "".join(ch for ch in body.mobile if ch.isdigit())
        try:
            # Уже есть аккаунт — не плодим дубль, приложение предложит войти или напомнить пароль
            if await state["abcp"].client_exists(mobile, body.email.strip()):
                raise HTTPException(409, "Этот номер или email уже зарегистрирован. Войдите или восстановите пароль.")
        except AbcpError:
            pass  # проверка не удалась — пусть решает сам ABCP
        try:
            r = await state["abcp"].register({
                "name": body.name.strip(), "surname": body.surname.strip(), "mobile": mobile,
                "email": body.email.strip(), "password": body.password, "office": body.office,
            })
        except AbcpError as e:
            raise HTTPException(400 if e.status < 500 else 502, e.message)
        return {"ok": True, "needsActivation": bool(r.get("activationCode"))}

    @app.post("/v1/restore")
    async def restore(body: RestoreIn, request: Request):
        """Без code — отправить SMS/письмо; с code и passwordNew — сохранить новый пароль."""
        if not body.code and not public_limit.allow("restore:" + client_ip(request)):
            raise HTTPException(429, "Слишком много запросов кода, попробуйте через час")
        try:
            r = await state["abcp"].restore(body.model_dump())
        except AbcpError as e:
            # 404 — такого клиента нет: приложение предложит зарегистрироваться
            raise HTTPException(404 if e.status == 404 else 400 if e.status < 500 else 502, e.message)
        return {"ok": True, "message": r.get("message")}

    @app.post("/v1/reliable")
    async def reliable(body: Article, uid: str = Depends(current_uid)):
        """Достоверные аналоги артикула — для звёздочки, как на сайте."""
        return {"reliable": await state["abcp"].reliable_crosses(body.brand, body.number)}

    # Подбор по авто: гостям — как гостевой поиск; вошедшим — отдельный, щедрее
    laximo_user_limit = RateLimiter(limit=120, window=60)

    @app.post("/v1/laximo/{method}")
    async def laximo_call(method: str, params: dict[str, str], request: Request,
                          authorization: str = Header(default="")):
        lx: Laximo = state["laximo"]
        if not lx.enabled:
            raise HTTPException(503, "Подбор по авто временно недоступен")
        if method not in LAXIMO_METHODS:
            raise HTTPException(404, "Неизвестный метод")
        if set(params) - LAXIMO_PARAMS or any(len(v) > 4000 for v in params.values()):
            raise HTTPException(400, "Неверные параметры")
        token = authorization.removeprefix("Bearer ").strip()
        uid = tokens.verify(state["s"].token_secret, token) if token else None
        if token and not uid:
            raise HTTPException(401, "Нужно войти заново")
        ok = laximo_user_limit.allow("lx:" + uid) if uid else guest_limit.allow("guest:" + client_ip(request))
        if not ok:
            raise HTTPException(429, "Слишком много запросов, подождите минуту")
        try:
            code, text = await lx.call(method, params)
        except Exception:
            raise HTTPException(502, "Каталог не ответил, попробуйте ещё раз")
        # Ответ как есть (и ошибки E_… тоже), но 5xx Laximo — это 502 для приложения
        return Response(text, status_code=502 if code >= 500 else code, media_type="application/json")

    @app.post("/v1/push/token")
    async def push_token(body: PushTokenIn, uid: str = Depends(current_uid)):
        new = uid not in state["tokens"].uids()
        state["tokens"].add(uid, body.token)
        if new:
            # Запоминаем текущие статусы клиента — уведомлять будем только об изменениях
            try:
                await state["watcher"].seed(uid)
            except AbcpError:
                pass
        return {"ok": True, "push": state["pusher"].enabled}

    @app.delete("/v1/push/token")
    async def push_token_delete(body: PushTokenIn):
        # Без авторизации: при выходе токен сервера уже может быть недействителен, а сам push-токен и есть ключ
        state["tokens"].remove(body.token)
        return {"ok": True}

    @app.post("/v1/admin/push-test")
    async def push_test(body: PushTestIn, x_upload_token: str = Header(default="")):
        s = state["s"]
        if not s.app_upload_token or not hmac.compare_digest(x_upload_token, s.app_upload_token):
            raise HTTPException(403, "Нет доступа")
        codes = [await state["pusher"].send(t, {"type": "test", "title": "Автодруг92", "body": body.text})
                 for t in state["tokens"].tokens(body.uid)]
        return {"devices": len(codes), "codes": codes}

    def queue() -> access.AccessQueue:
        return access.AccessQueue(Path(state["s"].state_dir))

    async def notify_managers(text: str, markup: dict | None = None) -> bool:
        s = state["s"]
        return await access.telegram(state["abcp"].http, s.telegram_bot_token, s.telegram_chat_id, text, markup)

    async def send_request(uid: str, req: dict, repeat: bool) -> bool:
        sent = await notify_managers(access.request_text(uid, req, repeat), access.granted_button(uid))
        if sent:
            queue().mark_notified(uid)  # не ушло (бот не настроен, Telegram недоступен) — повторим позже
        return sent

    async def resend_unsent():
        for uid in queue().unsent():
            await send_request(uid, queue().get(uid) or {}, repeat=False)

    async def access_granted(uid: str) -> bool:
        """Менеджер нажал «Доступ включён»: закрыть заявку и сказать клиенту push-уведомлением."""
        queue().close(uid)
        data = {"type": "access_granted", "title": "Доступ включён",
                "body": "Поиск, корзина и заказы в приложении работают. Откройте «Автодруг»."}
        codes = [await state["pusher"].send(t, data) for t in state["tokens"].tokens(uid)] if state["pusher"].enabled else []
        return 200 in codes

    @app.post("/v1/access-request")
    async def access_request(body: AccessIn, uid: str = Depends(current_uid)):
        missing = [m for m in body.missing if m in access.FEATURES]
        old = queue().get(uid)
        req, notify = queue().add(uid, missing)
        sent = await send_request(uid, req, repeat=old is not None) if notify else False
        log.info("access request %s notify=%s sent=%s", uid, notify, sent)
        return {"createdAt": req["createdAt"], "notified": sent or not notify}

    @app.get("/v1/access-request")
    async def access_status(uid: str = Depends(current_uid)):
        req = queue().get(uid)
        return {"pending": req is not None, "createdAt": req["createdAt"] if req else None}

    @app.delete("/v1/access-request")
    async def access_done(uid: str = Depends(current_uid)):
        req = queue().close(uid)
        if req:
            await notify_managers(f"✅ Доступ из приложения работает: клиент ID {uid}")
        return {"closed": req is not None}

    MAX_APK = 150 * 1024 * 1024

    @app.get("/v1/app/latest")
    async def app_latest():
        info = releases.latest(Path(state["s"].app_dir))
        if not info:
            raise HTTPException(404, "Сборок пока нет")
        return {**info, "url": f"/v1/app/apk/{info['versionCode']}"}

    @app.get("/v1/app/apk/{code}")
    async def app_apk(code: int):
        path = releases.apk_path(Path(state["s"].app_dir), code)
        if not path.is_file():
            raise HTTPException(404, "Сборка не найдена")
        return FileResponse(path, media_type="application/vnd.android.package-archive",
                            filename=f"avtodrug92-{code}.apk")

    @app.put("/v1/app/apk/{code}")
    async def app_upload(code: int, request: Request, versionName: str = "", notes: str = "",
                         x_upload_token: str = Header(default="")):
        s = state["s"]
        if not s.app_upload_token or not hmac.compare_digest(x_upload_token, s.app_upload_token):
            raise HTTPException(403, "Нет доступа")
        if not (1 <= code <= 2_000_000_000):
            raise HTTPException(400, "Неверный номер сборки")
        data = await request.body()
        # APK — это zip: без сигнатуры PK не принимаем, чтобы случайно не раздать мусор
        if not data.startswith(b"PK") or len(data) > MAX_APK:
            raise HTTPException(400, "Это не APK")
        info = releases.save(Path(s.app_dir), data, code, versionName[:40], notes[:2000])
        log.info("app uploaded: %s (%s bytes)", code, info["size"])
        return info

    return app


app = create_app()
