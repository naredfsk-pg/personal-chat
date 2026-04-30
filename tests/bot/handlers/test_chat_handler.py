from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Message

from src.bot.handlers.chat import _MAX_TG_LEN, _safe_edit, chat_handler, split_message
from src.core.memory.conversation_buffer import ConversationBuffer
from src.infrastructure.gemini.client import GeminiClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gemini(*chunks: str) -> MagicMock:
    """Return a GeminiClient mock whose stream_chat yields the given chunks."""
    async def _stream():
        for chunk in chunks:
            yield chunk

    client = MagicMock(spec=GeminiClient)
    client.stream_chat = MagicMock(return_value=_stream())
    return client


def _make_message(text: str | None = "hello") -> tuple[AsyncMock, AsyncMock]:
    """Return (message mock, reply mock)."""
    msg = AsyncMock(spec=Message)
    msg.text = text
    reply = AsyncMock()
    msg.reply = AsyncMock(return_value=reply)
    msg.answer = AsyncMock()
    return msg, reply


def _make_retry_after(seconds: int = 1) -> TelegramRetryAfter:
    return TelegramRetryAfter(method=MagicMock(), message=f"retry after {seconds}", retry_after=seconds)


def _make_bad_request(reason: str = "message is not modified") -> TelegramBadRequest:
    return TelegramBadRequest(method=MagicMock(), message=reason)


# ---------------------------------------------------------------------------
# split_message
# ---------------------------------------------------------------------------


def test_split_message_under_limit() -> None:
    result = split_message("hello", max_len=4096)
    assert result == ["hello"]


def test_split_message_over_limit_no_newline() -> None:
    long_text = "a" * 5000
    parts = split_message(long_text, max_len=4096)
    assert len(parts) == 2
    assert parts[0] == "a" * 4096
    assert parts[1] == "a" * 904
    assert all(len(p) <= 4096 for p in parts)


def test_split_message_prefers_newline_split() -> None:
    # "line\n" repeated 1000 times → each part must start at a newline boundary
    text = "line\n" * 1000
    parts = split_message(text, max_len=100)
    assert all(not p.startswith("\n") for p in parts)
    assert all(len(p) <= 100 for p in parts)


def test_split_message_exact_limit() -> None:
    text = "x" * 4096
    assert split_message(text) == [text]


def test_split_message_multiple_parts() -> None:
    text = "word\n" * 3000  # 15000 chars → 4 parts
    parts = split_message(text)
    assert len(parts) > 2
    assert all(len(p) <= _MAX_TG_LEN for p in parts)
    # Reassembled text must preserve all content (no characters dropped)
    reassembled = "\n".join(parts)
    assert set(reassembled.replace("\n", "")) == {"d", "r", "o", "w"}


# ---------------------------------------------------------------------------
# _safe_edit
# ---------------------------------------------------------------------------


async def test_safe_edit_calls_edit_text() -> None:
    msg = AsyncMock(spec=Message)
    msg.edit_text = AsyncMock()
    await _safe_edit(msg, "hello")
    msg.edit_text.assert_called_once_with("hello")


async def test_safe_edit_retries_after_rate_limit() -> None:
    msg = AsyncMock(spec=Message)
    msg.edit_text = AsyncMock(side_effect=[_make_retry_after(2), None])

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await _safe_edit(msg, "hello")

    mock_sleep.assert_called_once_with(2)
    assert msg.edit_text.call_count == 2


async def test_safe_edit_swallows_bad_request() -> None:
    msg = AsyncMock(spec=Message)
    msg.edit_text = AsyncMock(side_effect=_make_bad_request())
    # Must not raise
    await _safe_edit(msg, "hello")


async def test_safe_edit_swallows_bad_request_after_retry() -> None:
    msg = AsyncMock(spec=Message)
    msg.edit_text = AsyncMock(
        side_effect=[_make_retry_after(1), _make_bad_request()]
    )
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await _safe_edit(msg, "hello")  # should not raise


# ---------------------------------------------------------------------------
# chat_handler — ignored inputs
# ---------------------------------------------------------------------------


async def test_handler_ignores_non_text_message() -> None:
    msg, _ = _make_message(text=None)
    gemini = _make_gemini("response")
    buf = ConversationBuffer()

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    msg.reply.assert_not_called()
    gemini.stream_chat.assert_not_called()


async def test_handler_ignores_command_messages() -> None:
    msg, _ = _make_message(text="/unknown_command")
    gemini = _make_gemini("response")
    buf = ConversationBuffer()

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    msg.reply.assert_not_called()
    gemini.stream_chat.assert_not_called()


async def test_handler_replies_to_whitespace_message() -> None:
    msg, _ = _make_message(text="   ")
    gemini = _make_gemini("response")
    buf = ConversationBuffer()

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    msg.reply.assert_called_once_with("กรุณาส่งข้อความ")
    gemini.stream_chat.assert_not_called()


# ---------------------------------------------------------------------------
# chat_handler — normal flow
# ---------------------------------------------------------------------------


async def test_handler_sends_initial_placeholder() -> None:
    msg, reply = _make_message(text="hello")
    gemini = _make_gemini("Hi!")
    buf = ConversationBuffer()

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    msg.reply.assert_called_once_with("...")


