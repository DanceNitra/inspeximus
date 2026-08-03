"""Project/workspace scope for the MCP server: isolation, and the controls that make the isolation real.

THE MEASUREMENT is one line -- a record written under project A is not returned by a recall under project B.
On its own it is worthless: an implementation that returned NOTHING AT ALL, from every scope, would pass it
perfectly. So every isolation assertion here is paired with a control that fails if recall has simply gone
dark, and the file is written so that the pairing cannot be dropped by accident:

    isolation  A's record is invisible from B
    control 1  B still sees B's OWN record         (recall is alive, not returning [])
    control 2  A's record IS returned unscoped     (nothing was destroyed, only filtered)
    control 3  A's record IS returned by the explicit cross-project search
    control 4  a store written BEFORE any of this existed is fully readable, scoped and unscoped

Plus the second defect this unit closes -- the store PATH was resolved from a relative default against
whatever working directory the MCP host happened to launch in, so the same config reached different stores
from different directories, silently. The path tests assert the three-cwd invariant directly.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus
from inspeximus import _surface


# ── the library half: stamp on write, wildcard filter on recall ───────────────────────────────────────────

def _store(tmp_path, name="s.json"):
    return Inspeximus(path=str(tmp_path / name))


def test_isolation_a_record_from_project_a_is_not_recalled_under_project_b(tmp_path):
    """THE MEASUREMENT. It must be able to fail: see the control immediately below it."""
    m = _store(tmp_path)
    m.remember("the deploy channel is BLUE-9", project="alpha")
    m.remember("the deploy channel is GREEN-2", project="beta")

    from_b = m.recall("deploy channel", k=10, project="beta")
    texts = [h["text"] for h in from_b]
    assert "the deploy channel is BLUE-9" not in texts, "project alpha's memory leaked into project beta"

    # CONTROL 1 -- recall is ALIVE. Without this, a recall that returned [] for everything would score a
    # perfect isolation pass. This is the assertion that distinguishes "filtered" from "broken".
    assert "the deploy channel is GREEN-2" in texts, "beta cannot see its OWN memory -- recall is broken, not scoped"


def test_control_the_same_record_is_returned_when_no_scope_is_set(tmp_path):
    """CONTROL 2: nothing was destroyed or made unreachable -- an unscoped recall still returns both."""
    m = _store(tmp_path)
    m.remember("the deploy channel is BLUE-9", project="alpha")
    m.remember("the deploy channel is GREEN-2", project="beta")

    texts = [h["text"] for h in m.recall("deploy channel", k=10)]
    assert "the deploy channel is BLUE-9" in texts
    assert "the deploy channel is GREEN-2" in texts


def test_control_an_unstamped_record_is_visible_from_every_project(tmp_path):
    """The wildcard rule that makes adoption non-destructive, asserted as behaviour rather than assumed.

    It mirrors the uid/aid/sid hierarchy already in recall(): unset at a level means wildcard, not 'hidden'.
    """
    m = _store(tmp_path)
    m.remember("the api base url is https://example.test", project=None)

    for scope in ("alpha", "beta", None):
        texts = [h["text"] for h in m.recall("api base url", k=10, project=scope)]
        assert "the api base url is https://example.test" in texts, f"unstamped memory invisible from {scope!r}"


def test_project_none_is_byte_identical_to_never_passing_it(tmp_path):
    """BACKWARDS COMPATIBILITY at the record level: an unscoped write leaves NO stamp at all.

    Asserted on the persisted record, not on recall, because 'the default changed nothing' has to hold for
    what lands on disk too -- a stamp of None would still be a schema change for every existing reader.
    """
    m = _store(tmp_path)
    mid = m.remember("plain old memory")
    rec = next(r for r in m.items if r["id"] == mid)
    assert "project" not in (rec.get("meta") or {}), "an unscoped write must not stamp a project key"


def test_a_store_written_before_project_scope_existed_is_fully_readable(tmp_path):
    """CONTROL 4: the shipped-library guarantee. A store whose records predate this feature must be readable
    BOTH unscoped and under a project scope. This is the case that decides whether adopting a scope is safe
    for someone with real memories already on disk.

    The fixture is built by writing the JSON directly, in the pre-feature shape -- no `meta.project` anywhere
    -- rather than by calling the new code with project=None. A fixture produced by the code under test can
    only ever confirm that the code agrees with itself.
    """
    p = tmp_path / "legacy.json"
    legacy = [
        {"id": "aaaaaaaaaa", "text": "the billing api uses oauth2", "tags": ["infra"], "value": 3.0,
         "ts": 1_700_000_000.0, "iso": "2023-11-14T22:13:20Z", "valid_from": 1_700_000_000.0,
         "source": None, "mtype": "semantic", "last_access": 1_700_000_000.0,
         "status": "active", "links": [], "meta": {}},
        {"id": "bbbbbbbbbb", "text": "the staging cluster lives in frankfurt", "tags": [], "value": 1.0,
         "ts": 1_700_000_001.0, "iso": "2023-11-14T22:13:21Z", "valid_from": 1_700_000_001.0,
         "source": None, "mtype": "semantic", "last_access": 1_700_000_001.0,
         "status": "active", "links": [], "meta": {}},
    ]
    p.write_text(json.dumps(legacy), encoding="utf-8")

    m = Inspeximus(path=str(p))
    assert len(m.items) == 2, "the pre-existing store did not load"

    # readable with no scope (today's behaviour, unchanged) ...
    assert [h["text"] for h in m.recall("billing api", k=5)], "legacy store unreadable unscoped"
    # ... and STILL readable once a scope is adopted. This is the migration guarantee: opting in narrows
    # what you see without hiding what you already had.
    for scope in ("alpha", "beta"):
        texts = [h["text"] for h in m.recall("billing api", k=5, project=scope)]
        assert "the billing api uses oauth2" in texts, f"legacy memory vanished under project {scope!r}"


def test_project_scope_composes_with_the_session_hierarchy_rather_than_colliding(tmp_path):
    """project is a DIFFERENT AXIS from uid/aid/sid and the two must intersect, not overwrite each other.

    Four records across the 2x2 of (project, session). A query naming both must return exactly the one that
    matches both -- if either filter were dropped or were overwriting the other, a peer would survive.
    """
    m = _store(tmp_path)
    for proj in ("alpha", "beta"):
        for sess in ("s1", "s2"):
            m.remember(f"cache ttl for {proj} {sess} is 60", project=proj, session_id=sess)

    hits = m.recall("cache ttl", k=10, project="alpha", session_id="s1")
    texts = [h["text"] for h in hits]
    assert texts == ["cache ttl for alpha s1 is 60"], f"project x session did not intersect: {texts}"


def test_remember_decision_carries_the_project_stamp(tmp_path):
    """The decision path is the one the product tells users to write through, so it must scope too."""
    m = _store(tmp_path)
    mid = m.remember_decision("use postgres", topic="database", project="alpha")
    rec = next(r for r in m.items if r["id"] == mid)
    assert (rec.get("meta") or {}).get("project") == "alpha"
    assert "use postgres" not in [h["text"] for h in m.recall("database", k=10, project="beta")]


def test_supersession_keys_are_NOT_namespaced_by_project(tmp_path):
    """A CONFLICT, pinned as behaviour rather than papered over. Reported in the PR, not silently 'fixed'.

    Supersession keys are global to the store. Two projects using the SAME key therefore still supersede one
    another -- and under a project scope the loser sees NOTHING rather than the other project's value, because
    its own record is now superseded AND the survivor is filtered out. Namespacing keys by project would
    change `revert(key)`, `history(key)` and every stored `decision::<topic>` for existing users, so the fix
    is documentation (qualify the topic per project), not a silent semantic change to a shipped key space.

    This test exists to make the limitation VISIBLE and to fail loudly if anyone changes it by accident.
    """
    m = _store(tmp_path)
    m.remember("db is postgres", key="database", object="postgres", project="alpha")
    m.remember("db is sqlite", key="database", object="sqlite", project="beta")

    alpha = [h["text"] for h in m.recall("db", k=10, project="alpha")]
    assert "db is postgres" not in alpha, (
        "documented limitation changed: alpha's keyed record survived beta's same-key write. If this is now "
        "intended, update docs/ and the remember_decision docstring, which both state the opposite.")


# ── the MCP surface: flag, env var, precedence, and the escape hatch ───────────────────────────────────────

def _mcp():
    """The MCP server module, or SKIP -- it needs the OPTIONAL MCP SDK.

    Guarded on the SDK package (`mcp`), whose absence is a ModuleNotFoundError that importorskip turns
    into a clean skip -- NOT on `inspeximus.mcp_server`, whose module body raises a plain ImportError with
    install advice. pytest re-raises that one, and an ImportError at import time ABORTS COLLECTION for the
    whole file, taking every unrelated test in it down with it. Measured on the zero-dependency CI leg:
    `1 error during collection`, suite interrupted.

    Deliberately a FUNCTION, not a module-level guard. The library-scope and path-resolution tests in this
    file need no SDK and must actually RUN on that leg -- a file that skipped wholesale there would report
    green having measured nothing, and the zero-required-dependency leg is precisely where the
    backwards-compatibility and three-CWD measurements matter most. Measured with `mcp` blocked: 13 of the
    20 tests here RUN (including the isolation measurement, both backwards-compatibility controls and the
    three-CWD invariant); only the 7 that drive the MCP surface itself skip.
    """
    pytest.importorskip("mcp", reason="the inspeximus MCP server needs the optional MCP SDK")
    try:
        import inspeximus.mcp_server as m
    except ImportError as e:                    # SDK present but unusable (e.g. mcp 2.x reorganised fastmcp)
        pytest.skip(f"MCP server unavailable: {e}")
    return m


def test_resolve_project_precedence_flag_beats_env():
    mcp_server = _mcp()
    assert mcp_server.resolve_project("flagname", env={"INSPEXIMUS_PROJECT": "envname"}) == "flagname"
    assert mcp_server.resolve_project(None, env={"INSPEXIMUS_PROJECT": "envname"}) == "envname"
    assert mcp_server.resolve_project(None, env={}) is None, "no flag and no env must mean UNSCOPED"


def test_resolve_project_empty_env_is_unset_but_an_empty_flag_is_refused():
    """Asking for isolation and silently getting none is the defect; an explicit empty name must be loud.

    An empty ENV var is treated as unset (exporting VAR="" is how tooling says 'not set'); an explicit
    `--project ''` raises, because it can only be a mistake and the quiet outcome would be a server that
    shares every project while the user believes it is isolated.
    """
    mcp_server = _mcp()
    assert mcp_server.resolve_project(None, env={"INSPEXIMUS_PROJECT": "   "}) is None
    with pytest.raises(mcp_server.ProjectScopeError):
        mcp_server.resolve_project("   ", env={})


def test_resolve_project_auto_derives_from_the_working_directory(tmp_path):
    mcp_server = _mcp()
    d = tmp_path / "my-repo"
    d.mkdir()
    assert mcp_server.resolve_project("auto", env={}, cwd=str(d)) == "my-repo"


def test_the_project_flag_appears_in_help_and_an_unknown_flag_is_refused():
    """The flag must be DISCOVERABLE, and a typo must not start an unscoped server that looks scoped."""
    _mcp()
    out = subprocess.run([sys.executable, "-m", "inspeximus.mcp_server", "--help"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "--project" in out.stdout

    typo = subprocess.run([sys.executable, "-m", "inspeximus.mcp_server", "--porject", "web"],
                          capture_output=True, text=True)
    assert typo.returncode != 0, "a mistyped scope flag must fail at launch, not start an unscoped server"


def test_mcp_tools_isolate_write_and_recall_and_the_escape_hatch_crosses(tmp_path, monkeypatch):
    """The MCP surface end to end: the isolation AND all three controls, driven through the tools.

    The tools read the module-global scope at call time, so flipping it is exactly what `--project` does at
    launch -- this drives the shipped code path rather than a re-implementation of it.
    """
    _mcp()
    import importlib
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "mcp.json"))
    monkeypatch.delenv("INSPEXIMUS_PROJECT", raising=False)
    monkeypatch.delenv("INSPEXIMUS_SCOPE", raising=False)
    mod = importlib.reload(importlib.import_module("inspeximus.mcp_server"))

    mod._PROJECT = "alpha"
    mod.remember("the alpha secret handshake is ZQ7")
    mod._PROJECT = "beta"
    mod.remember("the beta secret handshake is LM3")

    # ISOLATION: beta cannot see alpha's record ...
    beta_texts = [h["text"] for h in mod.recall("secret handshake", k=10)]
    assert "the alpha secret handshake is ZQ7" not in beta_texts
    # CONTROL 1: ... but beta DOES see its own. Kills the 'return nothing' trivial pass.
    assert "the beta secret handshake is LM3" in beta_texts

    # CONTROL 2: unscoped server sees both.
    mod._PROJECT = None
    both = [h["text"] for h in mod.recall("secret handshake", k=10)]
    assert "the alpha secret handshake is ZQ7" in both and "the beta secret handshake is LM3" in both

    # CONTROL 3: the explicit cross-project escape hatch, FROM beta, finds alpha's record and says where
    # it came from.
    mod._PROJECT = "beta"
    crossed = mod.recall("secret handshake", k=10, all_projects=True)
    hit = next((h for h in crossed if h["text"] == "the alpha secret handshake is ZQ7"), None)
    assert hit is not None, "all_projects=True did not cross the scope boundary"
    assert hit["project"] == "alpha", "a cross-project hit must name its project"

    # the discoverability surface reports the same truth
    rep = mod.projects()
    assert rep["active"] == "beta"
    assert rep["projects"] == {"alpha": 1, "beta": 1}

    where = mod.where_am_i()
    assert where["project"] == "beta"
    assert os.path.isabs(where["store_path"]), "where_am_i must give an ABSOLUTE path -- the relative one is the bug"
    assert where["memories"] == 2

    mod._PROJECT = None


def test_multi_hop_recall_honours_the_project_scope(tmp_path, monkeypatch):
    """`recall_iterative` (the multi-hop surface) must not be a side door out of the scope.

    It arrived from a sibling PR while this branch was in flight, so the two features first met at a rebase --
    exactly where a scope hole gets introduced silently. A walk whose FIRST hop respects the project and whose
    later hops do not would leak a peer project's records under a name that reads as ordinary retrieval.
    """
    _mcp()
    import importlib
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "hop.json"))
    monkeypatch.delenv("INSPEXIMUS_PROJECT", raising=False)
    monkeypatch.delenv("INSPEXIMUS_SCOPE", raising=False)
    mod = importlib.reload(importlib.import_module("inspeximus.mcp_server"))

    mod._PROJECT = "alpha"
    mod.remember("the alpha release manager is Priya Raman")
    mod._PROJECT = "beta"
    mod.remember("the beta release manager is Tomas Neubauer")

    res = mod.recall_iterative("who is the release manager", k=10)
    texts = [h["text"] for h in res["hits"]]
    assert "the alpha release manager is Priya Raman" not in texts, f"multi-hop leaked across projects: {texts}"
    # CONTROL -- the surface is alive and returns beta's own record, so this is scoping, not an empty result.
    assert "the beta release manager is Tomas Neubauer" in texts, f"multi-hop returned nothing at all: {texts}"

    # the escape hatch reaches across, here too
    crossed = [h["text"] for h in mod.recall_iterative("who is the release manager", k=10,
                                                       all_projects=True)["hits"]]
    assert "the alpha release manager is Priya Raman" in crossed, crossed

    mod._PROJECT = None


def test_neighbors_does_not_leak_across_projects(tmp_path, monkeypatch):
    """Expansion around a hit must not be a side door into another project."""
    _mcp()
    import importlib
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "nb.json"))
    monkeypatch.delenv("INSPEXIMUS_PROJECT", raising=False)
    monkeypatch.delenv("INSPEXIMUS_SCOPE", raising=False)
    mod = importlib.reload(importlib.import_module("inspeximus.mcp_server"))

    mod._PROJECT = "alpha"
    seed = mod.remember("rate limiting uses a token bucket")["id"]
    mod.remember("rate limiting buckets refill every second")
    mod._PROJECT = "beta"
    mod.remember("rate limiting uses a leaky bucket")

    mod._PROJECT = "alpha"
    texts = [h["text"] for h in mod.neighbors(seed, k=5)]
    assert "rate limiting uses a leaky bucket" not in texts, "neighbors leaked a peer project's memory"
    # CONTROL: neighbors still works at all.
    assert "rate limiting buckets refill every second" in texts

    mod._PROJECT = None


# ── the store PATH: the cwd-dependence defect ─────────────────────────────────────────────────────────────

def test_default_path_resolution_is_unchanged(tmp_path, monkeypatch):
    """BACKWARDS COMPATIBILITY for the path: with nothing set, the answer is exactly the old relative name."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INSPEXIMUS_PATH", raising=False)
    monkeypatch.delenv("INSPEXIMUS_SCOPE", raising=False)
    assert _surface.resolve_path(None) == "inspeximus_memory.json"
    assert _surface.resolve_path(None, env={"INSPEXIMUS_SCOPE": "user"}) == "inspeximus_memory.json"


