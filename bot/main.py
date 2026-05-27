"""
Telegram бот для управления заказами
Полностью переписанная версия с защитой от зависаний и управлением сессией
"""
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, \
    CallbackQuery
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
ADMIN_IDS = env.list("ADMIN_IDS", default=[])
HANDLER_TIMEOUT = 20  # Таймаут для обработчиков команд

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
        [KeyboardButton(text='📦 Заказы')],
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
        [KeyboardButton(text='🔎 По статусу')],
        [KeyboardButton(text='◀️ Назад')],
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

def clear_user_session(telegram_id: int):
    """Удаляет токен и роль пользователя при невалидной сессии"""
    user_tokens.pop(telegram_id, None)
    user_roles.pop(telegram_id, None)
    order_mode[telegram_id] = False
    delete_mode[telegram_id] = False
    logger.info(f"User {telegram_id}: SESSION_CLEARED (token invalid)")


def format_date(date_string: str) -> str:
    """Форматирует дату ISO в Europe/Chisinau"""
    try:
        dt = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
        dt_moldova = dt.astimezone(ZoneInfo("Europe/Chisinau"))
        return dt_moldova.strftime('%d.%m.%Y %H:%M')
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
            InlineKeyboardButton(text='Все заказы', callback_data='filter_ALL'),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ========================
# API ЗАПРОСЫ (с таймаутами)
# ========================

async def api_login(username: str, password: str, telegram_id: int) -> tuple:
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
                if response.status in [200, 201]:
                    data = await asyncio.wait_for(response.json(), timeout=5)
                    return response.status, data
                return response.status, None
    except asyncio.TimeoutError:
        logger.error(f"Таймаут при входе для {username}")
        return 504, None
    except Exception as e:
        logger.error(f"Ошибка при входе: {str(e)}")
        return 500, None


async def api_get_profile(token: str) -> tuple:
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                    API_PROFILE,
                    headers={'Authorization': f'Token {token}'}
            ) as response:
                if response.status == 200:
                    data = await asyncio.wait_for(response.json(), timeout=5)
                    return response.status, data
                return response.status, None
    except asyncio.TimeoutError:
        logger.error("Таймаут при получении профиля")
        return 504, None
    except Exception as e:
        logger.error(f"Ошибка при получении профиля: {str(e)}")
        return 500, None


async def api_get_orders(token: str) -> tuple:
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                    API_ORDERS,
                    headers={'Authorization': f'Token {token}'}
            ) as response:
                if response.status == 200:
                    data = await asyncio.wait_for(response.json(), timeout=5)
                    orders = data.get('results', data) if isinstance(data, dict) else data
                    return response.status, orders
                elif response.status in [401, 403]:
                    return response.status, None
                return response.status, None
    except asyncio.TimeoutError:
        logger.error("Таймаут при получении заказов")
        return 504, None
    except Exception as e:
        logger.error(f"Ошибка при получении заказов: {str(e)}")
        return 500, None


async def api_create_order(token: str, text: str) -> tuple:
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                    API_ORDERS,
                    json={'text': text, 'status': 'NEW'},
                    headers={'Authorization': f'Token {token}'}
            ) as response:
                if response.status == 201:
                    data = await asyncio.wait_for(response.json(), timeout=5)
                    return response.status, data
                elif response.status in [401, 403]:
                    return response.status, None
                return response.status, None
    except asyncio.TimeoutError:
        logger.error("Таймаут при создании заказа")
        return 504, None
    except Exception as e:
        logger.error(f"Ошибка при создании заказа: {str(e)}")
        return 500, None


async def api_delete_order(token: str, order_id: int) -> tuple:
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.delete(
                    f'{API_ORDERS}{order_id}/',
                    headers={'Authorization': f'Token {token}'}
            ) as response:
                if response.status == 204:
                    return response.status, None
                elif response.status in [401, 403]:
                    return response.status, None
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
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.patch(
                    f'{API_ORDERS}{order_id}/',
                    json={'status': status},
                    headers={'Authorization': f'Token {token}'}
            ) as response:
                if response.status in [200, 201]:
                    return response.status, None
                elif response.status in [401, 403]:
                    return response.status, None
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
    try:
        telegram_id = message.from_user.id
        user_tokens.pop(telegram_id, None)
        user_roles.pop(telegram_id, None)
        order_mode[telegram_id] = False
        delete_mode[telegram_id] = False
        await state.clear()
        logger.info(f"User {telegram_id}: START")
        await message.answer('Добро пожаловать 👋', reply_markup=guest_keyboard)
    except Exception as e:
        logger.error(f"START_ERROR: {str(e)}")
        await message.answer('❌ Произошла ошибка. Попробуйте позже.')


