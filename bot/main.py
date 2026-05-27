"""
Telegram бот для управления заказами
Полностью переписанная версия с защитой от зависаний и управлением сессией
v3.0 - Production-ready с retry логикой, rate limiting и улучшенной обработкой ошибок
"""
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo
from typing import Tuple, Optional, Dict, List, Callable, Any

import aiohttp
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Update
)
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
HANDLER_TIMEOUT = 20
MAX_ORDERS_DISPLAY = 30
MAX_USERS_DISPLAY = 20
TIMEZONE = "Europe/Chisinau"

# Rate limiting параметры
LOGIN_MAX_ATTEMPTS = 5
LOGIN_TIME_WINDOW = 300  # 5 минут
ORDER_MAX_ATTEMPTS = 10
ORDER_TIME_WINDOW = 3600  # 1 час

# Session параметры
SESSION_TIMEOUT = 24 * 60 * 60  # 24 часа
CLEANUP_INTERVAL = 60 * 60  # 1 час

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

ORDER_STATUSES = {
    'NEW': '🆕 Новый',
    'IN_PROGRESS': '⚙️ В процессе',
    'DONE': '✅ Завершен',
    'CANCELED': '❌ Отменен'
}

ORDER_PRIORITIES = {
    1: '🟢 Низкий',
    2: '🟡 Средний',
    3: '🔴 Высокий',
    4: '⚫ Срочный'
}

ERROR_MESSAGES = {
    400: "❌ Неверные данные. Проверьте введённую информацию.",
    401: "❌ Неверный логин или пароль.",
    403: "❌ У вас нет доступа к этому ресурсу.",
    404: "❌ Ресурс не найден.",
    409: "❌ Конфликт данных. Заказ уже существует или был изменён.",
    429: "⏱️ Слишком много запросов. Попробуйте позже.",
    500: "❌ Ошибка сервера. Попробуйте позже.",
    502: "❌ Сервер недоступен. Попробуйте позже.",
    503: "⏱️ Сервер перегружен. Попробуйте позже.",
    504: "⏱️ Сервер не отвечает. Попробуйте позже.",
}


# ========================
# КЛАВИАТУРЫ
# ========================

def get_guest_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для неавторизованных пользователей"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text='🔐 Логин')]],
        resize_keyboard=True
    )


def get_customer_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для заказчиков"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text='👤 Профиль'), KeyboardButton(text='❓ Помощь')],
            [KeyboardButton(text='🛒 Сделать заказ')],
            [KeyboardButton(text='📦 Мои заказы')],
            [KeyboardButton(text='❌ Удалить заказ')],
            [KeyboardButton(text='🚪 Выйти')],
        ],
        resize_keyboard=True
    )


def get_admin_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для администраторов"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text='👤 Профиль'), KeyboardButton(text='❓ Помощь')],
            [KeyboardButton(text='📦 Все заказы')],
            [KeyboardButton(text='📊 Статистика')],
            [KeyboardButton(text='🚪 Выйти')],
        ],
        resize_keyboard=True
    )


def get_admin_filter_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для фильтрации заказов"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text='👤 По пользователю')],
            [KeyboardButton(text='📅 По дате')],
            [KeyboardButton(text='🔄 Без сортировки')],
            [KeyboardButton(text='🔎 По статусу')],
            [KeyboardButton(text='◀️ Назад в меню')],
        ],
        resize_keyboard=True
    )


GUEST_KEYBOARD = get_guest_keyboard()
CUSTOMER_KEYBOARD = get_customer_keyboard()
ADMIN_KEYBOARD = get_admin_keyboard()
ADMIN_FILTER_KEYBOARD = get_admin_filter_keyboard()


# ========================
# MIDDLEWARE
# ========================

class LoggingMiddleware(BaseMiddleware):
    """Middleware для логирования запросов"""

    async def __call__(self, handler, event: Update, data):
        import time
        start_time = time.time()
        user_id = None

        try:
            if event.message:
                user_id = event.message.from_user.id
                text = event.message.text[:50] if event.message.text else "media"
                logger.info(f"📨 Message from {user_id}: {text}")
            elif event.callback_query:
                user_id = event.callback_query.from_user.id
                logger.info(f"🔘 Callback from {user_id}: {event.callback_query.data}")

            result = await handler(event, data)
            elapsed = time.time() - start_time
            logger.debug(f"✅ Handler completed in {elapsed:.2f}s")
            return result

        except Exception as e:
            logger.error(f"❌ Error in handler: {str(e)}", exc_info=True)
            if event.message:
                try:
                    await event.message.answer("❌ Ошибка при обработке запроса. Попробуйте позже.")
                except:
                    pass
            elif event.callback_query:
                try:
                    await event.callback_query.answer("❌ Ошибка", show_alert=True)
                except:
                    pass
            raise


# ========================
# RATE LIMITER
# ========================

class RateLimiter:
    """Ограничение количества запросов"""

    def __init__(self):
        self.requests: Dict[str, list] = defaultdict(list)

    def is_allowed(
            self,
            user_id: int,
            action: str,
            max_requests: int = 5,
            time_window: int = 60
    ) -> Tuple[bool, int]:
        """
        Проверяет, разрешен ли запрос
        Возвращает (разрешен_ли, оставшиеся_попытки)
        """
        key = f"{user_id}:{action}"
        now = datetime.now()
        cutoff = now - timedelta(seconds=time_window)

        self.requests[key] = [req_time for req_time in self.requests[key] if req_time > cutoff]

        if len(self.requests[key]) >= max_requests:
            logger.warning(f"⚠️ Rate limit exceeded for {user_id}:{action}")
            return False, 0

        self.requests[key].append(now)
        remaining = max_requests - len(self.requests[key])
        return True, remaining


rate_limiter = RateLimiter()


# ========================
# SESSION MANAGER
# ========================

