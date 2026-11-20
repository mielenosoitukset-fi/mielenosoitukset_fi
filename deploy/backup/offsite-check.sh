#!/usr/bin/env bash
# Freshness check for the off-site copy (OPS-01). Exits 1, with a reason on
# stderr, when the last successful upload of a backup mode is too old; the
# systemd unit's OnFailure hook then sends the alert e-mail.
#
# Nightly core copies are checked against OFFSITE_STALE_HOURS (default 36).
# Weekly full copies are checked against OFFSITE_STALE_HOURS_FULL (default 216,
# nine days). Until a mode has been uploaded once, its age counts from the first
# time this check saw it, so a fresh install is not blamed for the week before
# the first full dump.
#
#   STATE_DIR   same state directory as offsite-copy.sh
#               (default $STATE_DIRECTORY or /var/lib/mielenosoitukset-backup)
set -euo pipefail

state_dir="${STATE_DIR:-${STATE_DIRECTORY:-/var/lib/mielenosoitukset-backup}}"
core_hours="${OFFSITE_STALE_HOURS:-36}"
full_hours="${OFFSITE_STALE_HOURS_FULL:-216}"
for value in "$core_hours" "$full_hours"; do
  [[ "$value" =~ ^[0-9]+$ && "$value" -ge 1 ]] || { echo "offsite-check: stale hours must be positive integers" >&2; exit 2; }
done
install -d -m 0700 "$state_dir"

now="$(date +%s)"
stale=0
for mode in core full; do
  limit="$core_hours"
  [[ "$mode" == full ]] && limit="$full_hours"
  if [[ -s "$state_dir/last-$mode" ]]; then
    since="$(cut -d' ' -f1 "$state_dir/last-$mode")"
    what="last uploaded $mode archive"
  else
    [[ -s "$state_dir/first-seen-$mode" ]] || echo "$now" > "$state_dir/first-seen-$mode"
    since="$(cat "$state_dir/first-seen-$mode")"
    what="no $mode archive uploaded yet; first checked"
  fi
  [[ "$since" =~ ^[0-9]+$ ]] || { echo "offsite-check: unreadable state for $mode" >&2; stale=1; continue; }
  hours=$(( (now - since) / 3600 ))
  if (( hours > limit )); then
    echo "offsite-check: STALE: $what $hours hours ago (limit $limit hours)" >&2
    stale=1
  else
    echo "offsite-check: ok: $what $hours hours ago (limit $limit hours)"
  fi
done
exit "$stale"
