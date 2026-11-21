#!/usr/bin/env python3
"""EMERGENCY ONLY: give an account admin rights straight in MongoDB.

This bypasses Hallinto decisions, the board clearance and every route check.
Normal rights go through the admin UI and, later, Hallinto (docs/system-teams.md).
Use it only to recover access when no other administrator can act.

    EMERGENCY_GRANT_ALLOWED=1 python scripts/emergency/grant_rights.py \\
        --email user@example.test --level admin --actor "Your Name" \\
        --reason "Why no other path works" --confirm-email user@example.test

``--level`` is ``admin`` (role admin, no global flag), ``global_admin`` (role and
global flag) or ``all`` (global_admin plus every permission). The write runs under
an audit scope that names the actor and the reason, and an admin-log entry is
added. The target must be an existing account.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LEVELS = ("admin", "global_admin", "all")
MIN_REASON = 15
WARNING = (
    "WARNING: emergency path. This skips Hallinto, board clearance and route checks.\n"
    "The change is recorded in audit_events and admin_logs with your name and reason."
)


class GuardError(ValueError):
    pass


def check_guard(args, environ):
    """Refuse unless every deliberate-use condition holds."""
    if environ.get("EMERGENCY_GRANT_ALLOWED") != "1":
        raise GuardError("Set EMERGENCY_GRANT_ALLOWED=1 to confirm this is an emergency.")
    if args.level not in LEVELS:
        raise GuardError(f"--level must be one of {', '.join(LEVELS)}.")
    if len((args.actor or "").strip()) < 2:
        raise GuardError("--actor (who is doing this) is required.")
    if len((args.reason or "").strip()) < MIN_REASON:
        raise GuardError(f"--reason must explain the emergency (at least {MIN_REASON} characters).")
    if (args.confirm_email or "").strip().casefold() != (args.email or "").strip().casefold():
        raise GuardError("--confirm-email must repeat --email exactly.")


def all_permissions():
    path = ROOT / "mielenosoitukset_fi" / "utils" / "data" / "permission_groups.json"
    groups = json.loads(path.read_text(encoding="utf-8"))
    return sorted(perm["name"] for perms in groups.values() for perm in perms)


def update_for(level):
    if level == "admin":
        return {"role": "admin"}
    update = {"role": "global_admin", "global_admin": True}
    if level == "all":
        update["global_permissions"] = all_permissions()
    return update


def grant(db, args):
    from mielenosoitukset_fi.utils.audit_trail import audit_scope
    from mielenosoitukset_fi.utils.time_utils import utcnow

    email = args.email.strip().lower()
    user = db.users.find_one({"$or": [{"email_canonical": email}, {"email": email}]})
    if not user:
        raise GuardError("No account with that email.")
    update = update_for(args.level)
    with audit_scope("script", "emergency_grant_rights", actor={"name": args.actor.strip()},
                     reason=args.reason.strip(), level=args.level):
        db.users.update_one({"_id": user["_id"]}, {"$set": update})
        db.admin_logs.insert_one({
            "event": "emergency_grant_rights",
            "module": "scripts.emergency.grant_rights",
            "timestamp": utcnow(),
            "actor": {"name": args.actor.strip(), "via": "emergency script"},
            "details": {"target_id": str(user["_id"]), "level": args.level,
                        "previous_role": user.get("role"), "reason": args.reason.strip()},
        })
    return user["_id"], update


def main(argv=None, environ=None):
    import os

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--level", required=True, choices=LEVELS)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--confirm-email", dest="confirm_email", default="")
    args = parser.parse_args(argv)
    print(WARNING, file=sys.stderr)
    try:
        check_guard(args, os.environ if environ is None else environ)
        from mielenosoitukset_fi.database_manager import DatabaseManager
        user_id, update = grant(DatabaseManager().get_instance().get_db(), args)
    except GuardError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1
    print(f"Granted {args.level} to account {user_id}: {sorted(update)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
