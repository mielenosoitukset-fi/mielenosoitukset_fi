"""Every approve/reject path must run the one decision service.

Maintainer (20 Nov 2026): however many buttons exist, approving or rejecting a
demonstration must be technically the same everywhere and do the same things.
``demonstrations/decisions.py:apply_demo_decision`` owns that transition and its
side effects (submitter email, case closing, link revocation, audit).
"""

import re
from pathlib import Path

from bson import ObjectId

PACKAGE = Path(__file__).resolve().parents[1] / "mielenosoitukset_fi"
DECISION_SERVICE = PACKAGE / "demonstrations" / "decisions.py"

# A literal Mongo update that sets approved/rejected, e.g.
# {"$set": {"approved": False, "hide": True}}. Reads and filters don't match:
# they are not inside a "$set"/"$setOnInsert"/"$unset" document.
RAW_STATUS_WRITE = re.compile(
    r"""["']\$(?:set|setOnInsert|unset)["']\s*:\s*\{[^{}]*["'](?:approved|rejected)["']\s*:""",
    re.S,
)

# Writes that are not decisions and so stay outside the service. Keep this
# list empty unless the write creates a new record or is governance data.
ALLOWED = {
    # Board/system-team governance records, not demonstrations.
    "utils/team_governance.py",
    "utils/god_emergency.py",
    "utils/god_appointments.py",
    "utils/security_cases.py",  # revokes board clearances, not demonstrations
}


def test_no_code_outside_the_decision_service_writes_demo_status():
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        relative = path.relative_to(PACKAGE).as_posix()
        if path == DECISION_SERVICE or relative in ALLOWED or "/tests/" in f"/{relative}":
            continue
        for match in RAW_STATUS_WRITE.finditer(path.read_text(encoding="utf-8")):
            line = path.read_text(encoding="utf-8").count("\n", 0, match.start()) + 1
            offenders.append(f"{relative}:{line}")
    assert offenders == [], (
        "Approve/reject must go through demonstrations.decisions.apply_demo_decision; "
        f"raw status writes found at {offenders}"
    )


def test_escalation_does_not_change_a_decided_demo(admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    db.cases.update_one({"_id": seeded_data["case_id"]}, {"$set": {"demo_id": demo_id, "type": "new_demo"}})
    db.demonstrations.update_one({"_id": demo_id}, {"$set": {"approved": True, "rejected": False}})

    response = admin_client.post(f"/admin/case/{seeded_data['case_id']}/demo_escalate")

    assert response.status_code == 409
    assert db.demonstrations.find_one({"_id": demo_id})["approved"] is True
    case = db.cases.find_one({"_id": seeded_data["case_id"]})
    assert not (case.get("meta") or {}).get("superior_needed")


def test_escalation_keeps_a_pending_demo_pending(admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    db.cases.update_one({"_id": seeded_data["case_id"]}, {"$set": {"demo_id": demo_id, "type": "new_demo"}})

    response = admin_client.post(f"/admin/case/{seeded_data['case_id']}/demo_escalate")

    assert response.status_code == 200
    demo = db.demonstrations.find_one({"_id": demo_id})
    assert not demo.get("approved") and not demo.get("rejected")
    assert db.cases.find_one({"_id": seeded_data["case_id"]})["meta"]["superior_needed"] is True


def _series_children(db, parent_id, count=2):
    ids = [ObjectId() for _ in range(count)]
    db.demonstrations.insert_many(
        [
            {"_id": child_id, "parent": parent_id, "title": "Lapsi", "date": f"2099-07-0{i + 1}",
             "city": "Helsinki", "approved": False, "rejected": False}
            for i, child_id in enumerate(ids)
        ]
    )
    return ids


def test_bulk_child_approval_uses_the_decision_service(admin_client, db, seeded_data):
    parent_id = seeded_data["recu_demo_id"]
    db.recu_demos.update_one({"_id": parent_id}, {"$set": {"approved": True}})
    children = _series_children(db, parent_id)

    response = admin_client.post(
        f"/admin/recu_demo/{parent_id}/bulk-update-children",
        data={"child_ids": [str(value) for value in children], "fields": ["approved"]},
    )

    assert response.status_code == 302
    for child_id in children:
        child = db.demonstrations.find_one({"_id": child_id})
        assert child["approved"] is True
        assert child["moderation_decision"]["source"] == "admin_recurring_bulk"


def test_pending_series_never_unapproves_children(admin_client, db, seeded_data):
    parent_id = seeded_data["recu_demo_id"]
    db.recu_demos.update_one({"_id": parent_id}, {"$set": {"approved": False}})
    children = _series_children(db, parent_id, count=1)
    db.demonstrations.update_one({"_id": children[0]}, {"$set": {"approved": True}})

    admin_client.post(
        f"/admin/recu_demo/{parent_id}/bulk-update-children",
        data={"child_ids": [str(children[0])], "fields": ["approved"]},
    )

    assert db.demonstrations.find_one({"_id": children[0]})["approved"] is True
