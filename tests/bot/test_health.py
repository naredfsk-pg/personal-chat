import time
import pytest
from aiohttp import web
from src.bot.health import health_handler


@pytest.fixture
def health_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health_handler)
    return app


async def test_health_returns_200(aiohttp_client: pytest.fixture, health_app: web.Application) -> None:
    client = await aiohttp_client(health_app)
    resp = await client.get("/health")
    assert resp.status == 200


async def test_health_response_has_status_ok(aiohttp_client: pytest.fixture, health_app: web.Application) -> None:
    client = await aiohttp_client(health_app)
    resp = await client.get("/health")
    data = await resp.json()
    assert data["status"] == "ok"


async def test_health_response_has_uptime(aiohttp_client: pytest.fixture, health_app: web.Application) -> None:
    client = await aiohttp_client(health_app)
    resp = await client.get("/health")
    data = await resp.json()
    assert "uptime_s" in data
    assert isinstance(data["uptime_s"], int)
    assert data["uptime_s"] >= 0


async def test_health_responds_within_200ms(aiohttp_client: pytest.fixture, health_app: web.Application) -> None:
    client = await aiohttp_client(health_app)
    start = time.perf_counter()
    await client.get("/health")
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 200
