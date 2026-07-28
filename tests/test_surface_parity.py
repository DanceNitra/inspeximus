"""Every SURFACE opens the store with one posture — and the wedge that proved it had to.

THE DEFECT THIS PINS (measured on one store file, `ebabfa8`):

    1. the CLI corrects the payout wallet 0xAAA -> 0xBBB      store serves 0xBBB
    2. an adapter restates the OLD value                      store serves 0xAAA   <- correction undone
    3. the CLI corrects it AGAIN                              store serves 0xAAA   <- and now it is STUCK

Step 3 is what makes it serious. Once the retired value is active again, the honest re-correction looks
like an echo of the value the guard just retired, so the guard refuses it: the store cannot be put right
through the same surface that broke it. "Correct a fact once and it stays corrected" -- the first line of
the README -- was false through the ordinary integration path, through any of the nine adapters.

The cause was not a bug in the guard. It was that `echo_guard` (and the receipts-sidecar rule) were
re-declared at each entry point, and the adapters were never told: `cli._store` and `mcp_server` turned
the guard on, the adapters built `Inspeximus(path=...)` directly and inherited the LIBRARY default, which
is OFF. So the last test here is the one that matters most in a year: it fails if any surface goes back to
constructing the store itself. A default that has to be re-declared at each entry point WILL be missed at
one of them -- it already was, at twelve sites.

The library default stays OFF deliberately, and `test_the_library_default_is_untouched` pins that too: a
caller who writes `Inspeximus(path=...)` gets exactly what they wrote. Surfaces are what needed a posture.
"""
from __future__ import annotations

import ast
import os

import pytest

from inspeximus import Inspeximus
from inspeximus import _surface

KEY = "payout::wallet"


def _active_objects(path):
    """What the store SERVES for the key: the objects of its active records."""
    st = Inspeximus(path=path)
    return [r.get("object") for r in st.items if r.get("key") == KEY and r.get("status") == "active"]


def _three_steps(path, open_adapter):
    """correct -> restate the old value through an adapter -> correct again. Returns the three answers."""
    s1 = _surface.open_store(path)
    s1.remember("payout wallet is 0xAAA", key=KEY, object="0xAAA")
    s1.flush()
    s2 = _surface.open_store(path)
    s2.remember("payout wallet is 0xBBB", key=KEY, object="0xBBB")   # the correction
    s2.flush()
    after_correction = _active_objects(path)

    a = open_adapter(path)
    a.remember("payout wallet is 0xAAA", key=KEY, object="0xAAA")    # an adapter restates the OLD value
    a.flush()
    after_restatement = _active_objects(path)

    s3 = _surface.open_store(path)
    s3.remember("payout wallet is 0xBBB", key=KEY, object="0xBBB")   # the honest re-correction
    s3.flush()
    after_recorrection = _active_objects(path)
    return after_correction, after_restatement, after_recorrection


# ── the defect itself ────────────────────────────────────────────────────────────────────────────────

def _legacy(path):
    """A store in the pre-1.87.0 posture: echo guard OFF, which used to be the library default."""
    m = Inspeximus(path=path)
    m.echo_guard = False
    return m


def test_the_wedge_reproduces_with_the_guard_off(tmp_path):
    """The control. With the guard off the correction is undone AND the store wedges.

    This is here so the tests below cannot pass for a trivial reason: it demonstrates the scenario is
    capable of failing, and that the echo guard is what decides the outcome.

    It used to construct a plain `Inspeximus(path=...)` -- because that WAS this posture: the library
    default shipped echo_guard=False for byte-identical legacy behaviour, and all nine adapters inherited
    it. Since 1.87.0 the default is ON, so the legacy posture has to be asked for explicitly. The wedge
    is unchanged and still reachable, which is the point: anyone who sets echo_guard=False for
    compatibility is choosing this behaviour, and now they are choosing it rather than receiving it.
    """
    p = str(tmp_path / "m.json")
    after_correction, after_restatement, after_recorrection = _three_steps(p, _legacy)

    assert after_correction == ["0xBBB"]
    assert after_restatement == ["0xAAA"], "the restatement should undo the correction here"
    assert after_recorrection == ["0xAAA"], "and the honest re-correction should be refused as an echo"


