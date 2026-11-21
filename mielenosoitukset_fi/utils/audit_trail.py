"""Driver-level audit trail: every database write leaves a trace.

Maintainer request (20 November 2026): "every edit, no matter how it is done,
should leave a trace so no untraceable edits can happen".

Instead of asking each of the ~480 write call sites to remember a log call,
every MongoDB client the application creates carries ``AuditCommandListener``.
PyMongo reports each command to it, so a write through the admin UI, a public
form, an MCP or API token, an organiser edit link, a background job, a script
or a migration is recorded the same way, in ``audit_events`` of the database
that was written:

* who: ``actor`` (user id, token, job or script; never an email, name or IP),
* when: ``at`` (UTC),
* what: ``collection``, ``op``, ``filter``, ``change`` (the update operators,
  i.e. the diff applied, or the inserted document) and ``doc_ids``,
* through which path: ``via`` (HTTP endpoint and method, job key, or script),
* outcome: ``outcome`` with matched/modified counts or the error code.

Secrets are never stored: values under secret-looking keys, password-hash
shaped strings and whole documents of credential collections are replaced by
``[redacted]``. String values in filters are kept only for id-like keys, so a
lookup by email does not copy the address into the trail.

The only way around the trail is a MongoDB client created without the
listener; ``tests/test_audit_trail_coverage.py`` fails when code outside this
module constructs one. ``mongo_client()`` is the factory to use instead.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import socket
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from bson import ObjectId
from pymongo import MongoClient, monitoring

from mielenosoitukset_fi.utils.logger import logger

AUDIT_COLLECTION = "audit_events"
SCHEMA_VERSION = 1
DEFAULT_RETENTION_DAYS = 730  # same default as account erasure (#1268)

# High-volume telemetry and lock heartbeats: not edits of anyone's data, and
# auditing them would double the database load. Adding a name here needs a
# reason in the comment and in docs/audit-trail.md.
SKIPPED_COLLECTIONS = frozenset(
    {
        AUDIT_COLLECTION,  # the trail itself
        "analytics",  # per-demonstration view counters
        "d_analytics",
        "prepped_analytics",
        "site_analytics",  # page view counters
        "site_analytics_visitors",
        "web_vitals_daily",
        "api_usage",  # per-request API counters
        "background_job_leader",  # leader lock heartbeat every few seconds
    }
)

# Append-only logs. Their inserts are records already; updates and deletes of
# them are still audited, so nobody can quietly rewrite a log.
APPEND_ONLY_LOGS = frozenset(
    {
        "admin_logs",
        "super_audit_logs",
        "demo_audit_logs",
        "login_logs",
        "board_audit_logs",
        "demo_edit_history",
        "background_job_runs",
        "token_usage_logs",
    }
)

# Credential stores: only keys, ids, timestamps and flags are kept.
SECRET_COLLECTIONS = frozenset(
    {
        "api_tokens",
        "magic_links",
        "mfas",
        "mfa_login_challenges",
        "user_passkeys",
        "passkey_challenges",
        "fresh_step_up_tokens",
        "used_password_change_tokens",
        "demo_cancellation_tokens",
        "demo_submission_tokens",
        "contact_form_tokens",
        "hallinto_sessions",
        "hallinto_oidc_codes",
        "hallinto_login_markers",
        "oauth_authorization_codes",
        "erasure_identity_seals",
    }
)

_SECRET_KEY = re.compile(
    r"(pass(word|wd)?|pwd|hash|secret|token|cookie|session|otp|totp|"
    r"recovery|backup_code|api_?key|authori[sz]ation|credential|private|"
    r"signature|nonce|challenge|seal|(^|_)codes?$|^code_|"
    # Network identifiers have their own shorter retention (suggestion IPs,
    # login logs); the trail must not keep a second copy for two years.
    r"(^|_)ips?$|ip_address|remote_addr|user_agent|forwarded)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"^(\$2[aby]\$|\$argon2|\$scrypt|pbkdf2:|scrypt:|sha256\$|gAAAA|eyJ)"
    r"|^\d{1,3}(\.\d{1,3}){3}$|^[0-9a-fA-F]{0,4}(:[0-9a-fA-F]{0,4}){2,7}$"
)
_ID_KEY = re.compile(r"(^_?id$|_ids?$|^slug$|^status$|^type$|^kind$|^job_key$|^key$)")

REDACTED = "[redacted]"
MAX_STRING = 300
MAX_LIST = 50
MAX_DEPTH = 8

WRITE_COMMANDS = frozenset({"insert", "update", "delete", "findAndModify", "findandmodify"})
DDL_COMMANDS = frozenset(
    {
        "create",
        "drop",
        "dropDatabase",
        "renameCollection",
        "createIndexes",
        "dropIndexes",
        "collMod",
    }
)

_scope: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "audit_scope", default=None
)
_local = threading.local()


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
def _scalar(value: Any) -> Any:
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, (datetime, ObjectId)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return f"[bytes {len(value)}]"
    if isinstance(value, str):
        if _SECRET_VALUE.match(value):
            return REDACTED
        if len(value) > MAX_STRING:
            return value[:MAX_STRING] + f"…[{len(value)} chars]"
        return value
    return str(value)[:MAX_STRING]


def _is_harmless(value: Any) -> bool:
    return isinstance(value, (bool, int, float, datetime, ObjectId)) or value is None


def redact(value: Any, *, secret_collection: bool = False, filter_mode: bool = False,
           key: str = "", depth: int = 0) -> Any:
    """Copy ``value`` for the trail without secrets.

    ``key`` is the document field the value belongs to; query and update
    operators (``$set``, ``$in`` ...) pass their field on to their operands.
    ``secret_collection`` keeps only ids, timestamps and flags.
    ``filter_mode`` keeps string values only under id-like keys.
    """
    if depth > MAX_DEPTH:
        return "[depth]"
    last = key.rsplit(".", 1)[-1]
    id_key = bool(last) and bool(_ID_KEY.search(last))
    secret_key = bool(key) and bool(_SECRET_KEY.search(key)) and not id_key
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            k = str(k)
            if k in ("$or", "$and", "$nor"):
                child = ""
            elif k.startswith("$"):
                child = key
            elif secret_key:
                out[k] = v if _is_harmless(v) else REDACTED
                continue
            else:
                child = k
            out[k] = redact(v, secret_collection=secret_collection, filter_mode=filter_mode,
                            key=child, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        items = [
            redact(v, secret_collection=secret_collection, filter_mode=filter_mode,
                   key=key, depth=depth + 1)
            for v in list(value)[:MAX_LIST]
        ]
        if len(value) > MAX_LIST:
            items.append(f"…[{len(value)} items]")
        return items
    if _is_harmless(value):
        return value
    if secret_key or (secret_collection and not id_key):
        return REDACTED
    if filter_mode and isinstance(value, str) and not id_key:
        return "[str]"
    return _scalar(value)


# --------------------------------------------------------------------------- #
# Who and through which path
# --------------------------------------------------------------------------- #
@contextmanager
def audit_scope(kind: str, name: str, *, actor: Optional[Dict[str, Any]] = None,
                reason: Optional[str] = None, **extra: Any):
    """Name the path for writes made inside the block (jobs, scripts, CLI).

    >>> with audit_scope("job", "merge_duplicates", run_id=str(run_id)):
    ...     merge()
    """
    data = {"kind": kind, "name": name, **{k: str(v) for k, v in extra.items()}}
    if actor:
        data["actor"] = actor
    if reason:
        data["reason"] = reason
    token = _scope.set(data)
    try:
        yield
    finally:
        _scope.reset(token)


def set_request_actor(kind: str, *, user_id: Any = None, token_id: Any = None,
                      name: Optional[str] = None) -> None:
    """Record a non-session principal (API token, MCP token) for this request."""
    try:
        from flask import g, has_app_context

        if not has_app_context():
            return
        actor = {"kind": kind}
        if user_id:
            actor["user_id"] = str(user_id)
        if token_id:
            actor["token_id"] = str(token_id)
        if name:
            actor["name"] = str(name)[:80]
        g._audit_actor = actor
    except Exception:  # pragma: no cover - never break the request
        logger.debug("Could not set audit actor", exc_info=True)


def _process_via() -> Dict[str, Any]:
    script = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else "python"
    return {
        "kind": "process",
        "script": script,
        "thread": threading.current_thread().name,
    }


def _context() -> Dict[str, Any]:
    scope = _scope.get()
    via: Dict[str, Any]
    actor: Dict[str, Any] = {"kind": "system"}
    request_id = None
    try:
        from flask import g, has_request_context, request

        if has_request_context():
            rule = request.url_rule.rule if request.url_rule is not None else None
            via = {
                "kind": "http",
                "service": "flask",
                "method": request.method,
                "endpoint": request.endpoint,
                "rule": rule,
            }
            if not hasattr(g, "_audit_request_id"):
                g._audit_request_id = uuid.uuid4().hex
            request_id = g._audit_request_id
            actor = getattr(g, "_audit_actor", None) or {"kind": "anonymous"}
            # Read the already-loaded user only; loading it here would query
            # the database from inside the driver callback.
            user = getattr(g, "_login_user", None)
            if user is not None and getattr(user, "is_authenticated", False) and actor.get("kind") == "anonymous":
                actor = {"kind": "user", "user_id": str(getattr(user, "id", "") or "")}
                duty = getattr(user, "active_duty", None)
                if duty:
                    actor["duty_shift_id"] = duty["shift_id"]
            if request.endpoint and ("token" in request.endpoint or "magic" in request.endpoint) and actor["kind"] == "anonymous":
                actor = {"kind": "link"}
        else:
            via = _process_via()
    except Exception:  # pragma: no cover - Flask missing in a script
        via = _process_via()
    if scope:
        via = {**via, **{k: v for k, v in scope.items() if k not in ("actor", "reason")}}
        if scope.get("actor"):
            actor = scope["actor"]
        elif actor.get("kind") == "system":
            actor = {"kind": scope["kind"], "name": scope["name"]}
    via["host"] = socket.gethostname()
    via["pid"] = os.getpid()
    ctx = {"actor": actor, "via": via}
    if request_id:
        ctx["request_id"] = request_id
    if scope and scope.get("reason"):
        ctx["reason"] = scope["reason"]
    return ctx


# --------------------------------------------------------------------------- #
# Turning a command into an event
# --------------------------------------------------------------------------- #
def _doc_ids(docs: Iterable[Any]) -> list:
    ids = []
    for doc in docs:
        if isinstance(doc, dict) and "_id" in doc:
            ids.append(doc["_id"])
        if len(ids) >= MAX_LIST:
            break
    return ids


def _filter_ids(flt: Any) -> list:
    if isinstance(flt, dict) and "_id" in flt:
        value = flt["_id"]
        if isinstance(value, dict) and isinstance(value.get("$in"), list):
            return list(value["$in"])[:MAX_LIST]
        if not isinstance(value, dict):
            return [value]
    return []


def describe_command(command_name: str, command: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the audit body for a write command, or None for reads/skips."""
    name = command_name
    if name == "aggregate":
        stages = command.get("pipeline") or []
        out = next((s for s in stages if isinstance(s, dict) and ("$out" in s or "$merge" in s)), None)
        if out is None:
            return None
        target = out.get("$out") or out.get("$merge")
        if isinstance(target, dict):
            target = target.get("into") or target.get("coll")
        if isinstance(target, dict):
            target = target.get("coll")
        return {"op": "aggregate_out", "collection": str(target),
                "source": str(command.get("aggregate"))}
    if name not in WRITE_COMMANDS and name not in DDL_COMMANDS:
        return None

    target = command.get(name)
    if name == "renameCollection":
        collection = str(target).split(".", 1)[-1]
    elif name == "dropDatabase":
        collection = "*"
    else:
        collection = str(target)
    if collection in SKIPPED_COLLECTIONS:
        return None
    secret = collection in SECRET_COLLECTIONS

    if name == "insert":
        if collection in APPEND_ONLY_LOGS:
            return None
        docs = command.get("documents") or []
        return {
            "op": "insert",
            "collection": collection,
            "doc_ids": _doc_ids(docs),
            "change": [redact(d, secret_collection=secret) for d in docs[:20]],
            "count": len(docs),
        }
    if name == "update":
        updates = command.get("updates") or []
        return {
            "op": "update",
            "collection": collection,
            "doc_ids": [i for u in updates for i in _filter_ids(u.get("q"))][:MAX_LIST],
            "filter": [redact(u.get("q"), filter_mode=True) for u in updates[:20]],
            "change": [redact(u.get("u"), secret_collection=secret) for u in updates[:20]],
            "multi": any(u.get("multi") for u in updates),
            "upsert": any(u.get("upsert") for u in updates),
            "count": len(updates),
        }
    if name == "delete":
        deletes = command.get("deletes") or []
        return {
            "op": "delete",
            "collection": collection,
            "doc_ids": [i for d in deletes for i in _filter_ids(d.get("q"))][:MAX_LIST],
            "filter": [redact(d.get("q"), filter_mode=True) for d in deletes[:20]],
            "multi": any(d.get("limit", 1) == 0 for d in deletes),
            "count": len(deletes),
        }
    if name in ("findAndModify", "findandmodify"):
        body = {
            "op": "delete" if command.get("remove") else "update",
            "collection": collection,
            "doc_ids": _filter_ids(command.get("query")),
            "filter": [redact(command.get("query"), filter_mode=True)],
            "upsert": bool(command.get("upsert")),
            "count": 1,
        }
        if not command.get("remove"):
            body["change"] = [redact(command.get("update"), secret_collection=secret)]
        return body
    # DDL: schema and index changes from migrations and scripts.
    body = {"op": f"ddl:{name}", "collection": collection}
    detail = {k: v for k, v in command.items() if k not in (name, "lsid", "$db", "$clusterTime", "txnNumber")}
    if detail:
        body["change"] = [redact(detail)]
    if name == "renameCollection":
        body["to"] = str(command.get("to"))
    return body


