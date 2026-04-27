# CARD-12 | Image/Multimodal Handler

**Phase:** 3 — Features  
**Priority:** P2  
**Estimated effort:** 0.5 day  
**Depends on:** CARD-03, CARD-04  
**Blocks:** —

---

## Goal

รับรูปภาพจาก Telegram → download → ส่ง Gemini Vision วิเคราะห์ → stream response กลับ รองรับ caption เป็น prompt เพิ่มเติมได้

---

## Tasks

### 1. เขียน `photo_handler`

```python
# src/bot/handlers/photo_handler.py
from aiogram import Router, Bot
from aiogram.types import Message
from src.infrastructure.gemini.client import GeminiClient, Message as GeminiMessage
from src.core.memory.context_builder import ContextBuilder

router = Router()
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024   # 20MB — Telegram limit

@router.message(F.photo)
async def photo_handler(
    message: Message,
    bot: Bot,
    gemini: GeminiClient,
    user_id: int,
    conversation_buffer,
    context_builder: ContextBuilder,
) -> None:
    # เลือก photo resolution สูงสุด (list เรียงจากเล็กไปใหญ่)
    photo = message.photo[-1]

    # ตรวจ file size ก่อน download
    file_info = await bot.get_file(photo.file_id)
    if file_info.file_size and file_info.file_size > MAX_FILE_SIZE_BYTES:
        await message.reply("ไฟล์รูปใหญ่เกิน 20MB กรุณาส่งรูปขนาดเล็กกว่านี้")
        return

    # Download image bytes
    reply = await message.reply("กำลังวิเคราะห์รูป...")
    image_bytes = await _download_image(bot, file_info)

    # ใช้ caption เป็น prompt ถ้ามี
    prompt = message.caption or "อธิบายรูปนี้ให้ละเอียด"

    # Build context (short-term history)
    context = await context_builder.build(user_id=user_id, query=prompt)
    conversation_buffer.add_turn(user_id, "user", f"[รูป] {prompt}")

    # สร้าง message ที่มีทั้ง text + image
    gemini_messages = [
        *[GeminiMessage(role=t.role, content=t.content) for t in context.short_term_turns[:-1]],
        GeminiMessage(role="user", content=prompt, image_bytes=image_bytes),
    ]

    accumulated = ""
    last_edit_len = 0

    async for chunk in gemini.stream_chat(gemini_messages, system_prompt=context.system_prompt):
        accumulated += chunk
        if len(accumulated) - last_edit_len >= 20:
            await reply.edit_text(accumulated)
            last_edit_len = len(accumulated)

    if accumulated:
        await reply.edit_text(accumulated)
        conversation_buffer.add_turn(user_id, "model", accumulated)
    else:
        await reply.edit_text("ไม่สามารถวิเคราะห์รูปได้ กรุณาลองใหม่")


async def _download_image(bot: Bot, file_info) -> bytes:
    """Download file และคืนเป็น bytes."""
    import io
    buffer = io.BytesIO()
    await bot.download_file(file_info.file_path, destination=buffer)
    return buffer.getvalue()
```

### 2. รองรับ document ที่เป็นรูป

บางครั้ง user ส่งรูปเป็น "file" (uncompressed) แทน photo:

```python
@router.message(F.document & F.document.mime_type.startswith("image/"))
async def document_image_handler(
    message: Message,
    bot: Bot,
    gemini: GeminiClient,
    user_id: int,
    conversation_buffer,
    context_builder: ContextBuilder,
) -> None:
    doc = message.document
    if doc.file_size > MAX_FILE_SIZE_BYTES:
        await message.reply("ไฟล์ใหญ่เกิน 20MB")
        return

    # เหมือน photo_handler แต่ใช้ document.file_id
    file_info = await bot.get_file(doc.file_id)
    # ... (logic เหมือนกัน)
```

### 3. ตรวจสอบ MIME type ที่รองรับ

Gemini Vision รองรับ: `image/jpeg`, `image/png`, `image/gif`, `image/webp`

