from __future__ import annotations

from .console import ConsoleNotifier
from .email import EmailNotifier
from .sms import SMSNotifier
from .telegram import TelegramNotifier


class NotificationDispatcher:
    def __init__(self) -> None:
        self.notifiers = {
            "console": ConsoleNotifier(),
            "sms": SMSNotifier(),
            "telegram": TelegramNotifier(),
            "email": EmailNotifier(),
        }

    async def dispatch(
        self,
        message: str,
        *,
        channels: list[str] | None = None,
        email_to: str = "",
    ) -> dict[str, bool]:
        """Send message to the specified channels.

        Args:
            message: The notification text.
            channels: Which channels to use. Defaults to ["console"].
            email_to: Recipient email for the email channel.
        """
        if channels is None:
            channels = ["console"]

        results: dict[str, bool] = {}
        for channel in channels:
            notifier = self.notifiers.get(channel)
            if not notifier:
                print(f"Unknown channel: {channel}")
                results[channel] = False
                continue

            if channel == "email" and email_to:
                from .email import EmailNotifier

                assert isinstance(notifier, EmailNotifier)
                results[channel] = await notifier.send(message, to_email=email_to)
            else:
                results[channel] = await notifier.send(message)
        return results
