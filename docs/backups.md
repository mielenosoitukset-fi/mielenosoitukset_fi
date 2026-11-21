# MongoDB backups (OPS-01)

## Current state (updated 17 November 2026)

- The database runs on **lc-db** (`/var/lib/mongodb`, 4.1 GB including other
  databases). Nightly core and weekly full dumps run on **lc-main** and have
  done so since 13 November 2026 (timers installed, first full dump Sunday
  15 November). The archives are on lc-main's `/var/backups` disk, a different
  machine from the database.
- Restoring from them is still **unproven** until the OPS-02 drill.
- **Units ship with every production deploy (20 November 2026).**
  `production_deploy.sh` runs `deploy/backup/install_units.sh`, which copies
  changed backup units into `/etc/systemd/system`, reloads systemd and enables
  the timers. Nobody copies units by hand any more. So the failure alerts (Bark,
  then e-mail) and the daily local freshness check are live after the first
  deploy that contains this change.
- **Off-site copy** (below) is installed with them but **skipped until the
  maintainer's public age key** is in `/etc/mielenosoitukset/backup-recipients.txt`
  (`ConditionPathExists=`; a skipped unit is not a failure and does not alert).
  Until the homelab side and the key exist, the archives are on lc-main alone.
- lc-main's `luovasec-snapshot-mielenosoitukset` timer is a *static HTML
  snapshot* of public pages for a fallback site, not a data backup. It fails
  every night in its own link check (missing `/app-assets/*` files and links to
  `/submit` and `/users/auth/*`) and has no `OnFailure` alert. That tool is
  maintained outside this repository.
- Whether Proxmox takes scheduled VM backups of lc-db was **not checked**. If it
  does, those are crash-consistent disk images; a logical dump is still needed
  for clean, per-database restores.

## What this repository provides

`deploy/backup/` holds a ready-to-install nightly backup that runs on **lc-main**
and dumps the database over the network from lc-db, using the same `MONGO_URI`
and `MONGO_DBNAME` as the app (`/var/www/mielenosoitukset_fi/config.yaml`).
The archives therefore live on a different machine from the database.

- `mongodb-backup.sh`:
  - writes `/var/backups/mielenosoitukset/<db>-<mode>-<UTC time>.archive.gz`.
    Mode `core` (nightly) skips `super_audit_logs` and `admin_logs`, which are
    about 13.6 GB uncompressed together (most of the database). Mode `full`
    (weekly) includes them. A full dump read for more than 20 minutes and
    loaded lc-db enough that a Go API restart during it failed its startup
    database check (11 November 2026), so full dumps run only once a week, at
    night;
  - requests `zstd` wire compression for weekly full reads when the app URI
    does not already select compressors. This reduces network traffic for the
    audit logs without changing the app config, credentials or archive format.
    Core reads and explicitly configured compression keep their existing behavior;
  - checks every archive's full gzip stream (CRC over every byte) and that
    `mongorestore --dryRun` can parse it. The dry run alone does not read
    collection data, so the restore drill below remains the real proof;
  - removes unfinished `.partial` files from failed or killed runs;
  - keeps 14 days of core archives and 35 days of full archives. Each mode only
    prunes its own files.
  - The URI is passed through a mode-0600 temporary config file, never on the
    command line.
- `mielenosoitukset-mongodb-backup.service` / `.timer`: core dump Monday to
  Saturday at 01:30 UTC (03:30 Helsinki in winter), with `Persistent=true` so a
  missed run catches up.
- `mielenosoitukset-mongodb-backup-full.service` / `.timer`: full dump on
  Sundays at 01:30 UTC (03:30 Helsinki in winter), so it never overlaps a core dump.
- `backup-age-check.sh` / `mielenosoitukset-backup-age-check.service` / `.timer`:
  at 06:20 UTC daily, fails (and so alerts) when the newest finished archive of
  any mode is older than `BACKUP_STALE_HOURS` (30, one missed night) or the newest
  full archive is older than `BACKUP_STALE_HOURS_FULL` (192, one missed Sunday). A
  failed dump alerts on its own; this catches a dump that never started (timer
  stopped, host down at night).
- `install_units.sh`: run by the production deploy, see "Install" below.
- `mielenosoitukset-backup-failed.service`: the `OnFailure=` hook of every unit
  here. It writes an error-level journal line tagged `mielenosoitukset-backup`
  and e-mails the maintainer through `backup-notify.py` (see Alerts).

