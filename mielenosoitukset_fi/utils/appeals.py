"""Valitukset ja oikaisuvaatimukset (24 Nov 2026).

A complaint about a decision of the service is kept here, with the
complainant's name and address. Hallinto decides it and never gets the
address: it sends back only the päätös, a closed set of fields that names the
deciding body and never a person (docs/appeals.md in the Hallinto repository).
This module stores the appeal, validates what Hallinto sends and queues the
two mails: one confirmation and one päätös, each at most once.

Statuses: ``received`` (new), ``in_review`` (Hallinto has a case), ``decided``
(the päätös was sent). ``withdrawn`` is reserved for the complainant.
"""

from __future__ import annotations

import re
from datetime import timezone
from typing import Optional

from bson import ObjectId

from mielenosoitukset_fi.utils.time_utils import utcnow
from mielenosoitukset_fi.utils.validators import normalize_email, valid_email

COLLECTION = "appeals"
KINDS = ("valitus", "oikaisuvaatimus")
STATUSES = ("received", "in_review", "decided", "withdrawn")
# Hallinto may move an appeal only to these.
STEPS = ("in_review", "decided")
OUTCOMES = ("accepted", "partly_accepted", "rejected", "dismissed")
# The päätös is exactly these fields. Anything else Hallinto sends is dropped,
# so a name can never be stored next to it by accident.
LETTER_FIELDS = ("body", "decision_ref", "case_ref", "decided_on", "subject", "outcome", "reasoning", "final")
_REF = re.compile(r"MOSO/[0-9]{3}/[A-Z]{3}/[0-9]{4}/[0-9]{3,}\Z")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_ID = re.compile(r"[0-9a-f]{24}\Z")
MAX_TEXT = 10_000
MAX_LETTER_TEXT = 12_000
DEFAULT_REPLY_TO = "hallinto@mielenosoitukset.fi"


class AppealError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def _text(value, limit: int, minimum: int = 0, required: bool = True) -> str:
    if value is None:
        if required:
            raise AppealError("invalid_request")
        return ""
    if not isinstance(value, str):
        raise AppealError("invalid_request")
    value = value.replace("\r\n", "\n").strip()
    if not value:
        if required:
            raise AppealError("invalid_request")
        return ""
    if len(value) > limit or len(value) < minimum:
        raise AppealError("invalid_request")
    return value


def new_row(payload: dict, by: str, now=None) -> dict:
    """The stored appeal for a typed-in complaint. Raises AppealError."""
    kind = payload.get("kind")
    if kind not in KINDS:
        raise AppealError("invalid_request")
    email = normalize_email(_text(payload.get("email"), 254))
    if not valid_email(email):
        raise AppealError("invalid_email")
    now = now or utcnow()
    return {
        "kind": kind,
        "subject": {"title": _text(payload.get("title"), 300, required=False),
                    "reference": _text(payload.get("reference"), 200, required=False), "link": ""},
        "text": _text(payload.get("text"), MAX_TEXT, minimum=10),
        "complainant": {"name": _text(payload.get("name"), 120, required=False), "email": email},
        "status": "received", "source": "hallinto-manual", "entered_by": by,
        "created_at": now, "updated_at": now,
    }


def view(row: dict) -> dict:
    """What Hallinto may see. The complainant's name and address are never in it."""
    stamp = lambda value: value.replace(tzinfo=timezone.utc).isoformat() if value else None
    return {
        "id": str(row["_id"]), "status": row["status"], "kind": row["kind"],
        "subject": {"title": (row.get("subject") or {}).get("title", ""),
                    "reference": (row.get("subject") or {}).get("reference", ""),
                    "link": (row.get("subject") or {}).get("link", "")},
        "text": row.get("text", ""), "case": row.get("hallinto_case") or "",
        "created_at": stamp(row.get("created_at")),
        "decision_ref": ((row.get("letter") or {}).get("decision_ref")) or "",
    }


def clean_letter(letter, text) -> tuple[dict, str]:
    """Keep only the päätös fields, each checked. Raises AppealError."""
    if not isinstance(letter, dict) or not isinstance(text, str):
        raise AppealError("invalid_request")
    clean = {key: letter.get(key) for key in LETTER_FIELDS}
    ok = (
        all(isinstance(clean[k], str) and 0 < len(clean[k]) <= 5000 for k in ("body", "reasoning"))
        and isinstance(clean["subject"], str) and len(clean["subject"]) <= 300
        and clean["outcome"] in OUTCOMES
        and isinstance(clean["decision_ref"], str) and _REF.match(clean["decision_ref"])
        and isinstance(clean["case_ref"], str) and _REF.match(clean["case_ref"])
        and isinstance(clean["decided_on"], str) and _DAY.match(clean["decided_on"])
        and isinstance(clean["final"], bool)
        and 0 < len(text) <= MAX_LETTER_TEXT and clean["decision_ref"] in text
    )
    if not ok:
        raise AppealError("invalid_letter")
    return clean, text.strip()


