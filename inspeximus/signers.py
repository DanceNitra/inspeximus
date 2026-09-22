"""Signers for write receipts, so the key that proves the record does not sit beside the record.

THE SENTENCE THIS EXISTS TO ANSWER. On a first review, an assessor asks who could have written the
receipts. If the signing key is a file in the same directory as the store, the honest answer is
"anyone who could write the records", and the chain then proves only that nobody edited it
afterwards from outside. Moving the key behind a service that signs but never exports changes that
answer without changing anything an auditor has to read.

`Inspeximus(..., receipt_signer=signer)` takes anything callable as `signer(hash_hex) -> sig_hex`,
so these classes are drop-ins. They also carry `public_key_hex`, which is what a verifier needs and
the only key material that ever leaves the service.

WHICH KMS, AND WHY THIS ONE FIRST. HashiCorp Vault's transit engine, because it is the cheapest
HONEST test path: it runs as a real service in a container with no account, no card and no cloud
region, signs with real Ed25519, and never returns the private key. AWS KMS, GCP KMS and Azure Key
Vault are the ones enterprises ask for, and each would be tested here against a mock, which is a
test of the mock. When a design partner names their KMS, this interface is what they implement, and
the test they get will run against their real service rather than a fake of it.

    from inspeximus.signers import VaultTransitSigner
    signer = VaultTransitSigner("http://127.0.0.1:8200", token, "inspeximus-receipts")
    m = Inspeximus("mem.json", receipts=True, receipt_signer=signer,
                   receipt_pubkey=signer.public_key_hex)
"""
from __future__ import annotations

import base64
import json
import ssl
import urllib.request


class FileSigner:
    """The development signer: the key is in this process, which is what the KMS path replaces.

    Kept, named and documented rather than quietly available, because "we support a KMS" and "we use
    one" are different claims and the difference is which of these two a deployment constructs.
    """

    def __init__(self, secret_hex: str):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self._sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret_hex))
        from cryptography.hazmat.primitives import serialization as _ser
        self.public_key_hex = self._sk.public_key().public_bytes(
            _ser.Encoding.Raw, _ser.PublicFormat.Raw).hex()
        self.kind = "file"
        self.exportable = True                                   # the honest label: this key can leave

    def __call__(self, hash_hex: str) -> str:
        return self._sk.sign(bytes.fromhex(hash_hex)).hex()


class VaultTransitSigner:
    """Sign through Vault's transit engine. The private key never exists in this process.

    Zero dependency: Vault's API is HTTP and JSON, so this is urllib. Failures are raised rather than
    swallowed, because a signer that quietly returns None turns every receipt unsigned while the
    store keeps reporting success, which is the failure mode this whole module is about.

    Set up, once, on the Vault side:

        vault secrets enable transit
        vault write -f transit/keys/inspeximus-receipts type=ed25519 exportable=false

    `exportable=false` is the point: with it, no token can read the private key out, so a stolen
    application token can sign new records but can never rewrite the old ones somewhere else.
    """

    def __init__(self, addr: str, token: str, key_name: str, timeout: float = 10.0,
                 ca_file: str | None = None, key_version: int | None = None):
        self.addr = addr.rstrip("/")
        self._token = token
        self.key_name = key_name
        self.timeout = timeout
        self.kind = "vault-transit"
        self.exportable = False
        self._ctx = ssl.create_default_context(cafile=ca_file) if ca_file else None
        self.key_version = key_version
        self.public_key_hex = self._fetch_public_key()

    # -- plumbing ------------------------------------------------------------------------------
    def _call(self, method: str, path: str, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.addr + path, data=data, method=method,
                                     headers={"X-Vault-Token": self._token,
                                              "Content-Type": "application/json"})
        kwargs = {"timeout": self.timeout}
        if self._ctx is not None:
            kwargs["context"] = self._ctx
        with urllib.request.urlopen(req, **kwargs) as r:
            return json.loads(r.read().decode("utf-8"))

    def _fetch_public_key(self) -> str:
        out = self._call("GET", "/v1/transit/keys/" + self.key_name)
        keys = (out.get("data") or {}).get("keys") or {}
        if not keys:
            raise RuntimeError("transit key %r has no versions" % self.key_name)
        version = str(self.key_version or max(int(v) for v in keys))
        entry = keys[version]
        pub = entry["public_key"] if isinstance(entry, dict) else entry
        raw = base64.b64decode(pub)
        if len(raw) != 32:
            raise RuntimeError("transit key %r is not Ed25519 (public key is %d bytes); create it "
                               "with type=ed25519" % (self.key_name, len(raw)))
        self.key_version = int(version)
        return raw.hex()

    # -- the signer interface ------------------------------------------------------------------
    def __call__(self, hash_hex: str) -> str:
        out = self._call("POST", "/v1/transit/sign/" + self.key_name,
                         {"input": base64.b64encode(bytes.fromhex(hash_hex)).decode("ascii")})
        sig = (out.get("data") or {}).get("signature")
        if not sig or not sig.startswith("vault:"):
            raise RuntimeError("transit returned no signature for %r" % self.key_name)
        # `vault:v<version>:<base64>`. The version is checked rather than ignored: a rotated key
        # signs with a new version, and a verifier holding the old public key would report every
        # later receipt as forged. Better to fail here, where the operator can read why.
        _prefix, version, blob = sig.split(":", 2)
        if int(version[1:]) != self.key_version:
            raise RuntimeError("transit signed with key version %s, but this signer verifies against "
                               "version %s: rotate the recorded public key deliberately"
                               % (version[1:], self.key_version))
        return base64.b64decode(blob).hex()

    def describe(self) -> dict:
        """What a deployment report says about where the key lives. No secret is in it."""
        return {"kind": self.kind, "address": self.addr, "key": self.key_name,
                "key_version": self.key_version, "public_key": self.public_key_hex,
                "private_key_in_process": False, "exportable": self.exportable}
