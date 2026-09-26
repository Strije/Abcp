"""Сторож сервера Автодруг: раз в 5 минут (systemd timer) проверяет, что всё живо, и пишет в Telegram
только при смене состояния («сломалось» / «починилось»). Раз в сутки — копия данных сервиса.

Проверки: API отвечает, место на диске, срок HTTPS-сертификата, VPN-подписка (соседний сервис на этом VPS).
Настройки — из /etc/avtodrug-api.env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_API, PUBLIC_URL,
WATCH_SUBSCRIPTION_URL).
"""
import json
import os
import shutil
import socket
import ssl
import tarfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

STATE = Path("/var/lib/avtodrug-watchdog/state.json")
DATA = Path("/var/lib/avtodrug-api")
BACKUPS = Path("/var/backups/avtodrug-api")
KEEP_BACKUPS = 14
TG_API = os.environ.get("TELEGRAM_API", "https://api.telegram.org").rstrip("/")


def check_api() -> str | None:
    try:
        r = httpx.get("http://127.0.0.1:8090/health", timeout=10)
        return None if r.status_code == 200 else f"API отвечает {r.status_code}"
    except httpx.HTTPError as e:
        return f"API не отвечает ({type(e).__name__})"


def check_disk() -> str | None:
    u = shutil.disk_usage("/")
    used = u.used / u.total * 100
    return f"Диск заполнен на {used:.0f}%" if used > 90 else None


def check_cert() -> str | None:
    """Сертификат смотрим снаружи, как его видит приложение: по адресу PUBLIC_URL (где бы он ни лежал на диске)."""
    url = urlsplit(os.environ.get("PUBLIC_URL", ""))
    if url.scheme != "https" or not url.hostname:
        return None
    try:
        with socket.create_connection((url.hostname, url.port or 443), timeout=15) as raw, \
                ssl.create_default_context().wrap_socket(raw, server_hostname=url.hostname) as tls:
            end = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
    except (OSError, KeyError, ValueError) as e:  # ssl.SSLError — тоже OSError (в т. ч. истёкший сертификат)
        return f"HTTPS снаружи не работает ({type(e).__name__})"
    days = int((end - time.time()) // 86400)
    return f"Сертификат HTTPS истекает через {days} дн. — автопродление не сработало" if days < 14 else None


def check_subscription() -> str | None:
    url = os.environ.get("WATCH_SUBSCRIPTION_URL", "")
    if not url:
        return None
    try:
        r = httpx.get(url, timeout=15, verify=ssl.create_default_context())
        return None if r.status_code == 200 and len(r.content) > 100 else f"VPN-подписка отвечает {r.status_code}"
    except httpx.HTTPError as e:
        return f"VPN-подписка не отвечает ({type(e).__name__})"


def telegram(text: str):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat:
        try:
            httpx.post(f"{TG_API}/bot{token}/sendMessage", json={"chat_id": chat, "text": text}, timeout=15)
        except httpx.HTTPError:
            pass


def backup(state: dict):
    """Раз в сутки: архив данных сервиса (очередь заявок, push-токены, статусы). Храним 14 последних."""
    today = time.strftime("%Y-%m-%d")
    if state.get("backup") == today or not DATA.exists():
        return
    BACKUPS.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUPS, 0o700)
    with tarfile.open(BACKUPS / f"data-{today}.tar.gz", "w:gz") as t:
        for f in DATA.glob("*.json"):
            t.add(f, arcname=f.name)
    for old in sorted(BACKUPS.glob("data-*.tar.gz"))[:-KEEP_BACKUPS]:
        old.unlink()
    state["backup"] = today


def main():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(STATE.read_text())
    except (FileNotFoundError, ValueError):
        state = {}
    problems = {name: msg for name, fn in [("api", check_api), ("disk", check_disk), ("cert", check_cert),
                                           ("vpn", check_subscription)] if (msg := fn())}
    was = state.get("problems", {})
    for name, msg in problems.items():
        if name not in was:
            telegram(f"🔴 Сервер Автодруг: {msg}")
    for name, msg in was.items():
        if name not in problems:
            telegram(f"🟢 Сервер Автодруг: снова в порядке — {msg.split(' (')[0].lower()}")
    state["problems"] = problems
    backup(state)
    STATE.write_text(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
