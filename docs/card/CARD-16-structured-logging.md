# CARD-16 | Structured Logging (structlog)

**Phase:** 4 — Reliability  
**Priority:** P1  
**Estimated effort:** 0.5 day  
**Depends on:** CARD-01  
**Blocks:** —

---

## Goal

log ทุก event ในรูปแบบ JSON ที่ query ด้วย `jq` ได้ ครอบคลุมทุก request/response/error โดยไม่ log content ของ user message

---

## Tasks

### 1. ติดตั้งและตั้งค่า structlog

```python
# src/bot/logging_config.py
import logging
import structlog
import os

def configure_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # Dev: pretty print / Prod: JSON
            structlog.dev.ConsoleRenderer() if os.getenv("ENV") == "dev"
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level, logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
```

เรียกใน `main.py` ก่อนทุกอย่าง:

```python
configure_logging()
log = structlog.get_logger()
```

### 2. Middleware สำหรับ log ทุก request

```python
# src/bot/middlewares/logging_middleware.py
import time
import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

log = structlog.get_logger()

class LoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        start = time.perf_counter()

        # Bind context vars เพื่อให้ทุก log ใน request นี้มี user_id อัตโนมัติ
        with structlog.contextvars.bound_contextvars(
            user_id=user.id if user else None,
            update_type=type(event).__name__,
        ):
            try:
                result = await handler(event, data)
                elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                log.info("request_completed", latency_ms=elapsed_ms)
                return result
            except Exception as e:
                elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                log.error(
                    "request_failed",
                    latency_ms=elapsed_ms,
                    error=str(e),
                    error_type=type(e).__name__,
                    exc_info=True,
                )
                raise
```

### 3. Log events ที่ต้องมี

| Event | Level | Fields |
|-------|-------|--------|
| `request_completed` | INFO | `user_id`, `update_type`, `latency_ms` |
| `request_failed` | ERROR | `user_id`, `update_type`, `latency_ms`, `error`, `error_type` |
| `blocked_update` | WARNING | `user_id`, `update_type` |
| `gemini_request_sent` | DEBUG | `user_id`, `message_count`, `has_image` |
| `gemini_response_received` | DEBUG | `user_id`, `chunk_count`, `latency_ms` |
| `gemini_retry` | WARNING | `attempt`, `wait_seconds`, `error` |
| `reminder_sent` | INFO | `user_id`, `reminder_id` |
| `reminder_missed` | WARNING | `user_id`, `reminder_id`, `overdue_seconds` |
| `quota_warned` | WARNING | `user_id`, `count`, `limit` |
| `quota_exceeded` | ERROR | `user_id`, `count`, `limit` |
| `reminders_restored` | INFO | `count` |

### 4. Log ใน `GeminiClient`

```python
# src/infrastructure/gemini/client.py
log = structlog.get_logger()

async def stream_chat(self, messages, system_prompt=None):
    log.debug(
        "gemini_request_sent",
        message_count=len(messages),
        has_image=any(m.image_bytes for m in messages),
    )
    start = time.perf_counter()
    chunk_count = 0

    try:
        chunks = await self._stream_with_retry(messages, system_prompt)
        for chunk in chunks:
            chunk_count += 1
            yield chunk
    finally:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        log.debug("gemini_response_received", chunk_count=chunk_count, latency_ms=elapsed_ms)
```

**ข้อห้าม:** ห้าม log content ของ message user หรือ response จาก Gemini

### 5. ตัวอย่าง JSON log output

```json
{"timestamp": "2024-01-15T09:30:00.123Z", "level": "info", "event": "request_completed", "user_id": 123456789, "update_type": "Message", "latency_ms": 45.2}
{"timestamp": "2024-01-15T09:30:01.456Z", "level": "debug", "event": "gemini_request_sent", "user_id": 123456789, "message_count": 5, "has_image": false}
{"timestamp": "2024-01-15T09:30:04.789Z", "level": "warning", "event": "gemini_retry", "attempt": 2, "wait_seconds": 2.4, "error": "ServiceUnavailable: 503"}
```

### 6. Query examples ด้วย `jq`

```bash
# ดู average latency
cat bot.log | jq 'select(.event == "request_completed") | .latency_ms' | awk '{sum+=$1; n++} END {print sum/n}'

# ดู errors ทั้งหมด
cat bot.log | jq 'select(.level == "error")'

# นับ requests ต่อ user
cat bot.log | jq 'select(.event == "request_completed") | .user_id' | sort | uniq -c
```

---

## Unit Tests

```python
# tests/bot/middlewares/test_logging_middleware.py

async def test_logs_request_completed_on_success(mock_structlog):
    middleware = LoggingMiddleware()
    handler = AsyncMock(return_value=None)
    event = MockMessage()
    data = {"event_from_user": MockUser(id=123)}
    
    await middleware(handler, event, data)
    
    log_calls = mock_structlog.info.call_args_list
    assert any("request_completed" in str(call) for call in log_calls)
    assert any("latency_ms" in str(call) for call in log_calls)

async def test_logs_error_on_handler_exception(mock_structlog):
    middleware = LoggingMiddleware()
    handler = AsyncMock(side_effect=ValueError("test error"))
    
    with pytest.raises(ValueError):
        await middleware(handler, MockMessage(), {"event_from_user": MockUser(id=1)})
    
    log_calls = mock_structlog.error.call_args_list
    assert any("request_failed" in str(call) for call in log_calls)

async def test_does_not_log_message_content(mock_structlog, mock_message):
    mock_message.text = "ข้อความส่วนตัว"
    middleware = LoggingMiddleware()
    await middleware(AsyncMock(), mock_message, {"event_from_user": MockUser(id=1)})
    
    all_log_args = str(mock_structlog.info.call_args_list)
    assert "ข้อความส่วนตัว" not in all_log_args
```

---

## Definition of Done

- [ ] ทุก request มี structured JSON log พร้อม `user_id`, `update_type`, `latency_ms`
- [ ] error log มี stack trace
- [ ] Gemini log มี `message_count`, `has_image` แต่ไม่มี content
- [ ] `LOG_LEVEL` env var เปลี่ยน log level ได้
- [ ] `ENV=dev` → pretty print / `ENV=prod` → JSON
- [ ] unit tests ยืนยันว่าไม่ log content
- [ ] `jq` query ทำงานได้กับ log output

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| Exception ใน middleware เอง | log แล้ว re-raise เสมอ ไม่กลืน |
| user เป็น None (channel post) | log `user_id: null` |
| Log file โตมากใน production | แนะนำใช้ `logrotate` หรือ redirect to stdout แล้วใช้ Docker logging driver |
