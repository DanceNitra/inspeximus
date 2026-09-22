# RUNBOOK: the hosted transparency service (`inspeximus-log`)

What runs, where the keys are, how to rebuild it in ten minutes, and what to do when the IP that may
reach SSH changes. Written 2026-09-22 from the first deployment; every command here was run once on
that day and the acceptance line beside it is what it printed.

## What runs

| piece | where | notes |
|---|---|---|
| host | Oracle Cloud Always Free, tenancy `xadoxado`, Frankfurt, `VM.Standard.E2.1.Micro`, Ubuntu 24.04, 2 vCPU, 954 MB RAM, 2 GB swapfile | reserved public IP `92.5.74.17`; Oracle reclaims idle Free Tier instances, which is why this file exists |
| SCRAPI service | container `scitt`, `python -m inspeximus.scrapi`, port 9800 on the compose network only | registration log at `/srv/scitt-data/registrations.log` (host bind mount, owner uid 10001) |
| witness | container `witness`, `python -m inspeximus.witness_server`, port 9810 on the compose network only | same operator as the service, so its co-signature is a tripwire, not independence |
| TLS front | container `caddy`, `caddy:2`, ports 443 and 80 | Let's Encrypt certificate for `92.5.74.17.sslip.io` (TLS-ALPN on 443; 80 is closed at the firewall and the certificate still issued) |
| static copy | `/srv/static-log`, served at `https://92.5.74.17.sslip.io/log/` | written daily by root cron `17 3 * * *` from `/usr/local/bin/publish-static-log.sh` |
| mirror + tripwire witness | `DanceNitra/agora` `.github/workflows/witness.yml`, job `hosted-log` | pulls `/log/` over HTTPS daily and commits to `witness/hosted/`; the VM holds no repository credential |

Compose files: `deploy/compose.yaml` (the shape) plus `deploy/compose.prod.yaml` (secret files, the
host bind mount, Caddy) and `deploy/Caddyfile`, all under `/opt/inspeximus`, checked out at the
release tag (`git describe --tags` says which).

## Keys

| key | file on the host | owner:mode | held elsewhere |
|---|---|---|---|
| service signing key (Ed25519 secret, hex) | `/etc/inspeximus/service.secret` | `10001:10001`, `0400` | the owner's password manager and one off-machine backup (handed over 2026-09-22) |
| witness key | `/etc/inspeximus/witness.secret` | `10002:10002`, `0400` | same |

The containers read them with `--secret-file /run/secrets/...`; nothing puts a key in an environment
variable or on a command line, with one exception: the root-only cron publisher reads the service
key into its own process environment for the seconds it runs, because `publish_static_log.py` mints
receipts with it. The files are owned by the container users rather than root because a compose
file secret keeps the host file's permissions and the containers run unprivileged.

Public halves: the service key's `kid` is in `https://92.5.74.17.sslip.io/.well-known/scitt-keys` and
in `/log/keys.json`; the witness's is listed under `witnessed.signers` in the same key set.

## Firewall

Three rules, learned by locking the box twice on the day it was built (2026-09-22).

