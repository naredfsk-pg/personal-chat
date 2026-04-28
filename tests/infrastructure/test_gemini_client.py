from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.gemini.client import GeminiClient, Message, ResourceExhausted, InvalidArgument


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _async_chunks(*texts: str):
    """Async generator yielding mock chunks with a .text attribute."""
    for text in texts:
        yield MagicMock(text=text)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_genai():
    """Patch google.generativeai for the duration of a test."""
    with patch("src.infrastructure.gemini.client.genai") as mock:
        yield mock


@pytest.fixture
def client_and_chat(mock_genai):
    """GeminiClient wired to a controllable mock chat object."""
    mock_chat = MagicMock()
    mock_genai.GenerativeModel.return_value = MagicMock(
        start_chat=MagicMock(return_value=mock_chat)
    )
    client = GeminiClient(api_key="test-key")
    return client, mock_chat


# ---------------------------------------------------------------------------
# stream_chat — happy path
# ---------------------------------------------------------------------------


async def test_stream_chat_yields_chunks(client_and_chat) -> None:
    client, mock_chat = client_and_chat
    mock_chat.send_message_async = AsyncMock(
        return_value=_async_chunks("Hello", " world", "!")
    )

    chunks = []
    async for chunk in client.stream_chat([Message(role="user", content="hi")]):
        chunks.append(chunk)

    assert chunks == ["Hello", " world", "!"]


async def test_stream_chat_skips_empty_chunks(client_and_chat) -> None:
    client, mock_chat = client_and_chat
    mock_chat.send_message_async = AsyncMock(
        return_value=_async_chunks("", "hello", "", "world", "")
    )

    chunks = []
    async for chunk in client.stream_chat([Message(role="user", content="hi")]):
        chunks.append(chunk)

    assert chunks == ["hello", "world"]


async def test_stream_chat_empty_response_yields_nothing(client_and_chat) -> None:
    client, mock_chat = client_and_chat
    mock_chat.send_message_async = AsyncMock(return_value=_async_chunks())

    chunks = [c async for c in client.stream_chat([Message(role="user", content="hi")])]
    assert chunks == []


# ---------------------------------------------------------------------------
# stream_chat — retry behaviour
# ---------------------------------------------------------------------------


async def test_retry_on_transient_error(client_and_chat) -> None:
    client, mock_chat = client_and_chat
    mock_chat.send_message_async = AsyncMock(
        side_effect=[
            Exception("network blip"),
            Exception("network blip"),
            _async_chunks("Success"),
        ]
    )

    chunks = []
    with patch("asyncio.sleep", new_callable=AsyncMock):
        async for chunk in client.stream_chat([Message(role="user", content="hi")]):
            chunks.append(chunk)

    assert chunks == ["Success"]
    assert mock_chat.send_message_async.call_count == 3


async def test_exhausted_retries_yield_error_message(client_and_chat) -> None:
    client, mock_chat = client_and_chat
    mock_chat.send_message_async = AsyncMock(side_effect=Exception("always fails"))

    chunks = []
    with patch("asyncio.sleep", new_callable=AsyncMock):
        async for chunk in client.stream_chat([Message(role="user", content="hi")]):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert "⚠️" in chunks[0]
    assert mock_chat.send_message_async.call_count == 5  # max_attempts


# ---------------------------------------------------------------------------
# stream_chat — error cases (no retry)
# ---------------------------------------------------------------------------


async def test_quota_exceeded_returns_user_message(client_and_chat) -> None:
    client, mock_chat = client_and_chat
    mock_chat.send_message_async = AsyncMock(side_effect=ResourceExhausted("quota"))

    chunks = [c async for c in client.stream_chat([Message(role="user", content="hi")])]

    assert len(chunks) == 1
    assert "quota" in chunks[0].lower()
    # ResourceExhausted must NOT trigger retry
    assert mock_chat.send_message_async.call_count == 1


async def test_invalid_argument_returns_user_message(client_and_chat) -> None:
    client, mock_chat = client_and_chat
    mock_chat.send_message_async = AsyncMock(side_effect=InvalidArgument("bad input"))

    chunks = [c async for c in client.stream_chat([Message(role="user", content="hi")])]

    assert len(chunks) == 1
    assert "⚠️" in chunks[0]
    assert mock_chat.send_message_async.call_count == 1


# ---------------------------------------------------------------------------
# stream_chat — multimodal
# ---------------------------------------------------------------------------


async def test_multimodal_message_includes_image(client_and_chat) -> None:
    client, mock_chat = client_and_chat
    mock_chat.send_message_async = AsyncMock(return_value=_async_chunks("a cat"))

    image_bytes = b"\xff\xd8\xff"  # minimal JPEG magic bytes
    messages = [Message(role="user", content="what is this?", image_bytes=image_bytes)]

    async for _ in client.stream_chat(messages):
        pass

    call_args = mock_chat.send_message_async.call_args
    parts = call_args[0][0]
    assert any(
        isinstance(p, dict) and p.get("data") == image_bytes for p in parts
    )


async def test_text_only_message_has_no_image_part(client_and_chat) -> None:
    client, mock_chat = client_and_chat
    mock_chat.send_message_async = AsyncMock(return_value=_async_chunks("answer"))

    async for _ in client.stream_chat([Message(role="user", content="plain text")]):
        pass

    call_args = mock_chat.send_message_async.call_args
    parts = call_args[0][0]
    assert not any(isinstance(p, dict) for p in parts)


# ---------------------------------------------------------------------------
# _to_gemini_history
# ---------------------------------------------------------------------------


def test_history_excludes_last_message(mock_genai) -> None:
    client = GeminiClient(api_key="test-key")
    messages = [
        Message(role="user", content="first"),
        Message(role="model", content="second"),
        Message(role="user", content="third"),
    ]

    history = client._to_gemini_history(messages)

    assert len(history) == 2
    assert history[0]["parts"][0] == "first"
    assert history[1]["parts"][0] == "second"
    assert not any("third" in str(h) for h in history)


def test_history_includes_image_in_parts(mock_genai) -> None:
    client = GeminiClient(api_key="test-key")
    img = b"jpeg-data"
    messages = [
        Message(role="user", content="look at this", image_bytes=img),
        Message(role="user", content="and this?"),
    ]

    history = client._to_gemini_history(messages)

    assert len(history) == 1
    parts = history[0]["parts"]
    assert parts[0] == "look at this"
    assert parts[1] == {"mime_type": "image/jpeg", "data": img}


def test_single_message_produces_empty_history(mock_genai) -> None:
    client = GeminiClient(api_key="test-key")
    history = client._to_gemini_history([Message(role="user", content="only")])
    assert history == []


# ---------------------------------------------------------------------------
# embed_text
# ---------------------------------------------------------------------------


async def test_embed_text_returns_vector(mock_genai) -> None:
    mock_genai.embed_content_async = AsyncMock(
        return_value={"embedding": [0.1, 0.2, 0.3]}
    )
    client = GeminiClient(api_key="test-key")

    result = await client.embed_text("hello world")

    assert result == [0.1, 0.2, 0.3]
    assert all(isinstance(v, float) for v in result)


async def test_embed_text_calls_correct_model(mock_genai) -> None:
    mock_genai.embed_content_async = AsyncMock(
        return_value={"embedding": [0.0] * 768}
    )
    client = GeminiClient(api_key="test-key")

    await client.embed_text("test")

    call_kwargs = mock_genai.embed_content_async.call_args[1]
    assert call_kwargs["model"] == "models/text-embedding-004"
    assert call_kwargs["task_type"] == "retrieval_document"
