import pytest


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent tests from loading .env file so env vars are fully controlled."""
    monkeypatch.setattr("src.bot.config.load_dotenv", lambda: None)
