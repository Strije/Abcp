"""Прокси к Laximo (подбор по VIN / кузову / госномеру, схемы узлов).

Логин и пароль Laximo хранятся только на сервере — в APK их больше нет.
Пропускаем лишь методы и параметры, которые использует приложение; ответ Laximo отдаём как есть,
чтобы приложение разбирало его и его ошибки (E_…) по-прежнему.
"""
import httpx

from .config import Settings

BASE = "https://ws.laximo.ru/restApi/v1/"

METHODS = {
    "findVehicle", "findVehicleByPlateNumber",
    "listCategories", "listUnits", "listDetailByUnit", "listImageMapByUnit",
    "listQuickGroup", "listQuickDetail",
}
PARAMS = {
    "identString", "countryCode", "plateNumber",
    "catalog", "ssd", "vehicleId", "categoryId", "unitId", "quickGroupId", "query", "all",
}


class Laximo:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.enabled = bool(settings.laximo_user and settings.laximo_pass)
        self.http = httpx.AsyncClient(
            base_url=BASE,
            auth=(settings.laximo_user, settings.laximo_pass),
            timeout=httpx.Timeout(25.0, connect=10.0),
            headers={"accept-language": "ru_RU", "Accept": "application/json"},
            transport=transport,
        )

    async def call(self, method: str, params: dict[str, str]) -> tuple[int, str]:
        # Laximo принимает параметры в строке запроса, тело — пустое, только POST
        r = await self.http.post(method, params=params, content=b"")
        return r.status_code, r.text

    async def close(self):
        await self.http.aclose()
