import os
from dataclasses import dataclass

import structlog
from dotenv import load_dotenv

log = structlog.get_logger()


@dataclass(frozen=True)
class Config:
    bot_token: str
    webhook_url: str | None
    webhook_port: int
    allowed_user_ids: frozenset[int]
    gemini_api_key: str
    gemini_model: str


def load_config() -> Config:
    load_dotenv()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is required")

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required")

    raw_ids = os.getenv("ALLOWED_USER_IDS", "")
    allowed = frozenset(int(uid.strip()) for uid in raw_ids.split(",") if uid.strip())

    if not allowed:
        log.warning("no_allowed_users", msg="ALLOWED_USER_IDS is empty — bot will reject all users")

    return Config(
        bot_token=token,
        webhook_url=os.getenv("WEBHOOK_URL") or None,
        webhook_port=int(os.getenv("WEBHOOK_PORT", "8080")),
        allowed_user_ids=allowed,
        gemini_api_key=gemini_api_key,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
    )
