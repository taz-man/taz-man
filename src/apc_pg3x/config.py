"""Protected bootstrap configuration for one APC PH6U4X32 strip."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

EXPECTED_ROLES = (
    "led",
    "outlet_1",
    "outlet_2",
    "outlet_3",
    "usb_1",
    "usb_2",
)


class RejectionReason(str, Enum):
    """Bounded, non-sensitive startup rejection diagnostics."""

    BOOTSTRAP_MISSING = "BOOTSTRAP_MISSING"
    BOOTSTRAP_TYPE = "BOOTSTRAP_TYPE"
    BOOTSTRAP_OWNER = "BOOTSTRAP_OWNER"
    BOOTSTRAP_MODE = "BOOTSTRAP_MODE"
    BOOTSTRAP_JSON = "BOOTSTRAP_JSON"
    BOOTSTRAP_SCHEMA = "BOOTSTRAP_SCHEMA"
    BOOTSTRAP_PATH = "BOOTSTRAP_PATH"
    BOOTSTRAP_AMBIGUOUS = "BOOTSTRAP_AMBIGUOUS"
    CALLBACK_HOST = "CALLBACK_HOST"
    CALLBACK_PORT = "CALLBACK_PORT"
    SETTINGS = "SETTINGS"
    STARTUP = "STARTUP"


class ConfigurationError(ValueError):
    """The protected bootstrap file is missing or unsafe."""

    def __init__(self, message: str, reason: RejectionReason = RejectionReason.BOOTSTRAP_SCHEMA):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PropertyBinding:
    role: str
    name: str
    label: str


@dataclass(frozen=True, slots=True)
class BootstrapConfig:
    dsn: str
    product_name: str
    address: str
    key_id: str
    lan_key: str = field(repr=False)
    properties: tuple[PropertyBinding, ...]

    def binding(self, role: str) -> PropertyBinding:
        for binding in self.properties:
            if binding.role == role:
                return binding
        raise ConfigurationError("required endpoint role is missing")

    @property
    def property_names(self) -> tuple[str, ...]:
        return tuple(binding.name for binding in self.properties)


class BootstrapConfigStore:
    """Read secrets from an owner-only file without logging its contents."""

    def __init__(self, path: str | Path, *, require_owner_only: bool = True) -> None:
        self.path = Path(path)
        self.require_owner_only = require_owner_only

    def load(self) -> BootstrapConfig:
        try:
            file_stat = _lstat(self.path)
        except OSError:
            raise ConfigurationError(
                "bootstrap configuration is unavailable", RejectionReason.BOOTSTRAP_MISSING
            ) from None
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise ConfigurationError(
                "bootstrap configuration must be a regular file",
                RejectionReason.BOOTSTRAP_TYPE,
            )
        if self.require_owner_only and _is_posix():
            if file_stat.st_uid != os.geteuid():
                raise ConfigurationError(
                    "bootstrap configuration owner is invalid",
                    RejectionReason.BOOTSTRAP_OWNER,
                )
            if file_stat.st_mode & 0o077:
                raise ConfigurationError(
                    "bootstrap configuration must use mode 0600",
                    RejectionReason.BOOTSTRAP_MODE,
                )
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ConfigurationError(
                "bootstrap configuration is invalid", RejectionReason.BOOTSTRAP_JSON
            ) from None
        if not isinstance(raw, dict):
            raise ConfigurationError("bootstrap configuration is invalid")

        dsn = _required_text(raw, "dsn")
        product_name = _required_text(raw, "product_name")
        address = _required_text(raw, "address")
        key_id = _required_text(raw, "lanip_key_id")
        lan_key = _required_text(raw, "lanip_key")
        entries = raw.get("properties")
        if not isinstance(entries, list) or len(entries) != len(EXPECTED_ROLES):
            raise ConfigurationError("exactly six endpoint properties are required")

        bindings: list[PropertyBinding] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ConfigurationError("endpoint property metadata is invalid")
            role = _required_text(entry, "role")
            name = _required_text(entry, "name")
            label = _required_text(entry, "label")
            if entry.get("base_type", "boolean") != "boolean":
                raise ConfigurationError("endpoint properties must be Boolean")
            if entry.get("writable", True) is not True:
                raise ConfigurationError("endpoint properties must be writable")
            bindings.append(PropertyBinding(role=role, name=name, label=label))

        roles = tuple(binding.role for binding in bindings)
        names = tuple(binding.name for binding in bindings)
        if set(roles) != set(EXPECTED_ROLES) or len(set(roles)) != len(EXPECTED_ROLES):
            raise ConfigurationError("endpoint roles must match the six supported controls")
        if len(set(names)) != len(EXPECTED_ROLES):
            raise ConfigurationError("endpoint property names must be unique")

        ordered = tuple(next(item for item in bindings if item.role == role) for role in EXPECTED_ROLES)
        return BootstrapConfig(
            dsn=dsn,
            product_name=product_name,
            address=address,
            key_id=key_id,
            lan_key=lan_key,
            properties=ordered,
        )


def _required_text(source: dict, key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("bootstrap configuration is missing required fields")
    return value.strip()


def _lstat(path: Path):
    return path.lstat()


def _is_posix() -> bool:
    return os.name == "posix"
