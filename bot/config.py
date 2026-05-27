"""Централизованная конфигурация"""

from dataclasses import dataclass
from typing import List
import os
from pathlib import Path

from dotenv import load_dotenv
from environ import Env


@dataclass
class BotConfig:
    """Конфигурация бота"""
    TOKEN: str
    API_BASE_URL: str
    API_TIMEOUT: int
    ADMIN_IDS: List[int]
    DEBUG: bool
    LOG_LEVEL: str

    # Rate limiting
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_TIME_WINDOW: int = 300  # 5 минут

    # Session
    SESSION_TIMEOUT: int = 24 * 60 * 60  # 24 часа

    @classmethod
    def from_env(cls):
        """Загрузить конфигурацию из .env"""
        load_dotenv()
        env = Env()

        BASE_DIR = Path(__file__).resolve().parent.parent
        env.read_env(str(BASE_DIR / ".env"))

        admin_ids_str = env.str("ADMIN_IDS", default="")
        admin_ids = [int(x.strip()) for x in admin_ids_str.split(',') if x.strip()]

        return cls(
            TOKEN=env.str("TOKEN"),
            API_BASE_URL=env.str("API_BASE_URL", default="http://127.0.0.1:8000"),
            API_TIMEOUT=env.int("API_TIMEOUT", default=10),
            ADMIN_IDS=admin_ids,
            DEBUG=env.bool("DEBUG", default=False),
            LOG_LEVEL=env.str("LOG_LEVEL", default="INFO"),
        )


# Использование
config = BotConfig.from_env()