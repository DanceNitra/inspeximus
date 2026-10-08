"""3.18: every INSPEXIMUS_* variable the package reads has a rule in inspeximus/_envpolicy.POLICY, and each rule for a
variable a project's environment could use against the user's store holds (AUDIT-A, appendix of 2026-10-08).

The classifier reads the names from the AST: every string constant in the package that is exactly an INSPEXIMUS_* name.
That is how the value is named however it is read (`os.environ.get`, `env.get` on a copy, `os.getenv`); a regex on
`environ.get(` missed 13 of them on the daemon branch.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import _envpolicy, _userconfig  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


def _names():
    import _env_read_scan
    seen = set()
    for f in _env_read_scan.shipped_files():
        for node in ast.walk(ast.parse(open(f, encoding="utf-8").read())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.fullmatch(
                    r"INSPEXIMUS_[A-Z0-9_]+", node.value):
                seen.add(node.value)
    return seen


def test_every_variable_the_package_reads_has_a_policy():
    seen = _names()
    assert {"INSPEXIMUS_CODING_STORE", "INSPEXIMUS_PROJECT", "INSPEXIMUS_WRITER_KEY"} <= seen, \
        "CONTROL: the scan finds variables read through env.get and os.environ alike"
    missing = sorted(seen - set(_envpolicy.POLICY))
    assert not missing, "give these a rule in inspeximus/_envpolicy.POLICY: %s" % missing


def test_every_rule_is_one_of_the_three_and_names_a_config_key_when_it_reads_one():
    for var, (rule, key, why) in _envpolicy.POLICY.items():
        assert rule in (_envpolicy.CONFIG_ONLY, _envpolicy.ENV_GUARD, _envpolicy.ENV_SAFE), var
        assert why, var
        if rule == _envpolicy.CONFIG_ONLY:
            assert key, "%s is config-only and names no config key" % var


def _quiet(var):
    _userconfig._SAID.discard(var)


# ── CONFIG_ONLY: the environment is never read ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("var,value", [
    ("INSPEXIMUS_PII_DETECT", "1"), ("INSPEXIMUS_OBSERVE_RECALL", "1"), ("INSPEXIMUS_KEEP_CONVERSION_BACKUP", "1"),
])
def test_a_config_only_flag_ignores_the_environment_and_reads_the_users_config(var, value, monkeypatch, user_config,
                                                                              capsys):
    _quiet(var)
    monkeypatch.setenv(var, value)
    assert _envpolicy.config_flag(var) is False, "%s from the environment switched it on" % var
    assert "%s is set in the environment and is ignored" % var in capsys.readouterr().err
    user_config(**{var: value})
    assert _envpolicy.config_flag(var) is True


def test_the_writer_key_and_the_actor_come_from_the_users_config_only(monkeypatch, user_config):
    monkeypatch.setenv("INSPEXIMUS_WRITER_KEY", "ab" * 32)
    monkeypatch.setenv("INSPEXIMUS_ACTOR", "intruder")
    assert _envpolicy.config_string("INSPEXIMUS_WRITER_KEY") is None
    assert _envpolicy.config_string("INSPEXIMUS_ACTOR") is None
    user_config(INSPEXIMUS_WRITER_KEY="cd" * 32, INSPEXIMUS_ACTOR="owner")
    assert _envpolicy.config_string("INSPEXIMUS_WRITER_KEY") == "cd" * 32
    assert _envpolicy.config_string("INSPEXIMUS_ACTOR") == "owner"


def test_the_supersession_policy_is_the_users_choice(monkeypatch, user_config):
    from inspeximus.core import _resolve_supersession
    monkeypatch.setenv("INSPEXIMUS_SUPERSESSION", "authority")
    assert _resolve_supersession() == "lww"
    user_config(INSPEXIMUS_SUPERSESSION="authority")
    assert _resolve_supersession() == "authority"


# ── ENV_GUARD: only the direction that cannot harm the store ───────────────────────────────────────────────
@pytest.mark.parametrize("var", ["INSPEXIMUS_ECHO_GUARD", "INSPEXIMUS_READ_GUARDS", "INSPEXIMUS_HEADS"])
def test_the_environment_cannot_switch_a_guard_off(var, monkeypatch, user_config):
    monkeypatch.setenv(var, "0")
    assert _envpolicy.guard_on(var) is True
    user_config(**{var: "0"})
    assert _envpolicy.guard_on(var) is False


def test_the_environment_can_raise_a_wait_and_never_lower_it(monkeypatch, user_config):
    monkeypatch.setenv("INSPEXIMUS_SAVE_RETRIES", "0")
    assert _envpolicy.at_least("INSPEXIMUS_SAVE_RETRIES", 2, int) == 2
    monkeypatch.setenv("INSPEXIMUS_SAVE_RETRIES", "6")
    assert _envpolicy.at_least("INSPEXIMUS_SAVE_RETRIES", 2, int) == 6
    user_config(INSPEXIMUS_SAVE_RETRIES="0")
    assert _envpolicy.at_least("INSPEXIMUS_SAVE_RETRIES", 2, int) == 0


def test_a_writer_key_file_inside_a_git_work_tree_is_never_used(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "w.key").write_text("ab" * 32, encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "w.key").write_text("cd" * 32, encoding="utf-8")
    monkeypatch.setenv("INSPEXIMUS_WRITER_KEY_FILE", str(repo / "w.key"))
    assert _envpolicy.key_file("INSPEXIMUS_WRITER_KEY_FILE") == (None, None)
    monkeypatch.setenv("INSPEXIMUS_WRITER_KEY_FILE", str(outside / "w.key"))
    assert _envpolicy.key_file("INSPEXIMUS_WRITER_KEY_FILE") == (str(outside / "w.key"), "INSPEXIMUS_WRITER_KEY_FILE")


def test_a_project_name_from_the_environment_is_this_folders_or_the_users_list(tmp_path, user_config):
    here = tmp_path / "web-app"
    here.mkdir()
    assert _envpolicy.project_name("web-app", str(here)) == "web-app"
    assert _envpolicy.project_name("auto", str(here)) == "auto"
    assert _envpolicy.project_name("payroll", str(here)) is None
    user_config(INSPEXIMUS_PROJECT="payroll")
    assert _envpolicy.project_name("payroll", str(here)) == "payroll"


# ── reembed under a project's recipe ───────────────────────────────────────────────────────────────────────
def test_reembed_replaces_another_recipe_only_when_the_user_types_it(tmp_path):
    """INSPEXIMUS_EMBED_MODEL from a project's environment moved the user's whole store to the project's model. A store
    with vectors of another recipe is refused without --replace-recipe."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            body = json.dumps({"data": [{"embedding": [0.1, 0.2, 0.3]}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/v1/embeddings" % srv.server_address[1]
    p = str(tmp_path / "s.json")
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(INSPEXIMUS_EMBED_URL=url, INSPEXIMUS_EMBED_MODEL="users-model", INSPEXIMUS_NO_UPDATE_CHECK="1",
               INSPEXIMUS_KEY_HOME=str(tmp_path / "kh"), PYTHONPATH=ROOT)

    def cli(*a, model):
        e = dict(env, INSPEXIMUS_EMBED_MODEL=model)
        return subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", p] + list(a), env=e,
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
    try:
        r = cli("remember", "the release train leaves on tuesday", model="users-model")
        assert r.returncode == 0, r.stderr[-400:]
        r = cli("reembed", model="users-model")
        assert r.returncode == 0, r.stderr[-400:]
        r = cli("reembed", model="projects-model")
        assert r.returncode == 2 and "--replace-recipe" in r.stderr, (r.returncode, r.stderr[-400:])
        r = cli("reembed", "--replace-recipe", model="projects-model")
        assert r.returncode == 0, r.stderr[-400:]
    finally:
        srv.shutdown()
