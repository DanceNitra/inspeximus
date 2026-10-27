"""Hermes Agent plugin wrapper for the inspeximus memory provider.

THIS FILE ADDS NO BEHAVIOUR. The provider lives in `inspeximus.integrations.hermes_agent`, which the
`pip_dependencies` entry installs, so the plugin directory and the pip entry point load the same
class and cannot drift apart. Hermes finds a directory plugin by `register(ctx)` first and a
top-level `MemoryProvider` subclass second (plugins/memory/__init__.py, `_load_provider_from_dir`),
so both are re-exported here.

Why the directory form exists at all, since the entry point already works: `hermes plugins install`
places a catalog entry under `~/.hermes/plugins/<name>`, and that is the route a Hermes user takes to
choose a memory backend. The entry point route requires the user to pip-install into Hermes' own
virtual environment by hand.
"""
from __future__ import annotations

from inspeximus.integrations.hermes_agent import (  # noqa: F401
    PROVIDER_NAME,
    provider_class,
    register,
)

__all__ = ["PROVIDER_NAME", "provider_class", "register"]