**1. The restriction lives where you can reach it out of band, which is the OCI security list, not
the host.** On this image the console has no way in when SSH is closed: Run command on Ubuntu 24.04
sits at Accepted forever, a reboot restores the saved rules, and the serial console needs a password
nobody set. So a wrong host rule is unrecoverable and the instance has to be destroyed; a wrong
security-list rule is one click in the console. The host therefore allows SSH from anywhere and the
security list carries the address restriction (22 from the builder's address, 443 from `0.0.0.0/0`).
The second line of defence on the host is key-only SSH (`/etc/ssh/sshd_config.d/60-keyonly.conf`,
`passwordauthentication no`) plus `fail2ban`, not the packet filter.

The builder's machine and the owner's machine are the same address here, so an allow-list of "two
addresses" is one address, and there is no second way in if it changes. That is the whole reason the
host stays open.

**2. Insert, never append.** The Oracle image ends its INPUT chain with
`-A INPUT -j REJECT --reject-with icmp-host-prohibited`. A rule added with `-A` lands after it and
does nothing; a rule inserted at a position at or below the REJECT line works. Read the position,
do not assume it:

```sh
REJ=$(sudo iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')
sudo iptables -I INPUT "$REJ" -p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT
```

The first attempt inserted at positions 5 and 6 of a chain whose REJECT sat at 5, which put both
rules behind it, and the session that did it was the last one able to reach the box.

**3. Arm the revert BEFORE you apply, and prove a NEW connection before you cancel it.** The
current session's connection is ESTABLISHED and survives a rule that closes port 22 to everyone, so
"my shell still works" proves nothing.

```sh
sudo iptables-save > /tmp/rules.old
# ... build the new set ...
sudo iptables-save > /tmp/rules.new
sudo sh -c 'nohup sh -c "sleep 120; iptables-restore < /tmp/rules.old" >/dev/null 2>&1 &'
sudo iptables-restore < /tmp/rules.new
# from ANOTHER shell: ssh in fresh. Only then:
sudo pkill -f "sleep 120"
sudo sh -c "iptables-save > /etc/iptables/rules.v4"
```

**And read the persisted file, not the live chain.** On 2026-09-22 the save ran inside a command
the `pkill` had already killed, so the live chain carried the new rule and `/etc/iptables/rules.v4`
did not: a reboot would have applied the old set. Check both:

```sh
sudo iptables -S INPUT
sudo awk '/^\*filter/,/^COMMIT/' /etc/iptables/rules.v4 | grep -E '^-A INPUT'
```

## Never delete from a store with raw SQL

A `DELETE` issued outside the library removes a row and can silently change the CURRENT VALUE of a
key. Measured on the live Crew OS store on 2026-09-22: a cleanup deleted two records with raw SQL,
and one of them (`bedcef7981`) was the newest record under the key
`decision::inspeximus-act-coverage-state`. Removing it un-retired its predecessor, so the store
served a value that a later write had corrected away, and the corrected text is gone: a receipt
holds hashes, not content.

`verify_writes()` reported all three effects (two records "written but missing from the store", one
"RETIRED ... and it is ACTIVE again"), which is what the write chain exists for. The repair, in
order:

```python
m = Inspeximus(PATH, receipts=True)
m.declare_out_of_band_deletion(memory_id, actor, reason)     # one per deleted record
m.remember(current_value, key=the_key)                       # re-issue through the API
Inspeximus(PATH, receipts=True).verify_writes()              # a FRESH handle -> (True, [])
```

To remove a record, call `forget()` or `retire()`: both leave a tombstone and keep supersession
intact. Back up the store and every sidecar first, because the deleted text is not recoverable.

## Rebuild on a fresh instance (ten minutes)

Tested 2026-09-22 by rebuilding into a throwaway container on the host (see the acceptance report).

1. Create the instance from the same shape and image, attach the reserved IP `92.5.74.17`, add the
   builder's public key (`~/.ssh/inspeximus_oci.pub`). In the security list: 22 from the builder's
   IP, 443 from anywhere, nothing else.
2. On the host, as `ubuntu`:
   ```sh
   set -e; export DEBIAN_FRONTEND=noninteractive
   # SSH stays open at the host (rule 1 above); only 443 is added here
   REJ=$(sudo iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')
   sudo iptables -I INPUT "$REJ" -p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT
   sudo iptables -S INPUT                                  # 22 and 443 both ABOVE the REJECT line
   sudo sh -c "iptables-save > /etc/iptables/rules.v4"
   sudo awk '/^\*filter/,/^COMMIT/' /etc/iptables/rules.v4 | grep -E '^-A INPUT'   # and persisted
   sudo apt-get update -qq && sudo apt-get upgrade -y -qq
   sudo apt-get install -y -qq unattended-upgrades fail2ban ca-certificates curl gnupg python3-cryptography
   sudo systemctl enable --now fail2ban
   sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
   echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
   echo "PasswordAuthentication no" | sudo tee /etc/ssh/sshd_config.d/60-keyonly.conf && sudo systemctl reload ssh
   # Docker from the official repository
   sudo install -m 0755 -d /etc/apt/keyrings
   curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
   echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list
   sudo apt-get update -qq && sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   sudo docker run --rm hello-world | grep "Hello from Docker"
   ```
3. Keys: restore `/etc/inspeximus/service.secret` and `/etc/inspeximus/witness.secret` from the
   password manager (one hex line each), `chown 10001:10001` and `10002:10002`, `chmod 0400`.
   **Restoring the SAME service key is what keeps the log's identity**; a new key is a new log and
   every receipt issued before it verifies only against the old public key.
4. Data: restore `/srv/scitt-data/registrations.log` (owner `10001:10001`) from the latest mirror
   in `DanceNitra/agora` `witness/hosted/mirror/` (`log.jsonl` is the same entries; the registration
   log is the service's own file, so restore the service's backup if one exists, and otherwise start
   the log again and say so in `/log/index.html`). The witness state (`witness_state.json` in the
   witness volume) restores from a backup or starts empty, in which case the witness forgets every
   head it signed and cannot refuse a fork of the old history: back it up.
5. Deploy:
   ```sh
   sudo git clone --branch v3.6.0 https://github.com/DanceNitra/inspeximus.git /opt/inspeximus
   sudo chown -R ubuntu:ubuntu /opt/inspeximus && cd /opt/inspeximus
   # deploy/compose.prod.yaml and deploy/Caddyfile as committed in this directory
   sudo mkdir -p /srv/scitt-data /srv/static-log && sudo chown 10001:10001 /srv/scitt-data && sudo chown ubuntu:ubuntu /srv/static-log
   sudo docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml up -d --build
   sudo docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml ps
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:9800/.well-known/scitt-keys   # 200
   ```
6. Publisher: install `/usr/local/bin/publish-static-log.sh` (root, `0700`) and the root cron line
   `17 3 * * * /usr/local/bin/publish-static-log.sh >> /var/log/publish-static-log.log 2>&1`; run it
   once by hand and check `https://92.5.74.17.sslip.io/log/head.json`.
7. Acceptance from another machine: `python probes/register_against_the_hosted_log.py
   https://92.5.74.17.sslip.io` exits 0 (201, receipt verifies against the published root with the
   leaf, control rejected); `inspeximus witness watch --url https://92.5.74.17.sslip.io/log --state s.json`
   answers EXTENDS against the state the mirror job holds.

## Monitoring

UptimeRobot (free tier), HTTPS monitor on `https://92.5.74.17.sslip.io/.well-known/scitt-keys` every
5 minutes, alerts to the owner's email. The measured uptime goes into the weekly channel brief; no
number is promised anywhere.

## What this deployment does not do

- The witness runs under the same operator as the service. Its co-signature says the service did
  not fork its own history in front of it, and nothing more; three external witnesses (plan 3.3) are
  the property.
- `--accept-any-issuer`: a receipt says a statement was recorded, never that its issuer was vetted.
  The policy name in every receipt (`inspeximus-log-frankfurt`) says so.
- `sslip.io` resolves the IP into a hostname anyone can claim for their own IP; the certificate is
  for that hostname. A domain the owner holds replaces it in `deploy/Caddyfile` in one line.
