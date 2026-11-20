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
                "OFFSITE_RECIPIENTS": str(tmp_path / "missing-recipients"), **env}
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
    assert "off-site copy NOT configured" in result.stdout
    assert not list(units.glob(".*.tmp"))


def test_second_install_skips_daemon_reload_and_reports_offsite(tmp_path):
    _install(tmp_path)
    (tmp_path / "recipients").write_text("age1" + "q" * 58 + "\n")
    result, _, calls = _install(tmp_path, OFFSITE_RECIPIENTS=str(tmp_path / "recipients"))
    assert result.returncode == 0, result.stderr
    assert "daemon-reload" not in calls
    assert "unchanged" in result.stdout and "off-site copy configured" in result.stdout


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
