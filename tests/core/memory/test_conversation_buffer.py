"""Unit tests for ConversationBuffer (CARD-05).

All tests are synchronous — no asyncio needed.
"""
import time

from src.core.memory.conversation_buffer import ConversationBuffer, Turn


# ---------------------------------------------------------------------------
# Spec-mandated tests (exact names from CARD-05)
# ---------------------------------------------------------------------------


def test_buffer_stores_turns() -> None:
    """add_turn persists turns that are retrievable via get_turns."""
    buf = ConversationBuffer()
    buf.add_turn(1, "user", "hello")
    buf.add_turn(1, "model", "hi there")
    turns = buf.get_turns(1)
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[0].content == "hello"
    assert turns[1].role == "model"
    assert turns[1].content == "hi there"


def test_buffer_drops_oldest_at_11th_turn() -> None:
    """When an 11th turn is added (maxlen=10), the 1st turn is dropped."""
    buf = ConversationBuffer(max_turns=10)
    for i in range(10):
        buf.add_turn(1, "user", f"msg {i}")
    # 10 turns stored — all present
    assert len(buf.get_turns(1)) == 10
    assert buf.get_turns(1)[0].content == "msg 0"

    # 11th turn pushes out the oldest
    buf.add_turn(1, "user", "msg 10")
    turns = buf.get_turns(1)
    assert len(turns) == 10
    assert turns[0].content == "msg 1"   # "msg 0" dropped
    assert turns[-1].content == "msg 10"


def test_buffer_isolated_per_user() -> None:
    """Turns added for user A must not appear for user B."""
    buf = ConversationBuffer()
    buf.add_turn(1, "user", "user 1 message")
    buf.add_turn(2, "user", "user 2 message")
    assert len(buf.get_turns(1)) == 1
    assert buf.get_turns(1)[0].content == "user 1 message"
    assert len(buf.get_turns(2)) == 1
    assert buf.get_turns(2)[0].content == "user 2 message"


def test_clear_removes_user_buffer() -> None:
    """clear() removes all turns for the given user."""
    buf = ConversationBuffer()
    buf.add_turn(1, "user", "hello")
    buf.add_turn(1, "model", "hi")
    buf.clear(1)
    assert buf.get_turns(1) == []


def test_idle_timeout_clears_buffer() -> None:
    """With idle_timeout_seconds=0, get_turns returns [] because elapsed > 0."""
    buf = ConversationBuffer(idle_timeout_seconds=0)
    buf.add_turn(1, "user", "hello")
    # Any call to get_turns after add_turn will have elapsed > 0 seconds,
    # so the buffer is expired on access.
    turns = buf.get_turns(1)
    assert turns == []


def test_get_turns_returns_empty_for_unknown_user() -> None:
    """get_turns on a user_id with no history returns an empty list, not an error."""
    buf = ConversationBuffer()
    assert buf.get_turns(9999) == []


# ---------------------------------------------------------------------------
# Adversarial tests
# ---------------------------------------------------------------------------


def test_add_turn_after_clear_works() -> None:
    """After clear(), add_turn must succeed without KeyError."""
    buf = ConversationBuffer()
    buf.add_turn(1, "user", "first")
    buf.clear(1)
    # Must not raise
    buf.add_turn(1, "user", "second")
    turns = buf.get_turns(1)
    assert len(turns) == 1
    assert turns[0].content == "second"


def test_idle_timeout_does_not_affect_fresh_entry() -> None:
    """With a very long timeout, a freshly added turn is still present."""
    buf = ConversationBuffer(idle_timeout_seconds=9999)
    buf.add_turn(1, "user", "hello")
    turns = buf.get_turns(1)
    assert len(turns) == 1
    assert turns[0].content == "hello"


def test_clear_idempotent() -> None:
    """Calling clear() twice on the same user_id must not raise."""
    buf = ConversationBuffer()
    buf.add_turn(1, "user", "hello")
    buf.clear(1)
    buf.clear(1)  # second clear on an already-empty key — must not raise


def test_get_turns_does_not_mutate_buffer() -> None:
    """get_turns returns a copy; mutating the returned list does not affect the buffer."""
    buf = ConversationBuffer()
    buf.add_turn(1, "user", "hello")
    first_call = buf.get_turns(1)
    # Mutate the returned list
    first_call.clear()
    second_call = buf.get_turns(1)
    assert len(second_call) == 1, "Buffer was mutated by the caller — get_turns must return a copy"


def test_add_turn_updates_last_activity() -> None:
    """After add_turn, the turn is immediately retrievable (last_activity is refreshed)."""
    buf = ConversationBuffer(idle_timeout_seconds=1800)
    buf.add_turn(1, "user", "ping")
    # get_turns must not expire the entry immediately after add_turn
    assert len(buf.get_turns(1)) == 1


def test_turn_has_correct_role_and_content() -> None:
    """Turn dataclass fields (role, content, timestamp) are set correctly."""
    buf = ConversationBuffer()
    buf.add_turn(42, "model", "response text")
    turns = buf.get_turns(42)
    assert len(turns) == 1
    t = turns[0]
    assert isinstance(t, Turn)
    assert t.role == "model"
    assert t.content == "response text"
    # timestamp must be a datetime with timezone info (UTC)
    from datetime import datetime, timezone
    assert isinstance(t.timestamp, datetime)
    assert t.timestamp.tzinfo == timezone.utc


def test_clear_does_not_affect_other_users() -> None:
    """Clearing user 1 must leave user 2's buffer intact."""
    buf = ConversationBuffer()
    buf.add_turn(1, "user", "from user 1")
    buf.add_turn(2, "user", "from user 2")
    buf.clear(1)
    assert buf.get_turns(1) == []
    turns2 = buf.get_turns(2)
    assert len(turns2) == 1
    assert turns2[0].content == "from user 2"


def test_buffer_max_turns_respected_for_multiple_users() -> None:
    """max_turns is enforced independently per user, not as a global count."""
    buf = ConversationBuffer(max_turns=3)
    for i in range(5):
        buf.add_turn(1, "user", f"u1 msg {i}")
        buf.add_turn(2, "user", f"u2 msg {i}")
    assert len(buf.get_turns(1)) == 3
    assert len(buf.get_turns(2)) == 3
    assert buf.get_turns(1)[-1].content == "u1 msg 4"
    assert buf.get_turns(2)[-1].content == "u2 msg 4"
