# CARD-09 | `/remember` + `/recall` Commands

**Phase:** 3 — Features  
**Priority:** P1  
**Estimated effort:** 0.5 day  
**Depends on:** CARD-06, CARD-07  
**Blocks:** —

---

## Goal

user สั่ง `/remember` เพื่อบันทึกข้อมูลลง long-term memory และ `/recall` เพื่อค้นหาด้วย semantic search

---

## Tasks

### 1. `/remember <text>` command

```python
# src/bot/handlers/memory_commands.py
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from src.core.memory.memory_service import MemoryService
from src.infrastructure.db.repositories.note_repository import NoteRepository

router = Router()

@router.message(Command("remember"))
async def remember_handler(
    message: Message,
    user_id: int,
    memory_service: MemoryService,
    note_repo: NoteRepository,
) -> None:
    # ดึง text หลัง /remember
    text = message.text.removeprefix("/remember").strip()

    if not text:
        await message.reply(
            "กรุณาระบุสิ่งที่ต้องการจำ\nตัวอย่าง: `/remember ชอบกาแฟดำ ไม่ใส่น้ำตาล`",
            parse_mode="Markdown",
        )
        return

    # embed + upsert ลง ChromaDB
    embedding_id = await memory_service.remember(user_id=user_id, content=text)

    # บันทึก metadata ลง SQLite ด้วย (เพื่อให้ /note list และ /summary ใช้ได้)
    await note_repo.create(user_id=user_id, content=text, embedding_id=embedding_id)

    await message.reply(f"จำแล้ว: _{text}_", parse_mode="Markdown")
```

### 2. `/recall <query>` command

```python
@router.message(Command("recall"))
async def recall_handler(
    message: Message,
    user_id: int,
    memory_service: MemoryService,
) -> None:
    query = message.text.removeprefix("/recall").strip()

    if not query:
        await message.reply(
            "กรุณาระบุสิ่งที่ต้องการค้นหา\nตัวอย่าง: `/recall ชอบกินอะไร`",
            parse_mode="Markdown",
        )
        return

    results = await memory_service.recall(user_id=user_id, query=query, top_k=5)

    if not results:
        await message.reply("ไม่พบข้อมูลที่เกี่ยวข้อง")
        return

    # format ผลลัพธ์
    lines = ["*ผลการค้นหา:*\n"]
    for i, result in enumerate(results, 1):
        relevance = round((1 - result.score) * 100)  # แปลง distance เป็น %
        lines.append(f"{i}. {result.content} _(เกี่ยวข้อง {relevance}%)_")

    await message.reply("\n".join(lines), parse_mode="Markdown")
```

### 3. Format ผลลัพธ์ที่ดี

ตัวอย่าง output ที่ควรได้:

```
ผลการค้นหา:

1. ชอบกาแฟดำ ไม่ใส่น้ำตาล (เกี่ยวข้อง 92%)
2. ดื่มกาแฟทุกเช้าก่อนประชุม (เกี่ยวข้อง 78%)
3. แพ้คาเฟอีนถ้าดื่มหลัง 16:00 (เกี่ยวข้อง 65%)
```

### 4. Register router และ dependencies

```python
# src/bot/main.py
from src.bot.handlers.memory_commands import router as memory_router
dp.include_router(memory_router)
dp["note_repo"] = NoteRepository(db)
```

---

## Integration Tests

```python
# tests/bot/handlers/test_memory_commands.py

async def test_remember_saves_to_chroma_and_sqlite(mock_memory_service, mock_note_repo, mock_message):
    mock_message.text = "/remember ชอบกาแฟดำ"
    await remember_handler(mock_message, user_id=1, memory_service=mock_memory_service, note_repo=mock_note_repo)
    
    mock_memory_service.remember.assert_called_once_with(user_id=1, content="ชอบกาแฟดำ")
    mock_note_repo.create.assert_called_once()

async def test_remember_empty_text_replies_with_usage(mock_message):
    mock_message.text = "/remember"
    await remember_handler(mock_message, user_id=1, ...)
    mock_message.reply.assert_called_with_text_containing("ตัวอย่าง")

async def test_recall_returns_formatted_results(mock_memory_service, mock_message):
    mock_message.text = "/recall กาแฟ"
    mock_memory_service.recall.return_value = [
        SearchResult(id="1", content="ชอบกาแฟดำ", score=0.08, metadata={})
    ]
    await recall_handler(mock_message, user_id=1, memory_service=mock_memory_service)
    
    reply_text = mock_message.reply.call_args[0][0]
    assert "ชอบกาแฟดำ" in reply_text
    assert "92%" in reply_text  # 1 - 0.08 = 92%

async def test_recall_no_results_replies_not_found(mock_memory_service, mock_message):
    mock_message.text = "/recall สิ่งที่ไม่มีอยู่"
    mock_memory_service.recall.return_value = []
    await recall_handler(mock_message, user_id=1, memory_service=mock_memory_service)
    
    mock_message.reply.assert_called_with("ไม่พบข้อมูลที่เกี่ยวข้อง")

async def test_recall_empty_query_replies_with_usage(mock_message):
    mock_message.text = "/recall"
    await recall_handler(mock_message, user_id=1, ...)
    mock_message.reply.assert_called_with_text_containing("ตัวอย่าง")
```

---

## Definition of Done

- [ ] `/remember ข้อความ` → บันทึกลง ChromaDB + SQLite + reply ยืนยัน
- [ ] `/remember` ไม่มีข้อความ → reply แนะนำวิธีใช้
- [ ] `/recall คำค้น` → คืน top-5 ที่เรียงตาม relevance พร้อม %
- [ ] `/recall` ไม่มีข้อความ → reply แนะนำวิธีใช้
- [ ] `/recall` ไม่พบผล → reply "ไม่พบข้อมูล"
- [ ] integration tests ทุกกรณีผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| `/remember` text ยาวมาก (>2000 ตัวอักษร) | รับได้ แต่ truncate ที่ 2000 ก่อน embed + แจ้ง user |
| ChromaDB ล้มเหลวระหว่าง remember | ยกเลิก SQLite insert ด้วย (atomic) + reply error |
| `/recall` แล้ว results มี score สูงทุกตัว (ไม่เกี่ยว) | แสดงทั้งหมดพร้อม % ต่ำ ให้ user ตัดสิน |
| ข้อความมี special Markdown characters | escape ก่อน reply เพื่อไม่ให้ Telegram parse error |
