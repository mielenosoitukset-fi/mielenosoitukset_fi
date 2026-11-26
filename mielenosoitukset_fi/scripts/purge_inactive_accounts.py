"""Background job and command: warn and delete inactive accounts (#1174).

    python -m mielenosoitukset_fi.scripts.purge_inactive_accounts --dry-run

Rules and timeline: ``mielenosoitukset_fi/utils/inactive_accounts.py``. Does
nothing unless ``INACTIVE_ACCOUNT_DELETION_ENABLED`` is set; ``--dry-run`` only
counts and sends no mail.
"""
import argparse
import json

from config import Config
from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils import inactive_accounts
from mielenosoitukset_fi.utils.logger import logger


def main(db=None, dry_run=False):
    if not dry_run and not inactive_accounts.is_enabled():
        return {"enabled": False, "changed": 0}
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    result = inactive_accounts.run(
        db, dry_run=dry_run, secret=Config.SECRET_KEY,
        seal_key=getattr(Config, "ERASURE_SEAL_PUBLIC_KEY", "") or None,
    )
    if not dry_run and any(result["counts"].values()):
        logger.info("Inactive accounts: %s", result["counts"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="count only, change and send nothing")
    print(json.dumps(main(dry_run=parser.parse_args().dry_run), default=str))
