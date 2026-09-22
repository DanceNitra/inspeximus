#!/usr/bin/env python
"""Mirror a site that is ALREADY public, and refuse to write anything that is not.

WHY MIRROR AT ALL. A second copy on infrastructure we control answers a question a customer will
ask: what happens to the published record when the publisher's host goes away. It is also real work
for a machine a cloud provider reclaims when it looks idle, and unlike synthetic load it produces
something somebody can read.

THE SOURCE IS THE PUBLISHED SITE, NOT THE REPOSITORY, and that is the whole safety argument. A
mirror built from a working tree can pick up a draft, a strategy note or an .env that was never
meant to leave the machine. A mirror built by fetching URLs can only contain what a stranger could
already fetch. The denylist below is a second line, not the first.

    python tools/mirror_public_site.py --base https://dancenitra.github.io/agora/ --out /srv/mirror

Writes the pages, then `mirror-status.json` beside them: what was fetched, what was refused and
why, and the sweep's verdict. Exit 0 only when every page fetched and the sweep found nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:                                              # noqa: BLE001
    _CTX = ssl.create_default_context()

#: Path fragments that must never appear in a mirror, even though the source is public. If one ever
#: does, the publisher leaked it and the mirror is the second place to notice.
DENY = ("agora_output/", "/strategy/", "/drafts/", ".env", "id_ed25519", "secret", "private",
        ".ots.key", "credentials", "handoff_")

#: What a private file looks like INSIDE a page, for the sweep. A mirror can be clean by path and
#: still carry a pasted key, so the sweep reads bytes as well as names.
PRIVATE_MARKERS = (
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"X-Vault-Token"),
    re.compile(r"(?i)\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9]{32,}\b"),
)

HREF = re.compile(r'(?:href|src)\s*=\s*["\']([^"\'#]+)', re.I)


def fetch(url: str, timeout: float = 30.0, attempts: int = 3):
    """One page, with retries. A transient read timeout is not a missing page.

    Measured on the first full run: one of sixty URLs timed out mid-read and was recorded as an
    error, while a plain curl returned it twice with 200. A mirror that reports a page missing
    because the network blinked teaches its reader to ignore the error list.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "inspeximus-mirror/1.0"})
    last = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout * (attempt + 1), context=_CTX) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError:
            raise                                                # a 404 is an answer, not a blink
        except Exception as exc:                                 # noqa: BLE001
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise last


def denied(url: str) -> str | None:
    lowered = url.lower()
    for frag in DENY:
        if frag in lowered:
            return frag
    return None


def local_path(out: str, base: str, url: str) -> str:
    """Where a URL lands on disk. A directory URL becomes index.html, as a web server expects."""
    rel = url[len(base):] if url.startswith(base) else urllib.parse.urlparse(url).path.lstrip("/")
    rel = rel.split("?")[0]
    if rel == "" or rel.endswith("/"):
        rel += "index.html"
    return os.path.join(out, rel.replace("/", os.sep))


def sweep(out: str) -> dict:
    """Read the mirrored tree: nothing whose PATH is denied, nothing whose BYTES look private."""
    by_path, by_content, files = [], [], 0
    for dirpath, dirnames, filenames in os.walk(out):
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, out).replace(os.sep, "/")
            files += 1
            frag = denied("/" + rel)
            if frag:
                by_path.append({"path": rel, "matched": frag})
                continue
            try:
                with open(path, "rb") as fh:
                    blob = fh.read(2 * 1024 * 1024)
            except Exception:                                    # noqa: BLE001
                continue
            text = blob.decode("utf-8", "replace")
            for pattern in PRIVATE_MARKERS:
                if pattern.search(text):
                    by_content.append({"path": rel, "matched": pattern.pattern[:40]})
                    break
    return {"files": files, "private_by_path": by_path, "private_by_content": by_content,
            "clean": not by_path and not by_content}


def mirror(base: str, out: str, max_pages: int = 400) -> dict:
    base = base if base.endswith("/") else base + "/"
    os.makedirs(out, exist_ok=True)
    started = time.time()

    queue, seen, saved, refused, errors = [base], set(), [], [], []
    try:
        _s, _c, xml = fetch(urllib.parse.urljoin(base, "sitemap.xml"))
        queue += re.findall(r"<loc>([^<]+)</loc>", xml.decode("utf-8", "replace"))
    except Exception as exc:                                     # noqa: BLE001
        errors.append("sitemap: %s" % str(exc)[:120])

    while queue and len(saved) < max_pages:
        url = queue.pop(0).split("#")[0]
        if url in seen or not url.startswith(base):
            continue
        seen.add(url)
        frag = denied(url)
        if frag:
            refused.append({"url": url, "matched": frag})
            continue
        try:
            status, ctype, body = fetch(url)
        except urllib.error.HTTPError as exc:
            errors.append("%s: HTTP %s" % (url, exc.code))
            continue
        except Exception as exc:                                 # noqa: BLE001
            errors.append("%s: %s" % (url, str(exc)[:100]))
            continue
        if status != 200:
            errors.append("%s: HTTP %d" % (url, status))
            continue
        path = local_path(out, base, url)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(body)
        saved.append({"url": url, "path": os.path.relpath(path, out).replace(os.sep, "/"),
                      "bytes": len(body)})
        if "html" in ctype.lower():
            for href in HREF.findall(body.decode("utf-8", "replace")):
                nxt = urllib.parse.urljoin(url, href).split("#")[0]
                if nxt.startswith(base) and nxt not in seen:
                    queue.append(nxt)

    verdict = sweep(out)
    status = {
        "kind": "inspeximus.public-mirror/1",
        "mirrored_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": base,
        "pages_saved": len(saved),
        "bytes": sum(s["bytes"] for s in saved),
        "refused_by_path": refused,
        "errors": errors,
        "sweep": verdict,
        "seconds": round(time.time() - started, 2),
        "scope": ("A copy of what this source already publishes to anyone. It is fetched over HTTP "
                  "rather than built from a working tree, so it cannot contain a file that was never "
                  "published. The sweep is the second line, not the first."),
    }
    with open(os.path.join(out, "mirror-status.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(status, fh, indent=2, sort_keys=True)
    return status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True, help="the public site to mirror")
    ap.add_argument("--out", required=True, help="where the mirror lands")
    ap.add_argument("--max-pages", type=int, default=400)
    a = ap.parse_args(argv)
    status = mirror(a.base, a.out, a.max_pages)
    print("%d pages, %.1f KB, %d refused, %d errors, sweep %s, %.2fs"
          % (status["pages_saved"], status["bytes"] / 1024.0, len(status["refused_by_path"]),
             len(status["errors"]), "clean" if status["sweep"]["clean"] else "DIRTY",
             status["seconds"]))
    for row in status["sweep"]["private_by_path"] + status["sweep"]["private_by_content"]:
        print("  PRIVATE: %s (%s)" % (row["path"], row["matched"]))
    return 0 if status["sweep"]["clean"] and status["pages_saved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
