import httpx

from src.config import settings


class TelegramNotifier:
    async def send(self, message: str) -> bool:
        if not all([settings.telegram_bot_token, settings.telegram_chat_id]):
            print("Telegram: Missing credentials, skipping")
            return False

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": settings.telegram_chat_id,
                        "text": message,
                        "parse_mode": "HTML",
                    },
                )
                resp.raise_for_status()
                print("Telegram message sent!")
                return True
        except Exception as e:
            print(f"Telegram error: {e}")
            return False
