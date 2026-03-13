from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TypedDict

from src.observability import log_event

from .console import ConsoleNotifier
from .email import EmailNotifier
from .sms import SMSNotifier
from .telegram import TelegramNotifier

logger = logging.getLogger("uvicorn.error")


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

        log_event(
            logger,
            logging.INFO,
            "notification_dispatch_started",
            channel_count=len(channels),
            channels=channels,
            email_recipient=email_to or "-",
            sms_recipient=sms_to or "-",
        )

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
                log_event(
                    logger,
                    logging.WARNING,
                    "notification_dispatch_unknown_channel",
                    channel=channel,
                )
                results.append(
                    self._result(
                        channel=channel,
                        success=False,
                        error=f"Unknown channel: {channel}",
                    )
                )
                continue

            result = self._result(
                channel=channel,
                success=success,
                recipient=recipient,
                error=error,
            )
            results.append(result)
            log_event(
                logger,
                logging.INFO,
                "notification_dispatch_result",
                channel=channel,
                success=success,
                recipient=recipient or "-",
                error=error or "-",
            )

        success_count = sum(1 for item in results if item["success"])
        log_event(
            logger,
            logging.INFO,
            "notification_dispatch_completed",
            channel_count=len(results),
            success_count=success_count,
            failure_count=len(results) - success_count,
        )
        return results
