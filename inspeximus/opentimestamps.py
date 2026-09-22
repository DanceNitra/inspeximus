"""Read an OpenTimestamps proof and check it against a Bitcoin block header, with no dependencies.

WHY THIS EXISTS. Our anchor receipts are `.ots` files, and the reference way to check one is the
`ots` command, which pulls in python-bitcoinlib. On Windows that import reaches for libssl through
ctypes and crashes before it reads a byte of the proof. A customer's first contact with our anchor
must not be somebody else's packaging problem, so the check is here, in the standard library.

WHAT A PROOF IS. A chain of operations applied to a digest: append these bytes, prepend those,
hash, repeat. Follow it and you land on a value that is claimed to be the merkle root of a Bitcoin
block. The proof is true when that value IS the merkle root of the block at the named height, which
is the one fact this module cannot know on its own. You supply the block header, from your own node,
from an explorer, from anywhere: the point of an offline verifier is that you choose the source.

    from inspeximus.opentimestamps import verify
    verify(open("head.json", "rb").read(), open("head.json.ots", "rb").read(),
           block_header=bytes.fromhex(header_80_bytes))

WHAT IT PROVES AND WHAT IT DOES NOT. An anchored proof says these exact bytes existed before that
block was mined. It says nothing about whether anything in them is true, and it cannot see a history
shown to somebody else. A PENDING proof says even less: a calendar server has promised to include
the digest, and no block has been mined about it yet. Those are different verdicts here and they are
never collapsed into "ok".

FORMAT: opentimestamps.org, the .ots serialization. Implemented from the specification; the
attestation tags and operation bytes are named below rather than left as magic numbers.
"""
from __future__ import annotations

import hashlib

#: The file header every .ots proof starts with, then a version varint.
MAGIC = bytes.fromhex("004f70656e54696d657374616d7073000050726f6f6600bf89e2e884e89294")

#: Attestation tags, 8 bytes each.
BITCOIN_TAG = bytes.fromhex("0588960d73d71901")
PENDING_TAG = bytes.fromhex("83dfe30d2ef90c8e")
LITECOIN_TAG = bytes.fromhex("06869a0d73d71b45")
ETHEREUM_TAG = bytes.fromhex("30fe8087b5c7ead7")

ATTESTATION = 0x00
FORK = 0xFF

#: Binary operations: the byte and what it does with the argument that follows.
_BINARY = {0xF0: "append", 0xF1: "prepend"}
_UNARY = {
    0x02: ("sha1", lambda b: hashlib.sha1(b).digest()),
    0x03: ("ripemd160", lambda b: hashlib.new("ripemd160", b).digest()),
    0x08: ("sha256", lambda b: hashlib.sha256(b).digest()),
    0x67: ("keccak256", None),                                   # not implemented, and says so
    0xF2: ("reverse", lambda b: b[::-1]),
    0xF3: ("hexlify", lambda b: b.hex().encode("ascii")),
}


class Malformed(ValueError):
    """The proof could not be read. This is not a verdict about the log."""


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.i = data, 0

    def byte(self) -> int:
        if self.i >= len(self.data):
            raise Malformed("the proof ended early")
        self.i += 1
        return self.data[self.i - 1]

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.data):
            raise Malformed("the proof claims %d more bytes than it has" % n)
        self.i += n
        return self.data[self.i - n:self.i]

    def varuint(self) -> int:
        value, shift = 0, 0
        while True:
            b = self.byte()
            value |= (b & 0x7F) << shift
            if not b & 0x80:
                return value
            shift += 7
            if shift > 63:
                raise Malformed("a varint longer than 64 bits")

    def varbytes(self) -> bytes:
        return self.take(self.varuint())

    def done(self) -> bool:
        return self.i >= len(self.data)


def _attestation(r: _Reader) -> dict:
    tag = r.take(8)
    payload = r.varbytes()
    inner = _Reader(payload)
    if tag == BITCOIN_TAG:
        return {"kind": "bitcoin", "height": inner.varuint()}
    if tag == PENDING_TAG:
        return {"kind": "pending", "uri": inner.varbytes().decode("utf-8", "replace")}
    if tag == LITECOIN_TAG:
        return {"kind": "litecoin", "height": inner.varuint()}
    if tag == ETHEREUM_TAG:
        return {"kind": "ethereum", "payload": payload.hex()}
    return {"kind": "unknown", "tag": tag.hex(), "payload": payload.hex()}


