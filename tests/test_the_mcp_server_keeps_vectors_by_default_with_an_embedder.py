"""3.17.0: the MCP server keeps its vectors by default when it has an embedder.

Off, a server with an embedder ranks only what it wrote since it started; a 13,489-record store behind such
a server held 0 vectors. With an embedder and INSPEXIMUS_PERSIST_VECTORS unset, the server now persists
vectors (float16, tagged with the recipe). INSPEXIMUS_PERSIST_VECTORS=0 turns it off. Without an embedder
nothing changes. The hooks are not part of this: they open their store their own way. Nothing is embedded at
start; the start line names `inspeximus reembed` and what it costs.
"""
from __future__ import annotations

import http.server
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVER = io.open(os.path.join(ROOT, "inspeximus", "mcp_server.py"), encoding="utf-8").read()
_HOOKS = io.open(os.path.join(ROOT, "inspeximus", "claude_code.py"), encoding="utf-8").read()


def _fns():
    """The two parsers, compiled from the shipped file without importing the server (no MCP SDK needed)."""
    ns = {"os": os}
    for name in ("_flag_from_env", "_persist_default"):
        body = _SERVER.split("def %s(" % name, 1)[1].split("\n\n\n", 1)[0]
        exec("def %s(" % name + body, ns)                           # noqa: S102
    return ns["_persist_default"]


@pytest.mark.parametrize("env,has_embedder,want", [
    ({}, True, True),                                             # the new default
    ({"INSPEXIMUS_PERSIST_VECTORS": ""}, True, True),             # empty reads as unset
    ({"INSPEXIMUS_PERSIST_VECTORS": "0"}, True, False),           # the opt-out
    ({"INSPEXIMUS_PERSIST_VECTORS": "off"}, True, False),
    ({}, False, False),                                           # no embedder: nothing to keep
    ({"INSPEXIMUS_PERSIST_VECTORS": "1"}, False, True),           # an explicit yes still means yes
])
def test_the_default_follows_the_embedder_and_the_variable_still_decides(env, has_embedder, want):
    assert _fns()(env, has_embedder=has_embedder) is want


def test_the_server_derives_the_flag_from_the_embedder():
    assert "_PERSIST_VECTORS = _persist_default(has_embedder=_EMB_DOC is not None)" in _SERVER


def test_the_hooks_are_unchanged():
    """The hooks open the coding store with persist_vectors=True of their own, and read no default here."""
    assert "_persist_default" not in _HOOKS and "INSPEXIMUS_PERSIST_VECTORS" not in _HOOKS
    assert "embed_id=emb_id, persist_vectors=True)" in _HOOKS


# ── the real server module, started in a subprocess against a loopback embedder ──────────────────────
class _Embed(http.server.BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):                                            # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        text = json.loads(self.rfile.read(n) or b"{}").get("input", "")
        _Embed.calls += 1
        h = sum(map(ord, text)) % 97
        body = json.dumps({"data": [{"embedding": [((h * (i + 3)) % 97) / 97.0 - 0.5 for i in range(16)]}]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def embedder():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Embed)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    _Embed.calls = 0
    yield "http://127.0.0.1:%d/v1/embeddings" % srv.server_address[1]
    srv.shutdown()


def _store(tmp_path, n=5):
    sys.path.insert(0, ROOT)
    from inspeximus import Inspeximus
    p = str(tmp_path / "m.json")
    m = Inspeximus(path=p)
    for i in range(n):
        m.remember("an older record %d about the deploy window" % i)
    m.flush()
    return p


def _run(tmp_path, p, url, extra_env=None, code=""):
    pytest.importorskip("mcp")
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(INSPEXIMUS_PATH=p, INSPEXIMUS_KEY_HOME=str(tmp_path / "keys"), INSPEXIMUS_NO_UPDATE_CHECK="1",
               PYTHONIOENCODING="utf-8", INSPEXIMUS_EMBED_MODEL="test-model")
    if url:
        env["INSPEXIMUS_EMBED_URL"] = url
    env.update(extra_env or {})
    src = ("import json, inspeximus.mcp_server as s\n" + code +
           "print('WAI' + json.dumps(s.where_am_i()['semantic_index']))\n")
    r = subprocess.run([sys.executable, "-c", src], cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    assert "WAI" in r.stdout, r.stderr[-800:]
    return json.loads(r.stdout.split("WAI", 1)[1]), r.stderr


def test_with_an_embedder_the_server_persists_new_vectors_and_embeds_nothing_at_start(tmp_path, embedder):
    p = _store(tmp_path)
    wai, err = _run(tmp_path, p, embedder)
    assert wai["persist_vectors"] is True, wai
    assert _Embed.calls == 0, "starting the server embedded %d record(s)" % _Embed.calls
    _, err = _run(tmp_path, p, embedder, code="s._MEM.remember('a new record the server writes'); s._MEM.flush()\n")
    assert _Embed.calls == 1, "one write, one embedding call: %d" % _Embed.calls
    con = sqlite3.connect(p)
    docs = [json.loads(d) for (d,) in con.execute("SELECT doc FROM records")]
    con.close()
    new = [d for d in docs if d.get("text") == "a new record the server writes"]
    assert new and ":" in new[0].get("vec16", "")[:9], "the new record's vector was not kept as tagged float16"
    assert sum(1 for d in docs if "vec16" in d) == 1, "the older records were embedded at start"
    line = [ln for ln in err.splitlines() if "inspeximus reembed" in ln]
    assert len(line) == 1 and "85 ms" in line[0] and "minute" in line[0] and "5 records" in line[0], err[-800:]


def test_the_opt_out_keeps_the_old_behaviour(tmp_path, embedder):
    p = _store(tmp_path)
    wai, err = _run(tmp_path, p, embedder, {"INSPEXIMUS_PERSIST_VECTORS": "0"},
                    code="s._MEM.remember('a new record the server writes'); s._MEM.flush()\n")
    assert wai["persist_vectors"] is False, wai
    con = sqlite3.connect(p)
    assert not any("vec16" in json.loads(d) for (d,) in con.execute("SELECT doc FROM records"))
    con.close()
    assert "persist_vectors=True" in err, "the opt-out start line no longer names how to keep the vectors"


def test_without_an_embedder_nothing_changes(tmp_path):
    p = _store(tmp_path)
    wai, err = _run(tmp_path, p, None)
    assert wai["persist_vectors"] is False and wai["problem"] is None, wai
    assert "reembed" not in err
