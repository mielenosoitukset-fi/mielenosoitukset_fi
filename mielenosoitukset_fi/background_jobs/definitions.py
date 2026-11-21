"""
Job definitions used by the background job manager.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from mielenosoitukset_fi.scripts.repeat_v2 import main as repeat_main
from mielenosoitukset_fi.scripts.update_demo_organizers import main as update_main
from mielenosoitukset_fi.scripts.in_past import hide_past
from mielenosoitukset_fi.scripts.CL import main as cl_main
from mielenosoitukset_fi.scripts.preview_image_creator import run as run_preview
from mielenosoitukset_fi.scripts.send_demo_reminders import main as demo_sche
from mielenosoitukset_fi.scripts.process_submission_notifications import (
    run as process_submit_notifications,
)
from mielenosoitukset_fi.scripts.process_ui_translation_sync import (
    run as process_ui_translation_sync,
)
from mielenosoitukset_fi.utils.analytics import prep
from mielenosoitukset_fi.utils.aggregate_analytics import rebuild_demo_analytics
from mielenosoitukset_fi.scripts.auto_close_cases import main as auto_close_cases
from mielenosoitukset_fi.scripts.process_support_tickets import main as process_support_tickets
from mielenosoitukset_fi.scripts.process_email_queue import run as process_email_queue
from mielenosoitukset_fi.scripts.escalate_city_assignments import main as escalate_city_assignments
from mielenosoitukset_fi.scripts.expire_undecided_demos import main as expire_undecided_demos
from mielenosoitukset_fi.scripts.notify_overdue_moderation_reviews import main as notify_overdue_moderation_reviews
from mielenosoitukset_fi.scripts.deliver_city_admin_applications import main as deliver_city_admin_applications
from mielenosoitukset_fi.scripts.purge_city_admin_applications import main as purge_city_admin_applications
from mielenosoitukset_fi.scripts.deliver_case_notices import main as deliver_case_notices
from mielenosoitukset_fi.scripts.deliver_fundraising_reminder_confirmations import main as deliver_fundraising_reminder_confirmations
from mielenosoitukset_fi.scripts.send_fundraising_reminders import main as send_fundraising_reminders
from mielenosoitukset_fi.scripts.purge_fundraising_reminders import main as purge_fundraising_reminders
from mielenosoitukset_fi.scripts.purge_campaign_volunteers import main as purge_campaign_volunteers
from mielenosoitukset_fi.scripts.purge_suggestion_ips import main as purge_suggestion_ips
from mielenosoitukset_fi.scripts.purge_erased_accounts import main as purge_erased_accounts
from mielenosoitukset_fi.scripts.scrub_admin_log_secrets import main as scrub_admin_log_secrets
from mielenosoitukset_fi.scripts.deliver_pride_banner_confirmations import main as deliver_pride_banner_confirmations
from mielenosoitukset_fi.scripts.purge_pride_banner_volunteers import main as purge_pride_banner_volunteers
from mielenosoitukset_fi.scripts.deliver_opening_interest_confirmations import main as deliver_opening_interest_confirmations
from mielenosoitukset_fi.scripts.purge_opening_interest import main as purge_opening_interest
from mielenosoitukset_fi.scripts.process_urgent_review_alerts import run as process_urgent_review_alerts


@dataclass(frozen=True)
class JobDefinition:
    """Metadata for a background job."""

    key: str
    name: str
    description: str
    func: Callable[[], Any]
    default_trigger: Dict[str, Any]
    allow_manual_trigger: bool = True
    allow_interval_override: bool = True

    def to_document(self) -> Dict[str, Any]:
        """Return the MongoDB document fragment containing immutable job info."""
        return {
            "_id": self.key,
            "name": self.name,
            "description": self.description,
            "trigger": self.default_trigger.get("trigger", "interval"),
            "trigger_args": self.default_trigger.get("trigger_args", {}),
            "allow_manual_trigger": self.allow_manual_trigger,
            "allow_interval_override": self.allow_interval_override,
        }


def _interval(hours: int = 0, minutes: int = 0, days: int = 0) -> Dict[str, Any]:
    """Build an interval trigger configuration from hours, minutes, and days."""
    return {"trigger": "interval", "trigger_args": {"hours": hours, "minutes": minutes, "days": days}}


JOB_DEFINITIONS: List[JobDefinition] = [
    JobDefinition(
        key="process_urgent_review_alerts",
        name="Urgent demonstration review alerts",
        description="Reconciles urgent review Telegram messages when explicitly enabled.",
        func=process_urgent_review_alerts,
        default_trigger=_interval(minutes=2),
    ),
    JobDefinition(
        key="repeat_main",
        name="Recurring demonstrations refresher",
        description="Processes recurring demonstrations and creates child events.",
        func=repeat_main,
        default_trigger=_interval(hours=24),
    ),
    JobDefinition(
        key="update_main",
        name="Organizer metadata updater",
        description="Refreshes demonstration organizer details.",
        func=update_main,
        default_trigger=_interval(hours=1),
    ),
    JobDefinition(
        key="cl_main",
        name="Cleanup and linking",
        description="Runs the CL maintenance script.",
        func=cl_main,
        default_trigger=_interval(hours=24),
    ),
    JobDefinition(
        key="prep",
        name="Analytics rollup",
        description="Aggregates analytics stats for dashboards.",
        func=prep,
        default_trigger=_interval(minutes=15),
    ),
    JobDefinition(
        key="rebuild_d_analytics",
        name="Demo analytics rebuild",
        description="Recounts d_analytics from the raw analytics events, repairing inflated or stale counters.",
        func=rebuild_demo_analytics,
        default_trigger=_interval(days=30),
    ),
    JobDefinition(
        key="run_preview",
        name="Preview image regeneration",
        description="Generates fresh preview images for demonstrations.",
        func=run_preview,
        default_trigger=_interval(hours=24),
        allow_interval_override=False,
    ),
    JobDefinition(
        key="demo_sche",
        name="Demonstration reminder emails",
        description="Sends reminder emails to subscribers.",
        func=demo_sche,
        default_trigger=_interval(hours=24),
    ),
    JobDefinition(
        key="hide_past",
        name="Past demonstration hider",
        description="Archives events that have already taken place.",
        func=hide_past,
        default_trigger=_interval(hours=24),
    ),
    JobDefinition(
        key="process_submit_notifications",
        name="New demo notification dispatcher",
        description="Sends confirmation + admin notification emails for submitted demonstrations.",
        func=process_submit_notifications,
        default_trigger=_interval(minutes=5),
    ),
    JobDefinition(
        key="process_ui_translation_sync",
        name="UI translation Git sync",
        description="Pushes approved UI translation catalog changes to the configured Git branch/PR flow.",
        func=process_ui_translation_sync,
        default_trigger=_interval(minutes=10),
    ),
    JobDefinition(
        key="auto_close_cases",
        name="Case autoclose (demos/orgs)",
        description="Closes cases whose linked demo, suggestion or org edit was already handled. The case list itself never closes cases.",
        func=auto_close_cases,
        default_trigger=_interval(minutes=10),
        allow_interval_override=True,
    ),
    JobDefinition(
        key="process_support_tickets",
        name="Support ticket ingress (tuki@ mailbox)",
        description="Polls the tuki@ IMAP mailbox, creates support_ticket cases, sends auto-replies and relays URGENT follow-ups.",
        func=process_support_tickets,
        default_trigger=_interval(minutes=1),
        allow_interval_override=True,
    ),
    JobDefinition(
        key="process_email_queue",
        name="Email queue drain",
        description="Drains orphaned queued emails, ensuring delivery even when originating processes terminate.",
        func=process_email_queue,
        default_trigger=_interval(minutes=2),
    ),
    JobDefinition(
        key="escalate_city_assignments",
        name="City assignment escalation (24h)",
        description="Escalates city-admin assigned demonstrations with no decision within 24 h to the national team.",
        func=escalate_city_assignments,
        default_trigger=_interval(minutes=30),
        allow_interval_override=True,
    ),
    JobDefinition(
        key="expire_undecided_demos",
        name="Close undecided past demonstrations",
        description="Closes demonstrations nobody approved or rejected before their date as expired (Vanhentunut) through the decision service. Submitters of demos that passed within EXPIRED_DEMO_NOTIFY_DAYS (14) get an apology email; older ones close quietly. EXPIRE_UNDECIDED_DEMOS=false turns it off.",
        func=expire_undecided_demos,
        default_trigger=_interval(hours=6),
        allow_interval_override=True,
    ),
    JobDefinition(
        key="notify_overdue_moderation_reviews",
        name="Moderation review delay notices",
        description="Emails the submitter once when the OT review of a rejection or take-down is still open after five working days (moderation guide 18 Nov 2026). The rejection stays provisional; nothing is decided automatically.",
        func=notify_overdue_moderation_reviews,
        default_trigger=_interval(hours=1),
        allow_interval_override=True,
    ),
    JobDefinition(
        key="deliver_city_admin_applications",
        name="City admin applications to cases",
        description="Creates a review case and national team inbox notices for each city admin application stored by the Go API.",
        func=deliver_city_admin_applications,
        default_trigger=_interval(minutes=2),
    ),
    JobDefinition(
        key="deliver_case_notices",
        name="Demo suggestions and new case notices",
        description="Opens a case for each open demonstration suggestion (DEMO_SUGGESTION_CASES_ENABLED) and sends a site inbox notice about each new case to the admins who may view it (CASE_NOTIFICATIONS_ENABLED). Does nothing unless a switch is set. Sends no e-mail.",
        func=deliver_case_notices,
        default_trigger=_interval(minutes=2),
    ),
    JobDefinition(
        key="purge_city_admin_applications",
        name="City admin application retention",
        description="Erases the text of decided city admin applications after their retention period. Does nothing unless CITY_ADMIN_APPLICATION_RETENTION_ENABLED is set.",
        func=purge_city_admin_applications,
        default_trigger=_interval(days=1),
    ),
    JobDefinition(
        key="deliver_fundraising_reminder_confirmations",
        name="Fundraising reminder confirmations",
        description="Queues the confirmation mail for each fundraising reminder signup. Does nothing unless FUNDRAISING_REMINDER_MAILS_ENABLED is set.",
        func=deliver_fundraising_reminder_confirmations,
        default_trigger=_interval(minutes=2),
    ),
    JobDefinition(
        key="send_fundraising_reminders",
        name="Fundraising opening reminder (once)",
        description="Queues the single opening reminder for confirmed signups from 16.11.2026 00:00 Helsinki. Does nothing without FUNDRAISING_REMINDER_MAILS_ENABLED and FUNDRAISING_REMINDER_BULK_SEND_APPROVED.",
        func=send_fundraising_reminders,
        default_trigger=_interval(minutes=5),
    ),
    JobDefinition(
        key="purge_fundraising_reminders",
        name="Fundraising reminder retention",
        description="Deletes unconfirmed signups after two weeks and all reminder data 30 days after the collection ends (17.3.2027).",
        func=purge_fundraising_reminders,
        default_trigger=_interval(days=1),
    ),
    JobDefinition(
        key="purge_campaign_volunteers",
        name="Campaign volunteer retention",
        description="Deletes all campaign volunteer signups once 31.5.2027 has passed (from 1.6.2027 00:00 Helsinki). Does nothing before that.",
        func=purge_campaign_volunteers,
        default_trigger=_interval(days=1),
    ),
    JobDefinition(
        key="purge_suggestion_ips",
        name="Suggestion IP retention",
        description="Removes the IP address and User-Agent from organisation and demonstration suggestions (and their cases) older than 90 days. Never deletes the suggestions. Does nothing unless SUGGESTION_IP_PURGE_ENABLED is set.",
        func=purge_suggestion_ips,
        default_trigger=_interval(days=1),
    ),
    JobDefinition(
        key="purge_erased_accounts",
        name="Deleted account retention",
        description="Deletes the identity seal and kept non-audit records of accounts deleted more than ERASURE_RETENTION_DAYS (default 730) ago.",
        func=purge_erased_accounts,
        default_trigger=_interval(days=1),
    ),
    JobDefinition(
        key="scrub_admin_log_secrets",
        name="Admin log credential cleanup",
        description="One-time job: removes stored session cookies, request headers and password hashes from admin request log rows written before 20.11.2026. Keeps the rows. Does nothing after its first finished run.",
        func=scrub_admin_log_secrets,
        default_trigger=_interval(hours=1),
    ),
    JobDefinition(
        key="deliver_pride_banner_confirmations",
        name="Pride banner carrier confirmations",
        description="Queues the confirmation mail for each Pride banner carrier signup. Does nothing unless PRIDE_BANNER_MAILS_ENABLED is set.",
        func=deliver_pride_banner_confirmations,
        default_trigger=_interval(minutes=2),
    ),
    JobDefinition(
        key="purge_pride_banner_volunteers",
        name="Pride banner carrier retention",
        description="Deletes unconfirmed signups after two weeks and all carrier data from 31.10.2027 unless a later retention date is set.",
        func=purge_pride_banner_volunteers,
        default_trigger=_interval(days=1),
    ),
    JobDefinition(
        key="deliver_opening_interest_confirmations",
        name="Campaign opening interest confirmations",
        description="Queues the confirmation mail for each campaign opening interest signup. Does nothing unless OPENING_INTEREST_MAILS_ENABLED is set.",
        func=deliver_opening_interest_confirmations,
        default_trigger=_interval(minutes=2),
    ),
    JobDefinition(
        key="purge_opening_interest",
        name="Campaign opening interest retention",
        description="Deletes unconfirmed signups after two weeks and all opening interest data from 13.3.2027 unless a later retention date is set.",
        func=purge_opening_interest,
        default_trigger=_interval(days=1),
    ),
]

JOB_DEFINITION_MAP: Dict[str, JobDefinition] = {job.key: job for job in JOB_DEFINITIONS}
