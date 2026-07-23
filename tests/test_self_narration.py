"""check_self_narration: flags assistant self-talk/reasoning masquerading as a stored user fact."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus.core import Inspeximus

M = Inspeximus(path=None)

def test_flags_assistant_self_reference():
    r = M.check_self_narration("As an AI, I can help you plan your trip")
    assert r["self_narration"] is True and "as an ai" in r["markers"], r

def test_flags_first_person_reasoning():
    r = M.check_self_narration("I think the user prefers window seats")
    assert r["self_narration"] is True and "i think" in r["markers"], r

def test_flags_memory_self_reference():
    r = M.check_self_narration("I remember that you mentioned a dog")
    assert r["self_narration"] is True and "i remember that" in r["markers"], r

def test_clean_user_fact_passes():
    r = M.check_self_narration("The user's favorite color is blue")
    assert r["self_narration"] is False and r["markers"] == [], r

def test_third_person_fact_passes():
    r = M.check_self_narration("The user thinks the plan is risky")   # 'thinks' != 'i think'
    assert r["self_narration"] is False, r

def test_word_boundary_no_false_fire():
    r = M.check_self_narration("Within the report, findings were summarized")  # 'within' must not fire 'i think'? n/a
    assert r["self_narration"] is False, r
    r2 = M.check_self_narration("The airbelieve brand launched")  # substring must not fire 'i believe'
    assert r2["self_narration"] is False, r2

def test_empty_and_nonstring():
    assert M.check_self_narration("")["self_narration"] is False
    assert M.check_self_narration(None)["self_narration"] is False

def test_multiple_markers():
    r = M.check_self_narration("As an assistant, I believe I remember that you like tea")
    assert r["self_narration"] is True and len(r["markers"]) >= 2, r

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS {fn.__name__}"); p += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{p}/{len(fns)} passed")
    sys.exit(0 if p == len(fns) else 1)
