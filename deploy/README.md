# Running the transparency service

## Why anyone would host this

A log the audited party runs is not evidence against that party.

Everything else in this package holds under an honest operator. The receipts verify, the heads chain,
the timestamps come from a third party. None of it stops an operator keeping two histories and
showing each reader the one that suits. Only a party the operator cannot edit closes that gap, which
is what a hosted service is for, and why the witness here is a separate container.

## Start it

```bash
docker compose -f deploy/compose.yaml up --build
```

That gives you a SCRAPI service on `127.0.0.1:9800` and a witness it does not control. Verified end
to end on 2026-08-31: both images build, a Signed Statement registered, a 124-byte receipt came back,
and the witness co-signed the head with no refusals.

The compose file runs `--accept-any-issuer`, which is the development setting. The published policy
says so, and every receipt then means only that something was recorded, not that a known party said
it. For anything real, replace it with one `--issuer-pubkey` per client you accept.

## Keys

Both services read their key from the environment or a file, and mint one if neither is set:

```bash
INSPEXIMUS_SERVICE_SECRET   # or --secret-file
INSPEXIMUS_WITNESS_SECRET   # or --secret-file
```

Prefer a file. An environment is inherited by every child process and turns up in crash reports.

Do not use `--secret`. It puts the signing key in the process table, where any local user reads it
with one command; measured on 2026-08-31, a running server returned its own key to a second process.
The flag still works and warns, because removing it silently would break a running deployment.

The witness key must not be the service key. A witness holding the service's key is the service
wearing a second name, and its refusal is worth nothing.

## What the witness does, exactly

It remembers the last head it signed for each store and refuses a fork or a rollback of that history.
That is all. It does not check that anything registered is true, and it knows nothing about a log it
has never signed.

A refusal does not block registration, on purpose. Making a witness a veto hands anyone who can reach
it a way to stop the service. The refusal is published in `/.well-known/scitt-keys` instead, where a
dishonest operator cannot quietly drop it.

The state file in the witness volume is the memory that makes a refusal possible. Lose it and the
witness forgets every head it signed and co-signs a fork without complaint, because it has no way to
know it is one. Back it up.

## Before this is a service other people rely on

These are decisions, not configuration, and running a transparency service commits you to them:

- **Where it runs, and who can reach the host.** Bind behind TLS. This module does not implement
  HTTPS and the SCRAPI draft assumes it.
- **Who holds the signing key, and what happens when it is lost.** There is no key rotation here yet:
  `/.well-known/scitt-keys/{kid}` serves the whole key set because the service has exactly one key.
  Rotation needs that endpoint to narrow first.
- **Where the witness runs.** Another machine, under another account. Two containers on one host
  share an operator, and the separation is the guarantee.
- **What uptime you are willing to promise.** A client that cannot register cannot prove what it
  knew, and the failure lands on them.
- **How long the log is kept, and who pays for it.** An append-only log only grows.

Until those are answered, this is a service you can run for yourself, and that is a different product
from one other people depend on.
