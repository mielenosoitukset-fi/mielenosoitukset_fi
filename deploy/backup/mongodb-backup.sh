#!/usr/bin/env bash
# Nightly MongoDB backup for mielenosoitukset.fi (OPS-01).
#
# Writes one gzip archive per run, checks the whole compressed stream is intact
# (gzip CRC over every byte) and that mongorestore can parse it, then keeps the
# newest $KEEP_DAYS days of archives. A periodic restore drill (docs/backups.md)
# is still the real proof that a backup restores.
# The database URI is read from the Flask config and handed to the Mongo tools
# through a private config file, so the password never appears in argv or ps.
#
# Environment (all optional):
#   APP_CONFIG    Flask config.yaml   (default /var/www/mielenosoitukset_fi/config.yaml)
#   BACKUP_DIR    where archives go   (default /var/backups/mielenosoitukset)
#   BACKUP_MODE   core or full        (default core)
#                 core skips the bulky audit-log collections in EXCLUDE_COLLECTIONS;
#                 full dumps every collection. Archives are named <db>-<mode>-<time>.
#   EXCLUDE_COLLECTIONS  space-separated, core mode only
#                 (default "super_audit_logs admin_logs")
#   KEEP_DAYS     days to keep archives of this mode (default 14)
#   MONGODUMP     mongodump binary    (default mongodump)
#   MONGORESTORE  mongorestore binary (default mongorestore)
#   PYTHON        interpreter used to read the YAML config (default python3)
#   BACKUP_RATE_LIMIT  cap on the dump stream, pv syntax (default 8m = 8 MiB/s of
#                 the compressed archive; 0 turns it off). A dump that pulls the
#                 database over the network as fast as it can saturated lc-db's
#                 network stack (ksoftirqd 100%, packet loss) and made the site
#                 answer 503 (17 November 2026), so the stream is throttled.
#   DUMP_PARALLEL_COLLECTIONS  collections read at once (default 1; mongodump's own
#                 default is 4)
#   PV            rate limiter binary (default pv)
set -euo pipefail
umask 077

app_config="${APP_CONFIG:-/var/www/mielenosoitukset_fi/config.yaml}"
backup_dir="${BACKUP_DIR:-/var/backups/mielenosoitukset}"
keep_days="${KEEP_DAYS:-14}"
mode="${BACKUP_MODE:-core}"
exclude="${EXCLUDE_COLLECTIONS-super_audit_logs admin_logs}"
mongodump="${MONGODUMP:-mongodump}"
mongorestore="${MONGORESTORE:-mongorestore}"
python="${PYTHON:-python3}"
rate_limit="${BACKUP_RATE_LIMIT:-8m}"
parallel="${DUMP_PARALLEL_COLLECTIONS:-1}"
pv="${PV:-pv}"

[[ "$keep_days" =~ ^[0-9]+$ && "$keep_days" -ge 1 ]] || { echo "mongodb-backup: KEEP_DAYS must be a positive integer" >&2; exit 2; }
[[ "$parallel" =~ ^[1-9][0-9]*$ ]] || { echo "mongodb-backup: DUMP_PARALLEL_COLLECTIONS must be a positive integer" >&2; exit 2; }
[[ "$rate_limit" == 0 || "$rate_limit" =~ ^[1-9][0-9]*[kKmMgG]?$ ]] || { echo "mongodb-backup: BACKUP_RATE_LIMIT must look like 8m, 500k or 0" >&2; exit 2; }
if [[ "$rate_limit" != 0 ]] && ! command -v "$pv" >/dev/null 2>&1; then
  echo "mongodb-backup: BACKUP_RATE_LIMIT is set but $pv is not installed (apt install pv), refusing an unthrottled dump" >&2; exit 2
