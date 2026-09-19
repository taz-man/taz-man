"""Per-device Ayla LAN state machine and callback routing."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Protocol

from .commands import CommandQueue
from .state import ConfirmedSwitch
from .transport import AylaLanTransport, ProtocolError

_LOGGER = logging.getLogger(__name__)


class RoutingError(RuntimeError):
    """A callback could not be matched to exactly one configured device."""


@dataclass(frozen=True, slots=True)
class DeviceLocation:
    dsn: str
    key_id: str
    address: str


@dataclass(frozen=True, slots=True)
class DeviceConfig:
    dsn: str
    key_id: str
    lan_key: str = field(repr=False)
    address: str
    property_names: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.dsn or not self.key_id or not self.lan_key or not self.address:
            raise ValueError("device identity, key, and address are required")
        if not self.property_names or any(not name for name in self.property_names):
            raise ValueError("at least one named property is required")
        if len(set(self.property_names)) != len(self.property_names):
            raise ValueError("property names must be unique")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    command_timeout: float = 8.0
    max_attempts: int = 3
    backoff_base: float = 0.5
    backoff_cap: float = 2.0
    stale_after: float = 30.0
    keepalive_interval: float = 15.0

    def __post_init__(self) -> None:
        if self.command_timeout <= 0 or self.stale_after <= 0 or self.keepalive_interval <= 0:
            raise ValueError("timeouts must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.backoff_base <= 0 or self.backoff_cap < self.backoff_base:
            raise ValueError("invalid backoff bounds")

    def backoff(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        return min(self.backoff_cap, self.backoff_base * (2 ** (attempt - 1)))


class LocationResolver(Protocol):
    def refresh(self, dsn: str, current_address: str) -> DeviceLocation | None: ...


StateCallback = Callable[[str, bool], None]


class AylaLanDevice:
    """Own one device's routing, retries, and confirmed property state."""

    def __init__(
        self,
        config: DeviceConfig,
        *,
        transport: AylaLanTransport,
        resolver: LocationResolver,
        callback_host: str,
        callback_port: int = 10275,
        policy: RetryPolicy | None = None,
        on_state: StateCallback | None = None,
    ) -> None:
        self.config = config
        self.address = config.address
        self._transport = transport
        self._resolver = resolver
        self._callback_host = callback_host
        self._callback_port = callback_port
        self.policy = policy or RetryPolicy()
        self._on_state = on_state or (lambda _name, _value: None)
        self._lock = RLock()
        self._states = {name: ConfirmedSwitch() for name in config.property_names}
        self._queue = CommandQueue()
        self._sequence = 0
        self._notify_attempts = 0
        self._next_notify_at: float | None = None
        self._keepalive_failures = 0
        self._next_keepalive_at = 0.0
        self.connected = False
        self.stale = True
        self.last_seen_at: float | None = None
        self.last_failure: str | None = None

    @property
    def dsn(self) -> str:
        return self.config.dsn

    @property
    def key_id(self) -> str:
        return self.config.key_id

    @property
    def pending_count(self) -> int:
        with self._lock:
            return self._queue.pending_count

    def state(self, name: str) -> ConfirmedSwitch:
        with self._lock:
            try:
                return replace(self._states[name])
            except KeyError:
                raise ValueError("unknown property") from None

    def command_attempts(self, name: str) -> int:
        with self._lock:
            return self._queue.attempts(name)

    def matches_source(self, source_address: str) -> bool:
        with self._lock:
            return self.address == source_address

    def request(self, name: str, value: bool, *, now: float) -> None:
        with self._lock:
            try:
                state = self._states[name]
            except KeyError:
                raise ValueError("unknown property") from None
            state.request(value, now=now, timeout=self.policy.command_timeout)
            self._queue.put(name, value, now=now)
            self._notify_attempts = 0
            self._next_notify_at = now
            self.last_failure = None

    def poll(self, *, now: float) -> None:
        with self._lock:
            for name, state in self._states.items():
                if state.expire(now=now):
                    self._queue.discard(name)
                    self.stale = True
                    self.last_failure = "confirmation_timeout"
                    _LOGGER.warning("Ayla LAN command confirmation timed out")

            if self.last_seen_at is None or now - self.last_seen_at > self.policy.stale_after:
                self.stale = True

            if self._queue.pending_count == 0:
                self._poll_keepalive(now)
                return
            if self._next_notify_at is None or now < self._next_notify_at:
                return
            if self._notify_attempts >= self.policy.max_attempts:
                self._fail_pending("retry_exhausted")
                return

            self._notify_attempts += 1
            try:
                self._register(notify=True)
            except ProtocolError:
                self.connected = False
                self.stale = True
                self.last_failure = "transport_failure"
                self._refresh_address()
                if self._notify_attempts >= self.policy.max_attempts:
                    self._fail_pending("retry_exhausted")
                    return
            self._next_notify_at = now + self.policy.backoff(self._notify_attempts)

    def fetch_commands(self, *, source_address: str, now: float) -> dict:
        with self._lock:
            self._require_source(source_address)
            self._sequence += 1
            payload = self._queue.payload(
                sequence=self._sequence,
                now=now,
                max_attempts=self.policy.max_attempts,
            )
            if self._queue.pending_count and not payload["data"]:
                self._fail_pending("retry_exhausted")
                return payload
            attempts = max((self._queue.attempts(name) for name in self._queue.names()), default=0)
            if self._queue.pending_count:
                self._next_notify_at = now + self.policy.backoff(max(1, attempts))
            return payload

    def receive_datapoints(
        self,
        values: Mapping[str, object],
        *,
        source_address: str,
        now: float,
    ) -> None:
        with self._lock:
            self._require_source(source_address)
            if not isinstance(values, Mapping) or not values:
                raise ProtocolError("malformed datapoint response")
            parsed: list[tuple[str, bool]] = []
            for name, value in values.items():
                if name not in self._states or type(value) is not bool:
                    raise ProtocolError("malformed datapoint response")
                parsed.append((name, value))

            self._mark_confirmed_telemetry(now)
            for name, value in parsed:
                self._states[name].report(value, now=now)
                self._queue.confirm(name, value)
            if self._queue.pending_count == 0:
                self._next_notify_at = None
                self._notify_attempts = 0
        for name, value in parsed:
            try:
                self._on_state(name, value)
            except Exception:  # noqa: BLE001 - isolate external reporting callbacks
                _LOGGER.warning("Ayla LAN state reporting callback failed")

    def establish_session(self, *, source_address: str, now: float) -> None:
        del now
        with self._lock:
            self._require_source(source_address)

    def _mark_confirmed_telemetry(self, now: float) -> None:
        self.connected = True
        self.stale = False
        self.last_seen_at = now
        self.last_failure = None

    def _require_source(self, source_address: str) -> None:
        if source_address != self.address:
            raise RoutingError("callback source does not match configured device")

    def _refresh_address(self) -> None:
        try:
            location = self._resolver.refresh(self.dsn, self.address)
        except Exception:  # noqa: BLE001 - resolver details must never escape to logs
            _LOGGER.warning("Ayla LAN address refresh failed")
            return
        if (
            location is not None
            and location.dsn == self.dsn
            and location.key_id == self.key_id
            and location.address
        ):
            self.address = location.address

    def _register(self, *, notify: bool) -> None:
        self._transport.register(
            address=self.address,
            callback_host=self._callback_host,
            callback_port=self._callback_port,
            notify=notify,
        )

    def _poll_keepalive(self, now: float) -> None:
        if now < self._next_keepalive_at:
            return
        try:
            self._register(notify=False)
        except ProtocolError:
            self.connected = False
            self.stale = True
            self.last_failure = "transport_failure"
            self._keepalive_failures = min(
                self.policy.max_attempts, self._keepalive_failures + 1
            )
            self._refresh_address()
            self._next_keepalive_at = now + self.policy.backoff(self._keepalive_failures)
            return
        self._keepalive_failures = 0
        self._next_keepalive_at = now + self.policy.keepalive_interval

    def _fail_pending(self, reason: str) -> None:
        for name in self._queue.names():
            self._queue.discard(name)
            state = self._states[name]
            state.desired = None
            state.deadline = None
            state.timed_out = True
        self._next_notify_at = None
        self.stale = True
        self.last_failure = reason
        _LOGGER.warning("Ayla LAN command failed after bounded retries")


