"""Background job: tell submitters once when OT review passes five working days."""
from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.logger import logger
from mielenosoitukset_fi.utils.moderation_reviews import notify_overdue


def main(db=None, email_sender=None):
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    if email_sender is None:
        from mielenosoitukset_fi.emailer.EmailSender import EmailSender

        email_sender = EmailSender()
    sent = notify_overdue(db, email_sender)
    if sent:
        logger.info("Sent %s moderation review delay notice(s)", sent)
    return {"sent": sent}
