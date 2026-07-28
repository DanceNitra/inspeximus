"""The documented off-switch has to work through the LIBRARY, not only through a surface.

1.87.0 flipped the default to ON. `_surface.open_store` read `INSPEXIMUS_ECHO_GUARD`; the constructor
hardcoded `True` and took no argument at all. Measured in three subprocesses (the env is read at
construction, so setting it in-process proves nothing):

    INSPEXIMUS_ECHO_GUARD=0     -> echo_guard=True, active ['B']
    INSPEXIMUS_ECHO_GUARD=1     -> echo_guard=True, active ['B']
    unset                       -> echo_guard=True, active ['B']

All three identical: the switch the docs named was dead for a direct API user, and `Inspeximus(
echo_guard=False)` raised TypeError. A switch that reports nothing when it fails to take effect is worse
than no switch -- the operator sets it, sees writes still retired, and has no way to tell which of the two
is wrong.

One resolver now decides for both: explicit argument > env var > ON. `_surface.echo_guard_default()`
delegates to it rather than re-deriving it, because keeping two copies in agreement by convention lasted
exactly as long as nobody added a tenth entry point -- which is the same failure that left the nine
adapters unguarded for ten releases.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus, _surface  # noqa: E402
from inspeximus.core import _resolve_echo_guard  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PROG = (
    "import sys, os, tempfile; sys.path.insert(0, r'{repo}');"
    "from inspeximus import Inspeximus;"
    "st = Inspeximus(path=os.path.join(tempfile.mkdtemp(), 's.json'));"
    "st.remember('v is A', key='k', object='A');"
    "st.remember('v is B', key='k', object='B');"
    "st.remember('v is A again', key='k', object='A');"
    "print(st.echo_guard,"
    "      [r.get('object') for r in st.items if r.get('key')=='k' and r.get('status')=='active'])"
).format(repo=REPO)


def _subproc(value):
    """The env var is read at construction; only a fresh process measures it honestly."""
    env = dict(os.environ)
    env.pop("INSPEXIMUS_ECHO_GUARD", None)
    if value is not None:
        env["INSPEXIMUS_ECHO_GUARD"] = value
    out = subprocess.run([sys.executable, "-c", _PROG], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr[-400:]
    return out.stdout.strip()


def _store(**kw):
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), **kw)


def _echo_lands(st):
    """A -> B -> A. True if the third write became current, i.e. the guard did not act."""
    st.remember("v is A", key="k", object="A")
    st.remember("v is B", key="k", object="B")
    st.remember("v is A again", key="k", object="A")
    return [r.get("object") for r in st.items
            if r.get("key") == "k" and r.get("status") == "active"] == ["A"]


def test_the_env_var_reaches_library_code():
    """THE defect: this was 'True [...]' -- identical to the guarded arms."""
    assert _subproc("0") == "False ['A']"


def test_the_env_var_is_not_the_only_thing_that_matters():
    """CONTROL. If '=0' were passed through unconditionally the switch would look fine while being
    stuck the other way -- so the ON arms must genuinely differ from the OFF one."""
    assert _subproc("1") == "True ['B']"
    assert _subproc(None) == "True ['B']", "unset must still mean ON -- that is the 1.87.0 default"


def test_the_constructor_takes_an_explicit_posture():
    st_off, st_on = _store(echo_guard=False), _store(echo_guard=True)
    assert st_off.echo_guard is False and st_on.echo_guard is True
    assert _echo_lands(st_off) is True
    assert _echo_lands(st_on) is False, "the two arms must disagree, else the argument does nothing"


def test_an_explicit_argument_beats_the_environment(monkeypatch):
    """A caller who names a posture gets it. Otherwise a deployment-wide env var would silently
    override a store that was constructed to be strict on purpose."""
    monkeypatch.setenv("INSPEXIMUS_ECHO_GUARD", "0")
    assert _resolve_echo_guard(True) is True
    assert _resolve_echo_guard(False) is False
    assert _resolve_echo_guard(None) is False, "with no explicit argument the env var decides"
    monkeypatch.setenv("INSPEXIMUS_ECHO_GUARD", "1")
    assert _resolve_echo_guard(False) is False
    assert _resolve_echo_guard(None) is True


def test_the_default_is_on_with_no_environment_at_all(monkeypatch):
    monkeypatch.delenv("INSPEXIMUS_ECHO_GUARD", raising=False)
    assert _resolve_echo_guard() is True
    assert _store().echo_guard is True


def test_the_surface_and_the_library_cannot_drift_apart(monkeypatch):
    """They already had. The surface honoured the env var while the library ignored it, and nothing
    in the suite compared the two."""
    for val, want in (("0", False), ("1", True)):
        monkeypatch.setenv("INSPEXIMUS_ECHO_GUARD", val)
        assert _surface.echo_guard_default() is want
        assert _resolve_echo_guard() is want, "the surface and the library must resolve identically"