def test_project_scope_resolves_to_the_same_path_from_three_directories_in_one_repo(tmp_path):
    """THE CWD-DEPENDENCE MEASUREMENT, and the one a simple A/B isolation test cannot make.

    Three different working directories inside ONE repository must resolve to ONE store path; a directory in
    a DIFFERENT repository must resolve elsewhere. The old default -- a relative filename -- fails this on
    the first pair, which is the whole defect: same agent, same config, different store, no warning.
    """
    repo = tmp_path / "repo-one"
    (repo / ".git").mkdir(parents=True)
    (repo / "src" / "deep" / "nested").mkdir(parents=True)
    other = tmp_path / "repo-two"
    (other / ".git").mkdir(parents=True)

    env = {"INSPEXIMUS_SCOPE": "project"}
    resolved = [_surface.resolve_path(None, env=env, cwd=str(d))
                for d in (repo, repo / "src", repo / "src" / "deep" / "nested")]
    assert len(set(resolved)) == 1, f"one repo resolved to {len(set(resolved))} store paths: {resolved}"
    assert os.path.isabs(resolved[0])
    assert resolved[0] == os.path.join(str(repo), ".inspeximus", "memory.json")

    elsewhere = _surface.resolve_path(None, env=env, cwd=str(other))
    assert elsewhere != resolved[0], "two different repositories shared one store"

    # CONTROL: the old relative default really does fail the invariant this test asserts. Without this, the
    # test could be passing because the fixture never reproduced the defect in the first place.
    old_style = [os.path.abspath(os.path.join(str(d), "inspeximus_memory.json"))
                 for d in (repo, repo / "src", repo / "src" / "deep" / "nested")]
    assert len(set(old_style)) == 3, "the fixture no longer reproduces the cwd-dependence defect"


