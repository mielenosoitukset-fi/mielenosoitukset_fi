#!/usr/bin/env bash
# Encrypted off-site copy of the MongoDB backups (OPS-01, docs/backups.md).
#
# Encrypts each finished archive with age to the PUBLIC recipient keys in
# $OFFSITE_RECIPIENTS, then copies it with rsync over SSH to $OFFSITE_TARGET.
# The private key never exists on this host, so a stolen server or off-site disk
# cannot decrypt the archives. Archives are encrypted one at a time into a
# private staging directory that is emptied after each upload.
#
# Which archives: every finished <db>-<core|full>-<time>.archive.gz that is
# younger than $OFFSITE_SEND_DAYS days and not yet listed in the ledger
# ($STATE_DIR/sent). After a successful upload the ledger and a per-mode stamp
# ($STATE_DIR/last-core, last-full) are updated; offsite-check.sh reads the stamp.
#
# Settings come from the environment, normally the root-only file
# /etc/mielenosoitukset/backup.env loaded by the systemd unit. Nothing about the
# target host, user, key or recipients is stored in the repository.
#   OFFSITE_TARGET      rsync target, user@host:directory   (required)
#   OFFSITE_RECIPIENTS  file of age public recipients       (default /etc/mielenosoitukset/backup-recipients.txt)
#   OFFSITE_SSH_KEY     private SSH key for the target      (default /etc/mielenosoitukset/offsite_ed25519)
#   OFFSITE_KNOWN_HOSTS known_hosts file pinning the target (default /etc/mielenosoitukset/offsite_known_hosts)
#   OFFSITE_PORT        SSH port                            (default 22)
#   OFFSITE_SEND_DAYS   only send archives younger than this (default 3)
#   BACKUP_DIR          local archives                      (default /var/backups/mielenosoitukset)
#   STATE_DIR           ledger and stamps                   (default $STATE_DIRECTORY or /var/lib/mielenosoitukset-backup)
#   AGE, RSYNC          binaries                            (default age, rsync)
set -euo pipefail
umask 077

backup_dir="${BACKUP_DIR:-/var/backups/mielenosoitukset}"
state_dir="${STATE_DIR:-${STATE_DIRECTORY:-/var/lib/mielenosoitukset-backup}}"
recipients="${OFFSITE_RECIPIENTS:-/etc/mielenosoitukset/backup-recipients.txt}"
target="${OFFSITE_TARGET:-}"
ssh_key="${OFFSITE_SSH_KEY:-/etc/mielenosoitukset/offsite_ed25519}"
known_hosts="${OFFSITE_KNOWN_HOSTS:-/etc/mielenosoitukset/offsite_known_hosts}"
port="${OFFSITE_PORT:-22}"
send_days="${OFFSITE_SEND_DAYS:-3}"
age="${AGE:-age}"
rsync="${RSYNC:-rsync}"

die() { echo "offsite-copy: $*" >&2; exit 2; }

[[ -n "$target" ]] || die "OFFSITE_TARGET is not set (see docs/backups.md)"
[[ "$target" =~ ^[A-Za-z0-9][A-Za-z0-9._@-]*:[A-Za-z0-9._/~-]*$ ]] || die "OFFSITE_TARGET must look like user@host:directory"
[[ "$port" =~ ^[0-9]+$ ]] || die "OFFSITE_PORT must be a number"
[[ "$send_days" =~ ^[0-9]+$ && "$send_days" -ge 1 ]] || die "OFFSITE_SEND_DAYS must be a positive integer"
for path in "$ssh_key" "$known_hosts" "$recipients"; do
  [[ "$path" =~ ^/[A-Za-z0-9_./-]+$ ]] || die "unsafe path in settings: $path"
  [[ -r "$path" ]] || die "cannot read $path"
done

# The recipients file may only hold public keys. Refuse a private key outright:
# a decryption key on this host would defeat the point of encrypting here.
if grep -q -E 'AGE-SECRET-KEY|PRIVATE KEY' "$recipients"; then
  die "$recipients contains a private key; remove it and use only the public key"
fi
recipient_lines="$(grep -c -v -E '^[[:space:]]*(#|$)' "$recipients" || true)"
[[ "$recipient_lines" -ge 1 ]] || die "$recipients has no recipient"
if grep -v -E '^[[:space:]]*(#|$)' "$recipients" | grep -q -v -E '^(age1[0-9a-z]+|ssh-(ed25519|rsa) [A-Za-z0-9+/=]+( .*)?)$'; then
  die "$recipients has a line that is not an age or ssh public key"
fi

install -d -m 0700 "$state_dir"
if command -v flock >/dev/null 2>&1; then
  exec 8>"$state_dir/offsite.lock"
  flock -n 8 || { echo "offsite-copy: another copy is running"; exit 0; }
fi
ledger="$state_dir/sent"
touch "$ledger"

staging="$backup_dir/offsite-staging"
install -d -m 0700 "$staging"
trap 'rm -f "$staging"/*' EXIT
rm -f "$staging"/*

ssh_cmd="ssh -F /dev/null -i $ssh_key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$known_hosts -o ConnectTimeout=30 -p $port"

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi
}

sent=0
while IFS= read -r name; do
  name="$(basename "$name")"
  [[ "$name" =~ ^[A-Za-z0-9_-]+-(core|full)-[0-9]{8}T[0-9]{6}Z\.archive\.gz$ ]] || continue
  mode="${BASH_REMATCH[1]}"
  grep -q -F -x -- "$name" "$ledger" && continue

  enc="$staging/$name.age"
  "$age" --recipients-file "$recipients" --output "$enc.tmp" "$backup_dir/$name"
  [[ -s "$enc.tmp" ]] || { echo "offsite-copy: encryption of $name produced no output" >&2; exit 1; }
  [[ "$(head -c 21 "$enc.tmp")" == "age-encryption.org/v1" ]] || { echo "offsite-copy: $name did not come out as an age file" >&2; exit 1; }
  mv "$enc.tmp" "$enc"
  (cd "$staging" && sha256 "$name.age" > "$name.age.sha256")

  # The checksum file goes last in the same call; the receiving side only
  # accepts an archive once its checksum file is there and matches.
  "$rsync" --times --chmod=F0600 --timeout=900 -e "$ssh_cmd" -- "$enc" "$enc.sha256" "$target" \
    || { echo "offsite-copy: upload of $name failed" >&2; exit 1; }

  echo "$name" >> "$ledger"
  printf '%s %s\n' "$(date +%s)" "$name" > "$state_dir/last-$mode.tmp"
  mv "$state_dir/last-$mode.tmp" "$state_dir/last-$mode"
  rm -f "$enc" "$enc.sha256"
  echo "offsite-copy: sent $name"
  sent=$((sent + 1))
done < <(find "$backup_dir" -maxdepth 1 -type f -name '*.archive.gz' -mtime "-$send_days" | sort)

# Keep the ledger short; names older than the send window can never match again.
if [[ "$(wc -l < "$ledger")" -gt 500 ]]; then
  tail -n 200 "$ledger" > "$ledger.tmp" && mv "$ledger.tmp" "$ledger"
fi
echo "offsite-copy: done, $sent archive(s) sent"
