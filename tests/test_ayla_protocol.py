# ruff: noqa: I001
import base64
import json

import pytest

from apc_pg3x.device import AylaLanRouter
from apc_pg3x.protocol import AuthenticationError, AylaCallbackProtocol, SessionCodec
from apc_pg3x.transport import ProtocolError
from test_ayla_device import make_device


LAN_KEY = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode("ascii")
DEVICE_RANDOM = b"device-random-16"
CONTROLLER_RANDOM = b"controller-random"


def configured_device(*, address="one.local", on_state=None):
    return make_device(lan_key=LAN_KEY, address=address, on_state=on_state)


def establish_protocol(device):
    router = AylaLanRouter()
    router.add(device)
    protocol = AylaCallbackProtocol(
        router,
        random_bytes=lambda size: CONTROLLER_RANDOM[:size],
        time_value=lambda: 456,
        max_body=4096,
    )
    response = protocol.key_exchange(
        key_id="key-one",
        source_address="one.local",
        device_random=DEVICE_RANDOM,
        device_time=123,
        now=1.0,
    )
    peer = SessionCodec.from_exchange(
        LAN_KEY,
        device_random=DEVICE_RANDOM,
        device_time=123,
        controller_random=response["random"],
        controller_time=response["time"],
        max_body=4096,
    )
    return protocol, peer


def datapoint_body(peer, value, *, sequence=1):
    plaintext = json.dumps(
        {"properties": [{"property": {"name": "outlet_1", "value": value}}]},
        separators=(",", ":"),
    ).encode("utf-8")
    return peer.seal_device(plaintext, sequence=sequence)


def test_authenticated_datapoint_is_the_only_protocol_commit_path():
    reports = []
    device = configured_device(on_state=lambda name, value: reports.append((name, value)))
    protocol, peer = establish_protocol(device)

    protocol.receive_datapoint(
        key_id="key-one",
        source_address="one.local",
        body=datapoint_body(peer, True),
        now=2.0,
    )

    assert device.state("outlet_1").actual is True
    assert reports == [("outlet_1", True)]


def test_signature_tamper_does_not_mutate_state():
    device = configured_device()
    protocol, peer = establish_protocol(device)
    envelope = json.loads(datapoint_body(peer, True))
    signature = bytearray(base64.b64decode(envelope["signature"]))
    signature[0] ^= 1
    envelope["signature"] = base64.b64encode(signature).decode("ascii")

    with pytest.raises(AuthenticationError, match="authentication failed"):
        protocol.receive_datapoint(
            key_id="key-one",
            source_address="one.local",
            body=json.dumps(envelope).encode("utf-8"),
            now=2.0,
        )

    assert device.state("outlet_1").actual is None


def test_replay_and_oversized_callback_are_rejected():
    device = configured_device()
    protocol, peer = establish_protocol(device)
    body = datapoint_body(peer, True)
    protocol.receive_datapoint(
        key_id="key-one", source_address="one.local", body=body, now=2.0
    )

    with pytest.raises(AuthenticationError, match="replay"):
        protocol.receive_datapoint(
            key_id="key-one", source_address="one.local", body=body, now=3.0
        )
    with pytest.raises(AuthenticationError, match="body too large"):
        protocol.receive_datapoint(
            key_id="key-one", source_address="one.local", body=b"x" * 4097, now=4.0
        )


def test_command_envelope_is_per_device_and_authenticated():
    device = configured_device()
    protocol, peer = establish_protocol(device)
    device.request("outlet_1", True, now=2.0)

    body = protocol.fetch_commands(
        key_id="key-one", source_address="one.local", now=2.1
    )
    payload = json.loads(peer.open_controller(body))

    assert payload["data"]["properties"][0]["property"]["name"] == "outlet_1"
    assert payload["data"]["properties"][0]["property"]["value"] is True


def test_device_config_repr_redacts_lan_key():
    device = configured_device()

    assert LAN_KEY not in repr(device.config)
    assert "lan_key" not in repr(device.config)


def test_invalid_key_exchange_does_not_mark_device_connected():
    device = configured_device()
    router = AylaLanRouter()
    router.add(device)
    protocol = AylaCallbackProtocol(
        router,
        random_bytes=lambda size: CONTROLLER_RANDOM[:size],
        time_value=lambda: 456,
    )

    with pytest.raises(AuthenticationError, match="invalid key exchange"):
        protocol.key_exchange(
            key_id="key-one",
            source_address="one.local",
            device_random=b"",
            device_time=123,
            now=1.0,
        )

    assert device.connected is False
    assert device.last_seen_at is None


def test_rekey_invalidates_old_session_before_state_commit():
    device = configured_device()
    protocol, old_peer = establish_protocol(device)
    second_random = b"second-device-rnd"
    response = protocol.key_exchange(
        key_id="key-one",
        source_address="one.local",
        device_random=second_random,
        device_time=124,
        now=2.0,
    )
    new_peer = SessionCodec.from_exchange(
        LAN_KEY,
        device_random=second_random,
        device_time=124,
        controller_random=response["random"],
        controller_time=response["time"],
        max_body=4096,
    )

    with pytest.raises(AuthenticationError):
        protocol.receive_datapoint(
            key_id="key-one",
            source_address="one.local",
            body=datapoint_body(old_peer, True),
            now=3.0,
        )
    assert device.state("outlet_1").actual is None

    protocol.receive_datapoint(
        key_id="key-one",
        source_address="one.local",
        body=datapoint_body(new_peer, True),
        now=3.1,
    )
    assert device.state("outlet_1").actual is True


def test_authenticated_malformed_payload_does_not_consume_sequence():
    device = configured_device()
    protocol, peer = establish_protocol(device)
    malformed = peer.seal_device(b"{}", sequence=1)

    with pytest.raises(ProtocolError, match="malformed datapoint"):
        protocol.receive_datapoint(
            key_id="key-one",
            source_address="one.local",
            body=malformed,
            now=2.0,
        )

    protocol.receive_datapoint(
        key_id="key-one",
        source_address="one.local",
        body=datapoint_body(peer, True, sequence=1),
        now=2.1,
    )
    assert device.state("outlet_1").actual is True


def test_stale_session_source_does_not_make_reassigned_ip_ambiguous():
    first = configured_device(address="shared.local")
    router = AylaLanRouter()
    router.add(first)
    protocol = AylaCallbackProtocol(
        router,
        random_bytes=lambda size: CONTROLLER_RANDOM[:size],
        time_value=lambda: 456,
    )
    protocol.key_exchange(
        key_id="key-one",
        source_address="shared.local",
        device_random=DEVICE_RANDOM,
        device_time=123,
        now=1.0,
    )
    first.address = "new.local"

    second = make_device(
        dsn="DSN-TWO",
        key_id="key-two",
        lan_key=LAN_KEY,
        address="shared.local",
    )
    router.add(second)
    protocol.key_exchange(
        key_id="key-two",
        source_address="shared.local",
        device_random=b"second-device-rnd",
        device_time=124,
        now=2.0,
    )

    assert protocol.key_id_for_source("shared.local") == "key-two"
