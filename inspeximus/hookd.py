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
    prompt is therefore in the answer. Between requests the watchdog compares the same signature every HELD_CHECK_S
    and drops a moved store's handle, so an erased record does not wait in the daemon's memory for the next prompt.

LIFETIME. Started by a hook that found none (opt-in: `{"hook": {"daemon": true}}` in the user's config), as a detached run
with the 3.16.4 launch rules (`_start_detached`: -E and the shim, breakaway, a new session), with the attempt
recorded. It exits after IDLE_EXIT_S without a request, when the store disappears, or when `inspeximus hookd stop` asks
every daemon of the key home to exit (`request_stop`).
"""
import contextlib
import hashlib
import hmac
import io
import json
import os
import sys
import threading
import time
import warnings

PROTOCOL = 1
CONNECT_TIMEOUT_S = 0.1          #: a daemon that does not take the connection within this is treated as absent
REPLY_TIMEOUT_S = 0.3            #: the whole exchange, connect to verified reply; past it the hook runs today's path
#: A client that stalls the handshake is dropped after this. A legitimate client finishes the whole exchange within
#: REPLY_TIMEOUT_S, so anything longer only lets a silent connection hold the single accept loop (AUDIT-A D-8: 2 s).
SERVER_READ_TIMEOUT_S = 0.5
IDLE_EXIT_S = 600.0              #: the daemon exits after this long without a request
#: The watchdog compares every held store's signature this often and drops the handle of a store that moved, so an
#: erasure leaves the daemon's memory within about this interval, not at the next request (3.19).
HELD_CHECK_S = 0.25
START_MIN_INTERVAL_S = 60.0      #: at most one start attempt per store in this interval
#: A daemon counts a stop request from the moment its launcher started it, not from its own start, so a request made
#: while the interpreter loads is not lost. A launch time older than this, or in the future, is not believed: an old
#: request left in the folder would then stop every daemon at once (3.18, EM).
LAUNCH_MAX_AGE_S = 120.0
BACKOFF_AFTER = 3                #: this many timeouts in a row and the hook stops asking ...
BACKOFF_S = 300.0                #: ... for this long, so a slow daemon costs at most BACKOFF_AFTER x REPLY_TIMEOUT_S
MAX_LIVE_DAEMONS = 4             #: at most this many live daemons per key home; a fifth store gets none (AUDIT-A D-3)
MAX_CONNECTIONS = 16             #: handshakes in flight at once (AUDIT-A E-4)
#: How often a connection waiting for its hello looks whether a newer connection evicted it (3.19). A silent connection
#: keeps its slot for SERVER_READ_TIMEOUT_S while slots are free, and loses it to the next connection once none is.
HELLO_POLL_S = 0.01


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


def _stop_path(kh):
    return os.path.join(state_dir(kh), "stop.request")


def _token_path(kh, t):
    return os.path.join(state_dir(kh), t + ".token")


def _pid_path(kh, t):
    return os.path.join(state_dir(kh), t + ".json")


def _fails_path(kh, t):
    return os.path.join(state_dir(kh), t + ".fails")


def _proc_start(pid):
    """The process's creation time as an opaque number, or None where it cannot be read. With the pid it names one
    process: a pid the system gave to another process since has another start time (AUDIT-A D-5)."""
    try:
        pid = int(pid)
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32")      # a private instance: restype set here stays here
            k32.OpenProcess.restype = wintypes.HANDLE
            h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return None
            try:
                ft = [wintypes.FILETIME() for _ in range(4)]
                if not k32.GetProcessTimes(wintypes.HANDLE(h), *[ctypes.byref(f) for f in ft]):
                    return None
                return (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime
            finally:
                k32.CloseHandle(wintypes.HANDLE(h))
        with open("/proc/%d/stat" % pid, "rb") as fh:            # Linux; field 22, after the parenthesised name
            return int(fh.read().rsplit(b")", 1)[1].split()[19])
    except Exception:                                           # noqa: BLE001
        return None


def _record_is_live(rec):
    """A pid record names a running daemon: its pid is alive and, where the start time can be read, is the same
    process that wrote the record."""
    from .claude_code import _pid_alive
    pid = rec.get("pid")
    if not _pid_alive(pid):
        return False
    # A record without a start time is stale (AUDIT-A E-5): a record from a03da444, or one written where the start
    # time cannot be read, was live by its pid alone, and a reused pid then blocked every start. On such a platform no
    # daemon is ever live, and every hook runs today's path.
    want, now = rec.get("proc_start"), _proc_start(pid)
    return want is not None and now is not None and want == now


def _read_record(kh, t):
    try:
        with open(_pid_path(kh, t), encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


#: The longest socket path used, in bytes: a path of at most this many fits. AF_UNIX takes 107 on Linux and 103 on
#: macOS, before the terminating NUL.
SOCK_PATH_MAX = 100
#: The last candidate folder for the socket, after the temp directory. A module name so a test can point it elsewhere.
_LAST_RESORT_ROOT = "/tmp"


def address(kh, t):
    """A named pipe on Windows. Elsewhere a socket in the key home, or, when that path is too long for AF_UNIX, in a
    per-user 0700 directory under the temp directory, then under /tmp. None when no candidate is usable (3.18): the
    daemon does not start and the hook takes its normal path. Measured 2026-10-11 in CI: the test sandbox's temp
    directory was long enough that the fallback did not fit either, the bind raised "AF_UNIX path too long", and the
    daemon's tests failed on Linux.

    A candidate folder is usable when its socket path fits SOCK_PATH_MAX and the folder is this user's and of mode 0700.
    A folder that exists and is another user's, or open to the group or others, is skipped: never changed, never used
    (AUDIT-A V-2: it raised, the hook gave up with no notice, and the next candidate was never tried)."""
    if os.name == "nt":
        return r"\\.\pipe\inspeximus-hookd-" + t
    p = os.path.join(state_dir(kh), t + ".sock")
    if len(p.encode("utf-8")) <= SOCK_PATH_MAX:
        return p
    import tempfile
    for root in (tempfile.gettempdir(), _LAST_RESORT_ROOT):
        d = os.path.join(root, "inspeximus-hookd-%d" % os.getuid())
        p = os.path.join(d, t + ".sock")
        if len(p.encode("utf-8")) > SOCK_PATH_MAX:
            continue                                            # too long to bind: the next candidate
        try:
            os.makedirs(d, mode=0o700, exist_ok=True)
            st = os.stat(d)
        except OSError:
            continue                                            # cannot be made or read: the next candidate
        if st.st_uid != os.getuid() or (st.st_mode & 0o077):
            continue                                            # not this user's private folder: never used
        return p
    return None


# ── what must match between the hook and the daemon ────────────────────────────────────────────────────────────
def env_fingerprint(env=None):
    """The INSPEXIMUS_* environment, hashed. The daemon answers only a hook started with the same one."""
    from . import _envpolicy
    items = sorted(_envpolicy.snapshot(env, without=("INSPEXIMUS_HOOK_DAEMON", "INSPEXIMUS_HOOK_DAEMON_NOSTART",
                                                     "INSPEXIMUS_HOOK_DAEMON_TRACE")).items())
    return hashlib.sha256(json.dumps(items).encode("utf-8")).hexdigest()[:16]


def code_identity():
    """The client's and the daemon's code: inspeximus version, guard-set hash and interpreter."""
    import inspeximus
    from .core import _guard_set_hash
    return {"version": inspeximus.__version__, "guard_set": _guard_set_hash(), "python": _real(sys.executable)}


def store_signature(path, answer_files=None):
    """What must not have moved for a held handle to be current: the row store's write generation and the stat of the
    store and every sidecar beside it. A peer's write, an erasure, a restore and a replace each move one of them.

    AUDIT-A D-9 added three parts. SQLite's own file change counter (header bytes 24 to 27), which every write
    transaction moves, so a raw `UPDATE` that keeps the size and restores the modification time is still seen. The
    journal files (`<store>-journal`, `-wal`, `-shm`), which a `<store>.` prefix missed. And the store's two key files
    in the key home, so a key rotated after a suspected leak reopens the handle that holds the old one.

    `answer_files` is `core._answer_files(path)` when the caller already has it. The daemon's watchdog passes the list
    `handle_for` resolved when it opened the handle, because resolving the key home is half the cost of this call (1.8
    of 3.7 ms on a 46 MB store, 3.19) and the watchdog makes it four times a second. `handle_for` resolves it afresh on
    every request, so a list that grew since (a key home created by the open) is seen there, as before."""
    d, base = os.path.dirname(os.path.abspath(path)), os.path.basename(path)
    parts = []
    try:
        names = sorted(n for n in os.listdir(d) if n == base or n.startswith(base + ".") or n.startswith(base + "-"))
    except OSError:
        names = []
    files = [os.path.join(d, n) for n in names]
    # EVERY FILE THE ANSWER DEPENDS ON (3.18, AUDIT-A I-2), not only the store's: the user's config, which the handle read
    # at open (guards, supersession), and the store's files in the key home. `core._answer_files` names them in one place.
    if answer_files is None:
        try:
            from .core import _answer_files
            answer_files = _answer_files(path)
        except Exception:                                       # noqa: BLE001
            answer_files = []
    files += list(answer_files)
    # THE INODE CHANGE TIME AND THE INODE ON POSIX (3.18, AUDIT-B R-1). An in-place patch of the same length that puts
    # the modification time back leaves the size, the mtime and SQLite's change counter as they were; the kernel moves
    # st_ctime_ns on any write and no tool can set it, and a replace moves st_ino. On Windows st_ctime is the creation
    # time and nothing cheap stands in for it, so that patch is not seen there (a known limit of 3.18).
    posix = os.name != "nt"
    for f in files:
        try:
            st = os.stat(f)
            parts.append((f, st.st_mtime_ns, st.st_size) + ((st.st_ctime_ns, st.st_ino) if posix else ()))
        except OSError:
            parts.append((f, None, None))
    counter = None
    try:
        with open(path, "rb") as fh:
            head = fh.read(28)
        if head[:16] == b"SQLite format 3\x00":
            counter = int.from_bytes(head[24:28], "big")
    except OSError:
        pass
    try:
        from . import sqlite_store
        gen = sqlite_store.generation(path) if sqlite_store.looks_like_sqlite(path) else None
    except Exception:                                           # noqa: BLE001
        gen = None
    return json.dumps([gen, counter, parts])


def store_mark(path):
    """The store file's own stat and SQLite's file change counter: what the watchdog reads every HELD_CHECK_S (3.19).

    An erasure writes the store file itself. A row store is in DELETE journal mode, so every write transaction moves
    the change counter in the header; a whole-file store is replaced, which moves the stat. So this cheap mark moves on
    every erasure, and only then does the watchdog compute the whole `store_signature`. Measured on one idle daemon over
    60 s: the whole signature on every tick took 1.04% of one core, against 0.05% for 3.18.0 (see the 3.19 note). A
    change that touches no store byte (the user's config, a key file) is left to the next request, as before.

    AND THE WRITE-AHEAD LOG. The library creates a row store in DELETE mode, but the mode is a property of the file:
    `PRAGMA journal_mode=WAL` from a user or another tool persists, and an open does not reset it. Measured: a WAL-mode
    store with a second connection open took an erasure into `<store>-wal`, and the main file's stat and change counter
    stayed as they were until a checkpoint. So the `-wal` file's stat is part of the mark, as it is of the signature."""
    try:
        st = os.stat(path)
        with open(path, "rb") as fh:
            head = fh.read(28)
    except OSError:
        return None
    try:
        w = os.stat(path + "-wal")
        wal = (w.st_mtime_ns, w.st_size, w.st_ctime_ns, w.st_ino)
    except OSError:
        wal = None
    return (st.st_mtime_ns, st.st_size, st.st_ctime_ns, st.st_ino, head[24:28], wal)


# ── the handshake ──────────────────────────────────────────────────────────────────────────────────────────────
def _mac(token, label, *parts):
    h = hmac.new(token, label, hashlib.sha256)
    for p in parts:
        h.update(p if isinstance(p, bytes) else p.encode("utf-8"))
    return h.hexdigest()


#: Module state a hook process starts without and the daemon would keep from answer to answer: the once-per-process
#: notices, and the git calls remembered as failed. `test_every_once_per_process_set_is_reset` scans the package for
#: module-level sets and lists and fails on one that is neither here nor in its list of caches.
PROCESS_STATE = (("_isolate", "_SAID"), ("_http", "_ENV_NOTICE"), ("_keyhome", "_NOTICE"), ("_userconfig", "_SAID"),
                 ("claude_code", "_REPO_EMBED_NOTICE"), ("claude_code", "_REPO_EMBED_KEYS_NOTICE"),
                 ("claude_code", "_REPO_ARCHIVE_NOTICE"), ("_storelink", "_GIT_FAILED"), ("core", "_SAID_ONCE"))


def reset_process_state():
    """Empty every container in PROCESS_STATE in place. Never raises."""
    import importlib
    for mod, name in PROCESS_STATE:
        try:
            getattr(importlib.import_module("inspeximus." + mod), name).clear()
        except Exception:                                       # noqa: BLE001
            pass


def _version_key(v):
    """A version string as a tuple of ints for ordering; anything unreadable sorts lowest."""
    try:
        return tuple(int(x) for x in str(v).split("+")[0].split("."))
    except (TypeError, ValueError):
        return ()


#: The INSPEXIMUS_* variables whose value names a file or a folder. A relative value is resolved by each process against
#: its own working directory, and the daemon's is not the hook's (AUDIT-A E-1: a relative decision store was silently
#: missing from a served answer). Every variable the package reads as a path is here; the test
#: `test_every_path_variable_is_listed` fails when one that is read with a filesystem call is not.
from ._envpolicy import PATH_VARS  # noqa: E402  (the list lives with the rules, 3.18)


def _hexkey(v):
    return len(v) == 64 and all(c in "0123456789abcdefABCDEF" for c in v)


def path_values(env=None):
    """{variable: value} for the path variables that are set, or None when one of them is relative. A 64-hex
    INSPEXIMUS_RECEIPT_KEY is the key itself, not a path."""
    from . import _envpolicy
    out = {}
    for k in PATH_VARS:
        v = (_envpolicy.raw(k, env=env) or "").strip()
        if not v or (k == "INSPEXIMUS_RECEIPT_KEY" and _hexkey(v)):
            continue
        if not os.path.isabs(v):
            return None
        out[k] = _real(v)
    return out


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


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
    # A FULL QUEUE IS WAITED ON, AS A BUSY PIPE IS (3.19). With a timeout the socket is non-blocking, and an AF_UNIX
    # connect to a listener whose queue is full fails at once with EAGAIN rather than waiting: measured in WSL, a burst
    # of silent connections made every hook beside it "absent". The connect is retried until the deadline.
    while True:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(max(0.001, deadline - time.monotonic()))
        try:
            s.connect(addr)
            break
        except BlockingIOError:
            s.close()
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.002)
        except OSError:
            s.close()
            return None
    s.setblocking(True)
    return Connection(s.detach())


# ── the client: the hook's side ────────────────────────────────────────────────────────────────────────────────
#: Why the last ask did not use the daemon: "served", "absent", "store", "refused:<reason>", "timeout", "bad-reply".
LAST = {"outcome": None, "foreign": 0}


def _backing_off(kh, t):
    try:
        with open(_fails_path(kh, t), encoding="utf-8") as fh:
            f = json.load(fh)
        return int(f.get("n") or 0) >= BACKOFF_AFTER and time.time() < float(f.get("until") or 0)
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def _note_outcome(kh, t, timed_out):
    """Count timeouts in a row; a served answer clears the count. Best effort: a lost write costs one more ask."""
    p = _fails_path(kh, t)
    try:
        if not timed_out:
            if os.path.exists(p):
                os.unlink(p)
            return
        try:
            with open(p, encoding="utf-8") as fh:
                n = int(json.load(fh).get("n") or 0)
        except (OSError, ValueError, AttributeError, TypeError):
            n = 0
        if n >= BACKOFF_AFTER:
            n = 0                                               # the back-off ran out and the daemon is still slow
        n += 1
        from ._safewrite import write_atomic
        write_atomic(p, json.dumps({"n": n, "until": time.time() + BACKOFF_S}))
    except Exception:                                           # noqa: BLE001
        pass


def ask(ev, store_path, timeout=REPLY_TIMEOUT_S):
    """The prompt block from the daemon for `ev`, or None. None means: run today's path. Never raises.

    The block comes with what the daemon wrote to stderr while it was computed, in LAST["err"], so the hook can print it
    where today's path prints it (AUDIT-A D-7)."""
    LAST.update(outcome=None, foreign=0, err="")
    try:
        deadline = time.monotonic() + timeout
        kh = key_home_for(store_path)
        t = tag(store_path, kh)
        # A SLOW DAEMON IS WORSE THAN NONE (AUDIT-A D-2): every ask that times out adds REPLY_TIMEOUT_S to today's path.
        # After BACKOFF_AFTER of them in a row the hook stops asking for BACKOFF_S.
        if _backing_off(kh, t):
            LAST["outcome"] = "backoff"
            return None
        token = _read_token(kh, t)
        if token is None:
            LAST["outcome"] = "absent"
            return None
        # THE TOKEN ALONE IS NOT ENOUGH (AUDIT-A D-1). A token left by a daemon that crashed, or read from a key home
        # someone else can read, let a process that took the free endpoint name read the prompt and write the reply.
        # The client asks only when a live daemon's record names this token: its pid alive, the same process by its
        # start time, and the record's hash of the token equal to the file's.
        rec = _read_record(kh, t)
        if (rec is None or rec.get("token_sha") != hashlib.sha256(token).hexdigest()[:32]
                or not _record_is_live(rec)):
            LAST["outcome"] = "absent"
            return None
        paths = path_values()
        if paths is None:
            LAST["outcome"] = "relative"                        # a relative path variable: today's path (E-1)
            return None
        addr = address(kh, t)
        if addr is None:
            LAST["outcome"] = "no-address"                      # no socket path fits here (3.18)
            return None
        conn = _connect(addr, min(deadline, time.monotonic() + CONNECT_TIMEOUT_S))
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
            # `until` is wall-clock: the daemon drops a request it reaches after the client has given up (D-2).
            req = json.dumps({"ev": ev, "cwd": ev.get("cwd") or os.getcwd(), "store": _real(store_path),
                              "code": code_identity(), "env": env_fingerprint(), "paths": paths,
                              "until": time.time() + max(0.0, deadline - time.monotonic())}, sort_keys=True)
            LAST["identity_ms"] = round(1000 * (time.perf_counter() - t_id), 1)
            _send(conn, {"req": req, "mac": _mac(token, b"C|", ns, req)})
            msg = _recv(conn, deadline)
            rep = str(msg.get("rep", ""))
            # The reply's MAC covers the request it answers, not only the client's nonce (D-1).
            if not hmac.compare_digest(str(msg.get("mac", "")), _mac(token, b"R|", nc, _sha(req), rep)):
                LAST["outcome"] = "bad-reply"
                return None
            r = json.loads(rep)
        finally:
            conn.close()
        _note_outcome(kh, t, False)
        if not r.get("ok"):
            LAST["outcome"] = "refused:" + str(r.get("reason"))
            return None
        LAST.update(outcome="served", foreign=int(r.get("foreign") or 0), answer_ms=r.get("answer_ms"),
                    err=str(r.get("err") or ""), total_ms=round(1000 * (time.monotonic() - (deadline - timeout)), 1))
        return str(r.get("out") or "")
    except TimeoutError:
        LAST["outcome"] = "timeout"
        try:
            _note_outcome(kh, t, True)
        except Exception:                                       # noqa: BLE001
            pass
        return None
    except Exception:                                           # noqa: BLE001
        LAST["outcome"] = "bad-reply"
        return None


def enabled():
    """`{"hook": {"daemon": true}}` in `<key home>/inspeximus/config.json` switches the daemon on. Off by default.

    ONLY THE USER'S CONFIG TURNS IT ON (AUDIT-A D-3, EM's decision; the rule 3.17.0 applies to receipts.tail). A daemon
    holds the user's store in memory for IDLE_EXIT_S after each prompt, and a project's `.claude/settings.json` reaches
    the hook's environment, so INSPEXIMUS_HOOK_DAEMON=1 is ignored, with one stderr line naming the key and the path.
    The repository's own `.inspeximus/config.json` is never read for it either."""
    from . import _userconfig
    if _userconfig.get("hook", "daemon") is True:
        return True
    from . import _envpolicy
    _envpolicy.notice_if_set("INSPEXIMUS_HOOK_DAEMON", lambda v: v in ("1", "true", "yes", "on"))
    return False


_NO_ADDRESS_SAID = []


def _notice_no_address():
    """One line on stderr when no socket path is usable (3.18): once per process, and, because `maybe_start` records
    the attempt first, once per START_MIN_INTERVAL_S across hook runs."""
    if not _NO_ADDRESS_SAID:
        _NO_ADDRESS_SAID.append(1)
        sys.stderr.write("[inspeximus] hookd: no socket path of at most %d bytes here (the key home, the temp "
                         "directory and /tmp are too long, or not your own folder of mode 0700); the prompt daemon does "
                         "not start" % SOCK_PATH_MAX + chr(10))


def maybe_start(cwd, store_path) -> str:
    """Start a daemon for `store_path` when none answers. Rate-limited per store, the attempt recorded before the start,
    the pid merged after it, through `claude_code._start_detached` (3.16.4's launch rules). Never raises."""
    try:
        from . import _envpolicy
        if not enabled() or _envpolicy.raw("INSPEXIMUS_HOOK_DAEMON_NOSTART"):
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
        if _proc_start(os.getpid()) is None:
            return "unsupported"                                # no start time here: no daemon is ever live (F-1)
        if live_daemon_count(key_home_for(store_path)) >= MAX_LIVE_DAEMONS:
            return "limit"
        if address(key_home_for(store_path), tag(store_path, key_home_for(store_path))) is None:
            # THE ATTEMPT IS RECORDED, SO THE NOTICE IS RATE-LIMITED LIKE A START (AUDIT-A V-1). Returning before this
            # write printed the line on every hook run: each run is a new process.
            write_atomic(state, json.dumps({"last_attempt": now, "no_address": True}))
            _notice_no_address()                                # the hook takes its normal path
            return "no-address"
        record = {"last_attempt": now, "interpreter": sys.executable}
        write_atomic(state, json.dumps(record))
        # The state directory is the daemon's working directory, so it holds no project folder open (AUDIT-A D-4); the
        # project is an argument. Started there and never moved: a chdir after start resolved a relative path variable
        # against the wrong folder (E-1).
        kh = key_home_for(store_path)
        os.makedirs(state_dir(kh), mode=0o700, exist_ok=True)
        proc = cc._start_detached(["--serve", "--expect-store", store_path, "--project", os.path.abspath(cwd),
                                   "--launched-at", repr(now)],
                                  state_dir(kh), cc._state_path(store_path, "hookd", ".log"))
        record["pid"] = getattr(proc, "pid", None)
        record["proc_start"] = _proc_start(record["pid"]) if record["pid"] else None
        write_atomic(state, json.dumps(record))
        return "started"
    except Exception:                                           # noqa: BLE001
        return "failed"


# ── the daemon ─────────────────────────────────────────────────────────────────────────────────────────────────
def _drop_handle(m) -> None:
    """Empty a replaced held handle (AUDIT-A R-2). The handle can sit in a reference cycle until the collector runs, and
    with it every record it held, an erased one included. Its list goes through the setter, which prunes the derived
    caches against it; the row snapshot goes too. The records then go by reference count, without a full collection on
    the answer path. Answers are serialized, so nothing else is using the handle."""
    try:
        m._items = []
        m._prune_derived_caches()                               # the setter does it from 3.18 on; this branch, here
        m._row_snapshot = None
    except Exception:                                           # noqa: BLE001 -- a failure here costs memory only
        pass


#: THE STREAMS AND THE UMASK ARE THE PROCESS'S (3.18). A daemon swaps sys.stdout and sys.stderr around its warm-up and
#: each answer, and sets the umask around the bind. Two daemons serving in one process (the tests do; a daemon process
#: serves one) interleaved those swaps. Measured in WSL: sys.stdout stayed a StringIO after 8 of 10 rounds, and the
#: umask stayed 0177 after 1 of 10, so every folder the process made after that had no execute bit (CI, 2026-10-11: 40
#: setup errors, "Permission denied" inside a test's own tmp_path). One daemon at a time changes them and restores
#: what it found.
_PROCESS_STATE = threading.RLock()


class Daemon:
    """Holds the stores the prompt hook reads, and answers one request type: the hook's recall block for an event."""

    def __init__(self, store_path, idle_exit_s=IDLE_EXIT_S, launched_at=None):
        self.store = _real(store_path)
        self.kh = key_home_for(store_path)
        self.tag = tag(store_path, self.kh)
        self.addr = address(self.kh, self.tag)
        self.code = code_identity()
        self.env = env_fingerprint()
        self.idle_exit_s = idle_exit_s
        self.held = {}                 # real path -> (signature, handle)
        self.held_paths = {}           # real path -> (the path, the answer files) its signature was computed from
        self.held_marks = {}           # real path -> store_mark when the signature was last found current
        self.last_request = time.monotonic()
        self.started_at = _launch_time(launched_at)    # a stop request older than this is not for this daemon
        self.stop = False
        self.token = os.urandom(32)
        self.reopens = 0
        self.counts = {}               # id(handle) -> (handle, foreign stamps counted when it was opened)
        self.answering = threading.Lock()  # one answer at a time: the stores and the redirected streams are shared
        self.conns = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.pending = {}              # id(conn) -> (accepted at, eviction event): connections that have not said hello
        self.pending_lock = threading.Lock()

    # -- holding the stores -----------------------------------------------------------------------------------------
    def handle_for(self, path, opener):
        """The held handle for `path`, reopened from disk when its signature moved since it was opened."""
        key = _real(path)
        try:
            from .core import _answer_files
            files = _answer_files(path)
        except Exception:                                       # noqa: BLE001
            files = []
        mark = store_mark(path)
        sig = store_signature(path, files)
        cur = self.held.get(key)
        if cur is None or cur[0] != sig:
            m = opener()
            m._ix_held = True                                   # held: the recall index from its second recall (3.18)
            self.held[key] = (sig, m)
            self.held_paths[key] = (path, files)
            self.held_marks[key] = mark
            self.reopens += 1
            if cur is not None:
                self.counts.pop(id(cur[1]), None)
                _drop_handle(cur[1])
            return m
        return cur[1]

    def answer(self, req):
        """The reply object for one verified request."""
        code = req.get("code") or {}
        if code != self.code:
            # ONLY A NEWER CLIENT STOPS THE DAEMON (AUDIT-A D-6). Any other identity was a stop as well, so two sessions
            # that pin different builds of the same store took turns killing each other's daemon. An upgrade in place
            # is a newer version and still replaces the daemon at the next prompt; an older client, or the same version
            # under another interpreter or guard set, is refused and runs today's path.
            if _version_key(code.get("version")) > _version_key(self.code.get("version")):
                self.stop = True
            return {"ok": False, "reason": "code"}
        if req.get("env") != self.env:
            return {"ok": False, "reason": "env"}
        try:
            late = time.time() > float(req.get("until"))
        except (TypeError, ValueError):
            late = False
        if late:
            return {"ok": False, "reason": "late"}              # the client gave up; do not spend the work (D-2)
        # THE SAME FILES, BY ABSOLUTE PATH (AUDIT-A E-1). The environment fingerprint compares the values as written, and a
        # relative value names another file in this process. A relative value here, or absolute paths that differ from
        # the hook's, is refused, and the hook runs today's path.
        mine_paths = path_values()
        if mine_paths is None:
            return {"ok": False, "reason": "relative-path"}
        if req.get("paths") != mine_paths:
            return {"ok": False, "reason": "paths"}
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
        def counted_once(m):
            # ONCE PER HELD HANDLE: a fresh hook counts the foreign stamps of a freshly opened store, before the recall's
            # assessment drops them from the handle. Recounting the held handle would cost 34 ms a request on a 22,500-row
            # store and answer a different question; the count is redone when the handle is reopened.
            hit = self.counts.get(id(m))
            if hit is None or hit[0] is not m:
                hit = self.counts[id(m)] = (m, cc.foreign_stamp_count(m))
            return hit[1]

        def held_store(c):
            return self.handle_for(self.store, lambda: cc._store(c))

        def held_decisions(p):
            return self.handle_for(p, lambda: _surface.open_store(p, resolve=False))
        # THE HANDLES ARE ARGUMENTS, NOT PATCHED MODULE NAMES: a second thread in this process cannot see them. stdout and
        # stderr are still redirected for the length of the answer, which is sound only while the accept loop is the one
        # thread that writes to them (the watchdog writes nothing).
        out, err = io.StringIO(), io.StringIO()
        with _PROCESS_STATE:                                # the streams and _LAST_STORES are the process's
            saved = sys.stdout, sys.stderr
            try:
                sys.stdout, sys.stderr = out, err
                # A FRESH HOOK IS A FRESH PROCESS (AUDIT-A E-2). The notices the library prints once per process, and the
                # warnings Python shows once per place, were spent by the first answer or the warm-up and never reached a
                # later hook. They are reset here, so every answer prints what a fresh process would.
                reset_process_state()
                with warnings.catch_warnings():                     # entering resets the shown-once registries
                    cc.recall(ev, store=held_store, decision_store=held_decisions, count_foreign=counted_once)
            finally:
                sys.stdout, sys.stderr = saved
            foreign = sum(n for _p, _e, n in cc._LAST_STORES)
            cc._LAST_STORES.clear()
            return {"ok": True, "out": out.getvalue(), "err": err.getvalue(), "foreign": foreign}

    # -- the channel ------------------------------------------------------------------------------------------------
    def _hello(self, conn):
        """The first message, within SERVER_READ_TIMEOUT_S, read in HELLO_POLL_S steps so an eviction ends the wait.

        NO SHORTER WAIT WHILE A SLOT IS FREE (3.19, EM). A first version gave the hello 0.1 s. Measured in WSL pinned to
        two cores beside 16 busy processes: real clients' hellos took up to 39 ms, a margin of 2.5 times, and a runner
        busier than that would have sent a slow hook to its normal path while slots stood free. A silent connection
        harms nobody while a slot is free; it is evicted when a connection finds none."""
        with self.pending_lock:
            ent = self.pending.get(id(conn))
        evicted = ent[1] if ent else threading.Event()
        until = time.monotonic() + SERVER_READ_TIMEOUT_S
        try:
            while True:
                left = until - time.monotonic()
                if left <= 0 or evicted.is_set():
                    raise TimeoutError("no hello in time")
                if conn.poll(min(HELLO_POLL_S, left)):
                    return _recv(conn, until)                   # the one bounded read (3.18 request size)
        finally:
            with self.pending_lock:
                self.pending.pop(id(conn), None)

    def _evict_oldest_silent(self):
        """Mark the oldest connection that has not said hello for eviction. Returns whether there was one."""
        with self.pending_lock:
            live = [(at, ev) for at, ev in self.pending.values() if not ev.is_set()]
            if not live:
                return False
            min(live, key=lambda x: x[0])[1].set()
            return True

    def _serve_one(self, conn):
        hello = self._hello(conn)
        deadline = time.monotonic() + SERVER_READ_TIMEOUT_S
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
            with self.answering:
                rep = self.answer(json.loads(req_s))
            rep["answer_ms"] = round(1000 * (time.perf_counter() - t0), 1)
        except Exception as e:                                  # noqa: BLE001
            rep = {"ok": False, "reason": "error: %s" % type(e).__name__}
        rep_s = json.dumps(rep)
        _send(conn, {"rep": rep_s, "mac": _mac(self.token, b"R|", nc, _sha(req_s), rep_s)})

    def _serve_thread(self, conn):
        try:
            self._serve_one(conn)
        except Exception:                                       # noqa: BLE001
            pass
        finally:
            try:
                conn.close()
            except Exception:                                   # noqa: BLE001
                pass
            self.conns.release()

    def _make_state_dir(self):
        """Before the listener binds: on POSIX the socket lives in it (AUDIT-A D-10), and it is 0700 from its creation."""
        os.makedirs(state_dir(self.kh), mode=0o700, exist_ok=True)
        if os.name != "nt":
            os.chmod(state_dir(self.kh), 0o700)

    def _write_files(self):
        from ._safewrite import write_atomic
        self._make_state_dir()
        write_atomic(_token_path(self.kh, self.tag), self.token)
        if os.name != "nt":
            os.chmod(_token_path(self.kh, self.tag), 0o600)
        write_atomic(_pid_path(self.kh, self.tag), json.dumps(
            {"pid": os.getpid(), "proc_start": _proc_start(os.getpid()), "store": self.store,
             "version": self.code["version"], "started": time.time(),
             "token_sha": hashlib.sha256(self.token).hexdigest()[:32]}))

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

    def drop_moved(self):
        """Drop every held handle whose store moved since it was opened. Returns how many went.

        AN ERASURE LEAVES THE DAEMON'S MEMORY WITHOUT A REQUEST (3.19). The signature was compared only when a request
        arrived, so a record another process erased stayed in this process until the next prompt, or for IDLE_EXIT_S
        when none came. The watchdog calls this every HELD_CHECK_S. It drops the handle and does not reopen it: the next
        request reopens it, as a request after any write did before, so the work per prompt does not change. The check
        is the signature `handle_for` compares, from the same path; the drop is made under the answer lock, so no answer
        is using the handle."""
        def now(k):
            path, files = self.held_paths.get(k, (k, None))
            return store_signature(path, files)
        moved = []
        for k, (sig, _m) in list(self.held.items()):
            mark = store_mark(self.held_paths.get(k, (k, None))[0])
            if mark is not None and mark == self.held_marks.get(k):
                continue                                        # no byte of the store moved: nothing to compare
            if now(k) != sig:
                moved.append(k)
            else:
                self.held_marks[k] = mark                       # moved and back, or a touch: current, so remember it
        n = 0
        for k in moved:
            with self.answering:
                cur = self.held.get(k)
                if cur is None or now(k) == cur[0]:
                    continue                                    # an answer reopened it meanwhile: it is current
                del self.held[k]
                self.held_paths.pop(k, None)
                self.held_marks.pop(k, None)
                self.counts.pop(id(cur[1]), None)
                _drop_handle(cur[1])
                n += 1
        return n

    def _watchdog(self):
        # TWO CADENCES (3.19). The held stores every HELD_CHECK_S, the lifetime checks (idle, store gone, stop asked) at
        # their 3.18 cadence: run four times a second, they were most of an idle daemon's CPU.
        life_s = min(1.0, self.idle_exit_s / 4)
        next_life = time.monotonic() + life_s
        while not self.stop:
            time.sleep(min(HELD_CHECK_S, life_s))
            try:
                self.drop_moved()
            except Exception:                                   # noqa: BLE001 -- the next request still compares
                pass
            if time.monotonic() < next_life:
                continue
            next_life = time.monotonic() + life_s
            if time.monotonic() - self.last_request > self.idle_exit_s or not os.path.exists(self.store) \
                    or not os.path.isdir(self.kh) or stop_requested(self.kh, self.started_at):
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
        from . import _envpolicy
        ext = _envpolicy.decision_store().strip()           # the hook's rule (3.18, EC-6)
        if ext and os.path.exists(ext) and _real(ext) != self.store:
            e = self.handle_for(ext, lambda: open_store(ext, resolve=False))
            e.recall("warm", k=1)

    def serve(self, warm_cwd=None):
        """Run until idle, until the store is gone, or until a request of another version. Returns the exit reason."""
        from multiprocessing.connection import Listener
        warm_cwd = warm_cwd or os.getcwd()
        if stop_requested(self.kh, self.started_at):
            return "stopped"                                    # asked to stop while it was being launched: no warm-up
        if self.addr is None:
            return "no-address"                                 # no socket path fits here: nothing to listen on (3.18)
        self._make_state_dir()
        # A SLOT AND THE STORE, CLAIMED BEFORE THE WARM-UP (AUDIT-A F-2). The record is written after the warm-up, so a
        # burst of starts saw no daemon and no count, and six starts made six daemons. Both claims are O_EXCL files.
        slot = claim_slot(self.kh, self.tag)
        if slot is None:
            return "limit"
        mine = _claim_store(self.kh, self.tag)
        if mine is None:
            release_slot(slot)
            return "taken"
        try:
            return self._serve_claimed(warm_cwd)
        finally:
            release_slot(mine)
            release_slot(slot)

    def _serve_claimed(self, warm_cwd):
        from multiprocessing.connection import Listener
        _clear_stale(self.kh, self.tag)
        with _PROCESS_STATE:                                    # the streams are the process's
            saved = sys.stdout, sys.stderr
            try:
                # What the warm-up prints is nobody's answer: discarded, and the once-per-process state it spent is
                # reset before every answer (E-2).
                sys.stdout = sys.stderr = io.StringIO()
                self.warm(warm_cwd)
            except Exception:                                   # noqa: BLE001 -- a cold first answer is only slower
                pass
            finally:
                sys.stdout, sys.stderr = saved
        if os.name == "nt":
            listener = _OwnerPipeListener(self.addr)
        else:
            if os.path.exists(self.addr):
                os.unlink(self.addr)
            with _PROCESS_STATE:                                # the umask is the process's
                old = os.umask(0o177)                           # the socket is 0600 from its creation
                try:
                    # A QUEUE FOR A BURST (3.19). multiprocessing's default backlog is 1, so a second connection that
                    # came while the loop handled the first was refused at once.
                    listener = Listener(self.addr, family="AF_UNIX", backlog=2 * MAX_CONNECTIONS)
                finally:
                    os.umask(old)
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
                # A THREAD PER CONNECTION (AUDIT-A E-4). The handshake was read on the accept loop, so a connection that
                # said nothing held the next hook past its 0.3 s bound, and three of them started the back-off. Now a
                # silent connection holds only its own thread, for SERVER_READ_TIMEOUT_S; the answers stay serial.
                # A FULL HOUSE EVICTS THE OLDEST SILENT CONNECTION (3.19). Refusing the newest one let MAX_CONNECTIONS
                # silent clients refuse every hook for as long as they stayed, and three refusals put the hook into its
                # back-off. A connection that said hello is never evicted, so the thread bound and the handshake hold.
                if not self.conns.acquire(blocking=False):
                    if not (self._evict_oldest_silent() and self.conns.acquire(timeout=2 * HELLO_POLL_S + 0.05)):
                        conn.close()                            # every slot is a client past its hello: refuse this one
                        continue
                with self.pending_lock:
                    self.pending[id(conn)] = (time.monotonic(), threading.Event())
                threading.Thread(target=self._serve_thread, args=(conn,), daemon=True).start()
        finally:
            listener.close()
            self._remove_files()
        return "stopped"


def live_daemon(store_path):
    """The pid record of a live daemon for `store_path`, or None. Live means the recorded process, by pid and start
    time: a record left by a crash, whose pid now names another process, is not a daemon (AUDIT-A D-5)."""
    try:
        kh = key_home_for(store_path)
        rec = _read_record(kh, tag(store_path, kh))
        return rec if rec is not None and _record_is_live(rec) else None
    except Exception:                                           # noqa: BLE001
        return None


def stop_requested(kh, since) -> bool:
    """Whether `inspeximus hookd stop` asked the daemons of this key home to exit at or after `since` (wall time)."""
    try:
        with open(_stop_path(kh), encoding="utf-8") as fh:
            at = json.load(fh).get("at")
        return isinstance(at, (int, float)) and at >= since
    except (OSError, ValueError, AttributeError):
        return False


def request_stop(kh, wait_s=5.0) -> dict:
    """Ask every daemon of key home `kh` to exit, and wait up to `wait_s` for them to go (CL-7). The request is a file
    in the daemons' own folder, which only this user can write; each daemon's watchdog reads it within a second,
    closes its endpoint and removes its files. No process is signalled. A daemon launched after the request is
    not affected; one launched before it and still loading is (`--launched-at`). Returns {"key_home", "running_before", "stopped", "still_running"}."""
    before = live_daemon_count(kh)
    os.makedirs(state_dir(kh), mode=0o700, exist_ok=True)
    tmp = _stop_path(kh) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"at": time.time(), "pid": os.getpid()}, fh)
    os.replace(tmp, _stop_path(kh))
    end = time.monotonic() + max(0.0, wait_s)
    left = before
    while left and time.monotonic() < end:
        time.sleep(0.2)
        left = live_daemon_count(kh)
    left = live_daemon_count(kh)
    return {"key_home": kh, "running_before": before, "stopped": max(0, before - left), "still_running": left}


