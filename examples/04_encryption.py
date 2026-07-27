"""
inspeximus example 04 — encryption-at-rest + crypto-shredding.

    pip install "inspeximus" cryptography
    python 04_encryption.py

Opt-in AES-256-GCM at rest (standard crypto, not home-rolled). inspeximus never stores your key. Destroying the key
(shred) makes the store — and every at-rest backup of it — permanently unrecoverable. Honest scope: this
protects data AT REST (a stolen file/disk/backup), NOT a compromised running process (the key lives in RAM).
"""
from inspeximus import Inspeximus, new_encryption_key

key = new_encryption_key()                 # 32-byte AES-256 key; hold it yourself (a secrets manager / keystore)

m = Inspeximus(path="secure.json", encrypt_key=key)
m.remember("customer card on file ends 4242", key="billing::card")
m.flush()

# On disk it's ciphertext — the plaintext is not there:
raw = open("secure.json", "rb").read()
print("on-disk header:", raw[:5], "| plaintext present?", b"4242" in raw)

# Reopen with the key -> data intact; wrong/no key fails LOUD (never a silent empty store):
ok = Inspeximus(path="secure.json", encrypt_key=key)
print("recall with key:", [r["text"] for r in ok.recall("card")])
try:
    Inspeximus(path="secure.json")              # no key
except ValueError as e:
    print("open without key ->", e)

# Crypto-shred: destroy the key -> unrecoverable (supports a GDPR Art.17 erasure workflow):
print("shred:", m.shred()["note"])

# (passphrase form: Inspeximus(path=..., encrypt_passphrase="..."), scrypt-derived)
import os; os.remove("secure.json")
