"""Every database write leaves a trace in audit_events (no secrets in it)."""

from types import SimpleNamespace

import pytest
from bson import ObjectId

from mielenosoitukset_fi.utils import audit_trail
from mielenosoitukset_fi.utils.audit_trail import (
    REDACTED,
    AuditCommandListener,
    audit_scope,
    describe_command,
    redact,
)


# --------------------------------------------------------------------------- #
# Pure: what an event contains
# --------------------------------------------------------------------------- #
def test_update_records_filter_and_diff_without_secrets():
    demo_id = ObjectId()
    body = describe_command(
        "update",
        {
            "update": "users",
            "updates": [
                {
                    "q": {"_id": demo_id, "email": "someone@example.org"},
                    "u": {
                        "$set": {
                            "displayname": "Uusi nimi",
                            "password_hash": "$2b$12$abcdefghijk",
                            "mfa": {"secret": "JBSWY3DPEHPK3PXP", "enabled": True},
                            "api_tokens_enabled": True,
                        },
                        "$unset": {"reset_token": ""},
                    },
                }
            ],
        },
    )
    assert body["op"] == "update"
    assert body["collection"] == "users"
    assert body["doc_ids"] == [demo_id]
    # A lookup by email does not copy the address into the trail.
    assert body["filter"] == [{"_id": demo_id, "email": "[str]"}]
    change = body["change"][0]
    assert change["$set"]["displayname"] == "Uusi nimi"
    assert change["$set"]["password_hash"] == REDACTED
    assert change["$set"]["mfa"]["secret"] == REDACTED
    assert change["$set"]["mfa"]["enabled"] is True
    assert change["$set"]["api_tokens_enabled"] is True
    assert change["$unset"]["reset_token"] == REDACTED


def test_hash_shaped_values_are_redacted_under_any_key():
    assert redact({"note": "$argon2id$v=19$m=65536"}) == {"note": REDACTED}
    assert redact({"note": "eyJhbGciOiJIUzI1NiJ9.e30.x"}) == {"note": REDACTED}
    assert redact({"note": "tavallinen teksti"}) == {"note": "tavallinen teksti"}


def test_credential_collections_keep_only_ids_and_flags():
    user_id = ObjectId()
    body = describe_command(
        "insert",
        {
            "insert": "magic_links",
            "documents": [{"_id": "abc", "user_id": user_id, "link": "https://x/t/123", "used": False}],
        },
    )
    doc = body["change"][0]
    assert doc["user_id"] == user_id
    assert doc["used"] is False
    assert doc["link"] == REDACTED


def test_reads_skipped_collections_and_log_inserts_are_not_events():
    assert describe_command("find", {"find": "users", "filter": {}}) is None
    assert describe_command("update", {"update": "site_analytics", "updates": [{"q": {}, "u": {}}]}) is None
    assert describe_command("insert", {"insert": "admin_logs", "documents": [{}]}) is None
    # Rewriting or deleting a log is an edit and is recorded.
    assert describe_command("delete", {"delete": "admin_logs", "deletes": [{"q": {}, "limit": 0}]})["multi"]


def test_schema_changes_and_aggregate_out_are_recorded():
    assert describe_command("drop", {"drop": "cases"})["op"] == "ddl:drop"
    renamed = describe_command("renameCollection", {"renameCollection": "db.old", "to": "db.new"})
    assert renamed["collection"] == "old" and renamed["to"] == "db.new"
    out = describe_command("aggregate", {"aggregate": "demonstrations", "pipeline": [{"$match": {}}, {"$out": "copy"}]})
    assert out == {"op": "aggregate_out", "collection": "copy", "source": "demonstrations"}
    assert describe_command("aggregate", {"aggregate": "demonstrations", "pipeline": [{"$match": {}}]}) is None


def test_find_and_modify_remove_is_a_delete():
    body = describe_command("findAndModify", {"findAndModify": "cases", "query": {"_id": 1}, "remove": True})
    assert body["op"] == "delete" and body["doc_ids"] == [1] and "change" not in body


# --------------------------------------------------------------------------- #
# Listener: who and through which path, outcome, no-op filtering
# --------------------------------------------------------------------------- #
class _Sink:
    def __init__(self):
        self.events = []

    def write(self, db_name, event):
        self.events.append(event)


def _run(listener, command_name, command, reply, request_id=1):
    started = SimpleNamespace(command_name=command_name, command=command, database_name="testdb",
                              request_id=request_id, connection_id=("h", 1))
    listener.started(started)
    listener.succeeded(SimpleNamespace(command_name=command_name, reply=reply, request_id=request_id,
                                       connection_id=("h", 1), duration_micros=1500))