def _slot_path(kh, i):
    return os.path.join(state_dir(kh), "slot-%d.claim" % i)


def _claim_owner(p):
    try:
        with open(p, encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return None


def live_daemon_count(kh):
    """How many daemons hold a slot in this key home. A slot is claimed with O_EXCL before the warm-up, so a burst of
    starts sees each other's claims at once (AUDIT-A F-2: the record was written after the warm-up, and six starts made
    six daemons). A claim whose process is gone is not counted. Only claims are counted, so `maybe_start`'s attempt
    record, which sits in the same folder, is never a daemon (E-3)."""
    n = 0
    for i in range(MAX_LIVE_DAEMONS):
        rec = _claim_owner(_slot_path(kh, i))
        if rec and _record_is_live(rec):
            n += 1
    return n


def _claim_exclusive(p, me) -> bool:
    """Create the claim file `p` holding `me`, or return False when `p` exists. THE CLAIM APPEARS WITH ITS CONTENT (3.18):
    it is written to a private temp file and hard-linked to `p`, which is atomic and fails when `p` exists. An O_EXCL
    create followed by the write left `p` empty in between, and an empty claim is not live: in a burst, the losers
    refused on slots whose owners had not written yet, and a count taken then missed a daemon that held a slot (the
    3.18.0 release run's main CI, Python 3.9: 3 counted for 4; with the gap widened to 0.3 s, 0 counted in 5 of 5). Where
    the file system has no hard links, the exclusive create and write, as before."""
    tmp = "%s.%d.%d.%s.tmp" % (p, os.getpid(), threading.get_ident(), os.urandom(4).hex())
    rec = dict(me, nonce=os.urandom(8).hex())                   # which starter wrote this claim (AUDIT-A E-4)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        try:
            os.link(tmp, p)
            return True
        except FileExistsError:
            return False
        except OSError:                                         # no hard links here: the exclusive create, as before
            try:
                fd2 = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return False
            with os.fdopen(fd2, "w", encoding="utf-8") as fh:
                json.dump(rec, fh)
            # A WRITER STALLED PAST CLAIM_STALE_S (AUDIT-A E-4): its empty claim was taken for a crash's leftover and
            # replaced, and its write went to a file no longer at the path. The claim at the path decides.
            got = _claim_owner(p)
            return isinstance(got, dict) and got.get("nonce") == rec["nonce"]
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


#: A claim that is empty or cannot be read is held while it is younger than this: in the no-hard-link fallback a starter
#: writes it right after creating it. Older, it is a crash's leftover and is reclaimed. Our own stray temp files of
#: this age are removed too.
CLAIM_STALE_S = 5.0
#: How long a starter waits for another starter's reclaim before it treats the claim as held.
RECLAIM_WAIT_S = 2.0
_TEMP_NAME = None


_NO_LOCK_SAID = []
#: How often a starter reopens the reclaim lock when the file it locked is no longer the one at the path (AUDIT-A E-2).
RECLAIM_REOPENS = 5


def _try_lock(fd) -> None:
    """Take the reclaim lock on `fd` without waiting, or raise OSError."""
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, 0)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def _lock_busy(e) -> bool:
    """Whether an OSError from _try_lock means another holder (AUDIT-A E-1). Anything else, ENOLCK from a file system
    without locks for one, means the lock cannot be taken here at all."""
    import errno
    return e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES)


