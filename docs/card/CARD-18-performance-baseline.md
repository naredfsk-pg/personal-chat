# CARD-18 | Performance Baseline

**Phase:** Cross-cutting  
**Priority:** P1  
**Estimated effort:** 0.5 day  
**Depends on:** CARD-04, CARD-08 (ต้องมี full flow ก่อนวัด)  
**Blocks:** —

---

## Goal

วัดและยืนยันว่าระบบตรงตาม NFR targets ทุกข้อก่อน ship โดยมี reproducible benchmark script

---

## NFR Targets ที่ต้องวัด

| NFR | Target | วิธีวัด |
|-----|--------|--------|
| NFR-01 | P95 latency < 3s (ไม่รวม Gemini gen) | benchmark end-to-end ไม่รวม streaming |
| NFR-03 | RAM < 200MB | `psutil` วัดตอน load |
| NFR-04 | Storage < 100MB/month | simulate 1,000 messages แล้วดู size |
| NFR-06 | Cold start < 5s | วัดเวลาตั้งแต่ process start ถึง ready |

---

## Tasks

### 1. เขียน benchmark script

```python
# scripts/benchmark.py
"""
รัน: python scripts/benchmark.py
ต้องการ: bot รันอยู่และพร้อมรับ request
"""
import asyncio
import time
import statistics
import psutil
import os

async def measure_context_build_latency(n: int = 100) -> dict:
    """วัด P95 latency ของ context_builder.build() โดยไม่รวม Gemini."""
    from src.core.memory.context_builder import ContextBuilder
    # setup minimal dependencies
    ...
    latencies = []
    for _ in range(n):
        start = time.perf_counter()
        await context_builder.build(user_id=1, query="test query")
        latencies.append((time.perf_counter() - start) * 1000)

    latencies.sort()
    return {
        "p50_ms": round(statistics.median(latencies), 2),
        "p95_ms": round(latencies[int(n * 0.95)], 2),
        "p99_ms": round(latencies[int(n * 0.99)], 2),
        "mean_ms": round(statistics.mean(latencies), 2),
    }


def measure_ram_usage() -> dict:
    """วัด RAM usage ของ process ปัจจุบัน."""
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    return {
        "rss_mb": round(mem_info.rss / 1024 / 1024, 2),
        "vms_mb": round(mem_info.vms / 1024 / 1024, 2),
    }


async def measure_storage_growth(n_messages: int = 1000) -> dict:
    """Simulate n_messages แล้ววัด DB + Chroma size."""
    import os
    db_path = "./data/benchmark_test.db"
    chroma_path = "./data/benchmark_chroma"

    # สร้าง n_messages ใน test DB
    # ...

    db_size_mb = os.path.getsize(db_path) / 1024 / 1024
    chroma_size_mb = sum(
        os.path.getsize(os.path.join(chroma_path, f))
        for f in os.listdir(chroma_path)
    ) / 1024 / 1024

    monthly_estimate = (db_size_mb + chroma_size_mb) * (30 * 24)  # extrapolate
    return {
        "db_size_mb": round(db_size_mb, 2),
        "chroma_size_mb": round(chroma_size_mb, 2),
        "estimated_monthly_mb": round(monthly_estimate, 2),
    }


async def measure_cold_start() -> dict:
    """วัดเวลาตั้งแต่ import ถึง bot ready."""
    start = time.perf_counter()
    from src.bot.main import create_app
    app = await create_app()
    elapsed = (time.perf_counter() - start) * 1000
    return {"cold_start_ms": round(elapsed, 2)}


async def run_all() -> None:
    print("=" * 50)
    print("Performance Baseline Report")
    print("=" * 50)

    print("\n[1/4] Context Build Latency (n=100)...")
    latency = await measure_context_build_latency()
    p95_ok = latency["p95_ms"] < 3000
    print(f"  P50: {latency['p50_ms']}ms")
    print(f"  P95: {latency['p95_ms']}ms {'✅' if p95_ok else '❌ FAIL (target: <3000ms)'}")

    print("\n[2/4] RAM Usage...")
    ram = measure_ram_usage()
    ram_ok = ram["rss_mb"] < 200
    print(f"  RSS: {ram['rss_mb']}MB {'✅' if ram_ok else '❌ FAIL (target: <200MB)'}")

    print("\n[3/4] Storage Growth (1000 messages)...")
    storage = await measure_storage_growth()
    storage_ok = storage["estimated_monthly_mb"] < 100
    print(f"  DB: {storage['db_size_mb']}MB")
    print(f"  Chroma: {storage['chroma_size_mb']}MB")
    print(f"  Monthly estimate: {storage['estimated_monthly_mb']}MB {'✅' if storage_ok else '❌ FAIL (target: <100MB)'}")

    print("\n[4/4] Cold Start...")
    cold = await measure_cold_start()
    cold_ok = cold["cold_start_ms"] < 5000
    print(f"  Time: {cold['cold_start_ms']}ms {'✅' if cold_ok else '❌ FAIL (target: <5000ms)'}")

    print("\n" + "=" * 50)
    all_ok = p95_ok and ram_ok and storage_ok and cold_ok
    print(f"Overall: {'✅ ALL PASS' if all_ok else '❌ SOME FAILED'}")

if __name__ == "__main__":
    asyncio.run(run_all())
```

### 2. pytest-benchmark integration

```python
# tests/performance/test_benchmarks.py
import pytest

@pytest.mark.benchmark(group="context_builder")
def test_context_build_p95_latency(benchmark, context_builder):
    result = benchmark.pedantic(
        context_builder.build,
        args=(1, "test query"),
        iterations=10,
        rounds=10,
    )
    stats = benchmark.stats
    p95 = stats["q3"] * 1000  # จาก seconds เป็น ms
    assert p95 < 3000, f"P95 latency {p95:.0f}ms เกิน 3000ms"

def test_ram_under_200mb():
    import psutil, os
    process = psutil.Process(os.getpid())
    rss_mb = process.memory_info().rss / 1024 / 1024
    assert rss_mb < 200, f"RAM {rss_mb:.1f}MB เกิน 200MB"
```

### 3. เพิ่ม NFR gates ใน CI

```yaml
# .github/workflows/nfr.yml
- name: Run NFR benchmarks
  run: python scripts/benchmark.py
  env:
    ENV: test
```

---

## Definition of Done

- [ ] `python scripts/benchmark.py` รันได้และแสดงผล pass/fail แต่ละ NFR
- [ ] P95 latency (ไม่รวม Gemini) < 3,000ms
- [ ] RAM usage < 200MB ตอนรัน bot + handle request
- [ ] estimated monthly storage < 100MB
- [ ] cold start < 5,000ms
- [ ] ถ้า NFR ใดไม่ผ่าน → script exit code != 0 (CI fail)

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| Gemini latency ทำให้ P95 เกิน 3s | ยอมรับได้ตาม spec (NFR-01 ไม่รวม gen time) — แยก measure |
| benchmark รันบน machine ที่ช้ากว่า prod | document hardware spec ใน report |
| ChromaDB cold-load ครั้งแรกช้า | วัดหลัง warm-up 1 query ก่อนเสมอ |
