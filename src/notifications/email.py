import logging

from src.config import settings
from src.http import build_async_client, default_timeout

logger = logging.getLogger("uvicorn.error")


class EmailNotifier:
    async def send(self, message: str, *, to_email: str = "") -> bool:
        if not settings.resend_api_key:
            print("Email: Missing RESEND_API_KEY, skipping")
            return False
        if not to_email:
            print("Email: No recipient email, skipping")
            return False

        url = "https://api.resend.com/emails"
        try:
            html = message.replace("\n", "<br>")

            async with build_async_client(
                service="notify.email.resend",
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
                print(f"Email sent to {to_email}!")
                return True
        except Exception as exc:
            logger.warning(
                "notification_delivery_failed service=%s channel=email recipient=%s url=%s timeout=%ss error=%s",
                "notify.email.resend",
                to_email,
                url,
                settings.external_http_timeout_seconds,
                exc,
            )
            return False
