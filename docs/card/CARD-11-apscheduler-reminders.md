# CARD-11 | APScheduler Reminders

**Phase:** 3 — Features  
**Priority:** P1  
**Estimated effort:** 1 day  
**Depends on:** CARD-07  
**Blocks:** CARD-13 (summary แสดง reminders ที่ผ่านมา)

---

## Goal

user ตั้ง reminder ผ่าน `/remind` แล้วได้รับ Telegram message ตรงเวลาที่กำหนด แม้ bot ถูก restart ก็ยังส่งได้ (ดึง pending reminders จาก SQLite ตอน startup)

---

## Tasks

### 1. ติดตั้ง dependencies

```
apscheduler>=3.10
dateparser>=1.2       # parse "พรุ่งนี้ 9 โมง", "in 2 hours", "tomorrow 9am"
```

### 2. เขียน `ReminderService`

```python
# src/core/reminders/reminder_service.py
from datetime import datetime, UTC
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from aiogram import Bot
from src.infrastructure.db.repositories.reminder_repository import ReminderRepository, Reminder

class ReminderService:
    def __init__(self, scheduler: AsyncIOScheduler, bot: Bot, repo: ReminderRepository) -> None:
        self._scheduler = scheduler
        self._bot = bot
        self._repo = repo

    async def schedule(self, user_id: int, message: str, trigger_at: datetime) -> Reminder:
        """สร้าง reminder ใน DB แล้ว schedule job."""
        reminder = await self._repo.create(user_id=user_id, message=message, trigger_at=trigger_at)
        self._add_job(reminder)
        return reminder

    def _add_job(self, reminder: Reminder) -> None:
        self._scheduler.add_job(
            self._send_reminder,
            trigger=DateTrigger(run_date=reminder.trigger_at),
            args=[reminder.id, reminder.user_id, reminder.message],
            id=f"reminder_{reminder.id}",
            replace_existing=True,
            misfire_grace_time=300,  # ถ้า bot down ≤ 5 นาที ยังส่งได้
        )

    async def _send_reminder(self, reminder_id: str, user_id: int, message: str) -> None:
        """Callback ที่ APScheduler เรียกตอนถึงเวลา."""
        await self._bot.send_message(user_id, f"⏰ *Reminder:* {message}", parse_mode="Markdown")
        await self._repo.mark_sent(reminder_id)

    async def restore_pending(self) -> int:
        """โหลด pending reminders จาก DB และ schedule ใหม่ (เรียกตอน startup)."""
        pending = await self._repo.get_pending(until=datetime(9999, 12, 31, tzinfo=UTC))
        count = 0
        for reminder in pending:
            if reminder.trigger_at > datetime.now(UTC):
                self._add_job(reminder)
                count += 1
            else:
                # reminder ที่ผ่านมาแล้วตอน bot down — ส่งทันที
                await self._send_reminder(reminder.id, reminder.user_id, reminder.message)
        return count

    async def cancel(self, reminder_id: str, user_id: int) -> bool:
        """ยกเลิก reminder."""
        success = await self._repo.delete(reminder_id, user_id)
        if success:
            job_id = f"reminder_{reminder_id}"
            if self._scheduler.get_job(job_id):
                self._scheduler.remove_job(job_id)
        return success
```

### 3. Parse เวลาจาก text

```python
# src/core/reminders/time_parser.py
import dateparser
from datetime import datetime, UTC
from zoneinfo import ZoneInfo

THAI_TZ = ZoneInfo("Asia/Bangkok")

def parse_reminder_time(text: str) -> datetime | None:
    """
    รองรับหลายรูปแบบ:
    - "พรุ่งนี้ 9 โมง"
    - "in 2 hours"
    - "tomorrow 9am"
    - "2024-12-25 08:00"
    - "วันศุกร์ 3 ทุ่ม"
    คืน None ถ้า parse ไม่ได้
    """
    settings = {
        "PREFER_DATES_FROM": "future",
        "TIMEZONE": "Asia/Bangkok",
        "RETURN_AS_TIMEZONE_AWARE": True,
    }
    parsed = dateparser.parse(text, settings=settings, languages=["th", "en"])
    return parsed
```

### 4. เขียน `/remind` handler

```python
# src/bot/handlers/reminder_handler.py
from aiogram.filters import Command
from aiogram.types import Message
import re

TIME_PATTERN = re.compile(r"^(.+?)\s+(in \d+|tomorrow|พรุ่งนี้|วัน\S+|\d{4}-\d{2}-\d{2}.*|\d+:\d+.*)$", re.IGNORECASE)

@router.message(Command("remind"))
async def remind_handler(
    message: Message,
    user_id: int,
    reminder_service: ReminderService,
) -> None:
    args = message.text.removeprefix("/remind").strip()

    if not args:
        await message.reply(
            "*วิธีใช้ /remind:*\n"
            "`/remind <ข้อความ> <เวลา>`\n\n"
            "ตัวอย่าง:\n"
            "`/remind ดื่มน้ำ in 30 minutes`\n"
            "`/remind ประชุม พรุ่งนี้ 9 โมง`\n"
            "`/remind โทรหาแม่ tomorrow 8am`",
            parse_mode="Markdown",
        )
        return

    # แยก message กับ time expression
    # strategy: ลอง parse จากท้ายไปหน้า
    time_dt, reminder_text = _split_time_and_message(args)

    if time_dt is None:
        await message.reply(
            "ไม่สามารถ parse เวลาได้ กรุณาระบุเวลาให้ชัดเจน\n"
            "เช่น: `/remind ดื่มน้ำ in 30 minutes`",
            parse_mode="Markdown",
        )
        return

    if time_dt <= datetime.now(UTC):
        await message.reply("เวลาที่ระบุผ่านมาแล้ว กรุณาระบุเวลาในอนาคต")
        return

    reminder = await reminder_service.schedule(
        user_id=user_id,
        message=reminder_text,
        trigger_at=time_dt,
    )

    time_str = time_dt.astimezone(THAI_TZ).strftime("%d/%m/%Y %H:%M น.")
    await message.reply(
        f"ตั้ง reminder แล้ว\n"
        f"📝 {reminder_text}\n"
        f"⏰ {time_str}",
    )


def _split_time_and_message(text: str) -> tuple[datetime | None, str]:
    """ลอง parse เวลาจากส่วนท้ายของ text ทีละ word."""
    words = text.split()
    for i in range(len(words) - 1, 0, -1):
        time_candidate = " ".join(words[i:])
        dt = parse_reminder_time(time_candidate)
        if dt:
            return dt, " ".join(words[:i])
    return None, text
```

