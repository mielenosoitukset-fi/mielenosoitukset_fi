"""Telling OT that an escalated case waits for it.

OT decides escalated demonstration cases in Hallinto, and Hallinto knows who
the OT members are (Flask does not). Hallinto therefore sends one signed order
naming the OT members' Flask accounts. Flask checks the signature (the caller
already passed the mTLS workload check) and that the order is fresh, then mails
each named account once for each open case that has reached OT. The queue id
is fixed per case and account, so repeating the order never mails twice.

Off unless ``ESCALATION_OT_NOTICES_ENABLED`` is true. Replies go to the
Hallinto mailbox, never to tuki@.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from bson import ObjectId

from mielenosoitukset_fi.utils import access_review
from mielenosoitukset_fi.utils.time_utils import utcnow

from . import city_grants as cg

FIELDS = {"recipients", "issued_at", "authority_attestation"}
MAX_AGE = timedelta(minutes=5)
MAX_RECIPIENTS = 50
INSTANT = "%Y-%m-%dT%H:%M:%SZ"
HALLINTO_URL = "https://hallinto.mielenosoitukset.fi/"


def enabled() -> bool:
    value = access_review._config("ESCALATION_OT_NOTICES_ENABLED", False)
    return value if isinstance(value, bool) else str(value).lower() in {"1", "true", "on", "yes"}


def validate(request: Any) -> Optional[dict]:
    """Canonical intent without the attestation, or None for an unsupported shape."""
    if not isinstance(request, dict) or set(request) != FIELDS:
        return None
    recipients = request["recipients"]
    if not (isinstance(recipients, list) and 1 <= len(recipients) <= MAX_RECIPIENTS
            and all(isinstance(r, str) and cg.ACCOUNT_ID.match(r) for r in recipients)
            and recipients == sorted(set(recipients))
            and isinstance(request["issued_at"], str)
            and isinstance(request["authority_attestation"], str) and 1 <= len(request["authority_attestation"]) <= 16384):
        return None
    try:
        datetime.strptime(request["issued_at"], INSTANT)
    except ValueError:
        return None
    return {k: request[k] for k in sorted(FIELDS - {"authority_attestation"})}


def intent_digest(request: Any) -> Optional[str]:
    intent = validate(request)
    return cg._digest(intent) if intent is not None else None


def _message(case_doc: dict, demo: dict) -> tuple[str, str]:
    number = case_doc.get("running_num") or ""
    title = (demo.get("title") or "").strip() or "nimetön ilmoitus"
    subject = f"Tapaus #{number} odottaa OT:n päätöstä"
    body = (
        f"Mielenosoitusilmoitus \"{title}\" on siirretty OT:n päätettäväksi, koska aiemmat tasot eivät voineet päättää sitä.\n\n"
        f"Päätös tehdään Hallinnossa: {HALLINTO_URL}\n"
        "Avaa Asiat. Tapaus on siellä omana asianaan. Ehdota ratkaisu, ja toinen tarkistaja vahvistaa sen.\n\n"
        "Tähän viestiin voi vastata, vastaus menee Hallinnon osoitteeseen.\n"
    )
    return subject, body


def apply(db, request: dict, now: Optional[datetime] = None) -> tuple[int, dict]:
    """Mail the named accounts about each pending case at OT. Returns (status, body)."""
    from mielenosoitukset_fi.emailer.EmailJob import EmailJob
    from mielenosoitukset_fi.utils.case_escalation import OT_LEVEL
    from mielenosoitukset_fi.utils.system_teams import active_account

    current = now or utcnow()
    issued = datetime.strptime(request["issued_at"], INSTANT)
    if abs(current.replace(tzinfo=None) - issued) > MAX_AGE:
        return 409, {"code": "stale"}
    if not enabled():
        return 200, {"status": "off", "queued": 0}
    users = [doc for doc in db.users.find({"_id": {"$in": [ObjectId(a) for a in request["recipients"]]}})
             if active_account(doc) and (doc.get("email") or "").strip()]
    queued = 0
    for case_doc in db.cases.find({"type": "new_demo", "meta.superior_needed": True, "meta.escalation_level": OT_LEVEL}):
        demo = db.demonstrations.find_one({"_id": ObjectId(str(case_doc.get("demo_id")))}) if ObjectId.is_valid(str(case_doc.get("demo_id"))) else None
        if not demo or demo.get("approved") or demo.get("rejected"):
            continue
        subject, body = _message(case_doc, demo)
        for user in users:
            job = EmailJob(subject, [user["email"].strip()], body=body,
                           extra_headers={"Reply-To": access_review.reply_to()}).to_dict()
            # Fixed queue id: the same case and account are never mailed twice.
            result = db.email_queue.update_one({"_id": f"escalation-ot-notice:{case_doc['_id']}:{user['_id']}"},
                                               {"$setOnInsert": job}, upsert=True)
            if result.upserted_id is not None:
                queued += 1
    return 200, {"status": "ok", "queued": queued}
