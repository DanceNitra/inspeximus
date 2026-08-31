"""Reference witness HTTP server -- stdlib only, no web framework, no new dependency.

Stand up your OWN independent co-signing witness:

    python -m inspeximus.witness_server --port 9700 --state witness.json [--secret <hex>]

Endpoints:
    GET  /pubkey                     -> {"pubkey": <hex>}
    GET  /attest?store_id=<id>       -> a SIGNED statement of what this witness saw for that store:
                                        the last head it co-signed, when it last saw it, and what it
                                        REFUSED. Verify with witness_pool.verify_attestation(). This
                                        is the only surface in the product whose evidence does not
                                        come from the party being audited.
    POST /cosign  {store_id, anchor} -> 200 {"pubkey","sig"}   (co-signed)
                                     -> 409 {"refused": reason} (a fork/rollback -- the split-view defense)
    POST /bootstrap {store_id}       -> 200 {"bootstrapped": id}  -- ONLY with --strict, and ONLY with
                                        the shared secret from --bootstrap-token in X-Bootstrap-Token.
                                        Declares a legitimate first contact. Unauthenticated it would
                                        defeat --strict entirely: anyone could declare any store id
                                        and the witness would co-sign a rollback it had forgotten.

This is a REFERENCE you run on an INDEPENDENT host/party, not a hosted service. A client gathers k-of-n
co-signatures (inspeximus.witness_pool.collect_cosignatures with inspeximus.witness_pool.http_witness(url))
and a forked head cannot reach threshold because honest witnesses refuse it. The witness persists its per-store
last-signed head to `--state` so the refusal survives a restart. Bind to 127.0.0.1 by default; put it behind
your own TLS/reverse-proxy for a real deployment.
"""
from __future__ import annotations
import json, argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .witness_pool import Witness


def _make_handler(witness: Witness, bootstrap_token: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, obj: dict):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") == "/pubkey":
                self._send(200, {"pubkey": witness.public})
                return
            # THE SURFACE AN AUDITOR ASKS. `attest()` is the whole point of a witness -- the one
            # statement in the product that does not come from the party being audited -- and it was
            # reachable from NO shipped interface: not the CLI, not the MCP server, not here. The
            # only caller who could reach it held the Witness object, i.e. the operator, i.e. exactly
            # the party it exists to bind. Measured on the candidate before this endpoint existed.
            #
            # The documented workaround -- construct a SECOND Witness over the running server's
            # --state file -- is worse than no route: it silently destroyed the first witness's fork
            # memory until the single-writer guard landed in this same release.
            #
            # GET, unauthenticated, and read-only on purpose: an attestation is signed, so it needs
            # no channel integrity, and anyone may ask what a witness saw. It reveals only what the
            # witness is FOR.
            if self.path.split("?")[0].rstrip("/") == "/attest":
                from urllib.parse import parse_qs, urlparse
                _q = parse_qs(urlparse(self.path).query)
                _sid = (_q.get("store_id") or [""])[0]
                if not _sid:
                    self._send(400, {"error": "attest needs ?store_id=<id>; a witness has nothing to "
                                              "say about a store nobody named"})
                    return
                self._send(200, witness.attest(_sid))
                return
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            # DRAIN THE BODY BEFORE ANSWERING, on every path including the refusals.
            #
            # BaseHTTPRequestHandler does not consume the request body for you, and a response sent
            # while unread bytes are still in flight makes the client see a CONNECTION RESET rather
            # than the response. Measured: POST /bootstrap with no token raised
            # ConnectionResetError(10054) client-side, so the 403 explaining how to enable the route
            # -- the entire value of that branch -- was never delivered. A helpful error nobody
            # receives is a hang with extra steps.
            try:
                _raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            except Exception:
                _raw = b""

            # A MALFORMED BODY IS A 400, NOT A 500. Both routes parsed inside a broad `except
            # Exception -> 500 {type}: {message}`, so `not json` from an unauthenticated caller
            # produced a server error carrying an exception class and text. On the one endpoint a
            # stranger can reach, that is a free error-shaped oracle and an ops alarm they can pull
            # at will; it is also simply wrong, since the fault is theirs.
            try:
                _body = json.loads(_raw or b"{}")
                if not isinstance(_body, dict):
                    raise ValueError("not an object")
            except Exception:
                self._send(400, {"error": "body must be a JSON object"})
                return

            if self.path.rstrip("/") == "/bootstrap":
                # THE ONE WRITE ENDPOINT THAT IS AUTHENTICATED, because it is the only one that can
                # WEAKEN the witness. /cosign cannot: the worst an unauthenticated caller does there
                # is record a head or a refusal, and both are facts. A bootstrap tells a strict
                # witness "you have never seen this store, that is fine" -- which is precisely the
                # sentence an operator laundering a rollback needs it to believe.
                #
                # Without --strict there is nothing to declare, and saying so beats accepting a call
                # that does nothing.
                if not witness._strict:
                    self._send(400, {"error": "this witness is not strict, so first contact needs no "
                                              "declaration and /bootstrap would be a no-op"})
                    return
                if not bootstrap_token:
                    self._send(403, {"error": "no bootstrap token is configured on this witness: "
                                              "start it with --bootstrap-token to enable /bootstrap, "
                                              "or bootstrap offline with `inspeximus witness "
                                              "bootstrap` while the server is stopped"})
                    return
                import hmac
                if not hmac.compare_digest(str(self.headers.get("X-Bootstrap-Token") or ""),
                                           str(bootstrap_token)):
                    self._send(403, {"error": "bad or missing X-Bootstrap-Token"})
                    return
                try:
                    sid = _body.get("store_id")
                    if not sid:
                        self._send(400, {"error": "need {store_id}"}); return
                    witness.bootstrap(sid)
                    self._send(200, {"bootstrapped": sid})
                except Exception as e:
                    self._send(500, {"error": f"{type(e).__name__}: {e}"})
                return
            if self.path.rstrip("/") != "/cosign":
                self._send(404, {"error": "not found"}); return
            try:
                store_id, anchor = _body.get("store_id"), _body.get("anchor")
                if not store_id or not isinstance(anchor, dict):
                    self._send(400, {"error": "need {store_id, anchor}"}); return
                pk, sig = witness.cosign(store_id, anchor)
                self._send(200, {"pubkey": pk, "sig": sig})
            except ValueError as e:                       # witness refused a fork/rollback
                self._send(409, {"refused": str(e)})
            except Exception as e:
                self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def log_message(self, *a):                        # quiet by default
            pass
    return Handler


