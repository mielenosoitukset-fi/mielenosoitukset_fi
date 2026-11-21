"""Every approve/reject path must run the one decision service.

Maintainer (20 Nov 2026): however many buttons exist, approving or rejecting a
demonstration must be technically the same everywhere and do the same things.
``demonstrations/decisions.py:apply_demo_decision`` owns that transition and its
side effects (submitter email, case closing, link revocation, audit).
"""

import ast
from pathlib import Path

from bson import ObjectId

PACKAGE = Path(__file__).resolve().parents[1] / "mielenosoitukset_fi"
DECISION_SERVICE = "demonstrations/decisions.py"
STATUS_FIELDS = {"approved", "rejected"}
UPDATE_OPERATORS = {"$set", "$setOnInsert", "$unset"}

# (file, function) pairs that may still assign the status, each with its reason.
# Keep this list shrinking; a new entry needs a maintainer-visible reason.
ALLOWED = {
    ("admin/admin_demo_bp.py", "duplicate_demo"): "a new copy always starts pending",
    ("admin/admin_case_bp.py", "_stringify_demo"): "read-only view model for the case page",
    ("admin/admin_recu_demo_bp.py", "handle_recu_demo_form"): "series record, not a demonstration",
    # Governance records (board clearances, appointments), not demonstrations.
    ("utils/team_governance.py", "cast_joint_override"): "governance",
    ("utils/team_governance.py", "cast_vote"): "governance",
    ("utils/god_emergency.py", "open_request"): "governance",
    ("utils/god_appointments.py", "cast_appointment_vote"): "governance",
    ("admin/elevated_grants.py", "_write_evidence"): "governance: Hallinto superadmin/god decision",
    ("utils/security_cases.py", "reconcile_case"): "revokes board clearances",
}


def _status_writes(tree):
    """Yield (line, function) for literal status writes.

    Catches ``{"$set": {"approved": ...}}`` style update documents and
    ``doc["approved"] = ...`` assignments. It cannot see updates assembled
    in variables or whole-document saves; those paths have behavioural tests
    (bulk children, merge, escalation) below and in test_demo_decision_service.
    """
    owners = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                owners.setdefault(id(child), node.name)
    for node in ast.walk(tree):
        hit = False
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value in UPDATE_OPERATORS
                        and isinstance(value, ast.Dict)):
                    hit |= any(isinstance(inner, ast.Constant) and inner.value in STATUS_FIELDS
                               for inner in value.keys)
        elif isinstance(node, ast.Assign):
            hit = any(isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                      and target.slice.value in STATUS_FIELDS for target in node.targets)
        if hit:
            yield node.lineno, owners.get(id(node))


