"""Preview/import Apache request counts; replacement is atomic and backed up."""
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from bson import json_util

from mielenosoitukset_fi.utils.analytics_log_recovery import (
    COLLECTION, SNAPSHOT_ID, recover_logs,
)
from mielenosoitukset_fi.utils.site_analytics import HELSINKI_TZ


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    today = datetime.now(HELSINKI_TZ).date()
    if not today - timedelta(days=90) <= args.start <= args.end < today:
        parser.error("Use completed days within the last 90 days")
    if args.apply and not args.backup:
        parser.error("--apply requires a new --backup file")
    snapshot = recover_logs(args.logs, args.start, args.end)
    args.output.write_text(json_util.dumps(snapshot, indent=2), encoding="utf-8")
    if args.apply:
        from mielenosoitukset_fi.database_manager import DatabaseManager

        collection = DatabaseManager.get_instance().get_db()[COLLECTION]
        previous = collection.find_one({"_id": SNAPSHOT_ID})
        with args.backup.open("x", encoding="utf-8") as backup:
            backup.write(json_util.dumps(previous, indent=2))
        # Match the entire prior snapshot so concurrent imports cannot overwrite it.
        if previous is None:
            # The _id index rejects a simultaneous first import.
            collection.insert_one(snapshot)
        else:
            result = collection.replace_one(previous, snapshot)
            if result.matched_count != 1:
                raise RuntimeError("Snapshot changed during import; no replacement was made")
    print(f"{'Imported' if args.apply else 'Previewed'} {len(snapshot['rows'])} daily page counts; "
          f"{sum(row['count'] for row in snapshot['rows'])} requests")


if __name__ == "__main__":
    main()
