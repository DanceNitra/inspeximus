"""3.20, F2 (Claude Security scan, LOW after AUDIT-A's triage): a timestamp proof cannot choose where the verifier connects.

THE DEFECT. A pending attestation's calendar URI is bytes the proof's author chose, and `opentimestamps.upgrade` opened
it with urllib's full default opener: http, ftp and file, redirects followed. Measured on v3.19.1 (AUDIT-A's
ots_repro.py): a URI `http://127.0.0.1:<port>/admin/action?x=` made the verifier GET that local path, and a `file:///`
URI was read from disk and parsed as a calendar answer. On Windows a `file:` URI is a UNC path, which can leak the
user's NTLM hash to the host it names.

WHAT IS PINNED HERE. `upgrade` asks only https calendars that `calendars` names (DEFAULT_CALENDARS, the four public
OpenTimestamps calendars, by default), whatever the proof says, and a calendar it will not ask gets a row saying so;
the rule holds for a caller's own `fetch` too. The default opener has no handler but HTTPS: no file, ftp, plain http or
redirect. A calendar's answer is capped at MAX_CALENDAR_RESPONSE.
"""
import http.server
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import opentimestamps as ots  # noqa: E402


def _varu(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _vb(b):
    return _varu(len(b)) + b


def _pending(uri, digest=b"\x11" * 32):
    return ots.MAGIC + b"\x01" + b"\x08" + digest + b"\x00" + ots.PENDING_TAG + _vb(_vb(uri.encode()))


@pytest.fixture
def local_server():
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], hits
    srv.shutdown()


def test_a_proof_cannot_send_the_verifier_to_a_local_http_service(local_server):
    port, hits = local_server
    out = ots.upgrade(_pending("http://127.0.0.1:%d/admin/action" % port), timeout=5)
    assert hits == [], "the verifier connected to %r" % hits
    assert out["calendars"][0]["error"].startswith("not asked") and "not https" in out["calendars"][0]["error"]


def test_a_proof_cannot_make_the_verifier_read_a_local_file(tmp_path):
    d = tmp_path / "cal"
    (d / "timestamp").mkdir(parents=True)
    (d / "timestamp" / ("11" * 32)).write_bytes(b"\x00" + ots.BITCOIN_TAG + _vb(_varu(123456)))
    out = ots.upgrade(_pending("file:///" + str(d).replace("\\", "/")), timeout=5)
    row = out["calendars"][0]
    assert row.get("bitcoin") is None and row["error"].startswith("not asked"), row


def test_an_https_host_that_is_not_a_calendar_is_not_asked_even_with_a_custom_fetch():
    asked = []
    out = ots.upgrade(_pending("https://attacker.example/x"), fetch=lambda url: asked.append(url) or b"")
    assert asked == [] and "not a calendar this verifier asks" in out["calendars"][0]["error"]


def test_a_listed_calendar_is_asked_and_a_caller_can_add_one():
    asked = []
    fetch = lambda url: asked.append(url) or b""                       # noqa: E731
    ots.upgrade(_pending(ots.DEFAULT_CALENDARS[0]), fetch=fetch)
    assert asked == [ots.DEFAULT_CALENDARS[0] + "/timestamp/" + "11" * 32], "CONTROL: a real calendar is asked"
    ots.upgrade(_pending("https://my.calendar.test"), fetch=fetch, calendars=("https://my.calendar.test",))
    assert asked[-1].startswith("https://my.calendar.test/timestamp/")


@pytest.mark.parametrize("uri", ["https://user:pw@alice.btc.calendar.opentimestamps.org",
                                 "https://alice.btc.calendar.opentimestamps.org?x=1",
                                 "https://alice.btc.calendar.opentimestamps.org:8443",
                                 "ftp://alice.btc.calendar.opentimestamps.org"])
def test_a_listed_host_with_userinfo_a_query_another_port_or_scheme_is_not_asked(uri):
    assert ots.calendar_refusal(uri) is not None


def test_a_calendar_answer_over_the_cap_is_refused():
    big = b"\x00" * (ots.MAX_CALENDAR_RESPONSE + 1)
    out = ots.upgrade(_pending(ots.DEFAULT_CALENDARS[0]), fetch=lambda url: big)
    assert "more than" in out["calendars"][0]["error"]


def test_the_default_opener_handles_https_and_nothing_else():
    import urllib.request
    handlers = {type(h) for h in ots._https_only_fetch(5).opener.handlers}
    assert urllib.request.HTTPSHandler in handlers
    for bad in (urllib.request.HTTPHandler, urllib.request.FileHandler, urllib.request.FTPHandler,
                urllib.request.HTTPRedirectHandler, urllib.request.DataHandler):
        assert bad not in handlers, bad
