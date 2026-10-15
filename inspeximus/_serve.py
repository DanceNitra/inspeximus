"""The one way inspeximus serves HTTP: a thread per request, a read timeout, a deadline, and caps on connections.

NO SERVER WAITS ON A CLIENT WITHOUT A BOUND (3.20, AUDIT-A N-11). SCRAPI, the witness server and the checkpoint
witness each ran a threading HTTP server with no socket timeout and no limit on threads. 300 connections that sent
their headers and one byte of a 1,000-byte body held 300 server threads for as long as the client kept them open
(AUDIT-A, dr/ss5/slowloris.py, on dcce3660). Now:

- every read on a connection gives up after READ_TIMEOUT seconds, and the connection is closed;
- every request is closed REQUEST_DEADLINE seconds after it was accepted, however it is sending (AUDIT-A N-14). The
  read timeout alone is per read, so 64 connections that each sent one byte every few seconds held every slot for as
  long as they kept sending, and an honest client got 503 meanwhile (dr/ss5/serve_cap.py, on d0e5b2ed);
- at most MAX_CONNECTIONS requests are handled at once, and at most MAX_PER_ADDRESS from one client address, so one
  address cannot take every slot (N-14). A connection over either cap is answered 503 and closed without a thread.

BOTH CAPS ARE SETTABLE (3.20, AUDIT-A section 7). Behind a reverse proxy every request comes from the proxy's
address, so MAX_PER_ADDRESS bounds all users together and one client behind the proxy can hold every slot. A proxied
deployment sets `max_per_address=0`, which turns that cap off. The address is the socket's peer: X-Forwarded-For is
never read, because a client can write it. Standard library only, imported when a server is made, so
`import inspeximus` does not load http.server.
"""
READ_TIMEOUT = 10.0
REQUEST_DEADLINE = 30.0
MAX_CONNECTIONS = 64
MAX_PER_ADDRESS = 16

_BUSY = (b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain\r\nContent-Length: 20\r\n"
         b"Connection: close\r\nRetry-After: 1\r\n\r\ntoo many connections")


def make_http_server(address, handler_class, max_connections=None, read_timeout=None, max_per_address=None):
    """A ThreadingHTTPServer on `address` for `handler_class`, with a read timeout, a deadline and connection caps.

    `max_connections`, `read_timeout` and `max_per_address` default to MAX_CONNECTIONS, READ_TIMEOUT and
    MAX_PER_ADDRESS; REQUEST_DEADLINE is read too, all when the server is made. `max_per_address=0` turns the
    per-address cap off. The handler class is subclassed with `timeout` set, which StreamRequestHandler applies to
    the socket."""
    import socket
    import sys
    import threading
    import time
    from http.server import ThreadingHTTPServer

    cap = int(MAX_CONNECTIONS if max_connections is None else max_connections)
    wait = float(READ_TIMEOUT if read_timeout is None else read_timeout)
    deadline = float(REQUEST_DEADLINE)
    per_address = int(MAX_PER_ADDRESS if max_per_address is None else max_per_address)
    if cap < 1 or wait <= 0 or deadline <= 0 or per_address < 0:
        raise ValueError("max_connections must be at least 1, max_per_address 0 (off) or more, and the timeout and "
                         "the deadline above 0")
    handler = type(handler_class.__name__, (handler_class,), {"timeout": wait})

    def _busy(request):
        try:
            request.settimeout(1.0)
            request.sendall(_BUSY)
        except OSError:
            pass

    class BoundedThreadingHTTPServer(ThreadingHTTPServer):
        daemon_threads = True

        def __init__(self, *a, **k):
            self._slots = threading.BoundedSemaphore(cap)
            self._lock = threading.Lock()
            self._by_address = {}            # client address -> requests in progress
            self._started = {}               # request socket -> when it was accepted
            self._closing = threading.Event()
            super().__init__(*a, **k)
            # ONE thread ends every request past its deadline, by shutting its socket down: the handler's next read
            # then returns nothing and the handler ends. A timer per request would double the thread count.
            threading.Thread(target=self._reap, name="inspeximus-serve-deadline", daemon=True).start()

        def _reap(self):
            while not self._closing.wait(min(1.0, deadline / 4)):
                now = time.monotonic()
                with self._lock:
                    late = [r for r, t in self._started.items() if now - t > deadline]
                for r in late:
                    try:
                        r.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        def process_request(self, request, client_address):
            if not self._slots.acquire(blocking=False):
                _busy(request)
                self.shutdown_request(request)
                return
            host = client_address[0] if isinstance(client_address, tuple) else client_address
            with self._lock:
                full = per_address > 0 and self._by_address.get(host, 0) >= per_address
                if not full:
                    self._by_address[host] = self._by_address.get(host, 0) + 1
                    self._started[request] = time.monotonic()
            if full:
                self._slots.release()
                _busy(request)
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self._slots.release()
                self._leave(request, host)
                raise

        def _leave(self, request, host):
            with self._lock:
                self._started.pop(request, None)
                left = self._by_address.get(host, 1) - 1
                if left > 0:
                    self._by_address[host] = left
                else:
                    self._by_address.pop(host, None)

        def process_request_thread(self, request, client_address):
            host = client_address[0] if isinstance(client_address, tuple) else client_address
            try:
                super().process_request_thread(request, client_address)
            finally:
                self._slots.release()
                self._leave(request, host)

        def server_close(self):
            self._closing.set()
            super().server_close()

        def handle_error(self, request, client_address):
            # A client that stopped sending is the case this class exists for, not a server error.
            if isinstance(sys.exc_info()[1], (socket.timeout, TimeoutError, ConnectionError)):
                return
            super().handle_error(request, client_address)

    return BoundedThreadingHTTPServer(address, handler)
