"""verify_claim: the read-time grounding check. Asserts it does what it claims — especially the
differentiator (stale_superseded: a reply citing a CORRECTED fact), which a cosine/LLM judge misses
because the old value is embedding-similar to the claim."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus.core import Inspeximus

def fresh():
    return Inspeximus(path=None)

def test_supported_keyed():
    m = fresh()
    m.remember("User's favorite color is blue", key="user::fav_color", object="blue")
    r = m.verify_claim("favorite color is blue", key="user::fav_color", object="blue")
    assert r["verdict"] == "supported", r
    assert r["current"] == "blue", r

def test_stale_superseded_keyed():
    """THE differentiator: old value was corrected; a reply that still cites it must be flagged."""
    m = fresh()
    m.remember("User lives in Berlin", key="user::city", object="Berlin")
    m.remember("User lives in Munich", key="user::city", object="Munich")   # correction -> supersedes Berlin
    # current truth is Munich; a reply asserting Berlin is citing a corrected fact
    r = m.verify_claim("you live in Berlin", key="user::city", object="Berlin")
    assert r["verdict"] == "stale_superseded", r
    assert r["current"] == "Munich", r          # tells the caller the truth NOW
    # and the current value verifies as supported
    r2 = m.verify_claim("you live in Munich", key="user::city", object="Munich")
    assert r2["verdict"] == "supported", r2

def test_contradicted_keyed():
    """A value never stored for this key, differing from current -> contradicted (not just unsupported)."""
    m = fresh()
    m.remember("User lives in Munich", key="user::city", object="Munich")
    r = m.verify_claim("you live in Paris", key="user::city", object="Paris")
    assert r["verdict"] == "contradicted", r
    assert r["current"] == "Munich", r

def test_unsupported_keyed():
    m = fresh()
    m.remember("User lives in Munich", key="user::city", object="Munich")
    r = m.verify_claim("your dog is named Rex", key="user::pet", object="Rex")
    assert r["verdict"] == "unsupported", r
    assert r["current"] is None, r

def test_supported_keyless():
    m = fresh()
    m.remember("The project deadline is Friday")
    r = m.verify_claim("the project deadline is Friday")
    assert r["verdict"] == "supported", r

def test_stale_superseded_keyless():
    """Keyless: a corrected fact retired via key, then asserted as free text -> found among retired."""
    m = fresh()
    m.remember("The API rate limit is 100 requests per second", key="api::rate_limit", object="100")
    m.remember("The API rate limit is 500 requests per second", key="api::rate_limit", object="500")
    r = m.verify_claim("the API rate limit is 100 requests per second")   # no key -> similarity path
    assert r["verdict"] in ("stale_superseded", "contradicted"), r        # must NOT be 'supported'
    assert r["verdict"] == "stale_superseded", r

def test_unsupported_keyless_fabrication():
    m = fresh()
    m.remember("The project deadline is Friday")
    r = m.verify_claim("the office is closed on Mondays")
    assert r["verdict"] == "unsupported", r

def test_does_not_write():
    """verify_claim must be read-only."""
    m = fresh()
    m.remember("User lives in Munich", key="user::city", object="Munich")
    before = len(m.items)
    m.verify_claim("you live in Berlin", key="user::city", object="Berlin")
    m.verify_claim("anything at all", key="new::key", object="x")
    assert len(m.items) == before, "verify_claim must not create records"

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS {fn.__name__}"); passed += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
