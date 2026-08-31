#!/usr/bin/env python
"""Create or update the Azure Container App that hosts the transparency service.

    python deploy/azure_up.py --key-file C:/Users/Danculus/inspeximus-deploy/service.key

Idempotent: every step checks for what it is about to create and skips it if it is already there, so
a second run after a failure continues rather than starting over.

WHAT IT REFUSES TO ASSUME. The first thing it does is make a real API call, because `az account show`
reads a local cache and reported this subscription as `Enabled` on 2026-08-31 while its refresh token
had been dead for months. A deployment script that trusted that would fail nine steps later with a
message about something else.

THE SIGNING KEY passes through `az containerapp` as a `--secrets` value, so it is briefly visible in
the process table OF THE MACHINE YOU RUN THIS ON. That machine already holds the key file, so the
marginal exposure is small, but it is real and it is not nothing: on a shared or CI machine, set the
secret from the portal instead and run this with --no-secret.

WHAT THIS DEPLOYS, AND WHAT IT DOES NOT. The service only. The witness stays off Azure on purpose: a
witness under our own subscription shares an operator with the service it is meant to check, and one
that shares an operator co-signs whatever it is shown. Witnesses are for other people to run.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

DEFAULTS = {
    "group": "inspeximus-transparency",
    "location": "swedencentral",
    "env": "inspeximus-env",
    "app": "inspeximus-scitt",
    "share": "scitt-log",
    "storage_link": "scittlog",
    "image": "ghcr.io/dancenitra/inspeximus-scitt:latest",
    "policy": "public-open-registration",
}


def say(*a):
    print(*a, flush=True)


def az(*args, check=True, parse=True, quiet=False):
    """Run an az command and return parsed JSON, or None.

    Reads the exit code directly rather than through a pipe: a pipeline hands back the LAST command's
    status, which has reported three separate failures here as success.
    """
    cmd = ["az", *args]
    if parse and "--output" not in args and "-o" not in args:
        cmd += ["--output", "json"]
    if not quiet:
        say("   $ az " + " ".join(a if "=" not in a or "secret" not in a.lower() else
                                 a.split("=", 1)[0] + "=<redacted>" for a in args)[:150])
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       shell=(os.name == "nt"))
    if r.returncode != 0:
        if check:
            say("\nFAILED: az " + " ".join(args[:4]))
            say((r.stderr or r.stdout or "").strip()[:900])
            raise SystemExit(2)
        return None
    if not parse:
        return r.stdout.strip()
    try:
        return json.loads(r.stdout or "null")
    except json.JSONDecodeError:
        return r.stdout.strip()


def require_live_session():
    say("[1/8] checking the Azure session with a real API call")
    got = az("group", "list", "--query", "length(@)", check=False, quiet=True)
    if got is None:
        say("\nThe Azure session is not usable. `az account show` can still print a subscription "
            "here: it reads a local cache and says nothing about whether the token works.\n"
            "Sign in first:\n\n    az login\n")
        raise SystemExit(2)
    sub = az("account", "show", "--query", "{name:name,id:id}", quiet=True)
    say("      signed in: %s (%s), %s resource groups visible" % (sub["name"], sub["id"][:8], got))
    return sub


def ensure_providers():
    say("[2/8] registering the resource providers this needs")
    for ns in ("Microsoft.App", "Microsoft.OperationalInsights", "Microsoft.Storage"):
        state = az("provider", "show", "-n", ns, "--query", "registrationState", quiet=True)
        if state == "Registered":
            say("      %-32s already registered" % ns)
            continue
        say("      %-32s registering, this takes a minute" % ns)
        az("provider", "register", "-n", ns, "--wait", parse=False)


def ensure_group(cfg):
    say("[3/8] resource group")
    if az("group", "show", "-n", cfg.group, check=False, quiet=True):
        say("      %s exists" % cfg.group)
        return
    az("group", "create", "-n", cfg.group, "-l", cfg.location)


def ensure_storage(cfg):
    """A file share for the log, because a container's own disk does not survive a revision.

    The append-only log IS the product. Losing it does not lose a cache, it loses every receipt this
    service has ever issued, and a service that cannot show its own history is not a transparency
    service.
    """
    say("[4/8] storage for the log")
    account = cfg.storage or ("scitt" + os.urandom(6).hex())
    existing = az("storage", "account", "list", "-g", cfg.group,
                  "--query", "[].name", quiet=True) or []
    if existing:
        account = existing[0]
        say("      reusing storage account %s" % account)
    else:
        say("      creating storage account %s" % account)
        az("storage", "account", "create", "-n", account, "-g", cfg.group, "-l", cfg.location,
           "--sku", "Standard_LRS", "--kind", "StorageV2", "--min-tls-version", "TLS1_2",
           "--allow-blob-public-access", "false")
    key = az("storage", "account", "keys", "list", "-n", account, "-g", cfg.group,
             "--query", "[0].value", quiet=True)
    shares = az("storage", "share", "list", "--account-name", account, "--account-key", key,
                "--query", "[].name", quiet=True) or []
    if cfg.share not in shares:
        az("storage", "share", "create", "-n", cfg.share, "--account-name", account,
           "--account-key", key, "--quota", "1", quiet=True)
        say("      created file share %s (1 GiB)" % cfg.share)
    else:
        say("      file share %s exists" % cfg.share)
    return account, key


def ensure_environment(cfg, account, key):
    say("[5/8] Container Apps environment")
    if not az("containerapp", "env", "show", "-n", cfg.env, "-g", cfg.group,
              check=False, quiet=True):
        az("containerapp", "env", "create", "-n", cfg.env, "-g", cfg.group, "-l", cfg.location)
    else:
        say("      %s exists" % cfg.env)
    say("      linking the file share into the environment")
    az("containerapp", "env", "storage", "set", "-n", cfg.env, "-g", cfg.group,
       "--storage-name", cfg.storage_link, "--azure-file-account-name", account,
       "--azure-file-account-key", key, "--azure-file-share-name", cfg.share,
       "--access-mode", "ReadWrite")


def app_yaml(cfg, secret_hex):
    """The app definition. Written as YAML because `az containerapp create` takes volume mounts only
    this way, and the log needs one."""
    # Indentation is load-bearing and was wrong once: `secrets` sat two columns left, which made it a
    # sibling of `properties` and turned the `ingress:` line after it into a member of the secrets
    # SEQUENCE. Python's yaml refused it outright; Azure would have refused it too, nine steps in,
    # with a message about something else. The generator is checked against a parser for that reason.
    secrets = ("    secrets:\n      - name: service-secret\n        value: %s\n" % secret_hex
               if secret_hex else "")
    env_var = ("        env:\n          - name: INSPEXIMUS_SERVICE_SECRET\n"
               "            secretRef: service-secret\n" if secret_hex else "")
    return """properties:
  managedEnvironmentId: %(env_id)s
  configuration:
    activeRevisionsMode: Single
