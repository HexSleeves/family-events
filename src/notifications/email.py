import logging

from src.config import settings
from src.http import build_async_client, default_timeout
from src.observability import log_event

logger = logging.getLogger("uvicorn.error")


class EmailNotifier:
    async def send(self, message: str, *, to_email: str = "") -> bool:
        service = "notify.email.resend"
        if not settings.resend_api_key:
            log_event(
                logger,
                logging.INFO,
                "notification_delivery_skipped",
                service=service,
                channel="email",
                recipient=to_email or "-",
                reason="missing_api_key",
            )
            return False
        if not to_email:
            log_event(
                logger,
                logging.INFO,
                "notification_delivery_skipped",
                service=service,
                channel="email",
                recipient="-",
                reason="missing_recipient",
            )
            return False

        url = "https://api.resend.com/emails"
        try:
            html = message.replace("\n", "<br>")

            async with build_async_client(
                service=service,
                timeout=default_timeout(),
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {settings.resend_api_key}",
                },
                max_retries=0,
            ) as client:
                resp = await client.post(
                    url,
                    json={
                        "from": settings.email_from,
                        "to": [to_email],
                        "subject": "🌟 Weekend Plans!",
                        "html": html,
                    },
                )
                resp.raise_for_status()
                log_event(
                    logger,
                    logging.INFO,
                    "notification_delivery_succeeded",
                    service=service,
                    channel="email",
                    recipient=to_email,
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
                channel="email",
                recipient=to_email,
                url=url,
                timeout_seconds=settings.external_http_timeout_seconds,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return False
