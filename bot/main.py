import asyncio
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import aiohttp

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from dotenv import load_dotenv
from environ import Env

from states import LoginState

load_dotenv()
env = Env()

BASE_DIR = Path(__file__).resolve().parent.parent
env.read_env(str(BASE_DIR / ".env"))

TOKEN = env.str("TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# =========================
# КЛАВИАТУРЫ
# =========================

guest_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text='🔐 Логин')]],
    resize_keyboard=True
)

customer_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text='👤 Профиль')],
        [KeyboardButton(text='🛒 Сделать заказ')],
        [KeyboardButton(text='📦 Мои заказы')],
        [KeyboardButton(text='❌ Удалить заказ')],
        [KeyboardButton(text='🚪 Выйти')],
    ],
    resize_keyboard=True
)

admin_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text='👤 Профиль')],
        [KeyboardButton(text='📦 Все заказы')],
        [KeyboardButton(text='📊 Статистика')],
        [KeyboardButton(text='🚪 Выйти')],
    ],
    resize_keyboard=True
)

admin_filter_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text='👤 По пользователю')],
        [KeyboardButton(text='📅 По дате')],
        [KeyboardButton(text='🔄 Без сортировки')],
        [KeyboardButton(text='📦 Все заказы')],
    ],
    resize_keyboard=True
)

# =========================
# ХРАНИЛИЩЕ
# =========================

user_tokens = {}
user_roles = {}
order_mode = {}
delete_mode = {}
filter_mode = {}


# =========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================

def format_date(date_string):
    """Форматирует дату ISO в удобный формат"""
    try:
        dt = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
        return dt.strftime('%d.%m.%Y %H:%M')
    except:
        return date_string


def sort_orders_by_user(orders):
    """Группирует заказы по пользователям"""
    grouped = defaultdict(list)
    for order in orders:
        user_info = order.get('user_details', {})
        username = user_info.get('username', 'Unknown')
        grouped[username].append(order)
    return grouped


def sort_orders_by_date(orders):
    """Сортирует заказы по дате (новые первыми)"""
    return sorted(orders, key=lambda x: x.get('created_at', ''), reverse=True)


# =========================
# START
# =========================

@dp.message(Command('start'))
async def start_handler(message: Message, state: FSMContext):
    telegram_id = message.from_user.id

    user_tokens.pop(telegram_id, None)
    user_roles.pop(telegram_id, None)
    order_mode[telegram_id] = False
    delete_mode[telegram_id] = False
    filter_mode.pop(telegram_id, None)

    await state.clear()

    await message.answer('Добро пожаловать 👋', reply_markup=guest_keyboard)


# =========================
# ЛОГИН
# =========================

@dp.message(lambda m: m.text == '🔐 Логин')
async def login_button(message: Message, state: FSMContext):
    await state.set_state(LoginState.username)
    await message.answer('Введите логин')


@dp.message(LoginState.username)
async def username_handler(message: Message, state: FSMContext):
    await state.update_data(username=message.text)
    await state.set_state(LoginState.password)
    await message.answer('Введите пароль')


@dp.message(LoginState.password)
async def password_handler(message: Message, state: FSMContext):
    data = await state.get_data()

    username = data.get('username')
    password = message.text
    telegram_id = message.from_user.id

    async with aiohttp.ClientSession() as session:
        async with session.post(
                'http://127.0.0.1:8000/api/users/login/',
                json={
                    'username': username,
                    'password': password,
                    'telegram_id': telegram_id,
                }
        ) as response:

            if response.status == 200:
                result = await response.json()
                user_tokens[telegram_id] = result['token']
                user_role = result.get('role', 'customer')
                user_roles[telegram_id] = user_role

                await state.clear()

                # Выбираем клавиатуру в зависимости от роли
                keyboard = admin_keyboard if user_role == 'admin' else customer_keyboard

                await message.answer(
                    'Вы успешно вошли ✅',
                    reply_markup=keyboard
                )
            else:
                await message.answer('Неверный логин или пароль ❌')


# =========================
# ПРОФИЛЬ
# =========================

@dp.message(lambda m: m.text == '👤 Профиль')
async def profile_handler(message: Message):
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)

    if not token:
        await message.answer('Сначала войдите 🔐')
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(
                'http://127.0.0.1:8000/api/users/profile/',
                headers={'Authorization': f'Token {token}'}
        ) as response:

            if response.status == 200:
                result = await response.json()
                role = user_roles.get(telegram_id, 'customer')
                role_text = 'Администратор' if role == 'admin' else 'Заказчик'

                await message.answer(
                    f'👤 Ваш профиль\n\n'
                    f'Username: {result["username"]}\n'
                    f'Telegram ID: {telegram_id}\n'
                    f'Роль: {role_text}\n'
                    f'Заказов: {result["orders_count"]}'
                )
            else:
                await message.answer('Ошибка профиля ❌')


