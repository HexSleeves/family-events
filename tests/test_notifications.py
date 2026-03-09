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