class SessionManager:
    """Менеджер сессий с timeout и автоочисткой"""

    def __init__(self):
        self.tokens: Dict[int, str] = {}
        self.roles: Dict[int, str] = {}
        self.order_mode: Dict[int, bool] = defaultdict(bool)
        self.delete_mode: Dict[int, bool] = defaultdict(bool)
        self.last_activity: Dict[int, datetime] = {}
        self.created_at: Dict[int, datetime] = {}
        self.cleanup_task: Optional[asyncio.Task] = None

    def start_cleanup(self):
        """Запустить фоновую очистку"""
        self.cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("✅ Session cleanup started")

    async def _cleanup_loop(self):
        """Фоновая очистка старых сессий"""
        while True:
            try:
                await asyncio.sleep(CLEANUP_INTERVAL)
                self._cleanup_expired_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Cleanup error: {str(e)}")

    def _cleanup_expired_sessions(self):
        """Удаляет истекшие сессии"""
        now = datetime.now()
        expired_users = []

        for user_id, created_time in self.created_at.items():
            if (now - created_time).total_seconds() > SESSION_TIMEOUT:
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

        last_activity = self.last_activity.get(telegram_id, datetime.now())
        if (datetime.now() - last_activity).total_seconds() > SESSION_TIMEOUT:
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


session_manager = SessionManager()


# ========================
# СОСТОЯНИЯ FSM
# ========================

class LoginState(StatesGroup):
    """Состояния для процесса логина"""
    username = State()
    password = State()


# ========================
# RETRY LOGIC
# ========================

async def retry_api_call(
        func: Callable,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        timeout: int = HANDLER_TIMEOUT
) -> Tuple[int, Any]:
    """
    Повторяет API запрос при ошибке с экспоненциальным backoff
    """
    last_error = None

    for attempt in range(max_retries):
        try:
            logger.debug(f"API call attempt {attempt + 1}/{max_retries}")
            result = await asyncio.wait_for(func(), timeout=timeout)
            return result

        except asyncio.TimeoutError:
            last_error = "Timeout"
            if attempt < max_retries - 1:
                wait_time = backoff_base ** attempt
                logger.warning(f"⏱️ Timeout. Retry after {wait_time}s")
                await asyncio.sleep(wait_time)

        except Exception as e:
            last_error = str(e)
            if attempt < max_retries - 1:
                wait_time = backoff_base ** attempt
                logger.warning(f"⚠️ Error: {str(e)}. Retry after {wait_time}s")
                await asyncio.sleep(wait_time)

    logger.error(f"❌ API call failed after {max_retries} attempts: {last_error}")
    return 500, None


def get_error_message(status_code: int) -> str:
    """Получить сообщение об ошибке по коду"""
    return ERROR_MESSAGES.get(status_code, f"❌ Ошибка {status_code}")


# ========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ========================

def format_date(date_string: str, tz: str = TIMEZONE) -> str:
    """Форматирует дату ISO в локальную дату"""
    try:
        dt = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
        dt_local = dt.astimezone(ZoneInfo(tz))
        return dt_local.strftime('%d.%m.%Y %H:%M')
    except (ValueError, TypeError):
        return date_string


def split_message(text: str, max_length: int = 4096) -> List[str]:
    """Разбивает длинное сообщение на части"""
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


def validate_order_text(text: str, min_length: int = 5, max_length: int = 500) -> Tuple[bool, str]:
    """Валидирует текст заказа"""
    if not text or not text.strip():
        return False, "❌ Текст заказа не может быть пустым"

    if len(text) < min_length:
        return False, f"❌ Текст заказа должен быть минимум {min_length} символов"

    if len(text) > max_length:
        return False, f"❌ Текст заказа не должен превышать {max_length} символов"

    return True, ""


def validate_order_id(order_id: str) -> Tuple[bool, Optional[int]]:
    """Валидирует ID заказа"""
    try:
        order_id_int = int(order_id.strip())
        if order_id_int > 0:
            return True, order_id_int
        return False, None
    except ValueError:
        return False, None


def sort_orders_by_user(orders: List[Dict]) -> Dict[str, List[Dict]]:
    """Группирует заказы по пользователям"""
    grouped = defaultdict(list)
    for order in orders:
        user_info = order.get('user_details', {})
        username = user_info.get('username', 'Unknown')
        grouped[username].append(order)
    return grouped


def sort_orders_by_date(orders: List[Dict]) -> List[Dict]:
    """Сортирует заказы по дате"""
    return sorted(orders, key=lambda x: x.get('created_at', ''), reverse=True)


def filter_orders_by_status(orders: List[Dict], status: str) -> List[Dict]:
    """Фильтрует заказы по статусу"""
    return [o for o in orders if o.get('status') == status]


def format_order(order: Dict, with_user: bool = False) -> str:
    """Форматирует заказ для отображения"""
    created_at = format_date(order.get('created_at', 'N/A'))
    status = ORDER_STATUSES.get(order.get('status', 'UNKNOWN'), order.get('status', 'UNKNOWN'))

    text = (
        f'🆔 ID: <b>{order.get("id", "N/A")}</b>\n'
        f'📝 <b>Описание:</b> {order.get("text", "N/A")}\n'
        f'📌 <b>Статус:</b> {status}\n'
        f'📅 <b>Создан:</b> {created_at}'
    )

    if with_user:
        user_info = order.get('user_details', {})
        username = user_info.get('username', 'Unknown')
        text = f'👤 <b>Пользователь:</b> {username}\n' + text

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
            InlineKeyboardButton(text='📋 Все заказы', callback_data='filter_ALL'),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ========================
# API ЗАПРОСЫ
# ========================

