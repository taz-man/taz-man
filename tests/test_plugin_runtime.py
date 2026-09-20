import base64
import json
import socket
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from apc_pg3x import config as config_module
from apc_pg3x.config import (
    EXPECTED_ROLES,
    BootstrapConfigStore,
    ConfigurationError,
    RejectionReason,
)
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


def test_missing_bootstrap_has_bounded_reason_code(tmp_path):
    with pytest.raises(ConfigurationError) as rejected:
        BootstrapConfigStore(tmp_path / "absent.json").load()

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_MISSING
    assert rejected.value.reason.value == "BOOTSTRAP_MISSING"


def test_all_rejection_reason_codes_are_bounded_identifiers():
    codes = [reason.value for reason in RejectionReason]

    assert len(codes) == len(set(codes))
    assert all(len(code) <= 32 for code in codes)
    assert all(code.replace("_", "").isalpha() and code == code.upper() for code in codes)


def test_non_regular_bootstrap_has_bounded_reason_code(tmp_path):
    with pytest.raises(ConfigurationError) as rejected:
        BootstrapConfigStore(tmp_path).load()

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_TYPE


def test_malformed_bootstrap_json_has_bounded_reason_code(tmp_path):
    path = tmp_path / "bootstrap.json"
    path.write_text("not-json", encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ConfigurationError) as rejected:
        BootstrapConfigStore(path).load()

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_JSON


def test_invalid_bootstrap_schema_has_bounded_reason_code(tmp_path):
    path = tmp_path / "bootstrap.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ConfigurationError) as rejected:
        BootstrapConfigStore(path).load()

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_SCHEMA


def test_group_readable_bootstrap_has_bounded_reason_code(tmp_path, monkeypatch):
    path = tmp_path / "bootstrap.json"
    path.write_text("{}", encoding="utf-8")
    store = BootstrapConfigStore(path)
    if config_module.os.name == "posix":
        path.chmod(0o640)
    else:
        file_stat = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o640,
            st_uid=1000,
            st_nlink=1,
            st_size=2,
        )
        monkeypatch.setattr(config_module, "_is_posix", lambda: True, raising=False)
        monkeypatch.setattr(config_module, "_lstat", lambda _path: file_stat, raising=False)
        monkeypatch.setattr(config_module.os, "geteuid", lambda: 1000, raising=False)

    with pytest.raises(ConfigurationError) as rejected:
        store.load()

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_MODE


def test_foreign_owned_bootstrap_has_bounded_reason_code(tmp_path, monkeypatch):
    path = tmp_path / "bootstrap.json"
    path.write_text("{}", encoding="utf-8")
    store = BootstrapConfigStore(path)
    if config_module.os.name == "posix":
        path.chmod(0o600)
        real_fstat = config_module.os.fstat

        def foreign_fstat(descriptor):
            result = real_fstat(descriptor)
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_uid=result.st_uid + 1,
                st_nlink=result.st_nlink,
                st_size=result.st_size,
                st_dev=result.st_dev,
                st_ino=result.st_ino,
            )

        monkeypatch.setattr(config_module.os, "fstat", foreign_fstat)
    else:
        file_stat = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_uid=1001,
            st_nlink=1,
            st_size=2,
        )
        monkeypatch.setattr(config_module, "_is_posix", lambda: True, raising=False)
        monkeypatch.setattr(config_module, "_lstat", lambda _path: file_stat, raising=False)
        monkeypatch.setattr(config_module.os, "geteuid", lambda: 1000, raising=False)

    with pytest.raises(ConfigurationError) as rejected:
        store.load()

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_OWNER


