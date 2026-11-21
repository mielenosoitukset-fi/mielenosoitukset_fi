from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.case_resolution import close_resolved_cases
from mielenosoitukset_fi.utils.logger import logger


def _resolve(db):
    return close_resolved_cases(db)


def main():
    db = DatabaseManager().get_instance().get_db()
    checked, closed = _resolve(db)
    logger.info("Auto-close cases job finished", extra={"checked": checked, "closed": closed})


if __name__ == "__main__":
    main()