def _notice_no_lock(d, e):
    """One line on stderr, once per process, when the reclaim lock cannot be taken on this file system."""
    if not _NO_LOCK_SAID:
        _NO_LOCK_SAID.append(1)
        sys.stderr.write("[inspeximus] hookd: no file lock in %s (%s); a claim left by a dead daemon is not taken over "
                         "here, so it holds its slot until removed" % (d, e) + chr(10))


@contextlib.contextmanager
def _reclaim_lock(d):
    """THE RECLAIM OF A DEAD CLAIM IS SERIALIZED (3.18, AUDIT-A C-1). It was "read, decide dead, unlink", and a starter
    that decided on an old read removed the claim another starter had just made: with a dead claim and 8 starters at
    once, more than one won the store claim in 103 of 120 rounds, and two shared a slot in 65 of 120. The lock is the
    kernel's (flock, or msvcrt on Windows), not a lock file: it ends with its holder, so a starter that dies holding it
    leaves nothing to time out and no stale lock to take over, which would race the same way one level up.

    The lock counts only on the file that is at the path: a file locked after the path was removed or replaced is let
    go and the path opened again, at most RECLAIM_REOPENS times (AUDIT-A E-2). When the file system has no locks
    (ENOLCK and the like), nothing is taken over, with one line on stderr: a lock file with an age limit could give
    two winners, and not reclaiming cannot (E-1). Yields True when held, False when another reclaim did not finish
    within RECLAIM_WAIT_S or no lock can be taken here."""
    # ON POSIX THE LOCK IS ON THE STATE FOLDER ITSELF (AUDIT-A E-2). A lock file can be removed while it is held, and
    # the next starter then makes and locks a new one: with a 0.5 ms deleter, two winners in 18 of 60 rounds, inode
    # check or not. A folder holding claims cannot be removed. On Windows a file another handle has open cannot be
    # removed either, so reclaim.lock is the lock there.
    path = os.path.join(d, "reclaim.lock") if os.name == "nt" else d
    deadline = time.monotonic() + RECLAIM_WAIT_S
    fd, held = None, False
    try:
        for _ in range(RECLAIM_REOPENS):
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600) if os.name == "nt" else os.open(path, os.O_RDONLY)
            while True:
                try:
                    _try_lock(fd)
                    held = True
                    break
                except OSError as e:
                    if not _lock_busy(e):
                        _notice_no_lock(d, e)
                        break
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.005)
            if not held:
                break
            try:
                at, mine = os.stat(path), os.fstat(fd)
                same = (at.st_ino, at.st_dev) == (mine.st_ino, mine.st_dev)
            except OSError:
                same = False
            if same:
                break
            _unlock(fd)                                         # a file no longer at the path: let it go, open again
            os.close(fd)
            fd, held = None, False
        yield held
    finally:
        if fd is not None:
            if held:
                _unlock(fd)
            os.close(fd)


