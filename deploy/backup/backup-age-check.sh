#!/usr/bin/env bash
# Local freshness check for the MongoDB dumps (OPS-01, docs/backups.md).
#
# A failed dump alerts through OnFailure=, but a dump that never starts (timer
# stopped, host down at night, unit removed) does not fail anything. This check
# exits 1, with the reason on stderr, when the newest finished archive of any
# mode is older than BACKUP_STALE_HOURS (default 30: one missed night) or the
# newest full archive is older than BACKUP_STALE_HOURS_FULL (default 192: one
# missed Sunday). The unit's OnFailure hook then sends the alert.
#
#   BACKUP_DIR  local archives (default /var/backups/mielenosoitukset)
set -euo pipefail

backup_dir="${BACKUP_DIR:-/var/backups/mielenosoitukset}"
any_hours="${BACKUP_STALE_HOURS:-30}"
full_hours="${BACKUP_STALE_HOURS_FULL:-192}"
for value in "$any_hours" "$full_hours"; do
  [[ "$value" =~ ^[0-9]+$ && "$value" -ge 1 ]] || { echo "backup-age-check: stale hours must be positive integers" >&2; exit 2; }
done
[[ -d "$backup_dir" ]] || { echo "backup-age-check: STALE: $backup_dir does not exist" >&2; exit 1; }

# newest <mode>...: epoch seconds of the newest finished archive of those modes,
# or nothing. Unfinished .partial files never match.
newest() {
  local names=() mode
  for mode in "$@"; do names+=(-o -name "*-${mode}-*.archive.gz"); done
  find "$backup_dir" -maxdepth 1 -type f \( "${names[@]:1}" \) -printf '%T@\n' | cut -d. -f1 | sort -n | tail -n 1
}

now="$(date +%s)"
stale=0
check() {
  local what=$1 limit=$2 stamp hours
  shift 2
  stamp="$(newest "$@")"
  if [[ -z "$stamp" ]]; then
    echo "backup-age-check: STALE: no $what archive in $backup_dir" >&2
    stale=1
    return
  fi
  hours=$(( (now - stamp) / 3600 ))
  if (( hours > limit )); then
    echo "backup-age-check: STALE: newest $what archive is $hours hours old (limit $limit hours)" >&2
    stale=1
  else
    echo "backup-age-check: ok: newest $what archive is $hours hours old (limit $limit hours)"
  fi
}
check "core or full" "$any_hours" core full
check full "$full_hours" full
exit "$stale"
