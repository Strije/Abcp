"""Сторож сервера Автодруг: раз в 5 минут (systemd timer) проверяет, что всё живо, и пишет в Telegram
только при смене состояния («сломалось» / «починилось»). Раз в сутки — копия данных сервиса.

Проверки: API отвечает, место на диске, срок HTTPS-сертификата, VPN-подписка (соседний сервис на этом VPS).
Настройки — из /etc/avtodrug-api.env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WATCH_SUBSCRIPTION_URL).
"""
import json
import os
import shutil
import ssl
import subprocess
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

STATE = Path("/var/lib/avtodrug-watchdog/state.json")
DATA = Path("/var/lib/avtodrug-api")
BACKUPS = Path("/var/backups/avtodrug-api")
KEEP_BACKUPS = 14
CERT = Path("/root/cert/le/fullchain.pem")


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
    if not CERT.exists():
        return None
    out = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in", str(CERT)], capture_output=True, text=True).stdout
    try:
        end = datetime.strptime(out.strip().split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except (IndexError, ValueError):
        return "Не удалось прочитать срок сертификата HTTPS"
    days = (end - datetime.now(timezone.utc)).days
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
            httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text}, timeout=15)
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
