"""API для приложения «Автодруг»: то, что ABCP даёт только API-администратору.

POST /v1/session              логин клиента → токен (пароль не хранится)
GET  /v1/me/finance           баланс, долг, кредитный лимит, профиль (уровень цен)
GET  /v1/orders/{number}/pay  ссылка на оплату заказа (только своего и неоплаченного)
GET  /v1/topup?amount=        ссылка на пополнение баланса
POST /v1/images               картинки товаров для выдачи
POST /v1/reliable             достоверные аналоги (звёздочка)
"""
import asyncio
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import tokens
from .abcp import Abcp, AbcpError
from .config import Settings, load

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


def create_app(settings: Settings | None = None, abcp: Abcp | None = None) -> FastAPI:
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = settings or load()
        state["s"] = s
        state["abcp"] = abcp or Abcp(s)
        yield
        await state["abcp"].close()

    app = FastAPI(title="Avtodrug API", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        # Только метод, путь и код — без параметров, тела и токена
        t = time.time()
        resp = await call_next(request)
        log.info("%s %s -> %s %.0fms", request.method, request.url.path, resp.status_code, (time.time() - t) * 1000)
        return resp
    login_limit = RateLimiter(limit=10, window=60)

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

    @app.post("/v1/reliable")
    async def reliable(body: Article, uid: str = Depends(current_uid)):
        """Достоверные аналоги артикула — для звёздочки, как на сайте."""
        return {"reliable": await state["abcp"].reliable_crosses(body.brand, body.number)}

    return app


app = create_app()
