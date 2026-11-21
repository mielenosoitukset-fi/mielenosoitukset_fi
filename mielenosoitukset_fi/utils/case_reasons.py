"""Structured reasons for rejecting a demonstration and closing a case.

The case page offers a fixed list of reasons instead of a blank text box. A
duplicate points at the original: another case (by its number) when a case is
closed, or another demonstration when a new demonstration is rejected. "Other"
needs a written note; for the rest the note is optional.

``parse_reason`` checks a submitted reason and returns a dict that the routes
store in the case history (``metadata``). ``metadata.reason`` keeps a readable
one-line summary, so older history views keep working. Requests that send only
the old free-text ``reason`` are read as "other" with that text as the note.
"""
import re

from bson import ObjectId

NOTE_MAX = 1000

LABELS = {
    "handled": "Käsitelty",
    "duplicate": "Kaksoiskappale",
    "spam": "Roskaposti",
    "out_of_scope": "Ei kuulu palveluun",
    "not_enough_info": "Puutteelliset tiedot",
    "other": "Muu syy",
    # Not a fixed choice: offered first when Johtoryhmä has already decided
    # against this demonstration (see ``reject_choices``).
    "jory_decision": "Johtoryhmän päätös",
}

# Rejecting a demonstration and closing a case share most reasons; only a case
# can be closed as handled.
REJECT_KEYS = ("jory_decision", "duplicate", "spam", "out_of_scope", "not_enough_info", "other")
CLOSE_KEYS = ("handled", "duplicate", "spam", "out_of_scope", "not_enough_info", "other")


class ReasonError(ValueError):
    """A reason that cannot be accepted; the message is shown to the admin."""


def choices(keys):
    """The fixed choices. The Johtoryhmä decision is never fixed: see ``reject_choices``."""
    return [(key, LABELS[key]) for key in keys if key != "jory_decision"]


def reject_choices(db, demo):
    """Reject reasons for one demonstration, with a matching Johtoryhmä decision first.

    When Johtoryhmä has already decided against this demonstration (a publishing
    precedent or an organizer ban from Hallinto), that decision comes first, with
    its id, so the form can preselect it.
    """
    from mielenosoitukset_fi.utils import moderation_blocks

    result = choices(REJECT_KEYS)
    block = moderation_blocks.demo_block(db, demo) if demo else None
    if block:
        reason = (block.get("reason") or "").strip()
        label = f"{LABELS['jory_decision']} {moderation_blocks.decision_ref(block)}"
        if reason:
            label += f": {reason[:120]}"
        result.insert(0, ("jory_decision", label))
    return result


def _case_by_reference(db, reference):
    """Find a case by its number (``#100123`` or ``100123``) or its id."""
    reference = (reference or "").strip()
    number = reference.lstrip("#").strip()
    if re.fullmatch(r"\d{1,12}", number):
        return db.cases.find_one({"running_num": int(number)}, {"_id": 1, "running_num": 1})
    if ObjectId.is_valid(reference):
        return db.cases.find_one({"_id": ObjectId(reference)}, {"_id": 1, "running_num": 1})
    return None


def parse_reason(payload, keys, db, *, case_id=None, demo_id=None, can_view_case=None):
    """Return the reason metadata for a request body, or raise ReasonError.

    ``keys`` is ``REJECT_KEYS`` or ``CLOSE_KEYS``. A duplicate of a case needs
    ``can_view_case(case_id)``; a case the admin may not view is reported as
    not found, so the check reveals nothing about other cities' cases.
    """
    payload = payload if isinstance(payload, dict) else {}
    key = str(payload.get("reason_key") or "").strip()
    note = str(payload.get("note") or "").strip()
    if not key:
        # Older clients send a single free-text reason.
        key, note = "other", str(payload.get("reason") or "").strip()
    if key not in keys:
        raise ReasonError("Valitse syy.")
    if len(note) > NOTE_MAX:
        raise ReasonError(f"Lisätieto on liian pitkä (enintään {NOTE_MAX} merkkiä).")
    if key == "other" and not note:
        raise ReasonError("Kirjoita syy, kun valitset muun syyn.")

    metadata = {"reason_key": key, "reason_label": LABELS[key], "note": note}
    summary = LABELS[key]
    schema = {"reason": "string", "reason_key": "string", "reason_label": "string", "note": "string"}

    if key == "jory_decision":
        from mielenosoitukset_fi.utils import moderation_blocks

        demo = None
        if demo_id is not None and ObjectId.is_valid(str(demo_id)):
            demo = db.demonstrations.find_one({"_id": ObjectId(str(demo_id))})
        # The server finds the decision itself: the form cannot name one.
        block = moderation_blocks.demo_block(db, demo) if demo else None
        if not block:
            raise ReasonError("Johtoryhmän päätös ei koske tätä mielenosoitusta.")
        ref = moderation_blocks.decision_ref(block)
        metadata["decision_id"] = ref
        metadata["decision_block_id"] = str(block["_id"])
        schema.update(decision_id="string", decision_block_id="string")
        summary += f" {ref}"

    if key == "duplicate":
        reference = str(payload.get("duplicate_of") or "").strip()
        if demo_id is not None:
            if not ObjectId.is_valid(reference):
                raise ReasonError("Valitse alkuperäinen mielenosoitus.")
            original = db.demonstrations.find_one({"_id": ObjectId(reference)}, {"_id": 1, "title": 1})
            if not original:
                raise ReasonError("Alkuperäistä mielenosoitusta ei löytynyt.")
            if str(original["_id"]) == str(demo_id):
                raise ReasonError("Mielenosoitus ei voi olla itsensä kaksoiskappale.")
            metadata["duplicate_of_demo_id"] = str(original["_id"])
            metadata["duplicate_of_demo_title"] = original.get("title") or ""
            schema.update(duplicate_of_demo_id="string", duplicate_of_demo_title="string")
            summary += f": {original.get('title') or original['_id']}"
        else:
            if not reference:
                raise ReasonError("Anna alkuperäisen tapauksen numero.")
            original = _case_by_reference(db, reference)
            if not original or (can_view_case is not None and not can_view_case(original["_id"])):
                raise ReasonError("Alkuperäistä tapausta ei löytynyt.")
            if case_id is not None and str(original["_id"]) == str(case_id):
                raise ReasonError("Tapaus ei voi olla itsensä kaksoiskappale.")
            metadata["duplicate_of_case_id"] = str(original["_id"])
            metadata["duplicate_of_case_num"] = original.get("running_num")
            schema.update(duplicate_of_case_id="string", duplicate_of_case_num="number")
            if original.get("running_num"):
                summary += f": #{original['running_num']}"

    if note:
        summary += f" – {note}"
    metadata["reason"] = summary
    return metadata, schema
