"""An embedder with nothing to rank against says so, in words, on every surface (3.16.6).

Measured 2026-10-07: a 13,489-record store sat behind two MCP servers, one with an embedder configured,
and held 0 vectors. Opening a store embeds nothing and nothing backfills, so with persist_vectors off
the index held only what that process wrote and was dropped at exit. `index_coherence` returned
`coherent: false` beside `missing_vecs`, and its note promised a backfill that does not exist. The
server's help text said the opposite: "every open re-embeds every record".

So `index_coherence` now counts the vectors and names the problem with its remedy, and the server
prints one line at start and reports the same posture in `where_am_i`.
"""
from __future__ import annotations

import io
import os

from inspeximus import Inspeximus as Memory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVER = io.open(os.path.join(ROOT, "inspeximus", "mcp_server.py"), encoding="utf-8").read()


def _emb(text):
    return [float(len(text) % 7) + 1.0, 1.0, 0.5]


def _store(tmp_path, n=4):
    """Written with an embedder and persist off, so the file on disk carries no vectors."""
    p = str(tmp_path / "m.json")
    m = Memory(path=p, embed=_emb)
    for i in range(n):
        m.remember("fact number %d about the deploy window" % i)
    m.flush()
    return p


def test_a_fresh_open_with_an_embedder_and_no_vectors_names_the_problem(tmp_path):
    p = _store(tmp_path)
    m = Memory(path=p, embed=_emb)
    assert m.embed is not None, "precondition: the embedder reached the handle"
    res = m.index_coherence()
    assert res["vectors"] == 0 and res["active_text_records"] == 4, res
    assert res["coherent"] is False, res
    assert any("none of the 4 active records has a vector" in x for x in res["problems"]), res
    assert any("persist_vectors=True" in x for x in res["problems"]), res
    assert "backfill re-embeds" not in res["note"], "the note promised a backfill that does not exist"


def test_a_partial_index_counts_what_is_missing(tmp_path):
    p = _store(tmp_path)
    m = Memory(path=p, embed=_emb, persist_vectors=True)
    m.reembed()
    m.items[0]["vec"] = None
    res = m.index_coherence()
    assert res["vectors"] == 3, res
    assert any("1 of 4 active records have no vector" in x for x in res["problems"]), res
    assert not any("persist_vectors=True" in x for x in res["problems"]), (
        "the persist remedy is named on a store that already persists", res)


def test_control_a_full_index_reports_no_problem(tmp_path):
    """CONTROL. Without it, a `problems` list that is never empty would pass every test above."""
    p = _store(tmp_path)
    m = Memory(path=p, embed=_emb, persist_vectors=True)
    m.reembed()
    res = m.index_coherence()
    assert res["vectors"] == 4 and res["problems"] == [] and res["coherent"] is True, res


def test_control_no_embedder_is_not_a_problem(tmp_path):
    """CONTROL. A lexical-only store has no index to be missing."""
    p = _store(tmp_path)
    res = Memory(path=p).index_coherence()
    assert res["problems"] == [] and res["coherent"] is True, res


def test_the_reembed_warning_does_not_promise_a_re_embed_on_open(tmp_path):
    p = _store(tmp_path)
    out = Memory(path=p, embed=_emb).reembed()
    assert out["reembedded"] == 4, out
    assert "re-embeds again" not in out["warning"] and "starts with none" in out["warning"], out


def test_the_server_reports_the_posture_at_start_and_in_where_am_i():
    """Read as text: importing the server needs the MCP SDK, which CI does not install (see
    test_the_server_can_keep_the_vectors_it_paid_for)."""
    assert '"semantic_index": _vector_posture()' in _SERVER
    start = _SERVER.split("def _vector_posture", 1)[1]
    assert 'print("[inspeximus-mcp] %s" % _VP["problem"], file=sys.stderr)' in start
    assert "every open re-embeds" not in _SERVER, "the help text still describes a re-embed that never runs"


def _start_server(tmp_path, embed_url):
    """Start the real server module on a stored file and read its stderr and where_am_i."""
    import json
    import subprocess
    import sys
    import pytest
    pytest.importorskip("mcp")
    p = _store(tmp_path)
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(INSPEXIMUS_PATH=p, INSPEXIMUS_KEY_HOME=str(tmp_path / "keys"), INSPEXIMUS_NO_UPDATE_CHECK="1",
               PYTHONIOENCODING="utf-8")
    if embed_url:
        env["INSPEXIMUS_EMBED_URL"] = embed_url        # a closed loopback port: opening must not call it
    code = ("import json, inspeximus.mcp_server as s\n"
            "print('WAI' + json.dumps(s.where_am_i()['semantic_index']))\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    assert "WAI" in r.stdout, r.stderr[-800:]
    return json.loads(r.stdout.split("WAI", 1)[1]), r.stderr


def test_the_server_says_it_at_start_and_in_where_am_i(tmp_path):
    wai, err = _start_server(tmp_path, "http://127.0.0.1:9/v1/embeddings")
    lines = [ln for ln in err.splitlines() if "none of the 4 active records has a vector" in ln]
    assert len(lines) == 1, err[-800:]
    # persist_vectors is True since 3.17.0: on by default when an embedder is configured.
    assert wai["vectors"] == 0 and wai["active_text_records"] == 4 and wai["persist_vectors"] is True, wai
    assert "none of the 4 active records" in (wai["problem"] or ""), wai


def test_control_a_server_with_no_embedder_prints_nothing_about_vectors(tmp_path):
    """CONTROL. A startup line printed for every store would pass the test above and mean nothing."""
    wai, err = _start_server(tmp_path, None)
    assert "has a vector" not in err and "have no vector" not in err, err[-800:]
    assert wai["problem"] is None and wai["vectors"] == 0, wai
