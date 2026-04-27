# CARD-03 | Gemini Client (Streaming)

**Phase:** 1 — Core  
**Priority:** P0  
**Estimated effort:** 1 day  
**Depends on:** CARD-01  
**Blocks:** CARD-04, CARD-12

---

## Goal

เขียน `GeminiClient` ที่ stream response กลับมาทีละ chunk รองรับทั้ง text-only และ multimodal (image) พร้อม retry logic และ quota tracking

---

## Tasks

### 1. ติดตั้ง SDK

```
google-generativeai>=0.8
tenacity
```

### 2. เขียน `GeminiClient` class

```python
# src/infrastructure/gemini/client.py
from dataclasses import dataclass
from typing import AsyncIterator
import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@dataclass(frozen=True)
class Message:
    role: str          # "user" | "model"
    content: str
    image_bytes: bytes | None = None

class GeminiClient:
    def __init__(self, api_key: str, model_name: str = "gemini-1.5-flash") -> None:
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model_name)

    async def stream_chat(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield text chunks as they arrive from Gemini."""
        ...

    async def embed_text(self, text: str) -> list[float]:
        """คืน embedding vector สำหรับ text (ใช้ใน CARD-06)."""
        ...
```

### 3. แปลง `Message` list เป็น Gemini format

Gemini SDK ใช้ format ต่างจาก OpenAI — ต้องแปลงก่อน:

```python
def _to_gemini_history(self, messages: list[Message]) -> list[dict]:
    history = []
    for msg in messages[:-1]:  # ทุกข้อความยกเว้นล่าสุด
        parts = [msg.content]
        if msg.image_bytes:
            parts.append({"mime_type": "image/jpeg", "data": msg.image_bytes})
        history.append({"role": msg.role, "parts": parts})
    return history
```

### 4. Implement streaming

```python
@retry(
    retry=retry_if_exception_type((Exception,)),
    wait=wait_exponential(multiplier=1, min=1, max=32),
    stop=stop_after_attempt(5),
)
async def stream_chat(self, messages: list[Message], ...) -> AsyncIterator[str]:
    chat = self._model.start_chat(history=self._to_gemini_history(messages))
    last_msg = messages[-1]
    
    parts = [last_msg.content]
    if last_msg.image_bytes:
        parts.append({"mime_type": "image/jpeg", "data": last_msg.image_bytes})
    
    response = await chat.send_message_async(parts, stream=True)
    async for chunk in response:
        if chunk.text:
            yield chunk.text
```

### 5. จัดการ error cases

```python
# ใน stream_chat — wrap ด้วย try/except หลัง retry หมด
try:
    async for chunk in self._stream_with_retry(messages):
        yield chunk
except google.api_core.exceptions.ResourceExhausted:
    yield "⚠️ Gemini quota หมดแล้ว ลองใหม่พรุ่งนี้"
except google.api_core.exceptions.InvalidArgument as e:
    yield f"⚠️ ข้อความไม่ถูกต้อง: {e}"
except Exception:
    yield "⚠️ เกิดข้อผิดพลาด กรุณาลองใหม่อีกครั้ง"
```

### 6. Embed text method

```python
async def embed_text(self, text: str) -> list[float]:
    result = await genai.embed_content_async(
        model="models/text-embedding-004",
        content=text,
        task_type="retrieval_document",
    )
    return result["embedding"]
```

### 7. เพิ่ม `GEMINI_API_KEY` ใน Config

```python
# src/bot/config.py — เพิ่มใน Config dataclass
gemini_api_key: str
gemini_model: str = "gemini-1.5-flash"
```

---

## Unit Tests

```python
# tests/infrastructure/test_gemini_client.py

async def test_stream_chat_yields_chunks(mock_gemini):
    # mock streaming response ที่ yield 3 chunks
    # assert: ได้รับ chunks ครบ 3 ชิ้น

async def test_retry_on_transient_error(mock_gemini_with_failures):
    # simulate: 2 ครั้งแรก raise Exception, ครั้งที่ 3 สำเร็จ
    # assert: client retry จนสำเร็จ, ไม่ raise exception

async def test_quota_exceeded_returns_user_message(mock_gemini_quota_error):
    # simulate: ResourceExhausted after all retries
    # assert: yield message เตือน quota (ไม่ raise)

async def test_multimodal_message_includes_image(mock_gemini):
    # assert: request ที่ส่งไป Gemini มี image bytes อยู่

async def test_embed_text_returns_vector(mock_embed):
    # assert: คืน list[float] ที่มี length > 0
```

---

## Definition of Done

- [ ] `stream_chat()` yield chunks ทีละส่วนได้
- [ ] retry 5 ครั้งก่อน fail
- [ ] quota error → yield warning message ไม่ raise exception
- [ ] multimodal message ส่ง image bytes ได้ถูกต้อง
- [ ] `embed_text()` คืน vector ที่ใช้กับ ChromaDB ได้
- [ ] unit tests ทุก case ผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| Response chunk ว่างเปล่า (`chunk.text = ""`) | skip ไม่ yield |
| Message content ยาวเกิน context limit | Gemini จะ raise error → catch แล้ว truncate และ retry |
| Image bytes เสียหาย / format ไม่รองรับ | catch `InvalidArgument` → yield error message |
| Network timeout | tenacity retry ตาม config |
| Model ถูก deprecate | log error ชัดเจนว่า model name ใดที่ล้มเหลว |
