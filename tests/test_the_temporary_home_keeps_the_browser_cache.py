"""The temporary home every test runs in keeps the real Playwright browser cache.

Measured 2026-09-27 on CI (tests run 36330567561): the job that runs the verifier page in Chromium
installs the browser into the real ~/.cache/ms-playwright, the conftest then moved HOME to a temporary
directory, and Playwright looked for the browser there. All 144 browser tests failed at setup with
"Executable doesn't exist at /tmp/inspeximus-test-home-.../.cache/ms-playwright/...".
"""
from __future__ import annotations

import os
import shutil

import pytest

from conftest import _redirect_home, _restore_home


class _Worker:
    workerinput = {}                       # a worker, not the xdist controller: the redirect applies

    def getoption(self, *a, **k):
        return None


@pytest.fixture
def real_home(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(real))
    for k in ("PLAYWRIGHT_BROWSERS_PATH", "XDG_CACHE_HOME"):
        monkeypatch.delenv(k, raising=False)
    return real


def _redirected(env_key):
    cfg = _Worker()
    _redirect_home(cfg)
    try:
        return os.environ.get(env_key), cfg._test_home
    finally:
        _restore_home(cfg)
        shutil.rmtree(cfg._test_home, ignore_errors=True)


@pytest.mark.parametrize("parts", [(".cache", "ms-playwright"), ("Library", "Caches", "ms-playwright")])
def test_the_real_playwright_cache_survives_the_redirect(real_home, parts):
    cache = real_home.joinpath(*parts)
    cache.mkdir(parents=True)
    got, home = _redirected("PLAYWRIGHT_BROWSERS_PATH")
    assert got == str(cache), (got, home)
    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") is None, "restored after the run"


def test_an_explicit_playwright_path_is_left_alone(real_home, monkeypatch):
    (real_home / ".cache" / "ms-playwright").mkdir(parents=True)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/browsers")
    got, _ = _redirected("PLAYWRIGHT_BROWSERS_PATH")
    assert got == "/opt/browsers"


def test_no_cache_means_no_variable(real_home):
    got, _ = _redirected("PLAYWRIGHT_BROWSERS_PATH")
    assert got is None
