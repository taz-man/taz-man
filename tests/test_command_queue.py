from apc_pg3x.commands import CommandQueue


def test_fetch_does_not_discard_unconfirmed_command():
    queue = CommandQueue()
    queue.put("outlet_1", True, now=10.0)

    first = queue.payload(sequence=1, now=10.5)
    second = queue.payload(sequence=2, now=11.0)

    assert first["seq_no"] == 1
    assert second["seq_no"] == 2
    assert first["data"] == second["data"]
    assert queue.pending_count == 1
    assert queue.attempts("outlet_1") == 2


def test_matching_device_report_removes_command():
    queue = CommandQueue()
    queue.put("outlet_1", True, now=10.0)

    confirmed = queue.confirm("outlet_1", True)

    assert confirmed is True
    assert queue.pending_count == 0


def test_mismatched_device_report_keeps_command_pending():
    queue = CommandQueue()
    queue.put("outlet_1", False, now=10.0)

    confirmed = queue.confirm("outlet_1", True)

    assert confirmed is False
    assert queue.pending_count == 1


def test_new_command_for_property_supersedes_old_intent():
    queue = CommandQueue()
    queue.put("outlet_1", True, now=10.0)
    queue.put("outlet_1", False, now=11.0)

    payload = queue.payload(sequence=1, now=11.5)

    assert queue.pending_count == 1
    assert payload["data"]["properties"][0]["property"]["value"] is False
