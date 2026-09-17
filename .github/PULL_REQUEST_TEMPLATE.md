## What this changes

## How it was measured
- [ ] A test fails without this change and passes with it.
- [ ] For a defect fix: a `tools/mutations.json` entry, `python tools/mutation_check.py tools/mutations.json` reports 0 survived.
- [ ] No new runtime dependency.
- [ ] If a README number changed: `python claims_audit.py --write-claims` was run and `docs/CLAIMS.md` is in this PR.
