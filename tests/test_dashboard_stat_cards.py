"""Dashboard numbers each count one plainly named thing (maintainer, 20 Nov 2026).

The old "Tapahtumat 490 / Valmiina julkaisuun 6 / Näkyvissä sivustolla" card
counted every non-cancelled demo (past, pending and rejected included) and
called rejected demos "ready to publish".
"""

from bson import ObjectId


def _demo(db, **fields):
    doc = {"_id": ObjectId(), "title": "Laskettava", "date": "2099-01-01", "city": "Helsinki",
           "approved": False, "rejected": False, **fields}
    db.demonstrations.insert_one(doc)
    return doc["_id"]


def test_snapshot_counts_have_one_meaning(app, db, seeded_data):
    from mielenosoitukset_fi.admin.admin_bp import _calculate_dashboard_snapshot

    with app.test_request_context():
        before = _calculate_dashboard_snapshot()["demos"]
    _demo(db)  # waiting
    _demo(db, rejected=True)  # decided: not waiting, not published
    _demo(db, cancelled=True)  # cancelled: neither
    _demo(db, approved=True)  # published upcoming
    _demo(db, approved=True, in_past=True)  # published, past
    _demo(db, approved=True, hide=True)  # hidden: not published

    with app.test_request_context():
        after = _calculate_dashboard_snapshot()["demos"]

    assert after["waiting"] - before["waiting"] == 1
    assert after["published_upcoming"] - before["published_upcoming"] == 1
    assert after["published_total"] - before["published_total"] == 2


def test_dashboard_cards_name_what_they_count(admin_client):
    body = admin_client.get("/admin/dashboard").get_data(as_text=True)
    assert "Valmiina julkaisuun" not in body and "Näkyvissä sivustolla</span>" not in body
    for label in ("Odottaa käsittelyä", "Julkaistut tulevat", "Avoimet tapaukset", "Käsittele tapauksissa"):
        assert label in body
