#!/usr/bin/env bash
# Keep the backup units on the production host in step with the repository
# (OPS-01, docs/backups.md). Called by production_deploy.sh on every deploy, so
# backup changes ship through a pull request and the deploy, never by hand.
#
# Copies every changed mielenosoitukset-*.service/.timer from deploy/backup into
# the systemd unit directory, reloads systemd when something changed, and
# enables the timers. It never starts a backup or copy service and never
# restarts anything public. A running dump keeps its old definition until it
# ends. The off-site units stay skipped (ConditionPathExists) until the
# maintainer's public age key is installed.
#
#   PRODUCTION_REPO_DIR  checkout (default /var/www/mielenosoitukset_fi)
#   BACKUP_UNIT_DIR      systemd unit directory (default /etc/systemd/system)
#   SYSTEMCTL            systemctl binary (default systemctl; tests use a fake)
#   OFFSITE_RECIPIENTS   off-site key file, only reported
#                        (default /etc/mielenosoitukset/backup-recipients.txt)
set -euo pipefail
umask 022

repo_dir=${PRODUCTION_REPO_DIR:-/var/www/mielenosoitukset_fi}
unit_dir=${BACKUP_UNIT_DIR:-/etc/systemd/system}
systemctl=${SYSTEMCTL:-systemctl}
recipients=${OFFSITE_RECIPIENTS:-/etc/mielenosoitukset/backup-recipients.txt}
source_dir="$repo_dir/deploy/backup"

log() { printf 'backup units: %s\n' "$*"; }

shopt -s nullglob
units=("$source_dir"/mielenosoitukset-*.service "$source_dir"/mielenosoitukset-*.timer)
(( ${#units[@]} )) || { echo "backup units: none found in $source_dir" >&2; exit 2; }

changed=()
timers=()
for file in "${units[@]}"; do
  name=${file##*/}
  [[ $name =~ ^mielenosoitukset-[a-z0-9-]+\.(service|timer)$ ]] || { echo "backup units: unexpected file name $name" >&2; exit 2; }
  [[ $name == *.timer ]] && timers+=("$name")
  if ! cmp -s "$file" "$unit_dir/$name"; then
    install -m 0644 "$file" "$unit_dir/.$name.tmp"
    mv -f "$unit_dir/.$name.tmp" "$unit_dir/$name"
    changed+=("$name")
  fi
done

if (( ${#changed[@]} )); then
  "$systemctl" daemon-reload
  log "updated ${changed[*]}"
else
  log "unchanged"
fi

# Enabling a timer only arms it. Persistent timers that never ran do not fire
# at once, and the dump script refuses to run outside its night window anyway.
"$systemctl" enable --now --quiet "${timers[@]}"
log "timers enabled: ${timers[*]}"

if [[ -s $recipients ]]; then
  log "off-site copy configured"
else
  log "off-site copy NOT configured yet: no $recipients (see docs/backups.md)"
fi
