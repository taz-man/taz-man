import base64
import json
import socket
from pathlib import Path

import pytest

from apc_pg3x.config import EXPECTED_ROLES, BootstrapConfigStore, ConfigurationError
from apc_pg3x.runtime import PluginRuntime, RuntimeSettings
from apc_pg3x.transport import ProtocolError


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


class Transport:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def register(self, **values):
        self.calls.append(values)
        if self.fail:
            raise ProtocolError("sanitized")


class Publisher:
    def __init__(self):
        self.controller = None
        self.children = {}
        self.drivers = {}

    def add_controller(self, address, name):
        self.controller = (address, name)

    def add_child(self, address, primary, name, role):
        self.children[role] = (address, primary, name)

    def set_controller(self, driver, value, *, force=False):
        self.drivers[("controller", driver)] = value

    def set_child(self, address, driver, value, *, force=False):
        self.drivers[(address, driver)] = value


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_bootstrap(path: Path, *, address="192.0.2.10"):
    properties = [
        {
            "role": role,
            "name": f"property_{role}",
            "label": role.replace("_", " ").title(),
            "base_type": "boolean",
            "writable": True,
        }
        for role in reversed(EXPECTED_ROLES)
    ]
    path.write_text(
        json.dumps(
            {
                "dsn": "AC0000000001",
                "product_name": "APC Test Strip",
                "address": address,
                "lanip_key_id": "key-1",
                "lanip_key": base64.b64encode(b"0123456789abcdef").decode(),
                "properties": properties,
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def make_runtime(tmp_path, *, transport=None, clock=None, timeout=8.0):
    path = tmp_path / "bootstrap.json"
    if not path.exists():
        write_bootstrap(path)
    publisher = Publisher()
    runtime = PluginRuntime(
        store=BootstrapConfigStore(path),
        settings=RuntimeSettings(
            callback_host="127.0.0.1",
            callback_port=free_port(),
            command_timeout=timeout,
            keepalive_interval=100,
        ),
        publisher=publisher,
        transport=transport or Transport(),
        clock=clock or Clock(),
        start_callback=False,
    )
    return runtime, publisher, path


def test_bootstrap_requires_exactly_six_named_roles_and_redacts_key(tmp_path):
    path = tmp_path / "bootstrap.json"
    write_bootstrap(path)
    config = BootstrapConfigStore(path).load()

    assert tuple(item.role for item in config.properties) == EXPECTED_ROLES
    assert config.lan_key not in repr(config)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["properties"].pop()
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ConfigurationError, match="exactly six"):
        BootstrapConfigStore(path).load()


def test_runtime_recreates_one_controller_and_six_stable_children(tmp_path):
    runtime, publisher, _ = make_runtime(tmp_path)
    first = {role: values[0] for role, values in publisher.children.items()}
    first_controller = publisher.controller[0]
    runtime.close()

    restarted, after_restart, _ = make_runtime(tmp_path)
    try:
        assert set(after_restart.children) == set(EXPECTED_ROLES)
        assert len(set(first.values())) == 6
        assert {role: values[0] for role, values in after_restart.children.items()} == first
        assert after_restart.controller[0] == first_controller
        assert all(values[1] == first_controller for values in after_restart.children.values())
    finally:
        restarted.close()


def test_command_dispatch_is_immediate_but_state_waits_for_verified_report(tmp_path):
    clock = Clock()
    transport = Transport()
    runtime, publisher, _ = make_runtime(tmp_path, clock=clock, transport=transport)
    try:
        manifest = json.loads(
            (Path(__file__).parents[1] / "server.json").read_text(encoding="utf-8")
        )
        short_poll = int(manifest["shortPoll"])
        assert short_poll == 15
        assert runtime.device.policy.command_timeout == 8.0

        address = runtime.child_addresses["outlet_1"]
        assert publisher.drivers.get((address, "ST")) is None

        runtime.command("outlet_1", True)
        assert publisher.drivers.get((address, "ST")) is None
        assert publisher.drivers[(address, "GV1")] == 1
        assert transport.calls == [
            {
                "address": "192.0.2.10",
                "callback_host": "127.0.0.1",
                "callback_port": runtime._settings.callback_port,
                "notify": True,
            }
        ]

        clock.now += short_poll
        runtime.poll()
        assert transport.calls[0]["notify"] is True
        assert publisher.drivers.get((address, "ST")) is None

        clock.now += 1
        runtime.device.receive_datapoints(
            {runtime.config.binding("outlet_1").name: True},
            source_address=runtime.device.address,
            now=clock.now,
        )
        assert publisher.drivers[(address, "ST")] == 1
        assert publisher.drivers[(address, "GV1")] == 0
    finally:
        runtime.close()


def test_device_originated_change_updates_confirmed_state(tmp_path):
    runtime, publisher, _ = make_runtime(tmp_path)
    try:
        binding = runtime.config.binding("usb_2")
        runtime.device.receive_datapoints(
            {binding.name: False}, source_address=runtime.device.address, now=12
        )
        assert publisher.drivers[(runtime.child_addresses["usb_2"], "ST")] == 0
        assert runtime.device.pending_count == 0
    finally:
        runtime.close()


def test_failed_command_clears_pending_and_marks_stale(tmp_path):
    clock = Clock()
    runtime, publisher, _ = make_runtime(tmp_path, clock=clock, timeout=2.0)
    try:
        address = runtime.child_addresses["led"]
        runtime.command("led", True)
        runtime.poll()
        clock.now += 3
        runtime.poll()

        assert runtime.device.pending_count == 0
        assert runtime.device.last_failure == "confirmation_timeout"
        assert publisher.drivers[(address, "GV1")] == 0
        assert publisher.drivers[(address, "GV2")] == 1
    finally:
        runtime.close()


def test_failed_registration_refreshes_dhcp_address_without_changing_nodes(tmp_path):
    clock = Clock()
    transport = Transport(fail=True)
    runtime, publisher, path = make_runtime(tmp_path, transport=transport, clock=clock)
    try:
        child_addresses = dict(runtime.child_addresses)
        write_bootstrap(path, address="192.0.2.44")
        runtime.command("outlet_3", True)
        runtime.poll()

        assert runtime.device.address == "192.0.2.44"
        assert runtime.child_addresses == child_addresses
        assert publisher.drivers[("controller", "GV0")] == 0
        assert publisher.drivers[("controller", "GV2")] == 1
    finally:
        runtime.close()


def test_public_restart_state_contains_no_secret(tmp_path):
    runtime, _, _ = make_runtime(tmp_path)
    try:
        serialized = json.dumps(runtime.public_restart_state())
        assert runtime.config.lan_key not in serialized
        assert runtime.config.key_id not in serialized
        assert set(runtime.public_restart_state()) == {"dsn", "last_address", "roles"}
    finally:
        runtime.close()


def test_matching_restart_state_restores_last_known_address(tmp_path):
    path = tmp_path / "bootstrap.json"
    write_bootstrap(path, address="192.0.2.10")
    runtime = PluginRuntime(
        store=BootstrapConfigStore(path),
        settings=RuntimeSettings(callback_host="127.0.0.1", callback_port=free_port()),
        publisher=Publisher(),
        transport=Transport(),
        start_callback=False,
        restart_state={"dsn": "AC0000000001", "last_address": "192.0.2.55"},
    )
    try:
        assert runtime.device.address == "192.0.2.55"
    finally:
        runtime.close()
