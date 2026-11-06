"""Regression tests for the suite-wide network isolation fixture."""

import socket

import pytest


NETWORK_BLOCK_MESSAGE = "Network access is disabled during STMAA tests"


@pytest.mark.parametrize("method_name", ["connect", "connect_ex"])
def test_socket_connection_methods_are_blocked(method_name):
    sock = socket.socket()
    try:
        with pytest.raises(RuntimeError, match=NETWORK_BLOCK_MESSAGE):
            getattr(sock, method_name)(("127.0.0.1", 9))
    finally:
        sock.close()


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("send", (b"x",)),
        ("sendall", (b"x",)),
        ("sendto", (b"x", ("127.0.0.1", 9))),
    ],
)
def test_socket_send_methods_are_blocked(method_name, args):
    sock = socket.socket()
    try:
        with pytest.raises(RuntimeError, match=NETWORK_BLOCK_MESSAGE):
            getattr(sock, method_name)(*args)
    finally:
        sock.close()


def test_socket_sendmsg_is_blocked_when_available():
    if not hasattr(socket.socket, "sendmsg"):
        pytest.skip("socket.sendmsg is not available on this platform")

    sock = socket.socket()
    try:
        with pytest.raises(RuntimeError, match=NETWORK_BLOCK_MESSAGE):
            sock.sendmsg([b"x"])
    finally:
        sock.close()


def test_socket_create_connection_is_blocked():
    with pytest.raises(RuntimeError, match=NETWORK_BLOCK_MESSAGE):
        socket.create_connection(("127.0.0.1", 9), timeout=0.1)


def test_dns_resolution_is_blocked():
    with pytest.raises(RuntimeError, match=NETWORK_BLOCK_MESSAGE):
        socket.getaddrinfo("localhost", 80)
