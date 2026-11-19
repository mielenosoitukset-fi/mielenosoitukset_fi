"""Isolated Flask and Mongo proof of browser-specific Hallinto revocation."""

from datetime import timedelta

import pytest
from bson import ObjectId
from flask import Flask, session
from flask_login import LoginManager, UserMixin, current_user, login_user, logout_user

from mielenosoitukset_fi.users.hallinto_sessions import (
    AccountVersion,
    issue_marker,
    marker_is_live,
    revoke_marker,
)
from mielenosoitukset_fi.utils.time_utils import utcnow


def test_marker_is_bound_to_live_account_version_and_expiry(db):
    markers = db.hallinto_login_markers
    account = AccountVersion(ObjectId(), 17, "synthetic-version")
    marker = issue_marker(markers, account, utcnow() + timedelta(minutes=5))

    assert markers.find_one({"_id": marker}) is None
    assert marker_is_live(markers, marker, account)
    assert not marker_is_live(markers, marker, None)
    assert not marker_is_live(markers, marker, AccountVersion(account.user_id, 18, account.credential_version))
    assert not marker_is_live(markers, marker, AccountVersion(account.user_id, 17, "changed"))
    assert not marker_is_live(markers, "🙂" * 43, account)
    foreign_account = AccountVersion(ObjectId(), account.auth_epoch, account.credential_version)
    revoke_marker(markers, marker, foreign_account.user_id)
    assert not marker_is_live(markers, marker, foreign_account)
    assert marker_is_live(markers, marker, account)

    markers.update_one({"user_id": account.user_id}, {"$set": {"expires_at": utcnow() - timedelta(seconds=1)}})
    assert not marker_is_live(markers, marker, account)
    with pytest.raises(ValueError, match="expiry"):
        issue_marker(markers, account, utcnow() - timedelta(seconds=1))


def test_synthetic_flask_logout_revokes_only_its_linked_login(db):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="synthetic-fixture", TESTING=True)
    login = LoginManager(app)
    markers = db.hallinto_login_markers
    account = AccountVersion(ObjectId(), 1, "synthetic-version")

    class FixtureUser(UserMixin):
        id = str(account.user_id)

    @login.user_loader
    def load_user(user_id):
        if user_id == str(account.user_id):
            return FixtureUser()
        return None

    @app.post("/test-login")
    def sign_in():
        login_user(FixtureUser())
        marker = issue_marker(markers, account, utcnow() + timedelta(minutes=5))
        session["test_hallinto_marker"] = marker
        return {"marker": marker}

    @app.post("/test-logout")
    def sign_out():
        if current_user.is_authenticated:
            revoke_marker(markers, session.pop("test_hallinto_marker"), account.user_id)
            logout_user()
        return "", 204

    first, other = app.test_client(), app.test_client()
    first_marker = first.post("/test-login").json["marker"]
    other_marker = other.post("/test-login").json["marker"]

    assert marker_is_live(markers, first_marker, account)
    assert marker_is_live(markers, other_marker, account)
    assert first.post("/test-logout").status_code == 204
    assert first.post("/test-logout").status_code == 204
    assert not marker_is_live(markers, first_marker, account)
    assert marker_is_live(markers, other_marker, account)
    assert markers.count_documents({"user_id": account.user_id}) == 2

    first_again = first.post("/test-login").json["marker"]
    assert first_again != first_marker
    assert marker_is_live(markers, first_again, account)
    assert not marker_is_live(markers, first_marker, account)
