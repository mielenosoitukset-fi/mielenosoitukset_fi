"""Write the derived e-mail confirmation key into the Go API environment file.

Run on the Flask host as a user who can read config.yaml and write the target file
(see docs/email-confirm-react.md). It derives the key from SECRET_KEY, sets
EMAIL_CONFIRM_JWT_KEY in the file (replacing an existing line, keeping every other
line) with mode 600, and prints only "written". The key is never printed, never
passed on a command line and never in shell history. SECRET_KEY itself is never
written anywhere.

    sudo /path/to/venv/bin/python -B scripts/write_confirmation_key.py
"""
import argparse
import os
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from mielenosoitukset_fi.utils.auth import derive_confirmation_key  # noqa: E402

NAME = "EMAIL_CONFIRM_JWT_KEY"


def write_key(config_path: str, env_path: str) -> None:
    try:
        with open(config_path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
        secret = loaded.get("SECRET_KEY") if isinstance(loaded, dict) else None
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        # A fixed message: a parser error would quote lines of config.yaml.
        raise SystemExit("config.yaml could not be read; nothing written") from None
    if not isinstance(secret, str) or len(secret) < 32:
        raise SystemExit("SECRET_KEY is missing or too short; nothing written")
    line = f"{NAME}={derive_confirmation_key(secret)}\n"
    try:
        with open(env_path, encoding="utf-8") as handle:
            lines = [existing for existing in handle if not existing.startswith(NAME + "=")]
    except FileNotFoundError:
        lines = []
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(line)
    # Write a private temporary file next to the target, then replace it atomically.
    directory = os.path.dirname(os.path.abspath(env_path))
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".env-")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
        try:
            shutil_stat = os.stat(env_path)
            os.chown(temporary, shutil_stat.st_uid, shutil_stat.st_gid)
        except (FileNotFoundError, PermissionError):
            pass
        os.replace(temporary, env_path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    os.chmod(env_path, 0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="/var/www/mielenosoitukset_fi/config.yaml")
    parser.add_argument("--env-file", default="/etc/mielenosoitukset-api/environment")
    arguments = parser.parse_args()
    write_key(arguments.config, arguments.env_file)
    print("written")


if __name__ == "__main__":
    main()
