"""A log that succeeds another one carries the predecessor's last signed checkpoint in its first leaf.

The policy is entry 0 of every log this service writes, and the policy is hashed into that leaf. So
putting the predecessor's checkpoint in the policy's notes makes the handover part of the new tree:
nobody can later swap which log this one claims to follow without changing entry 0, and entry 0 is
under every root the new log will ever sign.
"""
from __future__ import annotations

import argparse
import json

from inspeximus.scrapi import policy_from_args
from inspeximus.transparency import TransparencyService

PREDECESSOR = ("92.5.74.17.sslip.io/log\n10\nPv5KupIa7/Bfwe2iJD8aF+aUz2ctGQbM9EvPlcRGTqc=\n\n"
               "— 92.5.74.17.sslip.io/log Qd/ielBriqIS9rk7jwGjHjdM5Eu866mDvhUHNrRjYGYCb+qzT6XG+dTgjV3rXJdcaracRiOIA4CgnAXzGYVCP/fB2QI=\n")


def _args(**kw):
    base = dict(policy_name="succ", accept_any_issuer=True, policy_notes_file=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_the_notes_file_lands_in_entry_zero(tmp_path):
    notes = tmp_path / "succession.json"
    notes.write_text(json.dumps({"predecessor_checkpoint": PREDECESSOR}), encoding="utf-8")
    log = tmp_path / "reg.log"
    TransparencyService(str(log), policy_from_args(_args(policy_notes_file=str(notes))),
                        lambda m: b"s" * 64, lambda *_: True)
    first = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert first["kind"] == "registration-policy" and first["seq"] == 0
    assert json.loads(first["policy"]["notes"])["predecessor_checkpoint"] == PREDECESSOR


def test_CONTROL_without_the_file_entry_zero_names_no_predecessor(tmp_path):
    log = tmp_path / "reg.log"
    TransparencyService(str(log), policy_from_args(_args()), lambda m: b"s" * 64, lambda *_: True)
    first = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert first["policy"]["notes"] == ""
