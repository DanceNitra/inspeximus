"""`inspeximus reembed` builds the embedder the way the MCP server does (3.17.0, AUDIT-A P-4).

Measured by AUDIT-A on the head before this change: the CLI built its own embedder with no `embed_id`. Its vectors
carried no recipe stamp, `<store>.embedid` was never written, a nomic model got no task prefixes, and a plain run after a
model change replaced nothing. The 3.17.0 headline gives every user with an embedder this one action.
"""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import _http, cli  # noqa: E402
from inspeximus import sqlite_store as S  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

URL = "http://127.0.0.1:9/v1/embeddings"
DIM = 8


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    home = tmp_path / "keyhome"
    home.mkdir()
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(home))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", URL)
    sent = []

    def fake_post(url, body, headers, timeout):
        sent.append((body["model"], body["input"]))
        h = sum(map(ord, body["model"] + body["input"])) % 97
        return {"data": [{"embedding": [round(((h * (i + 3)) % 97) / 97.0 - 0.5, 6) for i in range(DIM)]}]}
    monkeypatch.setattr(_http, "post_json", fake_post)
    return tmp_path, sent


def _docs(p):
    con = sqlite3.connect(str(p))
    try:
        return [json.loads(d) for (d,) in con.execute("SELECT doc FROM records")]
    finally:
        con.close()


def _seed(p):
    m = Inspeximus(str(p))                       # records with no vector at all, as a store written before 3.17.0
    for i in range(3):
        m.remember("fact %d about the deploy window" % i, key="k%d" % i)
    m.flush()


def _tags(p):
    return {d[S.VEC_KEY].partition(":")[0] for d in _docs(p) if d.get("status") == "active" and d.get(S.VEC_KEY)}


def test_a_plain_reembed_tags_its_vectors_and_writes_the_sidecar(env, monkeypatch, capsys):
    tmp, _sent = env
    p = tmp / "s.json"
    _seed(p)
    monkeypatch.setenv("INSPEXIMUS_EMBED_MODEL", "model-a")
    assert cli.main(["--path", str(p), "reembed"]) in (0, None)
    assert _tags(p) == {S.recipe_tag("model-a", DIM)}, "the vectors carry no recipe stamp"
    assert open(str(p) + ".embedid", encoding="utf-8").read() == "model-a"


def test_a_plain_reembed_after_a_model_change_replaces_the_foreign_vectors(env, monkeypatch, capsys):
    tmp, _sent = env
    p = tmp / "s.json"
    _seed(p)
    monkeypatch.setenv("INSPEXIMUS_EMBED_MODEL", "model-a")
    cli.main(["--path", str(p), "reembed"])
    monkeypatch.setenv("INSPEXIMUS_EMBED_MODEL", "model-b")
    cli.main(["--path", str(p), "reembed"])
    assert _tags(p) == {S.recipe_tag("model-b", DIM)}, "a plain run after a model change replaced nothing"
    assert open(str(p) + ".embedid", encoding="utf-8").read() == "model-b"


def test_a_nomic_model_gets_its_task_prefixes_and_its_recipe(env, monkeypatch, capsys):
    tmp, sent = env
    p = tmp / "s.json"
    _seed(p)
    monkeypatch.setenv("INSPEXIMUS_EMBED_MODEL", "nomic-embed-text")
    cli.main(["--path", str(p), "reembed"])
    texts = [t for (_m, t) in sent if "fact" in t]
    assert texts and all(t.startswith("search_document: ") for t in texts), texts
    assert open(str(p) + ".embedid", encoding="utf-8").read() == "nomic-embed-text|nomic-sd-sq"
    assert _tags(p) == {S.recipe_tag("nomic-embed-text|nomic-sd-sq", DIM)}


def test_the_opt_out_of_the_prefixes_is_honoured(env, monkeypatch, capsys):
    tmp, sent = env
    p = tmp / "s.json"
    _seed(p)
    monkeypatch.setenv("INSPEXIMUS_EMBED_MODEL", "nomic-embed-text")
    monkeypatch.setenv("INSPEXIMUS_NOMIC_PREFIX", "0")
    cli.main(["--path", str(p), "reembed"])
    assert not any(t.startswith("search_") for (_m, t) in sent)
    assert open(str(p) + ".embedid", encoding="utf-8").read() == "nomic-embed-text"


def test_the_cli_and_the_server_build_the_same_recipe(env, monkeypatch):
    from inspeximus import _embedders
    monkeypatch.setenv("INSPEXIMUS_EMBED_MODEL", "nomic-embed-text")
    assert cli._embedders()[2] == _embedders.make_embedders()[2] == "nomic-embed-text|nomic-sd-sq"
    monkeypatch.setenv("INSPEXIMUS_EMBED_MODEL", "other")
    assert cli._embedders()[2] == "other" and cli._embedders()[1] is None


def test_without_a_url_there_is_no_embedder(monkeypatch):
    monkeypatch.delenv("INSPEXIMUS_EMBED_URL", raising=False)
    assert cli._embedders() == (None, None, None) and cli._embedder() is None


def test_the_cli_store_embeds_the_query_with_the_query_prefix(env, monkeypatch, capsys):
    tmp, sent = env
    p = tmp / "s.json"
    _seed(p)
    monkeypatch.setenv("INSPEXIMUS_EMBED_MODEL", "nomic-embed-text")
    cli.main(["--path", str(p), "reembed"])
    del sent[:]
    cli._store(str(p)).recall("deploy window", mode="semantic")     # a store this small is lexical under mode=auto
    queries = [t for (_m, t) in sent]
    assert queries and all(t.startswith("search_query: ") for t in queries), queries
