"""`python -m inspeximus` runs the CLI, exactly as `inspeximus` and `python -m inspeximus.cli` do.

3.14.4: it failed with "No module named inspeximus.__main__", and an agent on a machine without the
`inspeximus` script on PATH tries this form first.
"""
from inspeximus.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
