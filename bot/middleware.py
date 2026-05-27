"""Middleware для логирования и обработки ошибок"""
import logger
from aiogram import BaseMiddleware
from aiogram.types import Update
import time


class LoggingMiddleware(BaseMiddleware):
    """Логирование всех запросов"""

    async def __call__(self, handler, event: Update, data):
        start_time = time.time()
        user_id = None

        # Определить тип события
        if event.message:
            user_id = event.message.from_user.id
            logger.info(f"📨 Message from {user_id}: {event.message.text[:50]}")
        elif event.callback_query:
            user_id = event.callback_query.from_user.id
            logger.info(f"🔘 Callback from {user_id}: {event.callback_query.data}")

        try:
            result = await handler(event, data)
            elapsed = time.time() - start_time
            logger.debug(f"✅ Handler completed in {elapsed:.2f}s")
            return result

        except Exception as e:
            logger.error(f"❌ Error in handler: {str(e)}", exc_info=True)
            if event.message:
                await event.message.answer("❌ Ошибка при обработке запроса. Попробуйте позже.")
            elif event.callback_query:
                await event.callback_query.answer("❌ Ошибка", show_alert=True)
            raise


class ErrorHandlingMiddleware(BaseMiddleware):
    """Обработка глобальных ошибок"""

    async def __call__(self, handler, event: Update, data):
        try:
            return await handler(event, data)
        except Exception as e:
            logger.error(f"Critical error: {str(e)}", exc_info=True)