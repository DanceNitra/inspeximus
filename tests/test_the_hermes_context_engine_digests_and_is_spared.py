"""The Hermes context engine digests an oversized tool result, and Hermes's own pass spares it.

The sparing test is the one that matters. MEASURED 2026-10-29 on the owner's transcript: letting
Hermes's proactive pass run over a digest took the recovered share of buried question terms from
61.5% to 0.0%, because its summarizer replaces any long tool result, digest included, with a
byte count. The engine spares its own digests on that pass and still yields to the pressure pass,
which Hermes marks by passing protected_skills=None.

Hermes is not a dependency of this package and its own import chain needs ruamel, so the engine
is exercised against a stub parent that carries the contract it uses, and again against the real
ContextCompressor wherever Hermes is importable. The stub alone would measure a parent we wrote
ourselves, so test_the_stub_matches_the_real_parent compares the two signatures and fails on a
rename upstream; running under Hermes's own interpreter turns every test here into a real one.
"""
import importlib.util
import inspect
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_FILE = os.path.join(ROOT, "integrations", "hermes-context-engine-inspeximus", "__init__.py")
HERMES_SRC = os.path.join(
    os.environ.get("HERMES_HOME") or os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes"),
    "hermes-agent")

# The contract the engine uses from Hermes, as Hermes implements it. Kept this short on purpose:
# every line here is a line the parity test has to be able to check.
STUB_SOURCE = '''
class ContextCompressor:
    protect_last_n = 20

    def __init__(self, model="", proactive_prune_tokens=0, proactive_prune_min_result_chars=8000,
                 **kwargs):
        self.model = model
        self.proactive_prune_tokens = proactive_prune_tokens
        self.proactive_prune_min_result_chars = proactive_prune_min_result_chars

    def prune_tool_results_only(self, messages, current_tokens=None):
        return messages, 0

    @staticmethod
    def _demote_tool_result_at(result, idx, call_id_to_tool, min_prune_chars, protected_skills=None):
        msg = result[idx]
        content = msg.get("content")
        if not isinstance(content, str) or len(content) <= min_prune_chars:
            return False
        tool_name = call_id_to_tool.get(msg.get("tool_call_id", ""), ("unknown", ""))[0]
        result[idx] = {**msg, "content": "[%s] (%d chars result)" % (tool_name, len(content))}
        return True

    def get_status(self):
        return {}
'''