%(secrets)s    ingress:
      external: true
      targetPort: 9800
      transport: auto
      allowInsecure: false
      traffic:
        - latestRevision: true
          weight: 100
  template:
    containers:
      - name: scitt
        image: %(image)s
        args:
          - "--accept-any-issuer"
          - "--policy-name"
          - "%(policy)s"
%(env_var)s        resources:
          cpu: 0.25
          memory: 0.5Gi
        volumeMounts:
          - volumeName: scitt-log
            mountPath: /data
    volumes:
      - name: scitt-log
        storageType: AzureFile
        storageName: %(link)s
    scale:
      minReplicas: 1
      maxReplicas: 1
""" % {"env_id": cfg.env_id, "secrets": secrets, "image": cfg.image, "policy": cfg.policy,
       "env_var": env_var, "link": cfg.storage_link}


def ensure_app(cfg, secret_hex):
    say("[6/8] the container app")
    cfg.env_id = az("containerapp", "env", "show", "-n", cfg.env, "-g", cfg.group,
                    "--query", "id", quiet=True)
    path = os.path.join(HERE, "_containerapp.generated.yaml")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(app_yaml(cfg, secret_hex))
    exists = az("containerapp", "show", "-n", cfg.app, "-g", cfg.group, check=False, quiet=True)
    verb = "update" if exists else "create"
    say("      %s %s from %s" % (verb, cfg.app, os.path.basename(path)))
    az("containerapp", verb, "-n", cfg.app, "-g", cfg.group, "--yaml", path)
    # `minReplicas: 1` costs about EUR 4.21 a month at idle rates, measured from the Azure retail
    # price API on 2026-08-31. Scaling to zero is cheaper and wrong here: a log that is only up when
    # someone is already talking to it cannot be polled by a witness or an auditor.
    os.remove(path)


def wait_for_fqdn(cfg, attempts=30):
    say("[7/8] waiting for the public address")
    for i in range(attempts):
        fqdn = az("containerapp", "show", "-n", cfg.app, "-g", cfg.group,
                  "--query", "properties.configuration.ingress.fqdn", check=False, quiet=True)
        if fqdn:
            say("      https://%s" % fqdn)
            return fqdn
        say("      not ready yet (%d/%d)" % (i + 1, attempts))
        time.sleep(10)
    raise SystemExit("the app never reported an ingress address")


def verify(fqdn, key_file):
    """Prove the deployment serves what it is supposed to, rather than that it returned 200.

    Checks the published key against the key on disk. A service answering 200 with a key nobody holds
    is a service that cannot verify its own receipts, and that failure looks exactly like success
    from the outside.
    """
    say("[8/8] verifying the deployment")
    from inspeximus.cose import decode as cbor
    url = "https://%s/.well-known/scitt-keys" % fqdn
    doc = None
    for i in range(24):
        try:
            doc = cbor(urllib.request.urlopen(url, timeout=25).read())
            break
        except Exception as e:                                          # noqa: BLE001
            say("      not answering yet (%d/24): %s" % (i + 1, type(e).__name__))
            time.sleep(10)
    if doc is None:
        raise SystemExit("the service never answered at %s" % url)

    k = doc["keys"][0]
    ok_shape = k.get(1) == 1 and k.get(3) == -8 and k.get(-1) == 6 and len(k.get(-2, b"")) == 32
    say("      COSE_Key served: %s" % ("kty=OKP alg=EdDSA crv=Ed25519, 32-byte x" if ok_shape
                                       else "MALFORMED: %r" % (sorted(map(str, k)),)))
    if key_file and os.path.exists(key_file):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as SK
        with open(key_file, encoding="utf-8") as fh:
            want = SK.from_private_bytes(bytes.fromhex(fh.read().strip()))
        match = k.get(-2) == want.public_key().public_bytes_raw()
        say("      published key is the one in %s: %s" % (os.path.basename(key_file), match))
        if not match:
            raise SystemExit("the deployed service is signing with a key we do not hold")
    say("      policy: %s | entries: %s" % (doc["policy"]["name"], doc["entries"]))
    w = doc.get("witnessed") or {}
    say("      witnesses: %s" % (w.get("note") or "met=%s signers=%s" %
                                 (w.get("met"), len(w.get("signers") or []))))
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for k, v in DEFAULTS.items():
        ap.add_argument("--" + k.replace("_", "-"), default=v)
    ap.add_argument("--storage", default=None, help="storage account name (default: generated)")
    ap.add_argument("--key-file", default=None,
                    help="file holding the service Ed25519 secret hex. Without it the service mints "
                         "a key on every revision, so its receipts stop chaining to the old ones")
    ap.add_argument("--no-secret", action="store_true",
                    help="do not pass the key through az; set it in the portal instead")
    cfg = ap.parse_args(argv)

    secret_hex = None
    if not cfg.no_secret:
        if not cfg.key_file:
            say("refusing to start: pass --key-file, or --no-secret to say out loud that this "
                "revision will mint a key nobody has a copy of")
            return 2
        with open(cfg.key_file, encoding="utf-8") as fh:
            secret_hex = fh.read().strip()
        if len(secret_hex) != 64:
            say("the key file does not hold 64 hex characters; refusing to deploy a key we cannot read")
            return 2

    started = time.time()
    require_live_session()
    ensure_providers()
    ensure_group(cfg)
    account, key = ensure_storage(cfg)
    ensure_environment(cfg, account, key)
    ensure_app(cfg, secret_hex)
    fqdn = wait_for_fqdn(cfg)
    verify(fqdn, cfg.key_file)

    say("")
    say("deployed in %.0fs: https://%s" % (time.time() - started, fqdn))
    say("  key set : https://%s/.well-known/scitt-keys" % fqdn)
    say("  register: POST https://%s/entries  (Content-Type: application/cose)" % fqdn)
    say("")
    say("This service has NO witness. Nothing it serves is evidence against equivocation until "
        "somebody who is not us runs one and it is added to the configuration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
