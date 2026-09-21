import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from apc_pg3x import pg3
from apc_pg3x.config import EXPECTED_ROLES, ConfigurationError, RejectionReason


class FakeNode:
    def __init__(self, interface, primary, address, name):
        self.interface = interface
        self.primary = primary
        self.address = address
        self.name = name
        self.values = {}

    def setDriver(self, driver, value, report=True, force=False):
        self.values[driver] = value


class FakeCustom(dict):
    def __init__(self, interface, name):
        super().__init__()
        interface.custom[name] = self

    def load(self, values):
        self.clear()
        self.update(values)


class FakeInterface:
    CUSTOMPARAMS = "customparams"
    CUSTOMDATA = "customdata"
    POLL = "poll"
    STOP = "stop"

    def __init__(self, _classes):
        self.nodes = []
        self.events = {}
        self.Notices = {}
        self.custom = {}
        self.calls = []

    def start(self):
        self.calls.append("start")

    def subscribe(self, event, callback):
        self.events[event] = callback

    def ready(self):
        self.calls.append("ready")

    def setCustomParamsDoc(self):
        self.calls.append("docs")

    def updateProfile(self):
        self.calls.append("profile")

    def runForever(self):
        self.calls.append("run")

    def addNode(self, node):
        self.nodes.append(node)

    def stop(self):
        self.calls.append("stop")


class FakeRuntime:
    def __init__(self, *, store, settings, publisher, restart_state=None):
        self.store = store
        self.settings = settings
        self.publisher = publisher
        self.restart_state = restart_state
        self.commands = []
        self.polls = 0
        self.closed = False
        self.device = SimpleNamespace(address="192.0.2.20")
        controller = "controller123"
        publisher.add_controller(controller, "APC Controller")
        for role in EXPECTED_ROLES:
            publisher.add_child(f"child{len(publisher.nodes)}", controller, role, role)

    def command(self, role, value):
        self.commands.append((role, value))

    def query(self):
        self.poll()

    def discover(self):
        self.poll()

    def poll(self):
        self.polls += 1

    def close(self):
        self.closed = True

    def public_restart_state(self):
        return {"dsn": "dsn", "last_address": self.device.address, "roles": list(EXPECTED_ROLES)}


@pytest.fixture
def fake_udi():
    return SimpleNamespace(Interface=FakeInterface, Custom=FakeCustom, Node=FakeNode)


def test_pg3_application_subscribes_and_creates_fixed_topology(monkeypatch, tmp_path, fake_udi):
    monkeypatch.setattr(pg3, "PluginRuntime", FakeRuntime)
    app = pg3.Pg3Application(fake_udi, plugin_root=tmp_path)
    app.run()

    assert set(app.interface.events) == {"customparams", "customdata", "poll", "stop"}
    assert app.interface.calls[:4] == ["start", "ready", "docs", "profile"]

    app.load_restart_data({"dsn": "dsn", "last_address": "192.0.2.20"})
    app.configure(
        {
            "bootstrap_config_path": "data/apc-bootstrap.json",
            "callback_host": "192.0.2.5",
            "callback_port": "10275",
        }
    )
    assert len(app.interface.nodes) == 7
    assert len([node for node in app.interface.nodes if hasattr(node, "role")]) == 6
    assert set(app.restart_data) == {"dsn", "last_address", "roles"}
    assert app.runtime.restart_state["last_address"] == "192.0.2.20"
    assert app.runtime.polls == 1

    outlet = next(node for node in app.interface.nodes if getattr(node, "role", None) == "outlet_1")
    outlet.on()
    outlet.off()
    assert app.runtime.commands == [("outlet_1", True), ("outlet_1", False)]

    app.poll("shortPoll")
    app.poll("longPoll")
    assert app.runtime.polls == 3
    assert app.restart_data["last_address"] == "192.0.2.20"
    app.stop()
    assert app.runtime.closed is True
    assert app.interface.calls[-1] == "stop"


def test_pg3_application_ignores_unknown_poll_values(monkeypatch, tmp_path, fake_udi):
    monkeypatch.setattr(pg3, "PluginRuntime", FakeRuntime)
    app = pg3.Pg3Application(fake_udi, plugin_root=tmp_path)
    app.configure(
        {
            "bootstrap_config_path": "data/apc-bootstrap.json",
            "callback_host": "controller.local",
        }
    )

    app.poll("unexpected")
    app.poll({"shortPoll": True})

    assert app.runtime.polls == 1


def test_pg3_application_rejects_bootstrap_path_escape(tmp_path, fake_udi):
    app = pg3.Pg3Application(fake_udi, plugin_root=tmp_path)
    with pytest.raises(ConfigurationError) as rejected:
        app._settings(
            {
                "bootstrap_config_path": str(Path("..") / "secret.json"),
                "callback_host": "192.0.2.5",
            }
        )
    assert rejected.value.reason is RejectionReason.BOOTSTRAP_PATH


