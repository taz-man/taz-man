import json
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
    assert {"ST", "GV0", "GV1"} <= statuses


def test_profile_xml_files_are_well_formed():
    ElementTree.parse(ROOT / "profile" / "nodedef" / "nodedefs.xml")
    ElementTree.parse(ROOT / "profile" / "editor" / "editors.xml")
