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
#   OFFSITE_RECIPIENTS   where the public age recipients are installed
#                        (default /etc/mielenosoitukset/backup-recipients.txt)
#   OFFSITE_OUTBOX       encrypted outbox the off-site machine pulls from
#                        (default /var/backups/mielenosoitukset-outbox)
#   OFFSITE_PULL_HOME    home of the read-only pull user moso-offsite
#                        (default /var/lib/moso-offsite)
#   BACKUP_SKIP_ACCOUNTS 1 skips user, package and ownership steps (tests only)
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

# Off-site outbox (pull model). Everything here is public material: the age
# recipients and the puller's public key with a read-only rrsync command.
outbox=${OFFSITE_OUTBOX:-/var/backups/mielenosoitukset-outbox}
pull_home=${OFFSITE_PULL_HOME:-/var/lib/moso-offsite}
pull_user=moso-offsite
skip_accounts=${BACKUP_SKIP_ACCOUNTS:-0}

if [[ $skip_accounts != 1 ]]; then
  for tool in age rrsync; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      package=$tool; [[ $tool == rrsync ]] && package=rsync
      log "installing missing $tool (package $package)"
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$package" >/dev/null \
        || echo "backup units: could not install $package" >&2
    fi
  done
  if ! getent passwd "$pull_user" >/dev/null; then
    useradd --system --home-dir "$pull_home" --no-create-home --shell /bin/sh --user-group "$pull_user"
    passwd -l "$pull_user" >/dev/null
    log "created read-only pull user $pull_user"
  fi
fi

install -d -m 0700 "$(dirname "$recipients")"
if ! cmp -s "$source_dir/offsite-recipients.txt" "$recipients"; then
  install -m 0600 "$source_dir/offsite-recipients.txt" "$recipients"
  log "installed off-site recipients"
fi

# The home and its keys belong to root, so the pull user cannot change them.
install -d -m 0755 "$pull_home" "$pull_home/.ssh"
if ! cmp -s "$source_dir/offsite-pull.authorized_keys" "$pull_home/.ssh/authorized_keys"; then
  install -m 0644 "$source_dir/offsite-pull.authorized_keys" "$pull_home/.ssh/authorized_keys"
  log "installed the read-only pull key"
fi
install -d -m 0750 "$outbox"
if [[ $skip_accounts != 1 ]]; then
  chown "root:$pull_user" "$outbox"
fi

# sshd here allows only listed users. The pull arrives through the jump host,
# so allow the pull user from that address only (AllowUsers lines accumulate).
# The drop-in is validated with sshd -t and removed again if it does not pass;
# a reload keeps open sessions, including this deploy's.
sshd_dropin=${OFFSITE_SSHD_DROPIN:-/etc/ssh/sshd_config.d/60-moso-offsite.conf}
pull_from=${OFFSITE_PULL_FROM:-10.20.0.1}
sshd_config=${OFFSITE_SSHD_CONFIG:-/etc/ssh/sshd_config}
sshd=${SSHD:-sshd}
if command -v "$sshd" >/dev/null 2>&1; then
  allow=$("$sshd" -T 2>/dev/null | grep -iE '^(allowusers|allowgroups) ' || true)
  if [[ -n $allow ]] && ! grep -qw -- "$pull_user" <<<"$allow"; then
    if [[ -d $(dirname "$sshd_dropin") ]] && grep -qsE '^[[:space:]]*Include[[:space:]].*sshd_config\.d' "$sshd_config"; then
      printf '# Read-only off-site backup pull (OPS-01, docs/backups.md). Managed by the deploy.\nAllowUsers %s@%s\n' \
        "$pull_user" "$pull_from" > "$sshd_dropin.tmp"
      chmod 0644 "$sshd_dropin.tmp"
      mv -f "$sshd_dropin.tmp" "$sshd_dropin"
      if "$sshd" -t && "$sshd" -T 2>/dev/null | grep -qiE "^allowusers ${pull_user}@"; then
        "$systemctl" reload ssh 2>/dev/null || "$systemctl" reload sshd
        log "allowed $pull_user@$pull_from in sshd"
      else
        rm -f "$sshd_dropin"
        log "WARNING: sshd did not accept $sshd_dropin; removed it, the pull will be refused"
      fi
    else
      log "WARNING: sshd restricts logins and has no sshd_config.d include; $pull_user is not allowed, the pull will be refused"
    fi
  fi
fi
log "off-site outbox ready at $outbox"