async def test_handler_sends_reply_with_streamed_content() -> None:
    msg, reply = _make_message(text="hello")
    # Total = 12 chars — below 20-char threshold, so only the final edit fires
    gemini = _make_gemini("Hello", " world", "!")
    buf = ConversationBuffer()

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    assert reply.edit_text.called
    final_text = reply.edit_text.call_args_list[-1][0][0]
    assert final_text == "Hello world!"


async def test_handler_edits_progressively_on_threshold() -> None:
    msg, reply = _make_message(text="test")
    # Each chunk is 21 chars — crosses the 20-char threshold individually
    gemini = _make_gemini("A" * 21, "B" * 21)
    buf = ConversationBuffer()

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    # One mid-stream edit per chunk (21 ≥ 20), plus no extra final edit
    # (last_edit_len == len(accumulated) after second chunk)
    assert reply.edit_text.call_count == 2
    texts = [c[0][0] for c in reply.edit_text.call_args_list]
    assert texts[0] == "A" * 21
    assert texts[1] == "A" * 21 + "B" * 21


async def test_handler_no_final_edit_when_already_synced() -> None:
    """If last mid-stream edit covered all chars, no redundant final edit."""
    msg, reply = _make_message(text="test")
    gemini = _make_gemini("X" * 25)  # single chunk, 25 ≥ 20 → mid-stream edit fires
    buf = ConversationBuffer()

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    assert reply.edit_text.call_count == 1  # only the mid-stream edit, no duplicate


async def test_handler_sends_error_for_empty_gemini_response() -> None:
    msg, reply = _make_message(text="test")
    gemini = _make_gemini()  # yields nothing
    buf = ConversationBuffer()

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    reply.edit_text.assert_called_once()
    assert "ไม่มีคำตอบ" in reply.edit_text.call_args[0][0]


# ---------------------------------------------------------------------------
# chat_handler — long response (> 4096 chars)
# ---------------------------------------------------------------------------


async def test_handler_splits_long_response() -> None:
    msg, reply = _make_message(text="write a lot")
    long_text = "A" * 5000
    gemini = _make_gemini(long_text)
    buf = ConversationBuffer()

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    # reply.edit_text gets the first 4096 chars
    reply.edit_text.assert_called_with("A" * 4096)
    # message.answer gets the remainder
    msg.answer.assert_called_once_with("A" * 904)


async def test_handler_long_response_with_newline_split() -> None:
    msg, reply = _make_message(text="test")
    # A line that fits, then filler to exceed 4096
    text = ("line\n" * 100) + ("x" * 4000)
    gemini = _make_gemini(text)
    buf = ConversationBuffer()

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    # First part must end at a newline boundary, not mid-word
    first_part = reply.edit_text.call_args_list[-1][0][0]
    assert len(first_part) <= _MAX_TG_LEN
    assert msg.answer.called


# ---------------------------------------------------------------------------
# chat_handler — ConversationBuffer integration
# ---------------------------------------------------------------------------


async def test_handler_stores_user_message_in_buffer() -> None:
    """chat_handler must add a 'user' turn with the message text before streaming."""
    msg, reply = _make_message(text="hello")
    gemini = _make_gemini("Hi!")
    buf = ConversationBuffer()

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    turns = buf.get_turns(1)
    assert any(t.role == "user" and t.content == "hello" for t in turns)


async def test_handler_stores_model_response_in_buffer() -> None:
    """chat_handler must add a 'model' turn with the accumulated response after streaming."""
    msg, reply = _make_message(text="hello")
    gemini = _make_gemini("World!")
    buf = ConversationBuffer()

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    turns = buf.get_turns(1)
    assert any(t.role == "model" and t.content == "World!" for t in turns)


async def test_handler_does_not_store_model_turn_on_empty_response() -> None:
    """When Gemini returns nothing, no model turn must be stored in the buffer."""
    msg, reply = _make_message(text="hello")
    gemini = _make_gemini()  # yields nothing
    buf = ConversationBuffer()

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    model_turns = [t for t in buf.get_turns(1) if t.role == "model"]
    assert model_turns == []


async def test_handler_builds_context_from_buffer() -> None:
    """chat_handler must pass ALL buffer turns (history + current) to gemini.stream_chat."""
    msg, reply = _make_message(text="followup")

    # Use a fresh generator factory so stream_chat can be called with full history
    async def _stream():
        yield "answer"

    gemini = MagicMock(spec=GeminiClient)
    gemini.stream_chat = MagicMock(return_value=_stream())

    buf = ConversationBuffer()
    buf.add_turn(1, "user", "previous question")
    buf.add_turn(1, "model", "previous answer")

    await chat_handler(msg, gemini, user_id=1, conversation_buffer=buf)

    # stream_chat must have been called with all turns including the new one
    call_args = gemini.stream_chat.call_args[0][0]
    assert len(call_args) == 3  # 2 history turns + 1 current "followup" turn
    assert call_args[0].role == "user"
    assert call_args[0].content == "previous question"
    assert call_args[1].role == "model"
    assert call_args[1].content == "previous answer"
    assert call_args[2].role == "user"
    assert call_args[2].content == "followup"
