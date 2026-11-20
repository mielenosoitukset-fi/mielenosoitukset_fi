"""Provision public query indexes explicitly, before application worker reload."""

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.public_pagination import ensure_public_demo_pagination_index
from mielenosoitukset_fi.utils.step_up import ensure_step_up_failure_indexes


def main():
    """Fail deployment on provisioning errors without interrupting old workers."""
    db = DatabaseManager.get_instance().get_db()
    ensure_public_demo_pagination_index(db)
    ensure_step_up_failure_indexes(db)


if __name__ == "__main__":
    main()