def test_the_library_default_now_holds_the_correction(tmp_path):
    """The default a direct API user gets, which is what changed in 1.87.0.

    Measured on the live cross-system harness: with the guard off a paraphrased restatement of a retired
    value brings it back 1.000 of the time (n=8); through the product surface, 0.000. The old default
    protected byte-compatibility at the cost of the README's first line.
    """
    p = str(tmp_path / "m.json")
    after_correction, after_restatement, after_recorrection = _three_steps(
        p, lambda path: Inspeximus(path=path))

    assert after_correction == ["0xBBB"]
    assert after_restatement == ["0xBBB"], "a plain library store must not let a restatement undo it"
    assert after_recorrection == ["0xBBB"], "and it must not wedge"


def test_a_surface_opened_adapter_holds_the_correction(tmp_path):
    """Same three steps through the shared opener: 0xBBB throughout, and step 3 is not needed."""
    p = str(tmp_path / "m.json")
    after_correction, after_restatement, after_recorrection = _three_steps(
        p, lambda path: _surface.open_store(path, resolve=False))

    assert [after_correction, after_restatement, after_recorrection] == [["0xBBB"]] * 3


def test_a_real_langchain_adapter_holds_the_correction(tmp_path):
    """The same scenario through a real adapter object, not just the opener it calls."""
    pytest.importorskip("langchain_core")
    from inspeximus.integrations.langchain import InspeximusChatMessageHistory

    p = str(tmp_path / "m.json")
    steps = _three_steps(p, lambda path: InspeximusChatMessageHistory("s", path=path).store)
    assert list(steps) == [["0xBBB"]] * 3


def test_the_library_default_is_the_guard_ON():
    """CHANGED IN 1.87.0. This asserted the guard was OFF for a direct API caller.

    That default existed so a direct caller "got exactly what they constructed", byte-identical to legacy.
    What it meant in practice is that the mechanism this library exists for was off unless you knew to ask
    — and the nine framework adapters did not know, for ten releases, which is why _surface.py had to be
    written. Measured live: guard off, a paraphrased restatement of a retired value brings it back 1.000
    of the time (n=8); with it on, 0.000. `echo_guard = False` after construction restores the old
    behaviour for anyone who depends on it.
    """
    assert Inspeximus(path=None).echo_guard is True


# ── every construction site goes through the opener ───────────────────────────────────────────────────

def _autogen(mod, p):        return mod.InspeximusMemory(path=p)
def _crewai(mod, p):         return mod.InspeximusStorage(path=p)
def _adk(mod, p):            return mod.InspeximusMemoryService(path=p)
def _haystack(mod, p):       return mod.InspeximusDocumentStore(path=p)
def _lc_retriever(mod, p):   return mod.InspeximusRetriever(path=p)
def _lc_history(mod, p):     return mod.InspeximusChatMessageHistory("s", path=p)
def _lg_store(mod, p):       return mod.InspeximusStore(path=p)
def _lg_saver(mod, p):       return mod.InspeximusSaver(path=p)
def _llamaindex(mod, p):     return mod.InspeximusMemoryBlock(path=p)
def _oai_session(mod, p):    return mod.InspeximusSession("s", path=p)
def _oai_in_memory(mod, p):  return mod._store_for_path(None)     # the in-memory branch is its own site
def _pydantic_ai(mod, p):    return mod.inspeximus_toolset(path=p)

