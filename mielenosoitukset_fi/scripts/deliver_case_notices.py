"""Background job: open demo suggestions as cases and tell admins about new cases.

    python -m mielenosoitukset_fi.scripts.deliver_case_notices

Two independent switches, both off by default:

* ``DEMO_SUGGESTION_CASES_ENABLED`` opens a ``demo_edit_suggestion`` case for each
  open demonstration suggestion (also older ones still waiting) and closes the
  case once the suggestion is applied, rejected or closed.
* ``CASE_NOTIFICATIONS_ENABLED`` sends a site inbox notice about each new case to
  the admins who may view it.
* ``CASE_NOTIFICATION_EMAILS_ENABLED`` (needs the previous switch) also queues one
  e-mail to each of those admins.
"""
import json

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils import case_notifications, demo_suggestion_cases
from mielenosoitukset_fi.utils.logger import logger


def main(db=None, environ=None, email_sender=None):
    suggestions_on = demo_suggestion_cases.is_enabled(environ)
    notices_on = case_notifications.is_enabled(environ)
    result = {"suggestion_cases": suggestions_on, "notifications": notices_on,
              "emails": notices_on and case_notifications.emails_enabled(environ),
              "opened": 0, "closed": 0, "notified": 0}
    if not (suggestions_on or notices_on):
        return result
    db = db if db is not None else DatabaseManager().get_instance().get_db()
    if suggestions_on:
        # Open cases first so the same run can notify about them.
        result["opened"] = demo_suggestion_cases.deliver_pending(db)
        result["closed"] = demo_suggestion_cases.close_decided(db)
    if notices_on:
        if case_notifications.emails_enabled(environ) and email_sender is None:
            email_sender = case_notifications._email_sender()
        elif not case_notifications.emails_enabled(environ):
            email_sender = None
        result["notified"] = case_notifications.deliver_pending(db, email_sender=email_sender)
    if result["opened"] or result["closed"] or result["notified"]:
        logger.info("Case notices: %s", result)
    return result


if __name__ == "__main__":
    print(json.dumps(main(), default=str))