def _reply_to() -> str:
    from mielenosoitukset_fi.utils.access_review import reply_to
    return reply_to() or DEFAULT_REPLY_TO


def _queue(db, queue_id: str, subject: str, email: str, body: str) -> None:
    """Queue one plain mail under a fixed id, so a repeat can never send it twice."""
    from mielenosoitukset_fi.emailer.EmailJob import EmailJob

    job = EmailJob(subject, [email], body=body, extra_headers={"Reply-To": _reply_to()}).to_dict()
    db.email_queue.update_one({"_id": queue_id}, {"$setOnInsert": job}, upsert=True)


def _greeting(row: dict) -> str:
    name = ((row.get("complainant") or {}).get("name") or "").strip()
    return f"Hei {name}," if name else "Hei,"


def queue_confirmation(db, row: dict) -> None:
    email = (row.get("complainant") or {}).get("email")
    if not email:
        return
    what = "valituksesi" if row.get("kind") == "valitus" else "oikaisuvaatimuksesi"
    body = (f"{_greeting(row)}\n\nOlemme ottaneet {what} käsittelyyn. Pyrimme antamaan kirjallisen päätöksen "
            "30 vuorokauden kuluessa. Päätös lähetetään tähän sähköpostiosoitteeseen.\n\n"
            "Jos haluat lisätä jotain, vastaa tähän viestiin.\n\nMielenosoitukset.fi\n")
    _queue(db, f"appeal-received:{row['_id']}", "Valituksesi on otettu käsittelyyn", email, body)


def queue_decision(db, row: dict, text: str) -> None:
    email = (row.get("complainant") or {}).get("email")
    if not email:
        return
    ref = (row.get("letter") or {}).get("decision_ref", "")
    body = (f"{_greeting(row)}\n\nValituksesi on käsitelty. Päätös on alla.\n\n{text}\n"
            "Voit vastata tähän viestiin. Mainitse viestissä päätöksen numero.\n\nMielenosoitukset.fi\n")
    _queue(db, f"appeal-decision:{row['_id']}", f"Päätös valitukseesi {ref}".strip(), email, body)


def create(db, payload: dict, by: str, now=None) -> dict:
    row = new_row(payload, by, now)
    row["_id"] = db[COLLECTION].insert_one(row).inserted_id
    queue_confirmation(db, row)
    return row


def list_rows(db, limit: int = 200) -> list[dict]:
    return list(db[COLLECTION].find({"status": {"$in": list(STATUSES)}}, {"complainant": 0})
                .sort("created_at", -1).limit(limit))


def record_step(db, payload: dict, by: str, now=None) -> tuple[dict, bool]:
    """Hallinto's step: link its case (in_review) or send the päätös (decided).

    Returns the appeal as Hallinto may see it and whether anything changed. A
    repeat of a step changes nothing and queues no second mail. A decided or
    withdrawn appeal is closed.
    """
    appeal_id, status, case = payload.get("id"), payload.get("status"), payload.get("case")
    if (not isinstance(appeal_id, str) or not _ID.match(appeal_id) or status not in STEPS
            or not isinstance(case or "", str) or len(case or "") > 128):
        raise AppealError("invalid_request")
    clean, text = (None, "")
    if status == "decided":
        clean, text = clean_letter(payload.get("letter"), payload.get("text"))
    elif "letter" in payload or "text" in payload:
        raise AppealError("invalid_request")
    now = now or utcnow()
    before = db[COLLECTION].find_one({"_id": ObjectId(appeal_id)})
    if not before:
        raise AppealError("not_found", 404)
    if before["status"] in ("decided", "withdrawn"):
        if before["status"] == status and (status != "decided" or (before.get("letter") or {}) == clean):
            return view(before), False
        raise AppealError("closed", 409)
    update = {"status": status, "updated_at": now}
    if case:
        update["hallinto_case"] = case
    if clean:
        update["letter"], update["letter_text"] = clean, text
        update["decided_at"] = now
    unchanged = before["status"] == status and (not case or before.get("hallinto_case") == case)
    if unchanged and not clean:
        return view(before), False
    row = db[COLLECTION].find_one_and_update(
        {"_id": before["_id"], "status": before["status"]}, {"$set": update,
         "$push": {"history": {"status": status, "at": now, "by": by}}}, return_document=True)
    if not row:
        raise AppealError("closed", 409)
    if clean:
        queue_decision(db, row, text)
    return view(row), True
