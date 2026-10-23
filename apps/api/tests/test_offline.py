import socket

import pytest


def test_network_helper_is_blocked_before_dns() -> None:
    with pytest.raises(AssertionError, match="External network"):
        socket.create_connection(("example.invalid", 443))


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_direct_external_socket_is_blocked(method: str) -> None:
    with socket.socket() as connection:
        with pytest.raises(AssertionError, match="External network"):
            getattr(connection, method)(("192.0.2.1", 443))