def _claim_dead(p) -> bool:
    """Whether the claim at `p` may be removed: a whole claim whose process is gone, or an empty or unreadable claim
    older than CLAIM_STALE_S (AUDIT-A C-2: the store claim took a young empty claim, still being written in the
    no-hard-link fallback, for a dead one). A missing claim is not dead: there is nothing to remove."""
    try:
        age = time.time() - os.stat(p).st_mtime
    except OSError:
        return False
    rec = _claim_owner(p)
    if not rec:                                                 # empty, unreadable, or not a claim
        return age > CLAIM_STALE_S
    return not _record_is_live(rec)


def _reclaim_if_dead(p) -> bool:
    """Remove the claim at `p` when it is dead, deciding again under the reclaim lock. True when `p` is free to try
    again (removed here, or already gone), False when it is held."""
    with _reclaim_lock(os.path.dirname(p)) as held:
        if not held:
            return False                                        # another reclaim is in progress: held for now
        if not os.path.exists(p):
            return True
        if not _claim_dead(p):                                  # read again under the lock: it may have become live
            return False
        try:
            os.unlink(p)
        except OSError:
            return False
        return True


def _sweep_temp(d):
    """Remove our own claim temp files older than CLAIM_STALE_S (AUDIT-A C-3: a starter killed between writing its
    temp file and removing it left one). Only this exact name pattern, only in this folder."""
    import re
    global _TEMP_NAME
    if _TEMP_NAME is None:
        _TEMP_NAME = re.compile(r"(slot-\d+|[0-9a-f]{16})\.claim\.\d+\.\d+\.[0-9a-f]{8}\.tmp")
    try:
        names = os.listdir(d)
    except OSError:
        return
    now = time.time()
    for n in names:
        if not _TEMP_NAME.fullmatch(n):
            continue
        p = os.path.join(d, n)
        try:
            if now - os.stat(p).st_mtime > CLAIM_STALE_S:
                os.unlink(p)
        except OSError:
            pass


