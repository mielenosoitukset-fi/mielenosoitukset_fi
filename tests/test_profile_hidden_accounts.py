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
