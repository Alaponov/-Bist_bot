"""Rate limiting для защиты от спама"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict

import logger
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.main import GUEST_KEYBOARD, dp


class RateLimiter:
    """Ограничение количества запросов"""

    def __init__(self):
        self.requests: Dict[int, list] = defaultdict(list)

    def is_allowed(
            self,
            user_id: int,
            action: str,
            max_requests: int = 5,
            time_window: int = 60
    ) -> bool:
        """
        Проверяет, разрешен ли запрос

        Args:
            user_id: ID пользователя
            action: Название действия (login, create_order, etc)
            max_requests: Макс количество запросов
            time_window: Окно времени в секундах

        Returns:
            True если запрос разрешен, False если превышен лимит
        """
        key = f"{user_id}:{action}"
        now = datetime.now()
        cutoff = now - timedelta(seconds=time_window)

        # Удалить старые запросы
        self.requests[key] = [req_time for req_time in self.requests[key] if req_time > cutoff]

        # Проверить лимит
        if len(self.requests[key]) >= max_requests:
            logger.warning(f"⚠️ Rate limit exceeded for {user_id}:{action}")
            return False

        # Добавить текущий запрос
        self.requests[key].append(now)
        return True

    def get_remaining(self, user_id: int, action: str, max_requests: int = 5) -> int:
        """Возвращает количество оставшихся запросов"""
        key = f"{user_id}:{action}"
        return max(0, max_requests - len(self.requests[key]))


rate_limiter = RateLimiter()


# Использование в обработчиках:
@dp.message(lambda m: m.text == '🔐 Логин')
async def login_button(message: Message, state: FSMContext):
    telegram_id = message.from_user.id

    if not rate_limiter.is_allowed(telegram_id, 'login', max_requests=5, time_window=300):
        remaining = rate_limiter.get_remaining(telegram_id, 'login', max_requests=5)
        await message.answer(
            f'⏱️ Слишком много попыток логина. Осталось: {remaining} попыток через 5 минут.',
            reply_markup=GUEST_KEYBOARD
        )
        return

    # ... остальной код