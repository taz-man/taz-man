"""Stable IoX node-address generation."""

from __future__ import annotations

import hashlib


def endpoint_address(device_serial: str, endpoint: str) -> str:
    """Return a deterministic, IoX-safe address of at most 14 characters."""
    identity = f"{device_serial}\x00{endpoint}".encode()
    return "a" + hashlib.sha256(identity).hexdigest()[:13]
