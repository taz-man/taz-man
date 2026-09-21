"""Known-answer tests for the observed Ayla LAN wire contract.

The fixed ciphertext/signature values were independently derived from the
published protocol description and implementation at upstream commit
513c71157d5c03b89bab04b3ad3b6c2de8fd20b9 (AylaEncryption.py blob
 d9191d73b19bc7073a5f56e08cf3ca41b9dd3e87). Values use synthetic material;
no device identity or credential is present.
"""

import http.client
import json
import logging

import pytest
from test_ayla_device import make_device

from apc_pg3x.callback_server import AylaCallbackServer
from apc_pg3x.device import AylaLanRouter, RoutingError
from apc_pg3x.protocol import AuthenticationError, AylaCallbackProtocol, SessionCodec

LAN_KEY = "fixture-lan-key-001122334455"
DEVICE_RANDOM = "deviceRandom0001"
CONTROLLER_RANDOM = "serverRandom0002"
DEVICE_TIME = 1700000000123456
CONTROLLER_TIME = 1700000000654321

CONTROLLER_MESSAGES = (
    b'{"seq_no":1,"data":{}}',
    b'{"seq_no":2,"data":{"properties":[]}}',
)
CONTROLLER_ENVELOPES = (
    b'{"enc":"14d67anVMpJC1/5bYynFde93OwqYIS0bEvNWhdWCmAw=","sign":"+82N+U9JU86OdijFY35l6cCRYl3s94j1rDEyqT5kLg4="}',
    b'{"enc":"WvRP9P5Rztc57CLS4iUkRly5QflXn12KY2zuazRfokZuAyAeOMkQUoOy/BifDjqZ","sign":"+fq+dD5nVfm0ggRBIRGXi9cRmk06O9Vax8vkaXZoVg8="}',
)
DEVICE_MESSAGES = (
    b'{"seq_no":7,"data":{"name":"fixture_outlet","value":1}}',
    b'{"seq_no":8,"data":{"name":"fixture_outlet","value":0}}',
)
DEVICE_ENVELOPES = (
    b'{"enc":"80jU5249BTEWOC99AEQJDfS4wzcG94o0p5zlQ2w4CxG4N38A0GyvEI1YsvMPUiN8aG01xemWI7ctyM60L8/XBA==","sign":"Uu72IeQT6UdoEKd7VNonPmsjl4/ncZtVijjqYF4IXZE="}',
    b'{"enc":"qTxQPTjiP1nFhsfFUYh0vFdfV1hfaeqwPes6lqPjkn9HCuvJL/xAQr5XEdODv2ULroj2YK8JmRRhRZbyBl1bZw==","sign":"fCcZUH7OgWFmTUTkMFQjDW31zLFk3ntB3spildk8/MA="}',
)


def make_codec():
    return SessionCodec.from_exchange(
        LAN_KEY,
        device_random=DEVICE_RANDOM,
        device_time=DEVICE_TIME,
        controller_random=CONTROLLER_RANDOM,
        controller_time=CONTROLLER_TIME,
        max_body=4096,
    )


def test_controller_envelopes_match_external_known_answers_and_chain_cbc_state():
    codec = make_codec()

    assert codec.seal_controller(CONTROLLER_MESSAGES[0]) == CONTROLLER_ENVELOPES[0]
    assert codec.seal_controller(CONTROLLER_MESSAGES[1]) == CONTROLLER_ENVELOPES[1]


def test_device_envelopes_match_external_known_answers_and_verify_signatures():
    producer = make_codec()
    consumer = make_codec()

    assert producer.seal_device(DEVICE_MESSAGES[0]) == DEVICE_ENVELOPES[0]
    assert producer.seal_device(DEVICE_MESSAGES[1]) == DEVICE_ENVELOPES[1]
    assert consumer.open_device(DEVICE_ENVELOPES[0]) == DEVICE_MESSAGES[0]
    assert consumer.open_device(DEVICE_ENVELOPES[1]) == DEVICE_MESSAGES[1]


def test_device_signature_tamper_is_rejected():
    envelope = json.loads(DEVICE_ENVELOPES[0])
    envelope["sign"] = "Au72IeQT6UdoEKd7VNonPmsjl4/ncZtVijjqYF4IXZE="

    with pytest.raises(AuthenticationError, match="authentication failed"):
        make_codec().open_device(json.dumps(envelope).encode())


def test_callback_accepts_observed_nested_key_exchange_and_returns_top_level_pair(caplog):
    device = make_device(
        lan_key=LAN_KEY,
        key_id="fixture-key",
        address="127.0.0.1",
    )
    router = AylaLanRouter()
    router.add(device)
    protocol = AylaCallbackProtocol(
        router,
        random_bytes=lambda size: CONTROLLER_RANDOM.encode("ascii")[:size],
        time_value=lambda: CONTROLLER_TIME,
        max_body=4096,
    )
    server = AylaCallbackServer(protocol, host="127.0.0.1", port=0, max_body=4096)
    server.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=2)
    request = {
        "key_exchange": {
            "ver": 1,
            "proto": 1,
            "key_id": "fixture-key",
            "random_1": DEVICE_RANDOM,
            "time_1": DEVICE_TIME,
        }
    }
    try:
        with caplog.at_level(logging.INFO, logger="apc_pg3x.callback_server"):
            connection.request(
                "POST",
                "/local_lan/key_exchange.json",
                body=json.dumps(request),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read())

        assert response.status == 200
        assert payload == {
            "random_2": CONTROLLER_RANDOM,
            "time_2": CONTROLLER_TIME,
        }
        assert caplog.messages == [
            "Ayla LAN diagnostic stage=KEY_EXCHANGE reason=ACCEPTED"
        ]
    finally:
        connection.close()
        server.close()


def test_source_routing_rejection_logs_fixed_reason_without_source(caplog):
    protocol = AylaCallbackProtocol(
        AylaLanRouter(),
        random_bytes=lambda size: b"serverRandom0002"[:size],
        time_value=lambda: CONTROLLER_TIME,
    )

    with (
        caplog.at_level(logging.WARNING, logger="apc_pg3x.protocol"),
        pytest.raises(RoutingError),
    ):
        protocol.key_id_for_source("private-source.example")

    assert caplog.messages == [
        "Ayla LAN diagnostic stage=SOURCE_ROUTING reason=REJECTED"
    ]
    assert "private-source.example" not in caplog.text


def test_malformed_key_exchange_logs_fixed_rejection_without_payload(caplog):
    protocol = AylaCallbackProtocol(
        AylaLanRouter(),
        random_bytes=lambda size: b"serverRandom0002"[:size],
        time_value=lambda: CONTROLLER_TIME,
    )
    server = AylaCallbackServer(protocol, host="127.0.0.1", port=0)
    server.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=2)
    sensitive = "private-key-id-value"
    try:
        with caplog.at_level(logging.WARNING, logger="apc_pg3x.callback_server"):
            connection.request(
                "POST",
                "/local_lan/key_exchange.json",
                body=json.dumps({"key_exchange": {"key_id": sensitive}}),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            response.read()

        assert response.status == 400
        assert caplog.messages == [
            "Ayla LAN diagnostic stage=KEY_EXCHANGE reason=MALFORMED"
        ]
        assert sensitive not in caplog.text
    finally:
        connection.close()
        server.close()
