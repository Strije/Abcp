"""Модели ассистента — шлюз Timeweb (AIGW, OpenAI-совместимый): чат с инструментами и распознавание речи.

Каждый вызов считается в рублях по ценам панели Timeweb (26.10.2026) и пишется в assistant_usage.json по дням;
дневной потолок — Settings.assistant_day_rub: выше него ассистент не зовёт модель, а передаёт менеджеру.
Ключ — только на сервере (AIGW_API_KEY).
"""
import json
import time
from collections import defaultdict
from pathlib import Path

import httpx

# ₽ за миллион токенов: вход, выход (панель Timeweb → AI-шлюз, 26.10.2026)
PRICES = {
    "anthropic/claude-haiku-5-5": (14.0, 68.0),
    "anthropic/claude-sonnet-5-5": (270.0, 1350.0),
}
TRANSCRIBE_MODEL = "openai/gpt-4o-mini-transcribe"
TRANSCRIBE_RUB = 0.5   # за голосовое, с запасом: цены распознавания в панели нет — считаем дороже, чем должно быть


class ModelError(Exception):
    pass


class OverBudget(ModelError):
    pass


class Gateway:
    def __init__(self, url: str, key: str, state_dir: str = "", day_rub: float = 300.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.enabled = bool(url and key)
        self.day_rub = day_rub
        self.http = httpx.AsyncClient(base_url=(url or "http://aigw.invalid") + "/",
                                      headers={"Authorization": f"Bearer {key}"},
                                      timeout=httpx.Timeout(90.0, connect=10.0), transport=transport)
        self.path = Path(state_dir) / "assistant_usage.json" if state_dir else None
        self.usage: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        try:
            if self.path and self.path.exists():
                for d, v in json.loads(self.path.read_text(encoding="utf-8")).items():
                    self.usage[d].update(v)
        except (OSError, ValueError):
            pass

    def today(self) -> dict:
        return self.usage[time.strftime("%Y-%m-%d")]

    def _count(self, model: str, tin: int, tout: int, rub: float | None = None):
        p_in, p_out = PRICES.get(model, (270.0, 1350.0))   # неизвестная модель — считаем как дорогую
        day = self.today()
        day["calls"] += 1
        day["in"] += tin
        day["out"] += tout
        day["rub"] = round(day["rub"] + (rub if rub is not None else (tin * p_in + tout * p_out) / 1e6), 4)
        try:
            if self.path:
                self.path.write_text(json.dumps(self.usage, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    async def chat(self, model: str, messages: list[dict], tools: list[dict] | None = None,
                   max_out: int = 8000) -> tuple[dict, dict]:
        """(сообщение модели, расход {in, out}). Claude в шлюзе сам включает размышления — без reasoning_effort=low
        их бюджет съедал ответ (26.10: продавец вернул пустой текст)."""
        if not self.enabled:
            raise ModelError("шлюз моделей не настроен")
        if self.today()["rub"] >= self.day_rub:
            raise OverBudget(f"дневной лимит {self.day_rub:.0f} ₽ исчерпан")
        body = {"model": model, "messages": messages, "max_completion_tokens": max_out, "reasoning_effort": "low"}
        if tools:
            body["tools"] = tools
        try:
            r = await self.http.post("chat/completions", json=body)
            d = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ModelError(f"шлюз: {type(e).__name__}") from e
        if r.status_code != 200:
            raise ModelError(f"шлюз {r.status_code}: {str(d)[:200]}")
        u = d.get("usage") or {}
        tin, tout = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        self._count(model, tin, tout)
        m = (d.get("choices") or [{}])[0].get("message") or {}
        if isinstance(m.get("content"), list):   # ответ блоками
            m["content"] = "".join(b.get("text", "") for b in m["content"] if isinstance(b, dict))
        return m, {"in": tin, "out": tout}

    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> str:
        """Голосовое → текст (Telegram шлёт OGG/Opus)."""
        if not self.enabled:
            raise ModelError("шлюз моделей не настроен")
        if self.today()["rub"] >= self.day_rub:
            raise OverBudget(f"дневной лимит {self.day_rub:.0f} ₽ исчерпан")
        try:
            r = await self.http.post("audio/transcriptions", data={"model": TRANSCRIBE_MODEL, "language": "ru"},
                                     files={"file": (filename, audio, "audio/ogg")})
            d = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ModelError(f"распознавание: {type(e).__name__}") from e
        if r.status_code != 200:
            raise ModelError(f"распознавание {r.status_code}: {str(d)[:200]}")
        self._count(TRANSCRIBE_MODEL, 0, 0, rub=TRANSCRIBE_RUB)
        return str(d.get("text") or "").strip()

    def report(self) -> dict:
        return {d: dict(v) for d, v in sorted(self.usage.items())[-14:]}
