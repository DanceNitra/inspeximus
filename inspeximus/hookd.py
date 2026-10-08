"""A prompt daemon for the UserPromptSubmit hook (3.18 prototype, not released).

WHY. The prompt hook pays, on every prompt, for work on rows that did not change: opening the project store,
building its tokens, checking stamps, opening the decision store. Measured by AUDIT-B (design_hook_daemon_318.md):
about 1.3 s, of which a process that stays alive pays almost all once. This module is that process and its client.

THE DAEMON IS AN ACCELERATOR, NEVER A DEPENDENCY. The hook asks it first; on anything other than a verified,
timely, matching answer the hook runs today's path in its own process, and its output is exactly today's.

WHAT THE DAEMON TRUSTS, AND WHAT IT DOES NOT
  * The channel: an owner-scoped endpoint (a named pipe on Windows, a socket in a 0700 directory elsewhere), and a
    mutual HMAC handshake with a random token that only the daemon writes, into the user's key home. A client
    without the token gets no answer; a daemon without it (a squatted pipe name) gets no request, and its reply
    fails the client's check.
  * The store: the request carries the event, the working directory, and the store the hook resolved. The daemon
    resolves the store again itself, under its own environment, with the 3.16.5 vet, and answers only when both
    resolutions are the store it serves. It never opens a path a client names.
  * The code: the request carries the client's inspeximus version, guard-set hash and interpreter. A mismatch is
    refused and the daemon exits, so the next hook starts the right one. A different INSPEXIMUS_* environment is
    refused without an exit: that daemon still serves the hooks it was started for.
  * Freshness: before each answer the daemon computes the store's signature (the row store's write generation and
    the stat of the store and its sidecars) and reopens the store when it moved. A write the user made before the
    prompt is therefore in the answer, and an erasure cannot survive in the daemon's memory.

LIFETIME. Started by a hook that found none (opt-in for the prototype: INSPEXIMUS_HOOK_DAEMON=1), as a detached run
with the 3.16.4 launch rules (`_start_detached`: -E and the shim, breakaway, a new session), with the attempt
recorded. It exits after IDLE_EXIT_S without a request, when the store disappears, or on `--hookd-stop`.
"""
import hashlib
import hmac
import io
import json
import os
import sys
import threading
import time

PROTOCOL = 1
CONNECT_TIMEOUT_S = 0.1          #: a daemon that does not take the connection within this is treated as absent
REPLY_TIMEOUT_S = 0.3            #: the whole exchange, connect to verified reply; past it the hook runs today's path
SERVER_READ_TIMEOUT_S = 2.0      #: a client that stalls the handshake is dropped
IDLE_EXIT_S = 600.0              #: the daemon exits after this long without a request
START_MIN_INTERVAL_S = 60.0      #: at most one start attempt per store in this interval


# ── names: one daemon per (store, key home) ────────────────────────────────────────────────────────────────────
def _real(p):
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def key_home_for(store_path):
    from ._keyhome import key_home
    return key_home(store_path)


def tag(store_path, kh):
    """Sixteen hex characters naming the daemon for one store and one key home, by their real paths."""
    return hashlib.sha256(("%s\0%s" % (_real(store_path), _real(kh))).encode("utf-8")).hexdigest()[:16]


def state_dir(kh):
    return os.path.join(kh, "inspeximus", "hookd")


def _token_path(kh, t):
    return os.path.join(state_dir(kh), t + ".token")


def _pid_path(kh, t):
    return os.path.join(state_dir(kh), t + ".json")


def address(kh, t):
    """A named pipe on Windows. Elsewhere a socket in the key home, or, when that path is too long for AF_UNIX, in a
    per-user 0700 directory under the temp directory whose ownership is checked."""
    if os.name == "nt":
        return r"\\.\pipe\inspeximus-hookd-" + t
    p = os.path.join(state_dir(kh), t + ".sock")
    if len(p.encode("utf-8")) < 100:
        return p
    import tempfile
    d = os.path.join(tempfile.gettempdir(), "inspeximus-hookd-%d" % os.getuid())
    os.makedirs(d, mode=0o700, exist_ok=True)
    st = os.stat(d)
    if st.st_uid != os.getuid() or (st.st_mode & 0o077):
        raise PermissionError("%s is not this user's private directory" % d)
    return os.path.join(d, t + ".sock")


