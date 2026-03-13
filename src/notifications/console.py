import logging

from src.observability import log_event

logger = logging.getLogger("uvicorn.error")


class ConsoleNotifier:
    async def send(self, message: str) -> bool:
        log_event(
            logger,
            logging.INFO,
            "notification_delivery_succeeded",
            channel="console",
            recipient="console",
            message_length=len(message),
            notification_body=message,
        )
        return True
