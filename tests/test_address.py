from apc_pg3x.address import endpoint_address


def test_endpoint_address_is_stable_and_within_iox_limit():
    first = endpoint_address("AC000W000000001", "outlet_1")
    second = endpoint_address("AC000W000000001", "outlet_1")

    assert first == second
    assert len(first) <= 14
    assert first.isalnum()


def test_endpoint_address_separates_endpoints_and_devices():
    values = {
        endpoint_address("AC000W000000001", "outlet_1"),
        endpoint_address("AC000W000000001", "outlet_2"),
        endpoint_address("AC000W000000002", "outlet_1"),
    }

    assert len(values) == 3
