"""AUDIT-A round 2 on fix/3164 (0613c2cd): `_http.post_json` reads the whole answer with `r.read()` and the embedder
returns whatever vector the endpoint names. Measured: a loopback endpoint that answered with 200 MB made the call return
a vector of 52,428,801 floats after 6.8 s (about 2.5 GB as Python floats), and the hook would then write that vector
into the user's store. `timeout` bounds one socket wait, not the answer. An endpoint reached through the environment
(F-12) or a buggy local server can do this on every capture and prompt.
Contract proposed: an answer over 8 MB raises, and an embedding longer than 16,384 numbers, or with a value that is not
a finite number, is refused. Fails on 0613c2cd."""
import http.server
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import _http  # noqa: E402


def _serve(body: bytes):
    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def test_f17_an_answer_over_the_limit_is_refused():
    body = b'{"data":[{"embedding":[' + b"0.5," * (6 * 1024 * 1024) + b'0.5]}]}'        # about 24 MB
    s = _serve(body)
    try:
        answer = None
        try:
            answer = _http.post_json("http://127.0.0.1:%d/v1/embeddings" % s.server_address[1], {"input": "x"}, None, 30)
        except Exception:
            pass                                    # a refusal is the contract
        assert answer is None, "returned %d numbers instead of refusing" % len(answer["data"][0]["embedding"])
    finally:
        s.shutdown()


def test_control_a_normal_answer_still_works():
    body = b'{"data":[{"embedding":[' + b"0.5," * 767 + b'0.5]}]}'
    s = _serve(body)
    try:
        out = _http.post_json("http://127.0.0.1:%d/v1/embeddings" % s.server_address[1], {"input": "x"}, None, 30)
        assert len(out["data"][0]["embedding"]) == 768
    finally:
        s.shutdown()
