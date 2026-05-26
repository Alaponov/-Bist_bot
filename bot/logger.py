"""Логирование для бота"""
import logging
from pathlib import Path

# Создаём папку для логов если её нет
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "bot.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


def log_user_action(action: str, telegram_id: int, details: str = ""):
    """Логирует действие пользователя"""
    msg = f"[User {telegram_id}] {action}"
    if details:
        msg += f" - {details}"
    logger.info(msg)


def log_error(error_type: str, details: str, telegram_id: int = None):
    """Логирует ошибку"""
    msg = f"[ERROR] {error_type}"
    if telegram_id:
        msg += f" [User {telegram_id}]"
    msg += f" - {details}"
    logger.error(msg)


def log_api_request(method: str, endpoint: str, status_code: int):
    """Логирует API запрос"""
    logger.debug(f"[API] {method} {endpoint} -> {status_code}")
