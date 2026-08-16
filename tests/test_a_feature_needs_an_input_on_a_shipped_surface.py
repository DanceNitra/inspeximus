"""A mechanism is not shipped until something an agent can call has an input for it.

FOUR TIMES IN ONE DAY, the same defect: the code was correct, tested, documented, and reachable
from nothing anyone uses.

  * `Witness.attest()` -- 2.10.5's headline, "the surface an auditor asks directly" -- was callable
    from no shipped interface. The only caller holding a Witness object is the operator, i.e. the
    party it exists to bind.
  * `strict` and `require_authenticated_state` were `Witness` constructor arguments no CLI, server
    or MCP tool passed.
  * `--expected-pubkey` existed on `audit-build`, where the operator checks their own key against
    their own artifact, and not on `audit-verify`, where the auditor is.
  * `witness(bind_sources=True)` -- the whole VERIFY -> USE window of 2.11.0, announced publicly the
    day it shipped -- reached the MCP server as `witness()` with no arguments.

Every one was found by asking "does the mechanism have an INPUT", never by reading the code, because
the code was right each time. So this file asks that question mechanically: when a core method grows
a parameter, the tool an agent calls must grow one too, or say in an explicit exemption why not.
"""
from __future__ import annotations

import inspect
import os
import tempfile

import pytest

from inspeximus import Inspeximus

mcp_server = pytest.importorskip(
    "inspeximus.mcp_server", reason="the MCP SDK is an optional extra; `integrations` covers this")


# core method -> (mcp tool, {core param: mcp param}, {params the tool CANNOT take, and why})
PAIRS = {
    "witness": ("witness", {"records": "record_ids"}, {}),
    # `w` on the core method, `witness` on the tool -- a rename, declared rather than assumed. The
    # guard caught this one on its first run, which is the guard working: an undeclared difference
    # between the two signatures is exactly what it exists to notice.
    "verify_witness": ("verify_witness", {"w": "witness"}, {
        # a resolver is a Python callable and cannot cross a JSON tool boundary. The docstring says
        # so and says what it costs (a pinned URL comes back ORPHANED, which does not read as clean)
        # -- an exemption is only honest if the surface states the limit rather than hiding it.
        "resolver": "a Python callable cannot cross the MCP boundary; stated in the tool docstring",
    }),
    "check_sources": ("check_sources", {}, {
        "resolver": "same: a callable. The default file reader is what MCP gets.",
    }),
}


def _params(fn):
    return {n for n, p in inspect.signature(fn).parameters.items()
            if n != "self" and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)}


@pytest.mark.parametrize("core_name", sorted(PAIRS))
def test_the_mcp_tool_accepts_what_the_core_method_accepts(core_name):
    tool_name, rename, exempt = PAIRS[core_name]
    core = _params(getattr(Inspeximus, core_name))
    tool = _params(getattr(mcp_server, tool_name))
    missing = {p for p in core if rename.get(p, p) not in tool and p not in exempt}
    assert not missing, (
        f"Inspeximus.{core_name} accepts {sorted(missing)} and the MCP tool `{tool_name}` does not, "
        f"so an agent cannot reach that behaviour at all. Add the parameter, or add it to this "
        f"file's exemption map WITH the reason and state the limit in the tool's docstring -- a "
        f"feature nobody can pass an input to is not shipped.")


def test_an_exemption_must_still_name_a_real_parameter():
    """A declaration pointing at a parameter that no longer exists stops guarding anything, and the
    next parameter to take that name inherits an exemption nobody granted it. Same rule the tenant
    sweep uses for its exemption set."""
    for core_name, (_tool, rename, exempt) in PAIRS.items():
        core = _params(getattr(Inspeximus, core_name))
        for p in list(exempt) + list(rename):
            assert p in core, f"{core_name}: exemption/rename names {p!r}, which is not a parameter"


def test_the_window_actually_works_through_the_tool():
    """The signature is necessary and not sufficient: a parameter accepted and dropped reads as
    protection. This drives the whole VERIFY -> USE window through the MCP functions."""
    import hashlib
    import importlib
    d = tempfile.mkdtemp()
    os.environ["INSPEXIMUS_PATH"] = os.path.join(d, "s.json")
    m = importlib.reload(mcp_server)

    src = os.path.join(d, "policy.txt")
    body = b"deployment needs two approvers"
    open(src, "wb").write(body)
    m._MEM.remember("deployment needs two approvers", key="pol", object="two",
                    source={"doc": src, "observed_sha256": hashlib.sha256(body).hexdigest()})

    ids = [r["id"] for r in m._MEM.recall("approvers")]
    w = m.witness(ids, bind_sources=True)
    assert w["sources_bound"] == "1/1", w
    assert m.verify_witness(w)["sources_match"] is True

    open(src, "wb").write(b"deployment needs ONE approver")
    out = m.verify_witness(w)
    assert out["stale_at_use"] is True and out["sources_match"] is False
    assert out["digest_match"] is True, "the store did not change and must not be blamed"


def test_control_the_default_call_still_works():
    """The must-not-break control: `witness()` with no arguments is the shipped contract and every
    existing caller uses it."""
    import importlib
    os.environ["INSPEXIMUS_PATH"] = os.path.join(tempfile.mkdtemp(), "s.json")
    m = importlib.reload(mcp_server)
    m._MEM.remember("x", key="k", object="v")
    w = m.witness()
    assert "digest" in w and "sources" not in w
    assert m.verify_witness(w)["valid"] is True