def claim_slot(kh, t):
    """Take a free slot for the daemon of tag `t`, atomically, or return None when all MAX_LIVE_DAEMONS are held by live
    processes. A slot held by a dead process is taken over, under the reclaim lock. The claim names the pid, its start
    time and the tag."""
    os.makedirs(state_dir(kh), mode=0o700, exist_ok=True)
    _sweep_temp(state_dir(kh))
    me = {"pid": os.getpid(), "proc_start": _proc_start(os.getpid()), "tag": t}
    for _ in range(2):                                          # a second pass after clearing dead claims
        for i in range(MAX_LIVE_DAEMONS):
            p = _slot_path(kh, i)
            if _claim_exclusive(p, me):
                return p
        for i in range(MAX_LIVE_DAEMONS):
            _reclaim_if_dead(_slot_path(kh, i))
    return None


def _claim_store(kh, t):
    """The per-store twin of the slot claim: one daemon per (store, key home), also in a burst and over a dead claim.
    None when a live process holds it."""
    _sweep_temp(state_dir(kh))
    p = os.path.join(state_dir(kh), t + ".claim")
    me = {"pid": os.getpid(), "proc_start": _proc_start(os.getpid())}
    for _ in range(2):
        if _claim_exclusive(p, me):
            return p
        if not _reclaim_if_dead(p):
            return None                                         # held: live, or too young to judge
    return None


