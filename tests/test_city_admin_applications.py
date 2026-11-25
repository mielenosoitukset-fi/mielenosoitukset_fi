"""City admin applications: local visibility and global decisions on cases made before Hallinto took over."""

from bson import ObjectId

from mielenosoitukset_fi.utils.classes import Case
from mielenosoitukset_fi.utils.time_utils import utcnow
from tests.conftest import _client_for_user
from tests.test_city_scoped_admin import _create_scoped_admin


def _application(db, applicant, cities):
    doc = {
        "_id": ObjectId(), "user_id": applicant, "city_keys": cities,
        "about": "Olen järjestänyt mielenosoituksia kymmenen vuotta ja haluan auttaa.",
        "activism": {"cities": cities, "organizations": "Paikallinen ryhmä", "experience": "some",
                     "experience_details": ""},
        "references": "", "status": "received", "case_pending": True,
        "consent_at": utcnow(), "created_at": utcnow(), "updated_at": utcnow(),
    }
    db.city_admin_applications.insert_one(doc)
    return doc["_id"]


def deliver_pending(db):
    """Make the Flask review case that the removed delivery job used to create."""
    count = 0
    for application in db.city_admin_applications.find({"case_pending": True}):
        case = Case(
            case_type="city_admin_application", submitter_id=application.get("user_id"), submitter={},
            meta={"application_id": application["_id"], "city_keys": application.get("city_keys") or [],
                  "urgency": "normal"}, _id=application["_id"],
        )
        db.cases.update_one({"_id": application["_id"]}, {"$setOnInsert": case.to_dict()}, upsert=True)
        db.city_admin_applications.update_one({"_id": application["_id"]}, {"$set": {"case_pending": False}})
        count += 1
    return count


def test_local_admins_see_only_their_cities_and_cannot_decide(app, db, seeded_data):
    applicant = seeded_data["user_id"]
    own = _application(db, applicant, ["tampere"])
    foreign = _application(db, applicant, ["turku"])
    deliver_pending(db)
    local = _create_scoped_admin(db, ["tampere"], ["LIST_CASES", "VIEW_CASE", "EDIT_CASE"])
    client = _client_for_user(app, local)

    page = client.get(f"/admin/case/{own}/")
    assert page.status_code == 200
    assert "Olen järjestänyt mielenosoituksia" in page.get_data(as_text=True)
    assert client.get(f"/admin/case/{foreign}/").status_code == 403

    response = client.post(f"/admin/case/{own}/application_status/", data={"status": "approved"})
    assert response.status_code == 403
    assert db.city_admin_applications.find_one({"_id": own})["status"] == "received"


def test_global_admin_decision_closes_the_case_without_granting_rights(admin_client, db, seeded_data):
    applicant = seeded_data["user_id"]
    before_role = db.users.find_one({"_id": applicant}).get("role")
    application = _application(db, applicant, ["tampere"])
    deliver_pending(db)

    response = admin_client.post(f"/admin/case/{application}/application_status/", data={"status": "approved"})
    assert response.status_code == 302
    assert db.city_admin_applications.find_one({"_id": application})["status"] == "approved"
    case = db.cases.find_one({"_id": application})
    assert case["meta"]["closed"] is True
    assert case["case_history"][-1]["mech_action"] == "application_status"
    assert db.users.find_one({"_id": applicant}).get("role") == before_role
    assert not db.admin_scope_grants.find_one({"user_id": applicant, "revoked_at": None, "scope_keys": "tampere"})

    bad = admin_client.post(f"/admin/case/{application}/application_status/", data={"status": "withdrawn"})
    assert bad.status_code == 302
    assert db.city_admin_applications.find_one({"_id": application})["status"] == "approved"


def test_retention_erases_only_expired_decided_text(db, seeded_data):
    from datetime import timedelta
    from mielenosoitukset_fi.utils.city_admin_applications import purge_expired, retention_enabled
    applicant = seeded_data["user_id"]
    old = utcnow() - timedelta(days=100)
    expired = _application(db, applicant, ["tampere"])
    recent = _application(db, applicant, ["turku"])
    pending = _application(db, applicant, ["oulu"])
    approved = _application(db, applicant, ["vaasa"])
    db.city_admin_applications.update_one({"_id": expired}, {"$set": {"status": "declined", "updated_at": old}})
    db.city_admin_applications.update_one({"_id": recent}, {"$set": {"status": "declined"}})
    db.city_admin_applications.update_one({"_id": pending}, {"$set": {"updated_at": old}})
    db.city_admin_applications.update_one({"_id": approved}, {"$set": {"status": "approved", "updated_at": old}})

    assert purge_expired(db, dry_run=True) == 1
    assert "about" in db.city_admin_applications.find_one({"_id": expired})
    assert purge_expired(db) == 1
    assert purge_expired(db) == 0
    gone = db.city_admin_applications.find_one({"_id": expired})
    assert "about" not in gone and "references" not in gone and gone["purged_at"]
    assert gone["status"] == "declined" and gone["city_keys"] == ["tampere"]
    for kept in (recent, pending, approved):
        assert "about" in db.city_admin_applications.find_one({"_id": kept})
    assert retention_enabled({}) is False
    assert retention_enabled({"CITY_ADMIN_APPLICATION_RETENTION_ENABLED": "true"}) is True


def test_retention_job_is_off_by_default(db, monkeypatch):
    from mielenosoitukset_fi.scripts.purge_city_admin_applications import main
    monkeypatch.delenv("CITY_ADMIN_APPLICATION_RETENTION_ENABLED", raising=False)
    assert main(db) == {"enabled": False, "purged": 0}


def test_duplicate_check_pipeline_is_read_only_and_groups_open_per_user():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "check_city_admin_application_duplicates.py"
    spec = importlib.util.spec_from_file_location("dup_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stages = [next(iter(stage)) for stage in module.PIPELINE]
    assert stages == ["$match", "$group", "$match", "$sort"]
    assert module.OPEN_STATUSES == ["received", "in_review"]


def test_hallinto_case_stops_flask_decisions(admin_client, db, seeded_data):
    from mielenosoitukset_fi.utils import city_admin_applications as module
    applicant = seeded_data["user_id"]
    application = _application(db, applicant, ["tampere"])

    # A case made before the switch: once Hallinto links its own case, only Hallinto decides.
    assert deliver_pending(db) == 1
    module.hallinto_update(db, application, "in_review", "hallinto-case", "actor")
    assert module.set_status(db, application, "approved", "admin") is False
    assert db.city_admin_applications.find_one({"_id": application})["status"] == "in_review"
    module.hallinto_update(db, application, "declined", "hallinto-case", "actor")
    case = db.cases.find_one({"_id": application})
    assert case["meta"]["closed"] is True and case["case_history"][-1]["metadata"]["hallinto_case"] == "hallinto-case"