@pytest.mark.skipif(not hasattr(config_module.os, "geteuid"), reason="POSIX-only security probe")
def test_uploaded_default_mode_is_hardened_before_secret_read(tmp_path, monkeypatch):
    path = tmp_path / "data" / "apc-bootstrap.json"
    path.parent.mkdir()
    write_bootstrap(path)
    path.chmod(0o644)
    events = []
    real_fchmod = config_module.os.fchmod
    real_fsync = config_module.os.fsync
    real_fstat = config_module.os.fstat
    real_read = config_module.os.read

    def recording_fchmod(fd, mode):
        events.append(("chmod", mode))
        return real_fchmod(fd, mode)

    def recording_fsync(fd):
        events.append(("fsync", fd))
        return real_fsync(fd)

    def recording_fstat(fd):
        events.append(("fstat", fd))
        return real_fstat(fd)

    def recording_read(fd, size):
        events.append(("read", size))
        return real_read(fd, size)

    monkeypatch.setattr(config_module.os, "fchmod", recording_fchmod)
    monkeypatch.setattr(config_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(config_module.os, "fstat", recording_fstat)
    monkeypatch.setattr(config_module.os, "read", recording_read)

    config = BootstrapConfigStore(
        path,
        normalize_uploaded_mode=True,
        plugin_root=tmp_path,
    ).load()

    assert config.dsn == "AC0000000001"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    operations = [event[0] for event in events]
    assert operations == ["fstat", "chmod", "fsync", "fstat", "read", "read"]


@pytest.mark.skipif(not hasattr(config_module.os, "geteuid"), reason="POSIX-only security probe")
def test_canonical_source_is_retained_at_mode_0600_for_restart(tmp_path):
    path = tmp_path / "data" / "apc-bootstrap.json"
    path.parent.mkdir()
    write_bootstrap(path)
    path.chmod(0o644)

    BootstrapConfigStore(
        path,
        normalize_uploaded_mode=True,
        plugin_root=tmp_path,
    ).load()

    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(not hasattr(config_module.os, "geteuid"), reason="POSIX-only security probe")
def test_explicit_insecure_bootstrap_is_rejected_without_repair(tmp_path):
    path = tmp_path / "custom.json"
    write_bootstrap(path)
    path.chmod(0o644)

    with pytest.raises(ConfigurationError) as rejected:
        BootstrapConfigStore(path, plugin_root=tmp_path).load()

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_MODE
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


@pytest.mark.skipif(not hasattr(config_module.os, "geteuid"), reason="POSIX-only security probe")
def test_mode_normalization_flag_cannot_repair_top_level_fallback(tmp_path):
    path = tmp_path / "apc-bootstrap.json"
    write_bootstrap(path)
    path.chmod(0o644)

    with pytest.raises(ConfigurationError) as rejected:
        BootstrapConfigStore(
            path,
            normalize_uploaded_mode=True,
            plugin_root=tmp_path,
        ).load()

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_MODE
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


@pytest.mark.skipif(not hasattr(config_module.os, "geteuid"), reason="POSIX-only security probe")
def test_uploaded_hardlink_is_rejected_without_repair(tmp_path):
    original = tmp_path / "original.json"
    uploaded = tmp_path / "data" / "apc-bootstrap.json"
    uploaded.parent.mkdir()
    write_bootstrap(original)
    original.chmod(0o644)
    try:
        config_module.os.link(original, uploaded)
    except OSError:
        pytest.skip("hardlink creation is unavailable")

    with pytest.raises(ConfigurationError) as rejected:
        BootstrapConfigStore(
            uploaded,
            normalize_uploaded_mode=True,
            plugin_root=tmp_path,
        ).load()

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_LINK
    assert stat.S_IMODE(original.stat().st_mode) == 0o644


@pytest.mark.skipif(not hasattr(config_module.os, "geteuid"), reason="POSIX-only security probe")
def test_uploaded_symlink_is_rejected_without_changing_target(tmp_path):
    target = tmp_path / "target.json"
    uploaded = tmp_path / "data" / "apc-bootstrap.json"
    uploaded.parent.mkdir()
    write_bootstrap(target)
    target.chmod(0o644)
    try:
        uploaded.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ConfigurationError) as rejected:
        BootstrapConfigStore(
            uploaded,
            normalize_uploaded_mode=True,
            plugin_root=tmp_path,
        ).load()

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_TYPE
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