def release_slot(p):
    """Remove a slot claim this process holds. Never raises."""
    try:
        rec = _claim_owner(p)
        if rec and rec.get("pid") == os.getpid():
            os.unlink(p)
    except OSError:
        pass


def _clear_stale(kh, t):
    """At start, before anything is published: a token and a record that no live daemon owns are removed, so a token
    left by a crash cannot stand beside the new daemon's (AUDIT-A D-1)."""
    rec = _read_record(kh, t)
    if rec is not None and _record_is_live(rec):
        return False
    for p in (_token_path(kh, t), _pid_path(kh, t), _fails_path(kh, t)):
        try:
            os.unlink(p)
        except OSError:
            pass
    return True


def serve_main(argv):
    """`python -m inspeximus.claude_code --serve --expect-store <path>`: one daemon for that store, or exit at once."""
    i = argv.index("--expect-store") if "--expect-store" in argv[:-1] else -1
    if i < 0:
        return 2
    store_path = argv[i + 1]
    j = argv.index("--project") if "--project" in argv[:-1] else -1
    project = argv[j + 1] if j >= 0 else os.getcwd()
    if path_values() is None:
        return 2                                                # a relative path variable: no daemon (E-1)
    from . import _surface
    try:
        if _real(_surface.coding_store_path(project)) != _real(store_path):
            return 2                                            # the started-for store is not what this directory resolves
    except Exception:                                           # noqa: BLE001
        return 2
    if _proc_start(os.getpid()) is None:
        return 2                                                # no start time here: this daemon could never be live (F-1)
    if live_daemon(store_path):
        return 0                                                # one per (store, key home)
    k = argv.index("--launched-at") if "--launched-at" in argv[:-1] else -1
    Daemon(store_path, launched_at=argv[k + 1] if k >= 0 else None).serve(warm_cwd=project)
    return 0


