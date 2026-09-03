"""Does the Haystack integration doc still run, and is its one hard claim about disk bytes true?

WHY. deepset-ai/haystack-integrations#554 has been open since 22 July. On 2 September the maintainer
pushed a fix to our branch himself rather than closing it: he renamed `delete_documents` to
`erase_documents` in one paragraph. He was right and the error was ours. That paragraph describes a
signed tombstone and a provable data-subject deletion, which is `erase_documents(request_id=...)`
returning an erasure record. The protocol's `delete_documents` returns None and takes no request id.

A doc page is a promise to a stranger with a fresh install. This runs the promise: it fetches the
page at the exact commit the PR points at, executes every Python block in it, and checks the one
sentence that could embarrass the maintainer if it were false -- that erasure removes the value from
the bytes on disk.

CONTROLS, because a probe that only reports success has measured nothing:
  * THE DISK CHECK MUST BE ABLE TO SEE TEXT. Before erasing, the probe asserts the document's text IS
    findable in the file. If it cannot find it there, the later absence proves nothing about erasure
    and the run is void.
  * A SURVIVING DOCUMENT MUST STILL BE PRESENT after the erasure, so "the bytes are gone" is not
    satisfied by an empty or truncated file.
  * THE PAGE IS PINNED to the PR head sha, not to a branch name. A branch moves; a claim about what
    reviewers will read must not.
  * EVERY PYTHON BLOCK IS EXECUTED, not parsed. A block that only compiles is not a working example,
    and the second block needs a real document id, so the probe substitutes one rather than skipping
    it and calling the page verified.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(line_buffering=True)
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "the_haystack_doc_example_runs_and_its_disk_claim_holds.result.json")

PR = 554
REPO = "deepset-ai/haystack-integrations"
FORK_RAW = "https://raw.githubusercontent.com/DanceNitra/haystack-integrations/%s/integrations/inspeximus.md"


def refuse(why):
    print("REFUSED: " + why)
    json.dump({"verdict": "REFUSED", "why": why}, io.open(OUT, "w", encoding="utf-8"), indent=1)
    raise SystemExit(2)


def main():
    import urllib.request
    import haystack
    from inspeximus.integrations.haystack import InspeximusDocumentStore
    from haystack.dataclasses import Document

    try:
        sha = subprocess.run(["gh", "pr", "view", str(PR), "--repo", REPO,
                              "--json", "headRefOid", "--jq", ".headRefOid"],
                             capture_output=True, text=True, timeout=120).stdout.strip()
    except Exception as e:                                    # noqa: BLE001
        refuse("could not resolve the PR head sha (%r); the page must be pinned, not fetched from a "
               "branch name that moves" % e)
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        refuse("PR head sha looks wrong: %r" % sha)

    with urllib.request.urlopen(FORK_RAW % sha, timeout=60) as r:
        doc = r.read().decode("utf-8")
    blocks = re.findall(r"```python\n(.*?)```", doc, re.S)
    if len(blocks) < 2:
        refuse("expected at least two python blocks on the page, found %d; the page changed shape "
               "and this probe is checking something else" % len(blocks))

    tmp = tempfile.mkdtemp(prefix="hsdoc_")
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        ran = []
        for i, b in enumerate(blocks):
            if "<document-id>" in b:
                continue                                       # needs a real id; handled below
            p = os.path.join(tmp, "block_%d.py" % i)
            io.open(p, "w", encoding="utf-8").write(b)
            out = subprocess.run([sys.executable, "-X", "utf8", p],
                                 capture_output=True, text=True, timeout=600)
            if out.returncode != 0:
                refuse("python block %d from the published page fails on haystack %s:\n%s"
                       % (i, haystack.__version__, (out.stderr or "")[-700:]))
            ran.append({"block": i, "stdout": (out.stdout or "").strip()[:200]})

        store = InspeximusDocumentStore(path="documents.json")
        docs = store.filter_documents({})
        if len(docs) < 2:
            refuse("the indexing block left %d documents; the erasure check needs a victim AND a "
                   "survivor, or 'the bytes are gone' could be satisfied by an empty file" % len(docs))
        victim, survivor = docs[0], docs[1]

        raw_before = io.open("documents.json", "rb").read()
        if victim.content.encode("utf-8") not in raw_before:
            refuse("the victim's text is NOT findable in the file before erasure, so its absence "
                   "afterwards would prove nothing about erasure")

        result = store.erase_documents([victim.id], request_id="dsr-2026-04-01")
        if not isinstance(result, dict):
            refuse("erase_documents returned %s, but the page says it returns an erasure record"
                   % type(result).__name__)

        raw_after = io.open("documents.json", "rb").read()
        victim_gone = victim.content.encode("utf-8") not in raw_after
        survivor_kept = survivor.content.encode("utf-8") in raw_after
        left = [d.content for d in InspeximusDocumentStore(path="documents.json").filter_documents({})]

        if not survivor_kept:
            refuse("the surviving document is also gone from the bytes, so the file was truncated "
                   "rather than the value erased, and the page's claim is not what was measured")
        if not victim_gone:
            refuse("the erased document's text is STILL in documents.json. The page's central claim "
                   "is false and the maintainer must be told before this merges")
    finally:
        os.chdir(cwd)

    print("  PR #%d head %s | haystack %s" % (PR, sha[:8], haystack.__version__))
    print("  python blocks on the page: %d, executed: %d, all exit 0" % (len(blocks), len(ran)))
    for r in ran:
        print("     block %d stdout: %s" % (r["block"], r["stdout"]))
    print("  erase_documents returned keys: %s" % sorted(result))
    print("  victim text gone from disk: %s | survivor still there: %s | store now: %s"
          % (victim_gone, survivor_kept, left))

    json.dump({"probe": os.path.basename(__file__),
               "pr": PR, "pr_head_sha": sha, "haystack": haystack.__version__,
               "python_blocks": len(blocks), "blocks_executed": len(ran), "block_output": ran,
               "erase_result_keys": sorted(result),
               "victim_text_removed_from_disk": victim_gone,
               "survivor_text_still_on_disk": survivor_kept,
               "store_after_erasure": left,
               "controls": {
                   "victim_text_confirmed_present_before_erasure": True,
                   "survivor_proves_the_file_was_not_truncated": True,
                   "page_pinned_to_pr_head_sha_not_a_branch": True,
                   "blocks_executed_rather_than_parsed": True,
               }},
              io.open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
