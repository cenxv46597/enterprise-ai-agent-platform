"""Permit one loopback browser server and one temporary eaap_test identity."""

from asyncio import BaseEventLoop
import socket
from unittest.mock import patch

from scripts.run_postgres_tests import PostgresGuard, ROLE_PATTERN


class HistoryAcceptanceGuard(PostgresGuard):
    """Keep the database/HTTP guards while allowing this server's inbound traffic.

    This does not allow an HTTP client, another listener, or an outgoing socket.
    The original guard's event-loop wakeup socketpair remains narrowly allowed.
    """

    def __init__(self, url, api_port):
        if (url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1"
                or url.database != "eaap_test" or not url.password or url.query
                or not ROLE_PATTERN.fullmatch(url.username or "")
                or type(url.port) is not int or not 1 <= url.port <= 65535
                or type(api_port) is not int or not 1 <= api_port <= 65535
                or api_port == url.port):
            raise ValueError("Invalid isolated browser target")
        super().__init__(url)
        self.api_port = api_port

    def _endpoint(self, address):
        return (isinstance(address, tuple) and len(address) == 2
                and address[0] == "127.0.0.1" and address[1] == self.api_port)

    def _server_socket(self, sock, *, accepted=False):
        try:
            if not self._endpoint(sock.getsockname()):
                return False
            return not accepted or sock.getpeername()[0] == "127.0.0.1"
        except OSError:
            return False

    def _socket_operation(self, original, *, internal_send=False):
        name = original.__name__

        def guarded(sock, *args, **kwargs):
            if (getattr(self._socketpair_state, "active", False)
                    or (internal_send and sock in self._socketpairs)):
                return original(sock, *args, **kwargs)
            permitted = (
                (name == "bind" and len(args) == 1 and self._endpoint(args[0]))
                or (name == "listen" and self._server_socket(sock))
                or (name in {"send", "sendall", "sendmsg"}
                    and self._server_socket(sock, accepted=True))
            )
            if not permitted:
                self._reject("out-of-scope browser socket")
            return original(sock, *args, **kwargs)

        return guarded

    def __enter__(self):
        create_server = BaseEventLoop.create_server
        getaddrinfo = socket.getaddrinfo
        super().__enter__()

        async def loopback_server(loop, factory, host=None, port=None, *args, **kwargs):
            supplied = kwargs.get("sock")
            if supplied is not None:
                allowed = host is None and port is None and self._server_socket(supplied)
            else:
                allowed = host == "127.0.0.1" and port == self.api_port
            if not allowed:
                self._reject("out-of-scope browser listener")
            return await create_server(loop, factory, host, port, *args, **kwargs)

        def loopback_resolution(host, port, *args, **kwargs):
            if not (getattr(self._socketpair_state, "active", False)
                    or (host == "127.0.0.1" and str(port) == str(self.api_port))):
                self._reject("out-of-scope browser resolution")
            return getaddrinfo(host, port, *args, **kwargs)

        try:
            self._patches.enter_context(patch.object(BaseEventLoop, "create_server", loopback_server))
            self._patches.enter_context(patch.object(socket, "getaddrinfo", loopback_resolution))
            return self
        except BaseException:
            self._patches.close()
            raise
