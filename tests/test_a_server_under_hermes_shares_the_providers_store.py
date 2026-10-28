"""An MCP server under Hermes must be able to ASK for the provider's store, not be handed a path.

MEASURED 2026-10-28 on the owner's own Hermes 0.22.5. The MCP catalog manifest named
`~/.hermes/inspeximus_memory.json`, and `hermes_cli/mcp_catalog.py` copies a manifest's
`transport.env` verbatim: `cfg["env"] = dict(t.env)`, with substitution only for `${INSTALL_DIR}`
and only in `command` and `args`. HERMES_HOME on that machine was `AppData/Local/hermes`, so the
server opened an empty file in a directory Hermes does not use while every memory sat in the
provider's store. Writes succeeded, recalls came back empty.

So the manifest names `INSPEXIMUS_SCOPE=hermes` and never a path, and the rule that resolves it lives
in `hermes_store_path`, which the provider calls too. The last test here is the one that matters: it
fails if the two ever stop agreeing.
"""
from __future__ import annotations

import json
import os

from inspeximus._surface import hermes_store_path, resolve_path, resolved_path_source


def test_the_scope_resolves_to_the_providers_default(tmp_path):
    hermes = tmp_path / "profile"
    got = resolve_path(env={"INSPEXIMUS_SCOPE": "hermes", "HERMES_HOME": str(hermes)})
    assert got == os.path.join(str(hermes), "inspeximus", "memory.json"), got


def test_a_recorded_path_wins_because_the_user_moved_their_store(tmp_path):
    hermes = tmp_path / "profile"
    os.makedirs(str(hermes / "inspeximus"))
    moved = str(tmp_path / "elsewhere" / "memory.json")
    with open(str(hermes / "inspeximus" / "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"path": moved}, fh)
    got = resolve_path(env={"INSPEXIMUS_SCOPE": "hermes", "HERMES_HOME": str(hermes)})
    assert got == moved, (
        "a path recorded by `install --all` or the dashboard was ignored, which puts the server back "
        "on a different file from the provider. Got: %r" % (got,))


def test_an_explicit_path_still_outranks_the_scope(tmp_path):
    env = {"INSPEXIMUS_SCOPE": "hermes", "HERMES_HOME": str(tmp_path), "INSPEXIMUS_PATH": "C:/named/by/hand.json"}
    assert resolve_path(env=env) == "C:/named/by/hand.json"
    assert "OUTRANKS" in resolved_path_source(env=env), resolved_path_source(env=env)


def test_the_scope_is_reported_by_name(tmp_path):
    said = resolved_path_source(env={"INSPEXIMUS_SCOPE": "hermes", "HERMES_HOME": str(tmp_path)})
    assert "hermes" in said, said


def test_control_an_unknown_scope_is_refused():
    from inspeximus._surface import StoreScopeError
    try:
        resolve_path(env={"INSPEXIMUS_SCOPE": "hermees"})
    except StoreScopeError as e:
        assert "hermes" in str(e), "the message must list the real scope: %s" % e
    else:
        raise AssertionError("a misspelt scope resolved instead of being refused")


def test_control_no_hermes_home_falls_back_the_way_the_provider_does(tmp_path):
    got = hermes_store_path(env={}, home=str(tmp_path))
    assert got == os.path.join(str(tmp_path), ".hermes", "inspeximus", "memory.json"), got


def test_the_provider_and_the_scope_open_the_same_file(tmp_path):
    """THE ANTI-DRIFT TEST. Two copies of this rule is the defect, so there is one and both use it."""
    import inspect

    from inspeximus.integrations import hermes_agent

    src = inspect.getsource(hermes_agent)
    assert "hermes_store_path(" in src, (
        "the provider no longer calls the shared rule, so its path and INSPEXIMUS_SCOPE=hermes can "
        "drift apart again, which is exactly the defect this file was written for")
    assert "os.path.join(home, \"inspeximus\", \"memory.json\")" not in src, (
        "the provider computes the default path itself again, beside the shared rule")

    hermes = tmp_path / "profile"
    assert resolve_path(env={"INSPEXIMUS_SCOPE": "hermes", "HERMES_HOME": str(hermes)}) == \
        hermes_store_path(env={"HERMES_HOME": str(hermes)})


def test_the_first_session_can_write_before_any_provider_write(tmp_path):
    """A new user's server starts before the provider has ever run, and must still be able to write.

    MEASURED 2026-10-28. A missing store directory is refused everywhere else, because a mistyped
    path would otherwise become a new empty memory. Nobody types this path: it is computed from the
    Hermes home, so the scope creates the folder, exactly as the provider always has. Without that,
    the scope resolved and then every write was refused with "no such directory".
    """
    from inspeximus import Inspeximus
    from inspeximus._surface import store_location_problem

    hermes = tmp_path / "profile"
    os.makedirs(str(hermes))                               # Hermes makes its own home, not the subfolder
    assert not os.path.exists(str(hermes / "inspeximus")), "the fixture already has the folder, so this proves nothing"

    resolved = resolve_path(env={"INSPEXIMUS_SCOPE": "hermes", "HERMES_HOME": str(hermes)})
    assert store_location_problem(resolved) is None, store_location_problem(resolved)
    m = Inspeximus(resolved)
    m.remember("The deploy target is prod-7")
    assert [h["text"] for h in m.recall("deploy")] == ["The deploy target is prod-7"]


def test_control_the_other_scopes_still_refuse_a_missing_directory(tmp_path):
    """The folder is created for `hermes` ONLY. A typed path keeps the refusal that catches a typo."""
    from inspeximus._surface import store_location_problem

    typed = str(tmp_path / "typpo" / "memory.json")
    assert resolve_path(env={"INSPEXIMUS_PATH": typed}) == typed
    assert store_location_problem(typed), (
        "a mistyped INSPEXIMUS_PATH was accepted, which turns a typo into a new empty memory")
    assert not os.path.exists(str(tmp_path / "typpo")), "resolving a typed path created its directory"