def _launch_time(launched_at):
    """The wall time a stop request is counted from: the launcher's `--launched-at` when it is a number no older than
    LAUNCH_MAX_AGE_S and not in the future, else now. Without it, `inspeximus hookd stop` during the second the
    interpreter takes to load found no daemon, and the daemon then started after the request and ignored it."""
    now = time.time()
    try:
        at = float(launched_at)
    except (TypeError, ValueError):
        return now
    if not (now - LAUNCH_MAX_AGE_S <= at <= now):               # also false for nan
        return now
    return at


# ── the Windows endpoint: a pipe only this user and SYSTEM can open ────────────────────────────────────────────────
def _owner_only_sa():
    """A SECURITY_ATTRIBUTES whose descriptor grants full access to this user and to SYSTEM and to no one else, protected
    from inherited entries. The default descriptor of a named pipe also lets Everyone and Anonymous read it, which for
    a squatter-free endpoint is the difference between "another user can see the pipe" and "another user can talk to
    it" (AUDIT-A D-1). Returns (the structure, the buffer that keeps its descriptor alive)."""
    import ctypes
    from ctypes import wintypes
    adv, k32 = ctypes.WinDLL("advapi32"), ctypes.WinDLL("kernel32")   # private instances
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    tok = wintypes.HANDLE()
    if not adv.OpenProcessToken(wintypes.HANDLE(k32.GetCurrentProcess()), 0x0008, ctypes.byref(tok)):          # TOKEN_QUERY
        raise OSError("OpenProcessToken failed")
    try:
        need = wintypes.DWORD()
        adv.GetTokenInformation(tok, 1, None, 0, ctypes.byref(need))                          # TokenUser
        buf = ctypes.create_string_buffer(need.value)
        if not adv.GetTokenInformation(tok, 1, buf, need, ctypes.byref(need)):
            raise OSError("GetTokenInformation failed")
    finally:
        k32.CloseHandle(tok)
    psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
    ssid = wintypes.LPWSTR()
    if not adv.ConvertSidToStringSidW(ctypes.c_void_p(psid), ctypes.byref(ssid)):
        raise OSError("ConvertSidToStringSidW failed")
    sid = ssid.value
    k32.LocalFree(ssid)
    psd = ctypes.c_void_p()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            "D:P(A;;GA;;;%s)(A;;GA;;;SY)" % sid, 1, ctypes.byref(psd), None):
        raise OSError("ConvertStringSecurityDescriptorToSecurityDescriptorW failed")

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]
    sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), psd, False)
    return sa, psd


