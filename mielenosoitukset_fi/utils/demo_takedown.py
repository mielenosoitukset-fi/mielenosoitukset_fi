"""Who may take down (or republish) an already approved demonstration.

Maintainer decision (20 Nov 2026): taking down a public demonstration is a
governance matter. Every rights check goes through ``can_take_down_demo`` so
the Hallinto platform can supply the rule later without touching callers.
Interim rule: global admins with approval rights.
"""


def can_take_down_demo(user, demo=None) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "global_admin", False):
        return False
    has_permission = getattr(user, "has_permission", None)
    return bool(has_permission and has_permission("ACCEPT_DEMO"))