def test_no_code_outside_the_decision_service_writes_demo_status():
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        relative = path.relative_to(PACKAGE).as_posix()
        if relative == DECISION_SERVICE:
            continue
        for line, function in _status_writes(ast.parse(path.read_text(encoding="utf-8"))):
            if (relative, function) not in ALLOWED:
                offenders.append(f"{relative}:{line} ({function})")
    assert offenders == [], (
        "Approve/reject must go through demonstrations.decisions.apply_demo_decision; "
        f"literal status writes found at {offenders}"
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


def test_case_page_offers_escalation_only_for_pending_demos(admin_client, db, seeded_data):
    demo_id = seeded_data["pending_demo_id"]
    db.cases.update_one({"_id": seeded_data["case_id"]}, {"$set": {"demo_id": demo_id, "type": "new_demo"}})
    url = f"/admin/case/{seeded_data['case_id']}/"

    assert "escalateDemo()" in admin_client.get(url).get_data(as_text=True).split("function escalateDemo")[0]
    db.demonstrations.update_one({"_id": demo_id}, {"$set": {"approved": True}})
    assert "escalateDemo()" not in admin_client.get(url).get_data(as_text=True).split("function escalateDemo")[0]


def test_bulk_child_approval_needs_approval_permission(app, db, seeded_data):
    from mielenosoitukset_fi.users.models import User
    from tests.conftest import _client_for_user

    editor_id = ObjectId()
    editor = User.create_user(
        username=f"series-editor-{str(editor_id)[-6:]}", password="EditorPass1!",
        email=f"series-editor-{str(editor_id)[-6:]}@example.test", displayname="Series Editor",
    )
    editor.update({
        "_id": editor_id, "confirmed": True, "active": True, "role": "admin", "global_admin": False,
        "global_permissions": ["LIST_DEMOS", "VIEW_DEMO", "EDIT_DEMO", "EDIT_RECURRING_DEMO"],
    })
    db.users.insert_one(editor)
    parent_id = seeded_data["recu_demo_id"]
    db.recu_demos.update_one({"_id": parent_id}, {"$set": {"approved": True}})
    children = _series_children(db, parent_id, count=1)

    response = _client_for_user(app, editor_id).post(
        f"/admin/recu_demo/{parent_id}/bulk-update-children",
        data={"child_ids": [str(children[0])], "fields": ["approved"]},
    )

    assert response.status_code == 302
    child = db.demonstrations.find_one({"_id": children[0]})
    assert child["approved"] is False
    assert "moderation_decision" not in child


def test_merge_approves_the_kept_demo_through_the_decision_service(admin_client, db, seeded_data):
    primary_id = seeded_data["pending_demo_id"]
    approved_copy = seeded_data["demo_id"]
    assert db.demonstrations.find_one({"_id": approved_copy})["approved"] is True

    response = admin_client.post("/admin/demo/merge", data={
        "demo_ids": [str(primary_id), str(approved_copy)], "primary_demo_id": str(primary_id),
        "field_source[approved]": str(primary_id),
    })

    assert response.status_code == 302
    primary = db.demonstrations.find_one({"_id": primary_id})
    assert primary["approved"] is True
    assert primary["moderation_decision"]["source"] == "admin_merge"


def test_bulk_child_approval_skips_rejected_children(admin_client, db, seeded_data):
    parent_id = seeded_data["recu_demo_id"]
    db.recu_demos.update_one({"_id": parent_id}, {"$set": {"approved": True}})
    children = _series_children(db, parent_id, count=1)
    db.demonstrations.update_one({"_id": children[0]}, {"$set": {"approved": False, "rejected": True}})

    admin_client.post(
        f"/admin/recu_demo/{parent_id}/bulk-update-children",
        data={"child_ids": [str(children[0])], "fields": ["approved"]},
    )

    child = db.demonstrations.find_one({"_id": children[0]})
    assert child["rejected"] is True
    assert child["approved"] is False


def test_model_merge_keeps_the_kept_demos_decision(app, db, seeded_data):
    from mielenosoitukset_fi.utils.classes.Demonstration import Demonstration

    with app.app_context():
        base = Demonstration.from_dict(db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]}))
        base.merge(seeded_data["demo_id"])

    kept = db.demonstrations.find_one({"_id": seeded_data["pending_demo_id"]})
    assert kept["approved"] is False


def test_scheduled_merges_skip_groups_with_different_decisions():
    from mielenosoitukset_fi.utils.classes.Demonstration import has_mixed_decisions

    assert has_mixed_decisions([{"approved": True}, {"approved": False}])
    assert has_mixed_decisions([{"approved": False, "rejected": True}, {"approved": False}])
    assert not has_mixed_decisions([{"approved": False}, {"approved": False, "rejected": False}])


def test_merge_into_rejected_demo_warns(admin_client, db, seeded_data):
    primary_id = seeded_data["pending_demo_id"]
    db.demonstrations.update_one({"_id": primary_id}, {"$set": {"rejected": True}})

    response = admin_client.post("/admin/demo/merge", data={
        "demo_ids": [str(primary_id), str(seeded_data["demo_id"])], "primary_demo_id": str(primary_id),
    }, follow_redirects=True)

    assert "Säilytetty mielenosoitus on hylätty" in response.get_data(as_text=True)
    assert db.demonstrations.find_one({"_id": primary_id})["approved"] is False
