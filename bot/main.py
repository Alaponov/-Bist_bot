"""
Telegram бот для управления заказами
Улучшенная версия main.py
"""
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from dotenv import load_dotenv
from environ import Env

# ========================
# КОНФИГУРАЦИЯ И ЛОГИРОВАНИЕ
# ========================

load_dotenv()
env = Env()

BASE_DIR = Path(__file__).resolve().parent.parent
env.read_env(str(BASE_DIR / ".env"))

TOKEN = env.str("TOKEN")
API_BASE_URL = env.str("API_BASE_URL", "http://127.0.0.1:8000")
API_TIMEOUT = env.int("API_TIMEOUT", 10)

# Логирование
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Bot инициализация
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ========================
# КОНСТАНТЫ
# ========================

API_LOGIN = f"{API_BASE_URL}/api/users/login/"
API_PROFILE = f"{API_BASE_URL}/api/users/profile/"
API_ORDERS = f"{API_BASE_URL}/api/orders/"

# Статусы заказов
ORDER_STATUSES = {
    'NEW': '🆕 Новый',
    'IN_PROGRESS': '⚙️ В процессе',
    'DONE': '✅ Завершен',
    'CANCELED': '❌ Отменен'
}

# ========================
# КЛАВИАТУРЫ
# ========================

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

# ========================
# ХРАНИЛИЩЕ
# ========================

user_tokens = {}
user_roles = {}
order_mode = {}
delete_mode = {}

# ========================
# СОСТОЯНИЯ FSM
# ========================

class LoginState(StatesGroup):
    username = State()
    password = State()


# ========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ========================

def format_date(date_string: str) -> str:
    """Форматирует дату ISO в удобный формат"""
    try:
        dt = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
        return dt.strftime('%d.%m.%Y %H:%M')
    except Exception:
        return date_string


def split_message(text: str, max_length: int = 4096) -> list:
    """Разбивает длинное сообщение на части (лимит Telegram - 4096 символов)"""
    if len(text) <= max_length:
        return [text]

    messages = []
    current_message = ""
    lines = text.split('\n')

    for line in lines:
        if len(current_message) + len(line) + 1 <= max_length:
            current_message += line + '\n'
        else:
            if current_message:
                messages.append(current_message.strip())
            current_message = line + '\n'

    if current_message:
        messages.append(current_message.strip())

    return messages


def validate_order_text(text: str, min_length: int = 5, max_length: int = 500) -> tuple:
    """Валидирует текст заказа. Возвращает (валидна_ли, сообщение_об_ошибке)"""
    if not text or not text.strip():
        return False, "Текст заказа не может быть пустым"

    if len(text) < min_length:
        return False, f"Текст заказа должен быть минимум {min_length} символов"

    if len(text) > max_length:
        return False, f"Текст заказа не должен превышать {max_length} символов"

    return True, ""


def validate_order_id(order_id: str) -> tuple:
    """Валидирует ID заказа. Возвращает (валидна_ли, ID_или_None)"""
    try:
        order_id_int = int(order_id.strip())
        if order_id_int > 0:
            return True, order_id_int
        return False, None
    except ValueError:
        return False, None


def sort_orders_by_user(orders: list) -> dict:
    """Группирует заказы по пользователям"""
    grouped = defaultdict(list)
    for order in orders:
        user_info = order.get('user_details', {})
        username = user_info.get('username', 'Unknown')
        grouped[username].append(order)
    return grouped


def sort_orders_by_date(orders: list) -> list:
    """Сортирует заказы по дате (новые первыми)"""
    return sorted(orders, key=lambda x: x.get('created_at', ''), reverse=True)


def filter_orders_by_status(orders: list, status: str) -> list:
    """Фильтрует заказы по статусу"""
    return [o for o in orders if o.get('status') == status]


