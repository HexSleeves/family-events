import logging

from src.config import settings
from src.http import build_async_client, default_timeout

logger = logging.getLogger("uvicorn.error")


class TelegramNotifier:
    async def send(self, message: str) -> bool:
        if not all([settings.telegram_bot_token, settings.telegram_chat_id]):
            print("Telegram: Missing credentials, skipping")
            return False

        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        try:
            async with build_async_client(
                service="notify.telegram",
                timeout=default_timeout(),
                headers={"Accept": "application/json"},
                max_retries=0,
            ) as client:
                resp = await client.post(
                    url,
                    json={
                        "chat_id": settings.telegram_chat_id,
                        "text": message,
                        "parse_mode": "HTML",
                    },
                )
                resp.raise_for_status()
                print("Telegram message sent!")
                return True
        except Exception as exc:
            logger.warning(
                "notification_delivery_failed service=%s channel=telegram recipient=%s url=%s timeout=%ss error=%s",
                "notify.telegram",
                "telegram",
                url,
                settings.external_http_timeout_seconds,
                exc,
            )
            return False
