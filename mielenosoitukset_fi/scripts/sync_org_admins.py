"""Background job: keep org_admin grants equal to owner/admin memberships.

    python -m mielenosoitukset_fi.scripts.sync_org_admins

Flask membership writes sync at once; this pass also covers the Go API's invite
accept, recovery orders and writes made outside the app. Idempotent. Off with
``ORG_ADMIN_ROLE_ENABLED=0`` in the Flask environment.
"""
import os

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.logger import logger

OFF_VALUES = {"0", "false", "no", "off"}


def main(db=None, environ=None):
    environ = os.environ if environ is None else environ
    if str(environ.get("ORG_ADMIN_ROLE_ENABLED", "1")).strip().lower() in OFF_VALUES:
        return {"skipped": True}
    from mielenosoitukset_fi.admin import org_admin_grants

    db = db if db is not None else DatabaseManager.get_instance().get_db()
    totals = org_admin_grants.sync_all(db)
    logger.info("sync_org_admins %s", totals)
    return totals


if __name__ == "__main__":
    main()
