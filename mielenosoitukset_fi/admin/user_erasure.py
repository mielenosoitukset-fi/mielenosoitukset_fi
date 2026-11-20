"""What happens to an account's data when an admin deletes it (issue #596).

Maintainer decision, 20 November 2026: delete what only exists for the account
itself; keep what is needed to investigate abuse, and keep every record of an
account with extra permissions; make what is kept not directly linkable to the
person. See ``docs/account-erasure.md``.

Runs after the ``users`` document has been deleted:

1. ``CREDENTIAL_COLLECTIONS`` are always deleted: settings and ways to sign in or
   call the API.
2. ``PERSONAL_COLLECTIONS`` are deleted for an ordinary account. For a privileged
   account (``is_privileged``) they are kept and pseudonymised instead.
3. ``EVIDENCE_COLLECTIONS`` are always kept and pseudonymised.

Pseudonymising replaces the account id with a stable fake id of the same type
(``pseudonymous_id``), and the email, username and display name with the
``pseudonym`` text, wherever they appear in a record. IP addresses and user
agents stay, for abuse investigation. Each changed record gets ``erased_subject``
and ``erased_at`` so the retention job can find it. The true identity is stored
only sealed (``identity_seal``) with a public key; the private key is offline.
"""

import base64
import datetime
import hashlib
import hmac
import json
import os

from bson import ObjectId

from mielenosoitukset_fi.utils.time_utils import utcnow

CREDENTIAL_COLLECTIONS = (
    ("user_settings", ("user_id",)),
    ("mfas", ("user_id",)),
    ("mfa_login_challenges", ("user_id",)),
    ("magic_links", ("intended_recipient.user_id", "created_by_id")),
    ("api_tokens", ("user_id",)),
)
PERSONAL_COLLECTIONS = (
    ("password_changes", ("user_id",)),
    ("demo_reminders", ("user_id",)),
    ("demo_attending", ("user_id",)),
    ("notifications", ("user_id",)),
    ("memberships", ("user_id",)),
    ("api_token_requests", ("user_id",)),
    ("developer_apps", ("owner_id",)),
    ("developer_scope_requests", ("user_id",)),
    ("admin_scope_grants", ("user_id",)),
    ("board_clearances", ("user_id",)),
)
EVIDENCE_COLLECTIONS = (
    ("login_logs", ("user_id",)),
    ("messages", ("sender_id", "recipient_id")),
    ("reports", ("user",)),
    ("malicious_reports", ()),
    ("demo_submission_errors", ("user.id",)),
    ("submitters", ("user_id",)),
    ("demo_suggestions", ("user_id",)),
    ("cases", ("submitter_id",)),
    ("city_admin_applications", ("user_id",)),
    ("security_cases", ("user_id", "target_id")),
    ("admin_logs", ("user_id", "actor_id", "actor.id", "related_id")),
    ("board_audit_logs", ("user_id", "god_user_id")),
    ("super_audit_logs", ("actor_id", "actor.id", "entity_id", "related_id")),
    ("demo_audit_logs", ("user_id", "actor_id", "actor.id")),
)
# Old records sometimes carry only the email address.
EMAIL_FIELDS = (
    "email", "user_email", "reporter_email", "submitter_email", "submitter.email",
    "suggested_by.email", "form_snapshot.email", "form_snapshot.user_email",
    "form_snapshot.submitter_email", "actor.email", "user.email",
)
SEAL_COLLECTION = "erasure_identity_seals"
_PSEUDONYM_LABEL = b"moso-erasure-pseudonym-v1"


def is_privileged(user_doc):
    """An account with more than ordinary user rights keeps all its records."""
    return bool(
        user_doc.get("global_admin")
        or (user_doc.get("role") or "user") != "user"
        or user_doc.get("global_permissions")
    )


def _pseudonym_digest(secret, user_object_id):
    secret = secret.encode() if isinstance(secret, str) else secret
    return hmac.new(secret, _PSEUDONYM_LABEL + str(user_object_id).encode(), hashlib.sha256).digest()


def pseudonymous_id(secret, user_object_id):
    """Stable fake ObjectId for an erased account (does not reveal the real id)."""
    return ObjectId(_pseudonym_digest(secret, user_object_id)[:12])


def pseudonym(secret, user_object_id):
    return "poistettu-" + _pseudonym_digest(secret, user_object_id)[12:18].hex()


