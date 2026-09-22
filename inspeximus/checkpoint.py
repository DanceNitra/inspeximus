"""C2SP signed notes and transparency-log checkpoints, so a stranger's witness can read our head.

WHY A SECOND FORMAT EXISTS BESIDE `head.json`. Our own head is JSON and our own witness reads it,
which is fine right up to the moment the useful witness is somebody else's. The witness protocol the
field actually implements is c2sp.org/tlog-witness (v1.0.0, tagged 2026-03-31), and it speaks
checkpoints: a signed note whose text is origin, tree size, and the RFC 6962 root in base64. The
spec is blunt about the entry condition -- a public log's checkpoint MUST carry at least one
signature by the log -- and an unsigned head does not qualify, however honest it is.

So this module is the adapter, not a new design. The tree is the same tree: `merkle.py` already
computes RFC 6962 roots and consistency proofs, and `checkpoint_text` only restates the head another
implementation can already verify.

    note = signed_checkpoint("92.5.74.17.sslip.io/log", 281, root_bytes, sk_hex, pub_hex)
    text, signers = verify_note(note, {"92.5.74.17.sslip.io/log": pub_hex})

THE KEY ID IS NOT A SECURITY BOUNDARY, and the spec says so: it is four bytes, an attacker can grind
a collision, and all they win is a signature that fails to verify. It exists to pick which key to
try. We follow the recommended derivation so other implementations can find our key at all.

WHAT THIS DOES NOT DO. It does not make us witnessed. A checkpoint a witness can read is the
precondition for asking one, and the asking is a separate, human step.
"""
from __future__ import annotations

import base64
import hashlib

#: The signature type byte for Ed25519, from c2sp.org/signed-note. Only this one is implemented: the
#: spec recommends supporting exactly the type your design needs rather than a menu at runtime.
ED25519_SIGNATURE_TYPE = 0x01


def key_id(name: str, pubkey_hex: str) -> bytes:
    """The four-byte identifier for (key name, algorithm, public key).

    SHA-256(name || 0x0A || signature type || public key)[:4], per signed-note. Note the newline and
    the type byte: leaving either out produces an id that no other implementation computes, which
    looks like a working log until a real witness ignores every signature we make.
    """
    body = name.encode("utf-8") + b"\x0a" + bytes([ED25519_SIGNATURE_TYPE]) + bytes.fromhex(pubkey_hex)
    return hashlib.sha256(body).digest()[:4]


def checkpoint_text(origin: str, size: int, root: bytes, extensions=None) -> str:
    """The note text of a checkpoint: origin, tree size, base64 root, and optional extension lines.

    Ends with a newline, because the note text INCLUDES its final newline and excludes the blank
    line that separates it from the signatures. Extension lines are discouraged by the spec (a
    monitor cannot audit them) and are here only because the format allows them.
    """
    if not origin or any(c in origin for c in " +") or "\n" in origin:
        raise ValueError("origin must be non-empty and free of spaces, plus signs and newlines")
    if size < 0:
        raise ValueError("tree size cannot be negative")
    if len(root) != 32:
        raise ValueError("root must be the 32-byte RFC 6962 hash")
    lines = [origin, str(size), base64.b64encode(root).decode("ascii")]
    for ext in (extensions or []):
        if not ext or "\n" in ext:
            raise ValueError("an extension line must be non-empty and single-line")
        lines.append(ext)
    return "\n".join(lines) + "\n"


def sign_note(text: str, name: str, secret_hex: str, pubkey_hex: str) -> str:
    """Return the note: its text, a blank line, then one signature line for this key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if not text.endswith("\n"):
        raise ValueError("note text must end with a newline")
    sig = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret_hex)).sign(text.encode("utf-8"))
    blob = base64.b64encode(key_id(name, pubkey_hex) + sig).decode("ascii")
    return "%s\n— %s %s\n" % (text, name, blob)


def split_note(note: str):
    """-> (text, [(name, blob_bytes)]). The text is everything before the LAST empty line.

    The last one, not the first: a note text may contain empty lines, and taking the first would let
    anybody who can write a blank line into the text move the signature boundary.
    """
    if "\n\n" not in note:
        raise ValueError("a note has a blank line between its text and its signatures")
    head, _, tail = note.rpartition("\n\n")
    text = head + "\n"
    out = []
    for line in tail.splitlines():
        if not line.startswith("— "):
            raise ValueError("signature lines start with an em dash and a space")
        parts = line[2:].split(" ")
        if len(parts) != 2:
            raise ValueError("a signature line is: em dash, space, key name, space, base64")
        out.append((parts[0], base64.b64decode(parts[1])))
    if not out:
        raise ValueError("a note carries at least one signature")
    return text, out


def verify_note(note: str, known_keys: dict, required=None):
    """-> (text, [names that verified]). `known_keys` maps key name to public key hex.

    A signature from an unknown key is IGNORED, as the spec requires, and one from a known key that
    fails to verify raises: the difference matters, because the first is somebody else's business
    and the second is evidence about ours. `required` names keys that must be among the verifiers.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    text, sigs = split_note(note)
    verified = []
    for name, blob in sigs:
        pub = known_keys.get(name)
        if pub is None or len(blob) < 4:
            continue                                            # unknown key, or not for this scheme
        if blob[:4] != key_id(name, pub):
            continue                                            # same name, different key: not ours
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(blob[4:], text.encode("utf-8"))
        verified.append(name)
    for name in (required or []):
        if name not in verified:
            raise ValueError("no valid signature from %r" % name)
    return text, verified


def parse_checkpoint(note: str, known_keys: dict, required=None):
    """-> {"origin", "size", "root", "extensions", "signed_by"} for a verified checkpoint."""
    text, verified = verify_note(note, known_keys, required=required)
    lines = text.split("\n")[:-1]                               # the trailing newline is not a line
    if len(lines) < 3:
        raise ValueError("a checkpoint has at least three lines")
    root = base64.b64decode(lines[2])
    if len(root) != 32:
        raise ValueError("the root line is not a 32-byte hash")
    return {"origin": lines[0], "size": int(lines[1]), "root": root,
            "extensions": lines[3:], "signed_by": verified}


def signed_checkpoint(origin: str, size: int, root: bytes, secret_hex: str, pubkey_hex: str,
                      extensions=None) -> str:
    """The whole thing: build the text and sign it with the log's own key, under its origin name."""
    return sign_note(checkpoint_text(origin, size, root, extensions), origin, secret_hex, pubkey_hex)


def vkey(name: str, pubkey_hex: str) -> str:
    """The key in the form other implementations paste into their config: name+base64(type||key)."""
    blob = base64.b64encode(bytes([ED25519_SIGNATURE_TYPE]) + bytes.fromhex(pubkey_hex)).decode("ascii")
    return "%s+%s+%s" % (name, key_id(name, pubkey_hex).hex(), blob)
