"""`provenance()` — the single answer to "where did this fact come from, and how far does it bind?".

It ASSEMBLES existing primitives (source + lineage taint, attestation, grade, history, write receipts,
anchor); these tests pin that each part reaches the caller, that lineage rides transitively through a
derived write, and — the property the whole surface exists for — that a post-hoc RELABEL of a record's
source shows up as `attribution_matches_receipt=False` instead of passing silently.
"""
import os, sys, subprocess, tempfile, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus


def _store(**kw):
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), **kw)


def test_provenance_of_a_corrected_fact():
    m = _store(receipts=True)
    m.remember("billing uses api keys", key="billing::auth", object="api-keys",
               source={"doc": "runbook-v1"}, agent_id="kael", user_id="rasto")
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr-014"})
    p = m.provenance(key="billing::auth")

    assert p["found"] and p["key"] == "billing::auth"
    assert p["current"]["object"] == "oauth2" and p["current"]["status"] == "active"
    assert p["origin"]["source"] == {"doc": "adr-014"}
    assert p["origin"]["attested"] is False
    # the timeline carries BOTH values and names the policy that retired the old one
    assert [h["object"] for h in p["timeline"]] == ["api-keys", "oauth2"]
    assert p["superseded_count"] == 1
    assert p["timeline"][0]["policy"] == "keyed_lww"
    # integrity: written under receipts, unedited
    assert p["integrity"]["receipted"] is True
    assert p["integrity"]["content_matches_receipt"] is True
    assert p["integrity"]["attribution_matches_receipt"] is True
    assert p["integrity"]["chain_ok"] is True
    assert p["integrity"]["anchor"]["n_writes"] == 2
    assert p["trust"]["grade"] == "claimed"          # nothing corroborates it yet
    assert p["limits"], "the honest limits must always ride along with the answer"


def test_actor_and_lineage_ride_through_a_derived_write():
    m = _store()
    parent = m.remember("ticket says billing moved off api keys", source={"doc": "ticket-9"},
                        agent_id="kael", user_id="rasto")
    child = m.remember("summary: billing uses oauth2", key="billing::auth", object="oauth2",
                       derived=True, derived_from=[parent], agent_id="mira")

    par = m.provenance(id=parent)
    assert par["key"] is None and par["timeline"] == []          # unkeyed record: no supersession chain
    assert par["origin"]["actor"] == {"user_id": "rasto", "agent_id": "kael"}

    ch = m.provenance(id=child)
    assert ch["origin"]["derived"] is True
    assert ch["origin"]["ancestors"] == [parent]                 # a retraction of the parent reaches here
    assert "ticket9" in ch["origin"]["inherited_taint"]          # taint is entity-resolved, not raw
    assert ch["origin"]["actor"] == {"agent_id": "mira"}
    assert ch["key"] == "billing::auth"                          # an id lookup still reports the whole chain


def test_orphan_is_visible():
    m = _store()
    orphan = m.remember("summary with no resolvable parent", derived=True)
    p = m.provenance(id=orphan)
    assert p["origin"]["orphan"] is True
    assert p["origin"]["ancestors"] == []


def test_an_erased_ancestor_shows_as_lineage_we_can_no_longer_follow():
    """remember() drops parents it cannot resolve at write time, so a dangling link can only appear
    LATER — when an ancestor is erased. That is exactly when a caller must not read an empty
    `ancestors` as 'primary observation'."""
    m = _store()
    parent = m.remember("ticket says billing moved off api keys", source={"doc": "ticket-9"})
    child = m.remember("summary: billing uses oauth2", derived=True, derived_from=[parent])
    assert m.provenance(id=child)["origin"]["ancestors"] == [parent]

    m.forget(ids=[parent])
    q = m.provenance(id=child)
    assert q["origin"]["unresolved_parents"] == [parent]
    assert q["origin"]["ancestors"] == []
    assert q["origin"]["derived"] is True          # still declares derivation, so the gap is legible