def identity_seal(public_key_pem, identity):
    """Encrypt ``identity`` so only the holder of the offline private key can read it.

    Hybrid: a fresh Fernet key encrypts the JSON, RSA-OAEP(SHA-256) wraps that key.
    """
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    public_key = serialization.load_pem_public_key(public_key_pem.encode())
    data_key = Fernet.generate_key()
    wrapped = public_key.encrypt(
        data_key,
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return {
        "alg": "RSA-OAEP-256+Fernet",
        "wrapped_key": base64.b64encode(wrapped).decode(),
        "ciphertext": Fernet(data_key).encrypt(json.dumps(identity, default=str).encode()).decode(),
    }


def open_identity_seal(private_key_pem, seal, password=None):
    """Inverse of ``identity_seal``. Not called by this app: unsealing happens only
    offline, with the private key, in the separate administration system (decision
    20 Nov 2026; see docs/account-erasure.md). Kept here as the reference format."""
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    private_key = serialization.load_pem_private_key(private_key_pem, password=password)
    data_key = private_key.decrypt(
        base64.b64decode(seal["wrapped_key"]),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return json.loads(Fernet(data_key).decrypt(seal["ciphertext"].encode()))


def _query(ids, id_fields, email):
    clauses = [{field: {"$in": ids}} for field in id_fields]
    if email:
        clauses += [{field: email} for field in EMAIL_FIELDS]
    return {"$or": clauses} if clauses else None


def _replacer(user_object_id, fake_id, alias, texts):
    real_str, fake_str = str(user_object_id), str(fake_id)
    lowered = {text.casefold() for text in texts}
    emails = [text for text in texts if "@" in text]

    def replace(value):
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, ObjectId):
            return fake_id if value == user_object_id else value
        if isinstance(value, str):
            if value == real_str:
                return fake_str
            if value.casefold() in lowered:
                return alias
            for email in emails:
                folded = value.casefold()
                while email.casefold() in folded:
                    start = folded.index(email.casefold())
                    value = value[:start] + alias + value[start + len(email):]
                    folded = value.casefold()
            return value
        return value

    return replace


AUDIT_TRAIL = "audit_events"
UPDATE_OPERATORS = ("$set", "$setOnInsert", "$push", "$addToSet", "$pull", "$pullAll")
# Fields of other records that hold a person's id: friend lists, demonstration editors.
LINK_FIELDS = ("editors", "followers", "following", "friends", "friend_requests")


def _operator_clause(ids, operator, field):
    """Match ``change.<operator>.<field>`` (a $-prefixed path, so dot notation can't)."""
    inner = {"$getField": {"field": {"$literal": operator}, "input": "$change"}}
    value = {"$cond": [
        {"$eq": [{"$type": "$change"}, "object"]},
        {"$cond": [{"$eq": [{"$type": inner}, "object"]}, {"$getField": {"field": field, "input": inner}}, None]},
        None,
    ]}
    # A pushed friend entry is an object that names the person in user_id or sent_by.
    nested = [
        {"$cond": [{"$eq": [{"$type": value}, "object"]}, {"$getField": {"field": key, "input": value}}, None]}
        for key in ("user_id", "sent_by")
    ]
    return {"$expr": {"$or": [
        {"$in": [value, ids]},
        {"$and": [{"$isArray": value}, {"$gt": [{"$size": {"$setIntersection": [value, ids]}}, 0]}]},
        *({"$in": [item, ids]} for item in nested),
    ]}}


def _pseudonymise_audit_trail(db, ids, since, replace, alias, now):
    """The write trail (docs/audit-trail.md) keeps ids in filters and inserted documents,
    and this erasure's own writes land there too. Match the account's rows by actor,
    document id, the id fields this module knows, and every row written since the
    erasure began; keep a row unchanged unless it really names the account."""
    fields = {"user_id"}
    for _, id_fields in CREDENTIAL_COLLECTIONS + PERSONAL_COLLECTIONS + EVIDENCE_COLLECTIONS:
        fields.update(id_fields)
    match = {"$in": ids}
    query = {"$or": [
        {"actor.user_id": match}, {"doc_ids": match}, {"outcome.upserted": match}, {"at": {"$gte": since}},
        *({f"change.{field}": match} for field in sorted(fields)),
        *({f"filter.{field}": match} for field in sorted(fields)),
        *(_operator_clause(ids, operator, field)
          for operator in UPDATE_OPERATORS
          for field in sorted({f for f in fields if "." not in f} | set(LINK_FIELDS))),
    ]}
    count = 0
    for record in db[AUDIT_TRAIL].find(query):
        cleaned = replace(record)
        if cleaned == record:
            continue
        cleaned["erased_subject"] = alias
        cleaned.setdefault("erased_at", now)
        db[AUDIT_TRAIL].replace_one({"_id": record["_id"]}, cleaned)
        count += 1
    return count


def erase_user(db, user_doc, secret, public_key_pem=None, now=None):
    """Apply the decision above to a deleted account. Returns counts; never raises on a
    missing collection. Safe to run again (already pseudonymised records no longer match).
    """
    started = utcnow() - datetime.timedelta(seconds=1)
    now = now or utcnow()
    user_object_id = user_doc["_id"]
    if not isinstance(user_object_id, ObjectId):
        user_object_id = ObjectId(str(user_object_id))
    ids = [user_object_id, str(user_object_id)]
    email = user_doc.get("email")
    privileged = is_privileged(user_doc)
    fake_id = pseudonymous_id(secret, user_object_id)
    alias = pseudonym(secret, user_object_id)
    texts = [
        text.strip() for text in (email, user_doc.get("username"), user_doc.get("displayname"))
        if isinstance(text, str) and len(text.strip()) >= 3
    ]
    replace = _replacer(user_object_id, fake_id, alias, texts)
    report = {"privileged": privileged, "deleted": {}, "pseudonymised": {}, "sealed": False}

    to_delete = list(CREDENTIAL_COLLECTIONS) + ([] if privileged else list(PERSONAL_COLLECTIONS))
    to_keep = list(EVIDENCE_COLLECTIONS) + (list(PERSONAL_COLLECTIONS) if privileged else [])

    for collection, id_fields in to_delete:
        query = _query(ids, id_fields, None)
        count = db[collection].delete_many(query).deleted_count
        if count:
            report["deleted"][collection] = count

    for collection, id_fields in to_keep:
        query = _query(ids, id_fields, email)
        if query is None:
            continue
        count = 0
        for record in db[collection].find(query):
            cleaned = replace(record)
            cleaned["erased_subject"] = alias
            cleaned.setdefault("erased_at", now)
            db[collection].replace_one({"_id": record["_id"]}, cleaned)
            count += 1
        if count:
            report["pseudonymised"][collection] = count

    # Links in other accounts and in demonstrations: the person is gone from them.
    db.users.update_many(
        {"$or": [
            {"friends.user_id": {"$in": ids}},
            {"friend_requests.user_id": {"$in": ids}},
            {"friend_requests.sent_by": {"$in": ids}},
            {"followers": {"$in": ids}},
            {"following": {"$in": ids}},
        ]},
        {"$pull": {
            "friends": {"user_id": {"$in": ids}},
            "friend_requests": {"$or": [{"user_id": {"$in": ids}}, {"sent_by": {"$in": ids}}]},
            "followers": {"$in": ids},
            "following": {"$in": ids},
        }},
    )
    db.demonstrations.update_many({"editors": {"$in": ids}}, {"$pull": {"editors": {"$in": ids}}})

    if public_key_pem:
        identity = {
            "user_id": str(user_object_id),
            "username": user_doc.get("username"),
            "email": email,
            "displayname": user_doc.get("displayname"),
            "role": user_doc.get("role"),
            "global_admin": bool(user_doc.get("global_admin")),
            "deleted_at": now,
        }
        db[SEAL_COLLECTION].update_one(
            {"_id": alias},
            {"$setOnInsert": {
                "_id": alias,
                "pseudonymous_id": fake_id,
                "privileged": privileged,
                "created_at": now,
                "seal": identity_seal(public_key_pem, identity),
            }},
            upsert=True,
        )
        report["sealed"] = True

    # Last, so the trail rows of the writes above are covered too.
    count = _pseudonymise_audit_trail(db, ids, started, replace, alias, now)
    if count:
        report["pseudonymised"][AUDIT_TRAIL] = count
    return report


def retention_days():
    """Days a deleted account's kept records and seal stay (default two years)."""
    try:
        days = int(os.environ.get("ERASURE_RETENTION_DAYS", "730"))
    except ValueError:
        days = 730
    return max(days, 30)


def purge_expired(db, now=None, days=None, dry_run=False):
    """Retention: after the period, drop the seal and the kept non-audit records.

    Audit records (``cases``, ``security_cases`` and the audit logs) stay,
    pseudonymised, as the site's own history; without the seal they can no longer
    be linked to anyone.
    """
    now = now or utcnow()
    cutoff = now - datetime.timedelta(days=days or retention_days())
    audit = {"cases", "city_admin_applications", "security_cases", "admin_logs",
             "board_audit_logs", "super_audit_logs", "demo_audit_logs", AUDIT_TRAIL}
    collections = [name for name, _ in EVIDENCE_COLLECTIONS + PERSONAL_COLLECTIONS if name not in audit]
    counts = {}
    for collection in collections + [SEAL_COLLECTION]:
        field = "created_at" if collection == SEAL_COLLECTION else "erased_at"
        query = {field: {"$lt": cutoff}}
        if collection != SEAL_COLLECTION:
            query["erased_subject"] = {"$exists": True}
        if dry_run:
            counts[collection] = db[collection].count_documents(query)
        else:
            counts[collection] = db[collection].delete_many(query).deleted_count
    return {"dry_run": dry_run, "cutoff": cutoff.isoformat(), "counts": counts}
