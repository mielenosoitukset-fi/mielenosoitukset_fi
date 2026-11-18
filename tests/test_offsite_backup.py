"""Off-site backup copy, freshness check, receiving-side housekeeping and alert mail."""

import hashlib
import importlib.util
import os
import smtplib
import subprocess
import time
from pathlib import Path

import pytest

BACKUP = Path("deploy/backup").resolve()
PUBLIC_KEY = "age1" + "q" * 58
NAME = "mielenosoitukset-core-20261118T023000Z.archive.gz"

FAKE_AGE = """#!/usr/bin/env bash
# Stand-in for age: records argv, writes a file with the age header.
printf '%s\\n' "$@" >> "$ARGV_LOG"
while [[ $# -gt 0 ]]; do
  case "$1" in --output) out="$2"; shift ;; *) in="$1" ;; esac
  shift
done
{ printf 'age-encryption.org/v1\\n'; cat "$in"; } > "$out"
"""

FAKE_RSYNC = """#!/usr/bin/env bash
# Stand-in for rsync: records argv and copies the sources into $FAKE_DEST.
printf '%s\\n' "$@" >> "$ARGV_LOG"
[[ -n "${FAKE_RSYNC_FAIL:-}" ]] && exit 12
args=("$@")
sep=0
for i in "${!args[@]}"; do [[ "${args[$i]}" == -- ]] && sep=$i; done
n=${#args[@]}
for ((i = sep + 1; i < n - 1; i++)); do cp "${args[$i]}" "$FAKE_DEST/"; done
"""


def _setup(tmp_path):
    for sub in ("bin", "backups", "state", "etc", "dest"):
        (tmp_path / sub).mkdir(exist_ok=True)
    for name, body in (("age", FAKE_AGE), ("rsync", FAKE_RSYNC)):
        tool = tmp_path / "bin" / name
        tool.write_text(body)
        tool.chmod(0o755)
    for name, body in (
        ("recipients.txt", f"# maintainer\n{PUBLIC_KEY}\n"), ("key", "not a real key"),
        ("known_hosts", "host ssh-ed25519 AAAA"),
    ):
        if not (tmp_path / "etc" / name).exists():
            (tmp_path / "etc" / name).write_text(body)


def _copy(tmp_path, **env):
    _setup(tmp_path)
    full_env = {
        **os.environ,
        "BACKUP_DIR": str(tmp_path / "backups"), "STATE_DIR": str(tmp_path / "state"),
        "OFFSITE_TARGET": "backup@homelab.example:incoming/",
        "OFFSITE_RECIPIENTS": str(tmp_path / "etc/recipients.txt"),
        "OFFSITE_SSH_KEY": str(tmp_path / "etc/key"),
        "OFFSITE_KNOWN_HOSTS": str(tmp_path / "etc/known_hosts"),
        "AGE": str(tmp_path / "bin/age"), "RSYNC": str(tmp_path / "bin/rsync"),
        "ARGV_LOG": str(tmp_path / "argv.log"), "FAKE_DEST": str(tmp_path / "dest"),
        **env,
    }
    return subprocess.run(["bash", str(BACKUP / "offsite-copy.sh")], env=full_env, capture_output=True, text=True)


def _archive(tmp_path, name=NAME, age_hours=1, body=b"dump-bytes"):
    path = tmp_path / "backups" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(body)
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return path


def test_copy_encrypts_uploads_and_records_the_stamp(tmp_path):
    _setup(tmp_path)
    _archive(tmp_path)

    result = _copy(tmp_path)

    assert result.returncode == 0, result.stderr
    uploaded = tmp_path / "dest" / f"{NAME}.age"
    assert uploaded.read_bytes().startswith(b"age-encryption.org/v1\n")
    checksum = (tmp_path / "dest" / f"{NAME}.age.sha256").read_text().split()
    assert checksum == [hashlib.sha256(uploaded.read_bytes()).hexdigest(), f"{NAME}.age"]
    argv = (tmp_path / "argv.log").read_text()
    assert "--bwlimit=2048" in argv and "--recipients-file" in argv and "StrictHostKeyChecking=yes" in argv
    assert "backup@homelab.example:incoming/" in argv
    assert (tmp_path / "state/sent").read_text() == NAME + "\n"
    assert (tmp_path / "state/last-core").read_text().split()[1] == NAME
    # Nothing encrypted stays behind on this host.
    assert not list((tmp_path / "backups/offsite-staging").iterdir())

    # A second run has nothing new to send.
    (tmp_path / "argv.log").unlink()
    again = _copy(tmp_path)
    assert again.returncode == 0 and "0 archive(s) sent" in again.stdout
    assert not (tmp_path / "argv.log").exists()


