"""Прокси к Laximo (подбор по VIN / кузову / госномеру, схемы узлов).

Логин и пароль Laximo хранятся только на сервере — в APK их больше нет.
Пропускаем лишь методы и параметры, которые использует приложение; ответ Laximo отдаём как есть,
чтобы приложение разбирало его и его ошибки (E_…) по-прежнему.

Каждый запрос к Laximo считается (laximo_usage.json в каталоге состояния): по дням и методам.
Тариф платится раз в месяц, лимит неизвестен — счётчик показывает, сколько мы тратим.
"""
import json
import time
from collections import defaultdict
from pathlib import Path

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


class Usage:
    """Счётчик запросов к Laximo: {"2026-10-05": {"findVehicle": 12, …}, …}. Пишется на диск сразу —
    запросов немного, а потерять счёт при перезапуске нельзя."""

    def __init__(self, path: Path | None):
        self.path = path
        self.days: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        try:
            if path and path.exists():
                for day, methods in json.loads(path.read_text(encoding="utf-8")).items():
                    self.days[day].update(methods)
        except (OSError, ValueError):
            pass

    def add(self, method: str):
        self.days[time.strftime("%Y-%m-%d")][method] += 1
        if not self.path:
            return
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.days, ensure_ascii=False, indent=0), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def report(self) -> dict:
        today, month = time.strftime("%Y-%m-%d"), time.strftime("%Y-%m")
        months: dict[str, int] = defaultdict(int)
        for day, methods in self.days.items():
            months[day[:7]] += sum(methods.values())
        this = {d: dict(m) for d, m in sorted(self.days.items()) if d.startswith(month)}
        by_method: dict[str, int] = defaultdict(int)
        for m in this.values():
            for k, v in m.items():
                by_method[k] += v
        return {"today": sum(self.days.get(today, {}).values()), "month": months.get(month, 0),
                "month_by_method": dict(by_method), "months": dict(sorted(months.items())), "days": this}


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
        state = getattr(settings, "state_dir", "")
        self.usage = Usage(Path(state) / "laximo_usage.json" if state else None)

    async def call(self, method: str, params: dict[str, str]) -> tuple[int, str]:
        # Laximo принимает параметры в строке запроса, тело — пустое, только POST
        self.usage.add(method)
        r = await self.http.post(method, params=params, content=b"")
        return r.status_code, r.text

    async def close(self):
        await self.http.aclose()
