#!/usr/bin/env python3
"""Alert the maintainer that a backup unit failed (OPS-01, docs/backups.md).

Channels, tried in this order:
  1. Bark push (iOS) when BARK_KEY is set. BARK_URL is the server base (default
     https://api.day.app), HTTPS only. The push says only the label and the name
     of the failed check: no log lines, no host names.
  2. E-mail to ALERT_EMAIL, when Bark is not configured or failed.
If no channel is configured, or every configured one failed, the script exits 1
so the failure is visible. Settings (BARK_KEY, BARK_URL, ALERT_EMAIL, ALERT_LABEL)
come only from the root-only env file the unit loads, never from the repository.
A failed send logs only the exception type, never the key, URL or address.

Called by mielenosoitukset-backup-failed.service, and usable by other ops
units. systemd passes the failed unit's name in MONITOR_UNIT. E-mail goes
through the same SMTP account as the site (the MAIL section of the app
config.yaml), with the local sendmail as a fallback, and names the unit and host
only. Neither channel carries log lines: the reader opens the unit's journal.
"""
import json
import os
import shutil
import smtplib
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime, timezone
from email.message import EmailMessage

import yaml

APP_CONFIG = "/var/www/mielenosoitukset_fi/config.yaml"
BARK_DEFAULT_URL = "https://api.day.app"
BARK_SCHEMES = ("https",)  # tests widen this for a local fake server


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


def send_bark(env, unit):
    """Push through Bark. Raises on any failure; the caller logs only the type."""
    base = (env.get("BARK_URL") or BARK_DEFAULT_URL).strip().rstrip("/")
    if urlsplit(base).scheme not in BARK_SCHEMES:
        raise ValueError("BARK_URL must be https")
    label = (env.get("ALERT_LABEL") or "mielenosoitukset.fi").strip()
    payload = {"device_key": env["BARK_KEY"].strip(), "title": f"{label}: backup problem", "body": unit, "group": "backup"}
    request = urllib.request.Request(
        base + "/push", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.load(response)
    if result.get("code") != 200:
        raise RuntimeError("Bark refused the push")


def send_email(env, unit, app_config):
    recipient = env["ALERT_EMAIL"].strip()
    try:
        with open(env.get("APP_CONFIG", app_config), encoding="utf-8") as handle:
            mail_config = (yaml.safe_load(handle) or {}).get("MAIL") or {}
    except OSError as error:
        print(f"backup-notify: cannot read the app config: {error.strerror}", file=sys.stderr)
        mail_config = {}
    host = socket.gethostname()
    sender = mail_config.get("DEFAULT_SENDER") or mail_config.get("USERNAME") or f"backup@{host}"
    message = build_message(unit, host, sender, recipient, datetime.now(timezone.utc))
    send(message, mail_config)


def main(env=None, app_config=APP_CONFIG):
    env = os.environ if env is None else env
    unit = env.get("MONITOR_UNIT") or "a mielenosoitukset backup unit"
    has_bark = bool((env.get("BARK_KEY") or "").strip())
    recipient = (env.get("ALERT_EMAIL") or "").strip()
    if any(char in recipient + unit for char in "\r\n"):
        print("backup-notify: refusing a header with a line break", file=sys.stderr)
        return 1
    if not has_bark and not recipient:
        print("backup-notify: no alert channel configured (set BARK_KEY or ALERT_EMAIL); alert NOT sent", file=sys.stderr)
        return 1
    if has_bark:
        try:
            send_bark(env, unit)
            print("backup-notify: Bark push sent")
            return 0
        except Exception as error:  # noqa: BLE001 - log only the type, never the message
            print(f"backup-notify: Bark failed ({type(error).__name__})", file=sys.stderr)
    if recipient:
        try:
            send_email(env, unit, app_config)
        except (OSError, smtplib.SMTPException, subprocess.SubprocessError) as error:
            # Never print the exception text: it can echo the recipient or credentials.
            print(f"backup-notify: sending failed ({type(error).__name__})", file=sys.stderr)
            return 1
        print("backup-notify: alert mailed")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