# ── what must match between the hook and the daemon ────────────────────────────────────────────────────────────
def env_fingerprint(env=None):
    """The INSPEXIMUS_* environment, hashed. The daemon answers only a hook started with the same one."""
    env = os.environ if env is None else env
    items = sorted((k, v) for k, v in env.items()
                   if k.startswith("INSPEXIMUS_") and not k.startswith("INSPEXIMUS_HOOK_DAEMON"))
    return hashlib.sha256(json.dumps(items).encode("utf-8")).hexdigest()[:16]


def code_identity():
    """The client's and the daemon's code: inspeximus version, guard-set hash and interpreter."""
    import inspeximus
    from .core import _guard_set_hash
    return {"version": inspeximus.__version__, "guard_set": _guard_set_hash(), "python": _real(sys.executable)}


def store_signature(path):
    """What must not have moved for a held handle to be current: the row store's write generation and the stat of the
    store and every sidecar beside it. A peer's write, an erasure, a restore and a replace each move one of them."""
    d, base = os.path.dirname(os.path.abspath(path)), os.path.basename(path)
    parts = []
    try:
        names = sorted(n for n in os.listdir(d) if n == base or n.startswith(base + "."))
    except OSError:
        names = []
    for n in names:
        try:
            st = os.stat(os.path.join(d, n))
            parts.append((n, st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append((n, None, None))
    try:
        from . import sqlite_store
        gen = sqlite_store.generation(path) if sqlite_store.looks_like_sqlite(path) else None
    except Exception:                                           # noqa: BLE001
        gen = None
    return json.dumps([gen, parts])


# ── the handshake ──────────────────────────────────────────────────────────────────────────────────────────────
def _mac(token, label, *parts):
    h = hmac.new(token, label, hashlib.sha256)
    for p in parts:
        h.update(p if isinstance(p, bytes) else p.encode("utf-8"))
    return h.hexdigest()


def _recv(conn, deadline):
    left = deadline - time.monotonic()
    if left <= 0 or not conn.poll(left):
        raise TimeoutError("no reply in time")
    return json.loads(conn.recv_bytes(1 << 24).decode("utf-8"))


def _send(conn, obj):
    conn.send_bytes(json.dumps(obj).encode("utf-8"))


def _read_token(kh, t):
    try:
        with open(_token_path(kh, t), "rb") as fh:
            tok = fh.read()
        return tok if len(tok) >= 32 else None
    except OSError:
        return None


def _connect(addr, deadline):
    """A raw connection to `addr` within the deadline, or None. Nothing here waits past it."""
    if os.name == "nt":
        import _winapi
        from multiprocessing.connection import PipeConnection
        while True:
            try:
                h = _winapi.CreateFile(addr, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE, 0, _winapi.NULL,
                                       _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED, _winapi.NULL)
            except OSError as e:
                if getattr(e, "winerror", None) != 231:          # ERROR_PIPE_BUSY: wait for an instance, bounded
                    return None
                left = int((deadline - time.monotonic()) * 1000)
                if left <= 0:
                    return None
                try:
                    _winapi.WaitNamedPipe(addr, max(1, left))
                except OSError:
                    return None
                continue
            _winapi.SetNamedPipeHandleState(h, _winapi.PIPE_READMODE_MESSAGE, None, None)
            return PipeConnection(h)
    import socket
    from multiprocessing.connection import Connection
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(max(0.001, deadline - time.monotonic()))
    try:
        s.connect(addr)
    except OSError:
        s.close()
        return None
    s.setblocking(True)
    return Connection(s.detach())


# ── the client: the hook's side ────────────────────────────────────────────────────────────────────────────────
#: Why the last ask did not use the daemon: "served", "absent", "store", "refused:<reason>", "timeout", "bad-reply".
LAST = {"outcome": None, "foreign": 0}


def ask(ev, store_path, timeout=REPLY_TIMEOUT_S):
    """The prompt block from the daemon for `ev`, or None. None means: run today's path. Never raises."""
    LAST.update(outcome=None, foreign=0)
    try:
        deadline = time.monotonic() + timeout
        kh = key_home_for(store_path)
        t = tag(store_path, kh)
        token = _read_token(kh, t)
        if token is None:
            LAST["outcome"] = "absent"
            return None
        conn = _connect(address(kh, t), min(deadline, time.monotonic() + CONNECT_TIMEOUT_S))
        if conn is None:
            LAST["outcome"] = "absent"
            return None
        try:
            nc = os.urandom(16).hex()
            _send(conn, {"hello": PROTOCOL, "nc": nc})
            hi = _recv(conn, deadline)
            if not hmac.compare_digest(str(hi.get("mac", "")), _mac(token, b"S|", nc)):
                LAST["outcome"] = "bad-reply"                   # not the daemon that holds our token
                return None
            ns = str(hi.get("ns", ""))
            t_id = time.perf_counter()
            req = json.dumps({"ev": ev, "cwd": ev.get("cwd") or os.getcwd(), "store": _real(store_path),
                              "code": code_identity(), "env": env_fingerprint()}, sort_keys=True)
            LAST["identity_ms"] = round(1000 * (time.perf_counter() - t_id), 1)
            _send(conn, {"req": req, "mac": _mac(token, b"C|", ns, req)})
            msg = _recv(conn, deadline)
            rep = str(msg.get("rep", ""))
            if not hmac.compare_digest(str(msg.get("mac", "")), _mac(token, b"R|", nc, rep)):
                LAST["outcome"] = "bad-reply"
                return None
            r = json.loads(rep)
        finally:
            conn.close()
        if not r.get("ok"):
            LAST["outcome"] = "refused:" + str(r.get("reason"))
            return None
        LAST.update(outcome="served", foreign=int(r.get("foreign") or 0), answer_ms=r.get("answer_ms"),
                    total_ms=round(1000 * (time.monotonic() - (deadline - timeout)), 1))
        return str(r.get("out") or "")
    except TimeoutError:
        LAST["outcome"] = "timeout"
        return None
    except Exception:                                           # noqa: BLE001
        LAST["outcome"] = "bad-reply"
        return None


def enabled():
    return (os.environ.get("INSPEXIMUS_HOOK_DAEMON") or "").strip().lower() in ("1", "true", "yes", "on")


def maybe_start(cwd, store_path) -> str:
    """Start a daemon for `store_path` when none answers. Rate-limited per store, the attempt recorded before the start,
    the pid merged after it, through `claude_code._start_detached` (3.16.4's launch rules). Never raises."""
    try:
        if not enabled() or os.environ.get("INSPEXIMUS_HOOK_DAEMON_NOSTART"):
            return "off"
        if not os.path.exists(store_path):
            return "missing"
        from . import claude_code as cc
        from ._safewrite import write_atomic
        state = cc._state_path(store_path, "hookd")
        now = time.time()
        try:
            with open(state, encoding="utf-8") as fh:
                last = float(json.load(fh).get("last_attempt") or 0)
        except (OSError, ValueError, AttributeError):
            last = 0.0
        if now - last < START_MIN_INTERVAL_S:
            return "recent"
        record = {"last_attempt": now, "interpreter": sys.executable}
        write_atomic(state, json.dumps(record))
        proc = cc._start_detached(["--serve", "--expect-store", store_path], cwd,
                                  cc._state_path(store_path, "hookd", ".log"))
        record["pid"] = getattr(proc, "pid", None)
        write_atomic(state, json.dumps(record))
        return "started"
    except Exception:                                           # noqa: BLE001
        return "failed"


# ── the daemon ─────────────────────────────────────────────────────────────────────────────────────────────────
class Daemon:
    """Holds the stores the prompt hook reads, and answers one request type: the hook's recall block for an event."""

    def __init__(self, store_path, idle_exit_s=IDLE_EXIT_S):
        self.store = _real(store_path)
        self.kh = key_home_for(store_path)
        self.tag = tag(store_path, self.kh)
        self.addr = address(self.kh, self.tag)
        self.code = code_identity()
        self.env = env_fingerprint()
        self.idle_exit_s = idle_exit_s
        self.held = {}                 # real path -> (signature, handle)
        self.last_request = time.monotonic()
        self.stop = False
        self.token = os.urandom(32)
        self.reopens = 0
        self.counts = {}               # id(handle) -> (handle, foreign stamps counted when it was opened)

    # -- holding the stores -----------------------------------------------------------------------------------------
    def handle_for(self, path, opener):
        """The held handle for `path`, reopened from disk when its signature moved since it was opened."""
        key = _real(path)
        sig = store_signature(path)
        cur = self.held.get(key)
        if cur is None or cur[0] != sig:
            m = opener()
            self.held[key] = (sig, m)
            self.reopens += 1
            return m
        return cur[1]

    def answer(self, req):
        """The reply object for one verified request."""
        code = req.get("code") or {}
        if code != self.code:
            self.stop = True                                    # another version: exit, the next hook starts the right one
            return {"ok": False, "reason": "code"}
        if req.get("env") != self.env:
            return {"ok": False, "reason": "env"}
        ev = req.get("ev") or {}
        cwd = req.get("cwd") or ""
        from . import claude_code as cc
        from . import _surface
        try:
            mine = _real(_surface.coding_store_path(cwd))       # the 3.16.5 vet, here, under this process's rules
        except Exception as e:                                  # noqa: BLE001
            return {"ok": False, "reason": "vet: %s" % type(e).__name__}
        if mine != self.store or req.get("store") != self.store:
            return {"ok": False, "reason": "store"}
        if not os.path.exists(self.store):
            self.stop = True
            return {"ok": False, "reason": "missing"}
        orig_store, orig_open, orig_count = cc._store, _surface.open_store, cc.foreign_stamp_count

        def counted_once(m):
            # ONCE PER HELD HANDLE: a fresh hook counts the foreign stamps of a freshly opened store, before the recall's
            # assessment drops them from the handle. Recounting the held handle would cost 34 ms a request on a 22,500-row
            # store and answer a different question; the count is redone when the handle is reopened.
            hit = self.counts.get(id(m))
            if hit is None or hit[0] is not m:
                hit = self.counts[id(m)] = (m, orig_count(m))
            return hit[1]

        def held_store(c):
            return self.handle_for(self.store, lambda: orig_store(c))

        def held_open(p, **kw):
            if kw == {"resolve": False}:                        # the decision store, read-only on this path
                return self.handle_for(p, lambda: orig_open(p, **kw))
            return orig_open(p, **kw)
        buf = io.StringIO()
        cc._store, _surface.open_store, cc.foreign_stamp_count = held_store, held_open, counted_once
        saved_stdout = sys.stdout
        try:
            sys.stdout = buf
            cc.recall(ev)
        finally:
            sys.stdout = saved_stdout
            cc._store, _surface.open_store, cc.foreign_stamp_count = orig_store, orig_open, orig_count
        foreign = sum(n for _p, _e, n in cc._LAST_STORES)
        cc._LAST_STORES.clear()
        return {"ok": True, "out": buf.getvalue(), "foreign": foreign}

    # -- the channel ------------------------------------------------------------------------------------------------
    def _serve_one(self, conn):
        deadline = time.monotonic() + SERVER_READ_TIMEOUT_S
        hello = _recv(conn, deadline)
        nc = str(hello.get("nc", ""))
        if hello.get("hello") != PROTOCOL or len(nc) != 32:
            return
        ns = os.urandom(16).hex()
        _send(conn, {"ns": ns, "mac": _mac(self.token, b"S|", nc)})
        msg = _recv(conn, deadline)
        req_s = str(msg.get("req", ""))
        if not hmac.compare_digest(str(msg.get("mac", "")), _mac(self.token, b"C|", ns, req_s)):
            return                                              # a caller without the token gets nothing
        self.last_request = time.monotonic()
        t0 = time.perf_counter()
        try:
            rep = self.answer(json.loads(req_s))
            rep["answer_ms"] = round(1000 * (time.perf_counter() - t0), 1)
        except Exception as e:                                  # noqa: BLE001
            rep = {"ok": False, "reason": "error: %s" % type(e).__name__}
        rep_s = json.dumps(rep)
        _send(conn, {"rep": rep_s, "mac": _mac(self.token, b"R|", nc, rep_s)})

    def _write_files(self):
        from ._safewrite import write_atomic
        os.makedirs(state_dir(self.kh), exist_ok=True)
        if os.name != "nt":
            os.chmod(state_dir(self.kh), 0o700)
        write_atomic(_token_path(self.kh, self.tag), self.token)
        write_atomic(_pid_path(self.kh, self.tag), json.dumps(
            {"pid": os.getpid(), "store": self.store, "version": self.code["version"], "started": time.time()}))

    def _remove_files(self):
        for p in (_token_path(self.kh, self.tag), _pid_path(self.kh, self.tag)):
            try:
                with open(_pid_path(self.kh, self.tag), encoding="utf-8") as fh:
                    if json.load(fh).get("pid") != os.getpid():
                        return                                  # another daemon took over; its files stay
            except (OSError, ValueError):
                pass
            try:
                os.unlink(p)
            except OSError:
                pass

    def _watchdog(self):
        while not self.stop:
            time.sleep(min(1.0, self.idle_exit_s / 4))
            if time.monotonic() - self.last_request > self.idle_exit_s or not os.path.exists(self.store) \
                    or not os.path.isdir(self.kh):
                self.stop = True
        self._wake()

    def _wake(self):
        try:
            c = _connect(self.addr, time.monotonic() + 0.5)     # unblocks accept() so the loop sees `stop`
            if c is not None:
                c.close()
        except Exception:                                       # noqa: BLE001
            pass

    def warm(self, cwd):
        """Open the stores and build their search state before the token is published, so the first hook that finds the
        daemon gets a warm answer: a cold one takes the open (0.4 s on the project store) and exceeds the client's
        timeout. A direct recall, not the hook's: the hook's path also counts prompts for the nudge."""
        from . import claude_code as cc
        from ._surface import open_store
        m = self.handle_for(self.store, lambda: cc._store(cwd))
        m.recall("warm", k=1)
        ext = (os.environ.get("INSPEXIMUS_DECISION_STORE") or "").strip()
        if ext and os.path.exists(ext) and _real(ext) != self.store:
            e = self.handle_for(ext, lambda: open_store(ext, resolve=False))
            e.recall("warm", k=1)

    def serve(self, warm_cwd=None):
        """Run until idle, until the store is gone, or until a request of another version. Returns the exit reason."""
        from multiprocessing.connection import Listener
        try:
            self.warm(warm_cwd or os.getcwd())
        except Exception:                                       # noqa: BLE001 -- a cold first answer is only slower
            pass
        if os.name != "nt" and os.path.exists(self.addr):
            os.unlink(self.addr)
        listener = Listener(self.addr, family="AF_PIPE" if os.name == "nt" else "AF_UNIX")
        if os.name != "nt":
            os.chmod(self.addr, 0o600)
        self._write_files()
        threading.Thread(target=self._watchdog, daemon=True).start()
        try:
            while not self.stop:
                try:
                    conn = listener.accept()
                except OSError:
                    continue
                if self.stop:
                    conn.close()
                    break
                try:
                    self._serve_one(conn)
                except Exception:                               # noqa: BLE001
                    pass
                finally:
                    try:
                        conn.close()
                    except Exception:                           # noqa: BLE001
                        pass
        finally:
            listener.close()
            self._remove_files()
        return "stopped"


def live_daemon(store_path):
    """The pid record of a live daemon for `store_path`, or None."""
    try:
        kh = key_home_for(store_path)
        with open(_pid_path(kh, tag(store_path, kh)), encoding="utf-8") as fh:
            rec = json.load(fh)
        from .claude_code import _pid_alive
        return rec if _pid_alive(int(rec.get("pid"))) else None
    except Exception:                                           # noqa: BLE001
        return None


def serve_main(argv):
    """`python -m inspeximus.claude_code --serve --expect-store <path>`: one daemon for that store, or exit at once."""
    i = argv.index("--expect-store") if "--expect-store" in argv[:-1] else -1
    if i < 0:
        return 2
    store_path = argv[i + 1]
    from . import _surface
    try:
        if _real(_surface.coding_store_path(os.getcwd())) != _real(store_path):
            return 2                                            # the started-for store is not what this directory resolves
    except Exception:                                           # noqa: BLE001
        return 2
    if live_daemon(store_path):
        return 0                                                # one per (store, key home)
    Daemon(store_path).serve()
    return 0