# =========================
# МОИ ЗАКАЗЫ (для customer)
# =========================

@dp.message(lambda m: m.text == '📦 Мои заказы')
async def my_orders_handler(message: Message):
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)

    if not token:
        await message.answer('Сначала войдите 🔐')
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(
                'http://127.0.0.1:8000/api/orders/',
                headers={'Authorization': f'Token {token}'}
        ) as response:

            if response.status == 200:
                data = await response.json()
                orders = data.get('results', data)

                if not orders:
                    await message.answer('У вас нет заказов 📭')
                    return

                text = '📦 Ваши заказы:\n\n'

                for order in orders:
                    created_at = format_date(order.get('created_at', 'N/A'))

                    text += (
                        f'🆔 ID: {order["id"]}\n'
                        f'📝 {order["text"]}\n'
                        f'📌 {order["status"]}\n'
                        f'📅 {created_at}\n\n'
                    )

                await message.answer(text)
            else:
                error_text = await response.text()
                await message.answer(f'Ошибка ❌\n{error_text}')


# =========================
# ФИЛЬТРЫ ДЛЯ АДМИНА
# =========================

@dp.message(lambda m: m.text == '👤 По пользователю')
async def filter_by_user(message: Message):
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)
    role = user_roles.get(telegram_id)

    if not token or role != 'admin':
        await message.answer('У вас нет доступа 🔒')
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(
                'http://127.0.0.1:8000/api/orders/',
                headers={'Authorization': f'Token {token}'}
        ) as response:

            if response.status == 200:
                data = await response.json()
                orders = data.get('results', data)

                if not orders:
                    await message.answer('Заказов нет 📭')
                    return

                grouped = sort_orders_by_user(orders)
                text = '📦 Заказы по пользователям:\n\n'

                for username in sorted(grouped.keys()):
                    user_orders = grouped[username]
                    text += f'👤 {username} ({len(user_orders)} заказов)\n'

                    for order in user_orders:
                        created_at = format_date(order.get('created_at', 'N/A'))
                        text += (
                            f'  🆔 ID: {order["id"]}\n'
                            f'  📝 {order["text"]}\n'
                            f'  📌 {order["status"]}\n'
                            f'  📅 {created_at}\n\n'
                        )

                await message.answer(text, reply_markup=admin_filter_keyboard)
            else:
                await message.answer('Ошибка ❌')


@dp.message(lambda m: m.text == '📅 По дате')
async def filter_by_date(message: Message):
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)
    role = user_roles.get(telegram_id)

    if not token or role != 'admin':
        await message.answer('У вас нет доступа 🔒')
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(
                'http://127.0.0.1:8000/api/orders/',
                headers={'Authorization': f'Token {token}'}
        ) as response:

            if response.status == 200:
                data = await response.json()
                orders = data.get('results', data)

                if not orders:
                    await message.answer('Заказов нет 📭')
                    return

                sorted_orders = sort_orders_by_date(orders)
                text = '📦 Заказы по дате (новые первыми):\n\n'

                for order in sorted_orders:
                    user_info = order.get('user_details', {})
                    username = user_info.get('username', 'Unknown')
                    created_at = format_date(order.get('created_at', 'N/A'))

                    text += (
                        f'🆔 ID: {order["id"]}\n'
                        f'👤 Пользователь: {username}\n'
                        f'📝 {order["text"]}\n'
                        f'📌 {order["status"]}\n'
                        f'📅 {created_at}\n\n'
                    )

                await message.answer(text, reply_markup=admin_filter_keyboard)
            else:
                await message.answer('Ошибка ❌')


# =========================
# ВСЕ ЗАКАЗЫ (для admin)
# =========================

@dp.message(lambda m: m.text == '📦 Все заказы')
async def all_orders_handler(message: Message):
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)
    role = user_roles.get(telegram_id)

    if not token or role != 'admin':
        await message.answer('У вас нет доступа 🔒')
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(
                'http://127.0.0.1:8000/api/orders/',
                headers={'Authorization': f'Token {token}'}
        ) as response:

            if response.status == 200:
                data = await response.json()
                orders = data.get('results', data)

                if not orders:
                    await message.answer('Заказов нет 📭')
                    return

                text = '📦 Все заказы в системе:\n\n'

                for order in orders:
                    user_info = order.get('user_details', {})
                    username = user_info.get('username', 'Unknown')
                    created_at = format_date(order.get('created_at', 'N/A'))

                    text += (
                        f'🆔 ID: {order["id"]}\n'
                        f'👤 Пользователь: {username}\n'
                        f'📝 {order["text"]}\n'
                        f'📌 {order["status"]}\n'
                        f'📅 {created_at}\n\n'
                    )

                await message.answer(text, reply_markup=admin_filter_keyboard)
            else:
                error_text = await response.text()
                await message.answer(f'Ошибка ❌\n{error_text}')


