"""Обращения к ABCP. Клиентский API — только для проверки входа; всё остальное — админский (cp/…).

Списки ABCP приходят то массивом, то объектом {"0": {...}} — разбираем оба варианта.
"""
import asyncio
import re
import time
from typing import Any

import logging

import httpx

from .config import Settings

IMG_CDN = "https://imgcdn.abcp.ru/p/"
log = logging.getLogger("avtodrug")


class AbcpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def items(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [v for v in data.values() if isinstance(v, dict)]
    return []


def _num(v: Any) -> float:
    """ABCP отдаёт числа по-разному: 4630, "4630.00", "4 630,00" (пробел — разделитель тысяч, бывает неразрывный)."""
    s = str(v if v is not None else "").replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


class Abcp:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        self.http = httpx.AsyncClient(
            base_url=settings.abcp_host,
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={"Accept": "application/json"},
            transport=transport,
        )
        self._img_cache: dict[str, tuple[float, list[str]]] = {}
        self._profiles: dict[str, str] = {}
        self._img_sem = asyncio.Semaphore(6)
        self._rel_cache: dict[str, tuple[float, list[str]]] = {}
        self._ai_day = ""
        self._ai_used = 0

    def _articles_info_allowed(self) -> bool:
        """Суточный предохранитель для articles/info (лимит тарифа ABCP — 10 в сутки)."""
        day = time.strftime("%Y-%m-%d")
        if day != self._ai_day:
            self._ai_day, self._ai_used = day, 0
        if self._ai_used >= self.s.articles_info_per_day:
            return False
        self._ai_used += 1
        return True

    async def close(self):
        await self.http.aclose()

    async def _get(self, op: str, params: dict | list) -> Any:
        r = await self.http.get(op, params=params)
        try:
            data = r.json()
        except ValueError:
            raise AbcpError(502, "ABCP вернул не JSON")
        if r.status_code >= 400:
            msg = data.get("errorMessage") if isinstance(data, dict) else None
            raise AbcpError(r.status_code, msg or "Ошибка ABCP")
        return data

    def _admin(self, params: dict | None = None) -> list:
        base = [("userlogin", self.s.admin_login), ("userpsw", self.s.admin_md5)]
        return base + list((params or {}).items())

    # ---------- клиент ----------

    async def check_client(self, login: str, md5: str) -> dict:
        """Проверка логина клиента (его собственным паролем). Возвращает user/info или AbcpError."""
        return await self._get("user/info", {"userlogin": login, "userpsw": md5})

    # ---------- админ: данные одного клиента ----------

    async def finance(self, uid: str) -> dict:
        data = await self._get("cp/users", self._admin({"customersIds[]": uid}))
        u = next((x for x in items(data) if str(x.get("userId")) == uid), None)
        if not u:
            raise AbcpError(404, "Клиент не найден")
        profile_id = str(u.get("profileId") or "")
        return {
            "balance": _num(u.get("balance")),
            "debt": _num(u.get("debt")),
            "saldo": _num(u.get("saldo")),
            "creditLimit": _num(u.get("creditLimit")),
            "overdueSaldo": _num(u.get("overdueSaldo")),
            "inStopList": str(u.get("inStopList")) in ("1", "true", "True"),
            "profile": await self.profile_name(profile_id) if profile_id else None,
        }

    async def profile_name(self, profile_id: str) -> str | None:
        if profile_id not in self._profiles:
            try:
                data = await self._get("cp/users/profiles", self._admin({"profileId": profile_id}))
                p = next(iter(items(data)), {})
                self._profiles[profile_id] = p.get("name") or ""
            except AbcpError:
                return None
        return self._profiles[profile_id] or None

    async def order(self, number: str) -> dict:
        return await self._get("cp/order", self._admin({"number": number}))

    APP_NOTE = "📱 Оформлен через приложение"

    async def add_app_note(self, number: str, text: str) -> bool:
        """Служебная заметка к заказу (видят только сотрудники). Повторно не добавляем. True — добавлена."""
        order = await self.order(number)
        notes = order.get("notes") or []
        if any(self.APP_NOTE in str((n or {}).get("value", "")) for n in (notes if isinstance(notes, list) else items(notes))):
            return False
        # Редактирование заказа: передаём только номер и новую заметку — позиции не трогаем
        data = self._admin({"order[number]": number, "order[notes][0][value]": text})
        r = await self.http.post("cp/order", data=dict(data))
        if r.status_code >= 400:
            try:
                msg = r.json().get("errorMessage")
            except ValueError:
                msg = None
            raise AbcpError(r.status_code, _msg(msg) or "ABCP не сохранил заметку")
        return True

    async def payment_link(self, number: str) -> str:
        data = await self._get("cp/payment/token/", self._admin({"number": number}))
        link = data.get("paymentLink") if isinstance(data, dict) else None
        if not link:
            raise AbcpError(502, "ABCP не выдал ссылку на оплату")
        return link

    async def topup_link(self, uid: str, amount: float) -> str:
        data = await self._get("cp/payment/top-balance-link/", self._admin({"clientId": uid, "amount": f"{amount:.2f}"}))
        link = data.get("paymentLink") if isinstance(data, dict) else None
        if not link:
            raise AbcpError(502, "ABCP не выдал ссылку на пополнение")
        return link

    # ---------- картинки (articles/info, format=i) ----------

    async def images(self, brand: str, number: str, ttl: float = 24 * 3600) -> list[str]:
        key = f"{brand.upper()}|{number.upper()}"
        hit = self._img_cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        if not self._articles_info_allowed():
            return []
        async with self._img_sem:
            try:
                data = await self._get("articles/info", self._admin({"brand": brand, "number": number, "format": "bni"}))
            except (AbcpError, httpx.HTTPError) as e:
                # Ошибку не кэшируем: иначе «нет картинок» запомнилось бы на сутки
                log.warning("images %s %s: %s", brand, number, getattr(e, "message", e))
                return []
        urls: list[str] = []
        for art in items(data) or ([data] if isinstance(data, dict) else []):
            for img in art.get("images") or []:
                name = img.get("name") if isinstance(img, dict) else img
                if name:
                    urls.append(name if str(name).startswith("http") else IMG_CDN + str(name).lstrip("/"))
        urls = list(dict.fromkeys(urls))
        self._img_cache[key] = (time.time() + ttl, urls)
        if len(self._img_cache) > 20000:  # не даём кэшу расти бесконечно
            self._img_cache.clear()
        return urls

    # ---------- достоверные аналоги (articles/info, format=c: crosses[].reliable) ----------

    async def reliable_crosses(self, brand: str, number: str, ttl: float = 24 * 3600) -> list[str]:
        """Ключи «БРЕНД|НОМЕР» (numberFix, верхний регистр) аналогов, которые ABCP считает достоверными."""
        key = f"{brand.upper()}|{number.upper()}"
        hit = self._rel_cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        if not self._articles_info_allowed():
            return []
        try:
            data = await self._get("articles/info", self._admin({"brand": brand, "number": number, "format": "bnc"}))
        except (AbcpError, httpx.HTTPError) as e:
            log.warning("crosses %s %s: %s", brand, number, getattr(e, "message", e))
            return []
        out: list[str] = []
        for art in items(data) or ([data] if isinstance(data, dict) else []):
            for c in items(art.get("crosses") or []):
                if str(c.get("reliable")) in ("1", "true", "True"):
                    num = str(c.get("numberFix") or c.get("number") or "")
                    fix = "".join(ch for ch in num.upper() if ch.isalnum())
                    if fix:
                        out.append(f"{str(c.get('brand', '')).upper()}|{fix}")
        out = list(dict.fromkeys(out))
        self._rel_cache[key] = (time.time() + ttl, out)
        if len(self._rel_cache) > 5000:
            self._rel_cache.clear()
        return out

    # ---------- регистрация и восстановление пароля (клиентские операции без входа) ----------
    # ABCP выполняет их только с разрешённых IP — поэтому идут через сервер, а не с телефона.

    async def _post_public(self, op: str, data: dict) -> Any:
        r = await self.http.post(op, data=data)
        try:
            body = r.json()
        except ValueError:
            raise AbcpError(502, "ABCP вернул не JSON")
        if r.status_code >= 400:
            msg = body.get("errorMessage") if isinstance(body, dict) else None
            raise AbcpError(r.status_code, _msg(msg) or "Ошибка ABCP")
        return body

    async def orders_updated(self, since: str, user_id: str | None = None) -> list[dict]:
        """Заказы, изменённые после `since` (московское время ABCP), с позициями. Постранично по 500."""
        out: list[dict] = []
        skip = 0
        while True:
            params = {"dateUpdatedStart": since, "limit": "500", "skip": str(skip)}
            if user_id:
                params["userId"] = user_id
            page = [o for o in items(await self._get("cp/orders", self._admin(params))) if isinstance(o, dict)]
            out += page
            if len(page) < 500 or skip > 20000:
                return out
            skip += 500

    async def client_exists(self, mobile: str, email: str = "") -> bool:
        """Есть ли уже клиент с таким мобильным (79XXXXXXXXX — так ABCP их хранит) или email."""
        for key, value in (("phone", mobile), ("email", email)):
            if value:
                data = await self._get("cp/users", self._admin({key: value, "limit": "1"}))
                if items(data):
                    return True
        return False

    async def register(self, form: dict) -> dict:
        data = {k: v for k, v in form.items() if v not in (None, "")}
        data.setdefault("marketType", "1")  # розница
        # Клиентский user/new без входа у магазина запрещён («Access to requested operation is denied») —
        # регистрируем от имени API-администратора (cp/user/new, те же поля)
        body = await self._post_public("cp/user/new", dict(self._admin(data)))
        if isinstance(body, dict) and str(body.get("status")) == "0":
            raise AbcpError(400, _msg(body.get("errorMessage")) or "Регистрация не прошла")
        return body if isinstance(body, dict) else {}

    async def restore(self, data: dict) -> dict:
        body = await self._post_public("user/restore", {k: v for k, v in data.items() if v})
        if isinstance(body, dict) and str(body.get("status")) == "0":
            raise AbcpError(400, _msg(body.get("errorMessage")) or "Не получилось")
        return body if isinstance(body, dict) else {}


def _msg(m: Any) -> str:
    """errorMessage у ABCP бывает строкой, списком или словарём полей, иногда с HTML-разметкой сайта."""
    if isinstance(m, dict):
        s = "; ".join(str(v) for v in m.values())
    elif isinstance(m, list):
        s = "; ".join(str(v) for v in m)
    else:
        s = str(m) if m else ""
    s = re.sub(r"<[^>]+>", " ", s).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", s).strip()


# ---------- гостевой поиск (без входа): от имени API-админа с профилем цен гостя ----------

# Только то, что видит покупатель. Админский поиск отдаёт ещё закупочную цену (priceIn),
# курс (priceRate), id поставщика и т.п. — наружу это не должно уходить НИКОГДА.
GUEST_OFFER_FIELDS = (
    "brand", "number", "numberFix", "description", "price", "availability", "packing",
    "deliveryPeriod", "deliveryPeriodMax", "deadlineReplace", "supplierDescription",
    "supplierColor", "noReturn", "isUsed", "deliveryProbability",
    "descriptionOfDeliveryProbability", "lastUpdateTime",
)
GUEST_BRAND_FIELDS = ("brand", "number", "numberFix", "description", "availability")


def only(d: dict, fields) -> dict:
    return {k: d[k] for k in fields if k in d}


async def guest_brands(a: "Abcp", number: str) -> list[dict]:
    data = await a._get("search/brands", a._admin({"number": number, "useOnlineStocks": 1}))
    return [only(x, GUEST_BRAND_FIELDS) for x in items(data)]


async def guest_offers(a: "Abcp", number: str, brand: str, profile_id: str, all_: bool) -> list[dict]:
    data = await a._get("search/articles", a._admin({
        "number": number, "brand": brand, "useOnlineStocks": 1,
        "disableFiltering": 1 if all_ else 0, "profileId": profile_id,
    }))
    return [only(x, GUEST_OFFER_FIELDS) for x in items(data)]