# (module, third-party import it needs, how to reach the construction site). Twelve sites, nine adapters.
SITES = [
    ("inspeximus.integrations.autogen", None, _autogen),
    ("inspeximus.integrations.crewai", None, _crewai),
    ("inspeximus.integrations.google_adk", "google.adk", _adk),
    ("inspeximus.integrations.haystack", "haystack", _haystack),
    ("inspeximus.integrations.langchain", "langchain_core", _lc_retriever),
    ("inspeximus.integrations.langchain", "langchain_core", _lc_history),
    ("inspeximus.integrations.langgraph", "langgraph", _lg_store),
    ("inspeximus.integrations.langgraph", "langgraph", _lg_saver),
    ("inspeximus.integrations.llamaindex", "llama_index.core", _llamaindex),
    ("inspeximus.integrations.openai_agents", None, _oai_session),
    ("inspeximus.integrations.openai_agents", None, _oai_in_memory),
    ("inspeximus.integrations.pydantic_ai", "pydantic_ai", _pydantic_ai),
]


@pytest.mark.parametrize("modname,dep,build", SITES, ids=[f"{m.rsplit('.', 1)[-1]}:{b.__name__}" for m, _, b in SITES])
def test_every_adapter_site_opens_through_the_surface(modname, dep, build, tmp_path, monkeypatch):
    """Each site must call the shared opener, and the store it gets must carry the guard.

    The opener is spied on rather than replaced: the store handed back is the REAL one, so this asserts
    the posture that actually reaches the adapter, not merely that a function was called.
    """
    if dep:
        pytest.importorskip(dep)
    mod = pytest.importorskip(modname)

    seen = []
    real = _surface.open_store

    def spy(*a, **kw):
        st = real(*a, **kw)
        seen.append(st)
        return st

    monkeypatch.setattr(mod, "open_store", spy)
    build(mod, str(tmp_path / f"{build.__name__}.json"))

    assert seen, f"{modname}.{build.__name__} built a store without the shared surface opener"
    for st in seen:
        assert st.echo_guard is True, f"{modname}.{build.__name__} got a store with the echo guard off"


# ── the rules the opener holds ────────────────────────────────────────────────────────────────────────