@dp.message(lambda m: m.text == '🔄 Без сортировки')
async def no_sort_handler(message: Message):
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)
    role = user_roles.get(telegram_id)

    if not token or role != 'admin':
        await message.answer('У вас нет доступа 🔒')
        return

    await message.answer(
        'Сортировка отключена',
        reply_markup=admin_keyboard
    )


# =========================
# СТАТИСТИКА (для admin)
# =========================

@dp.message(lambda m: m.text == '📊 Статистика')
async def statistics_handler(message: Message):
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)
    role = user_roles.get(telegram_id)

    if not token or role != 'admin':
        await message.answer('У вас нет доступа 🔒')
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(
                'http://127.0.0.1:8000/api/orders/',
                headers={'Authorization': f'Token {token}'}
        ) as response:

            if response.status == 200:
                data = await response.json()
                orders = data.get('results', data)

                total_orders = len(orders)
                new_orders = len([o for o in orders if o['status'] == 'NEW'])
                in_progress = len([o for o in orders if o['status'] == 'IN_PROGRESS'])
                completed = len([o for o in orders if o['status'] == 'COMPLETED'])

                await message.answer(
                    f'📊 Статистика:\n\n'
                    f'📦 Всего заказов: {total_orders}\n'
                    f'🆕 Новых: {new_orders}\n'
                    f'⚙️ В процессе: {in_progress}\n'
                    f'✅ Завершено: {completed}'
                )
            else:
                await message.answer('Ошибка статистики ❌')


# =========================
# СДЕЛАТЬ ЗАКАЗ (для customer)
# =========================

@dp.message(lambda m: m.text == '🛒 Сделать заказ')
async def order_button(message: Message):
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)

    if not token:
        await message.answer('Сначала войдите 🔐')
        return

    order_mode[telegram_id] = True
    await message.answer('Отправьте текст заказа 🛒')


# =========================
# СОЗДАНИЕ ЗАКАЗА
# =========================

@dp.message(lambda m: order_mode.get(m.from_user.id, False))
async def create_order(message: Message):
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)

    async with aiohttp.ClientSession() as session:
        async with session.post(
                'http://127.0.0.1:8000/api/orders/',
                json={
                    'text': message.text,
                    'status': 'NEW',
                },
                headers={'Authorization': f'Token {token}'}
        ) as response:

            if response.status == 201:
                result = await response.json()

                await message.answer('Заказ создан ✅')
                await message.answer(f'ID заказа: {result["id"]}')

                order_mode[telegram_id] = False
            else:
                error_text = await response.text()
                await message.answer(f'Ошибка:\n{error_text}')


# =========================
# УДАЛИТЬ ЗАКАЗ (для customer)
# =========================

@dp.message(lambda m: m.text == '❌ Удалить заказ')
async def delete_order_button(message: Message):
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)

    if not token:
        await message.answer('Сначала войдите 🔐')
        return

    delete_mode[telegram_id] = True
    await message.answer('Введите ID заказа ❌')


# =========================
# УДАЛЕНИЕ ЗАКАЗА
# =========================

@dp.message(lambda m: delete_mode.get(m.from_user.id, False))
async def delete_order(message: Message):
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)
    order_id = message.text

    async with aiohttp.ClientSession() as session:
        async with session.delete(
                f'http://127.0.0.1:8000/api/orders/{order_id}/',
                headers={'Authorization': f'Token {token}'}
        ) as response:

            if response.status == 204:
                await message.answer('Заказ удален ✅')
            else:
                error_text = await response.text()
                await message.answer(f'Ошибка:\n{error_text}')

    delete_mode[telegram_id] = False


# =========================
# ВЫХОД
# =========================

@dp.message(lambda m: m.text == '🚪 Выйти')
async def logout_handler(message: Message, state: FSMContext):
    telegram_id = message.from_user.id

    user_tokens.pop(telegram_id, None)
    user_roles.pop(telegram_id, None)
    order_mode[telegram_id] = False
    delete_mode[telegram_id] = False
    filter_mode.pop(telegram_id, None)

    await state.clear()

    await message.answer('Вы вышли 🚪', reply_markup=guest_keyboard)


# =========================
# FALLBACK
# =========================

@dp.message()
async def fallback(message: Message):
    await message.answer('Не понимаю команду 🤔')


# =========================
# MAIN
# =========================

async def main():
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())

