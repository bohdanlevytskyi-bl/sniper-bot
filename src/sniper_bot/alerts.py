from __future__ import annotations

from datetime import datetime

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class AlertDeliveryError(RuntimeError):
    pass


class RetryableAlertError(AlertDeliveryError):
    pass


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.client = httpx.Client(timeout=15)

    def close(self) -> None:
        self.client.close()

    @retry(
        retry=retry_if_exception_type(RetryableAlertError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def send_message(self, text: str) -> None:
        response = self.client.post(self.url, json={"chat_id": self.chat_id, "text": text})
        if response.status_code == 429:
            raise RetryableAlertError("Telegram rate limited")
        if response.status_code >= 500:
            raise RetryableAlertError(f"Telegram server error {response.status_code}")
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise AlertDeliveryError(str(payload))


def format_alert(title: str, lines: list[str]) -> str:
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    body = "\n".join(lines)
    return f"{title}\n{timestamp}\n{body}"
