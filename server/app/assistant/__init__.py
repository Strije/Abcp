"""Ассистент «Кирилл»: три агента поверх робота подбора (core), модели через шлюз Timeweb (models), Telegram (telegram)."""
from .core import Assistant
from .models import Gateway
from .telegram import KirillBot

__all__ = ["Assistant", "Gateway", "KirillBot"]
