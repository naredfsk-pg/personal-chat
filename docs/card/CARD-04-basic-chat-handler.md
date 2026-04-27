# CARD-04 | Basic Chat Handler

**Phase:** 1 — Core  
**Priority:** P0  
**Estimated effort:** 0.5 day  
**Depends on:** CARD-02, CARD-03  
**Blocks:** CARD-05, CARD-08

---

## Goal

รับ text message จาก Telegram → ส่งเข้า GeminiClient → stream response กลับ Telegram แบบ progressive (user เห็นตัวอักษรทยอยปรากฏ)

---

## Tasks

### 1. เขียน `chat_handler`

```python
# src/bot/handlers/chat.py
from aiogram import Router
from aiogram.types import Message
from src.infrastructure.gemini.client import GeminiClient, Message as GeminiMessage

router = Router()

@router.message()
async def chat_handler(message: Message, gemini: GeminiClient, user_id: int) -> None:
    if not message.text:
        return

    # ส่ง "กำลังคิด..." ก่อน เพื่อ UX
    reply = await message.reply("...")

    accumulated = ""
    last_edit_len = 0

    async for chunk in gemini.stream_chat([GeminiMessage(role="user", content=message.text)]):
        accumulated += chunk
        # edit ทุก 20 ตัวอักษรใหม่ เพื่อไม่ flood Telegram API (rate limit: 30 edits/sec)
        if len(accumulated) - last_edit_len >= 20:
            await reply.edit_text(accumulated)
            last_edit_len = len(accumulated)

    # edit ครั้งสุดท้ายเพื่อให้ได้ข้อความสมบูรณ์
    if accumulated != reply.text:
        await reply.edit_text(accumulated)
```

### 2. จัดการ Telegram message length limit

Telegram จำกัด message ที่ 4096 ตัวอักษร — ถ้า response ยาวกว่านั้นต้องแยกส่ง:

```python
def split_message(text: str, max_len: int = 4096) -> list[str]:
    """แยก text เป็น chunks โดยพยายามตัดที่ขึ้นบรรทัดใหม่ก่อน."""
    if len(text) <= max_len:
        return [text]
    
    parts = []
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
```

### 3. Register `GeminiClient` เป็น dependency

inject ผ่าน aiogram middleware เพื่อให้ handler ทุกตัวใช้ได้:

```python
# src/bot/main.py
gemini = GeminiClient(api_key=config.gemini_api_key)
dp["gemini"] = gemini   # aiogram dependency injection
```

### 4. จัดการ edge cases ใน handler

```python
@router.message()
async def chat_handler(message: Message, gemini: GeminiClient, user_id: int) -> None:
    # กรณี: message ว่าง
    if not message.text or not message.text.strip():
        await message.reply("กรุณาส่งข้อความ")
        return

    # กรณี: message เป็น command (จะถูกจัดการโดย command handler แทน)
    if message.text.startswith("/"):
        return
```

### 5. Streaming edit strategy

| สถานการณ์ | พฤติกรรม |
|-----------|----------|
| chunk ยาว < 20 ตัวอักษร | รอ accumulate ก่อน edit |
| Gemini หยุดกลางทาง | edit ข้อความที่ได้มา + แจ้ง error |
| Telegram rate limit (429) | sleep 1s แล้ว retry edit |
| Response ยาวเกิน 4096 | ส่ง message แรก แล้ว send_message ต่อ (ไม่ใช่ edit) |

---

## Unit Tests

```python
# tests/bot/handlers/test_chat_handler.py

async def test_handler_sends_reply_with_streamed_content(mock_gemini, mock_message):
    # mock: gemini stream yield ["Hello", " world", "!"]
    # assert: reply.edit_text ถูกเรียกพร้อม "Hello world!"

async def test_handler_ignores_empty_message(mock_gemini, mock_empty_message):
    # assert: gemini ไม่ถูกเรียก, ส่ง "กรุณาส่งข้อความ"

async def test_handler_ignores_command_messages(mock_gemini, mock_command_message):
    # assert: gemini ไม่ถูกเรียก

async def test_split_message_under_limit():
    result = split_message("hello", 4096)
    assert result == ["hello"]

async def test_split_message_over_limit():
    long_text = "a" * 5000
    parts = split_message(long_text, 4096)
    assert len(parts) == 2
    assert all(len(p) <= 4096 for p in parts)

async def test_split_message_prefers_newline_split():
    text = ("line\n" * 1000)  # newlines ใน text
    parts = split_message(text, 100)
    assert all(not p.startswith("\n") for p in parts)
```

---

## Definition of Done

- [ ] พิมพ์ข้อความ → เห็น "..." ก่อน แล้วเห็น response ทยอยปรากฏ
- [ ] response ยาวเกิน 4096 ตัวอักษร → ส่งเป็นหลาย message
- [ ] message ว่าง → ได้รับ reply แจ้งให้ส่งข้อความ
- [ ] command (`/...`) → ไม่ถูก route เข้า chat handler
- [ ] unit tests ทุกกรณีผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| User ส่งแต่ emoji หรือ sticker | text เป็น None → return ไม่ทำอะไร |
| Telegram `edit_text` ล้มเหลว (message ถูกลบ) | catch `MessageToEditNotFound` → send new message แทน |
| Gemini ไม่ yield อะไรเลย | edit เป็น "ไม่มีคำตอบ กรุณาลองใหม่" |
| User ส่ง message ซ้ำเร็วมาก | แต่ละ message จัดการแยก async task ไม่ block กัน |
