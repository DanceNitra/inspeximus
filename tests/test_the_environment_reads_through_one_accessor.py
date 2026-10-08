"""3.18, AUDIT-A envclass review EC-1 to EC-7: every INSPEXIMUS_* read in shipped code goes through
inspeximus/_envpolicy, and the variables the review found still harmful from a project's environment follow the rule
EM decided for each.

EC-1 ARCHIVE_AUTO: the environment may switch the archive off, never on; the user's config wins.
EC-2 RECEIPTS: the environment starts a chain only when the user's config names a signing key.
EC-3 key files: the key-file rule includes the project the process runs in (a project without .git).
EC-4 BUSY_TIMEOUT_S and SAVE_RETRIES: bounded above (120 s, 20).
EC-5 one accessor: the AST scan in tests/_env_read_scan.py, with a control for every shape it must catch.
EC-6 TRUST_SEEDS and EMBED_HOOKS config-only; DECISION_STORE not inside a git work tree or the project.
EC-7 RECEIPT_KEY: a 64-hex value from the environment is ignored; a path follows the key-file rule.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env_read_scan as scan  # noqa: E402
from inspeximus import _envpolicy, _keyhome, _userconfig  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    _userconfig._SAID.clear()
    _keyhome._CACHE.clear()
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_") and k != "INSPEXIMUS_KEY_HOME"]:
        monkeypatch.delenv(k)
    yield
    _keyhome._CACHE.clear()


def _git(d):
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    return d


# ── EC-5: the scan ───────────────────────────────────────────────────────────────────────────────────────────
def test_no_shipped_code_reads_an_inspeximus_variable_outside_envpolicy():
    files = scan.shipped_files()
    rel = {os.path.relpath(f, ROOT).replace(os.sep, "/") for f in files}
    assert {"inspeximus/install_all.py", "inspeximus/integrations/hermes_agent.py",
            "inspeximus/probes/a_contradiction_one_channel_cannot_see.py"} <= rel, \
        "CONTROL: the scan reaches the package, its integrations and its probes"
    assert scan.all_violations() == []


@pytest.mark.parametrize("src", [
    'import os\nx = os.environ.get("INSPEXIMUS_PATH")',
    'import os\nx = os.getenv("INSPEXIMUS_PATH")',
    'def f(env):\n    return env.get("INSPEXIMUS_PATH")',
    'import os\nx = os.environ["INSPEXIMUS_PATH"]',
    'import os\nx = "INSPEXIMUS_PATH" in os.environ',
    'import os\ndef f(n):\n    return os.environ.get(f"INSPEXIMUS_{n}")',
    'import os\ndef f(n):\n    return os.environ.get("INSPEXIMUS_" + n)',
    'import os\ndef f(n):\n    return os.environ.get(n)',
    'import os\ndef f(n):\n    return os.environ[n]',
    'import os\nx = {k: v for k, v in os.environ.items() if k.startswith("INSPEXIMUS_")}',
    'import os\nfor var in ("INSPEXIMUS_A", "INSPEXIMUS_B"):\n    os.environ.get(var)',
    'NAMES = ["INSPEXIMUS_PATH"]',
])
def test_the_scan_catches_every_shape_of_read(src):
    assert scan.violations(os.path.join(ROOT, "inspeximus", "x.py"), src), "the scan missed: %s" % src


@pytest.mark.parametrize("src", [
    'from . import _envpolicy\nx = _envpolicy.raw("INSPEXIMUS_PATH")',
    'import os\nos.environ["INSPEXIMUS_KEY_HOME"] = "k"',
    'x = {"INSPEXIMUS_PATH": "p"}',
    'def f(src):\n    return src == "INSPEXIMUS_RECEIPT_KEY_FILE"',
    'def f():\n    return "INSPEXIMUS_PATH"',
    'def f(e):\n    return env_url("INSPEXIMUS_EMBED_URL")',
])
def test_a_write_or_a_read_through_the_accessor_is_not_flagged(src):
    assert scan.violations(os.path.join(ROOT, "inspeximus", "x.py"), src) == []


def test_raw_refuses_a_config_only_variable(monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_TRUST_SEEDS", "key:ab")
    with pytest.raises(ValueError):
        _envpolicy.raw("INSPEXIMUS_TRUST_SEEDS")


def test_the_compliance_cli_resolves_its_store_through_the_vetted_resolver(tmp_path, monkeypatch):
    """compliance.py read INSPEXIMUS_PATH unvetted; it now resolves through `_surface.resolve_path`, which refuses a
    link out of the project the same way every other surface does."""
    import inspeximus.compliance as c
    src = open(c.__file__, encoding="utf-8").read()
    assert "resolve_path()" in src and 'environ.get("INSPEXIMUS_PATH")' not in src


def test_install_all_reads_the_process_writer_key_by_the_servers_rule(tmp_path, monkeypatch, user_config):
    from inspeximus import install_all
    monkeypatch.setattr(install_all._i, "read_entry", lambda h: (None, None, None))
    monkeypatch.setenv("INSPEXIMUS_WRITER_KEY", "ab" * 32)
    assert install_all._writer_key([], str(tmp_path / "s.json")) is None, "a raw key from the environment was used"
    repo = _git(tmp_path / "repo")
    (repo / "w.key").write_text("cd" * 32, encoding="utf-8")
    monkeypatch.setenv("INSPEXIMUS_WRITER_KEY_FILE", str(repo / "w.key"))
    assert install_all._writer_key([], str(tmp_path / "s.json")) is None, "a key file inside a git tree was used"
    user_config(INSPEXIMUS_WRITER_KEY="ef" * 32)
    assert install_all._writer_key([], str(tmp_path / "s.json")) == "ef" * 32


# ── EC-1 ARCHIVE_AUTO ────────────────────────────────────────────────────────────────────────────────────────
def test_the_environment_can_switch_the_archive_off_and_never_on(monkeypatch, capsys):
    monkeypatch.setenv("INSPEXIMUS_ARCHIVE_AUTO", "1")
    assert _envpolicy.switch_off_only("INSPEXIMUS_ARCHIVE_AUTO", None) is None
    assert "INSPEXIMUS_ARCHIVE_AUTO is set in the environment and is ignored" in capsys.readouterr().err
    assert _envpolicy.switch_off_only("INSPEXIMUS_ARCHIVE_AUTO", False) is False, "the user's off lost to the env"
    monkeypatch.setenv("INSPEXIMUS_ARCHIVE_AUTO", "0")
    assert _envpolicy.switch_off_only("INSPEXIMUS_ARCHIVE_AUTO", None) is False
    assert _envpolicy.switch_off_only("INSPEXIMUS_ARCHIVE_AUTO", True) is True, "the user's config wins"


def test_the_hook_archive_policy_follows_the_rule(tmp_path, monkeypatch, user_config):
    from inspeximus import claude_code
    kh = user_config(INSPEXIMUS_ARCHIVE_AUTO="0")             # archive.auto: false in the user's config
    monkeypatch.setenv("INSPEXIMUS_ARCHIVE_AUTO", "1")
    assert claude_code.archive_policy(str(tmp_path)).get("auto") is False
    os.remove(os.path.join(kh, "inspeximus", "config.json"))
    _userconfig._CACHE.clear()
    monkeypatch.setenv("INSPEXIMUS_ARCHIVE_AUTO", "0")
    assert claude_code.archive_policy(str(tmp_path)).get("auto") is False


# ── EC-2 RECEIPTS ────────────────────────────────────────────────────────────────────────────────────────────
def test_receipts_from_the_environment_need_a_signing_key_in_the_users_config(tmp_path, monkeypatch, user_config,
                                                                             capsys):
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS", "1")
    kh = user_config()
    assert _envpolicy.receipts_from_env() is False
    assert "INSPEXIMUS_RECEIPTS is set in the environment and is ignored" in capsys.readouterr().err
    import _userconfig_env
    _userconfig_env.write_user_config(kh, {"receipts": {"key_file": str(tmp_path / "r.key")}})
    assert _envpolicy.receipts_from_env() is True
    monkeypatch.delenv("INSPEXIMUS_RECEIPTS")
    assert _envpolicy.receipts_from_env() is False
    user_config(INSPEXIMUS_RECEIPTS="1")
    assert _envpolicy.receipts_from_env() is True


# ── EC-3 the cwd's project ───────────────────────────────────────────────────────────────────────────────────
def test_a_key_file_inside_the_project_the_process_runs_in_is_refused_without_git(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = home / "zip-download"
    (proj / ".claude").mkdir(parents=True)
    (proj / "w.key").write_text("ab" * 32, encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(proj)
    monkeypatch.setenv("INSPEXIMUS_WRITER_KEY_FILE", str(proj / "w.key"))
    assert _envpolicy.key_file("INSPEXIMUS_WRITER_KEY_FILE") == (None, None)
    outside = tmp_path / "keys"
    outside.mkdir()
    (outside / "w.key").write_text("cd" * 32, encoding="utf-8")
    monkeypatch.setenv("INSPEXIMUS_WRITER_KEY_FILE", str(outside / "w.key"))
    assert _envpolicy.key_file("INSPEXIMUS_WRITER_KEY_FILE")[0] == str(outside / "w.key")


def test_a_key_file_inside_the_stores_project_is_refused(tmp_path, monkeypatch):
    proj = tmp_path / "p"
    (proj / ".inspeximus").mkdir(parents=True)
    (proj / "r.key").write_text("ab" * 32, encoding="utf-8")
    monkeypatch.setenv("INSPEXIMUS_RECEIPT_KEY_FILE", str(proj / "r.key"))
    store = str(proj / ".inspeximus" / "coding_memory.json")
    assert _envpolicy.key_file("INSPEXIMUS_RECEIPT_KEY_FILE", store) == (None, None)


# ── EC-4 ceilings ────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("var,default,ceiling,cast,ok,bad", [
    ("INSPEXIMUS_BUSY_TIMEOUT_S", 10, 120.0, float, "120", ["inf", "1e12", "121", "5"]),
    ("INSPEXIMUS_SAVE_RETRIES", 2, 20, int, "20", ["21", "1000000", "1"]),
])
def test_the_environment_may_set_a_wait_only_within_its_bounds(var, default, ceiling, cast, ok, bad, monkeypatch,
                                                              user_config):
    monkeypatch.setenv(var, ok)
    assert _envpolicy.at_least(var, default, cast, ceiling) == cast(ok)
    for v in bad:
        monkeypatch.setenv(var, v)
        assert _envpolicy.at_least(var, default, cast, ceiling) == default, "%s=%s was accepted" % (var, v)
    user_config(**{var: "500"})
    assert _envpolicy.at_least(var, default, cast, ceiling) == 500, "the user's config sets any value"


def test_the_read_sites_pass_the_ceilings():
    import inspeximus.core as core
    import inspeximus.sqlite_store as ss
    assert '_ep.at_least("INSPEXIMUS_BUSY_TIMEOUT_S", BUSY_TIMEOUT_S, float, 120.0)' in open(ss.__file__).read()
    assert '_envpolicy.at_least("INSPEXIMUS_SAVE_RETRIES", 2, int, 20)' in open(core.__file__, encoding="utf-8").read()


# ── EC-6 ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_trust_seeds_come_from_the_users_config_only(monkeypatch, user_config, capsys):
    monkeypatch.setenv("INSPEXIMUS_TRUST_SEEDS", "key:" + "ab" * 32)
    assert _envpolicy.trust_seeds() == set()
    assert "INSPEXIMUS_TRUST_SEEDS is set in the environment and is ignored" in capsys.readouterr().err
    user_config(INSPEXIMUS_TRUST_SEEDS="key:cd, mine")
    assert _envpolicy.trust_seeds() == {"key:cd", "mine"}


def test_embedding_in_hooks_is_the_users_config_only(tmp_path, monkeypatch, user_config):
    from inspeximus import claude_code
    monkeypatch.setenv("INSPEXIMUS_EMBED_HOOKS", "1")
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", "http://127.0.0.1:9/v1/embeddings")
    user_config()
    assert claude_code._make_embedder(str(tmp_path))[0] is None, "the environment switched embedding in hooks on"


def test_a_decision_store_from_the_environment_is_not_inside_a_repository(tmp_path, monkeypatch, user_config):
    repo = _git(tmp_path / "repo")
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE", str(repo / "decisions.json"))
    assert _envpolicy.decision_store() == ""
    out = tmp_path / "elsewhere"
    out.mkdir()
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE", str(out / "decisions.json"))
    assert _envpolicy.decision_store() == str(out / "decisions.json")
    user_config(INSPEXIMUS_DECISION_STORE=str(repo / "decisions.json"))
    assert _envpolicy.decision_store() == str(repo / "decisions.json"), "the user's config names any file"


# ── EC-7 RECEIPT_KEY ─────────────────────────────────────────────────────────────────────────────────────────
def test_a_hex_receipt_key_comes_from_the_users_config_only(monkeypatch, user_config, capsys):
    monkeypatch.setenv("INSPEXIMUS_RECEIPT_KEY", "ab" * 32)
    assert _envpolicy.receipt_key_value() is None
    assert "INSPEXIMUS_RECEIPT_KEY is set in the environment and is ignored" in capsys.readouterr().err
    user_config(INSPEXIMUS_RECEIPT_KEY="CD" * 32)
    assert _envpolicy.receipt_key_value() == "cd" * 32


def test_a_receipt_key_path_follows_the_key_file_rule(tmp_path, monkeypatch):
    repo = _git(tmp_path / "repo")
    (repo / "r.key").write_text("ab" * 32, encoding="utf-8")
    monkeypatch.setenv("INSPEXIMUS_RECEIPT_KEY", str(repo / "r.key"))
    assert _envpolicy.receipt_key_path() is None
    monkeypatch.setenv("INSPEXIMUS_RECEIPT_KEY", str(tmp_path / "missing.key"))
    assert _envpolicy.receipt_key_path() is None, "a missing file stopped nothing and must not be returned"
    ok = tmp_path / "keys"
    ok.mkdir()
    (ok / "r.key").write_text("cd" * 32, encoding="utf-8")
    monkeypatch.setenv("INSPEXIMUS_RECEIPT_KEY", str(ok / "r.key"))
    assert _envpolicy.receipt_key_path() == str(ok / "r.key")


def test_receipt_key_for_ignores_an_environment_hex_key(tmp_path, monkeypatch, user_config):
    from inspeximus.core import receipt_key_for
    user_config()
    store = str(tmp_path / "s" / "m.json")
    os.makedirs(os.path.dirname(store))
    monkeypatch.setenv("INSPEXIMUS_RECEIPT_KEY", "ab" * 32)
    assert receipt_key_for(store, create=False) != "ab" * 32
    monkeypatch.setenv("INSPEXIMUS_RECEIPT_KEY", str(tmp_path / "missing.key"))
    receipt_key_for(store, create=False)                      # ignored with a line, never a ValueError


# ── the read sites bind the accessors (a wrapper such as `_flag_from_env` passes the scan, so each site is named) ──
def _calls(path):
    import ast
    t = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
    return [ast.unparse(n) for n in ast.walk(t) if isinstance(n, ast.Call)]


def _assign(path, name):
    import ast
    t = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
    return [ast.unparse(n.value) for n in t.body if isinstance(n, ast.Assign)
            and any(isinstance(x, ast.Name) and x.id == name for x in n.targets)]


def test_the_mcp_server_binds_receipts_trust_seeds_and_key_files_through_the_policy():
    assert _assign("inspeximus/mcp_server.py", "_RECEIPTS") == ["_envpolicy.receipts_from_env()"]
    assert _assign("inspeximus/mcp_server.py", "_TRUST_SEEDS") == ["_envpolicy.trust_seeds()"]
    calls = _calls("inspeximus/mcp_server.py")
    assert "_envpolicy.key_file('INSPEXIMUS_WRITER_KEY_FILE', _PATH)" in calls, "the writer key file ignores the store"
    assert "_envpolicy.key_file('INSPEXIMUS_RECEIPT_KEY_FILE', path)" in calls
