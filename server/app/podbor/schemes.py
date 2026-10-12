"""Картинки схем узлов через наш сервер.

img.laximo.ru отвечает только из России: с VPN и из-за границы картинка не грузится (12.10.2026: в «Подробностях» и в ссылке
клиенту схем не было видно, с ПК пользователя запрос к img.laximo.ru висел). Наш сервер в Москве картинку получает — отдаём её
сами: /v1/podbor/scheme/<токен>. Токен — адрес картинки и подпись (TOKEN_SECRET), поэтому через нас можно получить только
схему, которую выдал подбор, а не любой адрес. Заодно клиент не видит номер нашей учётки Laximo в ссылке.
"""
import base64
import hashlib
import hmac
import os
from urllib.parse import urlparse

HOSTS = ("img.laximo.ru", "ws.laximo.ru")
PATH = "/v1/podbor/scheme/"


def _secret() -> bytes:
    return os.environ.get("TOKEN_SECRET", "").encode()


def token(url: str) -> str:
    raw = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), url.encode(), hashlib.sha256).hexdigest()[:20]
    return f"{raw}.{sig}"


def resolve(tok: str) -> str | None:
    """Токен → адрес картинки Laximo; подделка или чужой адрес — None."""
    raw, _, sig = tok.partition(".")
    try:
        url = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    good = hmac.new(_secret(), url.encode(), hashlib.sha256).hexdigest()[:20]
    if not _secret() or not hmac.compare_digest(sig, good) or urlparse(url).hostname not in HOSTS:
        return None
    return url


def link(url: str, absolute: bool = True) -> str:
    """Адрес картинки для клиента и страницы подбора. Без секрета (тесты, разработка) — как есть."""
    if not url or not _secret() or urlparse(url).hostname not in HOSTS:
        return url
    base = os.environ.get("PUBLIC_URL", "https://109.73.199.217").rstrip("/") if absolute else ""
    return base + PATH + token(url)