### 5. `/reminders` — list upcoming

```python
@router.message(Command("reminders"))
async def list_reminders_handler(message: Message, user_id: int, repo: ReminderRepository) -> None:
    upcoming = await repo.list_upcoming(user_id=user_id, limit=10)
    if not upcoming:
        await message.reply("ไม่มี reminder ที่รออยู่")
        return

    lines = ["*Reminders ที่รออยู่:*\n"]
    for r in upcoming:
        time_str = r.trigger_at.astimezone(THAI_TZ).strftime("%d/%m %H:%M น.")
        short_id = r.id[:8]
        lines.append(f"`[{short_id}]` ⏰ {time_str} — {r.message}")

    lines.append("\nยกเลิกด้วย `/cancel <id>`")
    await message.reply("\n".join(lines), parse_mode="Markdown")
```

### 6. Startup: restore pending reminders

```python
# src/bot/main.py
scheduler = AsyncIOScheduler(timezone=UTC)
reminder_service = ReminderService(scheduler=scheduler, bot=bot, repo=reminder_repo)

async def on_startup() -> None:
    scheduler.start()
    restored = await reminder_service.restore_pending()
    log.info("reminders_restored", count=restored)
```

---

## Unit Tests

```python
# tests/core/reminders/test_reminder_service.py

async def test_schedule_creates_db_record_and_job(mock_repo, mock_scheduler, mock_bot):
    service = ReminderService(mock_scheduler, mock_bot, mock_repo)
    trigger_at = datetime.now(UTC).replace(hour=23, minute=0)
    
    await service.schedule(user_id=1, message="test", trigger_at=trigger_at)
    
    mock_repo.create.assert_called_once()
    mock_scheduler.add_job.assert_called_once()

async def test_restore_pending_schedules_future_reminders(mock_repo, mock_scheduler, mock_bot):
    future_time = datetime.now(UTC) + timedelta(hours=1)
    mock_repo.get_pending.return_value = [
        Reminder(id="abc", user_id=1, message="test", trigger_at=future_time, is_sent=False)
    ]
    service = ReminderService(mock_scheduler, mock_bot, mock_repo)
    count = await service.restore_pending()
    
    assert count == 1
    mock_scheduler.add_job.assert_called_once()

async def test_restore_sends_missed_reminders_immediately(mock_repo, mock_scheduler, mock_bot):
    past_time = datetime.now(UTC) - timedelta(minutes=10)
    mock_repo.get_pending.return_value = [
        Reminder(id="abc", user_id=1, message="missed!", trigger_at=past_time, is_sent=False)
    ]
    service = ReminderService(mock_scheduler, mock_bot, mock_repo)
    await service.restore_pending()
    
    mock_bot.send_message.assert_called_once_with(1, ANY)
    mock_repo.mark_sent.assert_called_once_with("abc")

# tests/core/reminders/test_time_parser.py

def test_parse_relative_english():
    result = parse_reminder_time("in 30 minutes")
    assert result is not None
    assert result > datetime.now(UTC)

def test_parse_tomorrow():
    result = parse_reminder_time("tomorrow 9am")
    assert result is not None

def test_parse_invalid_returns_none():
    result = parse_reminder_time("ข้อความสุ่ม")
    assert result is None
```

---

## Definition of Done

- [ ] `/remind <msg> <time>` → ได้รับ reminder ตรงเวลา
- [ ] bot restart → pending reminders ถูก restore และส่งต่อได้
- [ ] reminder ที่ missed ระหว่าง bot down → ส่งทันทีตอน startup
- [ ] `/reminders` แสดง upcoming list พร้อม short ID
- [ ] `/cancel <id>` ยกเลิก reminder ได้
- [ ] unit tests ทุกกรณีผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| เวลาในอดีต | reply error "เวลาผ่านมาแล้ว" |
| dateparser parse ผิด timezone | ตั้ง `TIMEZONE=Asia/Bangkok` ใน settings |
| Bot down นานเกิน `misfire_grace_time` (5 นาที) | APScheduler skip job → restore_pending จัดการตอน restart |
| User ลบ chat กับ bot ก่อนถึงเวลา | `send_message` raise `BotBlocked` → catch แล้ว mark sent + log |
| Reminder message มีอักขระพิเศษ | escape ก่อนส่ง |