def test_default_bootstrap_path_uses_only_canonical_data_file(tmp_path, fake_udi):
    unsupported = tmp_path / "apc-bootstrap.json"
    unsupported.write_text("{}", encoding="utf-8")
    unsupported.chmod(0o600)
    app = pg3.Pg3Application(fake_udi, plugin_root=tmp_path)

    _, store = app._settings(
        {
            "bootstrap_config_path": "data/apc-bootstrap.json",
            "callback_host": "192.0.2.5",
        }
    )

    assert store.path == tmp_path / "data" / "apc-bootstrap.json"
    assert store.normalize_uploaded_mode is True


def test_noncanonical_bootstrap_path_is_prohibited(tmp_path, fake_udi):
    app = pg3.Pg3Application(fake_udi, plugin_root=tmp_path)

    with pytest.raises(ConfigurationError) as rejected:
        app._settings(
            {
                "bootstrap_config_path": "protected/custom.json",
                "callback_host": "eisy.local",
            }
        )

    assert rejected.value.reason is RejectionReason.BOOTSTRAP_PATH


def test_canonical_data_path_is_selected_for_mode_repair(tmp_path, fake_udi):
    canonical = tmp_path / "data" / "apc-bootstrap.json"
    canonical.parent.mkdir()
    canonical.write_text("{}", encoding="utf-8")
    app = pg3.Pg3Application(fake_udi, plugin_root=tmp_path)

    _, store = app._settings(
        {
            "bootstrap_config_path": "data/apc-bootstrap.json",
            "callback_host": "eisy.local",
        }
    )

    assert store.path == canonical
    assert store.normalize_uploaded_mode is True


def test_noncanonical_symlink_path_is_prohibited(tmp_path, fake_udi):
    app = pg3.Pg3Application(fake_udi, plugin_root=tmp_path)

    with pytest.raises(ConfigurationError) as rejected:
        app._settings(
            {
                "bootstrap_config_path": "bootstrap.json",
                "callback_host": "eisy.local",
            }
        )
    assert rejected.value.reason is RejectionReason.BOOTSTRAP_PATH


def test_top_level_file_does_not_make_canonical_path_ambiguous(tmp_path, fake_udi):
    canonical = tmp_path / "data" / "apc-bootstrap.json"
    canonical.parent.mkdir()
    canonical.write_text("{}", encoding="utf-8")
    uploaded = tmp_path / "apc-bootstrap.json"
    uploaded.write_text("{}", encoding="utf-8")
    app = pg3.Pg3Application(fake_udi, plugin_root=tmp_path)

    _, store = app._settings(
        {
            "bootstrap_config_path": "data/apc-bootstrap.json",
            "callback_host": "192.0.2.5",
        }
    )

    assert store.path == canonical


def test_configuration_rejection_logs_only_bounded_reason_code(
    tmp_path, fake_udi, caplog
):
    sensitive_path = "data/private-device-identity.json"
    sensitive_host = "private-callback.local"
    app = pg3.Pg3Application(fake_udi, plugin_root=tmp_path)

    with caplog.at_level(logging.ERROR, logger="apc_pg3x.pg3"):
        app.configure(
            {
                "bootstrap_config_path": sensitive_path,
                "callback_host": sensitive_host,
            }
        )

    assert caplog.messages == ["APC plugin configuration rejected [BOOTSTRAP_PATH]"]
    assert app.interface.Notices["configuration"] == (
        "Configuration rejected [BOOTSTRAP_PATH]."
    )
    rendered = " ".join(caplog.messages + list(app.interface.Notices.values()))
    assert sensitive_path not in rendered
    assert sensitive_host not in rendered


def test_unexpected_startup_failure_does_not_log_dynamic_exception(
    monkeypatch, tmp_path, fake_udi, caplog
):
    sensitive_exception = "runtime contained sensitive device material"

    def fail_runtime(**_kwargs):
        raise RuntimeError(sensitive_exception)

    monkeypatch.setattr(pg3, "PluginRuntime", fail_runtime)
    app = pg3.Pg3Application(fake_udi, plugin_root=tmp_path)

    with caplog.at_level(logging.ERROR, logger="apc_pg3x.pg3"):
        app.configure(
            {
                "bootstrap_config_path": "data/apc-bootstrap.json",
                "callback_host": "eisy.local",
            }
        )

    assert caplog.messages == ["APC plugin configuration rejected [STARTUP]"]
    assert sensitive_exception not in " ".join(caplog.messages)


def test_invalid_numeric_setting_logs_only_settings_reason(tmp_path, fake_udi, caplog):
    app = pg3.Pg3Application(fake_udi, plugin_root=tmp_path)

    with caplog.at_level(logging.ERROR, logger="apc_pg3x.pg3"):
        app.configure(
            {
                "callback_host": "eisy.local",
                "callback_port": "not-a-port",
            }
        )

    assert caplog.messages == ["APC plugin configuration rejected [SETTINGS]"]
