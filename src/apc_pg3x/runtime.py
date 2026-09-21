"""Integration layer joining PG3 node reporting to the Ayla LAN client."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .address import endpoint_address
from .callback_server import AylaCallbackServer
from .config import (
    EXPECTED_ROLES,
    BootstrapConfig,
    BootstrapConfigStore,
    ConfigurationError,
    RejectionReason,
)
from .device import (
    AylaLanDevice,
    AylaLanRouter,
    DeviceConfig,
    DeviceLocation,
    RetryPolicy,
)
from .protocol import AylaCallbackProtocol
from .transport import AylaLanTransport

_LOGGER = logging.getLogger(__name__)
CONTROLLER_ENDPOINT = "controller"
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _valid_callback_host(value: str) -> bool:
    if not value or value != value.strip() or len(value) > 253:
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return all(_HOST_LABEL.fullmatch(label) for label in value.split("."))
    return True


class NodePublisher(Protocol):
    def add_controller(self, address: str, name: str) -> None: ...

    def add_child(self, address: str, primary: str, name: str, role: str) -> None: ...

    def set_controller(self, driver: str, value: int, *, force: bool = False) -> None: ...

    def set_child(self, address: str, driver: str, value: int, *, force: bool = False) -> None: ...


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    callback_host: str
    callback_port: int = 10275
    command_timeout: float = 8.0
    max_attempts: int = 3
    stale_after: float = 30.0
    keepalive_interval: float = 15.0
    request_timeout: float = 3.0

    def __post_init__(self) -> None:
        if not _valid_callback_host(self.callback_host):
            raise ConfigurationError(
                "callback_host is invalid", RejectionReason.CALLBACK_HOST
            )
        if not 1 <= self.callback_port <= 65535:
            raise ConfigurationError(
                "callback_port is out of range", RejectionReason.CALLBACK_PORT
            )


class BootstrapLocationResolver:
    """Refresh a transient address from the protected source by durable identity."""

    def __init__(self, store: BootstrapConfigStore, expected_key_id: str) -> None:
        self._store = store
        self._expected_key_id = expected_key_id

    def refresh(self, dsn: str, current_address: str) -> DeviceLocation | None:
        del current_address
        try:
            config = self._store.load()
        except ConfigurationError:
            return None
        if config.dsn != dsn or config.key_id != self._expected_key_id:
            return None
        return DeviceLocation(dsn=config.dsn, key_id=config.key_id, address=config.address)


class PluginRuntime:
    """Own the fixed node topology and one authenticated Ayla LAN device."""

    def __init__(
        self,
        *,
        store: BootstrapConfigStore,
        settings: RuntimeSettings,
        publisher: NodePublisher,
        transport: AylaLanTransport | None = None,
        clock=time.monotonic,
        start_callback: bool = True,
        restart_state: Mapping[str, object] | None = None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._publisher = publisher
        self._clock = clock
        self._closed = False
        self.config = store.load()
        initial_address = self.config.address
        if restart_state is not None and restart_state.get("dsn") == self.config.dsn:
            saved_address = restart_state.get("last_address")
            if isinstance(saved_address, str) and saved_address.strip():
                initial_address = saved_address.strip()
        self.controller_address = endpoint_address(self.config.dsn, CONTROLLER_ENDPOINT)
        self.child_addresses = {
            binding.role: endpoint_address(self.config.dsn, binding.name)
            for binding in self.config.properties
        }
        self._property_to_role = {
            binding.name: binding.role for binding in self.config.properties
        }
        self._create_topology(self.config)

        policy = RetryPolicy(
            command_timeout=settings.command_timeout,
            max_attempts=settings.max_attempts,
            stale_after=settings.stale_after,
            keepalive_interval=settings.keepalive_interval,
        )
        self.router = AylaLanRouter()
        self.device = AylaLanDevice(
            DeviceConfig(
                dsn=self.config.dsn,
                key_id=self.config.key_id,
                lan_key=self.config.lan_key,
                address=initial_address,
                property_names=self.config.property_names,
            ),
            transport=transport or AylaLanTransport(request_timeout=settings.request_timeout),
            resolver=BootstrapLocationResolver(store, self.config.key_id),
            callback_host=settings.callback_host,
            callback_port=settings.callback_port,
            policy=policy,
            on_state=self._on_state,
        )
        self.router.add(self.device)
        self.protocol = AylaCallbackProtocol(
            self.router,
            random_bytes=os.urandom,
            time_value=lambda: int(time.time() * 1_000_000),
        )
        self.callback_server = AylaCallbackServer(
            self.protocol,
            port=settings.callback_port,
            clock=clock,
        )
        if start_callback:
            self.callback_server.start()
        self._publish_health(force=True)

    def command(self, role: str, value: bool) -> None:
        binding = self.config.binding(role)
        now = self._clock()
        self.device.request(binding.name, value, now=now)
        self.device.poll(now=now)
        self._publisher.set_child(self.child_addresses[role], "GV1", 1, force=True)
        self._publish_health()

    def query(self) -> None:
        self.poll()

    def discover(self) -> None:
        self.poll()

    def poll(self) -> None:
        if self._closed:
            return
        self.device.poll(now=self._clock())
        self._publish_health()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.callback_server.close()
        self._publisher.set_controller("ST", 0, force=True)

    def public_restart_state(self) -> dict[str, object]:
        """Return the minimum non-secret restart/IP-recovery metadata."""
        return {
            "dsn": self.config.dsn,
            "last_address": self.device.address,
            "roles": list(EXPECTED_ROLES),
        }

    def _create_topology(self, config: BootstrapConfig) -> None:
        self._publisher.add_controller(self.controller_address, f"{config.product_name} Controller")
        for binding in config.properties:
            self._publisher.add_child(
                self.child_addresses[binding.role],
                self.controller_address,
                binding.label,
                binding.role,
            )

    def _on_state(self, property_name: str, value: bool) -> None:
        role = self._property_to_role[property_name]
        address = self.child_addresses[role]
        self._publisher.set_child(address, "ST", int(value), force=True)
        self._publisher.set_child(
            address,
            "GV1",
            int(self.device.state(property_name).pending),
            force=True,
        )
        self._publish_health()

    def _publish_health(self, *, force: bool = False) -> None:
        now = self._clock()
        connected = int(self.device.connected)
        stale = int(self.device.stale)
        age = 0 if self.device.last_seen_at is None else max(0, int(now - self.device.last_seen_at))
        self._publisher.set_controller("ST", 1, force=force)
        self._publisher.set_controller("GV0", connected, force=force)
        self._publisher.set_controller("GV1", age, force=force)
        self._publisher.set_controller("GV2", stale, force=force)
        for binding in self.config.properties:
            address = self.child_addresses[binding.role]
            state = self.device.state(binding.name)
            self._publisher.set_child(address, "GV0", connected, force=force)
            self._publisher.set_child(address, "GV1", int(state.pending), force=force)
            self._publisher.set_child(address, "GV2", stale, force=force)
