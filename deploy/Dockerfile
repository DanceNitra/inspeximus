# A transparency service an operator does NOT run themselves.
#
# WHY HOSTING IS THE POINT AND NOT A CONVENIENCE. A log the audited party controls is not evidence
# against that party. Everything else in this package holds under an honest operator: the receipts
# verify, the heads chain, the timestamps are real. None of it stops an operator keeping two
# histories and showing each reader the one that suits. Only a party the operator cannot edit closes
# that, which is why this image exists and why the compose file beside it insists the witness is a
# separate container.
#
# Build:  docker build -f deploy/Dockerfile -t inspeximus-scrapi .
# Run:    docker run -p 9800:9800 -v scitt:/data \
#           -e INSPEXIMUS_SERVICE_SECRET=... inspeximus-scrapi
#
# The key arrives in the environment, never on the command line: a key passed as an argument is
# readable from the process table by any local user, measured 2026-08-31.

FROM python:3.12-slim AS base

# The service is stdlib-only apart from Ed25519. Nothing else is installed, because every dependency
# in a signing service is a party the operator is also trusting without being told.
RUN pip install --no-cache-dir "cryptography>=42"

WORKDIR /app
COPY inspeximus/ /app/inspeximus/
COPY README.md /app/

# A non-root user, and the data directory it owns. The log is append-only and its integrity is the
# product, so the process that serves reads must not be able to rewrite the filesystem around it.
RUN useradd --create-home --uid 10001 scitt \
    && mkdir -p /data && chown scitt:scitt /data
USER scitt
VOLUME ["/data"]

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 9800

# `--host 0.0.0.0` is correct INSIDE a container and wrong outside one: the container's network
# namespace is the boundary, and the published port is what decides who can reach it.
#
# There is no default issuer key on purpose. The service refuses to start without either
# --issuer-pubkey or --accept-any-issuer, because a service that cannot authenticate an issuer is
# recording bytes rather than statements, and a receipt from it means only that something arrived.
ENTRYPOINT ["python", "-m", "inspeximus.scrapi", "--host", "0.0.0.0", "--port", "9800", \
            "--log", "/data/registrations.log"]
CMD []
