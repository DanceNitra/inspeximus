"""Did the bytes actually go? A vendor-neutral residue check for any memory stack.

You called `delete()`. The API returned success and the value stopped being served. That is not the same
as the value being gone from disk, and for anyone with an erasure obligation it is the only part that
matters. Nothing about this is specific to inspeximus: point it at any directory -- a vector store, a
sqlite history, a JSONL trace, someone else's library -- and it answers for THAT deployment.

It reports three outcomes, and keeping them apart is the whole point:

  LIVE        a SQLite table still holds the value in a row. The system retained it.
  UNRECLAIMED the value is in the file's bytes but in no live row. SQLite (and most embedded stores) do
              not zero a page on delete, so the record is gone logically and the bytes linger until a
              VACUUM or compaction. This is a property of the storage engine, not a vendor's choice --
              reporting it as retention is the kind of over-claim that gets a report dismissed.
  PLAIN       a non-SQLite file (JSON, JSONL, log, backup) contains the value.

Measured on mem0 2.0.11 with a local qdrant while building this: after `delete()` and `reset()` the value
was in NO live row anywhere, and remained only as unreclaimed bytes in the vector store's sqlite. That is
the honest reading, and it is why the distinction exists.

MATCHING SCOPE -- read this before treating `ok=True` as an all-clear. The search is a LITERAL,
CASE-SENSITIVE byte/substring match for each value exactly as you passed it. Measured 2026-07-30 by
planting one secret in eight encodings and scanning for the original:

    exact                     FOUND
    JSON-quoted               FOUND
    lowercased                MISSED
    uppercased                MISSED
    double space between words MISSED
    newline between words     MISSED
    base64                    MISSED
    hex                       MISSED

Elsewhere this library discloses only that "a paraphrase is NOT caught". That understates it: a change
of CASE is not a paraphrase, and neither is a whitespace difference or a transport encoding. Any store
that normalises case on write, re-wraps text, or keeps a base64/hex copy holds residue this returns
`ok=True` for. So a clean result means "this exact byte sequence is absent", never "the value is gone".

Two ways to use it honestly: pass every form you know the value can take (`values=[v, v.lower(),
v.upper(), ...]`), and treat the result as evidence rather than proof. Making the match itself
case/whitespace-insensitive would raise recall and is cheap, but it changes verdicts -- a scan that was
`ok=True` can become `ok=False` -- so it is a behaviour change and is proposed rather than made here.
tests/test_erasure_residue_matching_scope.py pins the table above so the gap stays visible and cannot
silently widen.

The values you are searching for are secrets by construction, so this never echoes one. Findings carry a
short SHA-256 fingerprint instead, which is enough to correlate and useless to leak.

    from inspeximus.erasure_residue import scan_residue
    report = scan_residue(root="./data", values=["alice@example.com"])
    report["ok"]        # False if anything was found
    report["findings"]  # [{path, kind, table, column, fingerprint}, ...]

    python -m inspeximus.erasure_residue --root ./data --value alice@example.com
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time

_SQLITE_MAGIC = b"SQLite format 3\x00"
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache", ".pytest_cache"}


def _fingerprint(value: str) -> str:
    """A correlatable, non-reversible stand-in. The caller already knows the value; a report should not."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def scan_records(records, values, max_pairs: int = 2_000_000) -> dict:
    """Do any SURVIVING records still carry a value that was just erased?

    `scan_residue` answers this for OTHER stores on disk; nothing answered it for THIS one. Measured: a
    record reading "summary: she lives at 5 Elm St" survived `forget_subject('hr/alice')` holding the
    erased address verbatim, and the erasure reported `erased: 1` with nothing else to say.

    It has to happen AT ERASURE TIME. Tombstones are content-free by design -- a hash of PII is still
    PII -- so the values are gone the instant the rows are, and a check bolted on afterwards has nothing
    to compare against. This is the only moment the comparison is possible at all.

    HEURISTIC, and it is labelled as one wherever it surfaces: a paraphrase carries the fact without the
    string, so a clean result is not proof of absence; and a short value ("5", "ok") matches everywhere,
    so values under 4 characters are skipped rather than reported as residue in every record.

    Returns {ok, checked_records, searched_values, findings, problems}. Findings carry the record id, the
    field, and a FINGERPRINT -- never the value. The caller already knows what they erased; a report they
    will paste into a ticket should not reintroduce it.
    """
    vals = [v for v in {str(v).strip() for v in (values or [])} if len(v) >= 4]
    recs = list(records or [])
    if not vals:
        return {"ok": False, "checked_records": len(recs), "searched_values": 0, "findings": [],
                "problems": ["no values were searchable (all empty or under 4 characters), so nothing "
                             "was compared -- an empty search is not a clean result"]}

    problems: list[str] = []
    # Bound the work, and SAY SO when bounded. Silent truncation would turn a partial scan into a clean
    # report, which is the defect this whole surface exists to avoid.
    budget = max_pairs
    findings: list[dict] = []
    checked = 0
    for r in recs:
        if budget <= 0:
            problems.append(f"stopped after {checked} record(s): the comparison budget was reached, so "
                            f"{len(recs) - checked} record(s) were NOT examined")
            break
        checked += 1
        for field in ("text", "object"):
            blob = r.get(field)
            if not isinstance(blob, str) or not blob:
                continue
            low = blob.lower()
            for v in vals:
                budget -= 1
                if v.lower() in low:
                    findings.append({"id": r.get("id"), "field": field,
                                     "fingerprint": _fingerprint(v)})
    if findings:
        problems.append("a surviving record still contains a value that was just erased; the row went "
                        "and the string did not, so the erasure is incomplete within this store")
    return {"ok": not findings, "checked_records": checked, "searched_values": len(vals),
            "findings": findings, "problems": problems,
            "method": "substring match on text/object -- a paraphrase is NOT caught, so a clean result "
                      "is evidence and not proof"}


