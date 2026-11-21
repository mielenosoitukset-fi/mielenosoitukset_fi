"""Explicit, secret-file opt-in for the Hallinto owner issuer."""

import json
import os
import re
import stat

from pymongo.uri_parser import parse_uri

from mielenosoitukset_fi.utils.audit_trail import mongo_client

_MAX_CONFIG_BYTES = 64 * 1024
_MONGO_DATABASE = re.compile(r"hallinto(?:_[a-z0-9_]+)?\Z")
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# Any of these disables certificate or hostname verification for the issuer
# state connection, including over loopback.
_INSECURE_TLS_OPTIONS = (
    "tlsinsecure", "tlsallowinvalidcertificates", "tlsallowinvalidhostnames",
    "tlsdisableocspendpointcheck",
)


class ConfigFileError(RuntimeError):
    """A secret config file could not be read; ``reason`` is only an exception class name."""

    def __init__(self, message, reason):
        super().__init__(f"{message} ({reason})")
        self.reason = reason


def config_error_reason(exc):
    """Name the failure for the service journal without echoing values.

    A boot failure must say whether the file was unreadable (for example a
    ``PermissionError`` after a directory mode change) or malformed, while
    config values, paths and parser messages stay out of the log.
    """
    return exc.reason if isinstance(exc, ConfigFileError) else type(exc).__name__


def _read_settings(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                    or info.st_uid not in {0, os.geteuid()}):
                raise ValueError
            raw = handle.read(_MAX_CONFIG_BYTES + 1)
            if len(raw) > _MAX_CONFIG_BYTES:
                raise ValueError
            settings = json.loads(raw)
        if not isinstance(settings, dict):
            raise ValueError
        return settings
    except (OSError, ValueError) as exc:
        # Config values, paths and parser error details may contain secrets, so
        # only the exception class is kept.
        raise ConfigFileError("Hallinto issuer needs a readable, restricted JSON file",
                              type(exc).__name__) from None


def _open_state_database(settings, owner_db):
    name = settings.get("mongo_database")
    uri = settings.get("mongo_uri")
    if not isinstance(name, str) or not _MONGO_DATABASE.fullmatch(name) or name == owner_db.name:
        raise ValueError
    # An explicit URI avoids startup SRV resolution while the optional service
    # is down. Production TLS is mandatory beyond a loopback-only Mongo socket.
    if not isinstance(uri, str) or not uri.startswith("mongodb://"):
        raise ValueError
    parsed = parse_uri(uri)
    if not parsed["username"] or not parsed["password"] or parsed["database"] != name:
        raise ValueError
    # pymongo returns option names in their canonical camelCase spelling, so
    # compare case-insensitively rather than relying on one key form.
    options = {str(key).lower(): value for key, value in parsed["options"].items()}
    if any(options.get(option) for option in _INSECURE_TLS_OPTIONS):
        raise ValueError
    if not all(host in _LOOPBACK for host, _ in parsed["nodelist"]) and not options.get("tls"):
        raise ValueError
    # Do not let issuer downtime stall unrelated Flask authentication traffic.
    # The audited factory records issuer writes in this database's
    # audit_events; a refused audit write falls back to the service journal.
    client = mongo_client(
        uri, connect=False, serverSelectionTimeoutMS=1500, timeoutMS=2500,
        w="majority", journal=True, readPreference="primary",
    )
    return client[name]


def configure_hallinto_issuer(app, owner_db):
    """Register only on explicit operator opt-in or a synthetic test override."""
    test_settings = app.config.get("HALLINTO_OIDC") if app.testing else None
    path = os.environ.get("HALLINTO_ISSUER_CONFIG_FILE")
    if not path and not test_settings:
        return

    from mielenosoitukset_fi.users.hallinto_issuer import register_hallinto_issuer

    if test_settings:
        # Test fixtures pass their disposable account database explicitly.
        register_hallinto_issuer(app, owner_db, test_settings, state_db=owner_db)
        return
    settings = _read_settings(path)
    try:
        required = ("issuer", "client_id", "client_secret", "redirect_uri", "key_id", "private_key")
        if not all(isinstance(settings.get(field), str) and settings[field] for field in required[:-1]):
            raise ValueError
        if not isinstance(settings.get("private_key"), dict):
            raise ValueError
        state = _open_state_database(settings, owner_db)
        register_hallinto_issuer(app, owner_db, settings, state_db=state)
    except Exception as exc:
        # Construction failures can include private key or URI values.
        raise RuntimeError(
            f"Hallinto issuer configuration is invalid ({config_error_reason(exc)})") from None
