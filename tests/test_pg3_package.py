import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

import apc_pg3x
from apc_pg3x import installer

ROOT = Path(__file__).parents[1]


def _run_installer(plugin_root: Path) -> subprocess.CompletedProcess[str]:
    shutil.copy2(ROOT / "install.sh", plugin_root / "install.sh")
    shutil.copytree(ROOT / "src", plugin_root / "src")
    fake_bin = plugin_root / "fake-bin"
    fake_bin.mkdir()
    python_wrapper = fake_bin / "python3"
    python_wrapper.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then exit 0; fi\n'
        'exec "$REAL_PYTHON" "$@"\n',
        encoding="utf-8",
    )
    python_wrapper.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    environment["REAL_PYTHON"] = sys.executable
    return subprocess.run(
        ["sh", "install.sh"],
        cwd=plugin_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX installer safety regression")
def test_installer_rejects_symlinked_data_directory_without_mutating_target(tmp_path):
    plugin_root = tmp_path / "plugin"
    external_data = tmp_path / "external-data"
    plugin_root.mkdir()
    external_data.mkdir(mode=0o755)
    (plugin_root / "data").symlink_to(external_data, target_is_directory=True)

    result = _run_installer(plugin_root)

    assert result.returncode != 0
    assert stat.S_IMODE(external_data.stat().st_mode) == 0o755


@pytest.mark.skipif(os.name != "posix", reason="POSIX installer safety regression")
@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_installer_does_not_mutate_linked_bootstrap_target(tmp_path, link_kind):
    plugin_root = tmp_path / "plugin"
    data_directory = plugin_root / "data"
    external_bootstrap = tmp_path / "external-bootstrap.json"
    data_directory.mkdir(parents=True, mode=0o700)
    external_bootstrap.write_text("{}", encoding="utf-8")
    external_bootstrap.chmod(0o644)
    bootstrap = data_directory / "apc-bootstrap.json"
    if link_kind == "symlink":
        bootstrap.symlink_to(external_bootstrap)
    else:
        os.link(external_bootstrap, bootstrap)

    result = _run_installer(plugin_root)

    assert result.returncode == 0
    assert stat.S_IMODE(external_bootstrap.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name != "posix", reason="POSIX installer safety regression")
def test_installer_rejects_data_path_replacement_during_setup(tmp_path, monkeypatch):
    plugin_root = tmp_path / "plugin"
    data_directory = plugin_root / "data"
    external_data = tmp_path / "external-data"
    data_directory.mkdir(parents=True, mode=0o755)
    external_data.mkdir(mode=0o755)
    original_fchmod = installer.os.fchmod

    def replace_data_path(descriptor, mode):
        original_fchmod(descriptor, mode)
        data_directory.rename(plugin_root / "opened-data")
        data_directory.symlink_to(external_data, target_is_directory=True)

    monkeypatch.setattr(installer.os, "fchmod", replace_data_path)

    with pytest.raises(installer.InstallerSafetyError):
        installer.prepare_data_directory(plugin_root)

    assert stat.S_IMODE(external_data.stat().st_mode) == 0o755


def test_server_manifest_is_pg3x_installable():
    manifest = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))

    assert manifest["type"] == "python3"
    assert manifest["executable"] == "apc-poly.py"
    assert manifest["install"] == "install.sh"
    assert manifest["testMode"] is False
    assert int(manifest["shortPoll"]) < int(manifest["longPoll"])
    assert "password" not in json.dumps(manifest).lower()
    assert "callback_host" in manifest["customParams"]
    assert (ROOT / manifest["executable"]).is_file()
    assert (ROOT / manifest["install"]).is_file()


def test_supported_pg3x_version_is_0_1_2_everywhere():
    manifest = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")

    assert project["project"]["version"] == "0.1.2"
    assert apc_pg3x.__version__ == "0.1.2"
    assert manifest["profile_version"] == "0.1.2"
    assert {credit["version"] for credit in manifest["credits"]} == {"0.1.2"}
    assert 'name = "apc-ph6u4x32-pg3x"\nversion = "0.1.2"' in lock


