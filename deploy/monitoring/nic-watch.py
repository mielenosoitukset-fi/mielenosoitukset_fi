#!/usr/bin/env python3
"""E-mail the maintainer when a database host's network stack saturates a core.

Two checks, one per run, chosen with --mode:

  softirq  Reads a node-exporter /metrics URL. Alerts when any single CPU spent
           more than THRESHOLD (default 0.8) of the last WINDOW seconds (default
           300) in softirq time. A single-queue virtio NIC pins one core in
           ksoftirqd long before the host looks busy overall.
  kernel   Reads the kernel log of the last LOOKBACK minutes (default 6). Alerts
           on "NETDEV WATCHDOG" or "virtio_net ... TX timeout" lines.

Run softirq from a host that can reach node-exporter (it keeps a small state
file). Run kernel on the host being watched. Mail goes through the same helper as
the backup alerts (backup-notify.py). The message names the host and the check
only; it carries no log lines. A cooldown (default 6 h) stops repeat mails.
See docs/nic-watch.md.
"""
import argparse
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
KERNEL_PATTERN = re.compile(r"NETDEV WATCHDOG|virtio_net.*TX timeout|TX timeout.*virtio", re.I)
SOFTIRQ_LINE = re.compile(r'^node_cpu_seconds_total\{[^}]*cpu="(\d+)"[^}]*mode="softirq"[^}]*\}\s+([0-9.eE+-]+)\s*$')


def load_notify():
    path = os.path.join(HERE, "..", "backup", "backup-notify.py")
    spec = importlib.util.spec_from_file_location("backup_notify", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_softirq(text):
    """Return {cpu: cumulative softirq seconds} from node-exporter text."""
    result = {}
    for line in text.splitlines():
        match = SOFTIRQ_LINE.match(line.strip())
        if match:
            result[match.group(1)] = float(match.group(2))
    return result


def hot_cpus(samples, now, window, threshold):
    """CPUs whose average softirq share over the window exceeds the threshold.

    samples is a list of (timestamp, {cpu: seconds}), oldest first. Returns
    [] until the history covers the whole window.
    """
    old = None
    for stamp, values in samples:
        if now - stamp >= window:
            old = (stamp, values)
    if old is None:
        return []
    newest_stamp, newest = samples[-1]
    span = newest_stamp - old[0]
    if span <= 0:
        return []
    hot = [cpu for cpu, value in newest.items() if cpu in old[1] and (value - old[1][cpu]) / span > threshold]
    return sorted(hot, key=int)


def kernel_hit(log_text):
    return any(KERNEL_PATTERN.search(line) for line in log_text.splitlines())


def load_state(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(tmp, path)


def send_alert(notify, subject_tail, body, env):
    recipient = (env.get("ALERT_EMAIL") or "").strip()
    if not recipient:
        print("nic-watch: ALERT_EMAIL is not set; no mail sent", file=sys.stderr)
        return True
    host = socket.gethostname()
    mail_config = {}
    try:
        import yaml

        with open(env.get("APP_CONFIG", notify.APP_CONFIG), encoding="utf-8") as handle:
            mail_config = (yaml.safe_load(handle) or {}).get("MAIL") or {}
    except (OSError, ImportError):
        pass
    message = EmailMessage()
    message["Subject"] = f"[mielenosoitukset.fi] {subject_tail}"
    message["From"] = mail_config.get("DEFAULT_SENDER") or mail_config.get("USERNAME") or f"alerts@{host}"
    message["To"] = recipient
    message.set_content(body + f"\nSent {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from {host}.\n")
    try:
        notify.send(message, mail_config)
    except Exception as error:  # never print the text: it can echo the recipient
        print(f"nic-watch: sending failed ({type(error).__name__})", file=sys.stderr)
        return False
    return True


def main(argv=None, env=None, now=None):
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("softirq", "kernel"), required=True)
    parser.add_argument("--url", help="node-exporter metrics URL (softirq mode)")
    parser.add_argument("--label", default="the database host", help="name used in the mail")
    parser.add_argument("--state", default="/var/lib/mielenosoitukset/nic-watch.json")
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument("--window", type=int, default=300)
    parser.add_argument("--lookback", type=int, default=6, help="minutes of kernel log (kernel mode)")
    parser.add_argument("--cooldown", type=int, default=6 * 3600)
    args = parser.parse_args(argv)
    now = time.time() if now is None else now
    state = load_state(args.state)
    problem = None

    if args.mode == "softirq":
        if not args.url:
            parser.error("--url is required for softirq mode")
        try:
            with urllib.request.urlopen(args.url, timeout=10) as response:
                current = parse_softirq(response.read().decode("utf-8", "replace"))
        except (OSError, ValueError) as error:
            print(f"nic-watch: cannot read metrics ({type(error).__name__})", file=sys.stderr)
            return 1
        samples = [tuple(item) for item in state.get("samples", []) if now - item[0] <= args.window * 2]
        samples.append((now, current))
        state["samples"] = samples
        hot = hot_cpus(samples, now, args.window, args.threshold)
        if hot:
            problem = (
                f"Network softirq load on {args.label}",
                f"CPU {', '.join(hot)} on {args.label} spent more than {int(args.threshold * 100)} % of the last "
                f"{args.window // 60} minutes handling network interrupts. The site may answer 503. "
                "See docs/nic-watch.md.\n",
            )
    else:
        result = subprocess.run(
            ["journalctl", "-k", "--since", f"-{args.lookback} min", "-o", "cat", "--no-pager", "-n", "2000"],
            capture_output=True, text=True, timeout=30,
        )
        if kernel_hit(result.stdout):
            problem = (
                f"Network driver fault on {args.label}",
                f"The kernel on {args.label} logged a NETDEV WATCHDOG or virtio TX timeout in the last "
                f"{args.lookback} minutes. Read the kernel log of the last 30 minutes. See docs/nic-watch.md.\n",
            )

    ok = True
    if problem and now - state.get(f"alerted_{args.mode}", 0) >= args.cooldown:
        if send_alert(load_notify(), problem[0], problem[1], env):
            state[f"alerted_{args.mode}"] = now
        else:
            ok = False
    save_state(args.state, state)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