def test_copy_skips_old_partial_and_unrelated_files(tmp_path):
    _setup(tmp_path)
    _archive(tmp_path, "mielenosoitukset-core-20260101T000000Z.archive.gz", age_hours=24 * 10)
    _archive(tmp_path, "mielenosoitukset-core-20261118T023000Z.archive.gz.partial")
    _archive(tmp_path, "mielenosoitukset-20261118T023000Z.archive.gz")
    _archive(tmp_path, "notes.txt")

    result = _copy(tmp_path)

    assert result.returncode == 0, result.stderr
    assert not list((tmp_path / "dest").iterdir())


def test_failed_upload_is_not_recorded_so_it_is_retried(tmp_path):
    _setup(tmp_path)
    _archive(tmp_path)

    result = _copy(tmp_path, FAKE_RSYNC_FAIL="1")

    assert result.returncode == 1 and "upload of" in result.stderr
    assert (tmp_path / "state/sent").read_text() == ""
    assert not (tmp_path / "state/last-core").exists()
    assert not list((tmp_path / "backups/offsite-staging").iterdir())
    assert _copy(tmp_path).returncode == 0
    assert (tmp_path / "state/sent").read_text() == NAME + "\n"


def test_copy_refuses_a_private_key_or_missing_settings(tmp_path):
    _setup(tmp_path)
    _archive(tmp_path)
    (tmp_path / "etc/recipients.txt").write_text(f"{PUBLIC_KEY}\nAGE-SECRET-KEY-1ABC\n")
    result = _copy(tmp_path)
    assert result.returncode == 2 and "private key" in result.stderr
    assert "AGE-SECRET-KEY" not in result.stdout + result.stderr
    assert not list((tmp_path / "dest").iterdir())

    (tmp_path / "etc/recipients.txt").write_text("hello world\n")
    assert _copy(tmp_path).returncode == 2

    assert _copy(tmp_path, OFFSITE_TARGET="").returncode == 2
    assert _copy(tmp_path, OFFSITE_TARGET="-oProxyCommand=x:dir").returncode == 2


def _check(tmp_path, **env):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(BACKUP / "offsite-check.sh")], env={**os.environ, "STATE_DIR": str(state), **env},
        capture_output=True, text=True,
    )


def _stamp(tmp_path, mode, hours_ago):
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / f"last-{mode}").write_text(f"{int(time.time() - hours_ago * 3600)} {NAME}\n")


def test_check_passes_when_fresh_and_fails_when_stale(tmp_path):
    _stamp(tmp_path, "core", 20)
    _stamp(tmp_path, "full", 100)
    assert _check(tmp_path).returncode == 0

    _stamp(tmp_path, "core", 40)
    result = _check(tmp_path)
    assert result.returncode == 1 and "STALE" in result.stderr and "core" in result.stderr

    _stamp(tmp_path, "core", 1)
    _stamp(tmp_path, "full", 24 * 10)
    assert _check(tmp_path).returncode == 1
    assert _check(tmp_path, OFFSITE_STALE_HOURS_FULL="300").returncode == 0


def test_check_gives_a_new_install_a_grace_period(tmp_path):
    first = _check(tmp_path)
    assert first.returncode == 0, first.stderr
    assert (tmp_path / "state/first-seen-core").exists()
    # Backdate first-seen: still never uploaded, so now it is stale.
    (tmp_path / "state/first-seen-core").write_text(str(int(time.time()) - 40 * 3600))
    assert _check(tmp_path).returncode == 1


