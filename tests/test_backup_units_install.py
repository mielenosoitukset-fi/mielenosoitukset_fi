"""Backup units installed by the deploy, and the local backup freshness check (OPS-01)."""

import os
import subprocess
import time
from pathlib import Path

BACKUP = Path("deploy/backup").resolve()
REPO = BACKUP.parents[1]


def _install(tmp_path, **env):
    units = tmp_path / "units"
    units.mkdir(exist_ok=True)
    log = tmp_path / "systemctl.log"
    fake = tmp_path / "systemctl"
    fake.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$LOG\"\n")
    fake.chmod(0o755)
    full_env = {**os.environ, "PRODUCTION_REPO_DIR": str(REPO), "BACKUP_UNIT_DIR": str(units),
                "SYSTEMCTL": str(fake), "LOG": str(log),
                "OFFSITE_RECIPIENTS": str(tmp_path / "etc/backup-recipients.txt"),
                "OFFSITE_OUTBOX": str(tmp_path / "outbox"), "OFFSITE_PULL_HOME": str(tmp_path / "pull"),
                "BACKUP_SKIP_ACCOUNTS": "1", "SSHD": str(tmp_path / "no-sshd"), **env}
    result = subprocess.run(["bash", str(BACKUP / "install_units.sh")], env=full_env,
                            capture_output=True, text=True)
    calls = log.read_text().splitlines() if log.exists() else []
    log.unlink(missing_ok=True)
    return result, units, calls


def test_install_copies_units_reloads_and_enables_timers(tmp_path):
    result, units, calls = _install(tmp_path)
    assert result.returncode == 0, result.stderr
    shipped = sorted(p.name for p in BACKUP.glob("mielenosoitukset-*.service")) + \
        sorted(p.name for p in BACKUP.glob("mielenosoitukset-*.timer"))
    for name in shipped:
        assert (units / name).read_bytes() == (BACKUP / name).read_bytes()
        assert (units / name).stat().st_mode & 0o777 == 0o644
    assert calls[0] == "daemon-reload"
    enable = calls[1].split()
    assert enable[:3] == ["enable", "--now", "--quiet"]
    assert sorted(enable[3:]) == sorted(n for n in shipped if n.endswith(".timer"))
    assert "mielenosoitukset-backup-age-check.timer" in enable
    # Services are never started by the install.
    assert not any(call.startswith(("start", "restart")) for call in calls)
    assert not list(units.glob(".*.tmp"))
    recipients = tmp_path / "etc/backup-recipients.txt"
    assert recipients.read_bytes() == (BACKUP / "offsite-recipients.txt").read_bytes()
    assert recipients.stat().st_mode & 0o777 == 0o600
    keys = tmp_path / "pull/.ssh/authorized_keys"
    assert keys.read_bytes() == (BACKUP / "offsite-pull.authorized_keys").read_bytes()
    assert keys.stat().st_mode & 0o777 == 0o644
    assert (tmp_path / "outbox").stat().st_mode & 0o777 == 0o750
    assert "off-site outbox ready" in result.stdout


