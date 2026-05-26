from django.db.models.signals import post_save
from django.dispatch import receiver
import aiohttp
import asyncio
import os
from dotenv import load_dotenv

from .models import Order
from users.models import User

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DJANGO_URL = os.getenv('DJANGO_URL', 'http://127.0.0.1:8000')


@receiver(post_save, sender=Order)
def notify_admins_on_order_create(sender, instance, created, **kwargs):
    """Отправляет уведомление всем админам при создании нового заказа"""
    if created:
        # Запускаем асинхронную задачу
        asyncio.run(send_order_notification(instance))


async def send_order_notification(order):
    """Отправляет уведомление в Telegram"""
    try:
        # Получаем всех админов
        admins = User.objects.filter(role='admin', telegram_id__isnull=False)

        if not admins.exists():
            return

        # Формируем сообщение
        message_text = (
            f'🆕 Новый заказ!\n\n'
            f'👤 От: {order.user.username}\n'
            f'📝 Текст: {order.text}\n'
            f'📌 Статус: {order.status}\n'
            f'🆔 ID заказа: {order.id}\n\n'
            f'Нажмите /view_{order.id} для просмотра'
        )

        # Отправляем уведомление каждому админу
        async with aiohttp.ClientSession() as session:
            for admin in admins:
                await session.post(
                    f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
                    json={
                        'chat_id': admin.telegram_id,
                        'text': message_text,
                        'parse_mode': 'HTML'
                    }
                )
    except Exception as e:
        print(f'Error sending notification: {e}')
