"""Past demonstrations nobody decided are closed as expired (20 Nov 2026)."""

from datetime import date

from bson import ObjectId

from mielenosoitukset_fi.scripts.expire_undecided_demos import expire_once


class _Mail:
    def __init__(self):
        self.sent = []

    def queue_email(self, **kwargs):
        self.sent.append(kwargs)


def _demo(db, day, **fields):
    demo_id = ObjectId()
    db.demonstrations.insert_one({"_id": demo_id, "title": f"Demo {day}", "date": day, "city": "Helsinki",
                                  "approved": False, "rejected": False, **fields})
    db.submitters.insert_one({"demonstration_id": demo_id, "submitter_email": f"{demo_id}@example.test"})
    return demo_id


def test_recent_past_is_expired_with_apology_old_ones_quietly(db, seeded_data):
    today = date(2026, 11, 20)
    recent = _demo(db, "2026-11-15")
    old = _demo(db, "2026-09-01")
    upcoming = _demo(db, "2026-11-20")
    decided = _demo(db, "2026-11-10", approved=True)
    cancelled = _demo(db, "2026-11-10", cancelled=True)
    db.cases.insert_one({"_id": ObjectId(), "type": "new_demo", "case_type": "new_demo", "demo_id": recent, "meta": {}})
    mail = _Mail()

    counts = expire_once(db, mail, today=today)

    assert counts["expired"] >= 2 and counts["failed"] == 0
    for demo_id in (recent, old):
        demo = db.demonstrations.find_one({"_id": demo_id})
        assert demo["rejected"] is True and demo["approved"] is False
        assert demo["moderation_decision"]["status"] == "expired"
        assert demo["moderation_decision"]["source"] == "nightly_expiry"
    for demo_id in (upcoming, cancelled):
        assert db.demonstrations.find_one({"_id": demo_id})["rejected"] is False
    assert db.demonstrations.find_one({"_id": decided})["approved"] is True

    mailed = [m["recipients"][0] for m in mail.sent]
    assert f"{recent}@example.test" in mailed
    assert f"{old}@example.test" not in mailed
    apology = next(m for m in mail.sent if m["recipients"][0] == f"{recent}@example.test")
    assert apology["template_name"] == "demo_submitter_expired.html"

    case = db.cases.find_one({"demo_id": recent})
    assert case["meta"]["closed"] is True and case["meta"]["closed_reason"] == "demo_expired"
    assert case["status"] == "Vanhentunut"


def test_second_run_changes_nothing(db, seeded_data):
    _demo(db, "2026-11-15")
    mail = _Mail()
    expire_once(db, mail, today=date(2026, 11, 20))
    sent = len(mail.sent)
    again = expire_once(db, mail, today=date(2026, 11, 20))
    assert again["expired"] == 0 and len(mail.sent) == sent


def test_apology_template_renders():
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader("mielenosoitukset_fi/templates/emails"))
    html = env.get_template("demo_submitter_expired.html").render(title="Ilmastomarssi", date="2026-11-15", city="Helsinki")
    assert "Pahoittelemme" in html and "Ilmastomarssi" in html and "ei hylätty" in html
