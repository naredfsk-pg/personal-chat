import pytest
from src.bot.config import load_config


def test_missing_bot_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        load_config()


def test_config_loads_correctly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "test_token_123")
    monkeypatch.setenv("ALLOWED_USER_IDS", "111,222")
    monkeypatch.setenv("WEBHOOK_URL", "")
    monkeypatch.setenv("WEBHOOK_PORT", "9090")

    config = load_config()

    assert config.bot_token == "test_token_123"
    assert config.allowed_user_ids == frozenset({111, 222})
    assert config.webhook_url is None  # empty string → None
    assert config.webhook_port == 9090


def test_empty_allowed_ids_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "test_token")
    monkeypatch.delenv("ALLOWED_USER_IDS", raising=False)

    config = load_config()
    assert config.allowed_user_ids == frozenset()


def test_polling_mode_when_no_webhook_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "test_token")
    monkeypatch.delenv("WEBHOOK_URL", raising=False)

    config = load_config()
    assert config.webhook_url is None


def test_webhook_mode_when_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "test_token")
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com")

    config = load_config()
    assert config.webhook_url == "https://example.com"


def test_default_webhook_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "test_token")
    monkeypatch.delenv("WEBHOOK_PORT", raising=False)

    config = load_config()
    assert config.webhook_port == 8080


def test_allowed_user_ids_with_spaces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "test_token")
    monkeypatch.setenv("ALLOWED_USER_IDS", " 111 , 222 , 333 ")

    config = load_config()
    assert config.allowed_user_ids == frozenset({111, 222, 333})
