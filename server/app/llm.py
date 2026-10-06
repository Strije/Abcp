"""Языковая модель — агент Timeweb Cloud AI с OpenAI-совместимым API.

Модель только разбирает текст клиента (что нужно, какая сторона, что под вопросом). Номера, цены
и наличие она не придумывает и не видит: их дают Laximo и ABCP. Ключ — только на сервере (LLM_API_KEY).
Каждый вызов считается (llm_usage.json): токены на входе и выходе по дням — тариф помесячный.
"""
import json
import time
from collections import defaultdict
from pathlib import Path

import httpx

from .config import Settings


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.enabled = bool(settings.llm_url and settings.llm_key)
        self.http = httpx.AsyncClient(
            base_url=(settings.llm_url or "http://llm.invalid") + "/",
            headers={"Authorization": f"Bearer {settings.llm_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(40.0, connect=10.0), transport=transport)
        state = getattr(settings, "state_dir", "")
        self.usage_path = Path(state) / "llm_usage.json" if state else None
        self.usage: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        try:
            if self.usage_path and self.usage_path.exists():
                for d, v in json.loads(self.usage_path.read_text(encoding="utf-8")).items():
                    self.usage[d].update(v)
        except (OSError, ValueError):
            pass

    async def chat(self, system: str, user: str, max_tokens: int = 1200) -> str:
        """Один запрос: инструкция + сообщение → текст ответа модели."""
        if not self.enabled:
            raise LLMError("модель не подключена")
        body = {"model": "agent", "messages": [{"role": "system", "content": system},
                                               {"role": "user", "content": user}],
                "temperature": 0, "max_tokens": max_tokens, "stream": False}
        try:
            r = await self.http.post("chat/completions", json=body)
        except httpx.HTTPError as e:
            raise LLMError(f"модель не ответила: {type(e).__name__}") from e
        if r.status_code != 200:
            raise LLMError(f"модель ответила {r.status_code}: {r.text[:200]}")
        data = r.json()
        u = data.get("usage") or {}
        day = self.usage[time.strftime("%Y-%m-%d")]
        day["calls"] += 1
        day["in"] += int(u.get("prompt_tokens") or 0)
        day["out"] += int(u.get("completion_tokens") or 0)
        self._save()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("непонятный ответ модели") from e

    def _save(self):
        if not self.usage_path:
            return
        try:
            tmp = self.usage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.usage, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.usage_path)
        except OSError:
            pass

    def report(self) -> dict:
        month = time.strftime("%Y-%m")
        m = {"calls": 0, "in": 0, "out": 0}
        for d, v in self.usage.items():
            if d.startswith(month):
                for k in m:
                    m[k] += v.get(k, 0)
        return {"enabled": self.enabled, "today": dict(self.usage.get(time.strftime("%Y-%m-%d"), {})), "month": m}

    async def close(self):
        await self.http.aclose()
