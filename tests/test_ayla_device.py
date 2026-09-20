import logging

import pytest

from apc_pg3x.device import (
    AylaLanDevice,
    AylaLanRouter,
    DeviceConfig,
    DeviceLocation,
    RetryPolicy,
    RoutingError,
)
from apc_pg3x.transport import ProtocolError


class FakeTransport:
    def __init__(self, outcomes=()):
        self.outcomes = iter(outcomes)
        self.calls = []

    def register(self, **kwargs):
        self.calls.append(kwargs)
        try:
            outcome = next(self.outcomes)
        except StopIteration:
            outcome = None
        if isinstance(outcome, Exception):
            raise outcome


class FakeResolver:
    def __init__(self, locations=()):
        self.locations = iter(locations)
        self.calls = []

    def refresh(self, dsn, current_address):
        self.calls.append((dsn, current_address))
        try:
            outcome = next(self.locations)
        except StopIteration:
            return None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_device(
    *,
    dsn="DSN-ONE",
    key_id="key-one",
    lan_key="fixture-secret",
    address="one.local",
    transport=None,
    resolver=None,
    on_state=None,
):
    return AylaLanDevice(
        DeviceConfig(
            dsn=dsn,
            key_id=key_id,
            lan_key=lan_key,
            address=address,
            property_names=("outlet_1", "usb_1"),
        ),
        transport=transport or FakeTransport(),
        resolver=resolver or FakeResolver(),
        callback_host="controller.local",
        policy=RetryPolicy(command_timeout=8.0, max_attempts=3, backoff_base=1.0, backoff_cap=2.0, stale_after=30.0),
        on_state=on_state,
    )


def test_command_stays_uncommitted_until_matching_readback():
    reports = []
    device = make_device(on_state=lambda name, value: reports.append((name, value)))

    device.request("outlet_1", True, now=10.0)
    device.poll(now=10.0)
    payload = device.fetch_commands(source_address="one.local", now=10.1)

    assert payload["data"]["properties"][0]["property"]["value"] is True
    assert device.state("outlet_1").actual is None
    assert device.state("outlet_1").pending is True
    assert reports == []

    device.receive_datapoints({"outlet_1": True}, source_address="one.local", now=12.0)

    assert device.state("outlet_1").actual is True
    assert device.state("outlet_1").pending is False
    assert reports == [("outlet_1", True)]


def test_contradictory_readback_commits_truth_but_keeps_intent_pending():
    device = make_device()
    device.request("outlet_1", True, now=10.0)

    device.receive_datapoints({"outlet_1": False}, source_address="one.local", now=11.0)

    assert device.state("outlet_1").actual is False
    assert device.state("outlet_1").desired is True
    assert device.state("outlet_1").pending is True


def test_delayed_readback_before_deadline_confirms_command():
    device = make_device()
    device.request("outlet_1", True, now=10.0)
    device.poll(now=10.0)
    device.fetch_commands(source_address="one.local", now=10.1)
    device.poll(now=17.9)

    assert device.state("outlet_1").pending is True

    device.receive_datapoints({"outlet_1": True}, source_address="one.local", now=17.95)

    assert device.state("outlet_1").actual is True
    assert device.state("outlet_1").pending is False


def test_unsolicited_device_change_updates_confirmed_state():
    reports = []
    device = make_device(on_state=lambda name, value: reports.append((name, value)))

    device.receive_datapoints({"usb_1": True}, source_address="one.local", now=5.0)

    assert device.state("usb_1").actual is True
    assert device.state("usb_1").pending is False
    assert reports == [("usb_1", True)]


def test_router_never_falls_back_to_another_device():
    router = AylaLanRouter()
    one = make_device()
    two = make_device(dsn="DSN-TWO", key_id="key-two", address="two.local")
    router.add(one)
    router.add(two)
    one.request("outlet_1", True, now=0.0)
    two.request("outlet_1", False, now=0.0)

    with pytest.raises(RoutingError):
        router.fetch_commands(key_id="key-one", source_address="two.local", now=1.0)

    assert one.command_attempts("outlet_1") == 0
    assert two.command_attempts("outlet_1") == 0

    payload = router.fetch_commands(key_id="key-two", source_address="two.local", now=1.0)
    assert payload["data"]["properties"][0]["property"]["value"] is False
    assert one.command_attempts("outlet_1") == 0
    assert two.command_attempts("outlet_1") == 1


def test_transport_retries_and_backoff_are_bounded():
    transport = FakeTransport(
        [ProtocolError("secret-one"), ProtocolError("secret-two"), ProtocolError("secret-three")]
    )
    device = make_device(transport=transport)
    device.request("outlet_1", True, now=10.0)

    device.poll(now=10.0)
    device.poll(now=10.9)
    device.poll(now=11.0)
    device.poll(now=12.9)
    device.poll(now=13.0)
    device.poll(now=100.0)

    assert len([call for call in transport.calls if call["notify"]]) == 3
    assert device.pending_count == 0
    assert device.state("outlet_1").actual is None
    assert device.state("outlet_1").timed_out is True
    assert device.last_failure == "retry_exhausted"
    assert device.stale is True


def test_confirmation_deadline_clears_pending_without_changing_actual():
    device = make_device()
    device.receive_datapoints({"outlet_1": False}, source_address="one.local", now=1.0)
    device.request("outlet_1", True, now=10.0)

    device.poll(now=18.01)

    assert device.pending_count == 0
    assert device.state("outlet_1").actual is False
    assert device.state("outlet_1").timed_out is True
    assert device.last_failure == "confirmation_timeout"


