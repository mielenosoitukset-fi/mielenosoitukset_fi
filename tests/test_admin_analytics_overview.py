from bson import ObjectId


def _seed_views(db, count):
    db.prepped_analytics.delete_many({})
    demos = []
    for index in range(count):
        demo_id = ObjectId()
        demos.append({"_id": demo_id, "title": f"Katsotuin <{index:02d}>", "date": "2026-12-01"})
        db.prepped_analytics.insert_one({"_id": demo_id, "demo_id": demo_id, "views": 1000 - index})
    db.demonstrations.insert_many(demos)
    return demos


def test_analytics_overview_lists_most_viewed_page_by_page(admin_client, db):
    demos = _seed_views(db, 60)

    first = admin_client.get("/admin/demo_analytics").get_data(as_text=True)
    assert first.count('href="/admin/per_demo_analytics/') == 50
    assert first.index("Katsotuin &lt;00&gt;") < first.index("Katsotuin &lt;01&gt;")
    assert "Katsotuin &lt;50&gt;" not in first
    assert "Sivu 1 / 2" in first
    assert "page=2" in first
    assert "<00>" not in first

    second = admin_client.get("/admin/demo_analytics?page=2").get_data(as_text=True)
    assert second.count('href="/admin/per_demo_analytics/') == 10
    assert "Katsotuin &lt;59&gt;" in second
    assert f"<td>51</td>" in second

    clamped = admin_client.get("/admin/demo_analytics?page=99").get_data(as_text=True)
    assert "Sivu 2 / 2" in clamped
    assert str(demos[59]["_id"]) in clamped

    assert admin_client.get("/admin/demo_analytics?page=x").status_code == 200


def test_analytics_overview_names_deleted_demonstrations(admin_client, db):
    db.prepped_analytics.delete_many({})
    gone = ObjectId()
    db.prepped_analytics.insert_one({"_id": gone, "demo_id": gone, "views": 5})

    page = admin_client.get("/admin/demo_analytics").get_data(as_text=True)
    assert "Poistettu mielenosoitus" in page
    assert str(gone) in page
    assert "Sivu 1 / 1" not in page


def test_analytics_overview_empty_state(admin_client, db):
    db.prepped_analytics.delete_many({})
    page = admin_client.get("/admin/demo_analytics").get_data(as_text=True)
    assert "Ei tietoja saatavilla." in page
