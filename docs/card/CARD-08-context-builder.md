# CARD-08 | Context Builder (Concurrent Fetch)

**Phase:** 2 — Memory  
**Priority:** P1  
**Estimated effort:** 0.5 day  
**Depends on:** CARD-05, CARD-06  
**Blocks:** CARD-09 (ใช้ context ใน recall)

---

## Goal

รวม short-term buffer + long-term vector memory เป็น prompt context เดียวก่อนส่ง Gemini โดย fetch ทั้งสองพร้อมกัน (concurrent) เพื่อประหยัด ~30-50ms

---

## Tasks

### 1. เขียน `ContextBuilder` class

```python
# src/core/memory/context_builder.py
import asyncio
from dataclasses import dataclass
from src.core.memory.conversation_buffer import ConversationBuffer, Turn
from src.core.memory.memory_service import MemoryService
from src.infrastructure.vector_store.chroma_store import SearchResult

MAX_CONTEXT_TOKENS = 8000      # cap token budget
LONG_TERM_RELEVANCE_THRESHOLD = 0.7  # cosine distance — ไม่เอา result ที่ไม่เกี่ยว

@dataclass(frozen=True)
class BuiltContext:
    short_term_turns: list[Turn]
    long_term_memories: list[SearchResult]
    system_prompt: str

class ContextBuilder:
    def __init__(self, buffer: ConversationBuffer, memory_service: MemoryService) -> None:
        self._buffer = buffer
        self._memory_service = memory_service

    async def build(self, user_id: int, query: str) -> BuiltContext:
        """Fetch short-term + long-term concurrently แล้วรวมเป็น context."""
        short_term_task = asyncio.create_task(self._get_short_term(user_id))
        long_term_task = asyncio.create_task(self._get_long_term(user_id, query))

        short_term, long_term = await asyncio.gather(short_term_task, long_term_task)

        # filter long-term ที่ไม่เกี่ยวข้องออก
        relevant_memories = [m for m in long_term if m.score <= LONG_TERM_RELEVANCE_THRESHOLD]

        system_prompt = self._build_system_prompt(relevant_memories)

        return BuiltContext(
            short_term_turns=short_term,
            long_term_memories=relevant_memories,
            system_prompt=system_prompt,
        )

    async def _get_short_term(self, user_id: int) -> list[Turn]:
        return self._buffer.get_turns(user_id)   # sync — deque อ่านเร็ว

    async def _get_long_term(self, user_id: int, query: str) -> list[SearchResult]:
        return await self._memory_service.recall(user_id=user_id, query=query, top_k=5)

    def _build_system_prompt(self, memories: list[SearchResult]) -> str:
        if not memories:
            return "You are a helpful personal assistant."

        memory_text = "\n".join(f"- {m.content}" for m in memories)
        return (
            "You are a helpful personal assistant.\n\n"
            "Here are some things you know about the user:\n"
            f"{memory_text}\n\n"
            "Use this context when relevant."
        )
```

### 2. Token budget enforcement

ประมาณ token จาก character count (1 token ≈ 4 chars สำหรับภาษาไทย/English):

```python
def _estimate_tokens(text: str) -> int:
    return len(text) // 4

def _trim_to_budget(self, turns: list[Turn], budget: int) -> list[Turn]:
    """ตัด turns เก่าออกถ้าเกิน budget — เก็บ turns ล่าสุดไว้ก่อน."""
    used = 0
    result = []
    for turn in reversed(turns):
        tokens = self._estimate_tokens(turn.content)
        if used + tokens > budget:
            break
        result.append(turn)
        used += tokens
    return list(reversed(result))
```

### 3. อัปเดต `chat_handler` ให้ใช้ `ContextBuilder`

```python
# src/bot/handlers/chat.py — แทนที่การ fetch ตรงๆ
@router.message()
async def chat_handler(
    message: Message,
    gemini: GeminiClient,
    user_id: int,
    conversation_buffer: ConversationBuffer,
    context_builder: ContextBuilder,
) -> None:
    if not message.text or message.text.startswith("/"):
        return

    # Build context (concurrent fetch)
    context = await context_builder.build(user_id=user_id, query=message.text)

    # เพิ่ม user message เป็น turn ล่าสุด
    conversation_buffer.add_turn(user_id, "user", message.text)

    # แปลง turns เป็น GeminiMessage
    gemini_messages = [
        GeminiMessage(role=t.role, content=t.content)
        for t in context.short_term_turns
    ]

    reply = await message.reply("...")
    accumulated = ""

    async for chunk in gemini.stream_chat(gemini_messages, system_prompt=context.system_prompt):
        accumulated += chunk
        if len(accumulated) % 20 == 0:
            await reply.edit_text(accumulated)

    if accumulated:
        await reply.edit_text(accumulated)
        conversation_buffer.add_turn(user_id, "model", accumulated)
```

### 4. Register ใน `main.py`

```python
context_builder = ContextBuilder(buffer=conversation_buffer, memory_service=memory_service)
dp["context_builder"] = context_builder
```

---

## Benchmark Test

```python
# tests/core/memory/test_context_builder.py

import time

async def test_concurrent_fetch_faster_than_sequential(mock_buffer, mock_memory_service):
    # mock: short_term ใช้เวลา 30ms, long_term ใช้เวลา 50ms
    # ถ้า concurrent → รวมแล้วประมาณ 50ms
    # ถ้า sequential → รวมแล้วประมาณ 80ms

    builder = ContextBuilder(mock_buffer, mock_memory_service)
    start = time.perf_counter()
    await builder.build(user_id=1, query="test")
    elapsed_ms = (time.perf_counter() - start) * 1000
    
    assert elapsed_ms < 65  # concurrent ต้องเร็วกว่า sequential อย่างน้อย 25ms

async def test_low_relevance_memories_are_filtered():
    # mock: memory_service คืน results ที่ score = 1.5 (ไม่เกี่ยว)
    context = await builder.build(user_id=1, query="test")
    assert len(context.long_term_memories) == 0

async def test_system_prompt_includes_memories():
    # mock: memory_service คืน 2 results ที่ score < threshold
    context = await builder.build(user_id=1, query="test")
    assert "Here are some things you know" in context.system_prompt

async def test_system_prompt_default_when_no_memories():
    # mock: memory_service คืน []
    context = await builder.build(user_id=1, query="test")
    assert context.system_prompt == "You are a helpful personal assistant."

async def test_token_budget_trims_old_turns():
    builder = ContextBuilder(buffer, memory_service)
    turns = [Turn("user", "x" * 1000)] * 20   # 20 turns ที่ยาวมาก
    trimmed = builder._trim_to_budget(turns, budget=MAX_CONTEXT_TOKENS)
    total_chars = sum(len(t.content) for t in trimmed)
    assert total_chars <= MAX_CONTEXT_TOKENS * 4
```

---

## Definition of Done

- [ ] concurrent fetch เร็วกว่า sequential อย่างน้อย 25ms
- [ ] total context ไม่เกิน 8,000 tokens
- [ ] long-term memories ที่ score > 0.7 ถูก filter ออก
- [ ] system prompt มี memories เมื่อมี relevant results
- [ ] benchmark test ผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| ChromaDB ล้มเหลว (network/disk) | catch error, ใช้แค่ short-term ต่อได้ ไม่ block response |
| Buffer ว่าง (user ใหม่) | `short_term_turns = []` → Gemini ตอบโดยไม่มี context |
| Query ว่างเปล่า | ไม่ทำ vector search, ใช้แค่ short-term |
| ทั้ง short-term และ long-term ว่าง | ส่ง system prompt เปล่า + user message อย่างเดียว |