class AylaLanRouter:
    """Route callbacks by key ID and current source address without fallback."""

    def __init__(self) -> None:
        self._by_key_id: dict[str, AylaLanDevice] = {}
        self._lock = RLock()

    def add(self, device: AylaLanDevice) -> None:
        with self._lock:
            if device.key_id in self._by_key_id:
                raise ValueError("key ID must identify exactly one device")
            self._by_key_id[device.key_id] = device

    def establish_session(self, *, key_id: str, source_address: str, now: float) -> AylaLanDevice:
        device = self._route(key_id, source_address)
        device.establish_session(source_address=source_address, now=now)
        return device

    def match_device(self, *, key_id: str, source_address: str) -> AylaLanDevice:
        return self._route(key_id, source_address)

    def fetch_commands(self, *, key_id: str, source_address: str, now: float) -> dict:
        device = self._route(key_id, source_address)
        return device.fetch_commands(source_address=source_address, now=now)

    def receive_authenticated_datapoints(
        self,
        *,
        key_id: str,
        source_address: str,
        values: Mapping[str, object],
        now: float,
    ) -> None:
        device = self._route(key_id, source_address)
        device.receive_datapoints(values, source_address=source_address, now=now)

    def _route(self, key_id: str, source_address: str) -> AylaLanDevice:
        with self._lock:
            device = self._by_key_id.get(key_id)
            if device is None or not device.matches_source(source_address):
                raise RoutingError("callback does not match a configured device")
            return device
