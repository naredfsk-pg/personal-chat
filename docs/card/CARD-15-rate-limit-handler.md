# CARD-15 | Gemini Rate-Limit Handler

**Phase:** 4 — Reliability  
**Priority:** P1  
**Estimated effort:** 0.5 day  
**Depends on:** CARD-07, CARD-03  
**Blocks:** —

---

## Goal

ไม่ให้ Gemini API request เกิน 500 req/day (NFR-05 — ห่างไกล quota 1,500 จำกัด) โดย track ใน SQLite และ block request เมื่อใกล้ถึงขีดจำกัด

---

## Tasks

### 1. เขียน `QuotaGuard` class

```python
# src/core/quota/quota_guard.py
from datetime import datetime, UTC
from src.infrastructure.db.repositories.api_usage_repository import ApiUsageRepository

DAILY_LIMIT = 500
WARN_THRESHOLD = 450      # เตือนเมื่อใช้ไป 450 req

class QuotaGuard:
    def __init__(self, repo: ApiUsageRepository) -> None:
        self._repo = repo

    async def check_and_increment(self, user_id: int) -> "QuotaStatus":
        """
        ตรวจสอบ quota แล้วเพิ่ม counter
        คืน QuotaStatus ที่บอกว่า allowed, warned, หรือ exceeded
        """
        count = await self._repo.increment(user_id)
        reset_time = self._next_reset_time()

        if count > DAILY_LIMIT:
            # rollback increment — ไม่นับ request ที่ถูก block
            await self._repo.decrement(user_id)
            return QuotaStatus(allowed=False, count=count, reset_at=reset_time)

        if count >= WARN_THRESHOLD:
            return QuotaStatus(allowed=True, count=count, reset_at=reset_time, warn=True)

        return QuotaStatus(allowed=True, count=count, reset_at=reset_time)

    def _next_reset_time(self) -> datetime:
        """เที่ยงคืน UTC ของวันถัดไป."""
        now = datetime.now(UTC)
        return now.replace(hour=0, minute=0, second=0, microsecond=0).replace(
            day=now.day + 1
        )

from dataclasses import dataclass

@dataclass(frozen=True)
class QuotaStatus:
    allowed: bool
    count: int
    reset_at: datetime
    warn: bool = False
```

### 2. เพิ่ม methods ใน `ApiUsageRepository`

```python
# src/infrastructure/db/repositories/api_usage_repository.py
class ApiUsageRepository:
    async def increment(self, user_id: int) -> int:
        """เพิ่ม count ของวันนี้ คืนค่าหลังเพิ่ม."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        await self._db._conn.execute(
            """
            INSERT INTO api_usage(user_id, date, request_count) VALUES (?, ?, 1)
            ON CONFLICT(user_id, date) DO UPDATE SET request_count = request_count + 1
            """,
            (user_id, today),
        )
        await self._db._conn.commit()
        row = await self._db._conn.execute_fetchall(
            "SELECT request_count FROM api_usage WHERE user_id=? AND date=?",
            (user_id, today),
        )
        return row[0][0]

    async def decrement(self, user_id: int) -> None:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        await self._db._conn.execute(
            "UPDATE api_usage SET request_count = MAX(0, request_count - 1) WHERE user_id=? AND date=?",
            (user_id, today),
        )
        await self._db._conn.commit()

    async def get_today_count(self, user_id: int) -> int:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        rows = await self._db._conn.execute_fetchall(
            "SELECT request_count FROM api_usage WHERE user_id=? AND date=?",
            (user_id, today),
        )
        return rows[0][0] if rows else 0
```

### 3. Integrate ใน `chat_handler`

```python
# src/bot/handlers/chat.py
@router.message()
async def chat_handler(
    message: Message,
    gemini: GeminiClient,
    user_id: int,
    quota_guard: QuotaGuard,
    conversation_buffer: ConversationBuffer,
    context_builder: ContextBuilder,
) -> None:
    if not message.text or message.text.startswith("/"):
        return

    # ตรวจ quota ก่อนส่ง request ทุกครั้ง
    status = await quota_guard.check_and_increment(user_id)

    if not status.allowed:
        reset_str = status.reset_at.strftime("%H:%M UTC")
        await message.reply(
            f"⚠️ ใช้ Gemini quota ครบ {DAILY_LIMIT} req/day แล้ว\n"
            f"จะ reset เวลา {reset_str} (เที่ยงคืน UTC)"
        )
        return

    if status.warn:
        await message.reply(
            f"⚠️ เตือน: ใช้ quota ไปแล้ว {status.count}/{DAILY_LIMIT} req วันนี้",
        )
        # ยังส่ง request ต่อได้

    # ... rest of handler
```

