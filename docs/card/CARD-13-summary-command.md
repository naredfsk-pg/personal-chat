# CARD-13 | `/summary` Weekly Digest

**Phase:** 3 — Features  
**Priority:** P2  
**Estimated effort:** 0.5 day  
**Depends on:** CARD-07, CARD-10, CARD-11  
**Blocks:** —

---

## Goal

สรุปสิ่งที่จำ (notes) และ reminders ในช่วง 7 วันที่ผ่านมา โดยใช้ Gemini สร้างสรุปที่อ่านง่ายเป็น bullet points

---

## Tasks

### 1. เขียน `/summary` handler

```python
# src/bot/handlers/summary_handler.py
from datetime import datetime, timedelta, UTC
from aiogram.filters import Command
from aiogram.types import Message
from src.infrastructure.db.repositories.note_repository import NoteRepository
from src.infrastructure.db.repositories.reminder_repository import ReminderRepository
from src.infrastructure.gemini.client import GeminiClient, Message as GeminiMessage

router = Router()

@router.message(Command("summary"))
async def summary_handler(
    message: Message,
    user_id: int,
    note_repo: NoteRepository,
    reminder_repo: ReminderRepository,
    gemini: GeminiClient,
) -> None:
    reply = await message.reply("กำลังสรุปสัปดาห์ที่ผ่านมา...")

    now = datetime.now(UTC)
    since = now - timedelta(days=7)

    # ดึงข้อมูลจาก DB
    notes = await note_repo.list_in_range(user_id=user_id, since=since, until=now)
    reminders = await reminder_repo.list_sent_in_range(user_id=user_id, since=since, until=now)

    if not notes and not reminders:
        await reply.edit_text("ไม่มีข้อมูลในสัปดาห์ที่ผ่านมา")
        return

    # สร้าง prompt สำหรับ Gemini
    prompt = _build_summary_prompt(notes, reminders, since, now)

    # Stream summary จาก Gemini
    accumulated = ""
    async for chunk in gemini.stream_chat(
        [GeminiMessage(role="user", content=prompt)],
        system_prompt="คุณเป็น personal assistant ที่ช่วยสรุปสัปดาห์ ตอบเป็นภาษาไทย ใช้ bullet points",
    ):
        accumulated += chunk
        if len(accumulated) % 30 == 0:
            await reply.edit_text(accumulated)

    if accumulated:
        await reply.edit_text(accumulated)
    else:
        await reply.edit_text("ไม่สามารถสร้างสรุปได้ กรุณาลองใหม่")
```

### 2. สร้าง prompt ที่มีโครงสร้างดี

```python
def _build_summary_prompt(notes, reminders, since: datetime, until: datetime) -> str:
    since_str = since.strftime("%d/%m/%Y")
    until_str = until.strftime("%d/%m/%Y")

    sections = [f"สรุปข้อมูลระหว่าง {since_str} ถึง {until_str}:\n"]

    if notes:
        sections.append("## สิ่งที่บันทึกไว้:")
        for note in notes:
            date_str = note.created_at.strftime("%d/%m %H:%M")
            sections.append(f"- [{date_str}] {note.content}")

    if reminders:
        sections.append("\n## Reminders ที่ผ่านมา:")
        for r in reminders:
            date_str = r.trigger_at.strftime("%d/%m %H:%M")
            sections.append(f"- [{date_str}] {r.message}")

    sections.append(
        "\nกรุณาสรุปให้กระชับ แบ่งเป็น 3 ส่วน:\n"
        "1. สิ่งสำคัญที่ควรจำ\n"
        "2. Pattern หรือนิสัยที่สังเกตเห็น\n"
        "3. สิ่งที่ควรติดตามต่อ"
    )

    return "\n".join(sections)
```

### 3. เพิ่ม `list_sent_in_range` ใน `ReminderRepository`

