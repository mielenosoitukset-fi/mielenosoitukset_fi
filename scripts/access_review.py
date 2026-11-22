"""Ops tool for the admin account review (dry run unless --apply).

  python scripts/access_review.py stamp [--apply]     list/mark accounts with admin rights
  python scripts/access_review.py remind [--send]     queue reminder mails on reminder days
Prints ids, usernames and plain labels only: no emails or tokens.
"""
import argparse
import json

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils import access_review


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["stamp", "remind"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    db = DatabaseManager().get_instance().get_db()
    if args.command == "stamp":
        result = access_review.stamp(db, apply=args.apply)
    else:
        result = access_review.send_reminders(db, dry_run=not args.send)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
