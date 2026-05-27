import asyncio

import logger
from aiogram import Bot

from orders.models import Order


async def send_notification_to_user(
        bot: Bot,
        telegram_id: int,
        notification_type: str,
        message: str,
        order_id: int = None
):
    """Отправить уведомление пользователю"""
    try:
        await bot.send_message(
            chat_id=telegram_id,
            text=message,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}")


# Использование в API
async def notify_order_completion(order_id: int, bot: Bot):
    order = Order.objects.get(id=order_id)
    message = f"✅ Ваш заказ #{order_id} выполнен!"

    if order.user.telegram_id:
        await send_notification_to_user(
            bot,
            order.user.telegram_id,
            'order_completed',
            message,
            order_id
        )