"""AUDIT-B B-19: the Claude Code hook imports only the modules it uses, and the public package is unchanged.

The hook runs as a new process for every event, and `import inspeximus.claude_code` first ran the package
`__init__`, which imported eleven governance modules the hook never calls: erasure residue, SCITT and COSE,
qualified timestamps and trusted lists, the action ledger, subject rights, technical documentation, the
deployer report, the agent audit trail and partitions. Measured 2026-09-27, 7 interleaved fresh
interpreters each: 208 ms with them and 148 ms without, per hook event.

Session 1's condition for changing the package import: a fresh interpreter must show that every name in
`__all__`, `from inspeximus import *` and `dir()` still work, and that no module depends on an import-time
side effect. The first test is the finding and fails on the eager package. The others guard the public
surface against 3.14.3 and pass both before and after the change, so a lazy package cannot drop a name.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The governance modules the eager `__init__` imported and the hook never calls.
LAZY_MODULES = ["actions", "agent_audit_trail", "cose", "deployer", "erasure_residue", "partitions", "scitt",
                "subject_rights", "technical_documentation", "timestamp", "trusted_list"]
#: Every submodule `import inspeximus` exposed as an attribute on 3.14.3.
SUBMODULES_3_14_3 = sorted(LAZY_MODULES + ["core", "sqlite_store"])
#: The dunder names of the 3.14.3 package namespace.
DUNDERS_3_14_3 = ["__all__", "__builtins__", "__cached__", "__doc__", "__file__", "__loader__", "__name__",
                  "__package__", "__path__", "__spec__", "__version__"]


def _fresh(code):
    """Run `code` in a new interpreter on this tree and return what it printed as JSON."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env["PYTHONPATH"] = ROOT
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    if p.returncode:
        pytest.fail(f"the fresh interpreter failed: {p.stderr[-1500:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_importing_the_hook_loads_no_governance_module():
    loaded = _fresh("import json, sys; import inspeximus.claude_code; "
                    "print(json.dumps(sorted(m for m in sys.modules if m.startswith('inspeximus'))))")
    assert "inspeximus.claude_code" in loaded and "inspeximus.core" in loaded, f"control: {loaded}"
    extra = [m for m in LAZY_MODULES if f"inspeximus.{m}" in loaded]
    assert extra == [], f"importing the hook loaded {len(extra)} module(s) it never calls: {extra}"


def test_dir_lists_exactly_what_3_14_3_listed():
    got = _fresh("import json, inspeximus; print(json.dumps({'dir': dir(inspeximus), 'all': inspeximus.__all__}))")
    want = sorted(set(got["all"]) | set(SUBMODULES_3_14_3) | set(DUNDERS_3_14_3))
    assert len(got["all"]) == 51, f"control: __all__ has {len(got['all'])} names, 3.14.3 had 51"
    assert sorted(got["dir"]) == want, (sorted(set(want) - set(got["dir"])), sorted(set(got["dir"]) - set(want)))


def test_every_public_name_is_the_object_its_module_defines():
    got = _fresh(r'''
import importlib, json, inspeximus
renamed = {"export_audit_trail": ("agent_audit_trail", "export_jsonl"),
           "verify_audit_trail": ("agent_audit_trail", "verify_jsonl")}
homes = ["core", "erasure_residue", "scitt", "timestamp", "trusted_list", "actions", "subject_rights",
         "technical_documentation", "deployer", "agent_audit_trail", "partitions"]
bad = []
for name in inspeximus.__all__:
    obj = getattr(inspeximus, name)
    if name in renamed:
        mod, attr = renamed[name]
        ok = obj is getattr(importlib.import_module("inspeximus." + mod), attr)
    else:
        ok = any(getattr(importlib.import_module("inspeximus." + h), name, None) is obj for h in homes)
    if not ok:
        bad.append(name)
print(json.dumps(bad))
''')
    assert got == [], f"public names that are not their module's object: {got}"


def test_star_import_binds_every_public_name():
    got = _fresh("import json; ns = {}; exec('from inspeximus import *', ns); import inspeximus; "
                 "print(json.dumps([n for n in inspeximus.__all__ if n not in ns]))")
    assert got == [], f"`from inspeximus import *` did not bind: {got}"


def test_every_submodule_3_14_3_exposed_is_reachable_after_a_bare_import():
    got = _fresh("import json, types, inspeximus; "
                 f"print(json.dumps([n for n in {SUBMODULES_3_14_3!r} "
                 "if not isinstance(getattr(inspeximus, n, None), types.ModuleType)]))")
    assert got == [], f"`inspeximus.<name>` no longer resolves after `import inspeximus`: {got}"


def test_importing_a_governance_module_changes_nothing_in_core():
    """No import-time side effect for a lazy import to lose: importing each governance module after core
    leaves the Inspeximus class, the core module namespace, the root logger and the environment as they
    were. CONTROL: the snapshot does see a change made in the same window."""
    got = _fresh(r'''
import importlib, json, logging, os
import inspeximus.core as core
def snap():
    return {"cls": sorted(dir(core.Inspeximus)), "core": sorted(vars(core)),
            "root": [type(h).__name__ for h in logging.root.handlers] + [logging.root.level],
            "env": sorted(os.environ)}
before = snap()
for m in %r:
    importlib.import_module("inspeximus." + m)
after = snap()
core.Inspeximus._audit_b_probe = 1
control = snap()
print(json.dumps({"changed": [k for k in before if before[k] != after[k]],
                  "control_seen": control["cls"] != after["cls"]}))
''' % (LAZY_MODULES,))
    assert got["control_seen"], "control: the snapshot did not see a change to the class"
    assert got["changed"] == [], f"importing the governance modules changed: {got['changed']}"
