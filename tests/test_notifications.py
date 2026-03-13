from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager

from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.email import EmailNotifier
from src.notifications.sms import SMSNotifier
from src.notifications.telegram import TelegramNotifier
from src.observability import PrettyFormatter


class FakeNotifier:
    def __init__(self, success: bool) -> None:
        self.success = success

    async def send(self, message: str, **kwargs) -> bool:
        assert message
        return self.success


@contextmanager
def capture_uvicorn_logs(level: int = logging.INFO):
    logger = logging.getLogger("uvicorn.error")
    messages: list[str] = []

    class _Handler(logging.Handler):
        def __init__(self) -> None:
            super().__init__(level=level)
            self.setFormatter(PrettyFormatter())

        def emit(self, record: logging.LogRecord) -> None:
            messages.append(self.format(record))

    handler = _Handler()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(min(previous_level, level) if previous_level else level)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def test_notification_dispatcher_returns_structured_results():
    async def scenario() -> None:
        dispatcher = NotificationDispatcher()
        dispatcher.console = FakeNotifier(True)
        dispatcher.email = FakeNotifier(False)

        results = await dispatcher.dispatch(
            "hello",
            channels=["console", "email", "unknown"],
            email_to="parent@example.com",
        )

        assert len(results) == 3
        assert results[0]["channel"] == "console"
        assert results[0]["success"] is True
        assert results[1]["channel"] == "email"
        assert results[1]["success"] is False
        assert results[1]["recipient"] == "parent@example.com"
        assert results[1]["error"] == "Email delivery failed"
        assert results[2]["channel"] == "unknown"
        assert results[2]["success"] is False
        assert "Unknown channel" in results[2]["error"]

    asyncio.run(scenario())


def test_notification_dispatcher_logs_summary():
    async def scenario() -> None:
        dispatcher = NotificationDispatcher()
        dispatcher.console = FakeNotifier(True)
        dispatcher.email = FakeNotifier(False)

        with capture_uvicorn_logs() as messages:
            results = await dispatcher.dispatch(
                "hello",
                channels=["console", "email"],
                email_to="parent@example.com",
            )

        assert len(results) == 2
        assert any(
            "notification_dispatch_started" in message
            and "channel_count=2" in message
            and "channels=['console', 'email']" in message
            for message in messages
        )
        assert any(
            "notification_dispatch_completed" in message
            and "success_count=1" in message
            and "failure_count=1" in message
            for message in messages
        )

    asyncio.run(scenario())


def test_notification_notifiers_log_success_context(monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, **kwargs):
            calls.append({"url": url, **kwargs})
            return FakeResponse()

    def fake_build_async_client(**kwargs):
        return FakeClient()

    monkeypatch.setattr("src.notifications.email.build_async_client", fake_build_async_client)
    monkeypatch.setattr("src.notifications.sms.build_async_client", fake_build_async_client)
    monkeypatch.setattr("src.notifications.telegram.build_async_client", fake_build_async_client)
    monkeypatch.setattr("src.notifications.email.settings.resend_api_key", "resend-key")
    monkeypatch.setattr("src.notifications.email.settings.email_from", "Family <test@example.com>")
    monkeypatch.setattr("src.notifications.sms.settings.twilio_account_sid", "sid")
    monkeypatch.setattr("src.notifications.sms.settings.twilio_auth_token", "token")
    monkeypatch.setattr("src.notifications.sms.settings.twilio_from_number", "+15551234567")
    monkeypatch.setattr("src.notifications.telegram.settings.telegram_bot_token", "bot-token")
    monkeypatch.setattr("src.notifications.telegram.settings.telegram_chat_id", "chat-id")

    async def scenario() -> None:
        with capture_uvicorn_logs() as messages:
            assert await EmailNotifier().send("hello", to_email="parent@example.com") is True
            assert await SMSNotifier().send("hello", to_number="+15557654321") is True
            assert await TelegramNotifier().send("hello") is True

        assert any(
            "notification_delivery_succeeded" in message
            and "channel=email" in message
            and "recipient=parent@example.com" in message
            for message in messages
        )
        assert any(
            "notification_delivery_succeeded" in message
            and "channel=sms" in message
            and "recipient=+15557654321" in message
            for message in messages
        )
        assert any(
            "notification_delivery_succeeded" in message
            and "channel=telegram" in message
            and "recipient=telegram" in message
            for message in messages
        )

    asyncio.run(scenario())


def test_notification_notifiers_log_failure_context(monkeypatch):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, **kwargs):
            raise RuntimeError("boom")

    def fake_build_async_client(**kwargs):
        return FakeClient()

    monkeypatch.setattr("src.notifications.email.build_async_client", fake_build_async_client)
    monkeypatch.setattr("src.notifications.email.settings.resend_api_key", "resend-key")
    monkeypatch.setattr("src.notifications.email.settings.email_from", "Family <test@example.com>")

    async def scenario() -> None:
        with capture_uvicorn_logs() as messages:
            assert await EmailNotifier().send("hello", to_email="parent@example.com") is False

        assert any(
            "notification_delivery_failed" in message
            and "channel=email" in message
            and "recipient=parent@example.com" in message
            and "error_message=boom" in message
            for message in messages
        )

    asyncio.run(scenario())


def test_notification_notifiers_use_shared_http_client(monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, **kwargs):
            calls.append({"url": url, **kwargs})
            return FakeResponse()

    def fake_build_async_client(**kwargs):
        calls.append({"client_kwargs": kwargs})
        return FakeClient()

    monkeypatch.setattr("src.notifications.email.build_async_client", fake_build_async_client)
    monkeypatch.setattr("src.notifications.sms.build_async_client", fake_build_async_client)
    monkeypatch.setattr("src.notifications.telegram.build_async_client", fake_build_async_client)
    monkeypatch.setattr("src.notifications.email.settings.resend_api_key", "resend-key")
    monkeypatch.setattr("src.notifications.email.settings.email_from", "Family <test@example.com>")
    monkeypatch.setattr("src.notifications.sms.settings.twilio_account_sid", "sid")
    monkeypatch.setattr("src.notifications.sms.settings.twilio_auth_token", "token")
    monkeypatch.setattr("src.notifications.sms.settings.twilio_from_number", "+15551234567")
    monkeypatch.setattr("src.notifications.telegram.settings.telegram_bot_token", "bot-token")
    monkeypatch.setattr("src.notifications.telegram.settings.telegram_chat_id", "chat-id")

    async def scenario() -> None:
        assert await EmailNotifier().send("hello", to_email="parent@example.com") is True
        assert await SMSNotifier().send("hello", to_number="+15557654321") is True
        assert await TelegramNotifier().send("hello") is True

    asyncio.run(scenario())

    services = [entry["client_kwargs"]["service"] for entry in calls if "client_kwargs" in entry]
    assert services == ["notify.email.resend", "notify.sms.twilio", "notify.telegram"]
    assert any(entry.get("url") == "https://api.resend.com/emails" for entry in calls)
    assert any("api.twilio.com" in str(entry.get("url", "")) for entry in calls)
    assert any("api.telegram.org" in str(entry.get("url", "")) for entry in calls)
