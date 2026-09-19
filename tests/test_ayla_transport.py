import logging
import time
from threading import Event

import pytest
import requests

from apc_pg3x.transport import AylaLanTransport, ProtocolError


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, json_error=None):
        self._payload = payload
        self.status_code = status_code
        self._json_error = json_error

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"response body contains super-secret: {self.status_code}")

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


class FakeHttp:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def post(self, url, *, json, timeout):
        self.calls.append((url, json, timeout))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SlowHttp:
    def __init__(self):
        self.closed = Event()

    def post(self, url, *, json, timeout):
        self.closed.wait(1.0)
        return FakeResponse({"local_reg": {"status": "success"}})

    def close(self):
        self.closed.set()


def test_registration_uses_explicit_deadline_and_expected_route():
    http = FakeHttp([FakeResponse({"local_reg": {"status": "success"}})])
    transport = AylaLanTransport(http=http, request_timeout=2.5)

    transport.register(
        address="strip.local",
        callback_host="controller.local",
        callback_port=10275,
        notify=True,
    )

    assert http.calls == [
        (
            "http://strip.local/local_reg.json",
            {
                "local_reg": {
                    "ip": "controller.local",
                    "port": 10275,
                    "uri": "/local_lan",
                    "notify": 1,
                }
            },
            (2.5, 2.5),
        )
    ]


def test_registration_timeout_is_wrapped_without_leaking_payload(caplog):
    http = FakeHttp([requests.Timeout("super-secret lanip_key=abc")])
    transport = AylaLanTransport(http=http, request_timeout=1.0)

    with caplog.at_level(logging.WARNING), pytest.raises(ProtocolError, match="registration failed"):
        transport.register(
            address="private-device.local",
            callback_host="controller.local",
            callback_port=10275,
            notify=False,
        )

    assert "super-secret" not in caplog.text
    assert "abc" not in caplog.text
    assert "private-device.local" not in caplog.text


def test_registration_rejects_malformed_response():
    http = FakeHttp([FakeResponse({"unexpected": True})])
    transport = AylaLanTransport(http=http, request_timeout=1.0)

    with pytest.raises(ProtocolError, match="malformed registration response"):
        transport.register(
            address="strip.local",
            callback_host="controller.local",
            callback_port=10275,
            notify=False,
        )


def test_unexpected_http_failure_is_sanitized(caplog):
    http = FakeHttp([RuntimeError("token=do-not-log-me")])
    transport = AylaLanTransport(http=http, request_timeout=1.0)

    with caplog.at_level(logging.WARNING), pytest.raises(ProtocolError, match="registration failed") as error:
        transport.register(
            address="strip.local",
            callback_host="controller.local",
            callback_port=10275,
            notify=False,
        )

    assert "do-not-log-me" not in str(error.value)
    assert "do-not-log-me" not in caplog.text


def test_registration_has_a_total_wall_clock_deadline():
    http = SlowHttp()
    transport = AylaLanTransport(http=http, request_timeout=0.05)

    started = time.monotonic()
    with pytest.raises(ProtocolError, match="registration failed"):
        transport.register(
            address="strip.local",
            callback_host="controller.local",
            callback_port=10275,
            notify=False,
        )
    elapsed = time.monotonic() - started

    assert elapsed < 0.25
    assert http.closed.is_set()
