# CARD-07 | SQLite Schema

**Phase:** 2 — Memory  
**Priority:** P0  
**Estimated effort:** 1 day  
**Depends on:** CARD-01  
**Blocks:** CARD-08, CARD-09, CARD-10, CARD-11, CARD-13, CARD-15

---

## Goal

ออกแบบและ implement SQLite schema สำหรับ notes, reminders, user preferences และ API usage tracking พร้อม Repository pattern ที่ทดสอบได้

---

## Tasks

### 1. Schema Design

```sql
-- migrations/001_initial_schema.sql

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS notes (
    id          TEXT PRIMARY KEY,           -- UUID
    user_id     INTEGER NOT NULL,
    content     TEXT NOT NULL,
    embedding_id TEXT,                      -- ChromaDB doc_id (FK by convention)
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notes_user_id ON notes(user_id);
CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at DESC);

CREATE TABLE IF NOT EXISTS reminders (
    id          TEXT PRIMARY KEY,           -- UUID
    user_id     INTEGER NOT NULL,
    message     TEXT NOT NULL,
    trigger_at  TEXT NOT NULL,              -- ISO 8601 UTC
    is_sent     INTEGER NOT NULL DEFAULT 0, -- 0=pending, 1=sent
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_reminders_trigger ON reminders(trigger_at) WHERE is_sent = 0;

CREATE TABLE IF NOT EXISTS user_prefs (
    user_id     INTEGER NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS api_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    date        TEXT NOT NULL,              -- YYYY-MM-DD UTC
    request_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, date)
);
```

### 2. Database connection manager

```python
# src/infrastructure/db/connection.py
import aiosqlite
from contextlib import asynccontextmanager
from typing import AsyncIterator

class Database:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._conn.execute("BEGIN"):
            try:
                yield self._conn
                await self._conn.execute("COMMIT")
            except Exception:
                await self._conn.execute("ROLLBACK")
                raise
```

### 3. Migration runner

```python
# src/infrastructure/db/migrations.py
import os
import aiosqlite

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "../../../migrations")

async def run_migrations(db: aiosqlite.Connection) -> None:
    await db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (filename TEXT PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')))"
    )
    applied = {row[0] for row in await db.execute_fetchall("SELECT filename FROM schema_migrations")}
    
    for filename in sorted(os.listdir(MIGRATIONS_DIR)):
        if filename.endswith(".sql") and filename not in applied:
            with open(os.path.join(MIGRATIONS_DIR, filename)) as f:
                await db.executescript(f.read())
            await db.execute("INSERT INTO schema_migrations(filename) VALUES (?)", (filename,))
            await db.commit()
```

### 4. `NoteRepository`

```python
# src/infrastructure/db/repositories/note_repository.py
import uuid
from dataclasses import dataclass
from datetime import datetime, UTC

@dataclass(frozen=True)
class Note:
    id: str
    user_id: int
    content: str
    embedding_id: str | None
    created_at: datetime

class NoteRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, user_id: int, content: str, embedding_id: str | None = None) -> Note:
        note_id = str(uuid.uuid4())
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT INTO notes(id, user_id, content, embedding_id) VALUES (?,?,?,?)",
                (note_id, user_id, content, embedding_id),
            )
        return Note(id=note_id, user_id=user_id, content=content, embedding_id=embedding_id, created_at=datetime.now(UTC))

    async def list_recent(self, user_id: int, limit: int = 10) -> list[Note]:
        rows = await self._db._conn.execute_fetchall(
            "SELECT id, user_id, content, embedding_id, created_at FROM notes WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [Note(id=r["id"], user_id=r["user_id"], content=r["content"], embedding_id=r["embedding_id"], created_at=datetime.fromisoformat(r["created_at"])) for r in rows]

    async def delete(self, note_id: str, user_id: int) -> bool:
        """คืน True ถ้าลบสำเร็จ, False ถ้าไม่พบหรือ user ไม่ตรง."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM notes WHERE id=? AND user_id=?", (note_id, user_id)
            )
        return cursor.rowcount > 0

    async def list_in_range(self, user_id: int, since: datetime, until: datetime) -> list[Note]:
        rows = await self._db._conn.execute_fetchall(
            "SELECT * FROM notes WHERE user_id=? AND created_at BETWEEN ? AND ? ORDER BY created_at DESC",
            (user_id, since.isoformat(), until.isoformat()),
        )
        return [Note(**dict(r)) for r in rows]
```

