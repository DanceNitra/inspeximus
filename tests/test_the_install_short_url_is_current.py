"""/install is generated from docs/install/index.md, the page CI executes. A stale copy would hand an agent
commands nobody tested."""
import html
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "gen_install_page.py")


def test_the_short_url_page_matches_the_tested_markdown():
    r = subprocess.run([sys.executable, TOOL, "--check"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_page_carries_every_command_verbatim():
    page = open(os.path.join(ROOT, "install", "index.html"), encoding="utf-8").read()
    body = html.unescape(re.search(r'<pre id="install">(.*?)</pre>', page, re.S).group(1))
    md = open(os.path.join(ROOT, "docs", "install", "index.md"), encoding="utf-8").read().replace("\r\n", "\n")
    assert body == md
    assert "--rules RULES_ANSWER --hermes-provider HERMES_ANSWER" in body   # control: the commands are in it