def test_a_relabel_is_loud():
    """The reason provenance is worth calling: rewriting a record's source out of band no longer passes."""
    m = _store(receipts=True)
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr-014"})
    assert m.provenance(key="billing::auth")["integrity"]["attribution_matches_receipt"] is True

    rec = next(r for r in m.items if r.get("key") == "billing::auth")
    rec["source"] = {"doc": "forged-source"}                     # the silent attack
    p = m.provenance(key="billing::auth")
    assert p["integrity"]["attribution_matches_receipt"] is False
    assert p["integrity"]["chain_ok"] is True                    # the LOG is intact; the RECORD moved


def test_an_attacker_who_rewrites_the_sidecar_too_is_NOT_caught():
    """NEGATIVE CONTROL — the limit `provenance()` states about itself, demonstrated rather than asserted.

    The receipts sidecar sits next to the store and is UNSIGNED by default, and the attribution commitment
    is a hash of public inputs with no secret. So an attacker with enough file access to relabel a record
    can equally recompute the whole chain and pass every check. If this test ever started FAILING (i.e. we
    caught them), the honest limits in the docstring would be understating what we do.
    """
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    m = Inspeximus(path=path, receipts=True)
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr-014"})

    # the attacker relabels the source AND regenerates the receipt chain over the new state
    rec = next(r for r in m.items if r.get("key") == "billing::auth")
    rec["source"] = {"doc": "forged-source"}
    m._receipts = []
    m._emit_write_receipt(rec)
    m._save(force=True)

    p = Inspeximus(path=path, receipts=True).provenance(key="billing::auth")
    assert p["integrity"]["attribution_matches_receipt"] is True    # NOT caught — this is the point
    assert p["integrity"]["chain_ok"] is True
    assert any("sidecar" in lim for lim in p["limits"]), "the answer must carry this limit with it"


def test_receipts_off_says_so_instead_of_claiming_integrity():
    m = _store()
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2")
    p = m.provenance(key="billing::auth")
    assert p["integrity"]["receipted"] is False
    assert p["integrity"]["content_matches_receipt"] is None     # never asserted as OK
    assert p["integrity"]["chain_ok"] is None
    assert any("receipts are off" in lim for lim in p["limits"])


def test_missing_fact_and_argument_guard():
    m = _store()
    p = m.provenance(key="nothing::here")
    assert p["found"] is False and p["current"] is None and p["timeline"] == []

    for bad in ({}, {"key": "k", "id": "i"}):
        try:
            m.provenance(**bad)
            assert False, f"provenance({bad}) should have raised"
        except ValueError:
            pass


def test_cli_provenance_human_and_json():
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    env = dict(os.environ, INSPEXIMUS_PATH=path, PYTHONPATH=os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))

    def cli(*args):
        return subprocess.run([sys.executable, "-m", "inspeximus.cli", *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)

    cli("remember", "billing uses api keys", "--key", "billing::auth")
    cli("remember", "billing uses oauth2", "--key", "billing::auth")

    human = cli("provenance", "billing::auth")
    assert human.returncode == 0
    assert "billing::auth" in human.stdout and "retired by keyed_lww" in human.stdout
    assert human.stdout.isascii(), "CLI output must stay ASCII (non-UTF-8 consoles)"

    as_json = cli("--json", "provenance", "billing::auth")      # --json is a global flag
    assert as_json.returncode == 0
    assert json.loads(as_json.stdout)["superseded_count"] == 1

    assert cli("provenance", "no::such").returncode == 1          # missing fact is a non-zero exit
    assert cli("provenance").returncode == 2                      # neither key nor --id


def test_cli_reads_an_existing_receipt_chain():
    """The CLI opens stores WITHOUT receipts by default, so `provenance` has to force them on — else a
    receipted store is reported as "receipts off at write time", which is wrong, not just unhelpful."""
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    m = Inspeximus(path=path, receipts=True)
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr-014"})

    env = dict(os.environ, INSPEXIMUS_PATH=path, PYTHONPATH=os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    out = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--json", "provenance", "billing::auth"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    integrity = json.loads(out.stdout)["integrity"]
    assert integrity["receipted"] is True
    assert integrity["content_matches_receipt"] is True
    assert integrity["chain_ok"] is True


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_"):
            fn(); print("ok", name)
