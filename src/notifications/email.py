import httpx

from src.config import settings


class EmailNotifier:
    async def send(self, message: str, *, to_email: str = "") -> bool:
        if not settings.resend_api_key:
            print("Email: Missing RESEND_API_KEY, skipping")
            return False
        if not to_email:
            print("Email: No recipient email, skipping")
            return False

        try:
            html = message.replace("\n", "<br>")

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                    json={
                        "from": settings.email_from,
                        "to": [to_email],
                        "subject": "\U0001f31f Weekend Plans!",
                        "html": html,
                    },
                )
                resp.raise_for_status()
                print(f"Email sent to {to_email}!")
                return True
        except Exception as e:
            print(f"Email error: {e}")
            return False
