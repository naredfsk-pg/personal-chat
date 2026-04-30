"""Tests for the /clear command handler (CARD-05)."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from src.bot.handlers.base import clear_handler
from src.core.memory.conversation_buffer import ConversationBuffer


def _make_message() -> MagicMock:
    """Return a Message mock with reply stubbed as AsyncMock."""
    msg = MagicMock(spec=Message)
    msg.reply = AsyncMock()
    return msg


async def test_clear_handler_clears_buffer() -> None:
    """clear_handler removes the user's turns from the buffer."""
    buf = ConversationBuffer()
    buf.add_turn(1, "user", "hello")

    msg = _make_message()
    await clear_handler(msg, user_id=1, conversation_buffer=buf)

    assert buf.get_turns(1) == []
    msg.reply.assert_called_once_with("ล้างประวัติสนทนาแล้ว")


async def test_clear_handler_on_empty_buffer_does_not_raise() -> None:
    """clear_handler on a user with no history must not raise."""
    buf = ConversationBuffer()
    msg = _make_message()

    # Must not raise even though user 999 has no buffer entry
    await clear_handler(msg, user_id=999, conversation_buffer=buf)

    msg.reply.assert_called_once()


async def test_clear_handler_does_not_affect_other_users() -> None:
    """Clearing user 1's buffer must not affect user 2's buffer."""
    buf = ConversationBuffer()
    buf.add_turn(1, "user", "hello")
    buf.add_turn(2, "user", "world")

    msg = _make_message()
    await clear_handler(msg, user_id=1, conversation_buffer=buf)

    assert buf.get_turns(1) == []
    user2_turns = buf.get_turns(2)
    assert len(user2_turns) == 1
    assert user2_turns[0].content == "world"


async def test_clear_handler_replies_with_thai_confirmation() -> None:
    """clear_handler must reply with the exact Thai confirmation string."""
    buf = ConversationBuffer()
    msg = _make_message()
    await clear_handler(msg, user_id=1, conversation_buffer=buf)
    msg.reply.assert_called_once_with("ล้างประวัติสนทนาแล้ว")


async def test_clear_handler_reply_called_exactly_once() -> None:
    """clear_handler must reply exactly once, even if buffer is empty."""
    buf = ConversationBuffer()
    msg = _make_message()
    await clear_handler(msg, user_id=42, conversation_buffer=buf)
    assert msg.reply.call_count == 1
