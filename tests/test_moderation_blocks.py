"""Bans and publishing precedents that Hallinto sends and this site enforces."""
from datetime import datetime

import pytest
from bson import ObjectId

from mielenosoitukset_fi.utils import moderation_blocks as blocks


@pytest.fixture(autouse=True)
def clean(db):
    db[blocks.COLLECTION].delete_many({})
    yield
    db[blocks.COLLECTION].delete_many({})


def item(**changes):
    base = {"id": "b1", "kind": "organizer", "organizer": "Esimerkki ry", "reason": "R", "from": "2026-11-01", "until": ""}
    return {**base, **changes}


NOW = datetime(2026, 11, 21)


def test_sync_is_idempotent_and_lifting_ends_the_ban(db):
    assert blocks.sync(db, [item()]) == 1
    assert blocks.sync(db, [item()]) == 1
    assert db[blocks.COLLECTION].count_documents({}) == 1
    assert blocks.banned_organizer(db, ["  esimerkki   RY "], now=NOW)
    assert not blocks.banned_organizer(db, ["Toinen ry"], now=NOW)
    blocks.sync(db, [item(until="2026-11-21")])
    assert not blocks.banned_organizer(db, ["Esimerkki ry"], now=NOW)
    assert not blocks.banned_organizer(db, ["Esimerkki ry"], now=datetime(2026, 10, 1))  # Not yet in force.


def test_ban_matches_an_organization_id(db):
    org = ObjectId()
    blocks.sync(db, [item(organization_id=str(org))])
    assert blocks.banned_organizer(db, ["Eri nimi"], [str(org)], now=NOW)


def test_precedent_blocks_a_demo_or_an_organizations_demos(db):
    demo, org = ObjectId(), ObjectId()
    blocks.sync(db, [item(id="p1", kind="demo", organization_id=str(demo)),
                     item(id="p2", kind="organization", organization_id=str(org))])
    assert blocks.precedent_for(db, {"_id": demo}, now=NOW)["_id"] == "p1"
    assert blocks.precedent_for(db, {"_id": ObjectId(), "organizers": [{"organization_id": org}]}, now=NOW)["_id"] == "p2"
    assert blocks.precedent_for(db, {"_id": ObjectId(), "organizers": [{"name": "Muu"}]}, now=NOW) is None
    # An organizer ban is not a precedent.
    assert blocks.banned_organizer(db, ["Esimerkki ry"], now=NOW) is None


@pytest.mark.parametrize("bad", [
    item(kind="venue"), item(kind="demo"), item(organization_id="nope"), item(**{"from": ""}),
    item(until="2026-10-01"), item(organizer=""), item(id=""),
])
def test_sync_refuses_invalid_items(db, bad):
    with pytest.raises(ValueError):
        blocks.sync(db, [bad])
    assert db[blocks.COLLECTION].count_documents({}) == 0
