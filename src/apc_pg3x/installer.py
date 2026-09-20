"""Descriptor-safe installer setup for the persistent data directory."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path


class InstallerSafetyError(RuntimeError):
    """The persistent data directory cannot be prepared safely."""


def prepare_data_directory(plugin_root: str | Path = ".") -> None:
    """Create and harden data without following replaced path components."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None or not hasattr(os, "fchmod"):
        raise InstallerSafetyError("descriptor-safe directory setup is unavailable")

    descriptors: list[int] = []
    try:
        root_descriptor = os.open(plugin_root, os.O_RDONLY | directory | nofollow)
        descriptors.append(root_descriptor)
        try:
            os.mkdir("data", mode=0o700, dir_fd=root_descriptor)
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise
        data_descriptor = os.open(
            "data",
            os.O_RDONLY | directory | nofollow,
            dir_fd=root_descriptor,
        )
        descriptors.append(data_descriptor)
        before = os.fstat(data_descriptor)
        if not stat.S_ISDIR(before.st_mode) or before.st_uid != os.geteuid():
            raise InstallerSafetyError("persistent data directory is unsafe")
        os.fchmod(data_descriptor, 0o700)
        os.fsync(data_descriptor)
        os.fsync(root_descriptor)
        after = os.fstat(data_descriptor)
        path_state = os.stat("data", dir_fd=root_descriptor, follow_symlinks=False)
        if (
            (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or (after.st_dev, after.st_ino) != (path_state.st_dev, path_state.st_ino)
            or not stat.S_ISDIR(path_state.st_mode)
            or stat.S_IMODE(after.st_mode) != 0o700
        ):
            raise InstallerSafetyError("persistent data directory changed during setup")
    except InstallerSafetyError:
        raise
    except OSError as error:
        raise InstallerSafetyError("persistent data directory setup failed") from error
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


if __name__ == "__main__":
    prepare_data_directory()
