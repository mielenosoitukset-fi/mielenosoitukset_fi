#!/usr/bin/env python3
"""E-mail the maintainer that a backup unit failed (OPS-01, docs/backups.md).

Called by mielenosoitukset-backup-failed.service. systemd passes the failed
unit's name in MONITOR_UNIT. The recipient comes from ALERT_EMAIL, set in the
root-only /etc/mielenosoitukset/backup.env, never from the repository. The mail
goes out through the same SMTP account as the site (the MAIL section of the app
config.yaml), with the local sendmail as a fallback.

The message names the unit and host only. It carries no log lines, so a
database error can never leak into a mailbox; the reader runs journalctl.
Exit status: 0 when mailed or when no recipient is set (the caller has already
written the journal line), 1 when mail could not be sent.
"""
import os
import shutil
import smtplib
import socket
import subprocess
import sys
from datetime import datetime, timezone
from email.message import EmailMessage

import yaml

APP_CONFIG = "/var/www/mielenosoitukset_fi/config.yaml"


def build_message(unit, host, sender, recipient, now):
    message = EmailMessage()
    message["Subject"] = f"[mielenosoitukset.fi] Backup problem: {unit} on {host}"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"The unit {unit} failed on {host} at {now:%Y-%m-%d %H:%M} UTC.\n\n"
        f"Look at it on the server with:\n"
        f"  journalctl -u {unit} -n 50 --no-pager\n\n"
        "If the unit is mielenosoitukset-offsite-check, the off-site copy is\n"
        "older than it should be. If it is mielenosoitukset-offsite-copy, the\n"
        "upload or the encryption failed. Otherwise the database dump failed.\n"
        "See docs/backups.md. This mail is sent again each time the unit fails.\n"
    )
    return message


def send(message, mail_config):
    server = mail_config.get("SERVER")
    if server and server != "localhost":
        port = int(mail_config.get("PORT", 587))
        client = smtplib.SMTP_SSL(server, port, timeout=30) if port == 465 else smtplib.SMTP(server, port, timeout=30)
        with client:
            if port != 465 and mail_config.get("USE_TLS", True):
                client.starttls()
            if mail_config.get("USERNAME"):
                client.login(mail_config["USERNAME"], mail_config.get("PASSWORD", ""))
            client.send_message(message)
        return
    sendmail = shutil.which("sendmail") or "/usr/sbin/sendmail"
    subprocess.run([sendmail, "-t", "-i"], input=message.as_bytes(), check=True, timeout=60)


def main(env=None, app_config=APP_CONFIG):
    env = os.environ if env is None else env
    recipient = (env.get("ALERT_EMAIL") or "").strip()
    unit = env.get("MONITOR_UNIT") or "a mielenosoitukset backup unit"
    if not recipient:
        print("backup-notify: ALERT_EMAIL is not set; no mail sent", file=sys.stderr)
        return 0
    if any(char in recipient + unit for char in "\r\n"):
        print("backup-notify: refusing a header with a line break", file=sys.stderr)
        return 1
    try:
        with open(env.get("APP_CONFIG", app_config), encoding="utf-8") as handle:
            mail_config = (yaml.safe_load(handle) or {}).get("MAIL") or {}
    except OSError as error:
        print(f"backup-notify: cannot read the app config: {error.strerror}", file=sys.stderr)
        mail_config = {}
    host = socket.gethostname()
    sender = mail_config.get("DEFAULT_SENDER") or mail_config.get("USERNAME") or f"backup@{host}"
    message = build_message(unit, host, sender, recipient, datetime.now(timezone.utc))
    try:
        send(message, mail_config)
    except (OSError, smtplib.SMTPException, subprocess.SubprocessError) as error:
        # Never print the exception text: it can echo the recipient or credentials.
        print(f"backup-notify: sending failed ({type(error).__name__})", file=sys.stderr)
        return 1
    print("backup-notify: alert mailed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
