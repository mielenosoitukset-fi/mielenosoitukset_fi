#!/usr/bin/env python3
"""Mail each current role holder their personal guide link (Olivia, 22 Nov 2026).

Dry run by default: prints counts only, never names or addresses. ``--send``
issues one new link per person (replacing any older one) and queues one mail
each; people who got a link in the last 10 minutes are skipped, so a rerun
after a crash does not mail anyone twice. Run from the deployed checkout.
"""
import argparse
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def holders(db, load_user):
    """[(account doc, audiences)] for active accounts with a role that has a guide."""
    from mielenosoitukset_fi.utils import guide_links

    found = []
    for doc in db.users.find({"active": {"$ne": False}, "banned": {"$ne": True}}):
        if not (doc.get("email") or "").strip():
            continue
        user = load_user(doc["_id"])
        audiences = guide_links.audiences_for(user) if user else set()
        if audiences:
            found.append((doc, audiences))
    return found


def run(db, load_user, send_link, apply=False):
    from mielenosoitukset_fi.utils import guide_links

    people = holders(db, load_user)
    result = Counter(holders=len(people))
    for _, audiences in people:
        for audience in audiences:
            result[f"audience:{audience}"] += 1
    if apply:
        for doc, _ in people:
            if guide_links.sent_recently(db, doc["_id"]):
                result["skipped_recent"] += 1
            elif send_link(db, doc, actor="send_guide_links"):
                result["sent"] += 1
            else:
                result["no_email"] += 1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="queue the mails (default: dry run)")
    args = parser.parse_args()

    from mielenosoitukset_fi.app import create_app
    from mielenosoitukset_fi.admin import guide_links_bp as bp
    from mielenosoitukset_fi.admin.utils import mongo

    app = create_app()
    with app.test_request_context(base_url="https://mielenosoitukset.fi"):
        result = run(mongo, bp._load_user, bp.send_link, apply=args.send)
    print("SEND" if args.send else "DRY RUN", dict(sorted(result.items())))


if __name__ == "__main__":
    main()
