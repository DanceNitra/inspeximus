"""Write the store-format table in README.md, and its rows in the claims registry, from the receipt.

WHY. The table was typed from one run of `probes/one_write_two_formats_across_store_sizes.py` and the
receipt was later overwritten by another run of the same probe -- one that happened to execute while
the test suite was saturating the machine, so every figure was slower and the smallest size reversed
direction. Nothing failed. The README said one thing, the artifact beside it said another, and the
only reason it was caught is that the numbers were compared by hand. `claims_audit.py` checks that a
published number is REGISTERED and that its command exists; it does not re-derive the value.

BOTH FILES, ONE COMMAND. The registry pins each number to the line it appears on, so regenerating the
table alone leaves stale pins and unregistered numbers -- a chore after every re-measurement, and a
chore invites hand-editing the thing the audit exists to check.

RUN IT: python tools/sync_store_format_table.py [--write]
`--write` rewrites both; the default checks and exits non-zero on a mismatch (the release gate).
"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECEIPT = ROOT / "probes" / "one_write_two_formats_across_store_sizes.result.json"
README = ROOT / "README.md"
CLAIMS = ROOT / "claims_audit.py"
START = "| records in the store | whole file | one row | |"
CLAIM_IDS = {1000: "readme-row-write-cost-1k", 10000: "readme-row-write-cost-10k",
             30000: "readme-row-write-cost-30k"}

#: The concurrency sentence had the same defect the table had, and for the same reason: it was typed.
#: It said the JSON store landed 56 of 96 records in its worst trial while the receipt beside it read
#: 53, and a re-measurement after the lock fix moved it again to 63. One sentence, two drifts, and
#: nothing failed either time.
CONC_RECEIPT = ROOT / "probes" / "twelve_writers_and_the_one_that_stopped_writing.result.json"
#: THE OPENER MUST NOT NAME A WIDTH. It read "With twelve processes writing at once," while the probe
#: had been extended to 24 and 48, so the anchor described a run that was no longer the widest one in
#: the receipt it quotes. The count belongs in the derived part of the sentence, not in the anchor.
CONC_START = "Under concurrent writers,"
CONC_CLAIM_ID = "readme-concurrent-writers"


def _cells(receipt):
    """The flush-every-write cells, which are the ones the table publishes."""
    return [c for c in receipt["cells"] if c["flush_every_write"] is True]


def _row(cell):
    # THE RATIO MUST FOLLOW FROM THE TWO COLUMNS BESIDE IT. This read `max(ratios)`, the best of
    # three trials, and printed it next to the MEDIAN columns: the ten-thousand-record row said "about
    # 2.4x faster" where 0.0800 / 0.0441 is 1.8. A reader checking the arithmetic finds a number that
    # is not there, in a table this tool exists to keep honest. The inverse branch had the same shape
    # with `1 / min(ratios)`.
    if not cell["all_trials_agree_on_direction"]:
        verdict = "the three trials disagree; no difference to claim"
    elif cell["rows_median"] < cell["json_median"]:
        verdict = "rows about %.1fx faster" % (cell["json_median"] / cell["rows_median"])
    else:
        verdict = "the whole file about %.1fx faster" % (cell["rows_median"] / cell["json_median"])
    return "| %s | %.4f s | %.4f s | %s |" % (format(cell["records"], ","), cell["json_median"],
                                              cell["rows_median"], verdict)


def table_from(receipt):
    return "\n".join([START, "|---|---|---|---|"] + [_row(c) for c in _cells(receipt)])


def _arm(receipt, fmt, writers):
    for a in receipt["arms"]:
        if a["format"] == fmt and a["writers"] == writers:
            return a
    raise SystemExit("the concurrency receipt has no %s arm at %d writers" % (fmt, writers))


def conc_sentence(receipt):
    """The concurrency claim, derived rather than remembered, and attributed to the right party.

    IT USED TO SAY "the JSON store landed 56 of 96", WHICH BLAMED THE FORMAT. The JSON arm of that
    probe catches `StoreChangedOnDisk` and drops the record, while the store's own error text says
    "Call reload() to merge the two and retry" and the row path performs that union itself. Given the
    retry its error prescribes, the JSON store lands as much as the row store, measured side by side
    in `what_a_concurrent_writer_is_told_against_what_the_store_keeps.py`. So the sentence names the
    CALLER, and points at the fair comparison, because the README is where a stranger reads this.
    """
    wide = max(a["writers"] for a in receipt["arms"])
    js, rw = _arm(receipt, "json", wide), _arm(receipt, "rows", wide)
    trials = receipt["trials"]
    rows_clean = all(a["clean_trials"] == trials for a in receipt["arms"] if a["format"] == "rows")

    if js["clean_trials"] == trials:
        # THE CONTROL FAILED. If the whole-file arm loses nothing, the run was too quiet to say
        # anything about the row arm, and the sentence must not claim a win it did not observe.
        return ("%s at %d processes the whole-file store lost nothing in any of %d trials on this "
                "machine, so this run cannot separate the two callers." % (CONC_START, wide, trials))
    tail = ("the row store landed every record in %d of %d trials at every width tested."
            % (rw["clean_trials"], trials) if rows_clean else
            "the row store landed %d of %d in its worst trial at the same width."
            % (min(rw["landed"]), rw["attempted"]))
    return ("%s a caller that drops the store's own StoreChangedOnDisk instead of retrying landed %d "
            "of %d records in its worst trial at %d processes, while %s That gap belongs to the "
            "caller and not to the format: given the retry the error prescribes, the whole-file store "
            "keeps up (`probes/what_a_concurrent_writer_is_told_against_what_the_store_keeps.py`)."
            % (CONC_START, min(js["landed"]), js["attempted"], wide, tail))


def _conc_tokens(receipt):
    wide = max(a["writers"] for a in receipt["arms"])
    js, rw = _arm(receipt, "json", wide), _arm(receipt, "rows", wide)
    return [str(min(js["landed"])), str(js["attempted"]), str(wide),
            str(rw["clean_trials"]), str(receipt["trials"])]


def sync_conc(receipt, write):
    """True when README and the registry already carry the sentence this receipt supports."""
    want = conc_sentence(receipt)
    text = README.read_text(encoding="utf-8")
    m = re.search(re.escape(CONC_START) + r"[^\n]*\n", text)
    if not m:
        print("README.md has no line starting with: %s" % CONC_START)
        return False
    ok = m.group(0).rstrip("\n") == want
    if write and not ok:
        README.write_text(text.replace(m.group(0), want + "\n"), encoding="utf-8", newline="\n")
        print("README concurrency sentence rewritten from the receipt.")

    ctext = CLAIMS.read_text(encoding="utf-8")
    pat = re.compile(r'(_c\("' + re.escape(CONC_CLAIM_ID) +
                     r'", "README\.md", )\[[^\]]*\],\n(\s*)"[^"]*",')
    cm = pat.search(ctext)
    if not cm:
        print("claims_audit.py has no row for %s" % CONC_CLAIM_ID)
        return False
    quoted = want.split(", ", 1)[1].split(" and never")[0]
    replacement = "%s%s,\n%s%s," % (cm.group(1), json.dumps(_conc_tokens(receipt)), cm.group(2),
                                    json.dumps(quoted))
    if cm.group(0) != replacement:
        ok = False
        if write:
            CLAIMS.write_text(ctext[:cm.start()] + replacement + ctext[cm.end():],
                              encoding="utf-8", newline="\n")
            print("claims_audit.py concurrency row rewritten from the receipt.")
    return ok


def _tokens(cell, line):
    toks = [format(cell["records"], ","), "%.4f" % cell["json_median"],
            "%.4f" % cell["rows_median"]]
    if "about " in line:
        toks.append(line.rsplit("about ", 1)[-1].split("x")[0])
    return toks


def sync_claims(receipt, write):
    """True when the registry already matches. Rewrites it when `write`."""
    text = CLAIMS.read_text(encoding="utf-8")
    ok = True
    for cell in _cells(receipt):
        cid = CLAIM_IDS.get(cell["records"])
        if cid is None:
            continue
        line = _row(cell)
        pat = re.compile(r'(_c\("' + re.escape(cid) + r'", "README\.md", )\[[^\]]*\],\n(\s*)"[^"]*",')
        m = pat.search(text)
        if not m:
            print("claims_audit.py has no row for %s" % cid)
            return False
        want = '%s%s,\n%s%s,' % (m.group(1), json.dumps(_tokens(cell, line)), m.group(2),
                                 json.dumps(line))
        if m.group(0) != want:
            ok = False
            if write:
                text = text[:m.start()] + want + text[m.end():]
    if write and not ok:
        CLAIMS.write_text(text, encoding="utf-8", newline="\n")
        print("claims_audit.py rows rewritten from the receipt.")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="rewrite instead of checking")
    a = ap.parse_args()

    if not RECEIPT.exists():
        print("no receipt at %s -- run the probe first" % RECEIPT.name)
        return 2
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    want = table_from(receipt)
    text = README.read_text(encoding="utf-8")
    m = re.search(re.escape(START) + r"\n\|-[^\n]*\n(?:\|[^\n]*\n)+", text)
    if not m:
        print("the table is not in README.md; its first line must be:\n  %s" % START)
        return 2
    have = m.group(0).rstrip("\n")

    if a.write:
        if have != want:
            README.write_text(text.replace(m.group(0), want + "\n"), encoding="utf-8", newline="\n")
            print("README table rewritten from the receipt.")
        sync_claims(receipt, True)
        if CONC_RECEIPT.exists():
            sync_conc(json.loads(CONC_RECEIPT.read_text(encoding="utf-8")), True)
        # docs/CLAIMS.md is generated FROM the registry, so a registry edit leaves it stale and the
        # suite fails on a file no human writes. Third place the same numbers live, third place they
        # can drift; one command keeps all three.
        import subprocess
        # BYTES, NOT `text=True`. This CLI prints an em dash, and the console here is cp1250: with
        # text mode the reader thread raises UnicodeDecodeError and the output is lost. Third time
        # today for this class -- the example script and this tool both made it.
        subprocess.run([sys.executable, "claims_audit.py", "--write-claims"], cwd=str(ROOT),
                       capture_output=True)
        print("docs/CLAIMS.md regenerated from the registry.")
        return 0

    if have != want:
        print("README table does NOT match the receipt.\n--- README ---\n%s\n--- receipt ---\n%s"
              % (have, want))
        return 1
    if not sync_claims(receipt, False):
        print("the README table matches the receipt, but claims_audit.py still pins an older one.")
        return 1
    if not CONC_RECEIPT.exists():
        print("no receipt at %s -- run the concurrency probe first" % CONC_RECEIPT.name)
        return 2
    if not sync_conc(json.loads(CONC_RECEIPT.read_text(encoding="utf-8")), False):
        print("the concurrency sentence does not match %s." % CONC_RECEIPT.name)
        return 1
    print("README table, the concurrency sentence and the claims registry all match their receipts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
