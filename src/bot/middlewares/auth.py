from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

log = structlog.get_logger()


class AuthMiddleware(BaseMiddleware):
    def __init__(self, allowed_user_ids: frozenset[int]) -> None:
        self._allowed = allowed_user_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None or user.id not in self._allowed:
            log.warning(
                "blocked_update",
                user_id=getattr(user, "id", None),
                update_type=type(event).__name__,
            )
            return None
        data["user_id"] = user.id
        return await handler(event, data)
