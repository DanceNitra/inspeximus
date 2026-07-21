"""Deprecated import alias: `mnemo` is now `inspeximus`.

The package was renamed in 1.25.0. Everything importable as `mnemo.*` keeps working through this
shim — `from mnemo import Mnemo`, `from mnemo.mnemo import regex_extractor`,
`from mnemo.integrations.langgraph import MnemoStore` — and resolves to the *identical* object, so
`isinstance` checks, monkeypatching and module-level state behave the same in both namespaces.

Two implementations were tried and rejected before this one, both of which look correct and are not:

  1. A meta-path finder returning the target module's own `__spec__`. The import machinery re-executes
     the module from that spec, so `mnemo.mnemo.Mnemo` and `inspeximus.mnemo.Mnemo` became two
     distinct classes.
  2. The same finder returning a spec whose loader hands back the already-imported module. The
     machinery still re-initialises module attributes against the alias name, and the double
     execution persisted — measured: `mnemo.mnemo.Mnemo is inspeximus.mnemo.Mnemo` was False.

What actually works is to skip the import machinery entirely: bind the already-imported module
objects into `sys.modules` under their old names, so any later `import mnemo.x` is a cache hit and
no second execution can happen. The submodule list is walked, not hand-written, so it cannot go
stale when a module is added.

Deprecated: will be removed in 2.0.
"""
import importlib
import pkgutil
import sys
import warnings

_OLD = "mnemo"
_NEW = "inspeximus"

_pkg = importlib.import_module(_NEW)


def _alias_tree(package, old_prefix):
    """Bind every submodule of `package` into sys.modules under `old_prefix`, recursively."""
    for info in pkgutil.iter_modules(package.__path__):
        old_name = f"{old_prefix}.{info.name}"
        try:
            module = importlib.import_module(f"{package.__name__}.{info.name}")
        except Exception:
            continue                            # an optional integration whose extra isn't installed
        sys.modules[old_name] = module          # the SAME object, no re-execution
        if info.ispkg and hasattr(module, "__path__"):
            _alias_tree(module, old_name)


_alias_tree(_pkg, _OLD)

warnings.warn(
    "`mnemo` has been renamed to `inspeximus`; import `inspeximus` instead. "
    "The `mnemo` alias still works and will be removed in 2.0.",
    DeprecationWarning,
    stacklevel=2,
)

globals().update({k: v for k, v in vars(_pkg).items() if not k.startswith("__")})
__all__ = list(getattr(_pkg, "__all__", []))
__version__ = getattr(_pkg, "__version__", None)