# ========================
# ОБРАБОТЧИКИ: ЛОГИН
# ========================

@dp.message(lambda m: m.text == '🔐 Логин')
async def login_button(message: Message, state: FSMContext):
    try:
        telegram_id = message.from_user.id
        logger.info(f"User {telegram_id}: LOGIN_START")
        await state.set_state(LoginState.username)
        await message.answer('Введите логин')
    except Exception as e:
        logger.error(f"LOGIN_BUTTON_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте снова.')


@dp.message(LoginState.username)
async def username_handler(message: Message, state: FSMContext):
    try:
        username = message.text.strip()
        if not username:
            await message.answer('Логин не может быть пустым')
            return
        await state.update_data(username=username)
        await state.set_state(LoginState.password)
        await message.answer('Введите пароль')
    except Exception as e:
        logger.error(f"USERNAME_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Введите логин заново.')
        await state.set_state(LoginState.username)


@dp.message(LoginState.password)
async def password_handler(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        username = data.get('username')
        password = message.text
        telegram_id = message.from_user.id

        await message.answer('⏳ Проверяю учетные данные...')

        status, result = await asyncio.wait_for(
            api_login(username, password, telegram_id),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and result:
            token = result.get('token')
            role = result.get('role', 'customer')
            user_tokens[telegram_id] = token
            user_roles[telegram_id] = role
            await state.clear()
            logger.info(f"User {telegram_id}: LOGIN_SUCCESS (role={role})")
            keyboard = admin_keyboard if role == 'admin' else customer_keyboard
            await message.answer('Вы успешно вошли ✅', reply_markup=keyboard)
        elif status in [401, 403]:
            logger.warning(f"User {telegram_id}: LOGIN_FAILED for {username}")
            await message.answer('❌ Неверный логин или пароль\n\nПопробуйте ещё раз. Введите логин:')
            await state.set_state(LoginState.username)
        elif status == 504:
            await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
            await state.set_state(LoginState.username)
        else:
            logger.error(f"User {telegram_id}: LOGIN_ERROR (status={status})")
            await message.answer('❌ Ошибка при входе. Попробуйте позже.')
            await state.set_state(LoginState.username)
    except asyncio.TimeoutError:
        await message.answer('⏱️ Операция заняла слишком долго. Попробуйте позже.')
        await state.set_state(LoginState.username)
    except Exception as e:
        logger.error(f"PASSWORD_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка при входе. Попробуйте позже.')
        await state.set_state(LoginState.username)


# ========================
# ОБРАБОТЧИКИ: ПРОФИЛЬ
# ========================

@dp.message(lambda m: m.text == '👤 Профиль')
async def profile_handler(message: Message):
    try:
        telegram_id = message.from_user.id
        token = user_tokens.get(telegram_id)
        if not token:
            await message.answer('Сначала войдите 🔐', reply_markup=guest_keyboard)
            return

        await message.answer('⏳ Загружаю профиль...')

        status, result = await asyncio.wait_for(
            api_get_profile(token),
            timeout=HANDLER_TIMEOUT
        )

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
        elif status in [401, 403]:
            clear_user_session(telegram_id)
            await message.answer('❌ Ваша сессия истекла. Пожалуйста, войдите заново.', reply_markup=guest_keyboard)
        elif status == 504:
            await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
        else:
            logger.error(f"User {telegram_id}: PROFILE_ERROR (status={status})")
            await message.answer('❌ Ошибка профиля')
    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
    except Exception as e:
        logger.error(f"PROFILE_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


# ========================
# ОБРАБОТЧИКИ: МОИ ЗАКАЗЫ
# ========================

@dp.message(lambda m: m.text == '📦 Мои заказы')
async def my_orders_handler(message: Message):
    try:
        telegram_id = message.from_user.id
        token = user_tokens.get(telegram_id)
        if not token:
            await message.answer('Сначала войдите 🔐', reply_markup=guest_keyboard)
            return

        await message.answer('⏳ Загружаю заказы...')

        status, orders = await asyncio.wait_for(
            api_get_orders(token),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and orders:
            orders_to_show = orders[:50]  # Максимум 50 заказов
            text = f'📦 Ваши заказы ({len(orders_to_show)} из {len(orders)}):\n\n'
            for order in orders_to_show:
                text += format_order(order) + '\n\n'
            logger.info(f"User {telegram_id}: VIEW_MY_ORDERS (count={len(orders)}, showed={len(orders_to_show)})")
            for message_part in split_message(text):
                await message.answer(message_part)
        elif status == 200 and not orders:
            await message.answer('У вас нет заказов 📭')
        elif status in [401, 403]:
            clear_user_session(telegram_id)
            await message.answer('❌ Ваша сессия истекла. Пожалуйста, войдите заново.', reply_markup=guest_keyboard)
        elif status == 504:
            await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
        else:
            logger.error(f"User {telegram_id}: MY_ORDERS_ERROR (status={status})")
            await message.answer('❌ Ошибка при загрузке заказов')
    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
    except Exception as e:
        logger.error(f"MY_ORDERS_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


# ========================
# ОБРАБОТЧИКИ: СОЗДАНИЕ ЗАКАЗА
# ========================

@dp.message(lambda m: m.text == '🛒 Сделать заказ')
async def order_button(message: Message):
    try:
        telegram_id = message.from_user.id
        token = user_tokens.get(telegram_id)
        if not token:
            await message.answer('Сначала войдите 🔐', reply_markup=guest_keyboard)
            return
        order_mode[telegram_id] = True
        logger.info(f"User {telegram_id}: ORDER_START")
        await message.answer('Отправьте текст заказа 🛒')
    except Exception as e:
        logger.error(f"ORDER_BUTTON_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


@dp.message(lambda m: order_mode.get(m.from_user.id, False))
async def create_order(message: Message):
    try:
        telegram_id = message.from_user.id
        token = user_tokens.get(telegram_id)
        order_text = message.text
        is_valid, error_msg = validate_order_text(order_text)
        if not is_valid:
            await message.answer(f'❌ {error_msg}')
            return

        await message.answer('⏳ Создаю заказ...')

        status, result = await asyncio.wait_for(
            api_create_order(token, order_text),
            timeout=HANDLER_TIMEOUT
        )

        if status == 201 and result:
            order_id = result.get('id', 'N/A')
            logger.info(f"User {telegram_id}: ORDER_CREATED (id={order_id})")
            await message.answer('✅ Заказ создан')
            await message.answer(f'🆔 ID заказа: {order_id}')
            order_mode[telegram_id] = False

            # Асинхронная отправка админам
            user = message.from_user.username or f"{message.from_user.first_name} {message.from_user.last_name or ''}"
            dt_created = result.get("created_at", "")
            order_text_admin = (
                f'👤 Пользователь: {user}\n'
                f'🆔 ID: {order_id}\n'
                f'📝 {order_text}\n'
                f'📌 NEW\n'
                f'📅 {format_date(dt_created)}'
            )

            async def send_to_admins():
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, f'🆕 Новый заказ!\n\n{order_text_admin}')
                    except Exception as e:
                        logger.error(f"Ошибка отправки админу {admin_id}: {e}")

            asyncio.create_task(send_to_admins())
        elif status in [401, 403]:
            clear_user_session(telegram_id)
            await message.answer('❌ Ваша сессия истекла. Пожалуйста, войдите заново.', reply_markup=guest_keyboard)
            order_mode[telegram_id] = False
        elif status == 504:
            await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
            order_mode[telegram_id] = False
        else:
            logger.error(f"User {telegram_id}: ORDER_CREATE_ERROR (status={status})")
            await message.answer('❌ Ошибка при создании заказа')
            order_mode[telegram_id] = False
    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
        order_mode[telegram_id] = False
    except Exception as e:
        logger.error(f"CREATE_ORDER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')
        order_mode[telegram_id] = False


# ========================
# ОБРАБОТЧИКИ: УДАЛЕНИЕ ЗАКАЗА
# ========================

@dp.message(lambda m: m.text == '❌ Удалить заказ')
async def delete_order_button(message: Message):
    try:
        telegram_id = message.from_user.id
        token = user_tokens.get(telegram_id)
        if not token:
            await message.answer('Сначала войдите 🔐', reply_markup=guest_keyboard)
            return
        delete_mode[telegram_id] = True
        logger.info(f"User {telegram_id}: DELETE_ORDER_START")
        await message.answer('Введите ID заказа для удаления ❌')
    except Exception as e:
        logger.error(f"DELETE_ORDER_BUTTON_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


@dp.message(lambda m: delete_mode.get(m.from_user.id, False))
async def delete_order(message: Message):
    try:
        telegram_id = message.from_user.id
        token = user_tokens.get(telegram_id)
        order_id_text = message.text
        is_valid, order_id = validate_order_id(order_id_text)
        if not is_valid:
            await message.answer('❌ Введите корректный ID заказа (число)')
            return

        await message.answer('⏳ Удаляю заказ...')

        status, error_msg = await asyncio.wait_for(
            api_delete_order(token, order_id),
            timeout=HANDLER_TIMEOUT
        )

        if status == 204:
            logger.info(f"User {telegram_id}: ORDER_DELETED (id={order_id})")
            await message.answer('✅ Заказ удален')
        elif status in [401, 403]:
            clear_user_session(telegram_id)
            await message.answer('❌ Ваша сессия истекла. Пожалуйста, войдите заново.', reply_markup=guest_keyboard)
        elif status == 504:
            await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
        else:
            logger.error(f"User {telegram_id}: ORDER_DELETE_ERROR (id={order_id}, status={status})")
            await message.answer('❌ Ошибка при удалении заказа')
        delete_mode[telegram_id] = False
    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
        delete_mode[telegram_id] = False
    except Exception as e:
        logger.error(f"DELETE_ORDER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')
        delete_mode[telegram_id] = False


# ========================
# АДМИН: ЗАКАЗЫ, СОРТИРОВКИ, ФИЛЬТРЫ
# ========================

@dp.message(lambda m: m.text == '📦 Заказы')
async def admin_orders_handler(message: Message):
    try:
        telegram_id = message.from_user.id
        token = user_tokens.get(telegram_id)
        role = user_roles.get(telegram_id)
        if not token or role != 'admin':
            await message.answer('У вас нет доступа 🔒')
            return

        await message.answer('⏳ Загружаю заказы...')

        status, orders = await asyncio.wait_for(
            api_get_orders(token),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and orders:
            orders_to_show = orders[:30]  # Максимум 30 заказов
            logger.info(f"Admin {telegram_id}: VIEW_ALL_ORDERS (count={len(orders)}, showed={len(orders_to_show)})")
            for order in orders_to_show:
                text = format_order(order, with_user=True)
                keyboard = get_status_keyboard(order.get('id'))
                await message.answer(text, reply_markup=keyboard)
            await message.answer('👇 Сортировать/фильтровать:', reply_markup=admin_filter_keyboard)
        elif status == 200 and not orders:
            await message.answer('Заказов нет 📭')
        elif status in [401, 403]:
            clear_user_session(telegram_id)
            await message.answer('❌ Ваша сессия истекла. Пожалуйста, войдите заново.', reply_markup=guest_keyboard)
        elif status == 504:
            await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
        else:
            logger.error(f"Admin {telegram_id}: ALL_ORDERS_ERROR (status={status})")
            await message.answer('❌ Ошибка при получении заказов')
    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
    except Exception as e:
        logger.error(f"ADMIN_ORDERS_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


@dp.message(lambda m: m.text == '🔄 Без сортировки')
async def no_sort_handler(message: Message):
    try:
        telegram_id = message.from_user.id
        token = user_tokens.get(telegram_id)
        role = user_roles.get(telegram_id)
        if not token or role != 'admin':
            await message.answer('У вас нет доступа 🔒')
            return

        await message.answer('⏳ Загружаю заказы...')

        status, orders = await asyncio.wait_for(
            api_get_orders(token),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and orders:
            orders_to_show = orders[:30]  # Максимум 30 заказов
            for order in orders_to_show:
                text = format_order(order, with_user=True)
                keyboard = get_status_keyboard(order.get('id'))
                await message.answer(text, reply_markup=keyboard)
            await message.answer('Сортировать/фильтровать:', reply_markup=admin_filter_keyboard)
        elif status == 200 and not orders:
            await message.answer('Заказов нет 📭', reply_markup=admin_filter_keyboard)
        elif status in [401, 403]:
            clear_user_session(telegram_id)
            await message.answer('❌ Ваша сессия истекла. Пожалуйста, войдите заново.', reply_markup=guest_keyboard)
        elif status == 504:
            await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.', reply_markup=admin_filter_keyboard)
        else:
            await message.answer('❌ Ошибка при получении заказов', reply_markup=admin_filter_keyboard)
    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.', reply_markup=admin_filter_keyboard)
    except Exception as e:
        logger.error(f"NO_SORT_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.', reply_markup=admin_filter_keyboard)


@dp.message(lambda m: m.text == '🔎 По статусу')
async def filter_by_status_menu(message: Message):
    try:
        await message.answer(
            "Выберите статус для фильтрации:",
            reply_markup=get_status_filter_keyboard()
        )
    except Exception as e:
        logger.error(f"FILTER_STATUS_MENU_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


@dp.callback_query(lambda c: c.data.startswith('filter_'))
async def filter_orders_by_status_handler(callback: CallbackQuery):
    try:
        telegram_id = callback.from_user.id
        token = user_tokens.get(telegram_id)
        role = user_roles.get(telegram_id)
        if not token or role != 'admin':
            await callback.answer('У вас нет доступа 🔒', show_alert=True)
            return

        await callback.answer('⏳ Загружаю заказы...')

        status_filter = callback.data.split('_', 1)[1]
        status, orders = await asyncio.wait_for(
            api_get_orders(token),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and orders:
            if status_filter == 'ALL':
                filtered_orders = orders
                title = 'Все заказы:\n\n'
            else:
                filtered_orders = filter_orders_by_status(orders, status_filter)
                title = f'{ORDER_STATUSES.get(status_filter, status_filter)} заказы:\n\n'

            filtered_orders = filtered_orders[:30]  # Максимум 30 заказов

            if filtered_orders:
                text = title
                for order in filtered_orders:
                    text += format_order(order, with_user=True) + '\n\n'
                logger.info(
                    f"Admin {telegram_id}: FILTER_BY_STATUS (status={status_filter}, count={len(filtered_orders)})")
                for message_part in split_message(text):
                    await callback.message.answer(message_part)
                await callback.message.answer('Сортировать/фильтровать:', reply_markup=admin_filter_keyboard)
            else:
                await callback.message.answer(
                    f'Заказов со статусом {ORDER_STATUSES.get(status_filter, status_filter)} не найдено 📭',
                    reply_markup=admin_filter_keyboard)
        elif status in [401, 403]:
            clear_user_session(telegram_id)
            await callback.message.answer('❌ Ваша сессия истекла. Пожалуйста, войдите заново.',
                                          reply_markup=guest_keyboard)
        elif status == 504:
            await callback.message.answer('⏱️ Сервер не отвечает. Попробуйте позже.',
                                          reply_markup=admin_filter_keyboard)
        else:
            logger.error(f"Admin {telegram_id}: FILTER_STATUS_ERROR (status={status})")
            await callback.answer('❌ Ошибка', show_alert=True)
    except asyncio.TimeoutError:
        await callback.message.answer('⏱️ Сервер не отвечает. Попробуйте позже.', reply_markup=admin_filter_keyboard)
    except Exception as e:
        logger.error(f"FILTER_STATUS_HANDLER_ERROR: {str(e)}")
        await callback.answer('❌ Ошибка', show_alert=True)


@dp.message(lambda m: m.text == '👤 По пользователю')
async def filter_by_user(message: Message):
    try:
        telegram_id = message.from_user.id
        token = user_tokens.get(telegram_id)
        role = user_roles.get(telegram_id)
        if not token or role != 'admin':
            await message.answer('У вас нет доступа 🔒')
            return

        await message.answer('⏳ Загружаю заказы...')

        status, orders = await asyncio.wait_for(
            api_get_orders(token),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and orders:
            grouped = sort_orders_by_user(orders)
            text = '📦 Заказы по пользователям:\n\n'
            users_to_show = list(sorted(grouped.keys()))[:20]  # Максимум 20 пользователей
            for username in users_to_show:
                user_orders = grouped[username][:5]  # Максимум 5 заказов на пользователя
                text += f'👤 {username} ({len(user_orders)} заказов)\n'
                for order in user_orders:
                    order_display = format_order(order)
                    text += '\n'.join(f'  {line}' for line in order_display.split('\n')) + '\n\n'
            logger.info(f"Admin {telegram_id}: FILTER_BY_USER (users={len(users_to_show)})")
            for message_part in split_message(text):
                await message.answer(message_part)
            await message.answer('Сортировать/фильтровать:', reply_markup=admin_filter_keyboard)
        elif status == 200 and not orders:
            await message.answer('Заказов нет 📭', reply_markup=admin_filter_keyboard)
        elif status in [401, 403]:
            clear_user_session(telegram_id)
            await message.answer('❌ Ваша сессия истекла. Пожалуйста, войдите заново.', reply_markup=guest_keyboard)
        elif status == 504:
            await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.', reply_markup=admin_filter_keyboard)
        else:
            logger.error(f"Admin {telegram_id}: FILTER_USER_ERROR (status={status})")
            await message.answer('❌ Ошибка', reply_markup=admin_filter_keyboard)
    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.', reply_markup=admin_filter_keyboard)
    except Exception as e:
        logger.error(f"FILTER_BY_USER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.', reply_markup=admin_filter_keyboard)


@dp.message(lambda m: m.text == '📅 По дате')
async def filter_by_date(message: Message):
    try:
        telegram_id = message.from_user.id
        token = user_tokens.get(telegram_id)
        role = user_roles.get(telegram_id)
        if not token or role != 'admin':
            await message.answer('У вас нет доступа 🔒')
            return

        await message.answer('⏳ Загружаю заказы...')

        status, orders = await asyncio.wait_for(
            api_get_orders(token),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and orders:
            sorted_orders = sort_orders_by_date(orders)
            sorted_orders = sorted_orders[:30]  # Максимум 30 заказов
            text = '📦 Заказы по дате (новые первыми):\n\n'
            for order in sorted_orders:
                text += format_order(order, with_user=True) + '\n\n'
            logger.info(f"Admin {telegram_id}: FILTER_BY_DATE (count={len(sorted_orders)})")
            for message_part in split_message(text):
                await message.answer(message_part)
            await message.answer('Сортировать/фильтровать:', reply_markup=admin_filter_keyboard)
        elif status == 200 and not orders:
            await message.answer('Заказов нет 📭', reply_markup=admin_filter_keyboard)
        elif status in [401, 403]:
            clear_user_session(telegram_id)
            await message.answer('❌ Ваша сессия истекла. Пожалуйста, войдите заново.', reply_markup=guest_keyboard)
        elif status == 504:
            await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.', reply_markup=admin_filter_keyboard)
        else:
            logger.error(f"Admin {telegram_id}: FILTER_DATE_ERROR (status={status})")
            await message.answer('❌ Ошибка', reply_markup=admin_filter_keyboard)
    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.', reply_markup=admin_filter_keyboard)
    except Exception as e:
        logger.error(f"FILTER_BY_DATE_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.', reply_markup=admin_filter_keyboard)


# ========================
# ОБРАБОТЧИКИ: АДМИН - СТАТИСТИКА
# ========================

@dp.message(lambda m: m.text == '📊 Статистика')
async def statistics_handler(message: Message):
    try:
        telegram_id = message.from_user.id
        token = user_tokens.get(telegram_id)
        role = user_roles.get(telegram_id)
        if not token or role != 'admin':
            await message.answer('У вас нет доступа 🔒')
            return

        await message.answer('⏳ Подсчитываю статистику...')

        status, orders = await asyncio.wait_for(
            api_get_orders(token),
            timeout=HANDLER_TIMEOUT
        )

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
        elif status in [401, 403]:
            clear_user_session(telegram_id)
            await message.answer('❌ Ваша сессия истекла. Пожалуйста, войдите заново.', reply_markup=guest_keyboard)
        elif status == 504:
            await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
        else:
            logger.error(f"Admin {telegram_id}: STATS_ERROR (status={status})")
            await message.answer('❌ Ошибка статистики')
    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
    except Exception as e:
        logger.error(f"STATISTICS_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


@dp.callback_query(lambda c: c.data.startswith('status_'))
async def change_order_status(callback: CallbackQuery):
    try:
        telegram_id = callback.from_user.id
        token = user_tokens.get(telegram_id)
        role = user_roles.get(telegram_id)
        if not token or role != 'admin':
            await callback.answer('У вас нет доступа 🔒', show_alert=True)
            return
        parts = callback.data.rsplit('_', 1)
        order_id = int(parts[1])
        new_status = parts[0].replace('status_', '')

        status, error_msg = await asyncio.wait_for(
            api_update_order_status(token, order_id, new_status),
            timeout=HANDLER_TIMEOUT
        )

        if status in [200, 201]:
            logger.info(f"Admin {telegram_id}: ORDER_STATUS_CHANGED (id={order_id}, status={new_status})")
            status_text = ORDER_STATUSES.get(new_status, new_status)
            await callback.answer(f'✅ Статус изменен на {status_text}', show_alert=False)
            await callback.message.edit_reply_markup(reply_markup=get_status_keyboard(order_id))
        elif status in [401, 403]:
            clear_user_session(telegram_id)
            await callback.answer('❌ Ваша сессия истекла', show_alert=True)
        elif status == 504:
            await callback.answer('⏱️ Сервер не отвечает', show_alert=True)
        else:
            logger.error(f"Admin {telegram_id}: ORDER_STATUS_ERROR (id={order_id}, status={new_status})")
            await callback.answer(f'❌ Ошибка: {error_msg}', show_alert=True)
    except asyncio.TimeoutError:
        await callback.answer('⏱️ Сервер не отвечает', show_alert=True)
    except Exception as e:
        logger.error(f"CHANGE_ORDER_STATUS_ERROR: {str(e)}")
        await callback.answer('❌ Ошибка', show_alert=True)


# ========================
# ОБРАБОТЧИКИ: ВЫХОД
# ========================

@dp.message(lambda m: m.text == '🚪 Выйти')
async def logout_handler(message: Message, state: FSMContext):
    try:
        telegram_id = message.from_user.id
        user_tokens.pop(telegram_id, None)
        user_roles.pop(telegram_id, None)
        order_mode[telegram_id] = False
        delete_mode[telegram_id] = False
        await state.clear()
        logger.info(f"User {telegram_id}: LOGOUT")
        await message.answer('Вы вышли 🚪', reply_markup=guest_keyboard)
    except Exception as e:
        logger.error(f"LOGOUT_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка при выходе')


@dp.message(lambda m: m.text == '◀️ Назад')
async def back_handler(message: Message):
    try:
        telegram_id = message.from_user.id
        role = user_roles.get(telegram_id)
        if role == 'admin':
            await message.answer('Вернулись в главное меню', reply_markup=admin_keyboard)
        else:
            await message.answer('Вернулись в главное меню', reply_markup=customer_keyboard)
    except Exception as e:
        logger.error(f"BACK_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка')


@dp.message()
async def fallback(message: Message):
    try:
        await message.answer('Не понимаю команду 🤔')
    except Exception as e:
        logger.error(f"FALLBACK_ERROR: {str(e)}")


async def main():
    logger.info("🤖 Бот запущен")
    await dp.start_polling(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🤖 Бот остановлен")
