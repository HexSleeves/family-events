from __future__ import annotations

import asyncio

from src.notifications.dispatcher import NotificationDispatcher


class FakeNotifier:
    def __init__(self, success: bool) -> None:
        self.success = success

    async def send(self, message: str, **kwargs) -> bool:
        assert message
        return self.success


def test_notification_dispatcher_returns_structured_results():
    async def scenario() -> None:
        dispatcher = NotificationDispatcher()
        dispatcher.notifiers = {
            "console": FakeNotifier(True),
            "email": FakeNotifier(False),
        }

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


def test_notification_notifiers_use_shared_http_client(monkeypatch):
    import asyncio

    from src.notifications.email import EmailNotifier
    from src.notifications.sms import SMSNotifier
    from src.notifications.telegram import TelegramNotifier

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