def format_order(order: dict, with_user: bool = False) -> str:
    """Форматирует заказ для отображения"""
    created_at = format_date(order.get('created_at', 'N/A'))
    text = (
        f'🆔 ID: {order.get("id", "N/A")}\n'
        f'📝 {order.get("text", "N/A")}\n'
        f'📌 {order.get("status", "N/A")}\n'
        f'📅 {created_at}'
    )

    if with_user:
        user_info = order.get('user_details', {})
        username = user_info.get('username', 'Unknown')
        text = f'👤 Пользователь: {username}\n' + text

    return text


def get_status_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Создает инлайн-клавиатуру с кнопками статусов"""
    buttons = [
        [
            InlineKeyboardButton(text=ORDER_STATUSES['NEW'], callback_data=f'status_NEW_{order_id}'),
            InlineKeyboardButton(text=ORDER_STATUSES['IN_PROGRESS'], callback_data=f'status_IN_PROGRESS_{order_id}'),
        ],
        [
            InlineKeyboardButton(text=ORDER_STATUSES['DONE'], callback_data=f'status_DONE_{order_id}'),
            InlineKeyboardButton(text=ORDER_STATUSES['CANCELED'], callback_data=f'status_CANCELED_{order_id}'),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_status_filter_keyboard() -> InlineKeyboardMarkup:
    """Создает инлайн-клавиатуру для фильтра по статусам"""
    buttons = [
        [
            InlineKeyboardButton(text=ORDER_STATUSES['NEW'], callback_data='filter_NEW'),
            InlineKeyboardButton(text=ORDER_STATUSES['IN_PROGRESS'], callback_data='filter_IN_PROGRESS'),
        ],
        [
            InlineKeyboardButton(text=ORDER_STATUSES['DONE'], callback_data='filter_DONE'),
            InlineKeyboardButton(text=ORDER_STATUSES['CANCELED'], callback_data='filter_CANCELED'),
        ],
        [
            InlineKeyboardButton(text='📦 Все заказы', callback_data='filter_ALL'),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ========================
# API ЗАПРОСЫ (с таймаутами)
# ========================

async def api_login(username: str, password: str, telegram_id: int) -> tuple:
    """Вход в систему. Возвращает (статус_код, данные_или_None)"""
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                    API_LOGIN,
                    json={
                        'username': username,
                        'password': password,
                        'telegram_id': telegram_id,
                    }
            ) as response:
                data = await response.json() if response.status in [200, 201] else None
                return response.status, data
    except asyncio.TimeoutError:
        logger.error(f"Таймаут при входе для {username}")
        return 504, None
    except Exception as e:
        logger.error(f"Ошибка при входе: {str(e)}")
        return 500, None


async def api_get_profile(token: str) -> tuple:
    """Получить профиль. Возвращает (статус_код, данные_или_None)"""
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                    API_PROFILE,
                    headers={'Authorization': f'Token {token}'}
            ) as response:
                data = await response.json() if response.status == 200 else None
                return response.status, data
    except asyncio.TimeoutError:
        logger.error("Таймаут при получении профиля")
        return 504, None
    except Exception as e:
        logger.error(f"Ошибка при получении профиля: {str(e)}")
        return 500, None


async def api_get_orders(token: str) -> tuple:
    """Получить заказы. Возвращает (статус_код, список_заказов_или_None)"""
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                    API_ORDERS,
                    headers={'Authorization': f'Token {token}'}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    orders = data.get('results', data) if isinstance(data, dict) else data
                    return response.status, orders
                return response.status, None
    except asyncio.TimeoutError:
        logger.error("Таймаут при получении заказов")
        return 504, None
    except Exception as e:
        logger.error(f"Ошибка при получении заказов: {str(e)}")
        return 500, None


async def api_create_order(token: str, text: str) -> tuple:
    """Создать заказ. Возвращает (статус_код, данные_или_None)"""
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                    API_ORDERS,
                    json={'text': text, 'status': 'NEW'},
                    headers={'Authorization': f'Token {token}'}
            ) as response:
                data = await response.json() if response.status == 201 else None
                return response.status, data
    except asyncio.TimeoutError:
        logger.error("Таймаут при создании заказа")
        return 504, None
    except Exception as e:
        logger.error(f"Ошибка при создании заказа: {str(e)}")
        return 500, None


async def api_delete_order(token: str, order_id: int) -> tuple:
    """Удалить заказ. Возвращает (статус_код, сообщение_ошибки_или_None)"""
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.delete(
                    f'{API_ORDERS}{order_id}/',
                    headers={'Authorization': f'Token {token}'}
            ) as response:
                error_msg = None
                if response.status != 204:
                    try:
                        error_msg = await response.text()
                    except:
                        error_msg = f"Ошибка: статус {response.status}"
                return response.status, error_msg
    except asyncio.TimeoutError:
        logger.error(f"Таймаут при удалении заказа {order_id}")
        return 504, "Таймаут сервера"
    except Exception as e:
        logger.error(f"Ошибка при удалении заказа: {str(e)}")
        return 500, str(e)


async def api_update_order_status(token: str, order_id: int, status: str) -> tuple:
    """Обновить статус заказа. Возвращает (статус_код, ошибка_или_None)"""
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.patch(
                    f'{API_ORDERS}{order_id}/',
                    json={'status': status},
                    headers={'Authorization': f'Token {token}'}
            ) as response:
                error_msg = None
                if response.status not in [200, 201]:
                    try:
                        error_msg = await response.text()
                    except:
                        error_msg = f"Ошибка: статус {response.status}"
                return response.status, error_msg
    except asyncio.TimeoutError:
        logger.error(f"Таймаут при обновлении статуса заказа {order_id}")
        return 504, "Таймаут сервера"
    except Exception as e:
        logger.error(f"Ошибка при обновлении статуса: {str(e)}")
        return 500, str(e)


# ========================
# ОБРАБОТЧИКИ: START
# ========================

@dp.message(Command('start'))
async def start_handler(message: Message, state: FSMContext):
    """Обработчик команды /start"""
    telegram_id = message.from_user.id

    user_tokens.pop(telegram_id, None)
    user_roles.pop(telegram_id, None)
    order_mode[telegram_id] = False
    delete_mode[telegram_id] = False

    await state.clear()
    logger.info(f"User {telegram_id}: START")

    await message.answer('Добро пожаловать 👋', reply_markup=guest_keyboard)


# ========================
# ОБРАБОТЧИКИ: ЛОГИН
# ========================

@dp.message(lambda m: m.text == '🔐 Логин')
async def login_button(message: Message, state: FSMContext):
    """Обработчик кнопки логина"""
    telegram_id = message.from_user.id
    logger.info(f"User {telegram_id}: LOGIN_START")

    await state.set_state(LoginState.username)
    await message.answer('Введите логин')


@dp.message(LoginState.username)
async def username_handler(message: Message, state: FSMContext):
    """Обработчик ввода логина"""
    username = message.text.strip()

    if not username:
        await message.answer('Логин не может быть пустым')
        return

    await state.update_data(username=username)
    await state.set_state(LoginState.password)
    await message.answer('Введите пароль')


@dp.message(LoginState.password)
async def password_handler(message: Message, state: FSMContext):
    """Обработчик ввода пароля"""
    data = await state.get_data()
    username = data.get('username')
    password = message.text
    telegram_id = message.from_user.id

    status, result = await api_login(username, password, telegram_id)

    if status == 200 and result:
        token = result.get('token')
        role = result.get('role', 'customer')

        user_tokens[telegram_id] = token
        user_roles[telegram_id] = role

        await state.clear()
        logger.info(f"User {telegram_id}: LOGIN_SUCCESS (role={role})")

        keyboard = admin_keyboard if role == 'admin' else customer_keyboard

        await message.answer('Вы успешно вошли ✅', reply_markup=keyboard)
    else:
        logger.warning(f"User {telegram_id}: LOGIN_FAILED for {username}")
        await message.answer('❌ Неверный логин или пароль\n\nПопробуйте ещё раз. Введите логин:')
        # Возвращаемся на ввод логина, НЕ очищая состояние
        await state.set_state(LoginState.username)


# ========================
# ОБРАБОТЧИКИ: ПРОФИЛЬ
# ========================

@dp.message(lambda m: m.text == '👤 Профиль')
async def profile_handler(message: Message):
    """Обработчик просмотра профиля"""
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)

    if not token:
        await message.answer('Сначала войдите 🔐')
        return

    status, result = await api_get_profile(token)

    if status == 200 and result:
        role = user_roles.get(telegram_id, 'customer')
        role_text = 'Администратор' if role == 'admin' else 'Заказчик'

        profile_text = (
            f'👤 Ваш профиль\n\n'
            f'Username: {result.get("username", "N/A")}\n'
            f'Telegram ID: {telegram_id}\n'
            f'Роль: {role_text}\n'
            f'Заказов: {result.get("orders_count", 0)}'
        )

        logger.info(f"User {telegram_id}: VIEW_PROFILE")
        await message.answer(profile_text)
    else:
        logger.error(f"User {telegram_id}: PROFILE_ERROR (status={status})")
        await message.answer('Ошибка профиля ❌')


# ========================
# ОБРАБОТЧИКИ: МОИ ЗАКАЗЫ
# ========================

@dp.message(lambda m: m.text == '📦 Мои заказы')
async def my_orders_handler(message: Message):
    """Обработчик просмотра своих заказов"""
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)

    if not token:
        await message.answer('Сначала войдите 🔐')
        return

    status, orders = await api_get_orders(token)

    if status == 200 and orders:
        text = '📦 Ваши заказы:\n\n'

        for order in orders:
            text += format_order(order) + '\n\n'

        logger.info(f"User {telegram_id}: VIEW_MY_ORDERS (count={len(orders)})")

        for message_part in split_message(text):
            await message.answer(message_part)
    elif status == 200 and not orders:
        await message.answer('У вас нет заказов 📭')
    else:
        logger.error(f"User {telegram_id}: MY_ORDERS_ERROR (status={status})")
        await message.answer('Ошибка ❌')


# ========================
# ОБРАБОТЧИКИ: СОЗДАНИЕ ЗАКАЗА
# ========================

@dp.message(lambda m: m.text == '🛒 Сделать заказ')
async def order_button(message: Message):
    """Обработчик кнопки создания заказа"""
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)

    if not token:
        await message.answer('Сначала войдите 🔐')
        return

    order_mode[telegram_id] = True
    logger.info(f"User {telegram_id}: ORDER_START")
    await message.answer('Отправьте текст заказа 🛒')


@dp.message(lambda m: order_mode.get(m.from_user.id, False))
async def create_order(message: Message):
    """Обработчик создания заказа"""
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)
    order_text = message.text

    # Валидируем текст
    is_valid, error_msg = validate_order_text(order_text)
    if not is_valid:
        await message.answer(f'❌ {error_msg}')
        return

    status, result = await api_create_order(token, order_text)

    if status == 201 and result:
        order_id = result.get('id', 'N/A')
        logger.info(f"User {telegram_id}: ORDER_CREATED (id={order_id})")

        await message.answer('Заказ создан ✅')
        await message.answer(f'🆔 ID заказа: {order_id}')

        order_mode[telegram_id] = False
    else:
        logger.error(f"User {telegram_id}: ORDER_CREATE_ERROR (status={status})")
        await message.answer('❌ Ошибка при создании заказа')
        order_mode[telegram_id] = False


# ========================
# ОБРАБОТЧИКИ: УДАЛЕНИЕ ЗАКАЗА
# ========================

@dp.message(lambda m: m.text == '❌ Удалить заказ')
async def delete_order_button(message: Message):
    """Обработчик кнопки удаления заказа"""
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)

    if not token:
        await message.answer('Сначала войдите 🔐')
        return

    delete_mode[telegram_id] = True
    logger.info(f"User {telegram_id}: DELETE_ORDER_START")
    await message.answer('Введите ID заказа для удаления ❌')


@dp.message(lambda m: delete_mode.get(m.from_user.id, False))
async def delete_order(message: Message):
    """Обработчик удаления заказа"""
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)
    order_id_text = message.text

    # Валидируем ID
    is_valid, order_id = validate_order_id(order_id_text)
    if not is_valid:
        await message.answer('❌ Введите корректный ID заказа (число)')
        return

    status, error_msg = await api_delete_order(token, order_id)

    if status == 204:
        logger.info(f"User {telegram_id}: ORDER_DELETED (id={order_id})")
        await message.answer('✅ Заказ удален')
    else:
        logger.error(f"User {telegram_id}: ORDER_DELETE_ERROR (id={order_id}, status={status})")
        await message.answer('❌ Ошибка при удалении заказа')

    delete_mode[telegram_id] = False


# ========================
# ОБРАБОТЧИКИ: АДМИН - ВСЕ ЗАКАЗЫ
# ========================

@dp.message(lambda m: m.text == '📦 Все заказы')
async def all_orders_handler(message: Message):
    """Обработчик просмотра всех заказов (для админов)"""
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)
    role = user_roles.get(telegram_id)

    if not token or role != 'admin':
        await message.answer('У вас нет доступа 🔒')
        return

    status, orders = await api_get_orders(token)

    if status == 200 and orders:
        logger.info(f"Admin {telegram_id}: VIEW_ALL_ORDERS (count={len(orders)})")

        for order in orders:
            text = format_order(order, with_user=True)
            keyboard = get_status_keyboard(order.get('id'))
            await message.answer(text, reply_markup=keyboard)

        # После всех заказов показываем фильтры по статусам
        await message.answer('Отсортировать по статусу:', reply_markup=get_status_filter_keyboard())
    elif status == 200 and not orders:
        await message.answer('Заказов нет 📭')
    else:
        logger.error(f"Admin {telegram_id}: ALL_ORDERS_ERROR (status={status})")
        await message.answer('❌ Ошибка при получении заказов')


# ========================
# ОБРАБОТЧИКИ: АДМИН - ФИЛЬТР ПО СТАТУСАМ (Callback)
# ========================

@dp.callback_query(lambda c: c.data.startswith('filter_'))
async def filter_orders_by_status_handler(callback: CallbackQuery):
    """Обработчик фильтрации заказов по статусам"""
    telegram_id = callback.from_user.id
    token = user_tokens.get(telegram_id)
    role = user_roles.get(telegram_id)

    if not token or role != 'admin':
        await callback.answer('У вас нет доступа 🔒', show_alert=True)
        return

    # Парсим callback_data: "filter_NEW", "filter_IN_PROGRESS", "filter_ALL"
    status_filter = callback.data.split('_', 1)[1]

    status, orders = await api_get_orders(token)

    if status == 200 and orders:
        if status_filter == 'ALL':
            filtered_orders = orders
            title = '📦 Все заказы:\n\n'
        else:
            filtered_orders = filter_orders_by_status(orders, status_filter)
            title = f'{ORDER_STATUSES.get(status_filter, status_filter)} заказы:\n\n'

        if filtered_orders:
            text = title
            for order in filtered_orders:
                text += format_order(order, with_user=True) + '\n\n'

            logger.info(f"Admin {telegram_id}: FILTER_BY_STATUS (status={status_filter}, count={len(filtered_orders)})")

            for message_part in split_message(text):
                await callback.message.answer(message_part, reply_markup=get_status_filter_keyboard())
        else:
            await callback.message.answer(f'Заказов со статусом {ORDER_STATUSES.get(status_filter, status_filter)} не найдено 📭',
                                         reply_markup=get_status_filter_keyboard())

        await callback.answer()
    else:
        logger.error(f"Admin {telegram_id}: FILTER_STATUS_ERROR (status={status})")
        await callback.answer('❌ Ошибка', show_alert=True)


# ========================
# ОБРАБОТЧИКИ: АДМИН - ФИЛЬТР ПО ПОЛЬЗОВАТЕЛЮ
# ========================

@dp.message(lambda m: m.text == '👤 По пользователю')
async def filter_by_user(message: Message):
    """Обработчик фильтра по пользователю"""
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)
    role = user_roles.get(telegram_id)

    if not token or role != 'admin':
        await message.answer('У вас нет доступа 🔒')
        return

    status, orders = await api_get_orders(token)

    if status == 200 and orders:
        grouped = sort_orders_by_user(orders)
        text = '📦 Заказы по пользователям:\n\n'

        for username in sorted(grouped.keys()):
            user_orders = grouped[username]
            text += f'👤 {username} ({len(user_orders)} заказов)\n'

            for order in user_orders:
                order_display = format_order(order)
                text += '\n'.join(f'  {line}' for line in order_display.split('\n')) + '\n\n'

        logger.info(f"Admin {telegram_id}: FILTER_BY_USER (users={len(grouped)})")

        for message_part in split_message(text):
            await message.answer(message_part, reply_markup=admin_filter_keyboard)
    elif status == 200 and not orders:
        await message.answer('Заказов нет 📭', reply_markup=admin_filter_keyboard)
    else:
        logger.error(f"Admin {telegram_id}: FILTER_USER_ERROR (status={status})")
        await message.answer('❌ Ошибка')


# ========================
# ОБРАБОТЧИКИ: АДМИН - ФИЛЬТР ПО ДАТЕ
# ========================

@dp.message(lambda m: m.text == '📅 По дате')
async def filter_by_date(message: Message):
    """Обработчик фильтра по дате"""
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)
    role = user_roles.get(telegram_id)

    if not token or role != 'admin':
        await message.answer('У вас нет доступа 🔒')
        return

    status, orders = await api_get_orders(token)

    if status == 200 and orders:
        sorted_orders = sort_orders_by_date(orders)
        text = '📦 Заказы по дате (новые первыми):\n\n'

        for order in sorted_orders:
            text += format_order(order, with_user=True) + '\n\n'

        logger.info(f"Admin {telegram_id}: FILTER_BY_DATE (count={len(sorted_orders)})")

        for message_part in split_message(text):
            await message.answer(message_part, reply_markup=admin_filter_keyboard)
    elif status == 200 and not orders:
        await message.answer('Заказов нет 📭', reply_markup=admin_filter_keyboard)
    else:
        logger.error(f"Admin {telegram_id}: FILTER_DATE_ERROR (status={status})")
        await message.answer('❌ Ошибка')


# ========================
# ОБРАБОТЧИКИ: АДМИН - БЕЗ СОРТИРОВКИ
# ========================

@dp.message(lambda m: m.text == '🔄 Без сортировки')
async def no_sort_handler(message: Message):
    """Обработчик отключения сортировки"""
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)
    role = user_roles.get(telegram_id)

    if not token or role != 'admin':
        await message.answer('У вас нет доступа 🔒')
        return

    logger.info(f"Admin {telegram_id}: NO_SORT")
    await message.answer('🔄 Сортировка отключена', reply_markup=admin_keyboard)


# ========================
# ОБРАБОТЧИКИ: АДМИН - СТАТИСТИКА
# ========================

@dp.message(lambda m: m.text == '📊 Статистика')
async def statistics_handler(message: Message):
    """Обработчик просмотра статистики"""
    telegram_id = message.from_user.id
    token = user_tokens.get(telegram_id)
    role = user_roles.get(telegram_id)

    if not token or role != 'admin':
        await message.answer('У вас нет доступа 🔒')
        return

    status, orders = await api_get_orders(token)

    if status == 200 and orders:
        total_orders = len(orders)
        new_orders = len([o for o in orders if o.get('status') == 'NEW'])
        in_progress = len([o for o in orders if o.get('status') == 'IN_PROGRESS'])
        done_orders = len([o for o in orders if o.get('status') == 'DONE'])
        canceled_orders = len([o for o in orders if o.get('status') == 'CANCELED'])

        logger.info(f"Admin {telegram_id}: VIEW_STATS (total={total_orders})")

        stats_text = (
            f'📊 Статистика:\n\n'
            f'📦 Всего заказов: {total_orders}\n'
            f'🆕 Новых: {new_orders}\n'
            f'⚙️ В процессе: {in_progress}\n'
            f'✅ Завершено: {done_orders}\n'
            f'❌ Отменено: {canceled_orders}'
        )

        await message.answer(stats_text)
    else:
        logger.error(f"Admin {telegram_id}: STATS_ERROR (status={status})")
        await message.answer('❌ Ошибка статистики')


# ========================
# ОБРАБОТЧИКИ: АДМИН - СМЕНА СТАТУСА (Callback)
# ========================

@dp.callback_query(lambda c: c.data.startswith('status_'))
async def change_order_status(callback: CallbackQuery):
    """Обработчик смены статуса заказа через кнопку"""
    telegram_id = callback.from_user.id
    token = user_tokens.get(telegram_id)
    role = user_roles.get(telegram_id)

    if not token or role != 'admin':
        await callback.answer('У вас нет доступа 🔒', show_alert=True)
        return

    # Парсим callback_data: "status_NEW_123" -> ["status", "NEW_123"]
    parts = callback.data.rsplit('_', 1)  # Разбираем с конца, чтобы правильно обработать IN_PROGRESS
    order_id = int(parts[1])
    new_status = parts[0].replace('status_', '')

    status, error_msg = await api_update_order_status(token, order_id, new_status)

    if status in [200, 201]:
        logger.info(f"Admin {telegram_id}: ORDER_STATUS_CHANGED (id={order_id}, status={new_status})")
        status_text = ORDER_STATUSES.get(new_status, new_status)
        await callback.answer(f'✅ Статус изменен на {status_text}', show_alert=False)
        # Обновляем сообщение с новым текстом
        await callback.message.edit_reply_markup(reply_markup=get_status_keyboard(order_id))
    else:
        logger.error(f"Admin {telegram_id}: ORDER_STATUS_ERROR (id={order_id}, status={new_status})")
        await callback.answer(f'❌ Ошибка: {error_msg}', show_alert=True)


# ========================
# ОБРАБОТЧИКИ: ВЫХОД
# ========================

@dp.message(lambda m: m.text == '🚪 Выйти')
async def logout_handler(message: Message, state: FSMContext):
    """Обработчик выхода из системы"""
    telegram_id = message.from_user.id

    user_tokens.pop(telegram_id, None)
    user_roles.pop(telegram_id, None)
    order_mode[telegram_id] = False
    delete_mode[telegram_id] = False

    await state.clear()
    logger.info(f"User {telegram_id}: LOGOUT")

    await message.answer('Вы вышли 🚪', reply_markup=guest_keyboard)


# ========================
# FALLBACK
# ========================

@dp.message()
async def fallback(message: Message):
    """Обработчик неизвестных команд"""
    await message.answer('Не понимаю команду 🤔')


# ========================
# MAIN
# ========================

async def main():
    """Запуск бота"""
    logger.info("🤖 Бот запущен")
    await dp.start_polling(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🤖 Бот остановлен")
