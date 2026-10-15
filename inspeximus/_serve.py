"""The one way inspeximus serves HTTP: a thread per request, a read timeout, and a cap on connections.

NO SERVER WAITS ON A CLIENT FOR EVER (3.20, AUDIT-A N-11). SCRAPI, the witness server and the checkpoint witness
each ran a threading HTTP server with no socket timeout and no limit on threads. 300 connections that sent their
headers and one byte of a 1,000-byte body held 300 server threads for as long as the client kept them open
(AUDIT-A, dr/ss5/slowloris.py, on dcce3660). Now:

- every read on a connection gives up after READ_TIMEOUT seconds, and the connection is closed;
- at most MAX_CONNECTIONS requests are handled at once. A connection over the cap is answered 503 and closed
  without a thread, so stalled clients cost at most MAX_CONNECTIONS threads, each for READ_TIMEOUT seconds per
  read.

A reverse proxy in front (the Caddyfile in deploy/ sets 5 s header and body timeouts) still helps; this is the floor
for a server exposed directly. Standard library only, imported when a server is made, so `import inspeximus` does
not load http.server.
"""
READ_TIMEOUT = 10.0
MAX_CONNECTIONS = 64

_BUSY = (b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain\r\nContent-Length: 20\r\n"
         b"Connection: close\r\nRetry-After: 1\r\n\r\ntoo many connections")


def make_http_server(address, handler_class, max_connections=None, read_timeout=None):
    """A ThreadingHTTPServer on `address` for `handler_class`, with a read timeout and a connection cap.

    `max_connections` and `read_timeout` default to MAX_CONNECTIONS and READ_TIMEOUT, read when the server is made.
    The handler class is subclassed with `timeout` set, which StreamRequestHandler applies to the socket."""
    import socket
    import sys
    import threading
    from http.server import ThreadingHTTPServer

    cap = int(MAX_CONNECTIONS if max_connections is None else max_connections)
    wait = float(READ_TIMEOUT if read_timeout is None else read_timeout)
    if cap < 1 or wait <= 0:
        raise ValueError("max_connections must be at least 1 and read_timeout above 0")
    handler = type(handler_class.__name__, (handler_class,), {"timeout": wait})

    class BoundedThreadingHTTPServer(ThreadingHTTPServer):
        daemon_threads = True

        def __init__(self, *a, **k):
            self._slots = threading.BoundedSemaphore(cap)
            super().__init__(*a, **k)

        def process_request(self, request, client_address):
            if not self._slots.acquire(blocking=False):
                try:
                    request.settimeout(1.0)
                    request.sendall(_BUSY)
                except OSError:
                    pass
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self._slots.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self._slots.release()

        def handle_error(self, request, client_address):
            # A client that stopped sending is the case this class exists for, not a server error.
            if isinstance(sys.exc_info()[1], (socket.timeout, TimeoutError, ConnectionError)):
                return
            super().handle_error(request, client_address)

    return BoundedThreadingHTTPServer(address, handler)
