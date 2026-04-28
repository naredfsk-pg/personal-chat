import time

from aiohttp import web

_start_time = time.monotonic()


async def health_handler(request: web.Request) -> web.Response:
    """Basic health check. CARD-17 expands this with DB/Chroma/Gemini checks."""
    return web.json_response({
        "status": "ok",
        "uptime_s": round(time.monotonic() - _start_time),
    })
