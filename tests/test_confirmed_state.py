from apc_pg3x.state import ConfirmedSwitch


def test_request_does_not_change_reported_state():
    switch = ConfirmedSwitch(actual=False)

    switch.request(True, now=10.0, timeout=5.0)

    assert switch.actual is False
    assert switch.desired is True
    assert switch.pending is True


def test_device_report_is_the_only_confirmation_source():
    switch = ConfirmedSwitch(actual=False)
    switch.request(True, now=10.0, timeout=5.0)

    confirmed = switch.report(True, now=11.0)

    assert confirmed is True
    assert switch.actual is True
    assert switch.pending is False
    assert switch.last_confirmed_at == 11.0


def test_mismatched_device_report_preserves_truth_and_pending_command():
    switch = ConfirmedSwitch(actual=True)
    switch.request(False, now=10.0, timeout=5.0)

    confirmed = switch.report(True, now=11.0)

    assert confirmed is False
    assert switch.actual is True
    assert switch.pending is True


def test_expired_command_fails_without_changing_actual_state():
    switch = ConfirmedSwitch(actual=False)
    switch.request(True, now=10.0, timeout=5.0)

    expired = switch.expire(now=15.1)

    assert expired is True
    assert switch.actual is False
    assert switch.pending is False
    assert switch.timed_out is True
