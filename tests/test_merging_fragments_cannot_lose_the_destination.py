"""An unreadable store is not an empty one, and a merge that confuses the two destroys data.

FOUND LIVE, on this plugin's own dogfood repository. The project store there is 1.9 MB and does not
parse: a complete JSON array followed by a 7-character tail left behind when a shorter write landed
on a longer file without truncating it. The first version of `merge_fragments` caught the decode
error and returned `[]`, so the destination read as EMPTY -- and a real run would have replaced
2,990 recoverable records with the 2,290 gathered from the fragments.

Nothing was lost, because the merge is dry by default and the dry run printed `already_there: 0`
against a 1.9 MB file, which is the number that did not add up. That is the only reason this is a
test rather than an incident.

The rules this file holds:
  * a destination that does not parse ABORTS the merge, loudly;
  * fragments are read, never removed;
  * the destination is backed up before it is written;
  * merging is idempotent -- a second run adds nothing, because records carry ids;
  * and a dry run writes nothing at all, which is what makes the check above possible.
"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus.claude_code import merge_fragments  # noqa: E402


def _repo_with_fragments(n_frag=2, per=3):
    root = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", root], capture_output=True)
    made = 0
    for i in range(n_frag):
        d = os.path.join(root, f"sub{i}", ".inspeximus")
        os.makedirs(d, exist_ok=True)
        recs = [{"id": f"f{i}r{j}", "text": f"fragment {i} record {j}"} for j in range(per)]
        made += per
        with open(os.path.join(d, "coding_memory.json"), "w", encoding="utf-8") as f:
            json.dump(recs, f)
    return root, made


def test_the_fragments_are_gathered():
    root, made = _repo_with_fragments()
    r = merge_fragments(root, apply=True)
    assert r["new"] == made and r["applied"]
    with open(r["destination"], encoding="utf-8") as f:
        assert len(json.load(f)) == made


def test_a_dry_run_writes_nothing():
    root, _ = _repo_with_fragments()
    r = merge_fragments(root, apply=False)
    assert r["new"] > 0 and not r["applied"]
    assert not os.path.exists(r["destination"]), (
        "a dry run created the destination; the whole point is that it can be inspected first")


def test_fragments_survive_the_merge():
    root, _ = _repo_with_fragments()
    r = merge_fragments(root, apply=True)
    for f in r["fragments"]:
        assert os.path.exists(f["path"]), (
            "a fragment was removed. Reporting and gathering must never delete the source -- this "
            "project has already lost data to a well-meant cleanup once")


def test_merging_twice_adds_nothing():
    root, made = _repo_with_fragments()
    merge_fragments(root, apply=True)
    second = merge_fragments(root, apply=True)
    assert second["new"] == 0 and second["collisions"] == made


def test_the_destination_is_backed_up_before_it_is_written():
    root, _ = _repo_with_fragments()
    merge_fragments(root, apply=True)
    again = merge_fragments(root, apply=True)
    # nothing new the second time, so no write and no backup -- add a fragment to force one
    d = os.path.join(root, "extra", ".inspeximus")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "coding_memory.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": "x1", "text": "new"}], f)
    third = merge_fragments(root, apply=True)
    assert third["backup"] and os.path.exists(third["backup"]), (
        "the destination was overwritten without a copy of what it held")
    assert again["new"] == 0


def test_an_unparseable_destination_aborts_instead_of_reading_as_empty():
    """THE ONE THAT MATTERS. Live example: 1.9 MB, a valid array plus a 7-char tail from a
    non-truncating write. Treated as empty, a merge replaces every record in it."""
    root, _ = _repo_with_fragments()
    dest_dir = os.path.join(root, ".inspeximus")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "coding_memory.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump([{"id": "keep-me", "text": "2990 records worth of this"}], f)
        f.write('n"\n }\n]')                       # the exact shape of the live corruption
    before = open(dest, encoding="utf-8").read()
    with pytest.raises(ValueError, match="does not parse"):
        merge_fragments(root, apply=True)
    assert open(dest, encoding="utf-8").read() == before, (
        "the corrupt destination was modified despite the abort")


def test_the_control_a_healthy_destination_is_merged_into_not_replaced():
    """Without this, 'abort on unparseable' could be satisfied by aborting on everything."""
    root, made = _repo_with_fragments()
    dest_dir = os.path.join(root, ".inspeximus")
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, "coding_memory.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": "pre", "text": "was here first"}], f)
    r = merge_fragments(root, apply=True)
    with open(r["destination"], encoding="utf-8") as f:
        ids = {x["id"] for x in json.load(f)}
    assert "pre" in ids and len(ids) == made + 1
