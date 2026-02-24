import httpx

from src.config import settings


class SMSNotifier:
    async def send(self, message: str) -> bool:
        if not all(
            [
                settings.twilio_account_sid,
                settings.twilio_auth_token,
                settings.twilio_from_number,
                settings.twilio_to_number,
            ]
        ):
            print("SMS: Missing Twilio credentials, skipping")
            return False

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
                    auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                    data={
                        "From": settings.twilio_from_number,
                        "To": settings.twilio_to_number,
                        "Body": message[:1600],
                    },
                )
                resp.raise_for_status()
                print("SMS sent successfully!")
                return True
        except Exception as e:
            print(f"SMS error: {e}")
            return False
