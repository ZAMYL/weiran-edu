import socket

import pytest


@pytest.fixture(autouse=True)
def isolate_database_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    # A developer's configured database must never be opened by the test suite.
    monkeypatch.delenv("WEIRAN_EDU_DATABASE", raising=False)


@pytest.fixture(autouse=True)
def block_external_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def require_loopback(address: tuple[object, ...] | str) -> None:
        # Windows asyncio implements socketpair through literal loopback TCP connections.
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            raise AssertionError("External network connections are disabled in offline tests")

    def connect(sock: socket.socket, address: tuple[object, ...] | str) -> None:
        require_loopback(address)
        return original_connect(sock, address)

    def connect_ex(sock: socket.socket, address: tuple[object, ...] | str) -> int:
        require_loopback(address)
        return original_connect_ex(sock, address)

    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("External network connections are disabled in offline tests")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", denied)
