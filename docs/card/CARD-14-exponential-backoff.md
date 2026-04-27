# CARD-14 | Exponential Backoff (tenacity)

**Phase:** 4 — Reliability  
**Priority:** P1  
**Estimated effort:** 0.5 day  
**Depends on:** CARD-03  
**Blocks:** —

---

## Goal

ทำให้ transient error (network blip, Gemini 5xx) ไม่ทำให้ bot crash โดย retry อัตโนมัติด้วย exponential backoff พร้อม jitter

---

## Tasks

### 1. ติดตั้ง tenacity

```
tenacity>=8.2
```

### 2. กำหนด retry policy

```python
# src/infrastructure/gemini/retry_policy.py
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception_type,
    before_sleep_log,
    RetryError,
)
import google.api_core.exceptions as google_exc
import structlog

log = structlog.get_logger()

# Error ที่ควร retry (transient)
RETRYABLE_EXCEPTIONS = (
    google_exc.ServiceUnavailable,    # 503
    google_exc.InternalServerError,   # 500
    google_exc.DeadlineExceeded,      # timeout
    ConnectionError,
    TimeoutError,
)

# Error ที่ไม่ควร retry (client error)
NON_RETRYABLE_EXCEPTIONS = (
    google_exc.InvalidArgument,       # 400 — request ผิด ไม่มีประโยชน์ retry
    google_exc.PermissionDenied,      # 403 — API key ผิด
    google_exc.NotFound,              # 404
)

gemini_retry = retry(
    retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
    wait=wait_exponential_jitter(initial=1, max=32, jitter=2),
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(log, "warning"),
    reraise=True,
)
```

### 3. Apply retry ใน `GeminiClient`

```python
# src/infrastructure/gemini/client.py
from src.infrastructure.gemini.retry_policy import gemini_retry, NON_RETRYABLE_EXCEPTIONS

class GeminiClient:

    @gemini_retry
    async def _stream_with_retry(self, messages, system_prompt):
        """Inner method ที่ถูก wrap ด้วย retry."""
        chat = self._model.start_chat(history=self._to_gemini_history(messages))
        response = await chat.send_message_async(
            self._build_parts(messages[-1]),
            stream=True,
            generation_config={"system_instruction": system_prompt} if system_prompt else None,
        )
        chunks = []
        async for chunk in response:
            if chunk.text:
                chunks.append(chunk.text)
        return chunks   # collect ทั้งหมดก่อน เพราะ retry ต้อง replay ทั้งหมด

    async def stream_chat(self, messages, system_prompt=None):
        try:
            chunks = await self._stream_with_retry(messages, system_prompt)
            for chunk in chunks:
                yield chunk
        except NON_RETRYABLE_EXCEPTIONS as e:
            log.error("gemini_client_error", error=str(e), error_type=type(e).__name__)
            yield f"⚠️ ข้อผิดพลาด: {_user_friendly_error(e)}"
        except Exception as e:
            log.error("gemini_unexpected_error", error=str(e))
            yield "⚠️ เกิดข้อผิดพลาดที่ไม่คาดคิด กรุณาลองใหม่"
```

### 4. Retry สำหรับ Telegram API calls

Telegram บางครั้ง 429 (Too Many Requests) หรือ 5xx:

```python
# src/infrastructure/telegram/retry_policy.py
from aiogram.exceptions import TelegramRetryAfter, TelegramServerError
import asyncio

telegram_retry = retry(
    retry=retry_if_exception_type((TelegramServerError,)),
    wait=wait_exponential_jitter(initial=1, max=16),
    stop=stop_after_attempt(3),
    reraise=True,
)

async def safe_edit_text(message, text: str) -> None:
    """Edit message พร้อมรองรับ rate limit."""
    try:
        await message.edit_text(text)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        await message.edit_text(text)
```

### 5. Log ทุก retry attempt

```python
# before_sleep callback สำหรับ structured logging
def log_retry_attempt(retry_state):
    log.warning(
        "gemini_retry",
        attempt=retry_state.attempt_number,
        wait_seconds=round(retry_state.next_action.sleep, 2),
        error=str(retry_state.outcome.exception()),
    )
```

### 6. Backoff timing table

| Attempt | Base wait | With jitter (±2s) |
|---------|-----------|-------------------|
| 1 | 1s | 0–3s |
| 2 | 2s | 1–5s |
| 3 | 4s | 3–7s |
| 4 | 8s | 7–11s |
| 5 (max) | fail | — |

---

## Unit Tests

```python
# tests/infrastructure/gemini/test_retry_policy.py

async def test_retry_on_service_unavailable(mock_gemini_api):
    # simulate: 2 ครั้งแรก raise ServiceUnavailable, ครั้งที่ 3 สำเร็จ
    mock_gemini_api.side_effect = [
        google_exc.ServiceUnavailable("503"),
        google_exc.ServiceUnavailable("503"),
        MockSuccessResponse(["hello"]),
    ]
    client = GeminiClient(api_key="test")
    chunks = [chunk async for chunk in client.stream_chat([...])]
    assert chunks == ["hello"]
    assert mock_gemini_api.call_count == 3

async def test_no_retry_on_invalid_argument(mock_gemini_api):
    mock_gemini_api.side_effect = google_exc.InvalidArgument("bad request")
    client = GeminiClient(api_key="test")
    chunks = [chunk async for chunk in client.stream_chat([...])]
    assert mock_gemini_api.call_count == 1  # ไม่ retry
    assert "ข้อผิดพลาด" in chunks[0]

async def test_exhausted_retries_yields_error_message(mock_gemini_api):
    mock_gemini_api.side_effect = google_exc.ServiceUnavailable("503")
    client = GeminiClient(api_key="test")
    chunks = [chunk async for chunk in client.stream_chat([...])]
    assert mock_gemini_api.call_count == 5  # retry ครบ 5 ครั้ง
    assert "⚠️" in chunks[0]

async def test_retry_is_logged(mock_gemini_api, mock_structlog):
    mock_gemini_api.side_effect = [
        google_exc.ServiceUnavailable("503"),
        MockSuccessResponse(["ok"]),
    ]
    client = GeminiClient(api_key="test")
    await list(client.stream_chat([...]))
    
    log_calls = [call for call in mock_structlog.calls if "gemini_retry" in str(call)]
    assert len(log_calls) >= 1
```

---

## Definition of Done

- [ ] transient error (503, timeout) → retry อัตโนมัติ ไม่ crash
- [ ] non-retryable error (400, 403) → fail immediately + yield error message
- [ ] retry exhausted (5 ครั้ง) → yield user-friendly error message
- [ ] ทุก retry attempt ถูก log ด้วย structlog
- [ ] Telegram 429 → sleep ตาม `retry_after` แล้ว retry
- [ ] unit tests ทุกกรณีผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| Retry ทำให้ response ช้าเกิน 3s (NFR-01) | P95 latency วัดไม่รวม Gemini generation time (ตาม spec) |
| Bot restart ระหว่าง retry | async task ถูก cancel — graceful shutdown จัดการ |
| Quota exhausted (429) | retry_if_exception_type ไม่รวม ResourceExhausted → fail fast |
