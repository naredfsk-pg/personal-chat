# PROJECT_CONFIG.md — Tech Stack Reference

> This file is the single source of truth for project technology choices.
> All agents read this at session start. Edit manually or let the Master Agent populate it.

---

## Tech Stack

- **Language**: Python 3.12 (target-version in ruff), requires >=3.10
- **Framework**: aiogram 3.x (Telegram bot, webhook + polling modes), aiohttp (web server for health/webhook)
- **Database**: SQLite via aiosqlite (CARD-07, planned), ChromaDB (CARD-06, planned, in-memory/ephemeral for tests)
- **ORM / Query Builder**: Raw SQL via aiosqlite (no ORM)
- **Styling**: N/A (backend bot, no UI)
- **Testing**: pytest 8+, pytest-asyncio (asyncio_mode=auto), pytest-mock (AsyncMock/MagicMock)
- **Infrastructure / Deployment**: Docker, Google Cloud Run (webhook mode) or local polling
- **Key Libraries**:
  - `google-generativeai>=0.8` — Gemini LLM client (streaming + embeddings)
  - `tenacity>=8.0` — retry logic (on regular coroutines, NOT async generators)
  - `structlog` — structured logging
  - `python-dotenv` — env config loading
  - `ruff` — linter/formatter (line-length=100)
  - `mypy` — static typing (disallow_untyped_defs=true)

---

## Project Structure

```
src/
  bot/
    handlers/      # aiogram Router handlers (base.py, chat.py)
    middlewares/   # AuthMiddleware (outer_middleware)
    config.py      # Config dataclass + load_config()
    main.py        # Dispatcher setup, DI wiring, webhook/polling runner
    health.py      # /health endpoint
    logging_config.py
  core/
    memory/        # ConversationBuffer (CARD-05, in-memory, no DB)
  infrastructure/
    gemini/        # GeminiClient (stream_chat, embed_text)
tests/
  bot/
    handlers/
    test_auth_middleware.py
    test_config.py
    test_health.py
  core/
    memory/
  infrastructure/
```

## Dependency Injection (aiogram pattern)

```python
dp["gemini"] = GeminiClient(...)
dp["conversation_buffer"] = ConversationBuffer(...)
dp.update.outer_middleware(AuthMiddleware(allowed_ids))
```
Handler params injected by name matching dp dict keys.

## Completed Cards

- CARD-01: Bot skeleton (main.py, config, health, base handlers)
- CARD-02: AuthMiddleware — whitelist gating, injects user_id
- CARD-03: GeminiClient — streaming via _get_chat_response (regular coroutine) + tenacity retry
- CARD-04: chat_handler — progressive edit, split_message, _safe_edit

## Stack Status

FILLED — agents use this as ground truth.
