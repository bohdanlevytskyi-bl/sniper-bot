from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from sniper_bot.logging_config import get_logger

LOGGER = get_logger(__name__)


class TelegramError(RuntimeError):
    pass


class RetryableTelegramError(TelegramError):
    pass


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.client = httpx.Client(timeout=15)

    def close(self) -> None:
        self.client.close()

    @retry(
        retry=retry_if_exception_type(RetryableTelegramError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def send_message(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        response = self.client.post(url, json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"})
        if response.status_code in {429, 500, 502, 503}:
            raise RetryableTelegramError(f"Telegram HTTP {response.status_code}")
        if response.is_error:
            LOGGER.warning("telegram_send_failed", extra={"status": response.status_code, "body": response.text[:200]})


def format_alert(title: str, lines: list[str]) -> str:
    body = "\n".join(f"  {line}" for line in lines)
    return f"<b>{title}</b>\n{body}"