def _is_sqlite(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def _live_rows(path: str, value: str) -> list[dict]:
    """Where a SQLite file still holds the value in an addressable row."""
    hits: list[dict] = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return hits
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for t in tables:
            try:
                cols = [c[1] for c in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
            except sqlite3.Error:
                continue
            for c in cols:
                try:
                    n = con.execute(
                        f'SELECT COUNT(*) FROM "{t}" WHERE CAST("{c}" AS TEXT) LIKE ?',
                        (f"%{value}%",)).fetchone()[0]
                except sqlite3.Error:
                    continue          # a column type that cannot be cast is not a hit
                if n:
                    hits.append({"table": t, "column": c, "rows": int(n)})
    finally:
        con.close()
    return hits


def _resolve_skip(skip_dirs) -> set:
    """REPLACE, not union. `None` means the default; any other value is the caller's whole answer.

    One function rather than the same line in two places: a caller who passes `skip_dirs=set()` is
    asking to look everywhere, and a rule about that which lives in two functions can come to mean two
    different things. It also keeps the mutation target unambiguous, since a spec entry that matches
    twice is silently SKIPPED and inflates the score by exactly the checks it drops.
    """
    return set(_SKIP_DIRS) if skip_dirs is None else set(skip_dirs)


def scan_residue(root: str, values, max_file_mb: float = 512.0,
                 skip_dirs=None, follow_symlinks: bool = False,
                 manifest: bool = False) -> dict:
    """Search `root` for values that should no longer exist anywhere.

    Returns {ok, checked_files, skipped, findings, problems, manifest}. `ok` is True only when nothing was found --
    including unreclaimed bytes, because "logically deleted but still on the disk you are handing to
    someone" is exactly the state an erasure obligation is about. The `kind` on each finding is what tells
    you whether to file a bug or run a VACUUM.

    `manifest=True` additionally records, for every file actually READ, its path relative to `root`, its
    SHA-256 and its size. That list is what turns a scan into evidence: a count of files is the scanner's
    word, whereas hashes let a third party re-walk the same directory and confirm both that the bytes are
    the ones that were searched and that nothing changed afterwards. It costs one hash per file over bytes
    already in memory, so it is off by default only because most callers want the verdict, not the proof.
    `residue_certificate()` requires it.
    """
    vals = [v for v in (values or []) if isinstance(v, str) and v]
    if not vals:
        return {"ok": False, "checked_files": 0, "skipped": [], "findings": [], "manifest": [],
                "problems": ["no values were given, so nothing was searched for -- an empty search is "
                             "not a clean result"]}

    # REPLACE, not union. The default prunes .git/.venv/node_modules because they are usually noise, but a
    # caller who passes skip_dirs=set() is asking to look everywhere and used to be overruled silently --
    # there was no way to scan the one directory where a deleted store most reliably survives forever.
    skip = _resolve_skip(skip_dirs)

    # A path that is not there searches nothing and used to answer "clean": ok=True, 0 files, no problems --
    # byte-identical to a real all-clear. That is how a typo in a DSAR runbook becomes a clean bill of
    # health, and it is the same defect the erasure certificate had when its absence proof pointed at a
    # path that did not exist. Fail closed and name the cause.
    if not os.path.isdir(root):
        return {"ok": False, "checked_files": 0, "skipped": [], "findings": [], "manifest": [],
                "problems": [f"{root!r} is not a directory, so nothing was searched -- an unsearched "
                             f"location is not a clean one"]}
    findings: list[dict] = []
    problems: list[str] = []
    skipped: list[dict] = []
    files: list[dict] = []
    checked = 0
    limit = int(max_file_mb * 1024 * 1024)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        pruned = [d for d in dirnames if d in skip]
        dirnames[:] = [d for d in dirnames if d not in skip]
        for d in pruned:
            # NOT LOOKED AT is not CLEAN. This subtree was dropped without a word, so a secret sitting in
            # .git/objects -- where a deleted store survives longest -- produced "RESULT: clean" and exit 0.
            # The file already applies this rule to a file that was too large; a directory is the same claim
            # at larger scale. `skipped` forces ok=False, which is the honest verdict for "I did not check".
            skipped.append({"path": os.path.relpath(os.path.join(dirpath, d), root),
                            "why": "directory not searched (in skip_dirs); pass skip_dirs=set() to "
                                   "include it"})
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            try:
                size = os.path.getsize(path)
            except OSError as e:
                skipped.append({"path": rel, "why": f"{type(e).__name__}"})
                continue
            if size > limit:
                # Silently skipping a big file would make a store look clean because it was too large to
                # look at, so this is reported rather than dropped.
                skipped.append({"path": rel, "why": f"larger than max_file_mb ({size / 1e6:.0f} MB)"})
                continue
            try:
                with open(path, "rb") as fh:
                    blob = fh.read()
            except OSError as e:
                skipped.append({"path": rel, "why": f"{type(e).__name__}"})
                continue
            checked += 1
            if manifest:
                files.append({"path": rel.replace(os.sep, "/"), "sha256": hashlib.sha256(blob).hexdigest(),
                              "bytes": len(blob)})

            present = [v for v in vals if v.encode("utf-8") in blob]
            if not present:
                continue
            sqlite_file = _is_sqlite(path)
            for v in present:
                live = _live_rows(path, v) if sqlite_file else []
                if live:
                    for hit in live:
                        findings.append({"path": rel, "kind": "LIVE", "fingerprint": _fingerprint(v),
                                         "table": hit["table"], "column": hit["column"],
                                         "rows": hit["rows"]})
                elif sqlite_file:
                    findings.append({"path": rel, "kind": "UNRECLAIMED", "fingerprint": _fingerprint(v),
                                     "note": "no live row holds it; the page has not been reclaimed. "
                                             "VACUUM (sqlite) or compact the store, or use encryption "
                                             "plus key destruction if the disk leaves your control."})
                else:
                    findings.append({"path": rel, "kind": "PLAIN", "fingerprint": _fingerprint(v),
                                     "note": "a non-SQLite file still contains it (JSON, log, trace or "
                                             "backup). Nothing will reclaim this on its own."})

    if any(f["kind"] == "LIVE" for f in findings):
        problems.append("the value is still held in a LIVE row: the system retained it, and this is a "
                        "retention question for whoever wrote that store")
    if any(f["kind"] == "UNRECLAIMED" for f in findings):
        problems.append("the value survives only as UNRECLAIMED bytes: logically deleted, physically "
                        "present. Common to embedded stores and NOT a vendor defect -- but still on the "
                        "disk you would be handing over")
    if any(f["kind"] == "PLAIN" for f in findings):
        problems.append("a plain file still contains the value; nothing reclaims this automatically")
    if skipped:
        problems.append(f"{len(skipped)} file(s) could not be read or were skipped; a store is not clean "
                        f"because part of it was not looked at")

    if checked == 0 and not skipped:
        # Reported, not failed. A root that does not exist is a typo and fails closed above; a root that
        # EXISTS and holds nothing is a real location an operator pointed at, and calling that unclean
        # would be a false alarm on the ordinary case -- and a check that cries wolf gets switched off.
        problems.append("no files were searched under this root: it exists but is empty. That is a clean "
                        "result about nothing; confirm the path is the one you meant")

    return {"ok": not findings and not skipped, "checked_files": checked,
            "skipped": skipped, "findings": findings, "problems": problems,
            "manifest": sorted(files, key=lambda f: f["path"])}


try:                                  # OPTIONAL: only needed to SIGN or verify a residue certificate.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey as _Ed25519SK, Ed25519PublicKey as _Ed25519PK)
    _HAVE_ED = True
except Exception:
    _HAVE_ED = False

RESIDUE_CERT_SCOPE = (
    "This certificate attests WHAT THE SCANNER READ at issue time, and nothing else. It states that at "
    "`issued_iso`, the files listed in `manifest` had the SHA-256 hashes given there, and that a literal, "
    "case-sensitive byte search of those files for the values in `values_searched` produced `findings`. "
    "It does NOT certify that the store is complete: a file listed in `skipped` was never read, a file "
    "created after the scan is invisible to it, and re-verification against a live directory reports both. "
    "It does NOT certify that the values never existed, that they are unrecoverable from backups or from a "
    "filesystem's free space, or that an encoded form is absent -- the match is literal, so a lowercased or "
    "re-spaced copy is MISSED by design (see the module docstring). The signature identifies the scanner to "
    "a party who does not hold its key; it does not make the finding true. The finding is checkable by "
    "re-running the scan against the hashes in `manifest`, which is what makes this evidence rather than "
    "an assertion.")


def _canonical(doc) -> bytes:
    """The exact bytes that get signed. Sorted keys, no whitespace, ASCII-escaped, so that a document
    round-tripped through any conforming JSON implementation re-serialises identically."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def residue_certificate(root: str, values, signing_key: str | None = None, *,
                        root_label: str | None = None, max_file_mb: float = 512.0,
                        skip_dirs=None, follow_symlinks: bool = False) -> dict:
    """Scan a store you DO NOT OWN and package the result as an independently verifiable document.

    This is the auditor's form of `scan_residue`. An auditor is engaged to state a fact about someone
    else's system, and a Python dict is his word for it. This returns a self-contained JSON document that
    his client can hand to a regulator, and that the regulator checks with the module-level
    `verify_residue_certificate(cert, root=...)` WITHOUT the scanner's private key and WITHOUT trusting
    the scanner.

    It differs from `Inspeximus.erasure_certificate()` in what it can honestly claim, and the difference
    is the reason both exist. That one is issued by the OPERATOR about the ACT of erasure, and its
    evidence is a signed tombstone chain it owns. This one is issued by an OUTSIDE party about the
    CONTENT of a directory at one moment, and its evidence is the bytes it read. A scanner has no chain
    and cannot get one, so the certificate commits to file hashes instead: that is what a third party
    re-walks to confirm both that the search covered the bytes it claims and that they have not changed.

    `signing_key` is an Ed25519 secret as hex (see `new_receipt_keypair()`). Without one the document is
    still returned and still re-verifiable by re-walking, but it carries no signature and
    `verify_residue_certificate` reports `signed: False`. An unsigned certificate names no one, so
    anybody could have produced it.

    Every parameter that changes what gets read is recorded in `scan_parameters`, because a verifier who
    cannot reproduce the walk cannot check the result.
    """
    if signing_key is not None and not _HAVE_ED:
        raise RuntimeError("signing a residue certificate needs the `cryptography` package "
                           "(pip install cryptography). Omit signing_key for an unsigned document.")
    skip = _resolve_skip(skip_dirs)
    scan = scan_residue(root, values, max_file_mb=max_file_mb, skip_dirs=skip,
                        follow_symlinks=follow_symlinks, manifest=True)
    vals = [v for v in (values or []) if isinstance(v, str) and v]
    core = {
        "inspeximus_residue_certificate": "1.0",
        "issued_ts": time.time(),
        "issued_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # The label is what a report can print. The absolute path is deliberately NOT signed into the
        # core: the same directory legitimately sits at a different path on the verifier's machine, and
        # pinning it would fail an honest re-verification.
        "root_label": root_label or os.path.basename(os.path.abspath(root)) or root,
        "scan_parameters": {"max_file_mb": max_file_mb, "skip_dirs": sorted(skip),
                            "follow_symlinks": bool(follow_symlinks),
                            "match": "literal, case-sensitive, byte substring"},
        "values_searched": [{"fingerprint": _fingerprint(v)} for v in vals],
        "values_count": len(vals),
        "ok": scan["ok"],
        "checked_files": scan["checked_files"],
        "findings": scan["findings"],
        "skipped": scan["skipped"],
        "problems": scan["problems"],
        "manifest": scan["manifest"],
        "manifest_sha256": hashlib.sha256(_canonical(scan["manifest"])).hexdigest(),
        "scope": RESIDUE_CERT_SCOPE,
    }
    cert = dict(core)
    if signing_key is not None:
        sk = _Ed25519SK.from_private_bytes(bytes.fromhex(signing_key))
        cert["pubkey"] = sk.public_key().public_bytes_raw().hex()
        # Sign the core WITH the pubkey inside it, so that a signature cannot be re-attributed to another
        # key by editing the field it is checked against.
        cert["signature"] = sk.sign(_canonical(dict(core, pubkey=cert["pubkey"]))).hex()
    cert["verify_with"] = ("inspeximus.verify_residue_certificate(cert, root=<dir>)  "
                           "# root is optional; without it the bytes are not re-checked")
    return cert


def verify_residue_certificate(cert: dict, root: str | None = None,
                               expected_pubkey: str | None = None) -> dict:
    """Check a residue certificate WITHOUT the scanner's key and WITHOUT trusting the scanner.

    Four checks, and the last is the one that carries the weight:

      1. The document is structurally a residue certificate of a version this code knows.
      2. `manifest_sha256` commits to the manifest that is actually present.
      3. The Ed25519 signature verifies against the pubkey in the document, pinned to `expected_pubkey`
         when you supply one. Verifying against a key the document itself names proves only internal
         consistency, so pin the key you independently expect, or the signature answers a question
         nobody asked.
      4. Given `root`, every file in the manifest is re-hashed on disk. Reports `unchanged`, `changed`,
         `missing`, and `added`, where added names files that exist now and that the scan never read. A
         certificate whose bytes no longer match is not invalid, it is STALE. The two are reported
         separately because merging them either hides a real change or condemns an honest record.

    Returns {valid, signed, checks, problems, bytes}. `valid` is a statement about the document. It is
    never a verdict on the store: read `cert["ok"]` for that, and `cert["scope"]` for what it is worth.
    """
    problems: list = []
    checks: dict = {}
    if not isinstance(cert, dict) or "inspeximus_residue_certificate" not in cert:
        return {"valid": False, "signed": False, "checks": {},
                "problems": ["not an inspeximus residue certificate"], "bytes": None}
    ver = cert.get("inspeximus_residue_certificate")
    checks["version_known"] = ver == "1.0"
    if not checks["version_known"]:
        problems.append("certificate version " + repr(ver) + " is not one this verifier knows")

    manifest = cert.get("manifest")
    if not isinstance(manifest, list):
        problems.append("certificate carries no manifest, so nothing commits to the bytes searched")
        checks["manifest_committed"] = False
    else:
        recomputed = hashlib.sha256(_canonical(manifest)).hexdigest()
        checks["manifest_committed"] = recomputed == cert.get("manifest_sha256")
        if not checks["manifest_committed"]:
            problems.append("manifest_sha256 does not commit to the manifest in this document")

    signed = bool(cert.get("signature"))
    checks["signed"] = signed
    if not signed:
        problems.append("certificate is unsigned, so it names no scanner; anybody could have produced it")
    else:
        pub = cert.get("pubkey") or ""
        if expected_pubkey and pub != expected_pubkey:
            checks["pubkey_pinned"] = False
            problems.append("certificate pubkey does not match the expected one")
        elif expected_pubkey:
            checks["pubkey_pinned"] = True
        if not _HAVE_ED:
            checks["signature_valid"] = None
            problems.append("cannot check the signature without the `cryptography` package")
        else:
            core = {k: v for k, v in cert.items() if k not in ("signature", "verify_with")}
            try:
                _Ed25519PK.from_public_bytes(bytes.fromhex(pub)).verify(
                    bytes.fromhex(cert["signature"]), _canonical(core))
                checks["signature_valid"] = True
            except Exception as e:
                checks["signature_valid"] = False
                problems.append("signature does not verify: " + type(e).__name__)

    byte_report = None
    if root is not None and isinstance(manifest, list):
        byte_report = _recheck_bytes(cert, root)
        checks["bytes_unchanged"] = not (byte_report["changed"] or byte_report["missing"])
        if not checks["bytes_unchanged"]:
            problems.append(
                str(len(byte_report["changed"])) + " file(s) changed and " +
                str(len(byte_report["missing"])) + " missing since the scan, so this certificate is "
                "STALE for the current directory. That is a fact about the store rather than a defect "
                "in the document.")
        if byte_report["added"]:
            problems.append(
                str(len(byte_report["added"])) + " file(s) exist now that the scan never read, so the "
                "certificate says nothing about them")

    valid = (checks.get("version_known") and checks.get("manifest_committed")
             and checks.get("signature_valid") is not False
             and checks.get("pubkey_pinned") is not False)
    return {"valid": bool(valid), "signed": signed, "checks": checks,
            "problems": problems, "bytes": byte_report}


def _recheck_bytes(cert: dict, root: str) -> dict:
    """Re-walk `root` and compare it against the certificate's manifest.

    The walk uses the certificate's OWN recorded parameters rather than this function's defaults. A
    verifier who skips a different set of directories is answering a different question, and would
    report every file in them as added.
    """
    params = cert.get("scan_parameters") or {}
    skip = set(params.get("skip_dirs") or [])
    limit = float(params.get("max_file_mb") or 512.0) * 1024 * 1024
    follow = bool(params.get("follow_symlinks"))
    recorded = {f["path"]: f for f in cert.get("manifest") or [] if isinstance(f, dict) and "path" in f}
    seen = set()
    unchanged = []
    changed = []
    added = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                if os.path.getsize(path) > limit:
                    continue
                with open(path, "rb") as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                continue
            if rel not in recorded:
                added.append(rel)
                continue
            seen.add(rel)
            (unchanged if recorded[rel].get("sha256") == digest else changed).append(rel)
    missing = sorted(set(recorded) - seen)
    return {"unchanged": sorted(unchanged), "changed": sorted(changed),
            "missing": missing, "added": sorted(added), "manifest_files": len(recorded)}


def certificate_drift(old: dict, new: dict) -> dict:
    """What changed in a store between two residue certificates.

    This is the question a repeat engagement actually asks. A single certificate says whether a store was
    clean at one moment; two say whether it is getting better or worse, and which files moved. Both
    documents carry a per-file SHA-256, so the comparison is exact rather than inferred from counts.

    Returns {clean_to_dirty, dirty_to_clean, changed, added, removed, findings_delta, days,
    comparable, problems}. `clean_to_dirty` is the alarm: a store that passed and now does not.

    `comparable` is False when the two scans cannot honestly be differenced, and the reasons are in
    `problems`. Two scans of different roots, or with different skip lists, produce file sets that differ
    because the WALK differed rather than because the store did, and reporting that as drift would invent
    a change out of a parameter. Ordering is by issue time, so passing the pair the wrong way round is
    corrected rather than inverted.
    """
    problems: list = []
    for name, doc in (("old", old), ("new", new)):
        if not isinstance(doc, dict) or "inspeximus_residue_certificate" not in doc:
            return {"comparable": False, "problems": [name + " is not a residue certificate"],
                    "clean_to_dirty": None, "dirty_to_clean": None, "changed": [], "added": [],
                    "removed": [], "findings_delta": None, "days": None}
    if (old.get("issued_ts") or 0) > (new.get("issued_ts") or 0):
        old, new = new, old                       # order by issue time, not by argument position
    if old.get("root_label") != new.get("root_label"):
        problems.append("the two certificates are about different stores (" +
                        repr(old.get("root_label")) + " and " + repr(new.get("root_label")) +
                        "), so a file-level difference between them is not drift")
    op = (old.get("scan_parameters") or {})
    npar = (new.get("scan_parameters") or {})
    if op.get("skip_dirs") != npar.get("skip_dirs") or op.get("max_file_mb") != npar.get("max_file_mb"):
        problems.append("the two scans used different parameters, so files can appear or vanish because "
                        "the walk changed rather than because the store did")
    o = {f["path"]: f.get("sha256") for f in old.get("manifest") or [] if isinstance(f, dict)}
    n = {f["path"]: f.get("sha256") for f in new.get("manifest") or [] if isinstance(f, dict)}
    both = set(o) & set(n)
    days = None
    if old.get("issued_ts") and new.get("issued_ts"):
        days = round((new["issued_ts"] - old["issued_ts"]) / 86400.0, 2)
    return {
        "comparable": not problems,
        "problems": problems,
        "clean_to_dirty": bool(old.get("ok")) and not bool(new.get("ok")),
        "dirty_to_clean": (not bool(old.get("ok"))) and bool(new.get("ok")),
        "changed": sorted(k for k in both if o[k] != n[k]),
        "added": sorted(set(n) - set(o)),
        "removed": sorted(set(o) - set(n)),
        "findings_delta": len(new.get("findings") or []) - len(old.get("findings") or []),
        "days": days,
    }


def certificate_summary(cert: dict) -> dict:
    """The content-free summary of a certificate, small enough to keep in a memory store.

    The full document carries one line per file, so a large store makes it far too big to remember. This
    keeps what an auditor needs to answer "what did we certify, and when", plus `manifest_sha256` and
    `signature`, which together identify WHICH file on disk is the certificate this record refers to. The
    evidence stays in the file; the store remembers the claim.
    """
    return {"root_label": cert.get("root_label"), "issued_iso": cert.get("issued_iso"),
            "issued_ts": cert.get("issued_ts"), "ok": cert.get("ok"),
            "findings": len(cert.get("findings") or []), "checked_files": cert.get("checked_files"),
            "skipped": len(cert.get("skipped") or []), "values_count": cert.get("values_count"),
            "manifest_files": len(cert.get("manifest") or []),
            "manifest_sha256": cert.get("manifest_sha256"), "pubkey": cert.get("pubkey"),
            "signature": cert.get("signature")}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m inspeximus.erasure_residue",
        description="Check whether values you erased are still on disk. Works on any store, not just "
                    "inspeximus. Values are never echoed; findings carry a fingerprint.")
    ap.add_argument("--root", required=True, help="directory to search")
    ap.add_argument("--value", action="append", default=[],
                    help="a value that should be gone (repeatable)")
    ap.add_argument("--value-file", help="file with one value per line")
    ap.add_argument("--max-file-mb", type=float, default=512.0)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)

    values = list(a.value)
    if a.value_file:
        with open(a.value_file, encoding="utf-8") as fh:
            values += [ln.strip() for ln in fh if ln.strip()]

    rep = scan_residue(a.root, values, max_file_mb=a.max_file_mb)
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"checked {rep['checked_files']} file(s) under {a.root}")
        for f in rep["findings"]:
            where = f" [{f.get('table')}.{f.get('column')} x{f.get('rows')}]" if f["kind"] == "LIVE" else ""
            print(f"  {f['kind']:12s} {f['path']}{where}   fp={f['fingerprint']}")
        for p in rep["problems"]:
            print(f"  ! {p}")
        print("RESULT:", "clean — no residue found" if rep["ok"] else "residue found (see above)")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
