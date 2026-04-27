# CARD-05 | Short-Term Buffer (10 Turns)

**Phase:** 2 — Memory  
**Priority:** P0  
**Estimated effort:** 0.5 day  
**Depends on:** CARD-04  
**Blocks:** CARD-08

---

## Goal

จำบริบทบทสนทนาย้อนหลัง 10 turns ต่อ user เพื่อให้ Gemini ตอบได้โดยรู้ context ก่อนหน้า โดยไม่ใช้ DB (in-memory เท่านั้น — เร็ว, ง่าย, stateless ระหว่าง restart)

---

## Tasks

### 1. เขียน `ConversationBuffer` class

```python
# src/core/memory/conversation_buffer.py
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, UTC

@dataclass
class Turn:
    role: str           # "user" | "model"
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

class ConversationBuffer:
    """Thread-safe short-term buffer per user. maxlen=10 ทิ้ง turn เก่าสุดอัตโนมัติ."""

    def __init__(self, max_turns: int = 10, idle_timeout_seconds: int = 1800) -> None:
        self._max_turns = max_turns
        self._idle_timeout = idle_timeout_seconds
        self._buffers: dict[int, deque[Turn]] = {}
        self._last_activity: dict[int, datetime] = {}

    def add_turn(self, user_id: int, role: str, content: str) -> None:
        self._expire_if_idle(user_id)
        if user_id not in self._buffers:
            self._buffers[user_id] = deque(maxlen=self._max_turns)
        self._buffers[user_id].append(Turn(role=role, content=content))
        self._last_activity[user_id] = datetime.now(UTC)

    def get_turns(self, user_id: int) -> list[Turn]:
        self._expire_if_idle(user_id)
        return list(self._buffers.get(user_id, []))

    def clear(self, user_id: int) -> None:
        self._buffers.pop(user_id, None)
        self._last_activity.pop(user_id, None)

    def _expire_if_idle(self, user_id: int) -> None:
        last = self._last_activity.get(user_id)
        if last is None:
            return
        idle_seconds = (datetime.now(UTC) - last).total_seconds()
        if idle_seconds > self._idle_timeout:
            self.clear(user_id)
```

### 2. Inject buffer เป็น global dependency

```python
# src/bot/main.py
buffer = ConversationBuffer(max_turns=10, idle_timeout_seconds=1800)
dp["conversation_buffer"] = buffer
```

### 3. Update `chat_handler` ให้ใช้ buffer

```python
# src/bot/handlers/chat.py
@router.message()
async def chat_handler(
    message: Message,
    gemini: GeminiClient,
    user_id: int,
    conversation_buffer: ConversationBuffer,
) -> None:
    if not message.text or message.text.startswith("/"):
        return

    # บันทึก user message
    conversation_buffer.add_turn(user_id, role="user", content=message.text)

    # ดึง history เพื่อส่ง context
    turns = conversation_buffer.get_turns(user_id)
    gemini_messages = [GeminiMessage(role=t.role, content=t.content) for t in turns]

    reply = await message.reply("...")
    accumulated = ""
    last_edit_len = 0

    async for chunk in gemini.stream_chat(gemini_messages):
        accumulated += chunk
        if len(accumulated) - last_edit_len >= 20:
            await reply.edit_text(accumulated)
            last_edit_len = len(accumulated)

    if accumulated:
        await reply.edit_text(accumulated)
        # บันทึก model response ลง buffer
        conversation_buffer.add_turn(user_id, role="model", content=accumulated)
```

### 4. `/clear` command — reset buffer

```python
@router.message(Command("clear"))
async def clear_handler(message: Message, user_id: int, conversation_buffer: ConversationBuffer) -> None:
    conversation_buffer.clear(user_id)
    await message.reply("ล้างประวัติสนทนาแล้ว")
```

---

## Unit Tests

```python
# tests/core/memory/test_conversation_buffer.py

def test_buffer_stores_turns():
    buf = ConversationBuffer()
    buf.add_turn(1, "user", "hello")
    buf.add_turn(1, "model", "hi")
    turns = buf.get_turns(1)
    assert len(turns) == 2
    assert turns[0].content == "hello"

def test_buffer_drops_oldest_at_11th_turn():
    buf = ConversationBuffer(max_turns=10)
    for i in range(11):
        buf.add_turn(1, "user", f"msg {i}")
    turns = buf.get_turns(1)
    assert len(turns) == 10
    assert turns[0].content == "msg 1"   # msg 0 ถูกทิ้ง

def test_buffer_isolated_per_user():
    buf = ConversationBuffer()
    buf.add_turn(1, "user", "user1 message")
    buf.add_turn(2, "user", "user2 message")
    assert buf.get_turns(1)[0].content == "user1 message"
    assert buf.get_turns(2)[0].content == "user2 message"

def test_clear_removes_user_buffer():
    buf = ConversationBuffer()
    buf.add_turn(1, "user", "hello")
    buf.clear(1)
    assert buf.get_turns(1) == []

def test_idle_timeout_clears_buffer():
    buf = ConversationBuffer(idle_timeout_seconds=0)  # timeout ทันที
    buf.add_turn(1, "user", "hello")
    # act: อ่าน turns (trigger _expire_if_idle)
    turns = buf.get_turns(1)
    assert turns == []

def test_get_turns_returns_empty_for_unknown_user():
    buf = ConversationBuffer()
    assert buf.get_turns(999) == []
```

---

## Definition of Done

- [ ] Gemini ตอบโดยรู้ context ย้อนหลัง 10 ข้อความ
- [ ] turn ที่ 11 ทิ้ง turn แรกสุดอัตโนมัติ
- [ ] buffer แยกต่อ user ไม่ปนกัน
- [ ] idle > 30 นาที → buffer ถูก clear อัตโนมัติ
- [ ] `/clear` reset conversation ได้
- [ ] unit tests ทุกกรณีผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| Model response ว่าง (Gemini error) | ไม่ add_turn สำหรับ model → context ไม่เสียหาย |
| User ส่งข้อความเร็วมาก (concurrent) | `deque` thread-safe สำหรับ single writer, asyncio ไม่มี race condition |
| Bot restart | buffer หาย (in-memory) — by design, จงใจให้ session ใหม่ เสมอ |