Expected size: the dump is gzip-compressed BSON, typically well under the
on-disk size. Check the first archive and adjust `KEEP_DAYS` if lc-main's disk
(about 70 GB free, 868 MB used on 17 November 2026) gets tight.

### Gentle dumps (17 November 2026)

An ad-hoc core dump on 17 November 2026 (09:34-09:43 Helsinki) pulled the data
from lc-db as fast as the network allowed. lc-db's `ksoftirqd` reached 100 %, ping
rose to 443 ms with 50 % loss, database reads timed out, the public lockdown gate
failed closed and the site answered 503. The maintainer's rule: **ship a bit
slower rather than let the site go down.** The script now:

- **throttles** the archive stream through `pv` at `BACKUP_RATE_LIMIT` (default
  `1m`, `0` turns it off). The cap applies to the *compressed* archive, so the raw
  BSON on the wire is several times higher: about 4 MiB/s at a typical 4:1 ratio.
  **That ratio is not measured**; the first watched run must read lc-db's network
  receive rate and adjust the cap. A slow reader makes TCP back-pressure
  `mongodump`;
- reads one collection at a time (`--numParallelCollections=1`, mongodump's own
  default is 4);
- **refuses to start outside 03:00-04:59 Helsinki time** (exit 5) unless
  `BACKUP_ANYTIME=1`. The timers (01:30 UTC, Sunday full and Monday to Saturday
  core) fall inside the window in winter and summer. A catch-up after downtime
  therefore waits for the next night;
- **pre-checks lc-db** with five pings: an average above `PRECHECK_MAX_MS` (50) or
  any loss means up to three tries five minutes apart, then it **skips the backup
  (exit 4) and the alert mail says so**;
- never queues behind a running dump (exit 3);
- refuses to run unthrottled when `pv` is missing (installed on lc-main).

**Run-time limits.** `RuntimeMaxSec` kills a core dump after 1 hour, a full dump
after 90 minutes and a copy after 1 hour, and the failure mail follows. Sizes
behind these: core about 61 MB, full about 420 MB compressed, so about 1 and 7
minutes at 1 MiB/s.

**Never set `BACKUP_ANYTIME` in `backup.env` or a unit.** It exists only for a
supervised test run, and the night window is the protection.

**No ad-hoc daytime dumps.** Start a manual backup only at night with
`systemctl start mielenosoitukset-mongodb-backup.service`. Before a data change
(an activation, a migration) use the latest nightly archive as the backup, plus a
`mongoexport` of only the collections that will be touched, never a fresh dump.

**Not built: dumping on lc-db itself.** lc-db has `mongodump` (checked read-only
17 November 2026). Running it there over the local socket with `nice`/`ionice`,
then pulling the finished archive with `rsync --bwlimit`, would remove the network
read from the database host's critical path. It needs a script, secrets handling
and a timer on lc-db, so it is the next step if the throttled run is still not
gentle enough. **Weekly full dump:** it reads the 13.6 GB audit logs; if the
measured run is still not gentle, change `OnCalendar` of the full timer to the
first Sunday of the month (`Sun *-*-1..7 01:30:00 UTC`).

## Off-site copy, pull model (production since 21 November 2026)

