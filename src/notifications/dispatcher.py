from src.config import settings

from .console import ConsoleNotifier
from .email import EmailNotifier
from .sms import SMSNotifier
from .telegram import TelegramNotifier


class NotificationDispatcher:
    def __init__(self):
        self.notifiers = {
            "console": ConsoleNotifier(),
            "sms": SMSNotifier(),
            "telegram": TelegramNotifier(),
            "email": EmailNotifier(),
        }

    async def dispatch(self, message: str) -> dict[str, bool]:
        results = {}
        for channel in settings.notification_channels:
            notifier = self.notifiers.get(channel)
            if notifier:
                results[channel] = await notifier.send(message)
            else:
                print(f"Unknown channel: {channel}")
                results[channel] = False
        return results
