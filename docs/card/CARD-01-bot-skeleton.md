# CARD-01 | Bot Skeleton + Webhook/Polling

**Phase:** 1 — Core  
**Priority:** P0  
**Estimated effort:** 1 day  
**Depends on:** —  
**Blocks:** CARD-02, CARD-03, CARD-04

---

## Goal

ตั้ง aiogram 3.x ให้รับ update จาก Telegram ได้ทั้งใน dev (polling) และ prod (webhook) พร้อม project structure ที่รองรับการขยายใน Phase ต่อๆ ไป

---

## Project Structure ที่ต้องสร้าง

```
personal-bot/
├── src/
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── main.py           # entry point
│   │   ├── config.py         # load env vars
│   │   └── handlers/
│   │       └── __init__.py
│   ├── core/                 # domain logic (Phase 2+)
│   └── infrastructure/       # DB, external clients (Phase 2+)
├── tests/
│   └── bot/
├── docs/
├── pyproject.toml
├── .env.example
├── Dockerfile
└── README.md
```

---

## Tasks

### 1. ตั้ง pyproject.toml
- ใช้ `pyproject.toml` เป็น single source of truth สำหรับ dependencies
- dependencies หลัก:
  ```
  aiogram>=3.0
  python-dotenv
  aiohttp        # สำหรับ webhook server
  structlog      # เตรียมไว้ตั้งแต่ต้น (ใช้จริงใน CARD-16)
  ```
- dev dependencies: `pytest`, `pytest-asyncio`, `ruff`, `mypy`

### 2. เขียน `config.py`
โหลด env vars ทั้งหมดจาก `.env` และ validate ที่ startup:

```python
# src/bot/config.py
from dataclasses import dataclass
from dotenv import load_dotenv
import os

@dataclass(frozen=True)
class Config:
    bot_token: str
    webhook_url: str | None     # None = ใช้ polling mode
    webhook_port: int
    allowed_user_ids: frozenset[int]

def load_config() -> Config:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is required")
    
    raw_ids = os.getenv("ALLOWED_USER_IDS", "")
    allowed = frozenset(int(uid.strip()) for uid in raw_ids.split(",") if uid.strip())
    
    return Config(
        bot_token=token,
        webhook_url=os.getenv("WEBHOOK_URL"),        # ถ้าไม่มี = polling
        webhook_port=int(os.getenv("WEBHOOK_PORT", "8080")),
        allowed_user_ids=allowed,
    )
```

### 3. เขียน `main.py`
- init `Bot` + `Dispatcher`
- ตรวจสอบว่ามี `WEBHOOK_URL` หรือเปล่า:
  - มี → start webhook server ด้วย `aiohttp`
  - ไม่มี → start polling (dev mode)
- register router จาก `handlers/`
- graceful shutdown เมื่อได้รับ SIGINT/SIGTERM

```python
# src/bot/main.py
async def main() -> None:
    config = load_config()
    bot = Bot(token=config.bot_token)
    dp = Dispatcher()
    dp.include_router(base_router)

    if config.webhook_url:
        await start_webhook(bot, dp, config)
    else:
        await dp.start_polling(bot)
```

### 4. เขียน base handler (`/start`, `/help`)
- `/start` → ตอบ "สวัสดี! พิมพ์อะไรก็ได้เลย"
- `/help` → แสดงรายการ commands ที่ใช้ได้
- handler ทั้งสองต้องผ่าน auth middleware (CARD-02) ก่อน

### 5. Health check endpoint
- `GET /health` ตอบ `{"status": "ok", "uptime_s": <seconds>}` ด้วย HTTP 200
- รันบน port เดียวกับ webhook server
- ถ้าใช้ polling mode ให้รัน health server แยก thread

### 6. `.env.example`
```
BOT_TOKEN=your_telegram_bot_token
ALLOWED_USER_IDS=123456789
WEBHOOK_URL=                     # ปล่อยว่าง = polling mode
WEBHOOK_PORT=8080
LOG_LEVEL=INFO
```

### 7. Dockerfile
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install .
COPY src/ src/
CMD ["python", "-m", "src.bot.main"]
```

---

## Definition of Done

- [ ] `python -m src.bot.main` รันได้โดยไม่ crash
- [ ] พิมพ์ `/start` ใน Telegram → ได้รับ reply
- [ ] `GET /health` คืน `{"status": "ok"}` ใน < 200ms
- [ ] `ruff check src/` และ `mypy src/` ผ่านโดยไม่มี error
- [ ] `pytest tests/` ผ่านทุก test

---

## Edge Cases ที่ต้องจัดการ

| Case | การจัดการ |
|------|-----------|
| `BOT_TOKEN` ไม่มีใน env | raise `RuntimeError` ทันที (fail fast) |
| `ALLOWED_USER_IDS` ว่างเปล่า | log warning แต่ยังรันต่อได้ (อาจต้องการ open mode) |
| port ถูกใช้งานอยู่แล้ว | แสดง error ชัดเจนว่า port ใดที่ conflict |
| Telegram API ไม่ตอบตอน startup | retry 3 ครั้งแล้ว exit พร้อม error message |
