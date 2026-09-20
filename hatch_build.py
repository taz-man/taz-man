from __future__ import annotations

import gzip
import os
import tarfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_EXECUTABLE_SDIST_FILES = {"apc-poly.py", "install.sh"}


class CustomBuildHook(BuildHookInterface):
    """Normalize PG3x executable modes in source distributions."""

    def finalize(
        self,
        version: str,
        build_data: dict[str, object],
        artifact_path: str,
    ) -> None:
        if self.target_name != "sdist":
            return

        artifact = Path(artifact_path)
        temporary = artifact.with_name(f".{artifact.name}.tmp")
        try:
            with (
                tarfile.open(artifact, "r:gz") as source,
                temporary.open("wb") as raw_output,
                gzip.GzipFile(fileobj=raw_output, mode="wb", filename="", mtime=0) as compressed,
                tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as target,
            ):
                for member in source.getmembers():
                    if member.isfile() and Path(member.name).name in _EXECUTABLE_SDIST_FILES:
                        member.mode = 0o755
                    target.addfile(member, source.extractfile(member) if member.isfile() else None)
            os.replace(temporary, artifact)
        finally:
            temporary.unlink(missing_ok=True)
