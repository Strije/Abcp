"""Откуда подбор берёт данные.

Direct — на сервере: Laximo с паролем сервера и ABCP от имени API-админа с гостевыми ценами.
Remote — с любого компьютера через гостевые запросы рабочего сервера (/v1/laximo, /v1/guest/…):
так подбор можно гонять на заявках локально, без паролей.
"""
import asyncio
import json
from typing import Any

import httpx

from ..abcp import Abcp, AbcpError, guest_brands, guest_offers
from ..laximo import Laximo
from .catalog import LaximoError, check_error


class Direct:
    def __init__(self, laximo: Laximo, abcp: Abcp, profile_id: str):
        self.lx, self.abcp, self.profile = laximo, abcp, profile_id

    async def laximo(self, method: str, params: dict) -> Any:
        code, body = await self.lx.call(method, params)
        if code >= 500:
            raise LaximoError("E_UNAVAILABLE", str(code))
        try:
            return check_error(json.loads(body))
        except ValueError:
            raise LaximoError("E_BADRESPONSE", str(code))

    async def brands(self, number: str) -> list[dict]:
        try:
            return await guest_brands(self.abcp, number)
        except AbcpError as e:
            if e.status == 404:
                return []
            raise

    async def offers(self, number: str, brand: str) -> list[dict]:
        if not self.profile:
            return []  # гостевые цены не настроены — подбор работает без цен
        try:
            return await guest_offers(self.abcp, number, brand, self.profile, False)
        except AbcpError as e:
            if e.status == 404:
                return []
            raise


class Remote:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=httpx.Timeout(60.0, connect=15.0),
                                      transport=transport)
        self.sem = asyncio.Semaphore(3)

    async def _send(self, method: str, url: str, **kw) -> httpx.Response:
        async with self.sem:
            for attempt in range(5):
                r = await self.http.request(method, url, **kw)
                if r.status_code != 429:
                    return r
                await asyncio.sleep(15 * (attempt + 1))  # гостевой лимит сервера — 60 запросов в минуту
            return r

    async def laximo(self, method: str, params: dict) -> Any:
        r = await self._send("POST", f"/v1/laximo/{method}", json=params)
        try:
            data = r.json()
        except ValueError:
            raise LaximoError("E_BADRESPONSE", str(r.status_code))
        check_error(data)
        if r.status_code >= 400:
            raise LaximoError("E_HTTP", str(data.get("detail") if isinstance(data, dict) else r.status_code))
        return data

    async def brands(self, number: str) -> list[dict]:
        r = await self._send("GET", "/v1/guest/brands", params={"number": number})
        return r.json() if r.status_code == 200 else []

    async def offers(self, number: str, brand: str) -> list[dict]:
        r = await self._send("GET", "/v1/guest/offers", params={"number": number, "brand": brand, "all": 0})
        return r.json() if r.status_code == 200 else []

    async def close(self):
        await self.http.aclose()
