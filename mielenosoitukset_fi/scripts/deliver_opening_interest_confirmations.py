"""Background job: queue confirmation mails for campaign opening interest signups."""
from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.campaign_opening_interest import send_confirmations


def main(db=None):
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    return send_confirmations(db)
