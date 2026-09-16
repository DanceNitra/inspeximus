"""The EU AI Act compliance surface (compliance_report/check, retention, audit_bundle/verify) is callable over
MCP — so any MCP client (Claude Code, Cursor) gets it. Tools delegate to the free modules on the server's _MEM;
INSPEXIMUS_RECEIPTS=1 turns on the tamper-evident chain the record-keeping tools evidence."""
import sys, os, tempfile, importlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:                                        # the MCP server needs the MCP SDK; skip cleanly when it's absent
    import pytest
    pytest.importorskip("mcp.server.fastmcp")
except ImportError:                         # running standalone without pytest
    try:
        import mcp.server.fastmcp  # noqa: F401
    except Exception:
        print("SKIP: MCP SDK not installed"); sys.exit(0)


def _fresh_server(receipts):
    os.environ["INSPEXIMUS_PATH"] = os.path.join(tempfile.mkdtemp(), "m.json")
    os.environ["INSPEXIMUS_RECEIPTS"] = "1" if receipts else "0"
    import inspeximus.mcp_server as m
    return importlib.reload(m)


def test_mcp_compliance_surface_with_receipts():
    m = _fresh_server(receipts=True)
    try:
        assert m._RECEIPTS is True
        m._MEM.remember("retention is 90d", key="p::ret", object="90d")
        m._MEM.remember("retention is 30d", key="p::ret", object="30d")     # correction
        rep = m.compliance_report()
        assert len(rep["controls"]) == 22 and rep["summary"]["writes"] == 2, rep["summary"]
        assert m.compliance_check()["ok"]
        b = m.audit_bundle()
        assert b["anchor"]["n_writes"] == 2
        assert m.verify_audit_bundle(b)["ok"]
        assert m.retention(0, pii_only=False)["eligible"] == 1              # dry-run, nothing erased
    finally:
        os.environ.pop("INSPEXIMUS_RECEIPTS", None)
        os.environ.pop("INSPEXIMUS_PATH", None)


def test_mcp_check_flags_missing_receipts():
    m = _fresh_server(receipts=False)
    try:
        assert m._RECEIPTS is False
        m._MEM.remember("x", key="k", object="1")
        assert not m.compliance_check()["ok"]                               # records but no receipts -> violation
    finally:
        os.environ.pop("INSPEXIMUS_RECEIPTS", None)
        os.environ.pop("INSPEXIMUS_PATH", None)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS {fn.__name__}"); p += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{p}/{len(fns)} passed")
    sys.exit(0 if p == len(fns) else 1)
