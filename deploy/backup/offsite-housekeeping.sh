#!/usr/bin/env bash
# Runs on the RECEIVING machine (not on lc-main), from cron or a systemd timer.
# Part of the off-site backup copy (OPS-01, docs/backups.md).
#
# lc-main may only write into $INCOMING (a restricted rsync key, see the docs),
# so a compromised lc-main can at worst drop or overwrite files there. This
# script moves archives whose checksum matches into $VAULT, which lc-main cannot
# reach, never overwriting a file already in the vault. It then applies the
# off-site retention and checks that fresh archives keep arriving, independent of
# lc-main (so it also notices a dead or silent lc-main). It prints one line per
# finding and exits 1 on any problem; let the cron mailer or monitoring deliver
# that output.
#
#   INCOMING          landing directory the upload key writes to   (required)
#   VAULT             directory the verified archives are kept in  (required)
#   KEEP_DAYS_CORE    delete core archives older than this         (default 30)
#   KEEP_DAYS_FULL    delete full archives older than this         (default 90)
#   STALE_HOURS_CORE  newest core archive older than this is a problem (default 36)
#   STALE_HOURS_FULL  newest full archive older than this is a problem (default 216)
#   SETTLE_MINUTES    ignore files modified more recently than this (default 10)
# The newest archive of each mode is never deleted by retention.
set -euo pipefail
umask 077

incoming="${INCOMING:-}"
vault="${VAULT:-}"
keep_core="${KEEP_DAYS_CORE:-30}"
keep_full="${KEEP_DAYS_FULL:-90}"
stale_core="${STALE_HOURS_CORE:-36}"
stale_full="${STALE_HOURS_FULL:-216}"
settle="${SETTLE_MINUTES:-10}"

[[ -d "$incoming" && -d "$vault" ]] || { echo "housekeeping: set INCOMING and VAULT to existing directories" >&2; exit 2; }
for value in "$keep_core" "$keep_full" "$stale_core" "$stale_full" "$settle"; do
  [[ "$value" =~ ^[0-9]+$ ]] || { echo "housekeeping: numeric settings only" >&2; exit 2; }
done
[[ "$keep_core" -ge 1 && "$keep_full" -ge 1 ]] || { echo "housekeeping: keep days must be at least 1" >&2; exit 2; }

problem=0
note() { echo "housekeeping: $*"; problem=1; }
name_re='^[A-Za-z0-9_-]+-(core|full)-[0-9]{8}T[0-9]{6}Z\.archive\.gz\.age$'

sha_check() { # directory, checksum file
  if command -v sha256sum >/dev/null 2>&1; then (cd "$1" && sha256sum --status -c "$2"); else (cd "$1" && shasum -a 256 -c "$2" >/dev/null 2>&1); fi
}

# 1. Accept settled, checksum-verified archives into the vault.
while IFS= read -r path; do
  name="$(basename "$path")"
  [[ "$name" =~ $name_re ]] || { note "ignoring unexpected file $name in incoming"; continue; }
  [[ -f "$incoming/$name.sha256" ]] || continue            # checksum file not there yet
  if [[ -z "$(find "$incoming/$name.sha256" -mmin "+$settle")" ]]; then continue; fi
  if ! sha_check "$incoming" "$name.sha256"; then note "checksum mismatch for $name, left in incoming"; continue; fi
  if [[ -e "$vault/$name" ]]; then
    if cmp -s "$incoming/$name" "$vault/$name"; then rm -f "$incoming/$name" "$incoming/$name.sha256"
    else note "$name already in the vault with different content, left in incoming"; fi
    continue
  fi
  mv -n "$incoming/$name" "$vault/$name"
  mv -n "$incoming/$name.sha256" "$vault/$name.sha256"
  echo "housekeeping: accepted $name"
done < <(find "$incoming" -maxdepth 1 -type f -name '*.age' ! -name '.*' | sort)

# Remove upload leftovers (rsync temp files start with a dot) after two days.
find "$incoming" -maxdepth 1 -type f -name '.*' -mtime +1 -delete

# 2. Retention and 3. freshness, per mode.
now="$(date +%s)"
for mode in core full; do
  keep="$keep_core"; stale="$stale_core"
  [[ "$mode" == full ]] && { keep="$keep_full"; stale="$stale_full"; }
  files=()
  while IFS= read -r file; do files+=("$file"); done < <(find "$vault" -maxdepth 1 -type f -name "*-$mode-*.archive.gz.age" | sort)
  if [[ "${#files[@]}" -eq 0 ]]; then note "no $mode archive in the vault"; continue; fi
  newest="${files[${#files[@]}-1]}"      # names embed the UTC time, so the last sorts newest
  for file in "${files[@]}"; do
    [[ "$file" == "$newest" ]] && continue
    if [[ -n "$(find "$file" -mtime "+$((keep - 1))")" ]]; then
      rm -f "$file" "$file.sha256"
      echo "housekeeping: removed $(basename "$file") (older than $keep days)"
    fi
  done
  if command -v stat >/dev/null 2>&1 && stat -c %Y "$newest" >/dev/null 2>&1; then mtime="$(stat -c %Y "$newest")"; else mtime="$(stat -f %m "$newest")"; fi
  hours=$(( (now - mtime) / 3600 ))
  if (( hours > stale )); then note "newest $mode archive $(basename "$newest") is $hours hours old (limit $stale)"; fi
done
exit "$problem"