def make_server(port: int = 9700, host: str = "127.0.0.1", state_path: str | None = None,
                secret_hex: str | None = None, strict: bool = False,
                require_authenticated_state: bool = False, bootstrap_token: str | None = None):
    """BUILD the witness server without starting it, and return (server, witness).

    `serve()` binds and blocks, so a caller can never learn which port it got. That is fine for an
    operator who chose the number and fatal for anything running several witnesses at once: the tests
    picked fixed ports, collided under pytest-xdist, and then slept 1.2 seconds hoping the thread had
    bound. Both problems are the same missing capability.

    Pass port=0 to let the OS choose and read `server.server_address[1]`. The socket is already bound
    when this returns, so there is nothing to wait for.
    """
    w = Witness(secret_hex=secret_hex, state_path=state_path, strict=strict,
                require_authenticated_state=require_authenticated_state)
    return ThreadingHTTPServer((host, port), _make_handler(w, bootstrap_token)), w


def serve(port: int = 9700, host: str = "127.0.0.1", state_path: str | None = None,
          secret_hex: str | None = None, strict: bool = False,
          require_authenticated_state: bool = False, bootstrap_token: str | None = None):
    """Run a witness server (blocking). Returns never; Ctrl-C to stop.

    `strict` refuses a store this witness has no memory of, so deleting the state file stops being
    a way to launder a rollback -- amnesia IS the attack. `require_authenticated_state` refuses a
    fork-memory file with no MAC, which closes the stripped-MAC edit outright once every witness has
    persisted at least once.

    BOTH EXISTED AND NEITHER WAS REACHABLE. They were constructor arguments on `Witness` and no
    shipped interface passed them -- not this server, not the CLI, not MCP -- so the two hardening
    switches the code argues for at length were available only to someone importing the library.
    The same shape as `attest()` one round earlier: implemented, documented, unreachable.
    """
    httpd, w = make_server(port=port, host=host, state_path=state_path, secret_hex=secret_hex,
                           strict=strict,
                           require_authenticated_state=require_authenticated_state,
                           bootstrap_token=bootstrap_token)
    port = httpd.server_address[1]
    print(f"inspeximus witness on http://{host}:{port}  pubkey={w.public}", flush=True)
    print(f"  add to a client allowlist as: {w.public}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


def main():
    ap = argparse.ArgumentParser(description="inspeximus reference witness server")
    ap.add_argument("--port", type=int, default=9700)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--state", default=None, help="json file persisting the per-store last-signed head")
    ap.add_argument("--secret", default=None, help="Ed25519 secret hex (omit to mint a fresh key)")
    a = ap.parse_args()
    serve(a.port, a.host, a.state, a.secret)


if __name__ == "__main__":
    main()
