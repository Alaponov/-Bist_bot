"""Менеджер сессий с автоочисткой"""
from collections import defaultdict
from datetime import datetime, timedelta
import asyncio
from typing import Dict, Optional

import logger

from bot.main import dp, bot


class SessionManager:
    """Менеджер сессий с timeout и очисткой"""

    # Параметры сессии
    SESSION_TIMEOUT = 24 * 60 * 60  # 24 часа
    CLEANUP_INTERVAL = 60 * 60  # Очистка каждый час

    def __init__(self):
        self.tokens: Dict[int, str] = {}
        self.roles: Dict[int, str] = {}
        self.order_mode: Dict[int, bool] = defaultdict(bool)
        self.delete_mode: Dict[int, bool] = defaultdict(bool)
        self.last_activity: Dict[int, datetime] = {}
        self.created_at: Dict[int, datetime] = {}
        self.cleanup_task = None

    def start_cleanup(self):
        """Запустить фоновую очистку"""
        self.cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("✅ Session cleanup started")

    async def _cleanup_loop(self):
        """Фоновая очистка старых сессий"""
        while True:
            try:
                await asyncio.sleep(self.CLEANUP_INTERVAL)
                self._cleanup_expired_sessions()
            except Exception as e:
                logger.error(f"Cleanup error: {str(e)}")

    def _cleanup_expired_sessions(self):
        """Удаляет истекшие сессии"""
        now = datetime.now()
        expired_users = []

        for user_id, created_time in self.created_at.items():
            if (now - created_time).total_seconds() > self.SESSION_TIMEOUT:
                expired_users.append(user_id)

        for user_id in expired_users:
            self.clear_session(user_id)
            logger.info(f"🧹 Session expired for user {user_id}")

    def set_session(self, telegram_id: int, token: str, role: str) -> None:
        """Сохраняет сессию пользователя"""
        now = datetime.now()
        self.tokens[telegram_id] = token
        self.roles[telegram_id] = role
        self.last_activity[telegram_id] = now
        self.created_at[telegram_id] = now
        logger.info(f"✅ Session created for user {telegram_id} ({role})")

    def get_token(self, telegram_id: int) -> Optional[str]:
        """Получает токен и проверяет timeout"""
        if telegram_id not in self.tokens:
            return None

        # Проверить timeout
        last_activity = self.last_activity.get(telegram_id, datetime.now())
        if (datetime.now() - last_activity).total_seconds() > self.SESSION_TIMEOUT:
            self.clear_session(telegram_id)
            return None

        self.update_activity(telegram_id)
        return self.tokens.get(telegram_id)

    def get_role(self, telegram_id: int) -> Optional[str]:
        """Получает роль пользователя"""
        return self.roles.get(telegram_id)

    def is_authenticated(self, telegram_id: int) -> bool:
        """Проверяет авторизацию"""
        return self.get_token(telegram_id) is not None

    def clear_session(self, telegram_id: int) -> None:
        """Удаляет сессию пользователя"""
        self.tokens.pop(telegram_id, None)
        self.roles.pop(telegram_id, None)
        self.order_mode[telegram_id] = False
        self.delete_mode[telegram_id] = False
        self.last_activity.pop(telegram_id, None)
        self.created_at.pop(telegram_id, None)
        logger.info(f"🧹 Session cleared for user {telegram_id}")

    def update_activity(self, telegram_id: int) -> None:
        """Обновляет время последней активности"""
        self.last_activity[telegram_id] = datetime.now()

    def stop_cleanup(self):
        """Остановить фоновую очистку"""
        if self.cleanup_task:
            self.cleanup_task.cancel()
            logger.info("⛔ Session cleanup stopped")


# Инициализация в main.py
session_manager = SessionManager()


# Запустить при старте бота
async def main():
    session_manager.start_cleanup()
    logger.info("🤖 Bot started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


# Остановить при выходе
if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        session_manager.stop_cleanup()
        logger.info("🤖 Bot stopped")