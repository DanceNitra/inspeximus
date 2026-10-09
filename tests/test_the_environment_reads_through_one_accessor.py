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
    # S-1: touching the environment under any name is the violation, not a list of read shapes.
    'import os\nE = os.environ',
    'from os import environ as E\nx = E.get("X")',
    'import os\ng = os.getenv',
    'import os\nfor k, v in os.environ.items():\n    pass',
    'import os\nd = dict(os.environ)',
    'import os\nd = os.environ.copy()',
    'import os\nx = os.path.expandvars("$HOME")',
    'import os\nx = os.environb',
    'import os\nx = getattr(os, "environ")',
    'from os import *',
    'import os as o\no.putenv("X", "1")',
    'from inspeximus import _envpolicy\nx = _envpolicy.child_env().get("INSPEXIMUS_PATH")',
    'def f(x):\n    return ("inspeximus_" + x).upper()',
])
def test_the_scan_catches_every_shape_of_read(src):
    assert scan.violations(os.path.join(ROOT, "inspeximus", "x.py"), src), "the scan missed: %s" % src


@pytest.mark.parametrize("src", [
    'from . import _envpolicy\nx = _envpolicy.raw("INSPEXIMUS_PATH")',
    'from . import _envpolicy\n_envpolicy.set_for_this_process("INSPEXIMUS_KEY_HOME", "k")',
    'x = {"INSPEXIMUS_PATH": "p"}',
    'def f(src):\n    return src == "INSPEXIMUS_RECEIPT_KEY_FILE"',
    'def f():\n    return "INSPEXIMUS_PATH"',
    'def f(e):\n    return env_url("INSPEXIMUS_EMBED_URL")',
])
def test_a_write_or_a_read_through_the_accessor_is_not_flagged(src):
    assert scan.violations(os.path.join(ROOT, "inspeximus", "x.py"), src) == []


def test_the_passthroughs_refuse_our_own_names(monkeypatch):
    """S-1: `other` and `other_names` are for other programs' variables; an INSPEXIMUS_* name through them would skip
    its rule."""
    monkeypatch.setenv("INSPEXIMUS_PATH", "x")
    for call in (lambda: _envpolicy.other("INSPEXIMUS_PATH"), lambda: _envpolicy.other("inspeximus_path"),
                 lambda: _envpolicy.other_names("INSPEXIMUS_"), lambda: _envpolicy.other_names("INSP"),
                 lambda: _envpolicy.other_names("")):
        with pytest.raises(ValueError):
            call()
    monkeypatch.setenv("CLAUDE_CODE_X", "1")
    assert "CLAUDE_CODE_X" in _envpolicy.other_names("CLAUDE_CODE_")