if os.name == "nt":
    from multiprocessing.connection import PipeListener as _PipeListener

    class _OwnerPipeListener(_PipeListener):
        """multiprocessing's pipe listener, with an owner-only descriptor on every instance and remote clients refused."""

        def __init__(self, address, backlog=None):
            import ctypes
            self._sa, self._psd = _owner_only_sa()
            self._sa_ptr = ctypes.addressof(self._sa)
            super().__init__(address, backlog)

        def _new_handle(self, first=False):
            import _winapi
            from multiprocessing.connection import BUFSIZE
            flags = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
            if first:
                flags |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
            # Through ctypes: _winapi.CreateNamedPipe takes no descriptor (it refused one with WinError 123, measured on
            # 3.12). The handle it returns is an ordinary pipe handle, which accept() and PipeConnection take as they are.
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateNamedPipeW.restype = wintypes.HANDLE
            k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                             wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
            h = k32.CreateNamedPipeW(
                self._address, flags,
                _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | _winapi.PIPE_WAIT
                | 0x00000008,                                   # PIPE_REJECT_REMOTE_CLIENTS
                _winapi.PIPE_UNLIMITED_INSTANCES, BUFSIZE, BUFSIZE, _winapi.NMPWAIT_WAIT_FOREVER, self._sa_ptr)
            if h is None or h == wintypes.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            return h
