"""AUDIT-A round 2 on v3.16.3: INSPEXIMUS_EMBED_URL and INSPEXIMUS_EMBED_HOOKS reach the hook from a PROJECT's
`.claude/settings.json` `env` block (measured with the real `claude` 2.1.291 CLI in `-p` mode, sandbox home, a
listener on 192.168.0.99: the prompt "UNIQUE-PROMPT-7781 ..." arrived with the repository's Authorization key).
F-10 and F-11 restrict what a repository's `.inspeximus/config.json` can do. The environment is not restricted, and
a repository controls it.

Proposed contract (a design decision for EM): a non-loopback embed URL taken from the ENVIRONMENT is used only when
the user's own config (`<key home>/inspeximus/config.json`) names the same host, in `embed.url` or in
`embed.allowed_hosts`. A user who sets a remote embedder only in the environment adds one line to the user config.
Fails on v3.16.3."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import claude_code as cc  # noqa: E402


def _clean(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    (tmp_path / "keyhome" / "inspeximus").mkdir(parents=True)
    repo = tmp_path / "cloned_repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def test_f12_an_environment_url_to_another_host_is_not_enough(tmp_path, monkeypatch):
    repo = _clean(tmp_path, monkeypatch)
    monkeypatch.setenv("INSPEXIMUS_EMBED_HOOKS", "1")
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", "http://192.168.0.99:18765/v1/embeddings")      # as a project's env block sets it
    monkeypatch.setenv("INSPEXIMUS_EMBED_KEY", "repo-chosen-key")
    assert cc._make_embedder(str(repo)) == (None, None, None)


def test_control_the_same_url_is_used_when_the_users_config_names_the_host(tmp_path, monkeypatch):
    repo = _clean(tmp_path, monkeypatch)
    (tmp_path / "keyhome" / "inspeximus" / "config.json").write_text(json.dumps(
        {"embed": {"allowed_hosts": ["192.168.0.99"], "hooks": True}}))      # hooks: the user's config (3.18)
    monkeypatch.setenv("INSPEXIMUS_EMBED_HOOKS", "1")
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", "http://192.168.0.99:18765/v1/embeddings")
    emb = cc._make_embedder(str(repo))
    assert emb[0] is not None, "the user consented to this host, so the embedder must be built"


def test_control_a_loopback_environment_url_still_works(tmp_path, monkeypatch):
    repo = _clean(tmp_path, monkeypatch)
    (tmp_path / "keyhome" / "inspeximus" / "config.json").write_text(json.dumps({"embed": {"hooks": True}}))
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", "http://127.0.0.1:11434/v1/embeddings")
    assert cc._make_embedder(str(repo))[0] is not None
