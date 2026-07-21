"""Verify an installed wrapper package really imports and re-exports what it claims.

Run in CI after installing the built wheel:  python packages/_check_wrapper.py <package-dir-name>

It asks the package's own pyproject which modules it ships rather than transforming the distribution
name into an import name. The transform this replaced was written for the `langgraph.*` namespace
packages and turned `adk-inspeximus` into `adk.inspeximus`, which does not exist -- so the check would
have failed the release for a package that was fine.
"""
import importlib
import pathlib
import sys
import tomllib

HERE = pathlib.Path(__file__).resolve().parent


def main(pkg_dir: str) -> int:
    cfg = tomllib.loads((HERE / pkg_dir / "pyproject.toml").read_text(encoding="utf-8"))
    modules = cfg["tool"]["setuptools"]["packages"]
    if not modules:
        print(f"::error::{pkg_dir} declares no packages")
        return 1
    for name in modules:
        mod = importlib.import_module(name)
        exported = getattr(mod, "__all__", None)
        if not exported:
            print(f"::error::{name} imports but exports nothing (__all__ missing or empty)")
            return 1
        for symbol in exported:
            if not hasattr(mod, symbol):
                print(f"::error::{name} lists {symbol} in __all__ but does not define it")
                return 1
        print(f"  {name} -> {exported}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