fi
[[ "$mode" == core || "$mode" == full ]] || { echo "mongodb-backup: BACKUP_MODE must be core or full" >&2; exit 2; }
excluded=()
if [[ "$mode" == core ]]; then
  for name in $exclude; do
    [[ "$name" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "mongodb-backup: invalid collection name in EXCLUDE_COLLECTIONS" >&2; exit 2; }
    excluded+=("--excludeCollection=$name")
  done
fi
[[ -r "$app_config" ]] || { echo "mongodb-backup: cannot read $app_config" >&2; exit 2; }

work="$(mktemp -d)"
partial=""
# On any exit remove the temp config, and the unfinished archive if this run
# did not complete.
trap 'rm -rf "$work"; [[ -n "$partial" && -e "$partial" ]] && rm -f "$partial"' EXIT

# Read MONGO_URI and MONGO_DBNAME without echoing them.
"$python" - "$app_config" "$work/tools.yaml" "$work/dbname" "$work/authdb" "$work/compressors" "$mode" <<'PY'
import sys, yaml
from urllib.parse import parse_qs, parse_qsl, urlsplit
config = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
uri, db = config.get("MONGO_URI"), config.get("MONGO_DBNAME")
if not uri or not db:
    sys.exit("mongodb-backup: MONGO_URI and MONGO_DBNAME are required in the app config")
parts = urlsplit(uri)
if sys.argv[6] == "full":
    options = parse_qsl(parts.query, keep_blank_values=True)
    compressors = next((value for key, value in reversed(options) if key.lower() == "compressors"), "zstd") or "none"
    if any(name not in {"none", "snappy", "zstd", "zlib"} for name in compressors.split(",")):
        sys.exit("mongodb-backup: unsupported compressor")
    with open(sys.argv[5], "w", encoding="utf-8") as handle:
        handle.write(compressors)
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    handle.write("uri: " + yaml.safe_dump(uri, default_style='"').strip() + "\n")
with open(sys.argv[3], "w", encoding="utf-8") as handle:
    handle.write(db)
# Authenticate where the app does. PyMongo uses authSource, else the database
# in the URI path, else "admin"; the Mongo tools would otherwise use --db.
auth = (parse_qs(parts.query).get("authSource") or [parts.path.lstrip("/") or "admin"])[0]
with open(sys.argv[4], "w", encoding="utf-8") as handle:
    handle.write(auth)
PY
dbname="$(cat "$work/dbname")"
[[ "$dbname" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "mongodb-backup: unexpected database name" >&2; exit 2; }
authdb="$(cat "$work/authdb")"
[[ "$authdb" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "mongodb-backup: unexpected authentication database" >&2; exit 2; }
compression=()
if [[ "$mode" == full ]]; then
  # Tools 100.17 parses URI compressors but only enables its native option.
  compression+=("--compressors=$(cat "$work/compressors")")
fi

install -d -m 0700 "$backup_dir"
# Core and full runs share one directory; serialize them so one run never
# deletes the other's unfinished archive. Held until this process exits.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$backup_dir/.backup.lock"
  # Never queue behind a running dump: two heavy reads in a row hurt the database.
  flock -n 9 || { echo "mongodb-backup: another backup is running; not starting a second" >&2; exit 3; }
fi
# Clear leftovers from runs that were killed before their own cleanup ran.
find "$backup_dir" -maxdepth 1 -type f -name "*.partial" -print -delete
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backup_dir/${dbname}-${mode}-${stamp}.archive.gz"
partial="$target.partial"

# No --quiet: on failure mongodump's reason must reach the journal.
dump=("$mongodump" --config="$work/tools.yaml" --authenticationDatabase="$authdb" --db="$dbname" --numParallelCollections="$parallel" ${excluded[@]+"${excluded[@]}"} ${compression[@]+"${compression[@]}"} --gzip)
if [[ "$rate_limit" == 0 ]]; then
  "${dump[@]}" --archive="$partial"
else
  # The archive goes through pv: a slow reader makes TCP back-pressure the dump,
  # which limits what lc-db has to send. pipefail makes a mongodump error fatal.
  "${dump[@]}" --archive | "$pv" -q -L "$rate_limit" > "$partial"
fi
[[ -s "$partial" ]] || { echo "mongodb-backup: dump is empty" >&2; exit 1; }
# --dryRun alone stops before reading collection data, so first prove the whole
# gzip stream decompresses and its checksum matches.
gzip -t "$partial" || { echo "mongodb-backup: archive failed its gzip integrity check" >&2; exit 1; }
"$mongorestore" --config="$work/tools.yaml" --authenticationDatabase="$authdb" --archive="$partial" --gzip --dryRun --quiet \
  --nsFrom="${dbname}.*" --nsTo="restorecheck_${dbname}.*"
mv "$partial" "$target"
partial=""

# Retention: only ever delete our own finished archives, never the newest one.
find "$backup_dir" -maxdepth 1 -type f -name "${dbname}-${mode}-*.archive.gz" -mtime "+$((keep_days - 1))" \
  ! -newer "$target" ! -samefile "$target" -print -delete

# Archives written before the mode was part of the name (<db>-<time>.archive.gz)
# were full dumps. Expire them with the core retention so they do not pile up.
if [[ "$mode" == core ]]; then
  find "$backup_dir" -maxdepth 1 -type f -name "${dbname}-[0-9]*T[0-9]*Z.archive.gz" -mtime "+$((keep_days - 1))" \
    ! -newer "$target" ! -samefile "$target" -print -delete
fi

echo "mongodb-backup: wrote $target ($(du -h "$target" | cut -f1))"
