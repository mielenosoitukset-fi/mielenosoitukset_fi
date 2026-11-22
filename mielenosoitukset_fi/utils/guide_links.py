"""Personal links to the volunteer video guides (Olivia, 22 Nov 2026).

Guides for people who have not signed in yet are public on /ohjeet. The guides
for people with a role are on an unlisted page that a person reaches with a
personal link, ``/ohjeet/k/<token>``. The link works as a password that needs
no typing, so someone who cannot sign in yet can still watch the guides.

* The token is random and only its SHA-256 hash is stored in ``guide_links``.
* One live link per person; a new link revokes the old one.
* The link works only while the account is active and still has a role that
  has a guide. When the role ends the link is revoked the next time it is used.
* Video files are on cdn2; captions are in ``static/guides`` so they load from
  the same origin. To publish a guide add one line to ``GUIDES`` and copy its
  ``.fi.vtt`` file to ``static/guides/``.
"""

import hashlib
import secrets
from datetime import timedelta
from collections import OrderedDict

from mielenosoitukset_fi.utils.time_utils import utcnow

COLLECTION = "guide_links"
CDN_BASE = "https://cdn2.mielenosoitukset.fi/guides"
ALL_AUDIENCES = ("moderator", "city_admin", "organization", "translator")

GROUPS = OrderedDict([
    ("moderator", "Moderaattoreille ja ylläpitäjille"),
    ("city_admin", "Kaupunkiadmineille"),
    ("organization", "Organisaation hallinnoijille"),
    ("translator", "Kääntäjille"),
])

# (id, group, audiences, title, minutes, summary)
GUIDES = [
    ("b1-moderator-first-day", "moderator", ("moderator",), "Moderaattorin ensimmäinen päivä", 5,
     "Uuden ilmoituksen tarkistus sähköpostilinkistä ja Tapaukset-listasta."),
    ("b2-reject", "moderator", ("moderator", "city_admin"), "Hylkää ilmoitus", 3,
     "Hylkäyksen syy ja viesti ilmoittajalle."),
    ("b3-toinen-tarkistaja", "moderator", ("moderator", "city_admin"), "Siirrä toiselle tarkistajalle", 5,
     "Eskalointi viiteen tasoon Johtoryhmään asti."),
    ("b4-muutosehdotukset-ja-peruutuspyynnot", "moderator", ("moderator",),
     "Muutosehdotukset ja peruutuspyynnöt", 5,
     "Ehdotusten ja peruutuspyyntöjen käsittely Tapaukset-listassa."),
    ("b5-paivystysvuoro", "moderator", ("moderator",), "Päivystysvuoron varaaminen", 4,
     "Vuoron varaus viikkokalenterista, hyväksyntä ja peruminen."),
    ("b7-poisto-ja-palautus", "moderator", ("moderator",), "Poisto julkaisusta ja uudelleen julkaisu", 6,
     "Poistopyyntö, toisen ylläpitäjän vahvistus ja palautus."),
    ("d2-kutsu-ihmisia-organisaatioon", "organization", ("organization",), "Kutsu ihmisiä organisaatioon", 3,
     "Kutsulinkin lähettäminen organisaation jäsenille."),
    ("d4-organisaation-tiedot", "organization", ("organization",), "Päivitä organisaation tiedot", 3,
     "Kuvaus, verkkosivu, logo ja some-linkit."),
    ("e1-aloita-kaantajana", "translator", ("translator",), "Aloita kääntäjänä", 3,
     "Kääntäjän työkalut ja ensimmäinen käännös."),
]


def _has(user, name, *args):
    method = getattr(user, name, None)
    try:
        return bool(method(*args)) if callable(method) else False
    except Exception:  # a broken lookup must never widen access
        return False


def audiences_for(user):
    """Which guide audiences this account belongs to (empty: no role)."""
    if not user or getattr(user, "banned", False) or getattr(user, "is_active", True) is False:
        return set()
    if getattr(user, "security_suspended", False):
        return set()
    if _has(user, "has_full_permissions"):
        return set(ALL_AUDIENCES)
    keys = set()
    if (_has(user, "has_permission", "LIST_CASES") or getattr(user, "is_national_moderator", False)
            or getattr(user, "active_duty", None)):
        keys.add("moderator")
    if _has(user, "has_city_admin_scope_grants"):
        keys.add("city_admin")
    if _has(user, "manages_an_organization"):
        keys.add("organization")
    if (getattr(user, "role", None) == "translator"
            or any(_has(user, "has_permission", p) for p in ("TRANSLATE_DEMO", "TRANSLATE_UI"))):
        keys.add("translator")
    return keys


def guides_for(user):
    """[(heading, [guide dict])] for the guides this account may watch."""
    keys = audiences_for(user)
    groups = OrderedDict((key, []) for key in GROUPS)
    for guide_id, group, audience, title, minutes, summary in GUIDES:
        if keys & set(audience):
            groups[group].append({
                "id": guide_id, "title": title, "minutes": minutes, "summary": summary,
                "video": f"{CDN_BASE}/{guide_id}.mp4",
                "captions": f"/static/guides/{guide_id}.fi.vtt",
            })
    return [(GROUPS[key], items) for key, items in groups.items() if items]


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #

def _hash(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def issue_link(db, user_id, actor="system"):
    """Create the person's link and revoke the previous one. Returns the token.

    The token is shown only once (in the email); only its hash is stored.
    """
    now = utcnow()
    db[COLLECTION].update_many({"user_id": user_id, "revoked_at": None},
                               {"$set": {"revoked_at": now, "revoked_reason": "replaced"}})
    token = secrets.token_urlsafe(32)
    db[COLLECTION].insert_one({"user_id": user_id, "token_hash": _hash(token), "created_at": now,
                               "created_by": str(actor), "revoked_at": None, "last_used_at": None})
    return token


def sent_recently(db, user_id, minutes=10):
    """Whether a link was made lately, so nobody can flood a person's mailbox."""
    since = utcnow() - timedelta(minutes=minutes)
    return db[COLLECTION].count_documents({"user_id": user_id, "created_at": {"$gt": since}}) > 0


def revoke_links(db, user_id, reason="revoked"):
    return db[COLLECTION].update_many({"user_id": user_id, "revoked_at": None},
                                      {"$set": {"revoked_at": utcnow(), "revoked_reason": reason}}).modified_count


def resolve_link(db, token, load_user):
    """The account a live link belongs to, or None.

    ``load_user(user_id)`` returns the loaded account. A link whose person no
    longer has a role is revoked here, so it stays dead.
    """
    token = (token or "").strip()
    if not token or len(token) > 200:
        return None
    link = db[COLLECTION].find_one({"token_hash": _hash(token), "revoked_at": None})
    if not link:
        return None
    user = load_user(link["user_id"])
    if not user or not audiences_for(user):
        revoke_links(db, link["user_id"], "role_ended")
        return None
    db[COLLECTION].update_one({"_id": link["_id"]}, {"$set": {"last_used_at": utcnow()}})
    return user
