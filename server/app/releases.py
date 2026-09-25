"""Сборки приложения для автообновления: CI кладёт APK, приложение спрашивает, есть ли новее.

В папке лежат `app-<versionCode>.apk` и `latest.json` с описанием последней сборки.
Поставить чужой APK поверх нашего Android не даст (другая подпись), поэтому токен загрузки
защищает только от мусора, а не от подмены приложения.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

KEEP = 3  # сколько последних сборок держать на диске


def latest(folder: Path) -> dict | None:
    try:
        return json.loads((folder / "latest.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def apk_path(folder: Path, version_code: int) -> Path:
    return folder / f"app-{version_code}.apk"


def save(folder: Path, data: bytes, version_code: int, version_name: str, notes: str) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    # Сначала во временный файл, потом переименование — приложение не скачает недописанный APK
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".part")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, apk_path(folder, version_code))

    info = {
        "versionCode": version_code,
        "versionName": version_name,
        "notes": notes,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    cur = latest(folder)
    # Перезапуск старой сборки не должен откатывать «последнюю»
    if not cur or cur.get("versionCode", 0) <= version_code:
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".part")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False)
        os.replace(tmp, folder / "latest.json")

    apks = sorted(folder.glob("app-*.apk"), key=lambda p: int(p.stem.split("-")[1]), reverse=True)
    for old in apks[KEEP:]:
        old.unlink(missing_ok=True)
    return info
