"""Every change of an organization's ``verified`` flag leaves an admin-log entry."""




def _edit(client, org_id, verified):
    data = {"name": "Audit Org", "email": "audit-org@example.test", "description": "x", "website": ""}
    if verified:
        data["verified"] = "on"
    return client.post(f"/admin/organization/edit/{org_id}", data=data,
                       headers={"Referer": f"/admin/organization/edit/{org_id}"})



def test_verified_change_is_logged_with_old_and_new(admin_client, db, seeded_data):
    org_id = seeded_data["org_id"]
    db.organizations.update_one({"_id": org_id}, {"$set": {"verified": False}})
    db.admin_logs.delete_many({})
    _edit(admin_client, org_id, True)
    assert db.organizations.find_one({"_id": org_id})["verified"] is True
    logged = [e for e in db.admin_logs.find() if "organization_verified_changed" in str(e)]
    assert len(logged) == 1
    text = str(logged[0])
    assert str(org_id) in text and "'previous': False" in text and "'new': True" in text


def test_unchanged_verified_is_not_logged(admin_client, db, seeded_data):
    org_id = seeded_data["org_id"]
    db.organizations.update_one({"_id": org_id}, {"$set": {"verified": False}})
    db.admin_logs.delete_many({})
    _edit(admin_client, org_id, False)
    assert not [e for e in db.admin_logs.find() if "organization_verified_changed" in str(e)]
