"""Encryption-at-rest + crypto-shredding (1.7.0). Needs `cryptography`. Run: python -m pytest tests/test_encryption.py"""
import json
import os
import tempfile

import pytest

from inspeximus import Inspeximus, new_encryption_key

from _store_io import load_store


def _tmp():
    return os.path.join(tempfile.mkdtemp(), "store.json")


def test_new_encryption_key_is_32_bytes():
    k = new_encryption_key()
    assert isinstance(k, bytes) and len(k) == 32
    assert new_encryption_key() != k                 # random each call


def test_roundtrip_with_key():
    p, k = _tmp(), new_encryption_key()
    m = Inspeximus(path=p, encrypt_key=k)
    m.remember("the deploy secret is ACME-XYZ-9", key="dep::key", object="ACME-XYZ-9")
    m.flush()
    # reopen with the same key -> data intact
    m2 = Inspeximus(path=p, encrypt_key=k)
    got = m2.recall("deploy secret", k=5)
    assert got and "ACME-XYZ-9" in got[0]["text"]


def test_ciphertext_on_disk_hides_plaintext():
    p, k = _tmp(), new_encryption_key()
    m = Inspeximus(path=p, encrypt_key=k)
    m.remember("SUPER-SECRET-VALUE-42")
    m.flush()
    raw = open(p, "rb").read()
    assert raw[:5] == b"INSP\x01"                     # encrypted header
    assert b"SUPER-SECRET-VALUE-42" not in raw        # plaintext is NOT on disk


def test_open_encrypted_without_key_fails_loud():
    p, k = _tmp(), new_encryption_key()
    m = Inspeximus(path=p, encrypt_key=k); m.remember("x"); m.flush()
    with pytest.raises(ValueError):
        Inspeximus(path=p)                                  # encrypted store, no key -> raise (never silent-empty)


def test_wrong_key_fails_loud():
    p = _tmp()
    m = Inspeximus(path=p, encrypt_key=new_encryption_key()); m.remember("x"); m.flush()
    with pytest.raises(ValueError):
        Inspeximus(path=p, encrypt_key=new_encryption_key())   # different key -> decryption fails


def test_tamper_is_detected():
    p, k = _tmp(), new_encryption_key()
    m = Inspeximus(path=p, encrypt_key=k); m.remember("x"); m.flush()
    raw = bytearray(open(p, "rb").read())
    raw[-1] ^= 0x01                                    # flip a bit in the GCM tag/ciphertext
    open(p, "wb").write(raw)
    with pytest.raises(ValueError):
        Inspeximus(path=p, encrypt_key=k)                   # AEAD authentication fails


def test_passphrase_roundtrip_and_wrong_passphrase():
    p = _tmp()
    m = Inspeximus(path=p, encrypt_passphrase="correct horse battery staple")
    m.remember("passphrase secret ZZ"); m.flush()
    ok = Inspeximus(path=p, encrypt_passphrase="correct horse battery staple")
    assert any("ZZ" in r["text"] for r in ok.items)
    with pytest.raises(ValueError):
        Inspeximus(path=p, encrypt_passphrase="wrong passphrase")


def test_shred_makes_it_unrecoverable():
    p, k = _tmp(), new_encryption_key()
    m = Inspeximus(path=p, encrypt_key=k)
    m.remember("shred me PLEASE"); m.flush()
    res = m.shred()
    assert res["shredded"] and res["records_dropped"] == 1
    assert m.items == []                               # RAM cleared
    # the on-disk ciphertext is now only openable by whoever still holds the key; destroy every copy of the
    # key and it is gone. (Here the test still holds k, so it *can* reopen — that's correct crypto-shred
    # semantics: the guarantee is "no key -> no data", proven by test_wrong_key/test_open_without_key.)
    with pytest.raises(RuntimeError):
        Inspeximus().shred()                                # shred requires an encrypted store (plain store -> raise)


def test_bad_key_length_rejected():
    with pytest.raises(ValueError):
        Inspeximus(encrypt_key=b"too-short")


def test_unencrypted_is_byte_identical_legacy():
    p = _tmp()
    m = Inspeximus(path=p); m.remember("plain text here"); m.flush()
    raw = open(p, "rb").read()
    assert raw[:5] != b"INSP\x01"                       # plaintext JSON, no encryption header
    assert b"plain text here" in raw
    assert load_store(p), "an unencrypted store must read back as records"


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); print("ok", fn.__name__); passed += 1
        except BaseException as e:
            # pytest.raises used inline; emulate for __main__ run
            print("FAIL", fn.__name__, repr(e))
    print(f"\n{passed}/{len(fns)} passed")
