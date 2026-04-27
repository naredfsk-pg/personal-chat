# CARD-10 | `/note` Command

**Phase:** 3 — Features  
**Priority:** P2  
**Estimated effort:** 0.5 day  
**Depends on:** CARD-07  
**Blocks:** CARD-13 (ใช้ notes ใน summary)

---

## Goal

บันทึก plain-text note ลง SQLite โดยไม่ต้องทำ embedding — เหมาะสำหรับข้อมูลที่ต้องการเข้าถึงตาม list ไม่ใช่ semantic search

---

## Tasks

### 1. Sub-commands ที่ต้องรองรับ

| Command | Action |
|---------|--------|
| `/note <text>` | บันทึก note ใหม่ |
| `/note list` | แสดง 10 notes ล่าสุด |
| `/note delete <id>` | ลบ note ตาม id |

### 2. เขียน handler

```python
# src/bot/handlers/note_handler.py
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from src.infrastructure.db.repositories.note_repository import NoteRepository

router = Router()

@router.message(Command("note"))
async def note_handler(message: Message, user_id: int, note_repo: NoteRepository) -> None:
    args = message.text.removeprefix("/note").strip()

    if not args:
        await _show_usage(message)
        return

    if args == "list":
        await _list_notes(message, user_id, note_repo)
    elif args.startswith("delete "):
        note_id = args.removeprefix("delete ").strip()
        await _delete_note(message, user_id, note_id, note_repo)
    else:
        await _create_note(message, user_id, args, note_repo)


async def _create_note(message: Message, user_id: int, content: str, repo: NoteRepository) -> None:
    note = await repo.create(user_id=user_id, content=content)
    # แสดง short ID (8 ตัวแรก) เพื่อให้ user ใช้ delete ได้
    short_id = note.id[:8]
    await message.reply(f"บันทึกแล้ว `[{short_id}]`", parse_mode="Markdown")


async def _list_notes(message: Message, user_id: int, repo: NoteRepository) -> None:
    notes = await repo.list_recent(user_id=user_id, limit=10)
    if not notes:
        await message.reply("ยังไม่มี note")
        return

    lines = ["*Notes ล่าสุด:*\n"]
    for note in notes:
        short_id = note.id[:8]
        date_str = note.created_at.strftime("%d/%m %H:%M")
        lines.append(f"`[{short_id}]` {date_str} — {note.content}")

    await message.reply("\n".join(lines), parse_mode="Markdown")


async def _delete_note(message: Message, user_id: int, note_id: str, repo: NoteRepository) -> None:
    # รองรับทั้ง short ID (8 ตัว) และ full UUID
    # ถ้า short ID ต้อง lookup ก่อน
    success = await repo.delete(note_id, user_id=user_id)
    if success:
        await message.reply(f"ลบแล้ว `[{note_id[:8]}]`", parse_mode="Markdown")
    else:
        await message.reply("ไม่พบ note นั้น หรือไม่ใช่ note ของคุณ")


async def _show_usage(message: Message) -> None:
    await message.reply(
        "*วิธีใช้ /note:*\n"
        "`/note <ข้อความ>` — บันทึก note\n"
        "`/note list` — ดู notes ล่าสุด\n"
        "`/note delete <id>` — ลบ note",
        parse_mode="Markdown",
    )
```

### 3. Support short ID สำหรับ delete

เพิ่ม method ใน `NoteRepository`:

```python
async def find_by_short_id(self, short_id: str, user_id: int) -> Note | None:
    """ค้นหา note จาก 8 ตัวแรกของ UUID."""
    rows = await self._db._conn.execute_fetchall(
        "SELECT * FROM notes WHERE id LIKE ? AND user_id = ? LIMIT 1",
        (f"{short_id}%", user_id),
    )
    return Note(**dict(rows[0])) if rows else None
```

อัปเดต `_delete_note` ให้ resolve short ID ก่อน:

```python
async def _delete_note(...):
    if len(note_id) == 8:
        note = await repo.find_by_short_id(note_id, user_id)
        if not note:
            await message.reply("ไม่พบ note นั้น")
            return
        note_id = note.id  # ใช้ full UUID

    success = await repo.delete(note_id, user_id=user_id)
    ...
```

---

## Unit Tests

```python
# tests/bot/handlers/test_note_handler.py

async def test_create_note_saves_and_replies_with_short_id(mock_repo, mock_message):
    mock_message.text = "/note ซื้อนม"
    await note_handler(mock_message, user_id=1, note_repo=mock_repo)
    mock_repo.create.assert_called_once_with(user_id=1, content="ซื้อนม")
    reply_text = mock_message.reply.call_args[0][0]
    assert "[" in reply_text  # มี short ID ใน reply

async def test_list_notes_shows_latest_first(mock_repo, mock_message):
    mock_message.text = "/note list"
    mock_repo.list_recent.return_value = [
        Note(id="aaa...", user_id=1, content="note 2", ...),
        Note(id="bbb...", user_id=1, content="note 1", ...),
    ]
    await note_handler(mock_message, user_id=1, note_repo=mock_repo)
    reply_text = mock_message.reply.call_args[0][0]
    assert "note 2" in reply_text
    assert reply_text.index("note 2") < reply_text.index("note 1")

async def test_list_notes_empty_replies_message(mock_repo, mock_message):
    mock_message.text = "/note list"
    mock_repo.list_recent.return_value = []
    await note_handler(mock_message, user_id=1, note_repo=mock_repo)
    mock_message.reply.assert_called_with("ยังไม่มี note")

async def test_delete_existing_note_succeeds(mock_repo, mock_message):
    mock_message.text = "/note delete abc12345"
    mock_repo.delete.return_value = True
    await note_handler(mock_message, user_id=1, note_repo=mock_repo)
    reply_text = mock_message.reply.call_args[0][0]
    assert "ลบแล้ว" in reply_text

async def test_delete_nonexistent_note_replies_error(mock_repo, mock_message):
    mock_message.text = "/note delete nonexistent"
    mock_repo.find_by_short_id.return_value = None
    await note_handler(mock_message, user_id=1, note_repo=mock_repo)
    reply_text = mock_message.reply.call_args[0][0]
    assert "ไม่พบ" in reply_text

async def test_no_args_shows_usage(mock_message):
    mock_message.text = "/note"
    await note_handler(mock_message, user_id=1, note_repo=mock_repo)
    reply_text = mock_message.reply.call_args[0][0]
    assert "วิธีใช้" in reply_text
```

---

## Definition of Done

- [ ] `/note ข้อความ` → บันทึกและแสดง short ID
- [ ] `/note list` → แสดง 10 notes ล่าสุดพร้อมวันที่และ short ID
- [ ] `/note delete <id>` → รองรับทั้ง short (8 chars) และ full UUID
- [ ] delete note ของคนอื่น → reply "ไม่พบ"
- [ ] unit tests ทุกกรณีผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| Note content มี `/note list` (command ซ้อน) | บันทึกเป็น text ปกติ |
| Short ID ซ้ำกัน (พบ > 1 note) | เลือก note แรกที่พบ + แจ้ง user |
| Note ยาวเกิน 4096 chars (Telegram limit) | truncate ที่ 200 chars ใน list view, full content ใน create confirm |
| `/note list` มี Markdown ใน content | escape ก่อน reply |