def _outcome(command_name: str, reply: Dict[str, Any]) -> Dict[str, Any]:
    outcome: Dict[str, Any] = {"ok": bool(reply.get("ok", 1))}
    if command_name in ("findAndModify", "findandmodify"):
        last = reply.get("lastErrorObject") or {}
        outcome["n"] = int(last.get("n", 0))
        if last.get("upserted") is not None:
            outcome["upserted"] = [last["upserted"]]
        value = reply.get("value")
        if isinstance(value, dict) and "_id" in value:
            outcome["doc_id"] = value["_id"]
        return outcome
    if "n" in reply:
        outcome["n"] = int(reply.get("n") or 0)
    if "nModified" in reply:
        outcome["modified"] = int(reply.get("nModified") or 0)
    upserted = reply.get("upserted")
    if upserted:
        outcome["upserted"] = [u.get("_id") for u in upserted if isinstance(u, dict)]
    errors = reply.get("writeErrors")
    if errors:
        outcome["ok"] = False
        outcome["errors"] = [int(e.get("code", 0)) for e in errors][:10]
    return outcome


def _changed_nothing(op: str, outcome: Dict[str, Any]) -> bool:
    """Writes that matched nothing, or matched but changed nothing, are not edits.

    An update that sets every field to the value it already has reports
    ``nModified == 0``; MongoDB did not touch the document, so there is
    nothing to trace.
    """
    if not outcome.get("ok", True) or outcome.get("errors"):
        return False
    if outcome.get("upserted"):
        return False
    if op in ("update", "delete") and outcome.get("n") == 0:
        return True
    if op == "update" and outcome.get("modified") == 0:
        return True
    return False