def _house(tmp_path, **env):
    for sub in ("incoming", "vault"):
        (tmp_path / sub).mkdir(exist_ok=True)
    full_env = {**os.environ, "INCOMING": str(tmp_path / "incoming"), "VAULT": str(tmp_path / "vault"),
                "SETTLE_MINUTES": "0", **env}
    return subprocess.run(["bash", str(BACKUP / "offsite-housekeeping.sh")], env=full_env, capture_output=True, text=True)


def _upload(tmp_path, name, body=b"encrypted", age_days=0, good=True, directory="incoming"):
    enc = tmp_path / directory / f"{name}.age"
    enc.parent.mkdir(exist_ok=True)
    enc.write_bytes(body)
    digest = hashlib.sha256(body if good else b"other").hexdigest()
    (tmp_path / directory / f"{name}.age.sha256").write_text(f"{digest}  {name}.age\n")
    stamp = time.time() - age_days * 86400 - 3600
    for path in (enc, tmp_path / directory / f"{name}.age.sha256"):
        os.utime(path, (stamp, stamp))
    return enc


def test_housekeeping_accepts_verified_archives_and_rejects_bad_ones(tmp_path):
    _upload(tmp_path, NAME)
    _upload(tmp_path, "mielenosoitukset-full-20261115T033000Z.archive.gz", good=False)
    _upload(tmp_path, "mielenosoitukset-full-20261108T033000Z.archive.gz", age_days=1, directory="vault")

    result = _house(tmp_path)

    assert (tmp_path / "vault" / f"{NAME}.age").exists() and not (tmp_path / "incoming" / f"{NAME}.age").exists()
    assert "checksum mismatch" in result.stdout
    assert (tmp_path / "incoming/mielenosoitukset-full-20261115T033000Z.archive.gz.age").exists()
    assert result.returncode == 1


def test_housekeeping_never_overwrites_the_vault(tmp_path):
    _upload(tmp_path, NAME, body=b"original", directory="vault")
    _upload(tmp_path, NAME, body=b"overwrite attempt")

    result = _house(tmp_path)

    assert (tmp_path / "vault" / f"{NAME}.age").read_bytes() == b"original"
    assert "already in the vault with different content" in result.stdout
    assert (tmp_path / "incoming" / f"{NAME}.age").exists()


def test_housekeeping_prunes_per_mode_but_keeps_the_newest(tmp_path):
    old_core = "mielenosoitukset-core-20261001T023000Z.archive.gz"
    mid_core = "mielenosoitukset-core-20261110T023000Z.archive.gz"
    old_full = "mielenosoitukset-full-20260101T033000Z.archive.gz"
    mid_full = "mielenosoitukset-full-20260901T033000Z.archive.gz"
    newest_full = "mielenosoitukset-full-20261115T033000Z.archive.gz"
    _upload(tmp_path, old_core, age_days=40, directory="vault")
    _upload(tmp_path, mid_core, age_days=1, directory="vault")
    _upload(tmp_path, old_full, age_days=100, directory="vault")
    _upload(tmp_path, mid_full, age_days=50, directory="vault")
    _upload(tmp_path, newest_full, age_days=3, directory="vault")

    result = _house(tmp_path)

    vault = {p.name for p in (tmp_path / "vault").iterdir()}
    assert result.returncode == 0, result.stdout
    assert f"{old_core}.age" not in vault and f"{old_core}.age.sha256" not in vault
    assert f"{old_full}.age" not in vault
    assert f"{mid_core}.age" in vault and f"{mid_full}.age" in vault and f"{newest_full}.age" in vault


def test_housekeeping_keeps_the_only_archive_even_when_old_and_reports_it_stale(tmp_path):
    only_core = "mielenosoitukset-core-20260901T023000Z.archive.gz"
    only_full = "mielenosoitukset-full-20260901T033000Z.archive.gz"
    _upload(tmp_path, only_core, age_days=70, directory="vault")
    _upload(tmp_path, only_full, age_days=70, directory="vault")

    result = _house(tmp_path)

    assert (tmp_path / "vault" / f"{only_core}.age").exists() and (tmp_path / "vault" / f"{only_full}.age").exists()
    assert result.returncode == 1 and result.stdout.count("hours old") == 2


