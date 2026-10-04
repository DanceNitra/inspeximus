"""AUDIT-B 3.16.3: `state_digest()` returns what it returned, and reads each row without the record's `get`.

Through the MCP server, with the action ledger on, one `remember` took the digest twice over 13,359 records:
155,379 `_TrackedDict.get` calls and 0.095 s of the 0.18 s spent there. The fields it reads are scalars, so
`dict.get` gives the same value. The differential below holds the digest to the 3.16.1 implementation, frozen.
"""
import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


def frozen_state_digest(self) -> str:
    """3.16.1's state_digest, verbatim."""
    h = hashlib.sha256()
    for r in sorted(self._tenant_rows(), key=lambda x: x.get("id") or ""):
        line = "\x1f".join([
            str(r.get("id") or ""), str(r.get("status") or "active"),
            repr(r.get("ts")), str(r.get("key") or ""), str(r.get("tenant") or ""),
            hashlib.sha256((r.get("text") or "").encode("utf-8")).hexdigest(),
        ])
        h.update(line.encode("utf-8")); h.update(b"\x1e")
    return h.hexdigest()


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)


def _store(tmp_path, tenant=None):
    m = Inspeximus(str(tmp_path / "s.json"), tenant=tenant) if tenant else Inspeximus(str(tmp_path / "s.json"))
    for i in range(30):
        m.remember(f"fact number {i} about the build", key=f"k:{i % 7}", object=f"v{i}", tags=["t"])
    m.remember("keyless note", mtype="semantic")
    m.remember("unicode note é中文 \U0001f600")
    m.retire("k:3", "test")
    m.flush()
    return m


def test_the_digest_equals_the_frozen_implementation(tmp_path):
    m = _store(tmp_path)
    assert m.state_digest() == frozen_state_digest(m)
    assert any(r.get("status") != "active" for r in m._items), "control: the fixture has retired rows"


def test_the_digest_equals_the_frozen_implementation_on_a_tenant_view(tmp_path):
    m = _store(tmp_path)
    view = m.for_tenant("acme")
    view.remember("acme only note")
    assert view.state_digest() == frozen_state_digest(view)
    assert view.state_digest() != m.state_digest(), "control: the tenant's slice differs from the whole"


def test_the_digest_still_changes_with_every_field_it_covers(tmp_path):
    m = _store(tmp_path)
    base = m.state_digest()
    r = next(x for x in m._items if x.get("status") == "active")
    for field, new in (("text", "edited"), ("key", "other:key"), ("ts", 12345.0), ("status", "superseded")):
        old = r.get(field)
        r[field] = new
        assert m.state_digest() != base, field
        r[field] = old
    assert m.state_digest() == base


def test_the_digest_makes_no_python_level_get_on_a_record(tmp_path, monkeypatch):
    m = _store(tmp_path)
    calls = {"n": 0}
    real = core._TrackedDict.get

    def counted(self, *a, **k):
        calls["n"] += 1
        return real(self, *a, **k)
    monkeypatch.setattr(core._TrackedDict, "get", counted)
    frozen_state_digest(m)
    assert calls["n"] >= 6 * len(m._items), "control: the old shape pays six gets per row"
    calls["n"] = 0
    m.state_digest()
    assert calls["n"] == 0, calls
