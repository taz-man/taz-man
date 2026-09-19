"""Confirmed-state model that never treats intent as telemetry."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ConfirmedSwitch:
    actual: bool | None = None
    desired: bool | None = None
    deadline: float | None = None
    last_confirmed_at: float | None = None
    timed_out: bool = False

    @property
    def pending(self) -> bool:
        return self.desired is not None

    def request(self, desired: bool, *, now: float, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.desired = bool(desired)
        self.deadline = now + timeout
        self.timed_out = False

    def report(self, actual: bool, *, now: float) -> bool:
        self.actual = bool(actual)
        self.last_confirmed_at = now
        confirmed = self.pending and self.actual == self.desired
        if confirmed:
            self.desired = None
            self.deadline = None
            self.timed_out = False
        return confirmed

    def expire(self, *, now: float) -> bool:
        if not self.pending or self.deadline is None or now <= self.deadline:
            return False
        self.desired = None
        self.deadline = None
        self.timed_out = True
        return True
