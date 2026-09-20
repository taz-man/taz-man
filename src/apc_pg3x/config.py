"""Protected bootstrap configuration for one APC PH6U4X32 strip."""

from __future__ import annotations

import errno
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
MAX_BOOTSTRAP_BYTES = 65536
PG3X_UPLOADED_BOOTSTRAP_NAME = "apc-bootstrap.json"


class RejectionReason(str, Enum):
    """Bounded, non-sensitive startup rejection diagnostics."""

    BOOTSTRAP_MISSING = "BOOTSTRAP_MISSING"
    BOOTSTRAP_TYPE = "BOOTSTRAP_TYPE"
    BOOTSTRAP_OWNER = "BOOTSTRAP_OWNER"
    BOOTSTRAP_MODE = "BOOTSTRAP_MODE"
    BOOTSTRAP_LINK = "BOOTSTRAP_LINK"
    BOOTSTRAP_SIZE = "BOOTSTRAP_SIZE"
    BOOTSTRAP_HARDEN = "BOOTSTRAP_HARDEN"
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

    def __init__(
        self,
        path: str | Path,
        *,
        require_owner_only: bool = True,
        normalize_uploaded_mode: bool = False,
        plugin_root: str | Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.require_owner_only = require_owner_only
        self.normalize_uploaded_mode = normalize_uploaded_mode
        self.plugin_root = Path(plugin_root) if plugin_root is not None else None

    def load(self) -> BootstrapConfig:
        raw_bytes = self._read_protected_bytes()
        try:
            raw = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
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

    def _read_protected_bytes(self) -> bytes:
        if os.name == "posix":
            return self._read_posix_descriptor()
        return self._read_portable()

    def _read_posix_descriptor(self) -> bytes:
        if self.plugin_root is None:
            root = self.path.parent
            parts = (self.path.name,)
        else:
            root = self.plugin_root
            try:
                parts = self.path.relative_to(root).parts
            except ValueError:
                raise ConfigurationError(
                    "bootstrap path is unsafe", RejectionReason.BOOTSTRAP_PATH
                ) from None
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ConfigurationError(
                "bootstrap path is unsafe", RejectionReason.BOOTSTRAP_PATH
            )
        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        if nofollow is None or directory is None or not hasattr(os, "fchmod"):
            raise ConfigurationError(
                "bootstrap protection is unavailable", RejectionReason.BOOTSTRAP_HARDEN
            )
        descriptors: list[int] = []
        try:
            current = os.open(root, os.O_RDONLY | directory | nofollow)
            descriptors.append(current)
            for part in parts[:-1]:
                current = os.open(
                    part,
                    os.O_RDONLY | directory | nofollow,
                    dir_fd=current,
                )
                descriptors.append(current)
            descriptor = os.open(parts[-1], os.O_RDONLY | nofollow, dir_fd=current)
            descriptors.append(descriptor)
            before = os.fstat(descriptor)
            self._validate_descriptor(before)
            if self.require_owner_only and stat.S_IMODE(before.st_mode) != 0o600:
                if not self._is_uploaded_default_candidate():
                    raise ConfigurationError(
                        "bootstrap configuration must use mode 0600",
                        RejectionReason.BOOTSTRAP_MODE,
                    )
                try:
                    os.fchmod(descriptor, 0o600)
                    os.fsync(descriptor)
                    after = os.fstat(descriptor)
                except OSError:
                    raise ConfigurationError(
                        "bootstrap protection failed", RejectionReason.BOOTSTRAP_HARDEN
                    ) from None
                self._validate_descriptor(after)
                if (
                    (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                    or stat.S_IMODE(after.st_mode) != 0o600
                ):
                    raise ConfigurationError(
                        "bootstrap protection failed", RejectionReason.BOOTSTRAP_HARDEN
                    )
            return _read_bounded_descriptor(descriptor)
        except FileNotFoundError:
            raise ConfigurationError(
                "bootstrap configuration is unavailable", RejectionReason.BOOTSTRAP_MISSING
            ) from None
        except ConfigurationError:
            raise
        except OSError as error:
            reason = (
                RejectionReason.BOOTSTRAP_TYPE
                if error.errno in {errno.ELOOP, errno.ENOTDIR}
                else RejectionReason.BOOTSTRAP_HARDEN
            )
            raise ConfigurationError("bootstrap configuration is unsafe", reason) from None
        finally:
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _is_uploaded_default_candidate(self) -> bool:
        return (
            self.normalize_uploaded_mode
            and self.plugin_root is not None
            and self.path == self.plugin_root / PG3X_UPLOADED_BOOTSTRAP_NAME
        )

    def _read_portable(self) -> bytes:
        try:
            file_stat = _lstat(self.path)
        except OSError:
            raise ConfigurationError(
                "bootstrap configuration is unavailable", RejectionReason.BOOTSTRAP_MISSING
            ) from None
        self._validate_descriptor(file_stat)
        if (
            self.require_owner_only
            and _is_posix()
            and stat.S_IMODE(file_stat.st_mode) != 0o600
        ):
            raise ConfigurationError(
                "bootstrap configuration must use mode 0600",
                RejectionReason.BOOTSTRAP_MODE,
            )
        try:
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except OSError:
            raise ConfigurationError(
                "bootstrap configuration is unavailable", RejectionReason.BOOTSTRAP_MISSING
            ) from None
        try:
            return _read_bounded_descriptor(descriptor)
        finally:
            os.close(descriptor)

    def _validate_descriptor(self, file_stat) -> None:
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise ConfigurationError(
                "bootstrap configuration must be a regular file",
                RejectionReason.BOOTSTRAP_TYPE,
            )
        if file_stat.st_nlink != 1:
            raise ConfigurationError(
                "bootstrap configuration must have one link",
                RejectionReason.BOOTSTRAP_LINK,
            )
        if file_stat.st_size > MAX_BOOTSTRAP_BYTES:
            raise ConfigurationError(
                "bootstrap configuration is too large",
                RejectionReason.BOOTSTRAP_SIZE,
            )
        if self.require_owner_only and _is_posix() and file_stat.st_uid != os.geteuid():
            raise ConfigurationError(
                "bootstrap configuration owner is invalid",
                RejectionReason.BOOTSTRAP_OWNER,
            )


def _required_text(source: dict, key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("bootstrap configuration is missing required fields")
    return value.strip()


def _lstat(path: Path):
    return path.lstat()


def _read_bounded_descriptor(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = MAX_BOOTSTRAP_BYTES + 1
    while remaining:
        chunk = os.read(descriptor, min(remaining, 8192))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > MAX_BOOTSTRAP_BYTES:
        raise ConfigurationError(
            "bootstrap configuration is too large", RejectionReason.BOOTSTRAP_SIZE
        )
    return payload


def _is_posix() -> bool:
    return os.name == "posix"