def test_bootstrap_path_is_canonical_and_not_user_editable():
    manifest = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))

    assert "bootstrap_config_path" not in manifest["customParams"]
    assert "nsdata" not in json.dumps(manifest).lower()


def test_profile_defines_controller_and_confirmed_switch_nodes():
    root = ElementTree.parse(ROOT / "profile" / "nodedef" / "nodedefs.xml").getroot()
    definitions = {node.attrib["id"]: node for node in root.findall("nodeDef")}

    assert {"controller", "apcswitch"} <= definitions.keys()
    accepted = {
        command.attrib["id"]
        for command in definitions["apcswitch"].findall("./cmds/accepts/cmd")
    }
    statuses = {
        status.attrib["id"]
        for status in definitions["apcswitch"].findall("./sts/st")
    }

    assert {"DON", "DOF", "QUERY"} <= accepted
    assert {"ST", "GV0", "GV1", "GV2"} <= statuses


def test_profile_xml_files_are_well_formed():
    ElementTree.parse(ROOT / "profile" / "nodedef" / "nodedefs.xml")
    ElementTree.parse(ROOT / "profile" / "editor" / "editors.xml")


def test_runtime_has_no_home_assistant_or_external_mqtt_dependency():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()

    assert "homeassistant" not in requirements + project
    assert "paho" not in requirements + project
    assert "mqtt" not in requirements + project


def test_local_store_payload_contains_runtime_profile_and_operator_docs():
    required = [
        "apc-poly.py",
        "install.sh",
        "requirements.txt",
        "server.json",
        "POLYGLOT_CONFIG.md",
        "profile/nodedef/nodedefs.xml",
        "profile/editor/editors.xml",
        "profile/nls/en_us.txt",
        "src/apc_pg3x/pg3.py",
        "src/apc_pg3x/runtime.py",
        "src/apc_pg3x/installer.py",
        "src/apc_pg3x/callback_server.py",
        "src/apc_pg3x/protocol.py",
    ]
    assert all((ROOT / name).is_file() for name in required)
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "pip install --user --no-deps ." in install


def test_runtime_sources_are_lf_only_and_checkout_policy_preserves_them():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    shipped_text = [
        ROOT / ".gitattributes",
        ROOT / "install.sh",
        ROOT / "hatch_build.py",
        ROOT / "pyproject.toml",
        ROOT / "requirements.txt",
        ROOT / "server.json",
        ROOT / "uv.lock",
        *(ROOT / "profile").rglob("*.xml"),
        *(ROOT / "profile").rglob("*.txt"),
        *ROOT.glob("*.py"),
        *(ROOT / "build_tools").rglob("*.py"),
        *(ROOT / "src").rglob("*.py"),
        *(ROOT / "tests").rglob("*.py"),
        *ROOT.glob("*.md"),
        *(ROOT / "docs").rglob("*.md"),
    ]

    crlf_files = [path.relative_to(ROOT) for path in shipped_text if b"\x0d\x0a" in path.read_bytes()]
    assert crlf_files == []
    for pattern in ("*.json", "*.md", "*.py", "*.sh", "*.toml", "*.txt", "*.xml", "uv.lock"):
        assert f"{pattern} text eol=lf" in attributes.splitlines()


def test_operator_docs_distinguish_connectivity_from_telemetry_freshness():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "retained until a transport failure" in readme
    assert "Connected` can therefore be true while `State Stale` is true" in readme


