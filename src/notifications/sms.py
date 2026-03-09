import logging

from src.config import settings
from src.http import build_async_client, default_timeout

logger = logging.getLogger("uvicorn.error")


class SMSNotifier:
    async def send(self, message: str, *, to_number: str = "") -> bool:
        if not all(
            [
                settings.twilio_account_sid,
                settings.twilio_auth_token,
                settings.twilio_from_number,
            ]
        ):
            print("SMS: Missing Twilio credentials, skipping")
            return False
        if not to_number:
            print("SMS: No recipient phone number, skipping")
            return False

        url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
        try:
            async with build_async_client(
                service="notify.sms.twilio",
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
                print("SMS sent successfully!")
                return True
        except Exception as exc:
            logger.warning(
                "notification_delivery_failed service=%s channel=sms recipient=%s url=%s timeout=%ss error=%s",
                "notify.sms.twilio",
                to_number,
                url,
                settings.external_http_timeout_seconds,
                exc,
            )
            return False
