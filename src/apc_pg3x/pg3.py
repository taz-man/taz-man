"""PG3x adapter for the framework-neutral APC runtime."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, ClassVar

from .config import BootstrapConfigStore, ConfigurationError, RejectionReason
from .runtime import PluginRuntime, RuntimeSettings

_LOGGER = logging.getLogger(__name__)
_DEFAULT_BOOTSTRAP_PATH = "data/apc-bootstrap.json"



class Pg3Publisher:
    """Create and update PG3 nodes while retaining no device secrets."""

    def __init__(self, interface: Any, udi: Any) -> None:
        self.interface = interface
        self.udi = udi
        self.runtime: PluginRuntime | None = None
        publisher = self

        class ControllerNode(udi.Node):
            id = "controller"
            drivers: ClassVar[list[dict[str, object]]] = [
                {"driver": "ST", "value": 0, "uom": 2},
                {"driver": "GV0", "value": 0, "uom": 2},
                {"driver": "GV1", "value": 0, "uom": 58},
                {"driver": "GV2", "value": 1, "uom": 2},
            ]

            def query(self, _command=None):
                if publisher.runtime is not None:
                    publisher.runtime.query()

            def discover(self, _command=None):
                if publisher.runtime is not None:
                    publisher.runtime.discover()

            def update_profile(self, _command=None):
                publisher.interface.updateProfile()

            commands: ClassVar[dict[str, object]] = {
                "QUERY": query,
                "DISCOVER": discover,
                "UPDATE_PROFILE": update_profile,
            }

        class SwitchNode(udi.Node):
            id = "apcswitch"
            drivers: ClassVar[list[dict[str, object]]] = [
                {"driver": "ST", "value": 0, "uom": 2},
                {"driver": "GV0", "value": 0, "uom": 2},
                {"driver": "GV1", "value": 0, "uom": 2},
                {"driver": "GV2", "value": 1, "uom": 2},
            ]

            def on(self, _command=None):
                if publisher.runtime is not None:
                    publisher.runtime.command(self.role, True)

            def off(self, _command=None):
                if publisher.runtime is not None:
                    publisher.runtime.command(self.role, False)

            def query(self, _command=None):
                if publisher.runtime is not None:
                    publisher.runtime.query()

            commands: ClassVar[dict[str, object]] = {
                "DON": on,
                "DOF": off,
                "QUERY": query,
            }

        self.ControllerNode = ControllerNode
        self.SwitchNode = SwitchNode
        self.controller_address: str | None = None
        self.nodes: dict[str, Any] = {}

    def add_controller(self, address: str, name: str) -> None:
        self.controller_address = address
        node = self.ControllerNode(self.interface, address, address, name)
        self.nodes[address] = node
        self.interface.addNode(node)

    def add_child(self, address: str, primary: str, name: str, role: str) -> None:
        node = self.SwitchNode(self.interface, primary, address, name)
        node.role = role
        self.nodes[address] = node
        self.interface.addNode(node)

    def set_controller(self, driver: str, value: int, *, force: bool = False) -> None:
        if self.controller_address is not None:
            self.nodes[self.controller_address].setDriver(driver, value, True, force)

    def set_child(self, address: str, driver: str, value: int, *, force: bool = False) -> None:
        self.nodes[address].setDriver(driver, value, True, force)


class Pg3Application:
    """Subscribe to PG3 events and start one runtime after safe configuration."""

    def __init__(self, udi: Any, *, plugin_root: Path | None = None) -> None:
        self.udi = udi
        self.plugin_root = (plugin_root or Path.cwd()).resolve()
        self.interface = udi.Interface([])
        self.publisher = Pg3Publisher(self.interface, udi)
        self.parameters = udi.Custom(self.interface, "customparams")
        self.restart_data = udi.Custom(self.interface, "customdata")
        self.runtime: PluginRuntime | None = None

    def run(self) -> None:
        self.interface.start()
        self.interface.subscribe(self.interface.CUSTOMPARAMS, self.configure)
        self.interface.subscribe(self.interface.CUSTOMDATA, self.load_restart_data)
        self.interface.subscribe(self.interface.POLL, self.poll)
        self.interface.subscribe(self.interface.STOP, self.stop)
        self.interface.ready()
        self.interface.setCustomParamsDoc()
        self.interface.updateProfile()
        self.interface.runForever()

    def configure(self, params: dict[str, object]) -> None:
        self.parameters.load(params)
        if self.runtime is not None:
            return
        try:
            settings, store = self._settings(dict(params))
            runtime = PluginRuntime(
                store=store,
                settings=settings,
                publisher=self.publisher,
                restart_state=dict(self.restart_data.items()),
            )
        except ConfigurationError as error:
            self._reject_configuration(error.reason)
            return
        except (TypeError, ValueError):
            self._reject_configuration(RejectionReason.SETTINGS)
            return
        except Exception:  # noqa: BLE001 - fail closed with a bounded diagnostic
            self._reject_configuration(RejectionReason.STARTUP)
            return
        self.runtime = runtime
        self.publisher.runtime = runtime
        self.restart_data.clear()
        self.restart_data.update(runtime.public_restart_state())
        self.interface.Notices.pop("configuration", None)

    def _reject_configuration(self, reason: RejectionReason) -> None:
        self.interface.Notices["configuration"] = f"Configuration rejected [{reason.value}]."
        _LOGGER.error("APC plugin configuration rejected [%s]", reason.value)

    def load_restart_data(self, values: dict[str, object] | None) -> None:
        self.restart_data.load(values)

    def poll(self, poll_type: object) -> None:
        if self.runtime is None:
            return
        if isinstance(poll_type, dict) and (
            "shortPoll" in poll_type or "longPoll" in poll_type
        ):
            self.runtime.poll()
            if "longPoll" in poll_type:
                self.restart_data["last_address"] = self.runtime.device.address

    def stop(self, *_args: object) -> None:
        if self.runtime is not None:
            self.runtime.close()
        self.interface.stop()

    def _settings(
        self, params: dict[str, object]
    ) -> tuple[RuntimeSettings, BootstrapConfigStore]:
        relative_path = str(params.get("bootstrap_config_path", _DEFAULT_BOOTSTRAP_PATH))
        if relative_path != _DEFAULT_BOOTSTRAP_PATH:
            raise ConfigurationError(
                "bootstrap path must be the canonical data file",
                RejectionReason.BOOTSTRAP_PATH,
            )
        path = Path(os.path.abspath(self.plugin_root / relative_path))
        if not path.is_relative_to(self.plugin_root):
            raise ConfigurationError(
                "bootstrap path must remain under the plugin directory",
                RejectionReason.BOOTSTRAP_PATH,
            )
        if _has_symlink_parent(self.plugin_root, path):
            raise ConfigurationError(
                "bootstrap path parent is unsafe", RejectionReason.BOOTSTRAP_PATH
            )
        normalize_uploaded_mode = True
        callback_host = str(params.get("callback_host", "")).strip()
        settings = RuntimeSettings(
            callback_host=callback_host,
            callback_port=_integer(params, "callback_port", 10275),
            command_timeout=_number(params, "command_timeout_seconds", 8.0),
            max_attempts=_integer(params, "max_command_attempts", 3),
        )
        return settings, BootstrapConfigStore(
            path,
            normalize_uploaded_mode=normalize_uploaded_mode,
            plugin_root=self.plugin_root,
        )


def _integer(values: dict[str, object], name: str, default: int) -> int:
    value = values.get(name, default)
    if isinstance(value, bool):
        raise TypeError("invalid integer parameter")
    return int(value)


def _number(values: dict[str, object], name: str, default: float) -> float:
    value = values.get(name, default)
    if isinstance(value, bool):
        raise TypeError("invalid numeric parameter")
    return float(value)


def _has_symlink_parent(root: Path, candidate: Path) -> bool:
    current = root
    for part in candidate.relative_to(root).parts[:-1]:
        current /= part
        if current.is_symlink():
            return True
    return False


def run() -> None:
    import udi_interface  # Imported only by the installed PG3x entry point.

    Pg3Application(udi_interface).run()
