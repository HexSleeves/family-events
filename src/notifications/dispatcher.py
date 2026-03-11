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
        self.console = ConsoleNotifier()
        self.sms = SMSNotifier()
        self.telegram = TelegramNotifier()
        self.email = EmailNotifier()

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
            if channel == "email":
                recipient = email_to
                success = await self.email.send(message, to_email=email_to)
                error = "" if success else "Email delivery failed"
            elif channel == "sms":
                recipient = sms_to
                success = await self.sms.send(message, to_number=sms_to)
                error = "" if success else "SMS delivery failed"
            elif channel == "telegram":
                recipient = "telegram"
                success = await self.telegram.send(message)
                error = "" if success else "Telegram delivery failed"
            elif channel == "console":
                recipient = "console"
                success = await self.console.send(message)
                error = "" if success else "Console delivery failed"
            else:
                results.append(
                    self._result(
                        channel=channel,
                        success=False,
                        error=f"Unknown channel: {channel}",
                    )
                )
                continue

            results.append(
                self._result(
                    channel=channel,
                    success=success,
                    recipient=recipient,
                    error=error,
                )
            )
        return results
