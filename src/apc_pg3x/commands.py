"""Per-device command queue with confirmation-based removal."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PendingCommand:
    name: str
    value: bool
    created_at: float
    attempts: int = 0
    last_sent_at: float | None = None

    def as_property(self) -> dict:
        return {
            "property": {
                "base_type": "boolean",
                "value": self.value,
                "metadata": None,
                "name": self.name,
            }
        }


class CommandQueue:
    """Keep the latest intent per property until device telemetry confirms it."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingCommand] = {}

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def put(self, name: str, value: bool, *, now: float) -> None:
        if not name:
            raise ValueError("property name is required")
        self._pending[name] = PendingCommand(name=name, value=bool(value), created_at=now)

    def payload(self, *, sequence: int, now: float, max_attempts: int | None = None) -> dict:
        if sequence < 1:
            raise ValueError("sequence must be positive")
        if max_attempts is not None and max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        properties = []
        for command in self._pending.values():
            if max_attempts is not None and command.attempts >= max_attempts:
                continue
            command.attempts += 1
            command.last_sent_at = now
            properties.append(command.as_property())
        return {"seq_no": sequence, "data": {"properties": properties} if properties else {}}

    def confirm(self, name: str, value: bool) -> bool:
        command = self._pending.get(name)
        if command is None or command.value != bool(value):
            return False
        del self._pending[name]
        return True

    def attempts(self, name: str) -> int:
        command = self._pending.get(name)
        return 0 if command is None else command.attempts

    def discard(self, name: str) -> bool:
        return self._pending.pop(name, None) is not None

    def names(self) -> tuple[str, ...]:
        return tuple(self._pending)