def test_housekeeping_waits_for_new_files_to_settle(tmp_path):
    enc = _upload(tmp_path, NAME)
    now = time.time()
    os.utime(enc.with_name(enc.name + ".sha256"), (now, now))

    _house(tmp_path, SETTLE_MINUTES="10")

    assert enc.exists() and not (tmp_path / "vault" / enc.name).exists()


# --- alert mail -----------------------------------------------------------

def _load_notify():
    spec = importlib.util.spec_from_file_location("backup_notify", BACKUP / "backup-notify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSMTP:
    sent = []
    log = []
    fail = False

    def __init__(self, host, port, timeout=None):
        self.log.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.log.append(("starttls",))

    def login(self, user, password):
        self.log.append(("login", user))

    def send_message(self, message):
        if self.fail:
            raise smtplib.SMTPException("secret-recipient@example.org rejected")
        self.sent.append(message)


@pytest.fixture
def notify(monkeypatch, tmp_path):
    module = _load_notify()
    FakeSMTP.sent, FakeSMTP.log, FakeSMTP.fail = [], [], False
    monkeypatch.setattr(module.smtplib, "SMTP", FakeSMTP)
    config = tmp_path / "config.yaml"
    config.write_text(
        "MAIL:\n  SERVER: smtp.example\n  PORT: 587\n  USE_TLS: true\n  USERNAME: site@example.org\n"
        "  PASSWORD: pw\n  DEFAULT_SENDER: site@example.org\n"
    )
    return module, str(config)


def test_alert_mail_goes_through_the_site_smtp_account(notify):
    module, config = notify
    env = {"ALERT_EMAIL": "maintainer@example.org", "MONITOR_UNIT": "mielenosoitukset-offsite-check.service",
           "APP_CONFIG": config}

    assert module.main(env) == 0

    message = FakeSMTP.sent[0]
    assert message["To"] == "maintainer@example.org" and message["From"] == "site@example.org"
    assert "mielenosoitukset-offsite-check.service" in message["Subject"]
    assert ("starttls",) in FakeSMTP.log and ("login", "site@example.org") in FakeSMTP.log
    # Only the unit and host are named: no logs, no connection strings.
    assert "mongodb://" not in message.get_content()


def test_alert_without_any_channel_fails_visibly(notify, capsys):
    module, config = notify
    assert module.main({"APP_CONFIG": config}) == 1
    assert not FakeSMTP.sent and "NOT sent" in capsys.readouterr().err


def test_failed_send_exits_nonzero_without_printing_the_error_text(notify, capsys):
    module, config = notify
    FakeSMTP.fail = True

    assert module.main({"ALERT_EMAIL": "secret-recipient@example.org", "APP_CONFIG": config}) == 1

    output = capsys.readouterr()
    assert "secret-recipient" not in output.out + output.err and "SMTPException" in output.err


def test_alert_refuses_header_injection(notify):
    module, config = notify
    assert module.main({"ALERT_EMAIL": "a@example.org\nBcc: x@example.org", "APP_CONFIG": config}) == 1
    assert not FakeSMTP.sent


# --- unit files ------------------------------------------------------------

def _unit(name):
    return (BACKUP / name).read_text()


def test_backup_units_start_the_copy_and_alert_by_mail():
    for name in ("mielenosoitukset-mongodb-backup.service", "mielenosoitukset-mongodb-backup-full.service"):
        text = _unit(name)
        assert "OnSuccess=mielenosoitukset-offsite-copy.service" in text
        assert "OnFailure=mielenosoitukset-backup-failed.service" in text
    for name in ("mielenosoitukset-offsite-copy.service", "mielenosoitukset-offsite-check.service"):
        assert "OnFailure=mielenosoitukset-backup-failed.service" in _unit(name)
    failed = _unit("mielenosoitukset-backup-failed.service")
    assert "backup-notify.py" in failed and "EnvironmentFile=-/etc/mielenosoitukset/backup.env" in failed


def test_repository_holds_no_deployment_specific_values():
    text = "".join(path.read_text() for path in BACKUP.iterdir() if path.is_file())
    assert "AGE-SECRET-KEY-1" not in text
    assert "@gmail" not in text and "192.168." not in text and "10.0." not in text


def test_units_have_run_time_limits_and_the_catch_up_stays_at_night():
    assert "RuntimeMaxSec=3600" in _unit("mielenosoitukset-mongodb-backup.service")
    assert "RuntimeMaxSec=5400" in _unit("mielenosoitukset-mongodb-backup-full.service")
    assert "RuntimeMaxSec=3600" in _unit("mielenosoitukset-offsite-copy.service")
    timer = _unit("mielenosoitukset-offsite-copy.timer")
    assert "OnCalendar=*-*-* 01:50:00 UTC" in timer and "Persistent=" not in timer


# --- Bark push --------------------------------------------------------------

import http.server
import json
import threading


@pytest.fixture
def bark_server(notify):
    module, config = notify
    received = []
    reply = {"code": 200}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            if reply.get("redirect"):
                self.send_response(307)
                self.send_header("Location", reply["redirect"])
                self.end_headers()
                return
            body = json.dumps(reply).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    module.BARK_SCHEMES = ("https", "http")  # only the tests talk to a plain local server
    yield module, config, f"http://127.0.0.1:{server.server_port}", received, reply
    server.shutdown()


def test_bark_push_carries_only_label_and_check_name(bark_server):
    module, config, url, received, _ = bark_server
    env = {"BARK_KEY": "devkey123", "BARK_URL": url, "ALERT_EMAIL": "maintainer@example.org", "APP_CONFIG": config,
           "MONITOR_UNIT": "mielenosoitukset-offsite-check.service", "ALERT_LABEL": "site"}

    assert module.main(env) == 0

    path, payload = received[0]
    assert path == "/push" and payload["device_key"] == "devkey123"
    assert payload["title"] == "site: backup problem" and payload["body"] == "mielenosoitukset-offsite-check.service"
    assert not FakeSMTP.sent  # Bark worked, so no mail


def test_failed_bark_falls_back_to_email_and_never_logs_the_key(bark_server, capsys):
    module, config, url, received, reply = bark_server
    reply.update(code=400)
    env = {"BARK_KEY": "devkey123", "BARK_URL": url, "ALERT_EMAIL": "maintainer@example.org", "APP_CONFIG": config}

    assert module.main(env) == 0

    assert len(FakeSMTP.sent) == 1
    output = capsys.readouterr()
    assert "devkey123" not in output.out + output.err and "RuntimeError" in output.err


def test_failed_bark_without_email_exits_nonzero(bark_server, capsys):
    module, config, url, received, reply = bark_server
    reply.update(code=400)
    assert module.main({"BARK_KEY": "devkey123", "BARK_URL": url, "APP_CONFIG": config}) == 1
    output = capsys.readouterr()
    assert "devkey123" not in output.out + output.err


def test_bark_refuses_plain_http_and_unreachable_servers(notify, capsys):
    module, config = notify
    env = {"BARK_KEY": "devkey123", "BARK_URL": "http://127.0.0.1:9", "APP_CONFIG": config}
    assert module.main(env) == 1
    assert "ValueError" in capsys.readouterr().err
    module.BARK_SCHEMES = ("https", "http")
    assert module.main(env) == 1  # nothing listens on port 9
    output = capsys.readouterr()
    assert "devkey123" not in output.out + output.err


def test_bark_redirect_counts_as_a_failed_push_and_is_not_followed(bark_server, capsys):
    module, config, url, received, reply = bark_server
    reply["redirect"] = url + "/elsewhere"

    assert module.main({"BARK_KEY": "devkey123", "BARK_URL": url, "APP_CONFIG": config}) == 1

    assert [path for path, _ in received] == ["/push"]  # the redirect target was never requested
    output = capsys.readouterr()
    assert "HTTPError" in output.err and "devkey123" not in output.out + output.err
