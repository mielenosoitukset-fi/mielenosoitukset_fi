"""Whether the public site is closed (hätäsulku).

One definition for every reader. The shared public gate already treats a lock
set by the security cases (name ``security``) and the older one (name
``global``) alike; the dashboard card and the outreach pause used to read only
``global`` and showed "off" while the site was closed.
"""

LOCK_NAMES = ["global", "security"]


def site_locked(db) -> bool:
    return bool(db.panic.find_one({"name": {"$in": LOCK_NAMES}, "panic": True}, {"_id": 1}))
