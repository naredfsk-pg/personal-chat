from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Turn:
    role: str  # "user" | "model"
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationBuffer:
    """Per-user short-term buffer. maxlen=10 drops oldest turn automatically."""

    def __init__(self, max_turns: int = 10, idle_timeout_seconds: int = 1800) -> None:
        self._max_turns = max_turns
        self._idle_timeout_seconds = idle_timeout_seconds
        self._buffers: dict[int, deque[Turn]] = {}
        self._last_activity: dict[int, datetime] = {}

    def add_turn(self, user_id: int, role: str, content: str) -> None:
        self._expire_if_idle(user_id)
        if user_id not in self._buffers:
            self._buffers[user_id] = deque(maxlen=self._max_turns)
        self._buffers[user_id].append(Turn(role=role, content=content))
        self._last_activity[user_id] = datetime.now(timezone.utc)

    def get_turns(self, user_id: int) -> list[Turn]:
        self._expire_if_idle(user_id)
        return list(self._buffers.get(user_id, []))

    def clear(self, user_id: int) -> None:
        self._buffers.pop(user_id, None)
        self._last_activity.pop(user_id, None)

    def _expire_if_idle(self, user_id: int) -> None:
        last = self._last_activity.get(user_id)
        if last is None:
            return
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        if elapsed > self._idle_timeout_seconds:
            self.clear(user_id)