def test_disconnect_refreshes_changed_ip_and_reconnects_only_matching_identity():
    transport = FakeTransport([ProtocolError("offline"), None])
    resolver = FakeResolver([DeviceLocation("DSN-ONE", "key-one", "new.local")])
    device = make_device(transport=transport, resolver=resolver)
    device.request("outlet_1", True, now=10.0)

    device.poll(now=10.0)

    assert device.connected is False
    assert device.address == "new.local"
    assert resolver.calls == [("DSN-ONE", "one.local")]

    device.poll(now=11.0)
    device.establish_session(source_address="new.local", now=11.1)
    device.receive_datapoints({"outlet_1": True}, source_address="new.local", now=11.2)

    assert transport.calls[-1]["address"] == "new.local"
    assert device.connected is True
    assert device.stale is False
    assert device.state("outlet_1").actual is True


def test_ip_refresh_rejects_location_for_wrong_device():
    transport = FakeTransport([ProtocolError("offline")])
    resolver = FakeResolver([DeviceLocation("DSN-TWO", "key-two", "wrong.local")])
    device = make_device(transport=transport, resolver=resolver)
    device.request("outlet_1", True, now=10.0)

    device.poll(now=10.0)

    assert device.address == "one.local"


def test_command_fetch_attempts_are_bounded_and_exhaustion_fails_pending():
    device = make_device()
    device.request("outlet_1", True, now=10.0)

    first = device.fetch_commands(source_address="one.local", now=10.1)
    second = device.fetch_commands(source_address="one.local", now=11.1)
    third = device.fetch_commands(source_address="one.local", now=13.1)
    exhausted = device.fetch_commands(source_address="one.local", now=15.1)

    assert all(result["data"] for result in (first, second, third))
    assert exhausted["data"] == {}
    assert device.state("outlet_1").pending is False
    assert device.state("outlet_1").actual is None
    assert device.last_failure == "retry_exhausted"


def test_malformed_datapoint_does_not_partially_mutate_state():
    device = make_device()

    with pytest.raises(ProtocolError, match="malformed datapoint"):
        device.receive_datapoints(
            {"outlet_1": True, "unknown": False},
            source_address="one.local",
            now=1.0,
        )

    assert device.state("outlet_1").actual is None


def test_failure_logs_do_not_contain_credentials_addresses_or_payloads(caplog):
    secret = "lanip_key=top-secret-token"
    transport = FakeTransport([ProtocolError(secret), ProtocolError(secret), ProtocolError(secret)])
    resolver = FakeResolver([RuntimeError(secret), RuntimeError(secret), RuntimeError(secret)])
    device = make_device(transport=transport, resolver=resolver)
    device.request("outlet_1", True, now=0.0)

    with caplog.at_level(logging.WARNING):
        device.poll(now=0.0)
        device.poll(now=1.0)
        device.poll(now=3.0)

    assert "top-secret-token" not in caplog.text
    assert "lanip_key" not in caplog.text
    assert "one.local" not in caplog.text
    assert "outlet_1" not in caplog.text


def test_confirmed_state_becomes_stale_without_telemetry_and_recovers():
    device = make_device()
    device.receive_datapoints({"outlet_1": False}, source_address="one.local", now=10.0)
    assert device.connected is True
    assert device.stale is False

    device.poll(now=40.01)
    assert device.stale is True
    assert device.state("outlet_1").actual is False

    device.establish_session(source_address="one.local", now=41.0)
    assert device.connected is True
    assert device.stale is True

    device.receive_datapoints({"usb_1": True}, source_address="one.local", now=41.1)
    assert device.connected is True
    assert device.stale is False


def test_registration_session_and_command_fetch_do_not_claim_confirmed_health():
    transport = FakeTransport()
    device = make_device(transport=transport)
    device.request("outlet_1", True, now=10.0)

    device.poll(now=10.0)
    device.establish_session(source_address="one.local", now=10.1)
    device.fetch_commands(source_address="one.local", now=10.2)

    assert transport.calls[-1]["notify"] is True
    assert device.connected is False
    assert device.stale is True
    assert device.last_seen_at is None


def test_idle_keepalive_recovers_dhcp_change_without_pending_command():
    transport = FakeTransport([ProtocolError("old address failed"), None])
    resolver = FakeResolver([DeviceLocation("DSN-ONE", "key-one", "new.local")])
    device = make_device(transport=transport, resolver=resolver)

    device.poll(now=0.0)
    assert device.connected is False
    assert device.address == "new.local"

    device.poll(now=1.0)
    assert transport.calls[-1]["address"] == "new.local"
    assert transport.calls[-1]["notify"] is False
    assert device.connected is False
    assert device.stale is True


def test_reporting_callback_failure_cannot_partially_commit_datapoints():
    reports = []

    def report(name, value):
        reports.append((name, value))
        if name == "outlet_1":
            raise RuntimeError("sensitive callback detail")

    device = make_device(on_state=report)

    device.receive_datapoints(
        {"outlet_1": True, "usb_1": False}, source_address="one.local", now=1.0
    )

    assert device.state("outlet_1").actual is True
    assert device.state("usb_1").actual is False
    assert reports == [("outlet_1", True), ("usb_1", False)]
