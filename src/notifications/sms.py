import logging

from src.config import settings
from src.http import build_async_client, default_timeout
from src.observability import log_event

logger = logging.getLogger("uvicorn.error")


class SMSNotifier:
    async def send(self, message: str, *, to_number: str = "") -> bool:
        service = "notify.sms.twilio"
        if not all(
            [
                settings.twilio_account_sid,
                settings.twilio_auth_token,
                settings.twilio_from_number,
            ]
        ):
            log_event(
                logger,
                logging.INFO,
                "notification_delivery_skipped",
                service=service,
                channel="sms",
                recipient=to_number or "-",
                reason="missing_credentials",
            )
            return False
        if not to_number:
            log_event(
                logger,
                logging.INFO,
                "notification_delivery_skipped",
                service=service,
                channel="sms",
                recipient="-",
                reason="missing_recipient",
            )
            return False

        url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
        try:
            async with build_async_client(
                service=service,
                timeout=default_timeout(),
                headers={"Accept": "application/json"},
                max_retries=0,
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            ) as client:
                resp = await client.post(
                    url,
                    data={
                        "From": settings.twilio_from_number,
                        "To": to_number,
                        "Body": message[:1600],
                    },
                )
                resp.raise_for_status()
                log_event(
                    logger,
                    logging.INFO,
                    "notification_delivery_succeeded",
                    service=service,
                    channel="sms",
                    recipient=to_number,
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
                channel="sms",
                recipient=to_number,
                url=url,
                timeout_seconds=settings.external_http_timeout_seconds,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return False