async def api_request(
        method: str,
        url: str,
        token: Optional[str] = None,
        json_data: Optional[Dict] = None,
        timeout: int = API_TIMEOUT
) -> Tuple[int, Optional[Dict]]:
    """Универсальная функция для API запросов с retry логикой"""

    async def _make_request():
        try:
            headers = {}
            if token:
                headers['Authorization'] = f'Token {token}'

            client_timeout = aiohttp.ClientTimeout(total=timeout)

            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.request(
                        method=method,
                        url=url,
                        json=json_data,
                        headers=headers
                ) as response:
                    if response.status in [200, 201, 204]:
                        if response.status == 204:
                            return response.status, None
                        data = await asyncio.wait_for(response.json(), timeout=5)
                        return response.status, data

                    return response.status, None

        except asyncio.TimeoutError:
            logger.error(f"API Timeout: {method} {url}")
            return 504, None
        except Exception as e:
            logger.error(f"API Error: {method} {url} - {str(e)}")
            return 500, None

    return await retry_api_call(_make_request, max_retries=3)


async def api_login(username: str, password: str, telegram_id: int) -> Tuple[int, Optional[Dict]]:
    """Логин пользователя"""
    return await api_request(
        'POST',
        API_LOGIN,
        json_data={
            'username': username,
            'password': password,
            'telegram_id': telegram_id,
        }
    )


async def api_get_profile(token: str) -> Tuple[int, Optional[Dict]]:
    """Получить профиль пользователя"""
    return await api_request('GET', API_PROFILE, token=token)


async def api_get_orders(token: str, params: Optional[Dict] = None) -> Tuple[int, Optional[List[Dict]]]:
    """Получить список заказов"""
    url = API_ORDERS
    if params:
        query_string = '&'.join(f"{k}={v}" for k, v in params.items())
        url = f"{API_ORDERS}?{query_string}"

    try:
        status, data = await api_request('GET', url, token=token)
        if status == 200 and data:
            orders = data.get('results', data) if isinstance(data, dict) else data
            return status, orders if isinstance(orders, list) else [orders]
        return status, None
    except Exception as e:
        logger.error(f"Error fetching orders: {str(e)}")
        return 500, None


async def api_create_order(token: str, text: str) -> Tuple[int, Optional[Dict]]:
    """Создать заказ"""
    return await api_request(
        'POST',
        API_ORDERS,
        token=token,
        json_data={'text': text, 'status': 'NEW'}
    )


async def api_delete_order(token: str, order_id: int) -> Tuple[int, Optional[str]]:
    """Удалить заказ"""
    try:
        status, _ = await api_request(
            'DELETE',
            f'{API_ORDERS}{order_id}/',
            token=token
        )
        return status, None
    except Exception as e:
        return 500, str(e)


async def api_update_order_status(token: str, order_id: int, status: str) -> Tuple[int, Optional[str]]:
    """Обновить статус заказа"""
    try:
        status_code, _ = await api_request(
            'PATCH',
            f'{API_ORDERS}{order_id}/',
            token=token,
            json_data={'status': status}
        )
        return status_code, None
    except Exception as e:
        return 500, str(e)


# ========================
# ОБРАБОТЧИКИ: СТАРТ И ОСНОВНОЕ
# ========================

@dp.message(Command('start'))
async def start_handler(message: Message, state: FSMContext):
    """Обработчик команды /start"""
    try:
        telegram_id = message.from_user.id
        session_manager.clear_session(telegram_id)
        await state.clear()
        logger.info(f"User {telegram_id}: START")
        await message.answer(
            '👋 Добро пожаловать в систему управления заказами!\n\n'
            '📌 Используйте кнопку <b>🔐 Логин</b> для входа.',
            parse_mode='HTML',
            reply_markup=GUEST_KEYBOARD
        )
    except Exception as e:
        logger.error(f"START_ERROR: {str(e)}")
        await message.answer('❌ Произошла ошибка. Попробуйте позже.')