### 4. Register ใน `main.py`

```python
quota_guard = QuotaGuard(repo=ApiUsageRepository(db))
dp["quota_guard"] = quota_guard
```

### 5. `/quota` command — ดูสถานะ

```python
@router.message(Command("quota"))
async def quota_handler(message: Message, user_id: int, quota_repo: ApiUsageRepository) -> None:
    count = await quota_repo.get_today_count(user_id)
    remaining = max(0, DAILY_LIMIT - count)
    await message.reply(
        f"📊 *Gemini Quota วันนี้*\n"
        f"ใช้ไป: {count}/{DAILY_LIMIT} req\n"
        f"เหลือ: {remaining} req\n"
        f"Reset: เที่ยงคืน UTC",
        parse_mode="Markdown",
    )
```

---

## Unit Tests

```python
# tests/core/quota/test_quota_guard.py

async def test_allowed_when_under_limit(mock_repo):
    mock_repo.increment.return_value = 100
    guard = QuotaGuard(mock_repo)
    status = await guard.check_and_increment(user_id=1)
    assert status.allowed is True
    assert status.warn is False

async def test_warn_when_near_limit(mock_repo):
    mock_repo.increment.return_value = 455  # > WARN_THRESHOLD (450)
    guard = QuotaGuard(mock_repo)
    status = await guard.check_and_increment(user_id=1)
    assert status.allowed is True
    assert status.warn is True

async def test_blocked_when_over_limit(mock_repo):
    mock_repo.increment.return_value = 501  # > DAILY_LIMIT (500)
    guard = QuotaGuard(mock_repo)
    status = await guard.check_and_increment(user_id=1)
    assert status.allowed is False
    mock_repo.decrement.assert_called_once_with(1)  # rollback

async def test_reset_time_is_next_midnight_utc():
    guard = QuotaGuard(mock_repo)
    reset_time = guard._next_reset_time()
    assert reset_time.hour == 0
    assert reset_time.minute == 0
    assert reset_time > datetime.now(UTC)

# tests/infrastructure/db/test_api_usage_repository.py

async def test_increment_creates_new_record(db):
    repo = ApiUsageRepository(db)
    count = await repo.increment(user_id=1)
    assert count == 1

async def test_increment_increases_existing_count(db):
    repo = ApiUsageRepository(db)
    await repo.increment(1)
    await repo.increment(1)
    count = await repo.increment(1)
    assert count == 3

async def test_decrement_does_not_go_below_zero(db):
    repo = ApiUsageRepository(db)
    await repo.decrement(1)  # ไม่มี record
    count = await repo.get_today_count(1)
    assert count == 0
```

---

## Definition of Done

- [ ] request ที่ 501 ถูก block พร้อม reply บอก reset time
- [ ] request ที่ 450+ มี warning แต่ยังผ่านได้
- [ ] `/quota` แสดงจำนวน req ที่ใช้ไปวันนี้
- [ ] counter reset อัตโนมัติวันถัดไป (key แยกตาม date)
- [ ] blocked request ไม่เพิ่ม counter (rollback)
- [ ] unit tests ทุกกรณีผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| Concurrent requests พร้อมกัน (race condition) | SQLite `ON CONFLICT DO UPDATE` เป็น atomic — safe |
| User ใช้ `/remember`, `/recall` ด้วย | นับ quota เหมือนกัน (ทุก Gemini call นับ) |
| Counter ไม่ reset (timezone bug) | ใช้ UTC date string `YYYY-MM-DD` เป็น key — ไม่มี DST issue |
| DAILY_LIMIT เปลี่ยนใน config | reload config แล้ว guard ใช้ค่าใหม่ทันที (ไม่ hardcode) |
