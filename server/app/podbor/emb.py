"""Эмбеддинги групп каталога: кандидаты для арбитра («3 от правил + 3 от эмбеддингов»).

Замер на эталоне 08.10.2026 (550 фраз, /srv/podbor-lab/runs/2026-10-08-emb): верная группа среди 3+3 кандидатов — 96%,
у правил в тройке — 85%. Схема «правила уверены и их группа в пятёрке эмбеддингов, иначе арбитр»: 196 верно / 56 ошибок
против 142 / 69 у правил. Сами эмбеддинги группу не выбирают — только предлагают; выбирает модель.

Векторы групп считаются заранее (лаборатория, Giga-Embeddings-instruct-3B) и лежат файлом EMB_VECTORS (names, vecs);
вектор фразы — один запрос к Cloud.ru (EMB_API_KEY). Нет ключа или файла — выключено, робот работает как раньше.
"""
import base64
import json
import os
from pathlib import Path

import httpx

URL = os.environ.get("EMB_BASE_URL", "https://foundation-models.api.cloud.ru/v1").rstrip("/")
MODEL = os.environ.get("EMB_MODEL", "ai-sage/Giga-Embeddings-instruct-3B")
QI = "Дана фраза клиента магазина автозапчастей. Найди группу деталей каталога, о которой идёт речь"
_STATE: dict = {}


class Emb:
    def __init__(self, names: list[str], vecs, key: str, transport: httpx.AsyncBaseTransport | None = None):
        import numpy as np
        self.np = np
        self.index = {n.lower(): i for i, n in enumerate(names)}
        self.vecs = np.asarray(vecs, dtype=np.float32)
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), transport=transport,
                                      headers={"Authorization": f"Bearer {key}"})

    async def embed(self, texts: list[str]):
        """Векторы фраз (нормированные) или None, если сервис не ответил — тогда арбитр работает по-старому."""
        try:
            r = await self.http.post(f"{URL}/embeddings", json={"model": MODEL, "encoding_format": "base64",
                                                                 "input": [f"Instruct: {QI}\nQuery: {t}" for t in texts]})
            data = sorted(r.json()["data"], key=lambda x: x["index"]) if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError, KeyError):
            data = None
        if not data:
            return None
        np = self.np
        out = []
        for x in data:
            e = x["embedding"]
            v = np.frombuffer(base64.b64decode(e), dtype=np.float32) if isinstance(e, str) else np.asarray(e, np.float32)
            out.append(v / (np.linalg.norm(v) or 1.0))
        return out

    def top(self, vec, names: list[str], k: int = 5) -> list[tuple[str, float]]:
        """Ближайшие группы среди названий дерева этой машины (без вектора — не участвуют)."""
        known = [(n, self.index[n.lower()]) for n in dict.fromkeys(names) if n.lower() in self.index]
        if not known or vec is None:
            return []
        s = self.vecs[[i for _, i in known]] @ vec
        order = self.np.argsort(-s)[:k]
        return [(known[j][0], float(s[j])) for j in order]


def get() -> Emb | None:
    """Один экземпляр на процесс; None — выключено (нет ключа, файла векторов или numpy)."""
    if "emb" not in _STATE:
        _STATE["emb"] = None
        key = os.environ.get("EMB_API_KEY", "").strip()
        path = Path(os.environ.get("EMB_VECTORS", "/var/lib/avtodrug-api/podbor/emb_groups.npz"))
        if key and path.exists():
            try:
                import numpy as np
                z = np.load(path, allow_pickle=False)
                _STATE["emb"] = Emb(json.loads(str(z["names"])), z["vecs"], key)
            except Exception:  # noqa: BLE001 — сломанный файл не должен ронять робота
                _STATE["emb"] = None
    return _STATE["emb"]