def test_project_scope_finds_the_root_through_a_git_FILE_not_only_a_directory(tmp_path):
    """Worktrees and submodules carry a `.git` FILE. This repo is developed in worktrees, so a
    directory-only check would fail to find the root exactly where we work."""
    wt = tmp_path / "worktree"
    (wt / "sub").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n", encoding="utf-8")
    assert _surface.find_project_root(str(wt / "sub")) == str(wt)


def test_project_scope_without_a_repository_fails_loudly(tmp_path):
    """FAIL LOUDLY rather than silently dropping back to the cwd-relative default -- that fallback would
    reintroduce the exact defect the scope was set to remove, and would do it invisibly."""
    d = tmp_path / "not-a-repo"
    d.mkdir()
    with pytest.raises(_surface.StoreScopeError):
        _surface.resolve_path(None, env={"INSPEXIMUS_SCOPE": "project"}, cwd=str(d))
    with pytest.raises(_surface.StoreScopeError):
        _surface.resolve_path(None, env={"INSPEXIMUS_SCOPE": "nonsense"}, cwd=str(d))


def test_an_explicit_path_outranks_the_scope_and_says_so(tmp_path):
    """Precedence is fine; precedence that cannot be observed is not. `path_source` names the rule that won,
    so a scope quietly outranked by INSPEXIMUS_PATH looks different from a scope that did not work."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    env = {"INSPEXIMUS_SCOPE": "project", "INSPEXIMUS_PATH": str(tmp_path / "explicit.json")}
    assert _surface.resolve_path(None, env=env, cwd=str(repo)) == str(tmp_path / "explicit.json")


def test_the_mcp_surface_names_the_rule_that_won(tmp_path):
    """The reporting half of the precedence rule. Split from the assertion above so that one keeps running
    on the zero-dependency leg -- the resolution rule is library behaviour and needs no MCP SDK to check."""
    mcp_server = _mcp()
    env = {"INSPEXIMUS_SCOPE": "project", "INSPEXIMUS_PATH": str(tmp_path / "explicit.json")}
    assert "OUTRANKS" in mcp_server._path_source(env)