Production moved from lc-main to **lc-moso-01** (`moso-prod`, 10.20.0.20 behind the
AX41 jump host) on 20 November 2026; the deploy and these units now run there. The
off-site machine (the maintainer's homelab) has no inbound port and lc-moso-01 is
not on its private network, so production cannot push. The homelab **pulls**
instead, the same way it already pulls the Hallinto backups:

1. After each verified dump, `offsite-copy.sh` runs in outbox mode
   (`OFFSITE_OUTBOX`, set in `mielenosoitukset-offsite-copy.service`): it encrypts the
   archive with age to the public recipients in `deploy/backup/offsite-recipients.txt`
   (either of the maintainer's two offline keys decrypts), writes the `.sha256` and
   puts both, mode 0640, in `/var/backups/mielenosoitukset-outbox`. It keeps them
   there for 3 days (`OFFSITE_OUTBOX_DAYS`): the puller re-reads the whole outbox daily. Nothing unencrypted leaves the host.
2. `install_units.sh` (every deploy) installs the recipients file, creates the
   locked system user `moso-offsite`, and installs its root-owned
   `authorized_keys` from `deploy/backup/offsite-pull.authorized_keys`: the homelab's
   existing pull key, restricted to `rrsync -ro` on the outbox. That key can only
   read encrypted files; it cannot write, list anything else or get a shell. It also
   installs `age` and `rsync` (for `rrsync`) when missing, and warns in the deploy
   log if sshd's `AllowUsers`/`AllowGroups` would refuse `moso-offsite`.
3. The homelab pulls daily through the existing restricted jump hop into its
   `incoming/`, and `offsite-housekeeping.sh` moves verified files into the vault and
   alerts (Bark) when no fresh archive arrives. The homelab side is set up outside
   this repository.

Revoke: remove the key line from `offsite-pull.authorized_keys` (next deploy), or
`usermod -L -e 1 moso-offsite`. The 20 November push setup on lc-main (upload key,
`backup.env`, the homelab's port 2222 and the `mosobackup` upload key) is no longer
used. lc-main's `moso-offsite-pull` timer (incident stage 1, unencrypted pull of
lc-moso-01's archives to lc-main) is separate and outside this repository.

## Off-site copy (decided by the maintainer on 17 November 2026; push mode)

Decisions: the off-site copy is an **encrypted copy to the maintainer's homelab**;
the **maintainer holds the encryption key**; backup failures **alert by e-mail
to the maintainer**. Rationale and revert: [decisions.md](decisions.md).

How it works:

1. When a core or full dump finishes and verifies, the backup service starts
   `mielenosoitukset-offsite-copy.service` (`OnSuccess=`). A daily timer at
   01:50 UTC (03:50 Helsinki in winter; not persistent, so never by day) retries
   anything that failed or was missed.
2. `offsite-copy.sh` encrypts each unsent archive from the last 3 days with
   [age](https://age-encryption.org) to the **public** recipient keys in
   `/etc/mielenosoitukset/backup-recipients.txt`, writes a SHA-256 checksum
   file, and uploads both with rsync over SSH, capped at `OFFSITE_BWLIMIT` (default 2048 KiB/s, about 2 MiB/s) to `OFFSITE_TARGET`. rsync
   verifies each transferred file. Only after a successful upload does it record
   the archive in a ledger (`/var/lib/mielenosoitukset-backup/sent`) and a
   per-mode stamp. The encrypted staging file is deleted after each upload.
3. The **private key never exists on lc-main**: the maintainer generates it on
   their own machine and only the public key is copied to the server. The script
   refuses to run if the recipients file contains a private key. If lc-main or
   the off-site disk is stolen, the archives cannot be read. If the maintainer
   loses the private key, the off-site archives cannot be read by anyone, so keep
   a second copy of it (for example in a password manager and on an offline
   medium).
4. The upload key on lc-main is restricted on the homelab to write-only access to
   one landing directory (`incoming/`). A compromised lc-main can drop or
   overwrite files there, but cannot read, list or delete the vault.
   `offsite-housekeeping.sh` runs **on the homelab**: it moves archives whose
   checksum matches from `incoming/` into a vault directory (never overwriting a
   file already in the vault), applies the **off-site retention** (30 days of
   core archives, 90 days of full archives, the newest of each mode is always
   kept) and reports when no fresh archive arrives.
5. **Freshness**: `mielenosoitukset-offsite-check.timer` (06:20 UTC) fails, and
   so e-mails, when the last uploaded core archive is older than 36 hours
   (`OFFSITE_STALE_HOURS`) or the last full archive older than 9 days
   (`OFFSITE_STALE_HOURS_FULL`). A fresh install counts from its first check, so
   it is not blamed for the days before the first upload. The homelab-side
   housekeeping run checks freshness too, independently of lc-main, so a dead
   lc-main is noticed as well.

Disk: encryption needs one archive's worth of free space in
`/var/backups/mielenosoitukset/offsite-staging` (lc-main has about 70 GB free).

### Alerts

`backup-notify.py` alerts once for each failed unit: a dump failure, an off-site
copy failure, or a stale off-site copy. Channels, in order:

1. **Bark push (iOS)** when a Bark key is set. On lc-main the key is already in
   `/etc/mielenosoitukset-alerts.env` (`BARK_DEVICE_KEY`, `BARK_API_BASE`,
   `BARK_ALERT_GROUP`, `BARK_ALERTS_ENABLED`), which the failure unit loads, so
   **the key is not copied or pasted anywhere**. `BARK_KEY` and `BARK_URL` in
   `backup.env` are alternative names and override the shared file. The push is plain
   (not encrypted with `BARK_ENCRYPTION_KEY`), because it says only the label (`ALERT_LABEL`, default `mielenosoitukset.fi`) and the
   name of the failed check, with no log lines and no host names. `BARK_ALERTS_ENABLED=false`
   turns Bark off; the server base (default `https://api.day.app`) must be HTTPS, the request
   times out after 10 seconds, and a failure logs only the exception type, never the
   key or URL.
2. **E-mail** to `ALERT_EMAIL`, when Bark is not configured or failed. It uses the
   site's SMTP account (the `MAIL:` section of
   `/var/www/mielenosoitukset_fi/config.yaml`) with the local `sendmail` as a fallback,
   and names the unit and host only.

If no channel is configured, or every configured one fails, `backup-notify.py` exits
non-zero, so `mielenosoitukset-backup-failed.service` itself shows as failed. The journal line
stays either way (`journalctl -u mielenosoitukset-backup-failed -n 20`). A stale copy
alerts every day until fixed.

**Setting the Bark key:** nothing to do on lc-main; the existing shared file is used.
To use another key for backups only, add `BARK_KEY=<key>` to
`/etc/mielenosoitukset/backup.env` yourself (mode 0600, never in chat or the
repository). On another host (for example lc-db for the NIC watcher, #1208) the
key must exist there. Copy only the four lines that are needed, server to server
at install time, without printing them:

```sh
ssh <other host> 'install -m 0600 /dev/null /etc/mielenosoitukset-alerts.env'
ssh lc-main-root "grep -E '^BARK_(DEVICE_KEY|API_BASE|ALERT_GROUP|ALERTS_ENABLED)=' /etc/mielenosoitukset-alerts.env" \
  | ssh <other host> 'umask 077; cat > /etc/mielenosoitukset-alerts.env'
```

Check on the target with `ls -l` (mode `-rw-------`, root) and count the lines with
`grep -c . <file>`; never `cat` it. Revert: delete that file there.

### Server settings file

`/etc/mielenosoitukset/backup.env` (root, mode 0600), read by the units. systemd
reads `KEY=value` lines only: no quotes, no comment after a value.

```sh
# Alert channel. Bark settings normally come from /etc/mielenosoitukset-alerts.env;
# set these only to override them. E-mail is the fallback.
# BARK_KEY=<device key>
# ALERT_EMAIL=<maintainer address>
# BARK_URL=https://api.day.app
# ALERT_LABEL=mielenosoitukset.fi
# The path is relative to the rrsync directory, which is incoming/.
OFFSITE_TARGET=<user>@<homelab host>:./
OFFSITE_PORT=22
# Optional, defaults in parentheses:
# OFFSITE_RECIPIENTS=/etc/mielenosoitukset/backup-recipients.txt
# OFFSITE_SSH_KEY=/etc/mielenosoitukset/offsite_ed25519
# OFFSITE_KNOWN_HOSTS=/etc/mielenosoitukset/offsite_known_hosts
# OFFSITE_BWLIMIT=2048
# OFFSITE_SEND_DAYS=3
# OFFSITE_STALE_HOURS=36
# OFFSITE_STALE_HOURS_FULL=216
```

The other files in `/etc/mielenosoitukset/` (mode 0600, root): the upload
private key `offsite_ed25519`, `offsite_known_hosts` (the homelab's host key,
checked strictly) and `backup-recipients.txt` (public key lines only).

## Install (production change: needs the maintainer's go-ahead)

The dump timers are already installed (13 November 2026). This section adds the
off-site copy, the alert mail and the gentler dump settings and timer times (copy
the changed `.service` and `.timer` files, then `systemctl daemon-reload`). Do it **outside the Sunday full-dump window**
(01:30 UTC; it takes about 7 minutes at the 1 MiB/s cap) and never restart public services while a full dump
runs. Installing units does not restart anything public.

1. **Homelab, once** (the maintainer; the specifics are kept out of the
   repository): create a dedicated user with two directories: `incoming/`, which the
   upload key may write to, and `vault/`, which that key cannot reach. Put the upload key's public half
   in that user's `authorized_keys` as
   `restrict,command="rrsync -wo -no-del <path to incoming>" ssh-ed25519 AAAA...`
   (rsync 3.2.7 on lc-main; check that `rrsync` on the homelab knows `-wo` and
   `-no-del`). Run `deploy/backup/offsite-housekeeping.sh` from cron there with
   `INCOMING` and `VAULT` set, and let cron mail its output to you.
2. **Key, on the maintainer's own machine** (never on lc-main, never in the
   repository, never in chat):

   ```sh
   age-keygen -o mielenosoitukset-backup.key
   ```

   It prints `Public key: age1...`. Keep `mielenosoitukset-backup.key` in a
   password manager **and** on an offline copy. Send only the `age1...` line.
3. **lc-main, as root:**

   ```sh
   apt install age
   install -d -m 0700 /etc/mielenosoitukset
   install -m 0600 /dev/null /etc/mielenosoitukset/backup-recipients.txt
   echo 'age1...public key...' >> /etc/mielenosoitukset/backup-recipients.txt
   ssh-keygen -t ed25519 -N '' -C mielenosoitukset-backup-upload -f /etc/mielenosoitukset/offsite_ed25519
   ssh-keyscan -p <port> <homelab host> > /etc/mielenosoitukset/offsite_known_hosts
   install -m 0600 /dev/null /etc/mielenosoitukset/backup.env   # then fill it in, see above
   ```

   Compare the fingerprint in `offsite_known_hosts` (`ssh-keygen -lf`) with the
   homelab's own before trusting it. Copy `offsite_ed25519.pub` to the homelab
   (step 1).
4. **Units:** nothing to copy. Every production deploy installs them
   (`deploy/backup/install_units.sh`) and the off-site timers are already enabled;
   their services start doing work once the recipients file from step 3 exists.
   The next night's dump starts the first copy. To see it:
   `journalctl -u mielenosoitukset-offsite-copy -n 20 --no-pager`.

5. **Test the alert once**, as root on lc-main:

   ```sh
   set -a; . /etc/mielenosoitukset/backup.env; set +a
   MONITOR_UNIT=test.service /var/www/mielenosoitukset_fi/deploy/backup/backup-notify.py
   ```

   Confirm the push (or mail) arrives. Then run the housekeeping script on the homelab and
   check that the first archive is accepted into the vault.
6. **Prove the key** and the restore: do the OPS-02 restore drill (first real restore
   from the off-site copy, see below) before relying on the off-site copy.

### Revert

To pause the off-site copy, remove `/etc/mielenosoitukset/backup-recipients.txt`:
both off-site units are then skipped again. To change or drop a unit, change it
in the repository; the next deploy installs it. The installer never deletes a
unit file, so removing a unit from the repository also needs
`systemctl disable --now <timer>` in that PR's rollout notes. The deploy's own
deploy log prints a `backup units:` line with what changed.

Remove the homelab user's `authorized_keys` line to revoke the upload key. Data
already copied off-site can be deleted on the homelab at any time.

The script needs `mongodump` and `mongorestore` (database tools 100.x, already on
lc-main) and Python with PyYAML (already used by the app). The off-site copy
also needs `age` (apt package) and rsync (installed).

## Restore

To a scratch database first. Never restore straight over production.

From the local archive:

```sh
mongorestore --config=<file with uri: "..."> --archive=/var/backups/mielenosoitukset/<file>.archive.gz --gzip \
  --nsFrom='mielenosoitukset.*' --nsTo='restore_check.*'
```

From the **off-site copy** (needs the private key, on the maintainer's machine or
a machine they trust; do this from the homelab vault file):

```sh
age -d -i mielenosoitukset-backup.key mielenosoitukset-core-<time>.archive.gz.age > restore.archive.gz
gzip -t restore.archive.gz
mongorestore --config=<file with uri: "..."> --archive=restore.archive.gz --gzip \
  --nsFrom='mielenosoitukset.*' --nsTo='restore_check.*'
```

Check the file against its `.sha256` first (`sha256sum -c`). Delete `restore.archive.gz`
afterwards: it is the unencrypted database.

Compare document counts against production, then drop `restore_check`. A real
recovery restores into `mielenosoitukset` only after the maintainer decides to.

## Still to decide (maintainer)

1. ~~Off-site copy, encryption key holder, alert channel~~ decided 17 November
   2026 (above). Open: the maintainer's go to install it, and the first
   decryption test.
2. **Recovery drill (OPS-02).** Restore one archive, local and off-site, into a
   scratch database, time it, and record the RPO (24 h with nightly dumps) and
   RTO.
3. **The static snapshot job** on lc-main: fix or silence its nightly failure,
   which is outside this repository.
