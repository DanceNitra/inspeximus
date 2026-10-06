"""The one way inspeximus POSTs a body to an endpoint someone configured: an embedder or a distiller.

NO REDIRECT IS FOLLOWED (3.16.4, AUDIT-A). `urllib.request.urlopen` follows a 301, 302 or 303 by sending a new
request to the host the answer names. For a POST it drops the body, but it keeps the `Authorization` header, so
the bearer key and a request reached a host nobody configured; for a GET-shaped endpoint the text went too. A
loopback embedder that answers a redirect is how a repository's local URL (the only kind F-10 lets a repository
name) would reach another machine. A redirect is now an error, and the caller falls back the way it does for any
failed call: the hook to lexical recall, a distiller to its exception.

NO PROXY FOR THIS MACHINE. `urlopen` sends an http URL through $HTTP_PROXY (and https through $HTTPS_PROXY)
unless $NO_PROXY names the host, so a loopback URL went to the proxy with the text in the body. A loopback URL
is opened with no proxy at all. A URL to another host keeps the environment's proxy, which a network can need.
Zero dependencies: urllib only.
"""
import json
import urllib.error
import urllib.request


def is_loopback(url) -> bool:
    """True when `url`'s host is this machine: localhost, 127.0.0.0/8 or ::1.

    A backslash, whitespace or an `@` anywhere in the authority makes it False: `urlsplit` and `urllib.request`
    can read a different host from `http://evil.example\\@127.0.0.1/` (AUDIT-A), so a URL whose host two parsers
    could disagree on is not trusted. The host `urlsplit` reads must also be the host `urllib.request.Request`, which
    makes the connection, reads."""
    try:
        import ipaddress
        from urllib.parse import urlsplit
        url = str(url)
        authority = url.split("://", 1)[1].split("/", 1)[0] if "://" in url else ""
        if "\\" in url or "@" in authority or any(c.isspace() for c in url):
            return False
        host = (urlsplit(url).hostname or "").strip().lower()
        connects_to = (urlsplit("//" + urllib.request.Request(url).host).hostname or "").strip().lower()
        if not host or connects_to != host:
            return False
        if host == "localhost":
            return True
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class RedirectRefused(urllib.error.HTTPError):
    """The endpoint answered with a redirect, and inspeximus does not follow one."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RedirectRefused(req.full_url, code, "redirect to %s refused: inspeximus sends a body only to the "
                              "endpoint you configured" % newurl, headers, fp)


def opener_for(url):
    """An opener that follows no redirect, and that uses no proxy when `url` is on this machine."""
    handlers = [_NoRedirect()]
    if is_loopback(url):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def post_json(url, payload, headers=None, timeout=20):
    """POST `payload` as JSON to `url` and return the decoded JSON answer. Raises on any HTTP error, a redirect
    included, so every caller keeps its own fail-open path."""
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h)
    with opener_for(url).open(req, timeout=timeout) as r:
        return json.loads(r.read())
