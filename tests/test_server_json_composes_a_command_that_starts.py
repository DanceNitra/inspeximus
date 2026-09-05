"""The command a directory builds from server.json must actually start the server.

Measured 2026-09-05, after Glama emailed that the build for our listing had failed.

`server.json` is the only thing an MCP directory reads, and ours declared `pypi: inspeximus` with no
runtime fields. A client composes `<runtime> <runtimeArguments> <identifier> <packageArguments>`, so
with nothing set the best it could do was `uvx inspeximus`, which starts the CLI and exits 2 asking
for a subcommand. The naive reading, `pip install inspeximus` then `inspeximus-mcp`, dies with
`ModuleNotFoundError: No module named 'mcp'`, because the SDK lives in the optional `[mcp]` extra.
Both measured. Either is a failed build.

Two things constrain the fix, and they pull against each other:

  * The identifier has to stay `inspeximus`. The registry verifies package ownership against an
    `mcp-name:` marker in the PyPI description, and `inspeximus-mcp` is not a package that exists.
    The registry's own `/v0.1/validate` does NOT catch this: it returned `{"valid": true}` for
    `inspeximus-mcp` and for `zzz-not-a-real-package-4f9a` alike, so it checks shape, not existence.
  * `uvx inspeximus` runs the console script named after the package, which is the CLI.

So the CLI grew an `mcp` subcommand, and server.json names the extra in `runtimeArguments`:

    uvx --from "inspeximus[mcp]" inspeximus mcp

These tests pin that composition and prove the composed command speaks MCP. The last one is the
control: without the extra the same command must still fail, or the extra is not the variable and
this file is testing nothing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER_JSON = ROOT / "server.json"
INIT = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "test", "version": "0"}}}) + "\n"


def _pkg():
    d = json.loads(SERVER_JSON.read_text(encoding="utf-8"))
    pkgs = [p for p in d["packages"] if (p.get("registryType") or p.get("registry_name")) == "pypi"]
    assert pkgs, "server.json declares no pypi package, so no client can install this server"
    return pkgs[0]


def compose():
    """The command a client builds, per the generic-server-json spec's ordering."""
    p = _pkg()
    cmd = [p["runtimeHint"]]
    for a in p.get("runtimeArguments") or []:
        if a.get("type") == "named":
            cmd += [a["name"], a["value"]]
        else:
            cmd.append(a["value"])
    cmd.append(p["identifier"])
    cmd += [a["value"] for a in (p.get("packageArguments") or [])]
    return cmd


def test_the_identifier_is_the_real_pypi_package():
    """Ownership is verified against this name. A name that is not published cannot be verified."""
    assert _pkg()["identifier"] == "inspeximus"


def test_the_extra_is_named_somewhere_or_the_sdk_is_missing_at_runtime():
    p = _pkg()
    text = json.dumps(p)
    assert "inspeximus[mcp]" in text, (
        "server.json never names the [mcp] extra, so a client installs the bare package and the "
        "server dies with ModuleNotFoundError: No module named 'mcp'")


def test_the_composed_command_is_the_documented_one():
    assert compose() == ["uvx", "--from", "inspeximus[mcp]", "inspeximus", "mcp"]


def test_the_cli_exposes_the_subcommand_the_composition_relies_on():
    from inspeximus import cli
    parser_help = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--help"],
                                 capture_output=True, text=True, cwd=str(ROOT))
    assert " mcp" in parser_help.stdout or "mcp" in parser_help.stdout, (
        "`inspeximus mcp` is not a subcommand, so the composed command exits 2")


@pytest.mark.skipif(__import__("importlib.util", fromlist=["util"]).find_spec("mcp") is None,
                    reason="the MCP SDK is not installed in this environment")
def test_the_composed_command_speaks_mcp():
    """Run the tail of the composed command (everything after uvx resolves the package) and check
    the server answers `initialize`. This is the assertion that would have caught the Glama failure."""
    cmd = [sys.executable, "-m", "inspeximus.cli", "mcp"]
    p = subprocess.run(cmd, input=INIT, capture_output=True, text=True, timeout=120,
                       cwd=str(ROOT), env=dict(os.environ, INSPEXIMUS_PATH=""))
    line = next((l for l in p.stdout.splitlines() if l.strip().startswith("{")), "")
    assert line, "the server produced no JSON on stdout; stderr: %s" % p.stderr[-400:]
    assert json.loads(line)["result"]["serverInfo"]["name"] == "inspeximus"


def test_CONTROL_the_sdk_is_what_makes_it_work(tmp_path):
    """Without the SDK importable the server must fail. If it passes anyway, the extra is not the
    variable and every assertion above is measuring something else."""
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    (tmp_path / "mcp.py").write_text("raise ImportError('shadowed for the control')\n",
                                     encoding="utf-8")
    p = subprocess.run([sys.executable, "-m", "inspeximus.cli", "mcp"], input=INIT,
                       capture_output=True, text=True, timeout=120, cwd=str(ROOT), env=env)
    ok = any(l.strip().startswith("{") and "serverInfo" in l for l in p.stdout.splitlines())
    assert not ok, "the server started with the SDK shadowed, so this suite proves nothing"
