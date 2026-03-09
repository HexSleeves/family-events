from __future__ import annotations

from datetime import UTC, datetime
from typing import TypedDict

from .console import ConsoleNotifier
from .email import EmailNotifier
from .sms import SMSNotifier
from .telegram import TelegramNotifier


class NotificationResult(TypedDict):
    channel: str
    success: bool
    recipient: str
    error: str
    sent_at: str


class NotificationDispatcher:
    def __init__(self) -> None:
        self.notifiers = {
            "console": ConsoleNotifier(),
            "sms": SMSNotifier(),
            "telegram": TelegramNotifier(),
            "email": EmailNotifier(),
        }

    def _result(
        self,
        *,
        channel: str,
        success: bool,
        recipient: str = "",
        error: str = "",
    ) -> NotificationResult:
        return {
            "channel": channel,
            "success": success,
            "recipient": recipient,
            "error": error,
            "sent_at": datetime.now(tz=UTC).isoformat(),
        }

    async def dispatch(
        self,
        message: str,
        *,
        channels: list[str] | None = None,
        email_to: str = "",
        sms_to: str = "",
    ) -> list[NotificationResult]:
        """Send message to the specified channels and return structured results."""
        if channels is None:
            channels = ["console"]

        results: list[NotificationResult] = []
        for channel in channels:
            notifier = self.notifiers.get(channel)
            if not notifier:
                results.append(
                    self._result(
                        channel=channel,
                        success=False,
                        error=f"Unknown channel: {channel}",
                    )
                )
                continue

            if channel == "email":
                recipient = email_to
                success = await notifier.send(message, to_email=email_to)
                error = "" if success else "Email delivery failed"
            elif channel == "sms":
                recipient = sms_to
                success = await notifier.send(message, to_number=sms_to)
                error = "" if success else "SMS delivery failed"
            elif channel == "telegram":
                recipient = "telegram"
                success = await notifier.send(message)
                error = "" if success else "Telegram delivery failed"
            else:
                recipient = "console"
                success = await notifier.send(message)
                error = "" if success else "Console delivery failed"

            results.append(
                self._result(
                    channel=channel,
                    success=success,
                    recipient=recipient,
                    error=error,
                )
            )
        return results
