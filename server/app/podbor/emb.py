"""Эмбеддинги групп каталога: кандидаты для арбитра («3 от правил + 3 от эмбеддингов»).

Замер на эталоне 08.10.2026 (550 фраз, /srv/podbor-lab/runs/2026-10-08-emb): верная группа среди 3+3 кандидатов — 96%,
у правил в тройке — 85%. Схема «правила уверены и их группа в пятёрке эмбеддингов, иначе арбитр»: 196 верно / 56 ошибок
против 142 / 69 у правил. Сами эмбеддинги группу не выбирают — только предлагают; выбирает модель.

Векторы групп считаются заранее (лаборатория; с 15.10.2026 gemini/gemini-embedding-2 через Timeweb AI Gateway, 3072 измерения —
до этого Giga-Embeddings-instruct-3B в Cloud.ru, у которого кончился баланс) и лежат файлом EMB_VECTORS (names, vecs);
вектор фразы — один запрос к шлюзу (EMB_BASE_URL, EMB_MODEL, EMB_API_KEY). Фраза уходит как есть, без префикса «Instruct:»
(он нужен был только Giga): векторы групп и веса замка обязаны быть посчитаны той же моделью, что стоит в EMB_MODEL. Нет ключа или файла — выключено, робот работает как раньше.

«Замок» (EMB_GATE, 12.10.2026): линейная модель на векторе фразы решает, деталь это или нет (НЕ / УТОЧ / МЕН / НЕТ).
Обучена на 550 размеченных фразах (лаборатория, emb_s7b_train.py); при проверке по частям эталона поднимает итог арбитра
с 80,2% до 82,0% (исправляет 12 ответов, портит 2). Отменяет только выбор группы арбитром, коды арбитра не трогает.
Нет файла — замка нет, всё как раньше.
"""
import base64
import json
import os
from pathlib import Path

import httpx

URL = os.environ.get("EMB_BASE_URL", "https://api.timeweb.ai/v1").rstrip("/")
MODEL = os.environ.get("EMB_MODEL", "gemini/gemini-embedding-2")
_STATE: dict = {}


class Emb:
    def __init__(self, names: list[str], vecs, key: str, transport: httpx.AsyncBaseTransport | None = None):
        import numpy as np
        self.np = np
        self.index = {n.lower(): i for i, n in enumerate(names)}
        self.vecs = np.asarray(vecs, dtype=np.float32)
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), transport=transport,
                                      headers={"Authorization": f"Bearer {key}"})
        self.gate_w = None        # (веса d×k, названия классов): см. load_gate
        self.gate_classes: list[str] = []

    def load_gate(self, path: Path) -> None:
        """Веса «замка»: класс 0 — «деталь», остальные — коды арбитра. Битый файл — замка нет."""
        try:
            z = self.np.load(path, allow_pickle=False)
            w, classes = self.np.asarray(z["W"], dtype=self.np.float32), json.loads(str(z["classes"]))
            if w.shape[1] == len(classes) and classes and classes[0] == "ДЕТАЛЬ":
                self.gate_w, self.gate_classes = w, classes
        except Exception:  # noqa: BLE001 — сломанный файл не должен ронять робота
            self.gate_w, self.gate_classes = None, []

    def gate(self, vec, rules_score: float, emb_sim: float) -> tuple[str, float] | None:
        """(код не-детали, насколько он сильнее «детали») или None, если замка нет. Признаки те же, что при обучении:
        вектор фразы, оценка лучшей группы правил, близость лучшей группы эмбеддингов."""
        if self.gate_w is None or vec is None:
            return None
        x = self.np.concatenate([self.np.asarray(vec, dtype=self.np.float32),
                                 self.np.asarray([rules_score, emb_sim], dtype=self.np.float32)])
        if x.shape[0] != self.gate_w.shape[0]:
            return None
        s = x @ self.gate_w
        k = int(self.np.argmax(s[1:])) + 1
        return self.gate_classes[k], float(s[k] - s[0])

    async def embed(self, texts: list[str]):
        """Векторы фраз (нормированные) или None, если сервис не ответил — тогда арбитр работает по-старому."""
        try:
            r = await self.http.post(f"{URL}/embeddings", json={"model": MODEL, "encoding_format": "base64",
                                                                 "input": list(texts)})
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
                gate = Path(os.environ.get("EMB_GATE", str(path.with_name("emb_gate.npz"))))
                if gate.exists():
                    _STATE["emb"].load_gate(gate)
            except Exception:  # noqa: BLE001 — сломанный файл не должен ронять робота
                _STATE["emb"] = None
    return _STATE["emb"]
