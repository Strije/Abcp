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
    # articles/info (картинки, достоверные аналоги) на тарифе — 10 запросов в СУТКИ на весь магазин.
    # Пока ABCP не поднимет лимит, держим выключенным, иначе он сгорает за минуту.
    articles_info_per_day: int = 0
    # Профиль цен для гостей (поиск без входа). Пусто — гостевой поиск выключен.
    guest_profile_id: str = ""
    # Автообновление: куда CI кладёт APK и чем подписывает загрузку. Пустой токен — загрузка выключена.
    app_dir: str = "/var/lib/avtodrug-api/app"
    app_upload_token: str = ""
    # Laximo (подбор по авто) — доступ только здесь, в приложении его нет
    laximo_user: str = ""
    laximo_pass: str = ""
    # Заявки на доступ к API — менеджерам в Telegram. Пусто — заявки копятся без уведомлений.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Push через RuStore: ID проекта и сервисный токен из Консоли RuStore. Пусто — push выключен.
    rustore_project_id: str = ""
    rustore_push_token: str = ""
    # Новый домен vkpns-dg.rustore.ru требует корневой сертификат Минцифры на сервере
    rustore_push_host: str = "https://vkpns.rustore.ru"
    # Как часто смотреть изменённые заказы, секунд (0 — не следить)
    order_watch_interval: int = 180
    # Где хранить очередь заявок
    state_dir: str = "/var/lib/avtodrug-api"


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
        articles_info_per_day=int(os.environ.get("ARTICLES_INFO_PER_DAY", "0") or 0),
        guest_profile_id=os.environ.get("GUEST_PROFILE_ID", "").strip(),
        app_dir=os.environ.get("APP_DIR", "/var/lib/avtodrug-api/app"),
        app_upload_token=os.environ.get("APP_UPLOAD_TOKEN", "").strip(),
        laximo_user=os.environ.get("LAXIMO_USER", "").strip(),
        laximo_pass=os.environ.get("LAXIMO_PASS", "").strip(),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
        state_dir=os.environ.get("STATE_DIR", "/var/lib/avtodrug-api"),
        rustore_project_id=os.environ.get("RUSTORE_PROJECT_ID", "").strip(),
        rustore_push_token=os.environ.get("RUSTORE_PUSH_TOKEN", "").strip(),
        rustore_push_host=os.environ.get("RUSTORE_PUSH_HOST", "https://vkpns.rustore.ru").strip(),
        order_watch_interval=int(os.environ.get("ORDER_WATCH_INTERVAL", "180") or 0),
    )