def _walk(r: _Reader, digest: bytes, out: list, depth: int = 0) -> None:
    """Apply operations to `digest`, collecting every attestation reached, down every fork.

    A proof is a TREE, not a line. A fork means the same digest was submitted to more than one
    calendar, and each branch has to be followed: reading only the first branch is how a proof that
    IS anchored reads as pending.
    """
    if depth > 256:
        raise Malformed("the proof nests deeper than 256 forks")
    while True:
        if r.done():
            return
        op = r.byte()
        if op == ATTESTATION:
            out.append(dict(_attestation(r), digest=digest.hex()))
            return
        if op == FORK:
            _walk(r, digest, out, depth + 1)
            continue
        if op in _BINARY:
            arg = r.varbytes()
            digest = digest + arg if _BINARY[op] == "append" else arg + digest
            continue
        if op in _UNARY:
            name, fn = _UNARY[op]
            if fn is None:
                raise Malformed("this proof uses %s, which this verifier does not implement" % name)
            digest = fn(digest)
            continue
        raise Malformed("unknown operation byte 0x%02x at offset %d" % (op, r.i - 1))


def parse(ots: bytes) -> dict:
    """The file digest the proof is about, and every attestation it reaches."""
    if not ots.startswith(MAGIC):
        raise Malformed("not an OpenTimestamps proof: the file header does not match")
    r = _Reader(ots)
    r.i = len(MAGIC)
    version = r.varuint()
    op = r.byte()
    if op not in _UNARY:
        raise Malformed("the proof opens with operation 0x%02x, not a hash" % op)
    name, _fn = _UNARY[op]
    length = {"sha1": 20, "ripemd160": 20, "sha256": 32}.get(name)
    if length is None:
        raise Malformed("unsupported file digest algorithm %s" % name)
    digest = r.take(length)
    attestations: list = []
    _walk(r, digest, attestations)
    return {"version": version, "algorithm": name, "file_digest": digest.hex(),
            "attestations": attestations}


def attestations_from_upgrade(pending_digest: str, upgrade_body: bytes) -> list:
    """Walk a calendar's upgrade response, which is a bare timestamp starting at the commitment.

    The response carries no file header and no opening digest: it continues from the digest the
    pending attestation named. Feeding it the wrong digest produces a plausible-looking chain that
    lands on a merkle root belonging to nothing, so the caller passes the digest from the proof
    rather than one it computed itself.
    """
    out: list = []
    _walk(_Reader(upgrade_body), bytes.fromhex(pending_digest), out)
    return out


