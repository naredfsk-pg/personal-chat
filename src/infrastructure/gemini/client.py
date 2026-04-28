from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator

import google.generativeai as genai
import structlog
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

try:
    from google.api_core.exceptions import InvalidArgument, ResourceExhausted
except ImportError:
    # google-api-core is a transitive dep; define fallbacks so the module loads regardless
    ResourceExhausted = type("ResourceExhausted", (Exception,), {})  # type: ignore[misc,assignment]
    InvalidArgument = type("InvalidArgument", (Exception,), {})  # type: ignore[misc,assignment]

log = structlog.get_logger()


@dataclass(frozen=True)
class Message:
    role: str  # "user" | "model"
    content: str
    image_bytes: bytes | None = None


def _is_retryable(exc: BaseException) -> bool:
    """Quota and bad-request errors should not be retried — they won't recover."""
    return not isinstance(exc, (ResourceExhausted, InvalidArgument))


class GeminiClient:
    def __init__(self, api_key: str, model_name: str = "gemini-1.5-flash") -> None:
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model_name)

    def _to_gemini_history(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert all messages except the last to Gemini's history format."""
        history: list[dict[str, Any]] = []
        for msg in messages[:-1]:
            parts: list[Any] = [msg.content]
            if msg.image_bytes:
                parts.append({"mime_type": "image/jpeg", "data": msg.image_bytes})
            history.append({"role": msg.role, "parts": parts})
        return history

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=32),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def _get_chat_response(
        self,
        messages: list[Message],
        system_prompt: str | None,
    ) -> Any:
        """Fetch the streaming response object from Gemini. Separated from the
        generator so tenacity can retry this coroutine directly."""
        chat = self._model.start_chat(history=self._to_gemini_history(messages))
        last_msg = messages[-1]
        parts: list[Any] = [last_msg.content]
        if last_msg.image_bytes:
            parts.append({"mime_type": "image/jpeg", "data": last_msg.image_bytes})
        if system_prompt:
            parts.insert(0, system_prompt)
        return await chat.send_message_async(parts, stream=True)

    async def stream_chat(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield text chunks as they arrive from Gemini."""
        try:
            response = await self._get_chat_response(messages, system_prompt)
            async for chunk in response:
                if chunk.text:
                    yield chunk.text
        except ResourceExhausted:
            yield "⚠️ Gemini quota หมดแล้ว ลองใหม่พรุ่งนี้"
        except InvalidArgument as e:
            yield f"⚠️ ข้อความไม่ถูกต้อง: {e}"
        except Exception:
            log.exception("gemini_stream_failed")
            yield "⚠️ เกิดข้อผิดพลาด กรุณาลองใหม่อีกครั้ง"

    async def embed_text(self, text: str) -> list[float]:
        """Return an embedding vector for text (used by ChromaDB in CARD-06)."""
        result = await genai.embed_content_async(
            model="models/text-embedding-004",
            content=text,
            task_type="retrieval_document",
        )
        return result["embedding"]