### 5. `ReminderRepository`

```python
# src/infrastructure/db/repositories/reminder_repository.py
@dataclass(frozen=True)
class Reminder:
    id: str
    user_id: int
    message: str
    trigger_at: datetime
    is_sent: bool

class ReminderRepository:
    async def create(self, user_id: int, message: str, trigger_at: datetime) -> Reminder: ...
    async def get_pending(self, until: datetime) -> list[Reminder]: ...
    async def mark_sent(self, reminder_id: str) -> None: ...
    async def delete(self, reminder_id: str, user_id: int) -> bool: ...
    async def list_upcoming(self, user_id: int, limit: int = 10) -> list[Reminder]: ...
```

### 6. `ApiUsageRepository`

```python
# src/infrastructure/db/repositories/api_usage_repository.py
class ApiUsageRepository:
    async def increment(self, user_id: int) -> int:
        """เพิ่ม count และคืน count ปัจจุบันของวันนี้."""

    async def get_today_count(self, user_id: int) -> int:
        """คืนจำนวน request วันนี้."""

    async def reset_if_new_day(self, user_id: int) -> None:
        """ถ้าวันเปลี่ยน reset count — เรียกโดย CARD-15."""
```

---

## Unit Tests

```python
# tests/infrastructure/db/test_note_repository.py

async def test_create_note_returns_note_with_id(db):
    repo = NoteRepository(db)
    note = await repo.create(user_id=1, content="test note")
    assert note.id is not None
    assert note.content == "test note"

async def test_list_recent_returns_latest_first(db):
    repo = NoteRepository(db)
    await repo.create(1, "first")
    await repo.create(1, "second")
    notes = await repo.list_recent(1, limit=10)
    assert notes[0].content == "second"

async def test_list_recent_limits_results(db):
    repo = NoteRepository(db)
    for i in range(15):
        await repo.create(1, f"note {i}")
    notes = await repo.list_recent(1, limit=10)
    assert len(notes) == 10

async def test_delete_removes_correct_note(db):
    repo = NoteRepository(db)
    note = await repo.create(1, "to delete")
    result = await repo.delete(note.id, user_id=1)
    assert result is True
    assert await repo.list_recent(1) == []

async def test_delete_returns_false_for_wrong_user(db):
    repo = NoteRepository(db)
    note = await repo.create(user_id=1, content="private")
    result = await repo.delete(note.id, user_id=2)  # user 2 ลบของ user 1
    assert result is False

async def test_wal_mode_enabled(db):
    rows = await db._conn.execute_fetchall("PRAGMA journal_mode")
    assert rows[0][0] == "wal"
```

---

## Definition of Done

- [ ] WAL mode เปิดอยู่เสมอ
- [ ] migrations รันอัตโนมัติตอน startup
- [ ] CRUD ทุก table ทำงานได้ถูกต้อง
- [ ] `delete` ตรวจสอบ `user_id` — ไม่ให้ลบของคนอื่น
- [ ] unit tests ทุกกรณีผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| ลบ note ที่ไม่มีอยู่ | คืน `False` ไม่ raise |
| migration รันซ้ำ | idempotent — ข้ามไปถ้า applied แล้ว |
| DB file ไม่มีอยู่ | `aiosqlite.connect()` สร้างไฟล์ใหม่ให้ |
| Concurrent writes | WAL mode รองรับ concurrent reads + 1 writer |
| reminder trigger_at ในอดีต | สร้างได้แต่จะถูก trigger ทันทีในรอบถัดไป |
