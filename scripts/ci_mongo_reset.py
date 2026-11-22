"""Lease and reset the databases reserved for the current CI runner."""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient


def reset_databases(client, slot):
    for worker in ("controller", "gw0", "gw1"):
        name = f"mielenosoitukset_test_{slot}_{worker}"
        for suffix in ("", "_issuer", "_alternate", "_stale_sender"):
            client.drop_database(f"{name}{suffix}")
    for purpose in ("main", "limits"):
        client.drop_database(f"moso_go_{slot}_{purpose}")


def main(phase):
    slot = os.environ["MOSO_CI_MONGO_SLOT"]
    if slot not in {"02", "03", "04"}:
        raise ValueError("Invalid shared MongoDB slot")
    owner = ":".join(os.environ[key] for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "SHARD"))
    with MongoClient(os.environ["TEST_MONGO_URI"], serverSelectionTimeoutMS=10000) as client:
        leases = client.mielenosoitukset_test_leases.slots
        if phase == "start":
            leases.update_one({"_id": slot}, {"$setOnInsert": {"locked_until": datetime.now(timezone.utc)}}, upsert=True)
            for _ in range(65):
                now = datetime.now(timezone.utc)
                result = leases.update_one(
                    {"_id": slot, "$or": [{"locked_until": {"$lte": now}}, {"owner": owner}, {"owner": {"$ne": "janitor"}}]},
                    {"$set": {"owner": owner, "updated": now, "locked_until": now + timedelta(hours=1)}},
                )
                if result.matched_count:
                    reset_databases(client, slot)
                    return
                time.sleep(2)
            raise RuntimeError("Timed out waiting for the MongoDB janitor")
        if phase == "finish":
            if leases.find_one({"_id": slot, "owner": owner}):
                reset_databases(client, slot)
                now = datetime.now(timezone.utc)
                leases.update_one(
                    {"_id": slot, "owner": owner},
                    {"$set": {"updated": now, "locked_until": now}, "$unset": {"owner": ""}},
                )
            return
        raise ValueError("Expected start or finish")


if __name__ == "__main__":
    main(sys.argv[1])
