"""Background job and command: retention for deleted accounts' kept records.

    python -m mielenosoitukset_fi.scripts.purge_erased_accounts --dry-run

Maintainer decision (20.11.2026): when an account is deleted, the records needed
to investigate abuse are kept pseudonymised, with the identity sealed under an
offline key. After `ERASURE_RETENTION_DAYS` (default 730, two years) the seal and
the kept non-audit records are deleted; audit records stay pseudonymised and can
no longer be linked to anyone. Only records marked `erased_subject` are touched.
See docs/account-erasure.md.
"""
import argparse
import json

from mielenosoitukset_fi.admin.user_erasure import purge_expired
from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.logger import logger


def main(db=None, dry_run=False):
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    result = purge_expired(db, dry_run=dry_run)
    if not dry_run and any(result["counts"].values()):
        logger.info("Retention removed deleted-account records: %s", result["counts"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    print(json.dumps(main(dry_run=parser.parse_args().dry_run), default=str))