# --------------------------------------------------------------------------- #
# Writing the event
# --------------------------------------------------------------------------- #
class _Sink:
    """Writes events with a client of its own (no listener, no recursion)."""

    def __init__(self, uri: Optional[str], client_kwargs: Dict[str, Any]):
        self._uri = uri
        self._kwargs = client_kwargs
        self._client = None
        self._pid = None
        self._indexed: set = set()
        self._lock = threading.Lock()

    def _get_client(self):
        if self._client is None or self._pid != os.getpid():
            with self._lock:
                if self._client is None or self._pid != os.getpid():
                    kwargs = {k: v for k, v in self._kwargs.items()
                              if k not in ("event_listeners", "maxPoolSize", "minPoolSize")}
                    kwargs.setdefault("serverSelectionTimeoutMS", 2000)
                    kwargs.setdefault("socketTimeoutMS", 2000)
                    kwargs.setdefault("connectTimeoutMS", 2000)
                    self._client = MongoClient(self._uri, maxPoolSize=10, **kwargs)
                    self._pid = os.getpid()
                    self._indexed = set()
        return self._client

    def _ensure_indexes(self, collection, db_name: str) -> None:
        if db_name in self._indexed:
            return
        self._indexed.add(db_name)
        try:
            days = int(os.environ.get("AUDIT_RETENTION_DAYS") or DEFAULT_RETENTION_DAYS)
        except ValueError:
            days = DEFAULT_RETENTION_DAYS
        try:
            collection.create_index("at", expireAfterSeconds=max(days, 30) * 86400,
                                    name="audit_retention")
            collection.create_index([("collection", 1), ("doc_ids", 1), ("at", -1)],
                                    name="audit_by_document")
            collection.create_index([("actor.user_id", 1), ("at", -1)], name="audit_by_actor")
        except Exception:
            logger.warning("Could not ensure audit_events indexes", exc_info=True)

    def write(self, db_name: str, event: Dict[str, Any]) -> None:
        try:
            collection = self._get_client()[db_name][AUDIT_COLLECTION]
            self._ensure_indexes(collection, db_name)
            collection.insert_one(event)
        except Exception:
            # Fallback sink: the service journal keeps the trace when the
            # database refuses it. Never raise into the write that happened.
            logger.error("AUDIT_FALLBACK %s", json.dumps(event, default=str)[:8000])


