"""Повторная попытка API запросов при ошибках"""

import asyncio
from typing import Callable, Any, Tuple, Optional, List, Dict

import logger

from bot.main import API_ORDERS, api_request


async def retry_api_call(
        func: Callable,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        timeout: int = 20
) -> Tuple[int, Any]:
    """
    Повторяет API запрос при ошибке

    Args:
        func: Асинхронная функция для выполнения
        max_retries: Максимальное количество попыток
        backoff_base: Базовое значение для экспоненциального отступа
        timeout: Таймаут для каждой попытки

    Returns:
        (status_code, data)
    """
    last_error = None

    for attempt in range(max_retries):
        try:
            logger.info(f"API call attempt {attempt + 1}/{max_retries}")
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


# Обновить API функции в main.py:
async def api_get_orders(token: str, params: Optional[Dict] = None) -> Tuple[int, Optional[List[Dict]]]:
    """Получить список заказов с retry логикой"""

    async def _fetch():
        url = API_ORDERS
        if params:
            query_string = '&'.join(f"{k}={v}" for k, v in params.items())
            url = f"{API_ORDERS}?{query_string}"

        return await api_request('GET', url, token=token)

    status, data = await retry_api_call(_fetch, max_retries=3)

    if status == 200 and data:
        orders = data.get('results', data) if isinstance(data, dict) else data
        return status, orders if isinstance(orders, list) else [orders]

    return status, None