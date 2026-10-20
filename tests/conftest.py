import socket

import pytest


@pytest.fixture(autouse=True)
def _block_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Enforce offline tests: any socket connect fails unless the test is marked integration."""
    if request.node.get_closest_marker("integration"):
        return

    def guard(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Network access attempted in an offline test")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)
    monkeypatch.setattr(socket, "getaddrinfo", guard)  # DNS lookups count as network too
