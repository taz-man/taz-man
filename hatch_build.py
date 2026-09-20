from __future__ import annotations

import gzip
import importlib.util
import os
import tarfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_NORMALIZER_PATH = Path(__file__).parent / "build_tools" / "wheel_normalizer.py"
_NORMALIZER_SPEC = importlib.util.spec_from_file_location("wheel_normalizer", _NORMALIZER_PATH)
if _NORMALIZER_SPEC is None or _NORMALIZER_SPEC.loader is None:
    raise ImportError(f"cannot load wheel normalizer from {_NORMALIZER_PATH}")
_NORMALIZER = importlib.util.module_from_spec(_NORMALIZER_SPEC)
_NORMALIZER_SPEC.loader.exec_module(_NORMALIZER)
normalize_wheel = _NORMALIZER.normalize_wheel

_EXECUTABLE_SDIST_FILES = {"apc-poly.py", "install.sh"}


class CustomBuildHook(BuildHookInterface):
    """Normalize release archive metadata across build hosts."""

    def finalize(
        self,
        version: str,
        build_data: dict[str, object],
        artifact_path: str,
    ) -> None:
        artifact = Path(artifact_path)
        if self.target_name == "wheel":
            timestamp = int(os.environ.get("SOURCE_DATE_EPOCH", "315532800"))
            normalize_wheel(artifact, timestamp=timestamp)
            return
        if self.target_name != "sdist":
            return

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