class AuditCommandListener(monitoring.CommandListener):
    """Records every write command the client sends."""

    def __init__(self, uri: Optional[str] = None, client_kwargs: Optional[Dict[str, Any]] = None,
                 sink: Any = None):
        self._sink = sink or _Sink(uri, client_kwargs or {})
        self._pending: Dict[Any, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def started(self, event):
        try:
            if getattr(_local, "busy", False):
                return
            body = describe_command(event.command_name, event.command)
            if body is None:
                return
            entry = {
                "v": SCHEMA_VERSION,
                "at": datetime.now(timezone.utc),
                "db": event.database_name,
                "service": "python",
                **body,
                **_context(),
            }
            with self._lock:
                self._pending[(event.request_id, event.connection_id)] = entry
        except Exception:  # pragma: no cover - never break the write
            logger.exception("Audit listener failed to capture %s", event.command_name)

    def _finish(self, event, outcome: Dict[str, Any]):
        with self._lock:
            entry = self._pending.pop((event.request_id, event.connection_id), None)
        if entry is None:
            return
        entry["outcome"] = outcome
        entry["duration_ms"] = round(getattr(event, "duration_micros", 0) / 1000, 2)
        if _changed_nothing(entry["op"], outcome):
            return
        _local.busy = True
        try:
            self._sink.write(entry["db"], entry)
        finally:
            _local.busy = False

    def succeeded(self, event):
        try:
            self._finish(event, _outcome(event.command_name, event.reply or {}))
        except Exception:  # pragma: no cover
            logger.exception("Audit listener failed to record %s", event.command_name)

    def failed(self, event):
        try:
            failure = event.failure or {}
            self._finish(event, {"ok": False, "error_code": int(failure.get("code", 0) or 0)})
        except Exception:  # pragma: no cover
            logger.exception("Audit listener failed to record failed %s", event.command_name)


def audit_disabled() -> bool:
    return os.environ.get("AUDIT_TRAIL_DISABLED", "").strip().lower() in ("1", "true", "yes")


def mongo_client(uri: Optional[str] = None, **kwargs: Any) -> MongoClient:
    """Create a MongoClient whose writes are audited. Use this, not MongoClient."""
    listeners = list(kwargs.pop("event_listeners", None) or [])
    if not audit_disabled():
        listeners.append(AuditCommandListener(uri, dict(kwargs)))
    else:
        logger.warning("AUDIT_TRAIL_DISABLED is set: database writes are not audited.")
    return MongoClient(uri, event_listeners=listeners, **kwargs)
