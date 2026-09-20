from __future__ import annotations

import os
import stat
import zipfile
from datetime import UTC, datetime
from pathlib import Path

_MINIMUM_ZIP_TIMESTAMP = 315_532_800
_MAXIMUM_ZIP_TIMESTAMP = 4_354_819_198


def _zip_datetime(timestamp: int) -> tuple[int, int, int, int, int, int]:
    bounded = min(max(timestamp, _MINIMUM_ZIP_TIMESTAMP), _MAXIMUM_ZIP_TIMESTAMP)
    value = datetime.fromtimestamp(bounded, tz=UTC)
    return value.year, value.month, value.day, value.hour, value.minute, value.second // 2 * 2


def normalize_wheel(path: Path, *, timestamp: int) -> None:
    """Rewrite a wheel with host-independent ZIP metadata and storage."""
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with zipfile.ZipFile(path, "r") as source:
            entries = [(member.filename, source.read(member)) for member in source.infolist()]

        entries.sort(key=lambda entry: (entry[0].endswith(".dist-info/RECORD"), entry[0]))
        date_time = _zip_datetime(timestamp)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as target:
            for name, payload in entries:
                member = zipfile.ZipInfo(name, date_time)
                member.create_system = 3
                member.compress_type = zipfile.ZIP_STORED
                if name.endswith("/"):
                    member.external_attr = (stat.S_IFDIR | 0o755) << 16 | 0x10
                else:
                    member.external_attr = (stat.S_IFREG | 0o644) << 16
                target.writestr(member, payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
