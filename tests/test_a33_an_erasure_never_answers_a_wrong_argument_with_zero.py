"""A-33: an erasure entry point never answers a wrongly typed or unknown argument with "erased 0".

`forget_pii("email")` iterated the string as the types "e", "m", "a", "i", "l", matched nothing, and
reported erased 0. A caller running a DSAR reads that as "nothing to erase". One rule now holds for every
erasure entry point: a bare str where a collection is expected is ONE item, as `forget(ids=)` always took
it, and a name nothing here knows, a predicate that is not callable, or an `apply` that is not a bool is
refused. Checked and already right before this change: `forget(ids=)` (a str is one id),
`forget_subject` (a list raises TypeError), and the MCP tools, whose argument validation refuses a str
for `list[str]`. The CLI passes lists and bools it builds itself.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import claude_code  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


@pytest.fixture(autouse=True)
def _no_env(monkeypatch, tmp_path):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    home = str(tmp_path / "home")
    for k in ("USERPROFILE", "HOME"):
        monkeypatch.setenv(k, home)
    monkeypatch.setenv("APPDATA", os.path.join(home, "AppData", "Roaming"))


def _store(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"))
    m.remember("mail alice at alice@example.com", pii=["email"], source={"doc": "hr/alice"})
    m.remember("her badge is badge-7731", pii=["badge_id"])
    m.flush()
    return m


def test_a_bare_string_type_is_one_type(tmp_path):
    assert _store(tmp_path).forget_pii("email")["erased"] == 1


def test_a_type_nothing_here_knows_is_refused_not_erased_zero(tmp_path):
    m = _store(tmp_path)
    with pytest.raises(ValueError, match="emial"):
        m.forget_pii(types=["emial"])
    assert len(m._items) == 2, "a refused erasure removed something"


def test_a_type_only_a_record_carries_is_known(tmp_path):
    assert _store(tmp_path).forget_pii(types=["badge_id"])["erased"] == 1


def test_a_detector_type_no_record_carries_is_known_and_erases_zero(tmp_path):
    assert _store(tmp_path).forget_pii(types=["ssn"])["erased"] == 0


@pytest.mark.parametrize("where", ["alice", {"text": "alice"}])
def test_a_predicate_that_is_not_callable_is_refused_even_on_an_empty_store(tmp_path, where):
    m = Inspeximus(str(tmp_path / "empty.json"))
    with pytest.raises(TypeError, match="predicate"):
        m.forget(where=where)


def test_apply_that_is_not_a_bool_is_refused(tmp_path):
    m = _store(tmp_path)
    with pytest.raises(TypeError, match="True or False"):
        m.erase_past_copies(apply="false")
    with pytest.raises(TypeError, match="True or False"):
        claude_code.scrub_secrets(cwd=str(tmp_path), apply="false")


def test_the_one_item_rule_was_already_true_for_forget_ids(tmp_path):
    m = _store(tmp_path)
    one = m._items[0]["id"]
    assert m.forget(ids=one)["forgotten"] == 1