@dp.message(Command('help'))
async def help_handler(message: Message):
    """Обработчик команды /help"""
    try:
        telegram_id = message.from_user.id
        role = session_manager.get_role(telegram_id)

        help_text = """
<b>📖 Справка по командам:</b>

<b>Для всех:</b>
/start - Начать работу
/help - Эта справка
/profile - Ваш профиль
/logout - Выход
/health - Проверить статус API

<b>Для заказчиков:</b>
/orders - Мои заказы
/create_order - Создать заказ
/delete_order - Удалить заказ

<b>Для администраторов:</b>
/all_orders - Все заказы
/stats - Статистика
        """

        await message.answer(help_text, parse_mode='HTML')
        logger.info(f"User {telegram_id}: HELP")
    except Exception as e:
        logger.error(f"HELP_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


@dp.message(Command('health'))
async def health_check(message: Message):
    """Проверить доступность API"""
    try:
        telegram_id = message.from_user.id
        await message.answer('⏳ Проверяю доступность API...')

        status, _ = await asyncio.wait_for(
            api_request('GET', f"{API_BASE_URL}/api/", timeout=5),
            timeout=10
        )

        if status == 200:
            await message.answer('✅ API доступен и работает корректно')
            logger.info(f"User {telegram_id}: HEALTH_CHECK - OK")
        else:
            await message.answer(f'⚠️ API вернул ошибку: {status}')
            logger.warning(f"User {telegram_id}: HEALTH_CHECK - ERROR {status}")

    except asyncio.TimeoutError:
        await message.answer('⏱️ Таймаут при подключении к API')
        logger.error(f"HEALTH_CHECK: Timeout")
    except Exception as e:
        await message.answer(f'❌ Ошибка: {str(e)}')
        logger.error(f"HEALTH_CHECK_ERROR: {str(e)}")


# ========================
# ОБРАБОТЧИКИ: ЛОГИН
# ========================

@dp.message(lambda m: m.text == '🔐 Логин')
async def login_button(message: Message, state: FSMContext):
    """Начало процесса логина"""
    try:
        telegram_id = message.from_user.id

        # Rate limiting
        is_allowed, remaining = rate_limiter.is_allowed(
            telegram_id, 'login',
            max_requests=LOGIN_MAX_ATTEMPTS,
            time_window=LOGIN_TIME_WINDOW
        )

        if not is_allowed:
            await message.answer(
                f'⏱️ Слишком много попыток логина. Попробуйте через 5 минут.\n'
                f'Осталось попыток: {remaining}',
                reply_markup=GUEST_KEYBOARD
            )
            logger.warning(f"User {telegram_id}: LOGIN_RATE_LIMITED")
            return

        logger.info(f"User {telegram_id}: LOGIN_START")
        await state.set_state(LoginState.username)
        await message.answer('📝 Введите ваше имя пользователя (username):')
    except Exception as e:
        logger.error(f"LOGIN_BUTTON_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте снова.')


@dp.message(LoginState.username)
async def username_handler(message: Message, state: FSMContext):
    """Обработчик ввода username"""
    try:
        username = message.text.strip()
        if not username:
            await message.answer('❌ Логин не может быть пустым. Попробуйте снова:')
            return

        await state.update_data(username=username)
        await state.set_state(LoginState.password)
        await message.answer('🔐 Введите ваш пароль:')
    except Exception as e:
        logger.error(f"USERNAME_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Введите логин заново.')
        await state.set_state(LoginState.username)


@dp.message(LoginState.password)
async def password_handler(message: Message, state: FSMContext):
    """Обработчик ввода пароля и логина"""
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
            session_manager.set_session(telegram_id, token, role)
            await state.clear()

            logger.info(f"User {telegram_id}: LOGIN_SUCCESS (role={role})")

            keyboard = ADMIN_KEYBOARD if role == 'admin' else CUSTOMER_KEYBOARD
            await message.answer(
                f'✅ Вы успешно вошли как <b>{role.upper()}</b>',
                parse_mode='HTML',
                reply_markup=keyboard
            )

        elif status in [401, 403]:
            logger.warning(f"User {telegram_id}: LOGIN_FAILED for {username}")
            await message.answer(
                '❌ Неверный логин или пароль\n\n'
                '🔄 Попробуйте снова. Введите логин:',
                reply_markup=GUEST_KEYBOARD
            )
            await state.set_state(LoginState.username)

        elif status == 504:
            await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
            await state.set_state(LoginState.username)

        else:
            error_msg = get_error_message(status)
            logger.error(f"User {telegram_id}: LOGIN_ERROR (status={status})")
            await message.answer(f'{error_msg}\n\n🔄 Попробуйте позже.', reply_markup=GUEST_KEYBOARD)
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
@dp.message(Command('profile'))
async def profile_handler(message: Message):
    """Показать профиль пользователя"""
    try:
        telegram_id = message.from_user.id
        token = session_manager.get_token(telegram_id)

        if not token:
            await message.answer(
                '❌ Сначала войдите 🔐',
                reply_markup=GUEST_KEYBOARD
            )
            return

        await message.answer('⏳ Загружаю профиль...')
        session_manager.update_activity(telegram_id)

        status, result = await asyncio.wait_for(
            api_get_profile(token),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and result:
            role = session_manager.get_role(telegram_id)
            role_text = '👨‍💼 Администратор' if role == 'admin' else '👤 Заказчик'

            profile_text = (
                f'<b>👤 Ваш профиль</b>\n\n'
                f'<b>Username:</b> {result.get("username", "N/A")}\n'
                f'<b>Telegram ID:</b> <code>{telegram_id}</code>\n'
                f'<b>Роль:</b> {role_text}\n'
                f'<b>Заказов:</b> {result.get("orders_count", 0)}'
            )

            logger.info(f"User {telegram_id}: VIEW_PROFILE")
            await message.answer(profile_text, parse_mode='HTML')

        elif status in [401, 403]:
            session_manager.clear_session(telegram_id)
            await message.answer(
                '❌ Ваша сессия истекла. Пожалуйста, войдите заново.',
                reply_markup=GUEST_KEYBOARD
            )

        else:
            error_msg = get_error_message(status)
            logger.error(f"User {telegram_id}: PROFILE_ERROR (status={status})")
            await message.answer(error_msg)

    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
    except Exception as e:
        logger.error(f"PROFILE_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


# ========================
# ОБРАБОТЧИКИ: МОИ ЗАКАЗЫ
# ========================

@dp.message(lambda m: m.text == '📦 Мои заказы')
@dp.message(Command('orders'))
async def my_orders_handler(message: Message):
    """Показать заказы пользователя"""
    try:
        telegram_id = message.from_user.id
        token = session_manager.get_token(telegram_id)

        if not token:
            await message.answer(
                '❌ Сначала войдите 🔐',
                reply_markup=GUEST_KEYBOARD
            )
            return

        await message.answer('⏳ Загружаю заказы...')
        session_manager.update_activity(telegram_id)

        status, orders = await asyncio.wait_for(
            api_get_orders(token),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and orders:
            orders_to_show = orders[:MAX_ORDERS_DISPLAY]
            text = f'📦 <b>Ваши заказы</b> ({len(orders_to_show)} из {len(orders)}):\n\n'

            for order in orders_to_show:
                text += format_order(order) + '\n\n'

            logger.info(f"User {telegram_id}: VIEW_MY_ORDERS (count={len(orders)}, showed={len(orders_to_show)})")

            for message_part in split_message(text):
                await message.answer(message_part, parse_mode='HTML')

        elif status == 200 and not orders:
            await message.answer('📭 У вас нет заказов')

        elif status in [401, 403]:
            session_manager.clear_session(telegram_id)
            await message.answer(
                '❌ Ваша сессия истекла. Пожалуйста, войдите заново.',
                reply_markup=GUEST_KEYBOARD
            )

        else:
            error_msg = get_error_message(status)
            logger.error(f"User {telegram_id}: MY_ORDERS_ERROR (status={status})")
            await message.answer(error_msg)

    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
    except Exception as e:
        logger.error(f"MY_ORDERS_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


# ========================
# ОБРАБОТЧИКИ: СОЗДАНИЕ ЗАКАЗА
# ========================

@dp.message(lambda m: m.text == '🛒 Сделать заказ')
@dp.message(Command('create_order'))
async def order_button(message: Message, state: FSMContext):
    """Начать создание заказа"""
    try:
        telegram_id = message.from_user.id
        token = session_manager.get_token(telegram_id)

        if not token:
            await message.answer(
                '❌ Сначала войдите 🔐',
                reply_markup=GUEST_KEYBOARD
            )
            return

        # Rate limiting для создания заказов
        is_allowed, remaining = rate_limiter.is_allowed(
            telegram_id, 'create_order',
            max_requests=ORDER_MAX_ATTEMPTS,
            time_window=ORDER_TIME_WINDOW
        )

        if not is_allowed:
            await message.answer(
                f'⏱️ Превышен лимит создания заказов (максимум {ORDER_MAX_ATTEMPTS} в час).\n'
                f'Осталось попыток: {remaining}'
            )
            logger.warning(f"User {telegram_id}: CREATE_ORDER_RATE_LIMITED")
            return

        session_manager.order_mode[telegram_id] = True
        logger.info(f"User {telegram_id}: ORDER_START")
        await message.answer('📝 Опишите ваш заказ (минимум 5, максимум 500 символов):')
    except Exception as e:
        logger.error(f"ORDER_BUTTON_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


@dp.message(lambda m: session_manager.order_mode.get(m.from_user.id, False))
async def create_order(message: Message):
    """Создать заказ"""
    try:
        telegram_id = message.from_user.id
        token = session_manager.get_token(telegram_id)
        order_text = message.text

        is_valid, error_msg = validate_order_text(order_text)
        if not is_valid:
            await message.answer(error_msg)
            return

        await message.answer('⏳ Создаю заказ...')

        status, result = await asyncio.wait_for(
            api_create_order(token, order_text),
            timeout=HANDLER_TIMEOUT
        )

        if status == 201 and result:
            order_id = result.get('id', 'N/A')
            logger.info(f"User {telegram_id}: ORDER_CREATED (id={order_id})")

            await message.answer(
                f'✅ <b>Заказ создан успешно!</b>\n\n'
                f'🆔 <b>ID заказа:</b> <code>{order_id}</code>',
                parse_mode='HTML'
            )

            session_manager.order_mode[telegram_id] = False

            # Отправить уведомление админам
            async def notify_admins():
                user_name = (message.from_user.username or
                             f"{message.from_user.first_name} {message.from_user.last_name or ''}").strip()
                dt_created = result.get("created_at", "")

                admin_message = (
                    f'🆕 <b>Новый заказ!</b>\n\n'
                    f'👤 <b>Пользователь:</b> {user_name}\n'
                    f'🆔 <b>ID:</b> <code>{order_id}</code>\n'
                    f'📝 <b>Описание:</b> {order_text}\n'
                    f'📌 <b>Статус:</b> {ORDER_STATUSES["NEW"]}\n'
                    f'📅 <b>Создан:</b> {format_date(dt_created)}'
                )

                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, admin_message, parse_mode='HTML')
                    except Exception as e:
                        logger.error(f"Error notifying admin {admin_id}: {e}")

            asyncio.create_task(notify_admins())

        elif status in [401, 403]:
            session_manager.clear_session(telegram_id)
            await message.answer(
                '❌ Ваша сессия истекла. Пожалуйста, войдите заново.',
                reply_markup=GUEST_KEYBOARD
            )
            session_manager.order_mode[telegram_id] = False

        else:
            error_msg = get_error_message(status)
            logger.error(f"User {telegram_id}: ORDER_CREATE_ERROR (status={status})")
            await message.answer(error_msg)
            session_manager.order_mode[telegram_id] = False

    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
        session_manager.order_mode[telegram_id] = False
    except Exception as e:
        logger.error(f"CREATE_ORDER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')
        session_manager.order_mode[telegram_id] = False


# ========================
# ОБРАБОТЧИКИ: УДАЛЕНИЕ ЗАКАЗА
# ========================

@dp.message(lambda m: m.text == '❌ Удалить заказ')
@dp.message(Command('delete_order'))
async def delete_order_button(message: Message):
    """Начать удаление заказа"""
    try:
        telegram_id = message.from_user.id
        token = session_manager.get_token(telegram_id)

        if not token:
            await message.answer(
                '❌ Сначала войдите 🔐',
                reply_markup=GUEST_KEYBOARD
            )
            return

        session_manager.delete_mode[telegram_id] = True
        logger.info(f"User {telegram_id}: DELETE_ORDER_START")
        await message.answer('❌ Введите <b>ID заказа</b> для удаления:', parse_mode='HTML')
    except Exception as e:
        logger.error(f"DELETE_ORDER_BUTTON_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


@dp.message(lambda m: session_manager.delete_mode.get(m.from_user.id, False))
async def delete_order(message: Message):
    """Удалить заказ"""
    try:
        telegram_id = message.from_user.id
        token = session_manager.get_token(telegram_id)
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
            await message.answer(f'✅ Заказ <code>#{order_id}</code> удален', parse_mode='HTML')

        elif status in [401, 403]:
            session_manager.clear_session(telegram_id)
            await message.answer(
                '❌ Ваша сессия истекла. Пожалуйста, войдите заново.',
                reply_markup=GUEST_KEYBOARD
            )

        else:
            error_msg = get_error_message(status)
            logger.error(f"User {telegram_id}: ORDER_DELETE_ERROR (id={order_id}, status={status})")
            await message.answer(error_msg)

        session_manager.delete_mode[telegram_id] = False

    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
        session_manager.delete_mode[telegram_id] = False
    except Exception as e:
        logger.error(f"DELETE_ORDER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')
        session_manager.delete_mode[telegram_id] = False


# ========================
# АДМИН: ЗАКАЗЫ
# ========================

@dp.message(lambda m: m.text == '📦 Все заказы')
@dp.message(Command('all_orders'))
async def admin_orders_handler(message: Message):
    """Показать все заказы (для админов)"""
    try:
        telegram_id = message.from_user.id
        token = session_manager.get_token(telegram_id)
        role = session_manager.get_role(telegram_id)

        if not token or role != 'admin':
            await message.answer('🔒 У вас нет доступа к этой команде')
            return

        await message.answer('⏳ Загружаю заказы...')
        session_manager.update_activity(telegram_id)

        status, orders = await asyncio.wait_for(
            api_get_orders(token),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and orders:
            orders_to_show = orders[:MAX_ORDERS_DISPLAY]

            logger.info(f"Admin {telegram_id}: VIEW_ALL_ORDERS (count={len(orders)}, showed={len(orders_to_show)})")

            for order in orders_to_show:
                text = format_order(order, with_user=True)
                keyboard = get_status_keyboard(order.get('id'))
                await message.answer(text, parse_mode='HTML', reply_markup=keyboard)

            await message.answer(
                '👇 Выберите действие:',
                reply_markup=ADMIN_FILTER_KEYBOARD
            )

        elif status == 200 and not orders:
            await message.answer(
                '📭 Заказов нет',
                reply_markup=ADMIN_FILTER_KEYBOARD
            )

        elif status in [401, 403]:
            session_manager.clear_session(telegram_id)
            await message.answer(
                '❌ Ваша сессия истекла. Пожалуйста, войдите заново.',
                reply_markup=GUEST_KEYBOARD
            )

        else:
            error_msg = get_error_message(status)
            logger.error(f"Admin {telegram_id}: ALL_ORDERS_ERROR (status={status})")
            await message.answer(error_msg)

    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
    except Exception as e:
        logger.error(f"ADMIN_ORDERS_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


# ========================
# АДМИН: ФИЛЬТРЫ И СОРТИРОВКА
# ========================

@dp.message(lambda m: m.text == '🔄 Без сортировки')
async def no_sort_handler(message: Message):
    """Показать все заказы без фильтра"""
    try:
        telegram_id = message.from_user.id
        token = session_manager.get_token(telegram_id)
        role = session_manager.get_role(telegram_id)

        if not token or role != 'admin':
            await message.answer('🔒 У вас нет доступа')
            return

        await message.answer('⏳ Загружаю заказы...')

        status, orders = await asyncio.wait_for(
            api_get_orders(token),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and orders:
            orders_to_show = orders[:MAX_ORDERS_DISPLAY]

            for order in orders_to_show:
                text = format_order(order, with_user=True)
                keyboard = get_status_keyboard(order.get('id'))
                await message.answer(text, parse_mode='HTML', reply_markup=keyboard)

            await message.answer('Сортировать/фильтровать:', reply_markup=ADMIN_FILTER_KEYBOARD)

        elif status == 200 and not orders:
            await message.answer('📭 Заказов нет', reply_markup=ADMIN_FILTER_KEYBOARD)

        elif status in [401, 403]:
            session_manager.clear_session(telegram_id)
            await message.answer('❌ Сессия истекла.', reply_markup=GUEST_KEYBOARD)

        else:
            error_msg = get_error_message(status)
            await message.answer(error_msg, reply_markup=ADMIN_FILTER_KEYBOARD)

    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает.', reply_markup=ADMIN_FILTER_KEYBOARD)
    except Exception as e:
        logger.error(f"NO_SORT_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка.', reply_markup=ADMIN_FILTER_KEYBOARD)


@dp.message(lambda m: m.text == '🔎 По статусу')
async def filter_by_status_menu(message: Message):
    """Показать меню выбора статуса"""
    try:
        await message.answer(
            '<b>Выберите статус для фильтрации:</b>',
            parse_mode='HTML',
            reply_markup=get_status_filter_keyboard()
        )
    except Exception as e:
        logger.error(f"FILTER_STATUS_MENU_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


@dp.callback_query(lambda c: c.data.startswith('filter_'))
async def filter_orders_by_status_handler(callback: CallbackQuery):
    """Обработчик фильтрации по статусу"""
    try:
        telegram_id = callback.from_user.id
        token = session_manager.get_token(telegram_id)
        role = session_manager.get_role(telegram_id)

        if not token or role != 'admin':
            await callback.answer('🔒 У вас нет доступа', show_alert=True)
            return

        status_filter = callback.data.split('_', 1)[1]

        await callback.answer('⏳ Загружаю заказы...')

        status, orders = await asyncio.wait_for(
            api_get_orders(token),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and orders:
            if status_filter == 'ALL':
                filtered_orders = orders
                title = '📦 <b>Все заказы</b>:\n\n'
            else:
                filtered_orders = filter_orders_by_status(orders, status_filter)
                title = f'{ORDER_STATUSES.get(status_filter, status_filter)} <b>заказы</b>:\n\n'

            filtered_orders = filtered_orders[:MAX_ORDERS_DISPLAY]

            if filtered_orders:
                text = title
                for order in filtered_orders:
                    text += format_order(order, with_user=True) + '\n\n'

                logger.info(
                    f"Admin {telegram_id}: FILTER_BY_STATUS (status={status_filter}, count={len(filtered_orders)})"
                )

                for message_part in split_message(text):
                    await callback.message.answer(message_part, parse_mode='HTML')

                await callback.message.answer(
                    'Сортировать/фильтровать:',
                    reply_markup=ADMIN_FILTER_KEYBOARD
                )
            else:
                await callback.message.answer(
                    f'📭 Заказов со статусом {ORDER_STATUSES.get(status_filter, status_filter)} не найдено',
                    reply_markup=ADMIN_FILTER_KEYBOARD
                )

        elif status in [401, 403]:
            session_manager.clear_session(telegram_id)
            await callback.message.answer('❌ Сессия истекла.', reply_markup=GUEST_KEYBOARD)

        else:
            error_msg = get_error_message(status)
            logger.error(f"Admin {telegram_id}: FILTER_STATUS_ERROR (status={status})")
            await callback.message.answer(error_msg, reply_markup=ADMIN_FILTER_KEYBOARD)

    except asyncio.TimeoutError:
        await callback.message.answer('⏱️ Сервер не отвечает.', reply_markup=ADMIN_FILTER_KEYBOARD)
    except Exception as e:
        logger.error(f"FILTER_STATUS_HANDLER_ERROR: {str(e)}")
        await callback.answer('❌ Ошибка', show_alert=True)


@dp.message(lambda m: m.text == '👤 По пользователю')
async def filter_by_user(message: Message):
    """Фильтровать заказы по пользователям"""
    try:
        telegram_id = message.from_user.id
        token = session_manager.get_token(telegram_id)
        role = session_manager.get_role(telegram_id)

        if not token or role != 'admin':
            await message.answer('🔒 У вас нет доступа')
            return

        await message.answer('⏳ Загружаю заказы...')

        status, orders = await asyncio.wait_for(
            api_get_orders(token),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and orders:
            grouped = sort_orders_by_user(orders)
            text = '📦 <b>Заказы по пользователям</b>:\n\n'
            users_to_show = list(sorted(grouped.keys()))[:MAX_USERS_DISPLAY]

            for username in users_to_show:
                user_orders = grouped[username][:5]
                text += f'👤 <b>{username}</b> ({len(user_orders)} заказов)\n'
                for order in user_orders:
                    order_display = format_order(order)
                    text += '\n'.join(f'  {line}' for line in order_display.split('\n')) + '\n\n'

            logger.info(f"Admin {telegram_id}: FILTER_BY_USER (users={len(users_to_show)})")

            for message_part in split_message(text):
                await message.answer(message_part, parse_mode='HTML')

            await message.answer(
                'Сортировать/фильтровать:',
                reply_markup=ADMIN_FILTER_KEYBOARD
            )

        elif status == 200 and not orders:
            await message.answer('📭 Заказов нет', reply_markup=ADMIN_FILTER_KEYBOARD)

        elif status in [401, 403]:
            session_manager.clear_session(telegram_id)
            await message.answer('❌ Сессия истекла.', reply_markup=GUEST_KEYBOARD)

        else:
            error_msg = get_error_message(status)
            logger.error(f"Admin {telegram_id}: FILTER_USER_ERROR (status={status})")
            await message.answer(error_msg, reply_markup=ADMIN_FILTER_KEYBOARD)

    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает.', reply_markup=ADMIN_FILTER_KEYBOARD)
    except Exception as e:
        logger.error(f"FILTER_BY_USER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка.', reply_markup=ADMIN_FILTER_KEYBOARD)


@dp.message(lambda m: m.text == '📅 По дате')
async def filter_by_date(message: Message):
    """Фильтровать заказы по дате"""
    try:
        telegram_id = message.from_user.id
        token = session_manager.get_token(telegram_id)
        role = session_manager.get_role(telegram_id)

        if not token or role != 'admin':
            await message.answer('🔒 У вас нет доступа')
            return

        await message.answer('⏳ Загружаю заказы...')

        status, orders = await asyncio.wait_for(
            api_get_orders(token),
            timeout=HANDLER_TIMEOUT
        )

        if status == 200 and orders:
            sorted_orders = sort_orders_by_date(orders)
            sorted_orders = sorted_orders[:MAX_ORDERS_DISPLAY]

            text = '📦 <b>Заказы по дате (новые первыми)</b>:\n\n'
            for order in sorted_orders:
                text += format_order(order, with_user=True) + '\n\n'

            logger.info(f"Admin {telegram_id}: FILTER_BY_DATE (count={len(sorted_orders)})")

            for message_part in split_message(text):
                await message.answer(message_part, parse_mode='HTML')

            await message.answer(
                'Сортировать/фильтровать:',
                reply_markup=ADMIN_FILTER_KEYBOARD
            )

        elif status == 200 and not orders:
            await message.answer('📭 Заказов нет', reply_markup=ADMIN_FILTER_KEYBOARD)

        elif status in [401, 403]:
            session_manager.clear_session(telegram_id)
            await message.answer('❌ Сессия истекла.', reply_markup=GUEST_KEYBOARD)

        else:
            error_msg = get_error_message(status)
            logger.error(f"Admin {telegram_id}: FILTER_DATE_ERROR (status={status})")
            await message.answer(error_msg, reply_markup=ADMIN_FILTER_KEYBOARD)

    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает.', reply_markup=ADMIN_FILTER_KEYBOARD)
    except Exception as e:
        logger.error(f"FILTER_BY_DATE_ERROR: {str(e)}")
        await message.answer('❌ Ошибка.', reply_markup=ADMIN_FILTER_KEYBOARD)


# ========================
# АДМИН: СТАТИСТИКА
# ========================

@dp.message(lambda m: m.text == '📊 Статистика')
@dp.message(Command('stats'))
async def statistics_handler(message: Message):
    """Показать статистику заказов"""
    try:
        telegram_id = message.from_user.id
        token = session_manager.get_token(telegram_id)
        role = session_manager.get_role(telegram_id)

        if not token or role != 'admin':
            await message.answer('🔒 У вас нет доступа к этой команде')
            return

        await message.answer('⏳ Подсчитываю статистику...')
        session_manager.update_activity(telegram_id)

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

            completion_rate = round((done_orders / total_orders * 100) if total_orders > 0 else 0)

            logger.info(f"Admin {telegram_id}: VIEW_STATS (total={total_orders})")

            stats_text = (
                f'<b>📊 Статистика заказов</b>\n\n'
                f'📦 <b>Всего заказов:</b> {total_orders}\n'
                f'🆕 <b>Новых:</b> {new_orders}\n'
                f'⚙️ <b>В процессе:</b> {in_progress}\n'
                f'✅ <b>Завершено:</b> {done_orders}\n'
                f'❌ <b>Отменено:</b> {canceled_orders}\n\n'
                f'📈 <b>Процент выполнения:</b> {completion_rate}%'
            )

            await message.answer(stats_text, parse_mode='HTML')

        elif status in [401, 403]:
            session_manager.clear_session(telegram_id)
            await message.answer('❌ Ваша сессия истекла.', reply_markup=GUEST_KEYBOARD)

        else:
            error_msg = get_error_message(status)
            logger.error(f"Admin {telegram_id}: STATS_ERROR (status={status})")
            await message.answer(error_msg)

    except asyncio.TimeoutError:
        await message.answer('⏱️ Сервер не отвечает. Попробуйте позже.')
    except Exception as e:
        logger.error(f"STATISTICS_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка. Попробуйте позже.')


# ========================
# АДМИН: ИЗМЕНЕНИЕ СТАТУСА ЗАКАЗА
# ========================

@dp.callback_query(lambda c: c.data.startswith('status_'))
async def change_order_status(callback: CallbackQuery):
    """Изменить статус заказа"""
    try:
        telegram_id = callback.from_user.id
        token = session_manager.get_token(telegram_id)
        role = session_manager.get_role(telegram_id)

        if not token or role != 'admin':
            await callback.answer('🔒 У вас нет доступа', show_alert=True)
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
            session_manager.clear_session(telegram_id)
            await callback.answer('❌ Сессия истекла', show_alert=True)

        else:
            error_msg_text = get_error_message(status)
            logger.error(f"Admin {telegram_id}: ORDER_STATUS_ERROR (id={order_id}, status={new_status})")
            await callback.answer(error_msg_text, show_alert=True)

    except asyncio.TimeoutError:
        await callback.answer('⏱️ Сервер не отвечает', show_alert=True)
    except Exception as e:
        logger.error(f"CHANGE_ORDER_STATUS_ERROR: {str(e)}")
        await callback.answer('❌ Ошибка', show_alert=True)


# ========================
# ОБРАБОТЧИКИ: ВЫХОД
# ========================

@dp.message(lambda m: m.text == '🚪 Выйти')
@dp.message(Command('logout'))
async def logout_handler(message: Message, state: FSMContext):
    """Выход пользователя"""
    try:
        telegram_id = message.from_user.id
        session_manager.clear_session(telegram_id)
        await state.clear()
        logger.info(f"User {telegram_id}: LOGOUT")
        await message.answer(
            '👋 Вы вышли из системы',
            reply_markup=GUEST_KEYBOARD
        )
    except Exception as e:
        logger.error(f"LOGOUT_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка при выходе')


@dp.message(lambda m: m.text == '◀️ Назад в меню')
@dp.message(lambda m: m.text == '◀️ Назад')
async def back_handler(message: Message):
    """Вернуться в главное меню"""
    try:
        telegram_id = message.from_user.id
        role = session_manager.get_role(telegram_id)

        if role == 'admin':
            await message.answer('👈 Вернулись в главное меню', reply_markup=ADMIN_KEYBOARD)
        else:
            await message.answer('👈 Вернулись в главное меню', reply_markup=CUSTOMER_KEYBOARD)
    except Exception as e:
        logger.error(f"BACK_HANDLER_ERROR: {str(e)}")
        await message.answer('❌ Ошибка')


@dp.message(lambda m: m.text == '❓ Помощь')
async def help_button(message: Message):
    """Справка в меню"""
    try:
        help_text = """
<b>📖 Справка</b>

<b>Основные команды:</b>
/start - Начать
/help - Справка
/profile - Профиль
/orders - Мои заказы
/logout - Выход
/health - Проверить API

<b>Заказчику:</b>
🛒 Сделать заказ
❌ Удалить заказ

<b>Администратору:</b>
📦 Все заказы
📊 Статистика
🔎 По статусу
👤 По пользователю
📅 По дате
        """
        await message.answer(help_text, parse_mode='HTML')
        logger.info(f"User {message.from_user.id}: HELP_BUTTON")
    except Exception as e:
        logger.error(f"HELP_BUTTON_ERROR: {str(e)}")
        await message.answer('❌ Ошибка')


@dp.message()
async def fallback(message: Message):
    """Обработчик неизвестных команд"""
    try:
        await message.answer('🤔 Не понимаю эту команду.\n\nОтправьте /help для справки.')
        logger.warning(f"User {message.from_user.id}: UNKNOWN_COMMAND - {message.text}")
    except Exception as e:
        logger.error(f"FALLBACK_ERROR: {str(e)}")


# ========================
# ГЛАВНАЯ ФУНКЦИЯ
# ========================

async def main():
    """Запуск бота"""
    logger.info("=" * 60)
    logger.info("🤖 Telegram Bot v3.0 запущен")
    logger.info(f"API Base URL: {API_BASE_URL}")
    logger.info(f"Admin IDs: {ADMIN_IDS}")
    logger.info(f"Session timeout: {SESSION_TIMEOUT // 3600} часов")
    logger.info(f"Rate limiting: Login {LOGIN_MAX_ATTEMPTS} попыток в {LOGIN_TIME_WINDOW}s")
    logger.info("=" * 60)

    # Подключить middleware
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())

    # Запустить cleanup сессий
    session_manager.start_cleanup()

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        raise


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🤖 Бот остановлен пользователем")
        session_manager.stop_cleanup()
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        session_manager.stop_cleanup()
