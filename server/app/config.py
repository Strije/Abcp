"""Настройки сервера — только из переменных окружения (файл /etc/avtodrug-api.env на сервере).

Админский доступ ABCP хранится ТОЛЬКО здесь, в приложение он не попадает никогда.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    abcp_host: str
    admin_login: str
    admin_md5: str
    token_secret: bytes
    token_ttl: int = 30 * 24 * 3600  # 30 дней, потом приложение просто войдёт заново


def load() -> Settings:
    secret = os.environ.get("TOKEN_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("TOKEN_SECRET не задан или короче 32 символов")
    login = os.environ.get("ABCP_ADMIN_LOGIN", "")
    md5 = os.environ.get("ABCP_ADMIN_MD5", "")
    if not login or not md5:
        raise RuntimeError("ABCP_ADMIN_LOGIN / ABCP_ADMIN_MD5 не заданы")
    return Settings(
        abcp_host=os.environ.get("ABCP_HOST", "https://id25202.public.api.abcp.ru").rstrip("/"),
        admin_login=login,
        admin_md5=md5,
        token_secret=secret.encode(),
    )
