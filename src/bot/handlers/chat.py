import asyncio

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Message

from src.infrastructure.gemini.client import GeminiClient
from src.infrastructure.gemini.client import Message as GeminiMessage

router = Router()

_CHUNK_EDIT_THRESHOLD = 20  # edit the in-progress message every N new characters
_MAX_TG_LEN = 4096          # Telegram's hard message-length limit


def split_message(text: str, max_len: int = _MAX_TG_LEN) -> list[str]:
    """Split text into chunks ≤ max_len, preferring newline boundaries."""
    if len(text) <= max_len:
        return [text]

    parts: list[str] = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return parts


async def _safe_edit(msg: Message, text: str) -> None:
    """Edit a message, handling Telegram rate limits and stale messages."""
    try:
        await msg.edit_text(text)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        try:
            await msg.edit_text(text)
        except TelegramBadRequest:
            pass
    except TelegramBadRequest:
        pass  # message deleted or content unchanged


@router.message()
async def chat_handler(message: Message, gemini: GeminiClient, user_id: int) -> None:
    # Non-text updates (sticker, photo without caption, etc.) → ignore silently
    if not message.text:
        return

    # Commands are handled by dedicated command handlers
    if message.text.startswith("/"):
        return

    # Pure whitespace — tell user to send something
    if not message.text.strip():
        await message.reply("กรุณาส่งข้อความ")
        return

    reply = await message.reply("...")
    accumulated = ""
    last_edit_len = 0

    async for chunk in gemini.stream_chat(
        [GeminiMessage(role="user", content=message.text)]
    ):
        accumulated += chunk
        new_chars = len(accumulated) - last_edit_len
        # Edit progressively, but stop mid-stream edits when approaching the limit
        if new_chars >= _CHUNK_EDIT_THRESHOLD and len(accumulated) <= _MAX_TG_LEN:
            await _safe_edit(reply, accumulated)
            last_edit_len = len(accumulated)

    if not accumulated:
        await _safe_edit(reply, "ไม่มีคำตอบ กรุณาลองใหม่")
        return

    if len(accumulated) <= _MAX_TG_LEN:
        # Final edit to flush any remaining characters below the edit threshold
        if last_edit_len != len(accumulated):
            await _safe_edit(reply, accumulated)
    else:
        # Response exceeds limit — edit first part, send the rest as new messages
        parts = split_message(accumulated)
        await _safe_edit(reply, parts[0])
        for part in parts[1:]:
            await message.answer(part)