```python
SUPPORTED_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

def get_mime_type(file_path: str) -> str:
    """Infer MIME type จาก file extension."""
    import mimetypes
    mime, _ = mimetypes.guess_type(file_path)
    return mime or "image/jpeg"  # default
```

### 4. อัปเดต `GeminiClient.stream_chat` ให้รับ MIME type

```python
# src/infrastructure/gemini/client.py — อัปเดต Message dataclass
@dataclass
class Message:
    role: str
    content: str
    image_bytes: bytes | None = None
    image_mime_type: str = "image/jpeg"

# ใน _to_gemini_history
if msg.image_bytes:
    parts.append({"mime_type": msg.image_mime_type, "data": msg.image_bytes})
```

---

## Unit Tests

```python
# tests/bot/handlers/test_photo_handler.py

async def test_photo_handler_downloads_and_sends_to_gemini(mock_bot, mock_gemini, mock_message):
    mock_message.photo = [MockPhotoSize(file_id="abc", file_size=1024)]
    mock_bot.get_file.return_value = MockFileInfo(file_size=1024, file_path="photos/abc.jpg")
    mock_gemini.stream_chat = AsyncMock(return_value=async_generator(["รูปนี้คือ", "แมว"]))
    
    await photo_handler(mock_message, bot=mock_bot, gemini=mock_gemini, user_id=1, ...)
    
    mock_bot.download_file.assert_called_once()
    mock_gemini.stream_chat.assert_called_once()
    last_reply = mock_message.reply.call_args_list[-1][0][0]
    assert "แมว" in last_reply

async def test_photo_too_large_replies_error(mock_bot, mock_message):
    mock_message.photo = [MockPhotoSize(file_id="big", file_size=25_000_000)]
    mock_bot.get_file.return_value = MockFileInfo(file_size=25_000_000, file_path="x")
    
    await photo_handler(mock_message, bot=mock_bot, ...)
    
    mock_message.reply.assert_called_with("ไฟล์รูปใหญ่เกิน 20MB กรุณาส่งรูปขนาดเล็กกว่านี้")

async def test_caption_used_as_prompt(mock_bot, mock_gemini, mock_message):
    mock_message.photo = [MockPhotoSize(file_id="abc", file_size=100)]
    mock_message.caption = "รูปนี้คืออะไร?"
    
    await photo_handler(mock_message, bot=mock_bot, gemini=mock_gemini, ...)
    
    call_args = mock_gemini.stream_chat.call_args
    last_message = call_args[0][0][-1]
    assert last_message.content == "รูปนี้คืออะไร?"

async def test_no_caption_uses_default_prompt(mock_bot, mock_gemini, mock_message):
    mock_message.caption = None
    await photo_handler(mock_message, ...)
    call_args = mock_gemini.stream_chat.call_args
    last_message = call_args[0][0][-1]
    assert last_message.content == "อธิบายรูปนี้ให้ละเอียด"
```

---

## Definition of Done

- [ ] ส่งรูปโดยไม่มี caption → Gemini อธิบายรูปได้
- [ ] ส่งรูปพร้อม caption → Gemini ตอบตาม caption
- [ ] รูปใหญ่เกิน 20MB → reply error ชัดเจน
- [ ] document image (uncompressed) ก็รองรับได้
- [ ] รูปใน conversation history ถูกบันทึกเป็น `[รูป] <caption>`
- [ ] unit tests ทุกกรณีผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| MIME type ไม่รองรับ (PDF, video) | reply "รองรับเฉพาะ jpeg, png, gif, webp" |
| Download ล้มเหลว (network) | reply error + log |
| Gemini ไม่สามารถวิเคราะห์รูปได้ | reply "ไม่สามารถวิเคราะห์รูปได้" |
| รูปที่ส่งมาเป็น sticker (WebP animated) | Gemini อาจ error — catch แล้ว reply แจ้ง user |
| User ส่งหลายรูปพร้อมกัน (album/media group) | แต่ละรูปจัดการแยกกัน (Telegram ส่ง update แยก) |