@pytest.mark.skipif(not hasattr(config_module.os, "geteuid"), reason="POSIX-only security probe")
def test_canonical_parent_symlink_is_rejected_before_read(tmp_path, monkeypatch):
    target_dir = tmp_path / "target-data"
    target_dir.mkdir()
    target = target_dir / "apc-bootstrap.json"
    write_bootstrap(target)
    try:
        (tmp_path / "data").symlink_to(target_dir, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    monkeypatch.setattr(
        config_module.os,
        "read",
        lambda *_args: pytest.fail("bootstrap below a symlinked parent must not be read"),
    )

    with pytest.raises(ConfigurationError) as rejected:
        BootstrapConfigStore(
            tmp_path / "data" / "apc-bootstrap.json",
            normalize_uploaded_mode=True,
            plugin_root=tmp_path,
        ).load()

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_TYPE


@pytest.mark.skipif(not hasattr(config_module.os, "geteuid"), reason="POSIX-only security probe")
def test_path_swap_after_open_fails_closed_before_read(tmp_path, monkeypatch):
    uploaded = tmp_path / "data" / "apc-bootstrap.json"
    replacement = tmp_path / "replacement.json"
    uploaded.parent.mkdir()
    write_bootstrap(uploaded, address="192.0.2.10")
    write_bootstrap(replacement, address="192.0.2.99")
    uploaded.chmod(0o644)
    real_fchmod = config_module.os.fchmod

    def swap_path_then_chmod(fd, mode):
        uploaded.unlink()
        uploaded.symlink_to(replacement)
        real_fchmod(fd, mode)

    monkeypatch.setattr(config_module.os, "fchmod", swap_path_then_chmod)
    monkeypatch.setattr(
        config_module.os,
        "read",
        lambda *_args: pytest.fail("swapped bootstrap must not be read"),
    )

    with pytest.raises(ConfigurationError) as rejected:
        BootstrapConfigStore(
            uploaded,
            normalize_uploaded_mode=True,
            plugin_root=tmp_path,
        ).load()

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_LINK
    assert stat.S_IMODE(replacement.stat().st_mode) == 0o600


def test_oversized_bootstrap_is_rejected_before_read(tmp_path, monkeypatch):
    path = tmp_path / "data" / "apc-bootstrap.json"
    path.parent.mkdir()
    path.write_bytes(b" " * (config_module.MAX_BOOTSTRAP_BYTES + 1))
    path.chmod(0o600)
    monkeypatch.setattr(
        config_module.os,
        "read",
        lambda *_args: pytest.fail("oversized bootstrap must not be read"),
    )

    with pytest.raises(ConfigurationError) as rejected:
        BootstrapConfigStore(path, plugin_root=tmp_path).load()

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_SIZE


@pytest.mark.skipif(config_module.os.name == "posix", reason="Windows portability probe")
def test_windows_load_does_not_attempt_posix_mode_hardening(tmp_path, monkeypatch):
    path = tmp_path / "data" / "apc-bootstrap.json"
    path.parent.mkdir()
    write_bootstrap(path)
    monkeypatch.setattr(
        config_module.os,
        "fchmod",
        lambda *_args: pytest.fail("Windows must not call fchmod"),
        raising=False,
    )

    config = BootstrapConfigStore(
        path,
        normalize_uploaded_mode=True,
        plugin_root=tmp_path,
    ).load()

    assert config.dsn == "AC0000000001"


@pytest.mark.parametrize("callback_host", ["", "https://host.local", "host/name", "bad host"])
def test_invalid_callback_host_has_bounded_reason_code(callback_host):
    with pytest.raises(ConfigurationError) as rejected:
        RuntimeSettings(callback_host=callback_host)

    assert rejected.value.reason is RejectionReason.CALLBACK_HOST


@pytest.mark.parametrize("callback_host", ["192.0.2.5", "eisy.local", "2001:db8::5"])
def test_secure_callback_host_forms_are_accepted(callback_host):
    assert RuntimeSettings(callback_host=callback_host).callback_host == callback_host


def test_invalid_callback_port_has_bounded_reason_code():
    with pytest.raises(ConfigurationError) as rejected:
        RuntimeSettings(callback_host="eisy.local", callback_port=0)

    assert rejected.value.reason is RejectionReason.CALLBACK_PORT


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
