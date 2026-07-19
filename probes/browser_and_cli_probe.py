"""browser_and_cli_probe.py — the offline memory browser + the fattened CLI + the batteries-included distiller.

Every competitor ships a console to SEE memories; mnemo now renders a self-contained offline HTML browser, and the
CLI grew from 6 to 13 commands (browse/decision/contradictions/governance/consolidate/why/distill). Asserts (each
able to FAIL):
  1. browser.render_html produces a self-contained HTML doc with the memory text inlined.
  2. the browser marks a SUPERSEDED memory as superseded (so you can see corrections, not just current).
  3. `mnemo browse --out FILE` writes a real HTML file containing the memory.
  4. the new CLI commands parse + run without error (decision, contradictions, governance, consolidate, why).
  5. default_distiller raises a clear error when no LLM endpoint is configured (opt-in, honest).
"""
import os, sys, tempfile, io
sys.path.insert(0, ".")
from mnemo import Mnemo, default_distiller
from mnemo.browser import render_html, write_html
from mnemo import cli

FAILS = []
def check(n, c):
    print(f"  [{'OK ' if c else 'XXX'}] {n}")
    if not c: FAILS.append(n)

# 1 + 2: browser render, with a superseded memory
m = Mnemo(path=None)
m.remember("The deploy channel is BLUE-9.", key="deploy")
m.remember("The deploy channel is RED-2.", key="deploy")          # supersedes BLUE-9
htmlout = render_html(m)
check("1 render_html is a self-contained HTML doc with the memory text", "<!doctype html>" in htmlout.lower() and "RED-2" in htmlout)
check("2 browser shows the SUPERSEDED value + status", "BLUE-9" in htmlout and "superseded" in htmlout)

# 3: CLI browse writes a file
tmpstore = os.path.join(tempfile.gettempdir(), "bcli_probe_store.json")
if os.path.exists(tmpstore): os.remove(tmpstore)
os.environ["MNEMO_EMBED_URL"] = ""                                 # lexical, no GPU
cli.main(["--path", tmpstore, "remember", "Paris is the capital of France.", "--key", "cap"])
outhtml = os.path.join(tempfile.gettempdir(), "bcli_probe.html")
if os.path.exists(outhtml): os.remove(outhtml)
rc = cli.main(["--path", tmpstore, "browse", "--out", outhtml])
ok3 = rc == 0 and os.path.exists(outhtml) and "Paris" in open(outhtml, encoding="utf-8").read()
check("3 `mnemo browse` writes an HTML file containing the memory", ok3)

# 4: the new CLI commands run without error
def run(argv):
    try:
        return cli.main(["--path", tmpstore] + argv) == 0
    except SystemExit as e:
        return e.code in (0, None)
    except Exception as ex:
        print("     cmd error:", argv, str(ex)[:80]); return False
ok4 = all([
    run(["decision", "go with RED-2", "--because", "blue failed", "--topic", "deploy-choice"]),
    run(["contradictions"]), run(["governance"]), run(["consolidate"]), run(["why", "capital of France"]),
])
check("4 new CLI commands (decision/contradictions/governance/consolidate/why) all run", ok4)

# 5: default_distiller is opt-in — clear error without an endpoint
os.environ.pop("MNEMO_LLM_URL", None)
try:
    default_distiller()
    ok5 = False
except RuntimeError as e:
    ok5 = "MNEMO_LLM_URL" in str(e)
check("5 default_distiller raises a clear opt-in error with no endpoint", ok5)

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
