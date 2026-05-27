"""Проверка здоровья приложения"""
import asyncio

from aiogram.filters import Command
from aiogram.types import Message

from bot.main import dp, api_request, API_BASE_URL


@dp.message(Command('health'))
async def health_check(message: Message):
    """Проверить доступность API"""
    try:
        status, _ = await asyncio.wait_for(
            api_request('GET', f"{API_BASE_URL}/api/", timeout=5),
            timeout=10
        )

        if status == 200:
            await message.answer('✅ API доступен и работает корректно')
        else:
            await message.answer(f'⚠️ API вернул ошибку: {status}')

    except asyncio.TimeoutError:
        await message.answer('⏱️ Таймаут при подключении к API')
    except Exception as e:
        await message.answer(f'❌ Ошибка: {str(e)}')