"""Round six: the install path silently lost everything, and my own 1.63.0 fix rejected honest certificates.

Two of these were found by auditing the SHIPPED WHEEL as a new user meets it, rather than the repo. Six
rounds of source auditing had not touched the question "does this work for someone who has never seen the
checkout", and the answer was no: on both paths the documentation tells you to use, every write was lost with
no error at all.

The third is mine. The 1.63.0 certificate fix compared a REQUEST-SCOPED claim against the UNSCOPED tombstone
chain, so any store that had served more than one DSAR failed its own honest certificate — and the test I
wrote for it could not see that, because its fixture only ever created ONE request. A fixture that cannot
express the failing shape is not coverage.
"""
import copy
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core
from inspeximus import Inspeximus


# ── the store must land where the docs say it does ──────────────────────────────────────────────────
def test_a_nested_store_path_creates_its_directory():
    """The plugin advertises `.inspeximus/memory.json`, and in a fresh project that folder does not exist.
    Measured before: `remember()` returned an id, in-process recall worked, the directory was never created,
    and a NEW process saw nothing — over MCP without even a warning. A memory layer that forgets everything
    between sessions, on the path its own docs tell you to use."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, ".inspeximus", "memory.json")
    m = Inspeximus(path=p)
    m.remember("My deploy key is ABC123")
    m.flush()

    assert os.path.exists(p), "the store file was never written"
    assert [h["text"] for h in Inspeximus(path=p).recall("deploy key")] == ["My deploy key is ABC123"]


def test_a_tilde_in_the_path_is_expanded():
    """The README's headline MCP command is `INSPEXIMUS_PATH=~/.inspeximus_memory.json`. A literal `~` is not
    a directory, so every documented MCP setup lost its memory on restart."""
    target = os.path.expanduser("~/.inspeximus_tilde_probe.json")
    if os.path.exists(target):
        os.remove(target)
    try:
        m = Inspeximus(path="~/.inspeximus_tilde_probe.json")
        m.remember("x")
        m.flush()
        assert os.path.exists(target), "~ was not expanded"
        assert not os.path.exists("~"), "a literal '~' directory was created instead"
    finally:
        for f in (target, target + ".receipts.json"):
            if os.path.exists(f):
                os.remove(f)


def test_an_unwritable_parent_is_still_reported():
    """Creating the directory must not turn a real failure into a silent one. A parent that CANNOT become a
    directory (because it is a file) still has to surface."""
    d = tempfile.mkdtemp()
    blocker = os.path.join(d, "not-a-dir")
    open(blocker, "w", encoding="utf-8").write("i am a file")

    m = Inspeximus(path=os.path.join(blocker, "m.json"), receipts=True)
    m.remember("fact")
    assert m.verify_writes()[0] is False
    with pytest.raises(OSError):
        m.flush()


def test_the_cli_writes_to_a_nested_path_too():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "nested", "deep", "m.json")
    out = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", p, "remember", "a fact"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert out.returncode == 0, out.stdout + out.stderr
    assert os.path.exists(p)


# ── the scoped erasure certificate ──────────────────────────────────────────────────────────────────
def _two_requests():
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=True)
    m.remember("alice", source={"doc": "alice"})
    m.remember("bob", source={"doc": "bob"})
    m.forget_subject("alice", request_id="DSAR-ALICE", basis="gdpr-art17")
    m.forget_subject("bob", request_id="DSAR-BOB", basis="gdpr-art17")
    return m


def test_a_request_scoped_certificate_verifies_on_a_store_with_several_requests():
    """THE regression. `erasure_certificate(request_id=X)` summarises ONE request but ships the WHOLE
    tombstone chain, so 1.63.0's derivation compared a scoped claim against an unscoped chain and rejected
    every honest certificate from a store that had served more than one DSAR:

        VALID: False | count says 1 but the tombstone chain holds 2

    My test for that fix could not see it: its fixture made a single request, the one shape where scoped and
    unscoped are identical."""
    m = _two_requests()
    cert = m.erasure_certificate(request_id="DSAR-ALICE")
    assert cert["count"] == 1 and cert["request_ids"] == ["DSAR-ALICE"]

    res = core.verify_erasure_certificate(cert, store_items=m.items)
    assert res["valid"] is True, res["problems"]
    assert res["count"] == 1, "the returned count must be the certificate's scope, not the whole chain"


def test_an_unscoped_certificate_still_covers_everything():
    m = _two_requests()
    res = core.verify_erasure_certificate(m.erasure_certificate(), store_items=m.items)
    assert res["valid"] is True and res["count"] == 2


@pytest.mark.parametrize("field,value", [
    ("count", 99),
    ("erased_memory_ids", ["never-existed"]),
    ("request_ids", ["SOMEONE-ELSES"]),
])
def test_forging_a_scoped_certificate_still_fails(field, value):
    """Scoping the derivation must not reopen the hole it was written to close."""
    m = _two_requests()
    cert = copy.deepcopy(m.erasure_certificate(request_id="DSAR-ALICE"))
    cert[field] = value
    assert core.verify_erasure_certificate(cert, store_items=m.items)["valid"] is False


def test_a_certificate_claiming_another_requests_ids_fails():
    """The sharpest case: keep the scope label but swap in the OTHER request's erased ids."""
    m = _two_requests()
    alice = copy.deepcopy(m.erasure_certificate(request_id="DSAR-ALICE"))
    bob = m.erasure_certificate(request_id="DSAR-BOB")
    alice["erased_memory_ids"] = bob["erased_memory_ids"]
    assert core.verify_erasure_certificate(alice, store_items=m.items)["valid"] is False


# ── smaller shipped-artefact defects ────────────────────────────────────────────────────────────────
def test_the_update_check_points_at_a_package_that_exists():
    """It pointed at `agora-inspeximus`, which 404s — so the notice could never fire, and if it had it would
    have told the user to install a package that does not exist. The same wrong name was in claims_audit.py."""
    import inspeximus._update as upd
    assert "pypi.org/pypi/inspeximus/json" in upd._PYPI_JSON
    # Only the CODE, not the comment that records what the wrong value used to be.
    src = [l for l in open(upd.__file__, encoding="utf-8").read().split(chr(10))
           if "agora-inspeximus" in l and not l.strip().startswith("#")]
    assert not src, src


def test_the_mcp_server_reports_its_own_version():
    """FastMCP takes no `version=`, and without one the handshake reported the MCP SDK's version — so a
    client asking which inspeximus it was talking to got the SDK's number."""
    pytest.importorskip("mcp")
    import importlib
    os.environ["INSPEXIMUS_PATH"] = os.path.join(tempfile.mkdtemp(), "m.json")
    mod = importlib.reload(importlib.import_module("inspeximus.mcp_server"))
    import inspeximus
    assert mod.mcp._mcp_server.version == inspeximus.__version__


def test_the_llamaindex_adapter_names_the_right_missing_package():
    """Its first import was `pydantic`, which llama-index pulls in — so without the extra the user was told
    to install pydantic, installed it, and hit the next missing import."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "inspeximus", "integrations", "llamaindex.py"), encoding="utf-8").read()
    assert 'inspeximus[llamaindex]' in src
