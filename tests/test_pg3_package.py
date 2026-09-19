import json
import subprocess
import tarfile
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).parents[1]


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
        "src/apc_pg3x/callback_server.py",
        "src/apc_pg3x/protocol.py",
    ]
    assert all((ROOT / name).is_file() for name in required)
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "pip install --user --no-deps ." in install


def test_runtime_scripts_are_lf_only_and_checkout_policy_preserves_them():
    install = (ROOT / "install.sh").read_bytes()
    build_hook = (ROOT / "hatch_build.py").read_bytes()
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert b"\x0d\x0a" not in install
    assert b"\x0d\x0a" not in build_hook
    assert "*.sh text eol=lf" in attributes.splitlines()
    assert "hatch_build.py text eol=lf" in attributes.splitlines()


def test_operator_docs_distinguish_connectivity_from_telemetry_freshness():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "retained until a transport failure" in readme
    assert "Connected` can therefore be true while `State Stale` is true" in readme


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