```python
# src/infrastructure/db/repositories/reminder_repository.py
async def list_sent_in_range(
    self, user_id: int, since: datetime, until: datetime
) -> list[Reminder]:
    rows = await self._db._conn.execute_fetchall(
        """
        SELECT * FROM reminders
        WHERE user_id = ?
          AND is_sent = 1
          AND trigger_at BETWEEN ? AND ?
        ORDER BY trigger_at DESC
        """,
        (user_id, since.isoformat(), until.isoformat()),
    )
    return [_row_to_reminder(r) for r in rows]
```

### 4. รองรับ custom date range

```python
@router.message(Command("summary"))
async def summary_handler(message: Message, ...) -> None:
    args = message.text.removeprefix("/summary").strip()
    
    # รองรับ: /summary 14  → สรุป 14 วัน
    days = 7
    if args.isdigit():
        days = min(int(args), 30)  # จำกัดที่ 30 วัน
    
    since = now - timedelta(days=days)
    ...
    await reply.edit_text(f"กำลังสรุป {days} วันที่ผ่านมา...")
```

---

## Unit Tests

```python
# tests/bot/handlers/test_summary_handler.py

async def test_summary_includes_notes_and_reminders(mock_repos, mock_gemini, mock_message):
    mock_repos.note_repo.list_in_range.return_value = [
        Note(id="1", user_id=1, content="ชอบกาแฟดำ", ...)
    ]
    mock_repos.reminder_repo.list_sent_in_range.return_value = [
        Reminder(id="1", user_id=1, message="ดื่มน้ำ", ...)
    ]
    mock_gemini.stream_chat = AsyncMock(return_value=async_generator(["สรุป: "]))
    
    await summary_handler(mock_message, user_id=1, ...)
    
    call_args = mock_gemini.stream_chat.call_args
    prompt = call_args[0][0][0].content
    assert "ชอบกาแฟดำ" in prompt
    assert "ดื่มน้ำ" in prompt

async def test_summary_empty_week_replies_no_data(mock_repos, mock_gemini, mock_message):
    mock_repos.note_repo.list_in_range.return_value = []
    mock_repos.reminder_repo.list_sent_in_range.return_value = []
    mock_message.text = "/summary"
    
    await summary_handler(mock_message, ...)
    mock_message.reply.edit_text.assert_called_with("ไม่มีข้อมูลในสัปดาห์ที่ผ่านมา")

async def test_summary_custom_days(mock_repos, mock_gemini, mock_message):
    mock_message.text = "/summary 14"
    await summary_handler(mock_message, ...)
    
    call_args = mock_repos.note_repo.list_in_range.call_args
    since = call_args.kwargs["since"]
    expected_since = datetime.now(UTC) - timedelta(days=14)
    assert abs((since - expected_since).total_seconds()) < 5

def test_build_summary_prompt_includes_all_sections():
    notes = [Note(id="1", content="test note", created_at=datetime.now(UTC), ...)]
    reminders = [Reminder(id="1", message="test reminder", trigger_at=datetime.now(UTC), ...)]
    
    prompt = _build_summary_prompt(notes, reminders, since=datetime.now(UTC) - timedelta(days=7), until=datetime.now(UTC))
    
    assert "สิ่งที่บันทึกไว้" in prompt
    assert "Reminders ที่ผ่านมา" in prompt
    assert "test note" in prompt
    assert "test reminder" in prompt
```

---

## Definition of Done

- [ ] `/summary` → Gemini สรุปข้อมูลจริงจาก DB (ไม่ hallucinate)
- [ ] prompt มีทั้ง notes และ reminders จาก 7 วันที่ผ่านมา
- [ ] ไม่มีข้อมูล → reply ชัดเจนว่าไม่มีข้อมูล
- [ ] `/summary 14` → สรุป 14 วัน
- [ ] unit tests ทุกกรณีผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| มี notes มากมาย (>50 รายการ) | cap ที่ 30 items ใน prompt เพื่อไม่เกิน token limit |
| Prompt ยาวเกิน Gemini context limit | truncate content ของแต่ละ note ที่ 200 ตัวอักษร |
| `/summary 0` หรือ `/summary -1` | ใช้ default 7 วัน |
| `/summary 100` | cap ที่ 30 วัน |
