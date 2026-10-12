"""Картинки схем узлов через наш сервер.

img.laximo.ru отвечает только из России: с VPN и из-за границы картинка не грузится (12.10.2026: в «Подробностях» и в ссылке
клиенту схем не было видно, с ПК пользователя запрос к img.laximo.ru висел). Наш сервер в Москве картинку получает — отдаём её
сами: /v1/podbor/scheme/<номер>. Номер — подпись адреса (TOKEN_SECRET), сам адрес Laximo лежит у нас на диске: в адресе
номер нашей учётки Laximo (s=…) и ключ, клиенту их видеть незачем. Через нас можно получить только схему, которую выдал подбор.

Первая версия (12.10) клала в ссылку сам адрес в base64 с подписью — его можно раскодировать. Такие ссылки уже ушли клиентам,
поэтому они по-прежнему открываются.
"""
import base64
import hashlib
import hmac
import os
import re
from pathlib import Path
from urllib.parse import urlparse

HOSTS = ("img.laximo.ru", "ws.laximo.ru")
PATH = "/v1/podbor/scheme/"
_ID = re.compile(r"[0-9a-f]{24}")
_DIR: list[Path] = []   # папка с адресами схем; задаёт сервер (init), иначе STATE_DIR


def init(state_dir: str | Path):
    # Папку создаёт сам сервис при старте: создай её первым ночной прогон от root — сервис не смог бы туда писать
    _DIR[:] = [Path(state_dir) / "podbor" / "scheme_ids"]
    try:
        _DIR[0].mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _folder() -> Path:
    return _DIR[0] if _DIR else Path(os.environ.get("STATE_DIR", "/var/lib/avtodrug-api")) / "podbor" / "scheme_ids"


def _secret() -> bytes:
    return os.environ.get("TOKEN_SECRET", "").encode()


def _sig(url: str) -> str:
    return hmac.new(_secret(), url.encode(), hashlib.sha256).hexdigest()


def token(url: str) -> str:
    """Ссылка первой версии: адрес в base64 и подпись. Только чтобы открывались уже отправленные."""
    raw = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f"{raw}.{_sig(url)[:20]}"


def short(url: str) -> str | None:
    """Номер схемы: подпись адреса (не подобрать и не раскодировать), адрес — в файле у нас. Не записали — None."""
    sid = _sig(url)[:24]
    f = _folder() / (sid + ".url")
    try:
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(url, encoding="utf-8")
    except OSError:
        return None
    return sid


def resolve(tok: str) -> str | None:
    """Номер или старый токен → адрес картинки Laximo; подделка или чужой адрес — None."""
    if not _secret():
        return None
    if _ID.fullmatch(tok):
        try:
            url = (_folder() / (tok + ".url")).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        good = _sig(url)[:24]
    else:
        raw, _, sig = tok.partition(".")
        try:
            url = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        except (ValueError, UnicodeDecodeError):
            return None
        tok, good = sig, _sig(url)[:20]
    if not hmac.compare_digest(tok, good) or urlparse(url).hostname not in HOSTS:
        return None
    return url


def link(url: str, absolute: bool = True) -> str:
    """Адрес картинки для клиента и страницы подбора. Без секрета (тесты, разработка) — как есть."""
    if not url or not _secret() or urlparse(url).hostname not in HOSTS:
        return url
    base = os.environ.get("PUBLIC_URL", "https://109.73.199.217").rstrip("/") if absolute else ""
    return base + PATH + (short(url) or token(url))
