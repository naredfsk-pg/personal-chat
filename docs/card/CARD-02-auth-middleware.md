# CARD-02 | Auth Middleware (Whitelist)

**Phase:** 1 — Core  
**Priority:** P0  
**Estimated effort:** 0.5 day  
**Depends on:** CARD-01  
**Blocks:** CARD-04

---

## Goal

ปิดกั้น request จาก user ที่ไม่ได้รับอนุญาตทุก update โดยไม่มีทางรั่ว ไม่ว่าจะเป็น text, image, voice, หรือ inline query

---

## Tasks

### 1. เขียน `AuthMiddleware` class

middleware ต้องดัก `user_id` ก่อน handler ทุกตัว:

```python
# src/bot/middlewares/auth.py
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from typing import Any, Callable, Awaitable

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
            # silent ignore — ไม่ตอบ ไม่ error ไม่ log content
            return None
        return await handler(event, data)
```

### 2. Register middleware ใน `main.py`

ต้อง register บน `dp.update` (ไม่ใช่ message เท่านั้น) เพื่อดักทุก update type:

```python
config = load_config()
dp.update.middleware(AuthMiddleware(config.allowed_user_ids))
```

### 3. Inject `user_id` เข้า handler context

หลัง auth ผ่านแล้ว inject `user_id` เป็น dependency เพื่อให้ handler ไม่ต้องดึงซ้ำ:

```python
# เพิ่มใน middleware หลัง auth ผ่าน
data["user_id"] = user.id
return await handler(event, data)
```

### 4. Logging สำหรับ blocked request

log แค่ `user_id` และ `update_type` ที่ถูก block — **ห้าม log content** ของ message:

```python
import structlog
log = structlog.get_logger()

if user.id not in self._allowed:
    log.warning("blocked_update", user_id=user.id, update_type=type(event).__name__)
    return None
```

---

## Unit Tests

### Test cases ที่ต้องมี

```python
# tests/bot/test_auth_middleware.py

async def test_allowed_user_passes_through():
    # arrange: middleware กับ allowed_user_ids={12345}
    # act: ส่ง update จาก user_id=12345
    # assert: handler ถูกเรียก 1 ครั้ง

async def test_blocked_user_is_silently_ignored():
    # arrange: middleware กับ allowed_user_ids={12345}
    # act: ส่ง update จาก user_id=99999
    # assert: handler ไม่ถูกเรียกเลย, ไม่มี exception

async def test_update_without_user_is_blocked():
    # arrange: update ที่ไม่มี from_user (เช่น channel post)
    # assert: handler ไม่ถูกเรียก

async def test_blocked_user_is_logged():
    # assert: structlog ถูกเรียกด้วย user_id และ update_type
    # assert: log ไม่มี message content

async def test_user_id_injected_into_context():
    # assert: data["user_id"] == user.id หลัง middleware ผ่าน
```

---

## Definition of Done

- [ ] user นอก whitelist ไม่ได้รับ reply ทุกกรณี (text, photo, voice, command)
- [ ] user ใน whitelist ผ่านและ `data["user_id"]` ถูก inject
- [ ] blocked request ถูก log ด้วย `user_id` + `update_type` (ไม่มี content)
- [ ] unit tests ทุกกรณีผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| `ALLOWED_USER_IDS` ว่างเปล่า | `frozenset()` ว่าง → block ทุกคน + log warning ตอน startup |
| Update ไม่มี `from_user` (channel post) | block เสมอ |
| User เพิ่งถูกลบออกจาก whitelist | block ทันทีใน request ถัดไป (stateless check) |