def test_listener_records_job_scope_and_outcome():
    sink = _Sink()
    listener = AuditCommandListener(sink=sink)
    with audit_scope("job", "merge_duplicates", run_id="r1"):
        _run(listener, "update", {"update": "demonstrations", "updates": [{"q": {"_id": 5}, "u": {"$set": {"x": 1}}}]},
             {"ok": 1, "n": 1, "nModified": 1})
    (event,) = sink.events
    assert event["actor"] == {"kind": "job", "name": "merge_duplicates"}
    assert event["via"]["kind"] == "job" and event["via"]["name"] == "merge_duplicates"
    assert event["via"]["run_id"] == "r1"
    assert event["outcome"] == {"ok": True, "n": 1, "modified": 1}
    assert event["v"] == 1 and event["at"].tzinfo is not None


def test_listener_skips_writes_that_matched_nothing_but_keeps_failures():
    sink = _Sink()
    listener = AuditCommandListener(sink=sink)
    cmd = {"update": "email_queue", "updates": [{"q": {"status": "pending"}, "u": {"$set": {"status": "sending"}}}]}
    _run(listener, "update", cmd, {"ok": 1, "n": 0, "nModified": 0})
    assert sink.events == []
    _run(listener, "update", cmd, {"ok": 1, "n": 0, "writeErrors": [{"code": 11000}]}, request_id=2)
    assert sink.events[0]["outcome"]["errors"] == [11000]


def test_listener_skips_updates_that_changed_nothing():
    sink = _Sink()
    listener = AuditCommandListener(sink=sink)
    cmd = {"update": "demonstrations", "updates": [{"q": {"_id": 1}, "u": {"$set": {"in_past": True}}}]}
    _run(listener, "update", cmd, {"ok": 1, "n": 1, "nModified": 0})
    assert sink.events == []
    _run(listener, "update", cmd, {"ok": 1, "n": 1, "nModified": 0, "upserted": [{"index": 0, "_id": 2}]},
         request_id=2)
    assert len(sink.events) == 1


def test_listener_without_flask_names_the_script():
    sink = _Sink()
    listener = AuditCommandListener(sink=sink)
    _run(listener, "insert", {"insert": "cases", "documents": [{"_id": 1}]}, {"ok": 1, "n": 1})
    via = sink.events[0]["via"]
    assert via["kind"] == "process" and via["script"]
    assert sink.events[0]["actor"] == {"kind": "system"}


def test_listener_never_breaks_the_write_when_the_sink_fails(monkeypatch):
    logged = []
    monkeypatch.setattr(audit_trail.logger, "error", lambda *args, **kw: logged.append(args))
    listener = AuditCommandListener(uri="mongodb://127.0.0.1:1/", client_kwargs={"serverSelectionTimeoutMS": 50})
    _run(listener, "insert", {"insert": "cases", "documents": [{"_id": 1}]}, {"ok": 1, "n": 1})
    assert logged and logged[0][0] == "AUDIT_FALLBACK %s"
    assert '"collection": "cases"' in logged[0][1]


# --------------------------------------------------------------------------- #
# Integration: the application's own client is audited
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_request_write_is_attributed_to_the_signed_in_user(app, db):
    user_id = ObjectId()
    with app.test_request_context("/admin/demo/edit", method="POST"):
        from flask import g

        g._login_user = SimpleNamespace(is_authenticated=True, id=user_id)
        doc_id = db.audit_probe.insert_one({"title": "x"}).inserted_id
        db.audit_probe.update_one({"_id": doc_id}, {"$set": {"title": "y", "password": "hunter2"}})
        db.audit_probe.delete_one({"_id": doc_id})

    events = list(db[audit_trail.AUDIT_COLLECTION].find({"collection": "audit_probe"}).sort("at", 1))
    assert [e["op"] for e in events] == ["insert", "update", "delete"]
    for event in events:
        assert event["actor"] == {"kind": "user", "user_id": str(user_id)}
        assert event["via"]["kind"] == "http" and event["via"]["method"] == "POST"
        assert event["doc_ids"] == [doc_id]
    assert events[1]["change"][0]["$set"] == {"title": "y", "password": REDACTED}
    assert len({e["request_id"] for e in events}) == 1
    assert "hunter2" not in str(events)


@pytest.mark.integration
def test_audit_events_expire_after_the_retention_period(app, db):
    db.audit_probe.insert_one({"title": "x"})
    indexes = db[audit_trail.AUDIT_COLLECTION].index_information()
    assert indexes["audit_retention"]["expireAfterSeconds"] == audit_trail.DEFAULT_RETENTION_DAYS * 86400


def test_redaction_matches_the_go_api_vectors():
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "go-api" / "testdata" / "audit_redaction.json"
    for vector in json.loads(path.read_text(encoding="utf-8")):
        secret = vector["mode"] == "change" and vector["collection"] in audit_trail.SECRET_COLLECTIONS
        got = redact(vector["input"], secret_collection=secret, filter_mode=vector["mode"] == "filter")
        assert got == vector["expected"], vector["name"]