def test_the_env_var_can_turn_the_guard_off(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_ECHO_GUARD", "0")
    assert _surface.echo_guard_default() is False
    assert _surface.open_store(str(tmp_path / "m.json")).echo_guard is False


def test_any_other_value_leaves_the_guard_on(tmp_path, monkeypatch):
    """Only an explicit 0 disables it — not "false", not "", and not an unset variable."""
    for v in ("1", "", "false", "no"):
        monkeypatch.setenv("INSPEXIMUS_ECHO_GUARD", v)
        assert _surface.echo_guard_default() is True, v
    monkeypatch.delenv("INSPEXIMUS_ECHO_GUARD")
    assert _surface.echo_guard_default() is True


def test_an_existing_receipt_sidecar_keeps_receipts_on(tmp_path):
    """A surface write against a receipted store must not punch a hole in the evidence chain."""
    p = str(tmp_path / "m.json")
    seeded = Inspeximus(path=p, receipts=True)
    seeded.remember("the first fact")
    seeded.flush()
    assert os.path.exists(p + ".receipts.json")

    st = _surface.open_store(p)                     # receipts NOT requested
    assert st.receipts_enabled is True, "the sidecar was there and the surface opened receipts off"
    before = len(st._receipts)
    st.remember("a second fact")
    assert len(st._receipts) == before + 1, "the write did not extend the chain"


def test_no_sidecar_means_receipts_stay_off(tmp_path):
    """The rule is 'keep what is already there', not 'always on' — no sidecar is created unasked."""
    p = str(tmp_path / "m.json")
    st = _surface.open_store(p)
    assert st.receipts_enabled is False
    st.remember("a fact")
    st.flush()
    assert not os.path.exists(p + ".receipts.json")


def test_resolve_false_keeps_an_in_memory_store(tmp_path, monkeypatch):
    """The adapters that mean "no file" must not be given the default filename."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INSPEXIMUS_PATH", raising=False)
    assert _surface.open_store(None, resolve=False).path is None
    assert os.listdir(tmp_path) == []


def test_resolve_true_falls_back_to_the_documented_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INSPEXIMUS_PATH", raising=False)
    assert _surface.resolve_path(None) == "inspeximus_memory.json"
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "from_env.json"))
    assert _surface.resolve_path(None) == str(tmp_path / "from_env.json")
    assert _surface.resolve_path("explicit.json") == "explicit.json", "--path must beat the environment"


# ── the class guard ───────────────────────────────────────────────────────────────────────────────────

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(HERE, "inspeximus")
INTEGRATIONS = os.path.join(PKG, "integrations")
# Every surface a user WRITES through: the nine adapters, the CLI, the MCP server, the editor hook.
# `audit_bundle.py` is deliberately not here — it opens a store to VERIFY one, holds no write posture,
# and already refuses a path that does not exist.
SURFACE_FILES = sorted(
    os.path.join(INTEGRATIONS, f) for f in os.listdir(INTEGRATIONS) if f.endswith(".py")
) + [os.path.join(PKG, f) for f in ("cli.py", "mcp_server.py", "claude_code.py")]


def _direct_constructions(src: str) -> list[int]:
    """Line numbers where this file CALLS `Inspeximus(...)` itself. Imports and annotations do not count."""
    tree = ast.parse(src)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if name == "Inspeximus":
                hits.append(node.lineno)
    return hits


@pytest.mark.parametrize("path", SURFACE_FILES, ids=[os.path.basename(f) for f in SURFACE_FILES])
def test_no_surface_constructs_the_store_itself(path):
    """The guard on the CLASS, not the twelve instances.

    A tenth adapter — or a re-edit of one of the nine, or the CLI, or the MCP server, or the editor hook —
    that writes `Inspeximus(path=...)` inherits the library default and silently reopens the wedge above.
    Use `_surface.open_store`. Each of those four surfaces once held its own copy of one of the two rules,
    and no copy held both.
    """
    with open(path, encoding="utf-8") as fh:
        lines = _direct_constructions(fh.read())
    assert lines == [], f"{os.path.basename(path)} constructs Inspeximus directly at line(s) {lines}"


def test_the_editor_hook_opens_through_the_surface(tmp_path, monkeypatch):
    """The Claude Code hook writes more often than any other surface and had neither rule by reference.

    It set `echo_guard = True` by hand (so INSPEXIMUS_ECHO_GUARD never reached it) and never looked for a
    receipts sidecar, so a hook write against a receipted coding store left the record uncovered.
    """
    from inspeximus import claude_code

    monkeypatch.delenv("INSPEXIMUS_ECHO_GUARD", raising=False)
    monkeypatch.delenv("INSPEXIMUS_EMBED_URL", raising=False)   # lexical: no embedder call in a test
    monkeypatch.delenv("INSPEXIMUS_EMBED_HOOKS", raising=False)
    d = tmp_path / "proj"
    d.mkdir()
    assert claude_code._store(str(d)).echo_guard is True

    coding = str(d / ".inspeximus" / "coding_memory.json")
    seeded = Inspeximus(path=coding, receipts=True)
    seeded.remember("the receipted history starts here")
    seeded.flush()
    st = claude_code._store(str(d))
    assert st.receipts_enabled is True, "a hook write would have punched a hole in the evidence chain"

    monkeypatch.setenv("INSPEXIMUS_ECHO_GUARD", "0")
    assert claude_code._store(str(d)).echo_guard is False, "the shared posture must reach the hook too"


def test_the_cli_opens_through_the_surface(tmp_path, monkeypatch):
    """Same two rules, now held by reference rather than by a copy that only the CLI had."""
    from inspeximus import cli

    monkeypatch.delenv("INSPEXIMUS_ECHO_GUARD", raising=False)
    monkeypatch.delenv("INSPEXIMUS_EMBED_URL", raising=False)
    p = str(tmp_path / "m.json")
    assert cli._store(p).echo_guard is True

    seeded = Inspeximus(path=p, receipts=True)
    seeded.remember("the receipted history starts here")
    seeded.flush()
    assert cli._store(p).receipts_enabled is True
