"""extractor_hook_probe.py — a pluggable write-path extractor makes the governance layer key itself over free text.

Without an extractor, plain free-text writes never fire keyed supersession (the caller must pass key=/object=).
With `store.extractor = fn` (text -> (key, object)), remember() derives them, so keyed supersession, echo_guard,
check_conflict, and forget_subject all compose over free text — no per-call keying. Fail-open: a broken
extractor never breaks a write. The extractor here is a tiny deterministic regex (a real app plugs its own).
"""
import sys, pathlib, re
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mnemo import Mnemo

# toy deterministic extractor: "<entity> <rel> is <value>" -> (key="<entity>::<rel>", object="<value>")
_PAT = re.compile(r"(?P<subj>[\w ]+?)\s+(?:is|=)\s+(?P<val>[\w.\-]+)\s*$", re.I)
def extract(text):
    m = _PAT.search(text.strip().rstrip("."))
    if not m:
        return None
    subj = m.group("subj").strip().lower().replace(" ", "_")
    return (f"fact::{subj}", m.group("val").strip().lower())


def _active_val(m, key):
    a = [r for r in m.items if r.get("status") == "active" and r.get("key") == key]
    return a[0].get("object") if a else None


def main():
    ok = {}

    # baseline: NO extractor -> plain free-text writes do NOT supersede (both stay active)
    m = Mnemo(path=None)
    m.remember("server timezone is UTC")
    m.remember("server timezone is PST")
    active_no = [r for r in m.items if r.get("status") == "active"]
    ok["A no extractor -> no auto-supersession (append-only)"] = len(active_no) == 2

    # with extractor: the same free-text writes now key + supersede automatically -> current-truth
    m = Mnemo(path=None)
    m.extractor = extract
    m.remember("server timezone is UTC")
    m.remember("server timezone is PST")     # same key -> supersedes UTC, no manual key=
    ok["B extractor -> auto keyed supersession"] = (_active_val(m, "fact::server_timezone") == "pst"
        and len([r for r in m.items if r.get("status") == "active"]) == 1)

    # echo_guard composes: restating the superseded value does not resurrect it
    m = Mnemo(path=None); m.extractor = extract; m.echo_guard = True
    m.remember("region is frankfurt"); m.remember("region is ohio")
    r = m.remember("region is frankfurt")    # echo of superseded value, via extractor-derived key/object
    rec = next(x for x in m.items if x["id"] == r)
    ok["C echo_guard composes over free text"] = (bool((rec.get("meta") or {}).get("echo_blocked"))
        and _active_val(m, "fact::region") == "ohio")

    # check_conflict composes: a new free-text value is flagged as a keyed change before you commit
    m = Mnemo(path=None); m.extractor = extract
    m.remember("retry cap is 5")
    k, obj = extract("retry cap is 12")
    conflicts = m.check_conflict("retry cap is 12", key=k, object=obj)
    ok["D check_conflict composes"] = any(c["kind"] == "keyed_value_change" for c in conflicts)

    # forget_subject composes: erase by the extractor-derived source is untouched; here confirm keying didn't
    # break plain source-based erasure (governance still whole)
    m = Mnemo(path=None); m.extractor = extract
    m.remember("alice email is a@x.test", source={"doc": "user-alice"})
    er = m.forget_subject("user-alice")
    ok["E forget_subject still works with extractor on"] = (er["erased"] == 1)

    # fail-open: a broken extractor never breaks a write (falls back to plain append)
    m = Mnemo(path=None)
    m.extractor = lambda t: (_ for _ in ()).throw(RuntimeError("boom"))
    rid = m.remember("this should still be stored")
    ok["F broken extractor -> fail-open (write still lands)"] = (
        any(x["id"] == rid and x.get("status") == "active" for x in m.items))

    # explicit key wins: an extractor never overrides a caller-supplied key
    m = Mnemo(path=None); m.extractor = extract
    m.remember("port is 8080", key="explicit::port", object="8080")
    ok["G explicit key overrides extractor"] = (_active_val(m, "explicit::port") == "8080"
        and _active_val(m, "fact::port") is None)

    print("=" * 66)
    print("extractor hook — governance keys itself over free text (opt-in)")
    print("=" * 66)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("-" * 66)
    print("RECEIPT:", "VALID — all checks hold" if all(ok.values()) else "INVALID — do not ship")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
