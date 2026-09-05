"""An icon URL that 404s is worse than no icon: the directory renders a broken image.

Before 2026-09-05 `server.json` declared no `icons` at all, so Glama, PulseMCP and mcp.so rendered a
default placeholder beside our name. The registry schema has carried the field the whole time.

These tests check the declaration OFFLINE, by resolving each raw.githubusercontent URL back to the
file it points at in this repository. That catches the failure that actually happens, which is an
asset renamed or deleted while the URL stays behind, and it does so without a network call that
would make the suite flaky. `.github/workflows/discovery.yml` has network and checks the live URLs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER_JSON = ROOT / "server.json"
PREFIX = "https://raw.githubusercontent.com/DanceNitra/inspeximus/main/"


def _icons():
    d = json.loads(SERVER_JSON.read_text(encoding="utf-8"))
    icons = d.get("icons")
    assert icons, "server.json declares no icons, so every MCP directory shows a placeholder"
    return icons


def test_a_raster_icon_is_offered_because_not_every_directory_renders_svg():
    kinds = {i.get("mimeType") for i in _icons()}
    assert "image/png" in kinds, "only SVG is offered; a directory that cannot render it shows nothing"


@pytest.mark.parametrize("icon", _icons())
def test_every_icon_url_points_at_a_file_in_this_repository(icon):
    src = icon["src"]
    assert src.startswith("https://"), "the schema requires HTTPS: %r" % src
    assert src.startswith(PREFIX), (
        "%r is not served from this repository, so nothing here can tell you when it breaks" % src)
    target = ROOT / src[len(PREFIX):]
    assert target.is_file(), (
        "%s points at %s, which does not exist. A directory would render a broken image."
        % (src, target.relative_to(ROOT)))
    assert target.stat().st_size > 0, "%s is empty" % target.relative_to(ROOT)


def test_the_raster_icon_is_square_and_large_enough_to_downscale():
    png = [i for i in _icons() if i.get("mimeType") == "image/png"][0]
    target = ROOT / png["src"][len(PREFIX):]
    Image = pytest.importorskip("PIL.Image", reason="Pillow is not installed here")
    w, h = Image.open(target).size
    assert w == h, "the icon is %dx%d; a non-square icon is letterboxed or stretched" % (w, h)
    assert w >= 256, "%dpx is too small to downscale cleanly to a 32px listing row" % w


def test_CONTROL_the_path_check_can_actually_fail():
    """Without this, a resolver that silently matched everything would pass the suite above."""
    missing = ROOT / "docs/assets/zzqqxx-not-an-icon-4f9a.png"
    assert not missing.is_file(), "the fixture is contaminated"
