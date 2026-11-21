#!/usr/bin/env python3
"""Check, before a reload, that the Flask service user can read its config files.

Every ``*_CONFIG_FILE`` variable in the unit's environment (including drop-ins)
names a secret file that ``create_app`` opens while a new worker boots. If the
service user cannot read one, every replacement worker fails, gunicorn halts and
the old workers go down with it. On 21 November 2026 a directory mode change made
such a file unreadable, and the next reload caused a 503 outage of about five
minutes. Running this check before the HUP fails the deploy while the old
workers keep serving.

Only variable names and paths are printed, never file contents.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys

CONFIG_VARIABLE = re.compile(r"[A-Z][A-Z0-9_]*_CONFIG_FILE\Z")


def unit_settings(service: str, systemctl: str = "systemctl") -> tuple[str, dict[str, str]]:
    """Return the unit's ``User`` and its ``*_CONFIG_FILE`` environment entries."""
    output = subprocess.run(
        [systemctl, "show", service, "--property=User", "--property=Environment"],
        check=True, capture_output=True, text=True,
    ).stdout
    user, files = "", {}
    for line in output.splitlines():
        key, _, value = line.partition("=")
        if key == "User":
            user = value.strip()
        elif key == "Environment":
            for assignment in shlex.split(value):
                name, sep, path = assignment.partition("=")
                if sep and CONFIG_VARIABLE.fullmatch(name) and path:
                    files[name] = path
    return user, files


def readable_as(user: str, path: str, runuser: str = "runuser") -> bool:
    if not user or user == "root":
        command = ["test", "-f", path, "-a", "-r", path]
    else:
        command = [runuser, "-u", user, "--", "test", "-f", path, "-a", "-r", path]
    return subprocess.run(command, capture_output=True).returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("service")
    parser.add_argument("--systemctl", default="systemctl")
    parser.add_argument("--runuser", default="runuser")
    args = parser.parse_args(argv)
    try:
        user, files = unit_settings(args.service, args.systemctl)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"check_service_config_files: cannot read the {args.service} unit ({type(exc).__name__})",
              file=sys.stderr)
        return 1
    failed = [(name, path) for name, path in sorted(files.items())
              if not readable_as(user, path, args.runuser)]
    for name, path in failed:
        print(f"check_service_config_files: {name}={path} is not a file {user or 'root'} can read; "
              "a reload would leave no worker able to boot", file=sys.stderr)
    if failed:
        return 1
    print(f"Service config files readable by {user or 'root'}: {len(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