# -- S-2: host() takes a host config entry, never the environment ------------------------------------------------
@pytest.mark.parametrize("make", [
    lambda: os.environ, lambda: dict(os.environ), lambda: os.environ.copy(),
    lambda: {"env": os.environ}, lambda: {"env": dict(os.environ)}, lambda: {"env": os.environ.copy()},
])
def test_host_refuses_the_environment_and_any_copy_of_it(make, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_PATH", "from-the-environment")
    with pytest.raises(ValueError):
        _envpolicy.host("INSPEXIMUS_PATH", make())


def test_host_reads_the_env_block_of_a_host_entry():
    assert _envpolicy.host("INSPEXIMUS_PATH", {"command": "uvx", "env": {"INSPEXIMUS_PATH": "p"}}) == "p"
    assert _envpolicy.host("INSPEXIMUS_PATH", {"command": "uvx"}) == ""
    assert _envpolicy.host("INSPEXIMUS_PATH", None) == ""


#: S-2: every caller of `raw` for an env-with-guard name, or for a name it does not know, with the guard it applies.
#: A new caller fails the test below until it is listed here with its guard.
RAW_GUARDED_CALLERS = {
    ("inspeximus/_http.py", "embedders_from_env", "INSPEXIMUS_EMBED_MODEL"): "the recipe: an open embeds nothing",
    ("inspeximus/_http.py", "embedders_from_env", "INSPEXIMUS_NOMIC_PREFIX"): "the recipe, as EMBED_MODEL",
    ("inspeximus/_http.py", "env_url", "<var>"): "host_allowed: another host needs the user's config",
    ("inspeximus/_http.py", "env_key", "<var>"): "host_allowed for the URL in use",
    ("inspeximus/_keyhome.py", "key_home", "INSPEXIMUS_KEY_HOME"): "refusal(): git tree, store project, cwd project",
    ("inspeximus/_surface.py", "resolve_path", "INSPEXIMUS_PATH"): "_vet_path_link",
    ("inspeximus/_surface.py", "resolve_path", "INSPEXIMUS_SCOPE"): "the vetted resolver of each scope",
    ("inspeximus/_surface.py", "resolved_path_source", "INSPEXIMUS_PATH"): "a label only; opens nothing",
    ("inspeximus/_surface.py", "resolved_path_source", "INSPEXIMUS_SCOPE"): "a label only; opens nothing",
    ("inspeximus/_surface.py", "_coding_store_location", "INSPEXIMUS_CODING_STORE"): "_storelink.vet",
    ("inspeximus/claude_code.py", "_make_embedder", "INSPEXIMUS_EMBED_MODEL"): "the recipe, as above",
    ("inspeximus/claude_code.py", "_make_embedder", "INSPEXIMUS_NOMIC_PREFIX"): "the recipe, as above",
    ("inspeximus/demo.py", "run_demo", "INSPEXIMUS_KEY_HOME"): "saved to restore after the demo; read for nothing else",
    ("inspeximus/mcp_server.py", "resolve_project", "INSPEXIMUS_PROJECT"): "_envpolicy.project_name",
    ("inspeximus/mcp_server.py", "_flag_from_env", "<name>"): "its callers pass env-safe names only (checked below)",
    ("inspeximus/mcp_server.py", "where_am_i", "INSPEXIMUS_SCOPE"): "a label only; opens nothing",
    ("inspeximus/probes/a_contradiction_one_channel_cannot_see.py", "<module>", "INSPEXIMUS_EMBED_MODEL"):
        "a probe on its own temporary store",
}


def _raw_callers():
    import ast
    out, flag_args = set(), set()
    for f in scan.shipped_files():
        rel = os.path.relpath(f, ROOT).replace(os.sep, "/")
        if rel == "inspeximus/_envpolicy.py":
            continue
        t = ast.parse(open(f, encoding="utf-8").read())
        fn = {}
        for d in ast.walk(t):
            if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for n in ast.walk(d):
                    fn[id(n)] = d.name
        for n in ast.walk(t):
            if not isinstance(n, ast.Call) or not n.args:
                continue
            a = n.args[0]
            if scan._is_envpolicy_call(n) and n.func.attr == "raw":
                if isinstance(a, ast.Constant):
                    if _envpolicy.POLICY[a.value][0] == _envpolicy.ENV_SAFE:
                        continue
                    var = a.value
                else:
                    var = "<%s>" % ast.unparse(a)
                out.add((rel, fn.get(id(n), "<module>"), var))
            elif isinstance(n.func, ast.Name) and n.func.id == "_flag_from_env":
                flag_args.add(a.value if isinstance(a, ast.Constant) else "<%s>" % ast.unparse(a))
    return out, flag_args


def test_every_raw_read_of_a_guarded_name_is_a_listed_caller_that_applies_the_guard():
    callers, flag_args = _raw_callers()
    assert ("inspeximus/_keyhome.py", "key_home", "INSPEXIMUS_KEY_HOME") in callers, "CONTROL: the census finds callers"
    unlisted = sorted(callers - set(RAW_GUARDED_CALLERS))
    assert not unlisted, "raw() of a guarded name outside the list; apply its guard and list it: %s" % unlisted
    assert flag_args and all(_envpolicy.POLICY[v][0] == _envpolicy.ENV_SAFE for v in flag_args), flag_args


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


def test_only_a_64_hex_receipts_key_counts_as_a_signing_key(monkeypatch):
    """S-3: a receipts.key that is not a 64-hex key signs nothing, so it does not let the environment start a chain."""
    import _userconfig_env
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", _userconfig_env.key_home_with({"receipts": {"key": "not-a-key"}}))
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS", "1")
    assert _envpolicy.receipts_from_env() is False
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", _userconfig_env.key_home_with({"receipts": {"key": "ab" * 32}}))
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
    assert "timeout=busy_timeout_s()" in open(ss.__file__).read(), "the connect does not read the bounded value"
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


# -- C-2: the writer-key hint names the user's config, never a key file the server would ignore -------------------
def test_writer_key_points_at_the_users_config_and_says_when_the_file_is_in_the_project(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    kh = tmp_path / "kh"
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(INSPEXIMUS_KEY_HOME=str(kh), INSPEXIMUS_NO_UPDATE_CHECK="1", PYTHONPATH=ROOT,
               USERPROFILE=str(tmp_path / "home"), HOME=str(tmp_path / "home"))
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "writer-key", "--new", "--out", "key.txt"], cwd=str(work),
                       env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    assert "INSPEXIMUS_WRITER_KEY_FILE=" not in r.stdout, "the hint names a key file the server would ignore"
    assert '"key_file": ' in r.stdout and "config.json" in r.stdout, r.stdout
    assert "INSPEXIMUS_WRITER_KEY_FILE cannot name this file" in r.stdout, "CONTROL: key.txt is in the folder it ran in"


def test_the_pages_show_the_config_line_and_not_a_key_file_in_the_work_folder():
    for page in ("audit-trail.html", "erasure.html", "migrate-from-mem0.html"):
        text = open(os.path.join(ROOT, page), encoding="utf-8").read()
        assert "INSPEXIMUS_WRITER_KEY_FILE=key.txt" not in text, page
        assert "point the server at it: in " in text, page