def test_operator_docs_define_canonical_upload_and_truthful_lifecycle():
    config_doc = (ROOT / "POLYGLOT_CONFIG.md").read_text(encoding="utf-8")
    normalized_config_doc = " ".join(config_doc.split())

    assert "data/apc-bootstrap.json" in config_doc
    assert "Persistent Folder" in config_doc
    assert "Plugin Custom Data" in config_doc
    assert "retained" in config_doc
    assert "restart" in config_doc
    assert "0600" in config_doc
    assert "secure erasure" in config_doc
    assert "staging" in config_doc
    assert "backup" in config_doc
    assert "resurrect" in config_doc
    assert "top-level" in config_doc
    assert "Local Store purchase-option version" in config_doc
    assert "`0.1.2`" in config_doc
    assert "BOOTSTRAP_OWNER" in config_doc
    assert "CALLBACK_HOST" in config_doc
    assert "custom parameters persist only non-secret timing/network settings" in normalized_config_doc
    assert "canonical bootstrap path is fixed in code" in normalized_config_doc
    assert "custom parameters persist only the protected file path" not in normalized_config_doc


def test_sdist_preserves_pg3_entrypoint_executable_modes(tmp_path):
    subprocess.run(
        ["uv", "build", "--sdist", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
    )
    (sdist,) = tmp_path.glob("*.tar.gz")

    with tarfile.open(sdist, "r:gz") as archive:
        modes = {
            Path(member.name).name: member.mode & 0o777
            for member in archive.getmembers()
            if Path(member.name).name in {"apc-poly.py", "install.sh"}
        }

    assert modes == {"apc-poly.py": 0o755, "install.sh": 0o755}


def test_wheel_normalization_is_host_independent_and_canonical(tmp_path):
    from build_tools.wheel_normalizer import normalize_wheel

    hashes = []
    for create_system in (0, 3):
        wheel = tmp_path / f"host-{create_system}.whl"
        with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in (
                ("apc_pg3x/module.py", b"VALUE = 1\n"),
                ("apc_pg3x-0.1.1.dist-info/RECORD", b"record\n"),
            ):
                member = zipfile.ZipInfo(name, (2026, 9, 20, 15, 36, 42))
                member.create_system = create_system
                member.external_attr = 0 if create_system == 0 else 0o100644 << 16
                archive.writestr(member, payload, compress_type=zipfile.ZIP_DEFLATED)

        normalize_wheel(wheel, timestamp=1_789_942_602)
        hashes.append(hashlib.sha256(wheel.read_bytes()).hexdigest())

        with zipfile.ZipFile(wheel) as archive:
            members = archive.infolist()
            assert [member.filename for member in members] == [
                "apc_pg3x/module.py",
                "apc_pg3x-0.1.1.dist-info/RECORD",
            ]
            assert all(member.create_system == 3 for member in members)
            assert all(member.external_attr >> 16 == 0o100644 for member in members)
            assert all(member.compress_type == zipfile.ZIP_STORED for member in members)

    assert hashes[0] == hashes[1]


def test_release_build_backend_is_pinned():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["build-system"]["requires"] == ["hatchling==1.32.3"]


def test_built_wheel_has_canonical_zip_metadata(tmp_path):
    hashes = []
    for build_number in range(2):
        output = tmp_path / str(build_number)
        environment = os.environ.copy()
        environment["SOURCE_DATE_EPOCH"] = "1789942602"
        subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(output)],
            cwd=ROOT,
            env=environment,
            check=True,
        )
        (wheel,) = output.glob("*.whl")
        hashes.append(hashlib.sha256(wheel.read_bytes()).hexdigest())

        with zipfile.ZipFile(wheel) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            assert names[-1].endswith(".dist-info/RECORD")
            assert names[:-1] == sorted(names[:-1])
            assert all(member.create_system == 3 for member in members)
            assert all(member.external_attr >> 16 == 0o100644 for member in members)
            assert all(member.compress_type == zipfile.ZIP_STORED for member in members)
            assert {member.date_time for member in members} == {(2026, 9, 20, 22, 16, 42)}

    assert hashes[0] == hashes[1]
