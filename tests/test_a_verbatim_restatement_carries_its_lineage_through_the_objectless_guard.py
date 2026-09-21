"""3.4.0: a keyed write that repeats the current text verbatim, adding only metadata, lands; and the
objectless guard reports the writes it retires.

Measured 2026-09-21 on the Crew OS store (reported by the Crew OS session, reproduced here): a key
whose current record carried an `object` refused a write of the SAME text with `derived_from`, because
the objectless clobber guard saw a write with no object on an object-bearing key. The caller could add
lineage to an existing record only by changing the text. The guard exists to stop junk text displacing
a ledgered value (revert_by_reference_probe.py, B2 resistance 0.00 to 1.00); the same text displaces
nothing, so a verbatim restatement now inherits the incumbent's object and goes through supersession.

Second defect, found while reproducing: the guard returned the write as retired but `last_write`
still said status active, blocked False, the exact shape the echo guard had already been fixed for.

Controls: junk text on the same key is still retired, and now says so in `last_write`; a reworded
write with no object is still retired; a key that never used objects is unaffected.
"""
from __future__ import annotations

from inspeximus import Inspeximus


def _by(m):
    return {r["id"]: r for r in m.items}


def test_the_same_text_with_new_lineage_lands_and_inherits_the_object(tmp_path):
    m = Inspeximus(str(tmp_path / "mem.json"))
    parent = m.remember("the parent fact", key="P")
    a = m.remember("layer text for K", key="K", object="layer-v1")
    b = m.remember("layer text for K", key="K", derived_from=[parent])
    by = _by(m)
    assert by[b]["status"] == "active" and by[a]["status"] == "superseded"
    assert by[b]["object"] == "layer-v1", "the restatement carries the value it restates"
    assert by[b]["derived_from"] == [parent]
    assert m.last_write["status"] == "active" and m.last_write["blocked"] is False
    assert m.recall("layer text", k=1)[0]["id"] == b


def test_junk_text_is_still_retired_and_the_verdict_is_reported(tmp_path):
    m = Inspeximus(str(tmp_path / "mem.json"))
    a = m.remember("the region is frankfurt", key="region", object="frankfurt")
    c = m.remember("go back to the old one", key="region")
    by = _by(m)
    assert by[c]["status"] == "superseded" and by[c]["meta"]["superseded_by_policy"] == "objectless_guard"
    assert by[a]["status"] == "active"
    assert m.last_write == {**m.last_write, "id": c, "status": "superseded", "blocked": True,
                            "policy": "objectless_guard", "current_id": a}
    assert "retired on arrival" in m.last_write["note"]


def test_a_reworded_write_without_an_object_is_still_retired(tmp_path):
    m = Inspeximus(str(tmp_path / "mem.json"))
    a = m.remember("the region is frankfurt", key="region", object="frankfurt")
    d = m.remember("the region is Frankfurt, Germany", key="region")
    by = _by(m)
    assert by[d]["status"] == "superseded" and by[a]["status"] == "active"
    assert m.last_write["policy"] == "objectless_guard"


def test_a_key_that_never_used_objects_is_unaffected(tmp_path):
    m = Inspeximus(str(tmp_path / "mem.json"))
    a = m.remember("layer text for K", key="K")
    b = m.remember("layer text for K", key="K", derived_from=[a])
    by = _by(m)
    assert by[b]["status"] == "active" and by[b].get("object") is None and m.last_write["policy"] is None
