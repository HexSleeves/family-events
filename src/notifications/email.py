import httpx

from src.config import settings


class EmailNotifier:
    async def send(self, message: str) -> bool:
        if not all([settings.resend_api_key, settings.email_to]):
            print("Email: Missing credentials, skipping")
            return False

        try:
            # Convert plain text to simple HTML
            html = message.replace("\n", "<br>")

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                    json={
                        "from": settings.email_from,
                        "to": [settings.email_to],
                        "subject": "🌟 Weekend Plans!",
                        "html": html,
                    },
                )
                resp.raise_for_status()
                print("Email sent!")
                return True
        except Exception as e:
            print(f"Email error: {e}")
            return False
