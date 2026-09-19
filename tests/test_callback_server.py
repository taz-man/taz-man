# ruff: noqa: I001
import base64
import http.client
import json
import socket
import time

from apc_pg3x.callback_server import AylaCallbackServer
from apc_pg3x.device import AylaLanRouter
from apc_pg3x.protocol import AylaCallbackProtocol, SessionCodec
from test_ayla_device import make_device
from test_ayla_protocol import CONTROLLER_RANDOM, DEVICE_RANDOM, LAN_KEY, datapoint_body


def test_threaded_callback_server_runs_authenticated_exchange_and_datapoint():
    device = make_device(lan_key=LAN_KEY, address="127.0.0.1")
    router = AylaLanRouter()
    router.add(device)
    protocol = AylaCallbackProtocol(
        router,
        random_bytes=lambda size: CONTROLLER_RANDOM[:size],
        time_value=lambda: 456,
        max_body=4096,
    )
    server = AylaCallbackServer(
        protocol, host="127.0.0.1", port=0, max_body=4096, clock=lambda: 10.0
    )
    server.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=2)
    try:
        exchange_body = json.dumps(
            {
                "key_id": "key-one",
                "random": base64.b64encode(DEVICE_RANDOM).decode("ascii"),
                "time": 123,
            }
        )
        connection.request(
            "POST",
            "/local_lan/key_exchange.json",
            body=exchange_body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        exchange = json.loads(response.read())
        assert response.status == 200

        peer = SessionCodec.from_exchange(
            LAN_KEY,
            device_random=DEVICE_RANDOM,
            device_time=123,
            controller_random=base64.b64decode(exchange["random"]),
            controller_time=exchange["time"],
            max_body=4096,
        )
        connection.request(
            "POST",
            "/local_lan/property/datapoint.json",
            body=datapoint_body(peer, True),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        response.read()

        assert response.status == 200
        assert device.state("outlet_1").actual is True
    finally:
        connection.close()
        server.close()


def test_callback_server_rejects_oversized_body_before_protocol_processing():
    router = AylaLanRouter()
    protocol = AylaCallbackProtocol(
        router,
        random_bytes=lambda size: b"x" * size,
        time_value=lambda: 1,
        max_body=4096,
    )
    server = AylaCallbackServer(protocol, host="127.0.0.1", port=0, max_body=4096)
    server.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=2)
    try:
        connection.request(
            "POST",
            "/local_lan/key_exchange.json",
            body=b"x" * 4097,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 413
    finally:
        connection.close()
        server.close()


def test_callback_server_times_out_incomplete_request_body():
    protocol = AylaCallbackProtocol(
        AylaLanRouter(),
        random_bytes=lambda size: b"x" * size,
        time_value=lambda: 1,
    )
    server = AylaCallbackServer(
        protocol,
        host="127.0.0.1",
        port=0,
        read_timeout=0.05,
        max_workers=1,
    )
    server.start()
    client = socket.create_connection(server.server_address, timeout=2)
    try:
        client.sendall(
            b"POST /local_lan/key_exchange.json HTTP/1.1\r\n"
            b"Host: localhost\r\nContent-Length: 10\r\n\r\n"
        )
        response = client.recv(4096)
        assert b" 408 " in response
    finally:
        client.close()
        server.close()


def test_callback_server_enforces_absolute_request_deadline():
    protocol = AylaCallbackProtocol(
        AylaLanRouter(),
        random_bytes=lambda size: b"x" * size,
        time_value=lambda: 1,
    )
    server = AylaCallbackServer(
        protocol,
        host="127.0.0.1",
        port=0,
        read_timeout=1.0,
        request_deadline=0.05,
        max_workers=1,
    )
    server.start()
    client = socket.create_connection(server.server_address, timeout=2)
    started = time.monotonic()
    try:
        client.sendall(b"POST /local_lan/key_exchange.json HTTP/1.1\r\n")
        response = client.recv(4096)
        elapsed = time.monotonic() - started
        assert response == b""
        assert elapsed < 0.25
    finally:
        client.close()
        server.close()