def upgrade(ots: bytes, fetch=None, timeout: float = 25.0) -> dict:
    """Ask each calendar named in a pending proof whether a block has been mined about it.

    This is the ONE function here that touches the network, it is opt-in, and it reads public
    endpoints only. Nothing about your data leaves the machine: a calendar is asked about a digest
    it already holds. Pass `fetch` to route it through your own client, or to test without a
    network.

    Returns the pending attestations with whatever each calendar answered, so a caller can see which
    calendar supplied the block rather than being handed an anonymous verdict.
    """
    if fetch is None:
        import ssl
        import urllib.request
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:                                      # noqa: BLE001
            ctx = ssl.create_default_context()

        def fetch(url):                                          # noqa: F811
            req = urllib.request.Request(
                url, headers={"Accept": "application/vnd.opentimestamps.v1",
                              "User-Agent": "inspeximus-ots/1"})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read()

    parsed = parse(ots)
    results = []
    for a in parsed["attestations"]:
        if a["kind"] != "pending":
            continue
        row = {"calendar": a["uri"], "commitment": a["digest"]}
        try:
            body = fetch(a["uri"].rstrip("/") + "/timestamp/" + a["digest"])
            row["attestations"] = attestations_from_upgrade(a["digest"], body)
            row["bitcoin"] = [x for x in row["attestations"] if x["kind"] == "bitcoin"]
        except Exception as exc:                                 # noqa: BLE001
            # A calendar that cannot be reached is not a verdict about the proof, and it must not
            # look like one. The other calendars are still asked.
            row["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
        results.append(row)
    return {"kind": "inspeximus.ots-upgrade/1", "file_digest": parsed["file_digest"],
            "calendars": results,
            "heights": sorted({b["height"] for r in results for b in r.get("bitcoin", [])})}


def merkle_root_of(block_header: bytes) -> str:
    """The merkle root inside an 80-byte Bitcoin block header, in the order explorers print it.

    Bytes 36 to 68 hold it little-endian. Reversing it is not cosmetic: compared the wrong way round
    every valid proof fails, and the failure looks exactly like tampering.
    """
    if len(block_header) != 80:
        raise Malformed("a Bitcoin block header is 80 bytes, this is %d" % len(block_header))
    return block_header[36:68][::-1].hex()


def block_hash_of(block_header: bytes) -> str:
    """Double SHA-256 of the header, reversed: the block hash as it is normally written."""
    if len(block_header) != 80:
        raise Malformed("a Bitcoin block header is 80 bytes, this is %d" % len(block_header))
    return hashlib.sha256(hashlib.sha256(block_header).digest()).digest()[::-1].hex()


def verify(data: bytes, ots: bytes, block_header: bytes | None = None,
           merkle_root: str | None = None, height: int | None = None,
           extra_attestations: list | None = None) -> dict:
    """Check a proof about `data`, offline.

    Give either `block_header` (80 raw bytes, from any source you trust) or `merkle_root` directly.
    Without one of them the verdict is INCOMPLETE: the proof can be read and its target computed,
    but nothing has been compared, and this function will not call that OK.

    Verdicts: ANCHORED, PENDING, INCOMPLETE, MISMATCH. MISMATCH is the one that matters, and it is
    what a single changed byte in `data` produces.
    """
    parsed = parse(ots)
    # A proof stamped minutes ago carries only calendar promises. `upgrade()` asks those calendars
    # what has been mined since, and its answers are merged here rather than rewritten into the .ots
    # file: the file a customer holds stays the file we stamped.
    parsed["attestations"] = list(parsed["attestations"]) + list(extra_attestations or [])
    actual = hashlib.new(parsed["algorithm"], data).hexdigest()
    out = {
        "kind": "inspeximus.ots-verification/1",
        "file_digest_in_proof": parsed["file_digest"],
        "file_digest_of_your_data": actual,
        "attestations": parsed["attestations"],
        "scope": ("An anchored proof says these exact bytes existed before that block was mined. It "
                  "says nothing about whether anything in them is true, and it cannot see a history "
                  "shown to somebody else."),
    }
    if actual != parsed["file_digest"]:
        out["verdict"] = "MISMATCH"
        out["why"] = ("this proof is about %s and your data hashes to %s, so the proof is about "
                      "different bytes" % (parsed["file_digest"][:16], actual[:16]))
        # NAME THE LINE ENDINGS WHEN THEY ARE THE CAUSE. Measured on our own published anchor: the
        # receipt was stamped in Linux CI over LF bytes, and the same file checked out on Windows has
        # CRLF, so an honest file reports MISMATCH and the first customer to try it concludes
        # tampering. The verdict does NOT soften, because the bytes really do differ; what changes is
        # that the reader is told which difference it is and where it came from.
        normalised = hashlib.new(parsed["algorithm"], data.replace(b"\r\n", b"\n")).hexdigest()
        if normalised == parsed["file_digest"]:
            out["line_endings"] = (
                "Your copy differs from the stamped bytes ONLY in line endings: with CRLF converted "
                "to LF it hashes to the digest in the proof. Something converted the file after it "
                "was stamped, usually git on checkout. Fetch the file as published, or mark it "
                "`-text` in .gitattributes, and check again. This is not evidence of tampering and "
                "it is not a pass either.")
        return out

    bitcoin = [a for a in parsed["attestations"] if a["kind"] == "bitcoin"]
    if height is not None:
        bitcoin = [a for a in bitcoin if a["height"] == height]
    if not bitcoin:
        pending = [a for a in parsed["attestations"] if a["kind"] == "pending"]
        out["verdict"] = "PENDING" if pending else "INCOMPLETE"
        out["why"] = (("a calendar has promised to include this digest and no block has been mined "
                       "about it yet: %s" % ", ".join(a["uri"] for a in pending)) if pending else
                      "the proof reaches no Bitcoin attestation this verifier can check")
        return out

    if block_header is None and merkle_root is None:
        out["verdict"] = "INCOMPLETE"
        out["height"] = bitcoin[0]["height"]
        out["expected_merkle_root"] = bitcoin[0]["digest"]
        out["why"] = ("the proof computes to %s and claims it is the merkle root of block %d. Supply "
                      "that block's header to finish the check."
                      % (bitcoin[0]["digest"], bitcoin[0]["height"]))
        return out

    want = merkle_root_of(block_header) if block_header is not None else merkle_root.lower()
    if block_header is not None:
        out["block_hash"] = block_hash_of(block_header)
    for a in bitcoin:
        # The proof's computed value is the merkle root in INTERNAL byte order; explorers print the
        # reverse. Both orders are compared, so a caller cannot fail the check by pasting the one
        # their source happened to give them.
        computed = a["digest"]
        flipped = bytes.fromhex(computed)[::-1].hex()
        if want in (computed, flipped):
            out["verdict"] = "ANCHORED"
            out["height"] = a["height"]
            out["merkle_root"] = want
            out["why"] = "these bytes were committed to the merkle root of block %d" % a["height"]
            return out
    out["verdict"] = "MISMATCH"
    out["height"] = bitcoin[0]["height"]
    out["expected_merkle_root"] = bitcoin[0]["digest"]
    out["why"] = ("the proof computes to %s, and the header you gave has merkle root %s"
                  % (bitcoin[0]["digest"], want))
    return out
