import logging

from src.config import settings
from src.http import build_async_client, default_timeout
from src.observability import log_event

logger = logging.getLogger("uvicorn.error")


class TelegramNotifier:
    async def send(self, message: str) -> bool:
        service = "notify.telegram"
        if not all([settings.telegram_bot_token, settings.telegram_chat_id]):
            log_event(
                logger,
                logging.INFO,
                "notification_delivery_skipped",
                service=service,
                channel="telegram",
                recipient="telegram",
                reason="missing_credentials",
            )
            return False

        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        try:
            async with build_async_client(
                service=service,
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
                log_event(
                    logger,
                    logging.INFO,
                    "notification_delivery_succeeded",
                    service=service,
                    channel="telegram",
                    recipient="telegram",
                    url=url,
                    message_length=len(message),
                )
                return True
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "notification_delivery_failed",
                service=service,
                channel="telegram",
                recipient="telegram",
                url=url,
                timeout_seconds=settings.external_http_timeout_seconds,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return False
