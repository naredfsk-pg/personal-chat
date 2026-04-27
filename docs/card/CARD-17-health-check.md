# CARD-17 | Health Check Endpoint

**Phase:** 4 — Reliability  
**Priority:** P2  
**Estimated effort:** 0.5 day  
**Depends on:** CARD-01, CARD-06, CARD-07  
**Blocks:** CARD-19 (CI/CD ใช้ health check ยืนยัน deploy)

---

## Goal

`GET /health` ตรวจสอบสถานะของทุก dependency แล้วคืน JSON ที่บอกว่าระบบพร้อมใช้งานหรือไม่ ตอบใน < 500ms

---

## Tasks

### 1. เขียน health check functions

```python
# src/bot/health.py
import time
import asyncio
from datetime import datetime, UTC
from aiohttp import web
import structlog

log = structlog.get_logger()

_start_time = time.monotonic()


async def check_sqlite(db) -> dict:
    """ตรวจสอบ SQLite ด้วย simple query."""
    try:
        start = time.perf_counter()
        await db._conn.execute_fetchall("SELECT 1")
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception as e:
        log.error("health_sqlite_failed", error=str(e))
        return {"status": "error", "error": str(e)}


async def check_chromadb(vector_store) -> dict:
    """ตรวจสอบ ChromaDB ด้วย count()."""
    try:
        start = time.perf_counter()
        count = vector_store.count()
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {"status": "ok", "document_count": count, "latency_ms": latency_ms}
    except Exception as e:
        log.error("health_chroma_failed", error=str(e))
        return {"status": "error", "error": str(e)}


async def check_gemini(gemini_client) -> dict:
    """ตรวจสอบ Gemini API reachability ด้วย lightweight embed call."""
    try:
        start = time.perf_counter()
        await gemini_client.embed_text("health check")
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception as e:
        log.error("health_gemini_failed", error=str(e))
        return {"status": "error", "error": str(e)}
```

### 2. เขียน health handler

```python
async def health_handler(request: web.Request) -> web.Response:
    db = request.app["db"]
    vector_store = request.app["vector_store"]
    gemini = request.app["gemini"]

    start = time.perf_counter()

    # รัน checks ทั้ง 3 พร้อมกัน (concurrent) พร้อม timeout
    checks = await asyncio.gather(
        asyncio.wait_for(check_sqlite(db), timeout=2.0),
        asyncio.wait_for(check_chromadb(vector_store), timeout=2.0),
        asyncio.wait_for(check_gemini(gemini), timeout=2.0),
        return_exceptions=True,
    )

    results = {
        "db": checks[0] if not isinstance(checks[0], Exception) else {"status": "error", "error": str(checks[0])},
        "vector": checks[1] if not isinstance(checks[1], Exception) else {"status": "error", "error": str(checks[1])},
        "gemini": checks[2] if not isinstance(checks[2], Exception) else {"status": "error", "error": str(checks[2])},
    }

    all_ok = all(r.get("status") == "ok" for r in results.values())
    total_ms = round((time.perf_counter() - start) * 1000, 2)

    response_body = {
        "status": "ok" if all_ok else "degraded",
        "checks": results,
        "uptime_s": round(time.monotonic() - _start_time),
        "timestamp": datetime.now(UTC).isoformat(),
        "total_latency_ms": total_ms,
    }

    status_code = 200 if all_ok else 503
    return web.json_response(response_body, status=status_code)
```

### 3. Register endpoint

```python
# src/bot/main.py
app = web.Application()
app["db"] = db
app["vector_store"] = vector_store
app["gemini"] = gemini

app.router.add_get("/health", health_handler)
app.router.add_get("/", lambda r: web.Response(text="OK"))  # ping endpoint
```

### 4. ตัวอย่าง response

**ปกติ (200 OK):**
```json
{
  "status": "ok",
  "checks": {
    "db": {"status": "ok", "latency_ms": 1.2},
    "vector": {"status": "ok", "document_count": 42, "latency_ms": 3.5},
    "gemini": {"status": "ok", "latency_ms": 312.7}
  },
  "uptime_s": 86400,
  "timestamp": "2024-01-15T09:30:00.000Z",
  "total_latency_ms": 315.4
}
```

**มี dependency ล้มเหลว (503 Service Unavailable):**
```json
{
  "status": "degraded",
  "checks": {
    "db": {"status": "ok", "latency_ms": 1.2},
    "vector": {"status": "error", "error": "Connection refused"},
    "gemini": {"status": "ok", "latency_ms": 298.1}
  },
  "uptime_s": 3600,
  "timestamp": "2024-01-15T09:30:00.000Z",
  "total_latency_ms": 2001.0
}
```

---

## Integration Tests

```python
# tests/bot/test_health.py

async def test_health_returns_200_when_all_ok(aiohttp_client, mock_db, mock_vector, mock_gemini):
    app = create_test_app(db=mock_db, vector_store=mock_vector, gemini=mock_gemini)
    client = await aiohttp_client(app)
    
    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    assert all(c["status"] == "ok" for c in data["checks"].values())

async def test_health_returns_503_when_db_fails(aiohttp_client, mock_db_failing, mock_vector, mock_gemini):
    app = create_test_app(db=mock_db_failing, ...)
    client = await aiohttp_client(app)
    
    resp = await client.get("/health")
    assert resp.status == 503
    data = await resp.json()
    assert data["status"] == "degraded"
    assert data["checks"]["db"]["status"] == "error"

async def test_health_responds_within_500ms(aiohttp_client, ...):
    import time
    client = await aiohttp_client(create_test_app(...))
    
    start = time.perf_counter()
    await client.get("/health")
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 500

async def test_health_includes_uptime(aiohttp_client, ...):
    client = await aiohttp_client(create_test_app(...))
    resp = await client.get("/health")
    data = await resp.json()
    assert "uptime_s" in data
    assert isinstance(data["uptime_s"], int)

async def test_check_timeout_handled_gracefully(aiohttp_client, mock_slow_gemini):
    # mock_slow_gemini: sleep 3 วินาที (เกิน timeout 2s)
    client = await aiohttp_client(create_test_app(gemini=mock_slow_gemini, ...))
    resp = await client.get("/health")
    assert resp.status == 503
    data = await resp.json()
    assert data["checks"]["gemini"]["status"] == "error"
```

---

## Definition of Done

- [ ] `GET /health` ตอบ 200 เมื่อทุก dependency ปกติ
- [ ] dependency ใด fail → ตอบ 503 พร้อมระบุว่า check ไหนล้มเหลว
- [ ] response time < 500ms (checks รันพร้อมกัน + timeout 2s)
- [ ] `uptime_s` บอก uptime จริงของ process
- [ ] check timeout (>2s) → mark เป็น error ไม่แขวน
- [ ] integration tests ทุกกรณีผ่าน

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| Gemini check ใช้ quota | embed "health check" นับ 1 req — ต้องนับใน quota counter ด้วย |
| ChromaDB ตอนเพิ่ง start (กำลัง load) | อาจ timeout → 503 แต่ bot ทำงานได้ปกติ |
| health endpoint ถูกเรียกบ่อยจาก load balancer | embed call ทุก health check แพงไป — พิจารณาใช้ lightweight ping แทน (check connection แทน API call) |
