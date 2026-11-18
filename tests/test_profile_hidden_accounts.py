"""Banned, deactivated and suspended accounts have no public profile page."""

from mielenosoitukset_fi.users.BPs.profile import _profile_hidden


def test_ordinary_accounts_stay_visible():
    assert _profile_hidden({"username": "maija"}) is False
    assert _profile_hidden({"active": True, "banned": False}) is False
    assert _profile_hidden({"security_suspension": {"active": False}}) is False


def test_hidden_accounts():
    assert _profile_hidden({"banned": True}) is True
    assert _profile_hidden({"active": False}) is True
    assert _profile_hidden({"security_suspension": {"active": True}}) is True


def _follow_each_other(db, seeded_data):
    db.users.update_one(
        {"_id": seeded_data["user_id"]},
        {"$set": {"followers": [seeded_data["friend_id"]], "following": [seeded_data["friend_id"]]}},
    )


def test_hidden_accounts_leave_the_follower_lists(user_client, db, seeded_data):
    _follow_each_other(db, seeded_data)
    assert "Bob Friend" in user_client.get("/users/profile/").get_data(as_text=True)

    db.users.update_one({"_id": seeded_data["friend_id"]}, {"$set": {"banned": True}})

    html = user_client.get("/users/profile/").get_data(as_text=True)
    assert "Bob Friend" not in html
    assert "/users/profile/bob" not in html


def test_follow_and_friend_endpoints_reject_hidden_accounts(user_client, db, seeded_data):
    db.users.update_one(
        {"_id": seeded_data["friend_id"]},
        {"$set": {"security_suspension": {"active": True}}},
    )

    for path in (
        "/users/profile/api/follow/",
        "/users/profile/api/send_friend_request/",
        "/users/profile/api/accept_friend_request/",
    ):
        response = user_client.post(path, json={"username": "bob"})
        assert response.status_code == 404, path
    for path in (
        "/users/profile/api/is_following/?username=bob",
        "/users/profile/api/is_friends/?username=bob",
        "/users/profile/api/friend_state/?username=bob",
    ):
        assert user_client.get(path).status_code == 404, path


def test_friend_list_and_messages_skip_hidden_accounts(user_client, db, seeded_data):
    from mielenosoitukset_fi.utils.time_utils import utcnow

    db.users.update_one(
        {"_id": seeded_data["user_id"]},
        {"$set": {"friends": [{"user_id": seeded_data["friend_id"]}]}},
    )
    db.messages.insert_one({
        "sender_id": seeded_data["friend_id"], "recipient_id": seeded_data["user_id"],
        "content": "hei", "created_at": utcnow(), "read": False,
    })
    assert len(user_client.get("/users/profile/api/friends_list/").get_json()["friends"]) == 1
    assert len(user_client.get("/users/profile/api/messages/").get_json()) >= 1  # the seed already has a message from Bob

    db.users.update_one({"_id": seeded_data["friend_id"]}, {"$set": {"banned": True}})

    assert user_client.get("/users/profile/api/friends_list/").get_json()["friends"] == []
    assert user_client.get("/users/profile/api/messages/").get_json() == []
    assert user_client.get("/users/profile/api/messages/unread_count/").get_json() == {}
    assert user_client.get("/users/profile/api/messages/bob/").status_code == 404
    response = user_client.post("/users/profile/api/messages/send/", json={"recipient": "bob", "content": "hei"})
    assert response.status_code == 404
