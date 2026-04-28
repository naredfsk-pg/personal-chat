from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

from src.bot.middlewares.auth import AuthMiddleware


@pytest.fixture
def middleware() -> AuthMiddleware:
    return AuthMiddleware(allowed_user_ids=frozenset({12345}))


def _make_user(user_id: int) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    return user


def _make_handler() -> AsyncMock:
    return AsyncMock(return_value="handler_result")


def _make_data(user_id: int | None) -> dict[str, Any]:
    if user_id is None:
        return {}
    return {"event_from_user": _make_user(user_id)}


async def test_allowed_user_passes_through(middleware: AuthMiddleware) -> None:
    handler = _make_handler()
    event = MagicMock()
    data = _make_data(12345)

    result = await middleware(handler, event, data)

    handler.assert_called_once_with(event, data)
    assert result == "handler_result"


async def test_blocked_user_is_silently_ignored(middleware: AuthMiddleware) -> None:
    handler = _make_handler()
    event = MagicMock()
    data = _make_data(99999)

    result = await middleware(handler, event, data)

    handler.assert_not_called()
    assert result is None


async def test_update_without_user_is_blocked(middleware: AuthMiddleware) -> None:
    handler = _make_handler()
    event = MagicMock()
    data = _make_data(None)  # no event_from_user key

    result = await middleware(handler, event, data)

    handler.assert_not_called()
    assert result is None


async def test_blocked_user_is_logged(middleware: AuthMiddleware) -> None:
    handler = _make_handler()
    event = MagicMock()
    data = _make_data(99999)

    with structlog.testing.capture_logs() as captured:
        await middleware(handler, event, data)

    assert len(captured) == 1
    assert captured[0]["log_level"] == "warning"
    assert captured[0]["user_id"] == 99999
    assert "update_type" in captured[0]


async def test_no_user_blocked_is_logged_with_none_user_id(middleware: AuthMiddleware) -> None:
    handler = _make_handler()
    event = MagicMock()
    data = _make_data(None)

    with structlog.testing.capture_logs() as captured:
        await middleware(handler, event, data)

    assert len(captured) == 1
    assert captured[0]["log_level"] == "warning"
    assert captured[0]["user_id"] is None


async def test_log_does_not_contain_message_content(middleware: AuthMiddleware) -> None:
    handler = _make_handler()
    event = MagicMock()
    user = _make_user(99999)
    user.message = "secret content"
    data = {"event_from_user": user}

    with structlog.testing.capture_logs() as captured:
        await middleware(handler, event, data)

    log_entry = captured[0]
    assert "message" not in log_entry
    assert "secret content" not in str(log_entry)


async def test_user_id_injected_into_context(middleware: AuthMiddleware) -> None:
    handler = _make_handler()
    event = MagicMock()
    data = _make_data(12345)

    await middleware(handler, event, data)

    assert data["user_id"] == 12345


async def test_empty_whitelist_blocks_everyone() -> None:
    empty_middleware = AuthMiddleware(allowed_user_ids=frozenset())
    handler = _make_handler()
    event = MagicMock()
    data = _make_data(12345)

    result = await empty_middleware(handler, event, data)

    handler.assert_not_called()
    assert result is None