def test_second_install_skips_daemon_reload_and_rewrites_nothing(tmp_path):
    _install(tmp_path)
    result, _, calls = _install(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "daemon-reload" not in calls
    assert "unchanged" in result.stdout
    assert "installed" not in result.stdout


def test_shipped_offsite_material_is_public_and_read_only():
    lines = [l for l in (BACKUP / "offsite-recipients.txt").read_text().splitlines()
             if l.strip() and not l.startswith("#")]
    assert lines and all(l.startswith("age1") for l in lines)
    for path in (BACKUP / "offsite-recipients.txt", BACKUP / "offsite-pull.authorized_keys"):
        text = path.read_text()
        assert "AGE-SECRET-KEY" not in text and "PRIVATE KEY" not in text
    keys = (BACKUP / "offsite-pull.authorized_keys").read_text().splitlines()
    assert keys and all(k.startswith('restrict,command="rrsync -ro /var/backups/mielenosoitukset-outbox" ssh-ed25519 ')
                        for k in keys)
    unit = (BACKUP / "mielenosoitukset-offsite-copy.service").read_text()
    assert "Environment=OFFSITE_OUTBOX=/var/backups/mielenosoitukset-outbox" in unit
    assert "/var/backups/mielenosoitukset-outbox" in unit.split("ReadWritePaths=")[1].splitlines()[0]


def test_changed_unit_is_replaced(tmp_path):
    _, units, _ = _install(tmp_path)
    (units / "mielenosoitukset-mongodb-backup.timer").write_text("old\n")
    result, _, calls = _install(tmp_path)
    assert calls[0] == "daemon-reload"
    assert "updated mielenosoitukset-mongodb-backup.timer" in result.stdout


def test_offsite_units_are_skipped_until_the_key_exists():
    for name in ("mielenosoitukset-offsite-copy.service", "mielenosoitukset-offsite-check.service"):
        text = (BACKUP / name).read_text()
        assert "ConditionPathExists=/etc/mielenosoitukset/backup-recipients.txt" in text
        assert "EnvironmentFile=-/etc/mielenosoitukset/backup.env" in text


def test_age_check_unit_alerts_on_failure():
    service = (BACKUP / "mielenosoitukset-backup-age-check.service").read_text()
    assert "OnFailure=mielenosoitukset-backup-failed.service" in service
    assert "backup-age-check.sh" in service
    assert "Persistent=true" in (BACKUP / "mielenosoitukset-backup-age-check.timer").read_text()


def _age_check(tmp_path, files, **env):
    for name, hours in files.items():
        path = tmp_path / name
        path.write_bytes(b"x")
        stamp = time.time() - hours * 3600
        os.utime(path, (stamp, stamp))
    return subprocess.run(["bash", str(BACKUP / "backup-age-check.sh")],
                          env={**os.environ, "BACKUP_DIR": str(tmp_path), **env},
                          capture_output=True, text=True)


def test_age_check_passes_with_fresh_core_and_full(tmp_path):
    result = _age_check(tmp_path, {"m-core-a.archive.gz": 3, "m-full-a.archive.gz": 100})
    assert result.returncode == 0, result.stderr


def test_age_check_fails_after_a_missed_night(tmp_path):
    result = _age_check(tmp_path, {"m-core-a.archive.gz": 29, "m-full-a.archive.gz": 52,
                                   "m-core-b.archive.gz.partial": 1})
    assert result.returncode == 0
    result = _age_check(tmp_path, {"m-full-a.archive.gz": 31, "m-core-a.archive.gz": 31})
    assert result.returncode == 1
    assert "newest core or full archive is 31 hours old" in result.stderr


def test_age_check_fails_without_full_archive_or_directory(tmp_path):
    result = _age_check(tmp_path, {"m-core-a.archive.gz": 2})
    assert result.returncode == 1 and "no full archive" in result.stderr
    gone = subprocess.run(["bash", str(BACKUP / "backup-age-check.sh")],
                          env={**os.environ, "BACKUP_DIR": str(tmp_path / "missing")},
                          capture_output=True, text=True)
    assert gone.returncode == 1


def test_age_check_fails_when_full_is_a_week_late(tmp_path):
    result = _age_check(tmp_path, {"m-core-a.archive.gz": 2, "m-full-a.archive.gz": 197})
    assert result.returncode == 1 and "newest full archive is 197 hours old" in result.stderr


def _fake_sshd(tmp_path, accept=True):
    """A stand-in sshd: -T prints AllowUsers lines including any drop-in, -t validates."""
    dropin = tmp_path / "sshd_config.d/60-moso-offsite.conf"
    fake = tmp_path / "sshd"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ $1 == -t ]]; then " + ("exit 0" if accept else "exit 1") + "; fi\n"
        "echo 'allowusers root'\n"
        f"[[ -f {dropin} ]] && sed -n 's/^AllowUsers /allowusers /p' {dropin}\n"
        "exit 0\n")
    fake.chmod(0o755)
    (tmp_path / "sshd_config.d").mkdir(exist_ok=True)
    (tmp_path / "sshd_config").write_text("Include " + str(tmp_path / "sshd_config.d") + "/*.conf\n")
    return {"SSHD": str(fake), "OFFSITE_SSHD_DROPIN": str(dropin),
            "OFFSITE_SSHD_CONFIG": str(tmp_path / "sshd_config")}, dropin


def test_pull_user_is_allowed_in_sshd_from_the_jump_host_only(tmp_path):
    env, dropin = _fake_sshd(tmp_path)
    result, _, calls = _install(tmp_path, **env)
    assert result.returncode == 0, result.stderr
    assert dropin.read_text().splitlines()[-1] == "AllowUsers moso-offsite@10.20.0.1"
    assert "reload ssh" in calls
    # Second deploy: already allowed, no rewrite and no reload.
    result, _, calls = _install(tmp_path, **env)
    assert "reload ssh" not in calls and "allowed moso-offsite" not in result.stdout


def test_rejected_sshd_dropin_is_removed(tmp_path):
    env, dropin = _fake_sshd(tmp_path, accept=False)
    result, _, calls = _install(tmp_path, **env)
    assert result.returncode == 0, result.stderr
    assert not dropin.exists()
    assert "reload ssh" not in calls and "did not accept" in result.stdout