def _load_engine(parent_module):
    """Import the shipped engine file against the given parent module."""
    saved = sys.modules.get("agent.context_compressor"), sys.modules.get("agent")
    agent = sys.modules.get("agent") or types.ModuleType("agent")
    sys.modules["agent"] = agent
    sys.modules["agent.context_compressor"] = parent_module
    try:
        spec = importlib.util.spec_from_file_location("hermes_engine_inspeximus", ENGINE_FILE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for key, value in zip(("agent.context_compressor", "agent"), saved):
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


def _real_parent():
    """Hermes's own context_compressor module, or None where Hermes cannot be imported."""
    if not os.path.isfile(os.path.join(HERMES_SRC, "agent", "context_compressor.py")):
        return None
    sys.path.insert(0, HERMES_SRC)
    try:
        import agent.context_compressor as real
        return real
    except Exception:                                # noqa: BLE001 -- a missing extra, not a defect
        return None
    finally:
        sys.path.remove(HERMES_SRC)


@pytest.fixture(scope="module")
def stub_parent():
    mod = types.ModuleType("agent.context_compressor")
    exec(compile(STUB_SOURCE, "<stub_context_compressor>", "exec"), mod.__dict__)
    return mod


@pytest.fixture(params=["stub", "hermes"], scope="module")
def engine_module(request, stub_parent):
    if request.param == "stub":
        return _load_engine(stub_parent)
    real = _real_parent()
    if real is None:
        pytest.skip("Hermes is not importable here; run under its own interpreter for this arm")
    return _load_engine(real)


def _tool(content, call_id="c1"):
    return {"role": "tool", "tool_name": "terminal", "tool_call_id": call_id, "content": content}


def _engine(engine_module):
    engine = engine_module.InspeximusContextEngine()
    engine.protect_last_messages = 0
    return engine


def test_an_oversized_result_becomes_a_digest_that_names_its_file(engine_module):
    raw = "\n".join("line %d: inspeximus digest probe payload" % i for i in range(400))
    out, pruned = _engine(engine_module).prune_tool_results_only([_tool(raw)])
    text = out[0]["content"]
    assert pruned >= 1
    assert text.startswith(engine_module.MARKER), text[:200]
    assert len(text) < len(raw), (len(text), len(raw))
    path = text.strip().splitlines()[-1].split("full output: ", 1)[1]
    assert open(path, encoding="utf-8").read() == raw, "the file must hold the output verbatim"


def test_a_short_result_is_left_alone(engine_module):
    """CONTROL. An engine that digests everything would pass the test above."""
    short = "exit 0\n"
    out, _ = _engine(engine_module).prune_tool_results_only([_tool(short)])
    assert out[0]["content"] == short


def test_the_protected_tail_is_left_alone(engine_module):
    """CONTROL. The most recent results are what the agent is reasoning about right now.

    The window is the engine's own, not Hermes's protect_last_n: that one is 20 messages and
    governs LLM compaction, and at 20 a short session digests nothing at all.
    """
    engine = engine_module.InspeximusContextEngine()
    assert engine.protect_last_messages < engine.protect_last_n, "the digest window must be shorter"
    engine.protect_last_messages = 1
    raw = "payload " * 1000
    out, _ = engine.prune_tool_results_only([_tool(raw)])
    assert out[0]["content"] == raw


def test_a_digest_is_spared_on_the_proactive_pass_and_demoted_under_pressure(engine_module):
    cls = engine_module.InspeximusContextEngine
    digest = engine_module.MARKER + "\n---\nranked lines\n---\nfull output: C:\\tmp\\c.txt" + "y" * 2000
    calls = {"c1": ("terminal", '{"command":"ls"}')}

    def demote(content, protected):
        rows = [_tool(content)]
        changed = cls._demote_tool_result_at(rows, 0, calls, 200, protected)
        return changed, rows[0]["content"]

    changed, kept = demote(digest, set())
    assert changed is False and kept == digest, "the proactive pass must spare a digest"

    changed, kept = demote(digest, None)
    assert changed is True and len(kept) < len(digest), "the pressure pass must still win"

    # CONTROL, both directions: raw output is demoted on either pass. Without this the assertions
    # above would also pass on an override that spares every tool result.
    for protected in (set(), None):
        changed, kept = demote("x" * 9000, protected)
        assert changed is True and len(kept) < 9000, protected


def test_the_engine_names_itself_and_counts_its_work(engine_module):
    engine = _engine(engine_module)
    assert engine.name == "inspeximus"
    engine.prune_tool_results_only([_tool("payload " * 1000)])
    status = engine.get_status()
    assert status["inspeximus_digests"] == 1
    assert status["inspeximus_bytes_kept"] < status["inspeximus_bytes_in"]


def test_the_stub_matches_the_real_parent(stub_parent):
    """The stub is only evidence while it still describes Hermes. A renamed or re-signed method
    upstream must fail here rather than let the stub arm keep passing on its own invention."""
    real = _real_parent()
    if real is None:
        pytest.skip("Hermes is not importable here")
    theirs, ours = real.ContextCompressor, stub_parent.ContextCompressor
    for name in ("prune_tool_results_only", "_demote_tool_result_at", "get_status"):
        assert hasattr(theirs, name), "Hermes no longer has %s" % name
        mine = list(inspect.signature(getattr(ours, name)).parameters)
        upstream = list(inspect.signature(getattr(theirs, name)).parameters)
        assert mine == upstream[:len(mine)], (name, mine, upstream)
    assert isinstance(getattr(theirs, "protect_last_n", None), int)


def test_select_context_digests_the_request_and_leaves_the_transcript_alone(engine_module):
    """The engine's main path. Hermes calls prune_tool_results_only only when the context is
    over threshold AND compaction is blocked, so a digest that happens only there happens
    almost never."""
    engine = _engine(engine_module)
    raw = "payload line\n" * 500
    request = [{"role": "user", "content": "hi"}, _tool(raw)]
    history = [dict(m) for m in request]
    selected = engine.select_context(request, conversation_messages=history, budget_tokens=100000)
    assert selected is not None, "select_context must act on an oversized result"
    assert selected[1]["content"].startswith(engine_module.MARKER)
    assert request[1]["content"] == raw, "the request list passed in must not be mutated"
    assert history[1]["content"] == raw, "the transcript must keep the whole output"


def test_select_context_returns_none_when_there_is_nothing_to_digest(engine_module):
    """CONTROL. A replacement on every turn would break the prompt cache prefix for nothing."""
    engine = _engine(engine_module)
    assert engine.select_context([{"role": "user", "content": "hi"}, _tool("exit 0")]) is None


def test_the_same_output_digests_to_the_same_bytes(engine_module):
    """Twice over the same result must give one capture file and identical bytes: the host
    re-derives the prompt cache breakpoints from the selected list, so a digest that differs
    per turn costs the cache on every turn."""
    engine = _engine(engine_module)
    raw = "stable payload\n" * 500
    first = engine.select_context([_tool(raw), {"role": "user", "content": "x"}])
    second = engine.select_context([_tool(raw), {"role": "user", "content": "x"}])
    assert first is not None and second is not None
    assert first[0]["content"] == second[0]["content"]
    assert engine.get_status()["inspeximus_digests"] == 1, "the second call must hit the cache"


def test_the_digest_ranks_against_the_call_arguments(engine_module):
    """The arguments are the best intent available and they are in the same request list.

    MEASURED 2026-10-29: ranking against them kept 76.7% of the question's terms, against 56.1%
    when ranking against the user's last message. The assertion below is the mechanism, not the
    ratio: the digest of an output must differ when only the call that asked for it differs.
    """
    engine = _engine(engine_module)
    body = "\n".join(
        ["alpha section: hydraulics and the pressure curve"] * 40
        + ["beta section: the certificate chain and its expiry"] * 40)
    raw = body + "\n" + "filler line\n" * 300

    def digest_with(command):
        local = _engine(engine_module)
        request = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "terminal",
                                          "arguments": '{"command": "%s"}' % command}}]},
            _tool(raw),
        ] + [{"role": "user", "content": "x"}] * 0
        selected = local.select_context(request)
        assert selected is not None
        return selected[1]["content"]

    alpha = digest_with("grep hydraulics pressure curve")
    beta = digest_with("grep certificate chain expiry")
    assert alpha != beta, "the digest ignored the call, so the intent is not reaching the saver"


def test_a_result_whose_call_is_missing_still_digests(engine_module):
    """CONTROL. A transcript without the assistant message must not lose the digest: the tool's
    name is the fallback intent, and a crash here would cost the output."""
    engine = _engine(engine_module)
    out = engine.select_context([_tool("payload " * 1000)])
    assert out is not None and out[0]["content"].startswith(engine_module.MARKER)
